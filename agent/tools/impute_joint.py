#!/usr/bin/env python3
"""impute_joint.py -- joint panel-based imputation: the patient imputed together with a
hold-out cohort, so DR2 is measured natively in the same Beagle run that produced the
patient's dosages.

Beagle's DR2 (and INFO) is a PER-VARIANT statistic estimated across the target samples, so a
patient imputed alone leaves it nothing to spread over and it collapses to ~0 at almost every
site -- which is not a low-quality measurement, it is no measurement at all, and a coverage gate
reading those sites sees unmeasurable positions as failures.

The fix is structural, not cosmetic. The Beagle target is the patient MERGED with a hold-out
cohort drawn from the same panel (tools/build_dr2_cohort.py), masked to the patient's typed
positions so the hold-outs are genotyped as if on the same chip; the reference is everyone else,
disjoint from the hold-outs so no sample imputes itself. One Beagle run per chromosome produces
both the patient's dosages and a DR2 estimated over ~300 real genomes. Nothing is transplanted
from a separate run: the DR2 written here belongs to the very run that wrote the dosages.

The patient is in neither sample list, and is verified to be in neither before any chromosome
runs -- a patient present in the reference would find their own haplotypes there.

Usage:
  python3 tools/impute_joint.py --run slice-001 \
      --target runs/slice-001/door/normalized.vcf.gz --patient-id PATIENT \
      --holdout-samples runs/slice-001/score/dr2/dr2_holdout.txt \
      --reference-samples runs/slice-001/score/dr2/dr2_reference.txt \
      --phase3-dir refdata/ancestry-ref/phase3 --map-dir refdata/maps \
      --typed-positions runs/slice-001/score/patient_typed_positions.tsv \
      --chromosomes 1-22 --seed 20260722 --threads 6 --xmx 8g \
      --out-dir runs/slice-001/score/imputed_joint
"""
import argparse
import gzip
import os
import shutil
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


def die(msg):
    print(f"impute_joint.py: {msg}", file=sys.stderr)
    sys.exit(1)


def read_sample_list(path):
    """One sample ID per line; blank lines ignored."""
    if not os.path.exists(path):
        die(f"sample list not found: {path}")
    with open(path) as fh:
        return [line.strip() for line in fh if line.strip()]


def vcf_samples(path):
    """Sample names in a VCF, read off the file itself rather than assumed."""
    proc = subprocess.run(["bcftools", "query", "-l", path], capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"bcftools query -l failed on {path}: {proc.stderr.strip()}")
    return [s.strip() for s in proc.stdout.splitlines() if s.strip()]


