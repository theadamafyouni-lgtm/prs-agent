#!/usr/bin/env python3
"""weight_coverage.py -- the weighted-coverage metric (R9), computed two ways.

The metric, from prd §3:

    unresolved-weight fraction = sum|beta| over unresolved positions
                               / sum|beta| over all model positions

and a position is **resolved** if it is directly typed and passes QC, or imputed at or above
the r-squared bar. Summed |beta|, not beta^2 * 2p(1-p): the |beta| form is the individual-
instability quantity, because an unresolved locus can move THIS person's score by up to 2|beta|.
The variance form is the population lens, and it drives a rare high-|beta| variant's weight
toward zero as p -> 0 -- under-counting exactly the variant that swings one individual.

No standard metric exists for this and no tool computes it (R9: confirmed negative), so this is
a construction, not a citation. It is arithmetic only -- **the refusal decision is the agent's**
(SCO-8, SEL-10). This tool reports numbers and never says "refuse".

Two modes, deliberately sharing an output shape so SEL-12 can compare them:

  estimate  (select, SEL-11) -- pre-imputation. Buckets model positions into typed / in-panel /
            nowhere. Structurally BLIND to imputability: it sees weight on missing positions but
            cannot see r-squared until imputation runs. So it reports a bracket, not a number:
              upper bound = (typed + pending) / total -- as if every pending position imputes at
                            or above the bar. resolved_weight_fraction carries THIS in estimate
                            mode: a floor applied before measurement may only refuse a model that
                            cannot pass even in the best case.
              typed-only  = typed / total             -- as if nothing imputes. Shown, not gated.
            A position that is in the panel but not typed is PENDING: neither resolved nor
            unresolved until measure says so, and it cannot trip the high-|beta| veto here. Only
            a locus no route can reach can -- absent from the panel, on a contig score does not
            impute, or with no usable coordinate.

  measure   (score, SCO-8)   -- post-imputation. Uses the real per-variant r-squared (Beagle DR2)
            against the bar, which is the number the gate is actually entitled to act on.

Usage:
  python3 tools/weight_coverage.py estimate --scorefile <PGS...txt.gz> \
      --chr-col hm_chr --pos-col hm_pos --effect-weight-col effect_weight --imputed-contigs 1-22 \
      --patient-vcf runs/R/door/normalized.vcf.gz --panel-index refdata/panel/sites_index \
      --high-beta-fraction 0.05 --out runs/R/select/coverage_PGS012535.json

  python3 tools/weight_coverage.py measure --scorefile <PGS...txt.gz> \
      --chr-col hm_chr --pos-col hm_pos --effect-weight-col effect_weight \
      --dosage-tsv runs/R/score/dosages.tsv --r2-bar 0.80 \
      --high-beta-fraction 0.05 --out runs/R/score/coverage_PGS012535.json
"""
import argparse
import gzip
import heapq
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_run  # noqa: E402


TOP_LOCI_KEPT = 20000  # heap size for the largest-|beta| loci


def open_maybe_gz(path):
    return gzip.open(path, "rt", encoding="utf-8", errors="replace") if path.endswith(".gz") \
        else open(path, encoding="utf-8", errors="replace")


class PanelIndex:
    def __init__(self, stem):
        meta = lib_run.read_json(stem + ".json")
        self.offsets = meta["contig_offsets"]
        self.lengths = meta["contig_lengths"]
        with open(stem + ".bin", "rb") as fh:
            self.bits = fh.read()
        self.meta = meta

    def has(self, contig, pos):
        off = self.offsets.get(contig)
        if off is None or pos > self.lengths.get(contig, 0):
            return False
        i = off + pos
        return bool(self.bits[i >> 3] & (1 << (i & 7)))


