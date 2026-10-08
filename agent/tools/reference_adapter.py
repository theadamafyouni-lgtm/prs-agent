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
  0  reference present and valid; `placement` carries the counts
  3  reference_unavailable  -- no artifact supplied or file missing
  4  reference_invalid      -- artifact present but fails the contract, or is for a different
                               model or score input than the request; also request_missing /
                               request_invalid when the request itself cannot be checked against
  5  reference_unusable     -- provider itself reports usable=false (INT-2 refusal, a CORRECT outcome)

On a valid reference it also COUNTS: how many reference raw scores fall below, equal to and above
the patient's raw score. It does not turn those counts into a percentile. That takes a convention
-- how ties and the patient's own position are counted -- and the convention is the agent's to
choose and to state, not a fact this tool can supply.

Usage:
  python3 tools/reference_adapter.py --run slice-001 \
      --request runs/slice-001/interpret/request.json \
      [--reference path/to/provider_artifact.json] \
      --out runs/slice-001/interpret/reference_response.json
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_run  # noqa: E402

REQUIRED = [
    "usable", "reason", "source", "version_string", "ancestry_grouping_definition",
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


def _finite(x):
    """A real number, not a bool and not NaN or infinite."""
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x))


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
    if not isinstance(ref, dict):
        blocked(args.run, args.out, 4, "reference_invalid", "the artifact is not a JSON object")

    missing = [k for k in REQUIRED if k not in ref]
    if missing:
        blocked(args.run, args.out, 4, "reference_invalid",
                f"missing required contract fields: {missing}")

    # Shape checks that hold for a refusal too: a refusal for a different model is not this
    # request's refusal, and a `usable` that is not a real boolean is not a statement at all.
    if not isinstance(ref["usable"], bool):
        blocked(args.run, args.out, 4, "reference_invalid",
                f"usable must be true or false; got {ref['usable']!r}")
    if not isinstance(ref["reason"], str) or not ref["reason"].strip():
        blocked(args.run, args.out, 4, "reference_invalid",
                f"reason must be a non-empty string; got {ref['reason']!r}")
    want_model = request.get("model") or {}
    got_model = ref.get("model") if isinstance(ref.get("model"), dict) else {}
    for key in ("pgs_id", "sha256"):
        if not want_model.get(key) or got_model.get(key) != want_model.get(key):
            blocked(args.run, args.out, 4, "reference_invalid",
                    f"artifact model.{key} is {got_model.get(key)!r} but the request's is "
                    f"{want_model.get(key)!r}; a reference built for another scoring file is "
                    f"not a reference for this score")

    if ref.get("usable") is False:
        blocked(args.run, args.out, 5, "reference_unusable",
                f"provider reports no usable reference could be built: {ref.get('reason')}. "
                f"Per INT-2 this is a correct refusal, not a failure.",
                {"provider_reason": ref.get("reason"),
                 "n_participants": ref.get("n_participants"),
                 "too_few_threshold_used": ref.get("too_few_threshold_used")})

    dist = ref.get("distribution")
    if not isinstance(dist, dict):
        blocked(args.run, args.out, 4, "reference_invalid", "distribution must be an object")
    missing_q = [q for q in REQUIRED_QUANTILES if q not in dist]
    if missing_q:
        blocked(args.run, args.out, 4, "reference_invalid",
                f"distribution lacks quantiles {missing_q}")
    qs = [dist[q] for q in REQUIRED_QUANTILES]
    if not all(_finite(v) for v in qs):
        blocked(args.run, args.out, 4, "reference_invalid",
                f"distribution quantiles must be finite numbers; got {qs}")
    if any(b < a for a, b in zip(qs, qs[1:])):
        blocked(args.run, args.out, 4, "reference_invalid",
                f"distribution quantiles decrease somewhere along p01..p99: {qs}")
    n = dist.get("n")
    if not (isinstance(n, int) and not isinstance(n, bool) and n > 0):
        blocked(args.run, args.out, 4, "reference_invalid",
                f"distribution.n must be a positive integer; got {n!r}")
    # Every reference score, so the patient can be placed by counting rather than by reading
    # between nine quantiles -- which would be the approximate percentile interpret forbids.
    raw = dist.get("raw_scores")
    if not isinstance(raw, list) or len(raw) != n or not all(_finite(v) for v in raw):
        blocked(args.run, args.out, 4, "reference_invalid",
                f"distribution.raw_scores must be a list of {n} finite numbers, one per "
                f"reference participant")
    ids = dist.get("raw_score_sample_ids")
    if ids is not None and (not isinstance(ids, list) or len(ids) != n):
        blocked(args.run, args.out, 4, "reference_invalid",
                f"distribution.raw_score_sample_ids must list {n} ids, one per raw score")
    if ref.get("scoring_parity") not in PARITY:
        blocked(args.run, args.out, 4, "reference_invalid",
                f"scoring_parity must be one of {sorted(PARITY)}; got {ref.get('scoring_parity')!r}")
    patient = request.get("patient") or {}
    if ref.get("scoring_parity") == "same-resolved-set":
        # The claim is that the cohort was scored from the patient's own score input. The hash
        # is how that is checked rather than taken.
        got_sha = (ref.get("scoring_parity_detail") or {}).get("score_input_sha256")
        if not patient.get("score_input_sha256") or got_sha != patient.get("score_input_sha256"):
            blocked(args.run, args.out, 4, "reference_invalid",
                    f"scoring_parity is same-resolved-set but the artifact's score input "
                    f"sha256 is {got_sha!r} and the request's is "
                    f"{patient.get('score_input_sha256')!r}")

    x = patient.get("raw_score")
    if not _finite(x):
        blocked(args.run, args.out, 4, "request_invalid",
                f"the request's patient.raw_score is {x!r}; there is nothing to count against")
    placement = {
        "patient_raw_score": x,
        "n": n,
        "n_below": sum(1 for v in raw if v < x),
        "n_equal": sum(1 for v in raw if v == x),
        "n_above": sum(1 for v in raw if v > x),
        "compared_against": "reference.distribution.raw_scores",
        "note": "Counts only, by exact comparison. No percentile is computed here: turning these "
                "counts into one takes a convention for ties and for the patient's own "
                "position, and that is the agent's to choose and to state. Both sides are "
                "plink2 SCORE1_SUM values at the precision plink2 prints them.",
    }

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
    if ref.get("scoring_parity") == "other":
        warnings.append(
            "scoring_parity is other: the provider states "
            f"{(ref.get('scoring_parity_detail') or {}).get('statement')!r}. The reference "
            "and the patient were not scored over the same variant set; the report must say "
            "so.")
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
        "placement": placement,
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
