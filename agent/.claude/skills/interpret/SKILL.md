---
name: interpret
description: Stage 3. Place the raw score against an ancestry-matched, assay-matched population reference so the number means something. Refuses when a usable reference cannot be built (INT-1..INT-5).
---

# interpret

Place the raw score against an ancestry-matched population reference so the number means
something.

## The one thing to understand about this stage

**The reference is built, not looked up (`INT-1`).** No per-trait PRS percentile
distribution exists to query anywhere. It is constructed for this person, by running *the
same score* across a cohort genetically similar to them.

So `INT-2`'s refusal is **"we cannot build a usable one"** — too few sufficiently similar
participants for a stable distribution — not "no matched reference exists."

**Never substitute for it.** No synthetic distribution, no "approximate" percentile, no
borrowing a distribution from another trait or another cohort, no reasoning from published
population means. A raw score with no reference is not a weak result, it is *not a result*,
and presenting it as one is fabrication (`JC-2`).

## 1. Build the request

```bash
python3 tools/interpret_request.py --run <RUN_ID> \
  --score runs/<RUN_ID>/score/raw_score.json \
  --coverage runs/<RUN_ID>/score/coverage_<PGS>.json \
  --ancestry runs/<RUN_ID>/ancestry/ancestry.json \
  --provenance runs/<RUN_ID>/door/provenance.json \
  --model-download runs/<RUN_ID>/select/dl_<PGS>.json \
  --out runs/<RUN_ID>/interpret/request.json
```

The ancestry grouping in the request is **the same quantity `ANC-1` produced** (`INT-5`). It
flows `ANC-1` → interpret → `DATA-6`, which versions it. **Do not re-derive or re-label it
here.**

## 2. Call the provider

```bash
python3 tools/reference_adapter.py --run <RUN_ID> \
  --request runs/<RUN_ID>/interpret/request.json \
  --reference <provider artifact> \
  --out runs/<RUN_ID>/interpret/reference_response.json
```

| exit | what it means | what you do |
|---|---|---|
| 0 | a usable reference was built | go to §3 |
| 5 | the provider could not build a usable one | **`INT-2`'s refusal, a correct outcome** (`JC-6`). Record `refuse` with the provider's reason and the participant count, and hand to `report`, which still has something honest to say |
| 3 | no provider answered | the adapter has written a `blocked` ledger entry. Leave the stage's judgment unmade, because none was made. **Do not record `stop`** — the data is not broken |

## 3. Place the score, and carry the caveats forward

Take the percentile from the returned distribution. Then read `warnings_for_report` and
**carry every warning into report.** They are not decoration:

- **`INT-4`, assay match.** An array-derived score placed against a WGS-derived reference
  bakes the imputed-versus-complete bias straight into the percentile. If the layers do
  not match, the report says so.
- **`scoring_parity`.** If reference individuals were scored over all model positions while
  this person was scored over the resolved subset, the percentile is biased and the report
  says so.
- **`INT-5`, the membership rule.** A cohort is a set and needs either a discrete label or a
  distance cutoff, while the math treats ancestry as continuous. That fork is unresolved.
  **Name whichever rule the provider used in the report**, rather than hiding it.

## 4. Keep `SEL-11`'s floor and `INT-2`'s refusal separate

They are different failures, and they call for different things being said to the person, so
never describe one as the other:

- **the select floor is uncertainty** — not enough of the score's weight is resolvable, a
  property of the model plus the file
- **`INT-2`'s refusal is placement** — nothing valid to compare the score against, a
  property of the reference cohort

## 5. Record

```bash
python3 tools/record.py --run <RUN_ID> --stage interpret --name interpret --judgment - <<'JSON'
{
  "decision": "...", "outcome": "score|refuse|stop|ask",
  "requirement_ids": ["INT-1", "INT-2", "INT-4"],
  "percentile": null, "reference": {"source": "...", "version_string": "...", "n": 0,
                                    "assay_layer": "...", "scoring_parity": "..."},
  "caveats_for_report": ["..."],
  "reasons": ["..."],
  "evidence": [{"source": "runs/<RUN_ID>/interpret/reference_response.json", "field": "..."}]
}
JSON
```