def n_markers(path):
    proc = subprocess.run(["bcftools", "index", "-n", path], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


class Failed(Exception):
    """A step returned non-zero. Carries what to record; never propagates past one chromosome."""

    def __init__(self, step, returncode, stderr):
        super().__init__(step)
        self.step = step
        self.returncode = returncode
        self.stderr = stderr or ""


def run_step(step, cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Failed(step, proc.returncode, proc.stderr)
    return proc


def run_pipe(step, cmd_a, cmd_b):
    """cmd_a | cmd_b, both stderr captured. Either non-zero fails the step."""
    p_a = subprocess.Popen(cmd_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p_b = subprocess.Popen(cmd_b, stdin=p_a.stdout, stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
    p_a.stdout.close()  # so cmd_a sees SIGPIPE if cmd_b dies
    err_b = p_b.communicate()[1]
    err_a = p_a.communicate()[1]
    rc = p_a.returncode or p_b.returncode
    if rc != 0:
        text = (err_a or b"").decode(errors="replace") + (err_b or b"").decode(errors="replace")
        raise Failed(step, rc, text)


def query_gzip(step, cmd, out_path):
    """Stream a bcftools query straight into a gzipped file."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with gzip.open(out_path, "wb") as gz:
        for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
            gz.write(chunk)
    err = proc.communicate()[1]
    if proc.returncode != 0:
        raise Failed(step, proc.returncode, (err or b"").decode(errors="replace"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--target", required=True, help="patient VCF, bgzipped and indexed, one sample")
    ap.add_argument("--patient-id", required=True)
    ap.add_argument("--holdout-samples", required=True, help="from build_dr2_cohort.py")
    ap.add_argument("--reference-samples", required=True, help="from build_dr2_cohort.py")
    ap.add_argument("--phase3-dir", required=True, help="per-chromosome 1000G VCFs, chr<N>.vcf.gz")
    ap.add_argument("--map-dir", required=True)
    ap.add_argument("--typed-positions", required=True, help="chrom<TAB>pos, no header")
    ap.add_argument("--chromosomes", required=True)
    ap.add_argument("--seed", required=True, help="fixed and recorded -- SCO-10")
    ap.add_argument("--threads", default="4")
    ap.add_argument("--xmx", default="8g")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--work-dir", default=None, help="default <out-dir>/work")
    ap.add_argument("--keep-work", action="store_true", help="keep per-chromosome intermediates")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    work_root = args.work_dir or os.path.join(args.out_dir, "work")

    # ---- pre-flight. All three checks are about leakage or identity; none is recoverable. ----
    for path in (args.target, args.typed_positions):
        if not os.path.exists(path):
            die(f"file not found: {path}")

    target_samples = vcf_samples(args.target)
    if len(target_samples) != 1:
        die(f"--target must contain exactly one sample, found {len(target_samples)}: "
            f"{', '.join(target_samples[:10])}")
    if target_samples[0] != args.patient_id:
        die(f"--patient-id {args.patient_id!r} does not match the sample in --target "
            f"({target_samples[0]!r})")

    holdout = read_sample_list(args.holdout_samples)
    reference = read_sample_list(args.reference_samples)
    both = sorted(set(holdout) & set(reference))
    if both:
        die(f"{len(both)} sample(s) appear in both lists -- they would impute themselves: "
            f"{', '.join(both[:10])}")
    if args.patient_id in set(holdout) | set(reference):
        die(f"--patient-id {args.patient_id!r} appears in a sample list; the patient must be in "
            f"neither the reference nor the hold-out set")

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(work_root, exist_ok=True)
    # The 302-sample Beagle output lives in its own subdirectory. match_variants.py globs
    # *.vcf.gz in --imputed-dir, so a cohort file sitting beside the patient file would be
    # read as a second dosage source -- 44 files instead of 22, and only lexical ordering
    # kept the right one winning.
    cohort_dir = os.path.join(args.out_dir, "cohort")
    os.makedirs(cohort_dir, exist_ok=True)

    beagle_probe = subprocess.run([JAVA, "-jar", BEAGLE], capture_output=True, text=True)
    version_text = (beagle_probe.stdout + beagle_probe.stderr).strip()
    version_line = version_text.splitlines()[0] if version_text else None

    runs = []
    for c in parse_chroms(args.chromosomes):
        src = os.path.join(args.phase3_dir, f"chr{c}.vcf.gz")
        gmap = os.path.join(args.map_dir, f"plink.chr{c}.GRCh37.map")
        patient_out = os.path.join(args.out_dir, f"chr{c}.vcf.gz")
        cohort_stem = os.path.join(cohort_dir, f"chr{c}.cohort")
        dr2_out = os.path.join(args.out_dir, f"chr{c}.dr2.tsv.gz")
        work = os.path.join(work_root, f"chr{c}")

        if not (os.path.exists(src) and os.path.exists(src + ".tbi")):
            runs.append({"chrom": c, "status": "SKIPPED_no_source", "source": src})
            print(f"chr{c}: SKIPPED_no_source", file=sys.stderr)
            continue
        if not os.path.exists(gmap):
            runs.append({"chrom": c, "status": "SKIPPED_no_genetic_map", "source": src, "map": gmap})
            print(f"chr{c}: SKIPPED_no_genetic_map", file=sys.stderr)
            continue
        if args.skip_existing and os.path.exists(patient_out):
            runs.append({"chrom": c, "status": "SKIPPED_output_exists", "source": src,
                         "map": gmap, "patient_output": patient_out})
            print(f"chr{c}: SKIPPED_output_exists", file=sys.stderr)
            continue

        entry = {"chrom": c, "source": src, "map": gmap,
                 "patient_output": patient_out, "cohort_output": cohort_stem + ".vcf.gz",
                 "dr2_table": dr2_out, "beagle_log": os.path.join(args.out_dir, f"chr{c}.beagle.log")}
        os.makedirs(work, exist_ok=True)
        ref_vcf = os.path.join(work, "ref.vcf.gz")
        hold_vcf = os.path.join(work, "hold.vcf.gz")
        merged_vcf = os.path.join(work, "merged.vcf.gz")
        t0 = time.time()
        markers = None
        try:
            # reference = everyone not held out, biallelic SNVs, duplicates collapsed.
            # -Oz1 (fast compression): Beagle reads it fine and it writes far faster than level 6.
            run_pipe("bcftools view (reference)",
                     ["bcftools", "view", "-S", args.reference_samples, "--force-samples",
                      "-m2", "-M2", "-v", "snps", src, "-Ou"],
                     ["bcftools", "norm", "-d", "exact", "-Oz1", "--threads", args.threads,
                      "-o", ref_vcf])
            run_step("bcftools index (reference)", ["bcftools", "index", "-f", "-t", ref_vcf])

            # hold-out = the DR2 cohort, masked to the patient's typed positions so it carries an
            # array's worth of sites, not full WGS. Unmasked hold-outs would not resemble the
            # patient and the DR2 measured over them would not describe the patient's imputation.
            run_step("bcftools view (hold-out)",
                     ["bcftools", "view", "-S", args.holdout_samples, "--force-samples",
                      "-m2", "-M2", "-v", "snps", "-T", args.typed_positions, src,
                      "-Oz1", "--threads", args.threads, "-o", hold_vcf])
            run_step("bcftools index (hold-out)", ["bcftools", "index", "-f", "-t", hold_vcf])

            # patient + hold-outs as ONE Beagle target: this is what makes DR2 a measurement.
            run_step("bcftools merge",
                     ["bcftools", "merge", args.target, hold_vcf,
                      "-Oz1", "--threads", args.threads, "-o", merged_vcf])
            run_step("bcftools index (merged)", ["bcftools", "index", "-f", "-t", merged_vcf])
            entry["n_target_samples"] = len(vcf_samples(merged_vcf))

            # Beagle, run exactly as tools/impute.py runs it. chrom= confines the run to this
            # chromosome even though the patient VCF spans the genome.
            cmd = [JAVA, f"-Xmx{args.xmx}", "-jar", BEAGLE,
                   f"gt={merged_vcf}", f"ref={ref_vcf}", f"map={gmap}",
                   f"out={cohort_stem}", f"chrom={c}", f"nthreads={args.threads}",
                   f"seed={args.seed}", "gp=true", "impute=true"]
            entry["beagle_command"] = " ".join(cmd)
            proc = subprocess.run(cmd, capture_output=True, text=True)
            with open(entry["beagle_log"], "w") as fh:
                fh.write(" ".join(cmd) + "\n\n" + proc.stdout + "\n" + proc.stderr)
            if proc.returncode != 0:
                raise Failed("beagle", proc.returncode, proc.stderr)
            run_step("bcftools index (cohort)",
                     ["bcftools", "index", "-f", "-t", cohort_stem + ".vcf.gz"])

            # The patient-only file takes the plain chr<N>.vcf.gz name: downstream tools read this
            # directory expecting one sample per file.
            run_step("bcftools view (patient)",
                     ["bcftools", "view", "-s", args.patient_id, cohort_stem + ".vcf.gz",
                      "-Oz", "--threads", args.threads, "-o", patient_out])
            run_step("bcftools index (patient)", ["bcftools", "index", "-f", "-t", patient_out])

            # DR2/IMP off the cohort output -- estimated across the whole Beagle target.
            query_gzip("bcftools query (dr2)",
                       ["bcftools", "query", "-f",
                        "%CHROM\t%POS\t%REF\t%ALT\t%INFO/DR2\t%INFO/IMP\n",
                        cohort_stem + ".vcf.gz"],
                       dr2_out)

            markers = n_markers(cohort_stem + ".vcf.gz")
            entry.update({"status": "ok", "returncode": 0, "n_markers": markers})
        except Failed as exc:
            entry.update({"status": "FAILED", "failed_step": exc.step,
                          "returncode": exc.returncode, "stderr_tail": exc.stderr[-2000:]})
        entry["seconds"] = round(time.time() - t0, 1)
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)
        runs.append(entry)
        print(f"chr{c}: {entry['status']} ({entry['seconds']}s, "
              f"{markers if markers is not None else 'n/a'} markers)", file=sys.stderr)

    result = {
        "tool": "impute_joint.py",
        "generated_utc": lib_run.utcnow(),
        "run": args.run,
        "operation": "joint panel-based imputation: patient + hold-out cohort in one Beagle run, "
                     "so DR2 is measured natively over the run that produced the patient dosages. "
                     "NOT reference-AF mean-imputation.",
        "dr2_provenance": "DR2/IMP come from the same Beagle output as the patient dosages; "
                          "nothing is transplanted from a separate run",
        "beagle_version": version_line,
        "java": lib_run.tool_version([JAVA, "-version"]),
        "bcftools": lib_run.tool_version(["bcftools", "--version"]),
        "patient_id": args.patient_id,
        "target": args.target,
        "holdout_samples": args.holdout_samples,
        "n_holdout_samples": len(holdout),
        "reference_samples": args.reference_samples,
        "n_reference_samples": len(reference),
        "typed_positions": args.typed_positions,
        "phase3_dir": args.phase3_dir,
        "map_dir": args.map_dir,
        "chromosomes": args.chromosomes,
        "seed": args.seed,
        "preflight": {
            "target_single_sample": True,
            "target_sample_is_patient": True,
            "sample_lists_disjoint": True,
            "patient_in_neither_list": True,
        },
        "parameters": {"threads": args.threads, "xmx": args.xmx, "gp": True, "impute": True,
                       "keep_work": args.keep_work, "work_dir": work_root},
        "per_chromosome": runs,
        "n_ok": sum(1 for r in runs if r.get("status") == "ok"),
        "n_failed": sum(1 for r in runs if r.get("status") == "FAILED"),
        "n_skipped": sum(1 for r in runs if str(r.get("status", "")).startswith("SKIPPED")),
    }
    out = os.path.join(args.out_dir, "impute_joint.json")
    lib_run.emit(result, out)
    lib_run.ledger_append(args.run, {
        "kind": "artifact", "stage": "score", "tool": "impute_joint.py",
        "patient_id": args.patient_id, "seed": args.seed,
        "n_ok": result["n_ok"], "n_failed": result["n_failed"],
        "artifact": out,
    })


if __name__ == "__main__":
    main()
