#!/usr/bin/env python3
"""interpret_request.py -- assemble the INT-1 request from what the run already produced.

Mechanical: it copies fields from artifacts the earlier stages wrote. It computes nothing new and
decides nothing. Its only real job is making sure the ancestry grouping handed to interpret is the
SAME quantity ANC-1 produced (INT-5), rather than something re-derived here.

Contract: interfaces/reference-distribution.md

Usage:
  python3 tools/interpret_request.py --run slice-001 \
      --score runs/slice-001/score/raw_score.json \
      --coverage runs/slice-001/score/coverage_PGS0XXXXX.json \
      --ancestry runs/slice-001/ancestry/ancestry.json \
      --provenance runs/slice-001/door/provenance.json \
      --model-download runs/slice-001/select/dl_PGS0XXXXX.json \
      --out runs/slice-001/interpret/request.json
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_run  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--score", required=True)
    ap.add_argument("--coverage", required=True)
    ap.add_argument("--ancestry", required=True)
    ap.add_argument("--provenance", required=True)
    ap.add_argument("--model-download", required=True)
    ap.add_argument("--trait-id")
    ap.add_argument("--trait-label")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    score = lib_run.read_json(args.score)
    cov = lib_run.read_json(args.coverage)
    anc = lib_run.read_json(args.ancestry)
    prov = lib_run.read_json(args.provenance)
    dl = lib_run.read_json(args.model_download)
    rest = dl.get("rest_record", {})

    request = {
        "generated_utc": lib_run.utcnow(),
        "contract": "interfaces/reference-distribution.md",
        "run": args.run,
        "trait": {"id": args.trait_id or rest.get("trait_efo"),
                  "label": args.trait_label or rest.get("trait_reported")},
        "model": {
            "pgs_id": dl.get("pgs_id"),
            "hmPOS_build": dl.get("build_requested"),
            "resolved_url": dl.get("resolved_url"),
            "sha256": dl.get("sha256"),
            "header_verbatim": dl.get("file_header_verbatim"),
            "catalog_release_date": dl.get("catalog_release_date"),
            "license": rest.get("license"),
        },
        "patient": {
            "raw_score": score.get("raw_score"),
            "assay_class": prov.get("source_assay_class"),
            "vendor": prov.get("vendor"),
            "chip_generation": prov.get("chip_generation"),
            "genome_build": prov.get("declared_build"),
            # INT-5: the SAME quantity ANC-1 produced, copied, not re-derived
            "ancestry_grouping": {
                "group": anc.get("inferred_group"),
                "method": (anc.get("evidence") or [{}])[0].get("source"),
                "deviations": "see OPEN_QUESTIONS Q3",
                "source_judgment": args.ancestry,
            },
            "resolved_positions_manifest": os.path.join(
                os.path.dirname(args.coverage).replace("select", "score"), "dosages.tsv"),
            "resolved_weight_fraction": cov.get("resolved_weight_fraction"),
            "n_variants_in_score": score.get("n_variants_used_by_plink"),
            "r2_bar_used": cov.get("r2_bar"),
        },
        "what_the_provider_must_return": [
            "usable + reason (INT-2)", "source", "version_string (DATA-6/R6)",
            "ancestry_grouping_definition (INT-5)", "cohort_construction_rule (§17.2 fork)",
            "n_participants", "too_few_threshold_used", "assay_layer (INT-4)",
            "scoring_parity (R1)", "distribution",
        ],
    }
    lib_run.emit(request, args.out)
    lib_run.ledger_append(args.run, {"kind": "artifact", "stage": "interpret",
                                     "tool": "interpret_request.py", "artifact": args.out})


if __name__ == "__main__":
    main()
