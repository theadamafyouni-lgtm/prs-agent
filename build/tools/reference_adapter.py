#!/usr/bin/env python3
"""reference_adapter.py -- the INT-1 boundary. Validates a provider's reference distribution,
or refuses.

This is the stub the build brief calls for, and the important thing about it is what it does NOT
do: there is no fallback, no synthetic distribution, no "approximate" mode, and no default
percentile. If the reference artifact is absent or invalid, interpret is blocked and says so.
Inventing a distribution to make the pipeline run would be the JC-2 violation the whole product
exists to avoid.

Contract: interfaces/reference-distribution.md

Exit codes:
  0  reference present and valid
  3  reference_unavailable  -- no artifact supplied or file missing
  4  reference_invalid      -- artifact present but fails the contract
  5  reference_unusable     -- provider itself reports usable=false (INT-2 refusal, a CORRECT outcome)

Usage:
  python3 tools/reference_adapter.py --run slice-001 \
      --request runs/slice-001/interpret/request.json \
      [--reference path/to/provider_artifact.json] \
      --out runs/slice-001/interpret/reference_response.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_run  # noqa: E402

REQUIRED = [
    "usable", "source", "version_string", "ancestry_grouping_definition",
    "cohort_construction_rule", "n_participants", "too_few_threshold_used",
    "assay_layer", "scoring_parity", "distribution",
]
REQUIRED_QUANTILES = ["p01", "p05", "p10", "p25", "p50", "p75", "p90", "p95", "p99"]
PARITY = {"same-resolved-set", "full-model-set", "other"}


def blocked(run, out, code, status, detail, extra=None):
    payload = {
        "tool": "reference_adapter.py",
        "generated_utc": lib_run.utcnow(),
        "status": status,
        "detail": detail,
        "interpret_can_proceed": False,
        "contract": "interfaces/reference-distribution.md",
        "no_fallback_note": "There is deliberately no fallback path. INT-1's reference is built, "
                            "not looked up, and a synthesised distribution would be a fabricated "
                            "result under JC-2.",
    }
    if extra:
        payload.update(extra)
    lib_run.emit(payload, out)
    if run:
        lib_run.ledger_append(run, {"kind": "blocked", "stage": "interpret",
                                    "tool": "reference_adapter.py", "status": status,
                                    "artifact": out})
    raise SystemExit(code)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run")
    ap.add_argument("--request", required=True)
    ap.add_argument("--reference")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if not os.path.exists(args.request):
        blocked(args.run, args.out, 4, "request_missing",
                f"no interpret request at {args.request}")
    request = lib_run.read_json(args.request)

    if not args.reference:
        blocked(args.run, args.out, 3, "reference_unavailable",
                "No reference distribution artifact supplied. INT-1 is built separately and does "
                "not exist yet, so interpret and report cannot close.",
                {"request_echo": request})
    if not os.path.exists(args.reference):
        blocked(args.run, args.out, 3, "reference_unavailable",
                f"reference artifact not found at {args.reference}", {"request_echo": request})

    try:
        ref = json.loads(open(args.reference).read())
    except json.JSONDecodeError as exc:
        blocked(args.run, args.out, 4, "reference_invalid", f"not valid JSON: {exc}")

    missing = [k for k in REQUIRED if k not in ref]
    if missing:
        blocked(args.run, args.out, 4, "reference_invalid",
                f"missing required contract fields: {missing}")

    if ref.get("usable") is False:
        blocked(args.run, args.out, 5, "reference_unusable",
                f"provider reports no usable reference could be built: {ref.get('reason')}. "
                f"Per INT-2 this is a correct refusal, not a failure.",
                {"provider_reason": ref.get("reason"),
                 "n_participants": ref.get("n_participants"),
                 "too_few_threshold_used": ref.get("too_few_threshold_used")})

    dist = ref.get("distribution") or {}
    missing_q = [q for q in REQUIRED_QUANTILES if q not in dist]
    if missing_q and not dist.get("raw_scores"):
        blocked(args.run, args.out, 4, "reference_invalid",
                f"distribution needs raw_scores or the full quantile set; missing {missing_q}")
    if ref.get("scoring_parity") not in PARITY:
        blocked(args.run, args.out, 4, "reference_invalid",
                f"scoring_parity must be one of {sorted(PARITY)}; got {ref.get('scoring_parity')!r}")

    warnings = []
    # assay_layer may be a bare string or the object form the provider uses. Compare like with
    # like: the provider's own assay_matched flag when it declares one, else the layer descriptor
    # against the patient's assay class. Never compare an object to a string -- that is always
    # unequal, so the warning fired unconditionally and put a raw dict into report-facing prose.
    layer = ref.get("assay_layer")
    patient_assay = request.get("patient", {}).get("assay_class")
    if isinstance(layer, dict):
        layer_desc = layer.get("reference") or "unstated"
        matched = layer.get("assay_matched")
        assay_mismatch = not matched if isinstance(matched, bool) else layer_desc != patient_assay
    else:
        layer_desc = layer
        assay_mismatch = layer != patient_assay
    if assay_mismatch:
        warnings.append(
            f"INT-4: the reference assay layer ({layer_desc}) does not match the patient's "
            f"assay class {patient_assay!r}. The R1 "
            f"imputed/partial-vs-complete bias is baked into any percentile from this pairing and "
            f"the report must say so.")
    if ref.get("scoring_parity") == "full-model-set":
        warnings.append(
            "scoring_parity is full-model-set: reference individuals were scored over all model "
            "positions while the patient was scored over the resolved subset. The percentile "
            "carries the R1 bias; the report must state it.")

    payload = {
        "tool": "reference_adapter.py",
        "generated_utc": lib_run.utcnow(),
        "status": "reference_valid",
        "interpret_can_proceed": True,
        "warnings_for_report": warnings,
        "reference": ref,
        "request_echo": request,
        "version_capture": {
            "source": ref.get("source"),
            "version_string": ref.get("version_string"),
            "ancestry_grouping_definition": ref.get("ancestry_grouping_definition"),
            "note": "DATA-6: recorded, not pinned. For All of Us this buys an audit trail, not "
                    "reproducibility -- only three CDRs stay live and v3-v6 cannot be migrated to, "
                    "so the reference expires rather than moves.",
        },
    }
    lib_run.emit(payload, args.out)
    if args.run:
        lib_run.ledger_append(args.run, {"kind": "facts", "stage": "interpret",
                                         "tool": "reference_adapter.py",
                                         "status": "reference_valid", "artifact": args.out})


if __name__ == "__main__":
    main()