def read_scorefile(path, chr_col, pos_col, w_col, id_col=None):
    """Stream the scoring file using the column names the AGENT read off its header (SEL-13).
    No column layout is assumed or cached here."""
    header_meta = []
    with open_maybe_gz(path) as fh:
        cols = None
        for line in fh:
            if line.startswith("#"):
                header_meta.append(line.rstrip("\n"))
                continue
            f = line.rstrip("\n").split("\t")
            if cols is None:
                cols = {name: i for i, name in enumerate(f)}
                for needed in (chr_col, pos_col, w_col):
                    if needed not in cols:
                        raise SystemExit(
                            f"weight_coverage.py: column {needed!r} not in the scoring file. "
                            f"Present: {sorted(cols)}")
                yield ("__header__", header_meta, sorted(cols))
                continue
            try:
                w = float(f[cols[w_col]])
            except (ValueError, IndexError):
                yield ("__badweight__", None, None)
                continue
            c = f[cols[chr_col]].strip()
            p = f[cols[pos_col]].strip()
            if not c or not p or not p.isdigit():
                # The weight is known; only the coordinate is not. No route -- typed or imputed --
                # can reach this row (match_variants skips it too), so its weight stays in the
                # denominator as unresolved and it stays eligible for the high-|beta| veto.
                yield ("__badpos__", w, c or "?")
                continue
            vid = f[cols[id_col]] if id_col and id_col in cols else f"{c}:{p}"
            yield (c, int(p), w, vid)


def patient_positions(vcf):
    import subprocess
    out = subprocess.run(["bcftools", "query", "-f", "%CHROM\t%POS\n", vcf],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"weight_coverage.py: bcftools query failed\n{out.stderr[-2000:]}")
    return {(c, int(p)) for c, p in (l.split("\t") for l in out.stdout.splitlines())}


def read_dosage_r2(path):
    """chrom, pos -> (r2, source, status) as match_variants wrote it.

    The STATUS column is load-bearing and must not be ignored. A marker match_variants
    deliberately excluded -- an unresolvable palindrome, an allele mismatch -- still has a source
    and an r2, so reading only those two would count it as resolved. That would inflate coverage
    AND hide the exact high-|beta| locus SCO-8's second condition exists to catch."""
    d = {}
    with open_maybe_gz(path) as fh:
        header = fh.readline().rstrip("\n").split("\t")
        ix = {n: i for i, n in enumerate(header)}
        for line in fh:
            f = line.rstrip("\n").split("\t")
            d[(f[ix["chrom"]], int(f[ix["pos"]]))] = (
                float(f[ix["r2"]]) if f[ix["r2"]] not in ("", ".", "NA") else None,
                f[ix["source"]],
                f[ix["status"]] if "status" in ix else "resolved",
            )
    return d


def kind(status):
    """resolved | pending | unresolved. Anything not explicitly resolved or pending is
    unresolved, so a status added later fails safe into the gate rather than out of it."""
    if status.startswith("resolved"):
        return "resolved"
    if status.startswith("pending"):
        return "pending"
    return "unresolved"


