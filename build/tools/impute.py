#!/usr/bin/env python3
"""impute.py -- deterministic panel-based imputation with Beagle (SCO-7, SCO-10).

Mechanical. Runs Beagle per chromosome against the staged 1000G b37 panel and records exactly
what ran: Beagle version, panel file and its hash, genetic map, seed, and every parameter.

SCO-10 is a scoring parameter, not an implementation detail: stochastic pre-phasing moves an
individual's percentile with no change to the genetic model at all -- up to ~9.19% of individuals
shift more than 10 percentile points under a stochastic pipeline versus ~0.1% under a
deterministic one. So the seed is fixed and recorded, and this is a LOCAL pipeline, not the
Michigan/TOPMed server whose stochastic pre-phasing is the source of that shift (prd §2).

SCO-7's distinction matters and is preserved here: this is PANEL-BASED imputation, inference from
this person's own haplotypes. It is NOT reference-AF mean-imputation (filling a genotype with 2x
the population frequency), which inserts a population average and presents it as this person's
dosage. That operation never happens anywhere in this pipeline.

Usage:
  python3 tools/impute.py --run slice-001 \
      --target runs/slice-001/door/normalized.vcf.gz \
      --panel-dir refdata/panel --map-dir refdata/maps \
      --chromosomes 1-22 --seed 20260722 --threads 6 --xmx 8g \
      --out-dir runs/slice-001/score/imputed
"""
import argparse
import glob
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_run  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "refdata", "jre", "bin", "java")
BEAGLE = os.path.join(ROOT, "refdata", "bin", "beagle.jar")


def parse_chroms(spec):
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(str(i) for i in range(int(a), int(b) + 1))
        else:
            out.append(part)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--panel-dir", required=True)
    ap.add_argument("--map-dir", required=True)
    ap.add_argument("--chromosomes", required=True)
    ap.add_argument("--seed", required=True, help="fixed and recorded -- SCO-10")
    ap.add_argument("--threads", default="4")
    ap.add_argument("--xmx", default="8g")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    beagle_version = subprocess.run([JAVA, "-jar", BEAGLE], capture_output=True, text=True)
    version_line = (beagle_version.stdout + beagle_version.stderr).strip().splitlines()[0]

    runs = []
    for c in parse_chroms(args.chromosomes):
        panel = os.path.join(args.panel_dir, f"chr{c}.1kg.phase3.v5a.b37.bref3")
        gmap = os.path.join(args.map_dir, f"plink.chr{c}.GRCh37.map")
        out_stem = os.path.join(args.out_dir, f"chr{c}")
        if not os.path.exists(panel):
            runs.append({"chrom": c, "status": "SKIPPED_no_panel_file", "panel": panel})
            continue
        if not os.path.exists(gmap):
            runs.append({"chrom": c, "status": "SKIPPED_no_genetic_map", "map": gmap})
            continue
        if args.skip_existing and os.path.exists(out_stem + ".vcf.gz"):
            runs.append({"chrom": c, "status": "SKIPPED_output_exists"})
            continue

        cmd = [JAVA, f"-Xmx{args.xmx}", "-jar", BEAGLE,
               f"gt={args.target}", f"ref={panel}", f"map={gmap}",
               f"out={out_stem}", f"nthreads={args.threads}", f"seed={args.seed}",
               "gp=true", f"chrom={c}", "impute=true"]
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = round(time.time() - t0, 1)
        with open(out_stem + ".beagle.log", "w") as fh:
            fh.write(" ".join(cmd) + "\n\n" + proc.stdout + "\n" + proc.stderr)
        entry = {"chrom": c, "seconds": elapsed, "returncode": proc.returncode,
                 "panel": panel, "map": gmap, "output": out_stem + ".vcf.gz",
                 "status": "ok" if proc.returncode == 0 else "FAILED"}
        if proc.returncode == 0:
            subprocess.run(["bcftools", "index", "-f", "-t", out_stem + ".vcf.gz"], check=False)
        else:
            entry["stderr_tail"] = proc.stderr[-2000:]
        runs.append(entry)
        print(f"chr{c}: {entry['status']} ({elapsed}s)", file=sys.stderr)

    result = {
        "tool": "impute.py",
        "generated_utc": lib_run.utcnow(),
        "operation": "panel-based imputation (SCO-7). NOT reference-AF mean-imputation.",
        "beagle_version": version_line,
        "java": lib_run.tool_version([JAVA, "-version"]),
        "determinism": {
            "requirement": "SCO-10",
            "seed": args.seed,
            "pipeline": "local Beagle, fixed seed -- not the Michigan/TOPMed server pipeline",
        },
        "target": args.target,
        "panel_dir": args.panel_dir,
        "panel_manifest": "refdata/MANIFEST.json (URL + sha256 per panel file, DATA-6)",
        "parameters": {"threads": args.threads, "xmx": args.xmx, "gp": True, "impute": True},
        "per_chromosome": runs,
        "n_ok": sum(1 for r in runs if r.get("status") == "ok"),
        "n_failed": sum(1 for r in runs if r.get("status") == "FAILED"),
        "n_skipped": sum(1 for r in runs if str(r.get("status", "")).startswith("SKIPPED")),
    }
    out = os.path.join(args.out_dir, "imputation_report.json")
    lib_run.emit(result, out)
    lib_run.ledger_append(args.run, {
        "kind": "artifact", "stage": "score", "tool": "impute.py",
        "n_ok": result["n_ok"], "n_failed": result["n_failed"], "seed": args.seed,
        "artifact": out,
    })


if __name__ == "__main__":
    main()