def summarise(mode, buckets, total_abs, high_beta_fraction, top_loci):
    by_kind = {"resolved": 0.0, "pending": 0.0, "unresolved": 0.0}
    for k, v in buckets.items():
        by_kind[kind(k)] += v
    if mode == "measure" and by_kind["pending"]:
        raise SystemExit("weight_coverage.py: a pending bucket in measure mode is a bug")
    resolved, pending, unresolved = by_kind["resolved"], by_kind["pending"], by_kind["unresolved"]
    upper = resolved + pending  # == resolved in measure mode

    def frac(x):
        return (x / total_abs) if total_abs else None

    top_loci = sorted(top_loci, key=lambda t: -t[0])

    def loci_at_or_above(which):
        # at most 1/high_beta_fraction loci can clear the threshold, so the top 200 is exhaustive
        return [
            {"chrom": c, "pos": p, "abs_beta": a, "share_of_total_abs_beta": a / total_abs,
             "status": st}
            for a, c, p, st in top_loci[:200]
            if total_abs and a / total_abs >= high_beta_fraction and kind(st) == which
        ]

    high = loci_at_or_above("unresolved")
    out = {
        "total_abs_beta": total_abs,
        "resolved_abs_beta": resolved,              # estimate: directly typed only
        "pending_imputation_abs_beta": pending,     # measure: always 0
        "unresolved_abs_beta": unresolved,          # estimate: no route can reach it
        # THE GATED FRACTION. estimate: upper bound (typed + pending). measure: measured.
        "resolved_weight_fraction": frac(upper),
        "resolved_weight_fraction_is": ("upper_bound_pre_imputation" if mode == "estimate"
                                        else "measured_post_imputation"),
        "unresolved_weight_fraction": frac(unresolved),
        "abs_beta_by_bucket": buckets,
        "high_beta_unresolved_loci": high,
        "high_beta_condition": {
            "threshold_fraction_of_total_abs_beta": high_beta_fraction,
            "n_unresolved_loci_at_or_above_threshold": len(high),
            "unresolved_means": ("no route can resolve it: absent from the panel, on a contig "
                                 "score does not impute, or no usable coordinate. Loci pending "
                                 "imputation are listed separately and are not a veto until "
                                 "measured."
                                 if mode == "estimate" else
                                 "every status other than resolved_* after imputation and matching"),
            "reported": "individually, by locus -- never summed into the coverage fraction (SCO-8)",
        },
        "largest_single_locus_shares": [
            {"chrom": c, "pos": p, "abs_beta": a, "share": a / total_abs if total_abs else None,
             "status": st}
            for a, c, p, st in top_loci[:10]
        ],
    }
    if mode == "estimate":
        pending_high = loci_at_or_above("pending")
        out.update({
            "resolved_weight_fraction_upper_bound": frac(upper),
            "resolved_weight_fraction_typed_only": frac(resolved),
            "pending_imputation_weight_fraction": frac(pending),
            "high_beta_pending_imputation_loci": pending_high,
        })
        out["high_beta_condition"]["n_pending_imputation_loci_at_or_above_threshold"] = \
            len(pending_high)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("estimate", "measure"):
        p = sub.add_parser(name)
        p.add_argument("--scorefile", required=True)
        p.add_argument("--chr-col", required=True)
        p.add_argument("--pos-col", required=True)
        p.add_argument("--effect-weight-col", required=True)
        p.add_argument("--id-col")
        p.add_argument("--high-beta-fraction", type=float, required=True)
        p.add_argument("--run")
        p.add_argument("--out", required=True)
        if name == "estimate":
            p.add_argument("--patient-vcf", required=True)
            p.add_argument("--panel-index", required=True)
            # The sites index covers chrX/Y; the imputation panel is chr1-22. A position on a
            # contig score does not impute never reaches dosages.tsv (match_variants keys on the
            # imputed VCFs), so it is unresolved, not pending -- typed or not.
            p.add_argument("--imputed-contigs", required=True,
                           help="the --chromosomes score's impute step runs, e.g. 1-22")
        else:
            p.add_argument("--dosage-tsv", required=True)
            p.add_argument("--r2-bar", type=float, required=True)
    args = ap.parse_args()

    typed = panel_positions = dosages = imputed_contigs = None
    if args.mode == "estimate":
        typed = patient_positions(args.patient_vcf)
        panel_positions = PanelIndex(args.panel_index)
        imputed_contigs = set()
        for part in args.imputed_contigs.split(","):
            lo, _, hi = part.partition("-")
            if hi:
                imputed_contigs.update(str(i) for i in range(int(lo), int(hi) + 1))
            else:
                imputed_contigs.add(part)
    else:
        dosages = read_dosage_r2(args.dosage_tsv)

    buckets = {}
    total_abs = 0.0
    top_loci = []
    n_rows = n_bad = n_unlocatable = 0
    skipped = {}
    header_meta, columns_present = [], []

    for rec in read_scorefile(args.scorefile, args.chr_col, args.pos_col,
                              args.effect_weight_col, args.id_col):
        if rec[0] == "__header__":
            header_meta, columns_present = rec[1], rec[2]
            continue
        if rec[0] == "__badweight__":
            # |beta| unknown, so it cannot enter sum|beta| at all; counted and reported.
            n_bad += 1
            skipped["badweight"] = skipped.get("badweight", 0) + 1
            continue
        n_rows += 1
        if rec[0] == "__badpos__":
            _tag, w, c = rec
            p = -1
            n_unlocatable += 1
            st = "unresolved_unlocatable_position"
        else:
            c, p, w, _vid = rec
            st = None
        a = abs(w)
        total_abs += a

        if st is not None:
            pass
        elif args.mode == "estimate":
            if c not in imputed_contigs:
                st = "unresolved_contig_not_imputed"
            elif (c, p) in typed:
                st = "resolved_directly_typed"
            elif panel_positions.has(c, p):
                st = "pending_imputation_in_panel"
            else:
                st = "unresolved_absent_from_panel"
        else:
            hit = dosages.get((c, p))
            if hit is None:
                st = "unresolved_no_dosage"
            else:
                r2, src, status = hit
                if status == "multiallelic_unresolvable":
                    st = "unresolved_multiallelic_panel_site"
                elif status == "excluded_palindromic":
                    st = "unresolved_excluded_palindromic"
                elif status == "allele_mismatch":
                    st = "unresolved_allele_mismatch"
                elif status == "below_r2_bar":
                    st = "unresolved_imputed_below_bar"
                elif src == "typed":
                    st = "resolved_directly_typed"
                elif r2 is not None and r2 >= args.r2_bar:
                    st = "resolved_imputed_at_or_above_bar"
                else:
                    st = "unresolved_imputed_below_bar"

        buckets[st] = buckets.get(st, 0.0) + a
        # Keep the largest-|beta| loci by heap, NOT the first N encountered. SCO-8's second
        # condition is about the single heaviest unresolved locus, and a model can list its
        # heaviest variant on any row -- truncating by position would silently miss it.
        if len(top_loci) < TOP_LOCI_KEPT:
            heapq.heappush(top_loci, (a, c, p, st))
        elif a > top_loci[0][0]:
            heapq.heapreplace(top_loci, (a, c, p, st))

    result = {
        "tool": f"weight_coverage.py {args.mode}",
        "generated_utc": lib_run.utcnow(),
        "scorefile": args.scorefile,
        "scorefile_header_verbatim": header_meta,
        "scorefile_columns_present": columns_present,
        "columns_used": {"chrom": args.chr_col, "pos": args.pos_col,
                         "effect_weight": args.effect_weight_col},
        "n_model_rows_counted": n_rows,
        "n_model_rows_counted_unresolved_no_usable_position": n_unlocatable,
        "n_model_rows_skipped_unparseable_weight": n_bad,
        "skipped_row_reasons": skipped,
        "metric": "summed |beta| fraction (R9 / prd §3); NOT beta^2*2p(1-p)",
        "mode_caveat": (
            "ESTIMATE, pre-imputation (SEL-11). resolved_weight_fraction here is an UPPER BOUND: "
            "directly typed weight plus weight pending imputation in the panel, as if every "
            "pending position imputes at or above the bar. score's measured fraction cannot "
            "exceed it, so below the floor here the model cannot pass at score; at or above it, "
            "it only might. resolved_weight_fraction_typed_only is the no-imputation figure: "
            "shown, not gated. high_beta_unresolved_loci holds only loci no route can resolve; "
            "in-panel high-|beta| loci are in high_beta_pending_imputation_loci and are decided "
            "by measure (SCO-8). Not seen here: r2, the palindromic policy and allele checks, "
            "all of which can only lower what score measures."
            if args.mode == "estimate" else
            f"MEASURED, post-imputation, at r2 bar {getattr(args, 'r2_bar', None)} (prd §3 value 3)."),
        "decision_is_not_here": "SCO-8's refusal is the agent's call. This file is arithmetic.",
    }
    result.update(summarise(args.mode, buckets, total_abs, args.high_beta_fraction, top_loci))
    if args.mode == "estimate":
        result["panel_index"] = panel_positions.meta.get("sites_vcf")
        result["n_patient_typed_positions"] = len(typed)
        result["imputed_contigs"] = sorted(imputed_contigs, key=lambda x: (len(x), x))
    else:
        result["r2_bar"] = args.r2_bar

    lib_run.emit(result, args.out)
    if args.run:
        lib_run.ledger_append(args.run, {
            "kind": "facts", "stage": "select" if args.mode == "estimate" else "score",
            "tool": f"weight_coverage.py {args.mode}",
            "resolved_weight_fraction": result["resolved_weight_fraction"],
            "resolved_weight_fraction_is": result["resolved_weight_fraction_is"],
            "resolved_weight_fraction_typed_only": result.get("resolved_weight_fraction_typed_only"),
            "artifact": args.out,
        })


if __name__ == "__main__":
    main()
