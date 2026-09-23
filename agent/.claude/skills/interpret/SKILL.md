---
name: interpret
description: Stage 3. Place the raw score against an ancestry-matched, assay-matched population reference so the number means something. Refuses when a usable reference cannot be built. BLOCKED until the INT-1 reference distribution exists — it is built separately.
---

# interpret

**Purpose:** place the raw score against an ancestry-matched population reference so the number
means something.

## Current state: blocked, by design

The reference distribution (`INT-1`) is being built separately and does not exist yet. This stage
is written against `interfaces/reference-distribution.md` and **cannot close until a provider
satisfies that contract.**

**Do not work around this.** No synthetic distribution, no "approximate" percentile, no borrowing
a distribution from another trait or another cohort, no reasoning from published population means.
A raw score with no reference is not a weak result — it is *not a result*, and presenting it as one
would be fabrication (`JC-2`). The correct behaviour is to say the stage is blocked and why.

## The one thing to understand about this stage

**The reference is built, not looked up (`INT-1`).** No per-trait PRS percentile distribution
exists to query anywhere. The distribution is constructed by running *the same score* across a
cohort genetically similar to this person. That is why `INT-2`'s refusal is **"we cannot build a
usable one"** — too few sufficiently similar participants for a stable distribution — rather than
"no matched reference exists."

## How to run it, once a provider exists

### 1. Build the request

```bash
python3 tools/interpret_request.py --run <RUN_ID> \
  --score runs/<RUN_ID>/score/raw_score.json \
  --coverage runs/<RUN_ID>/score/coverage_<PGS>.json \
  --ancestry runs/<RUN_ID>/ancestry/ancestry.json \
  --provenance runs/<RUN_ID>/door/provenance.json \
  --model-download runs/<RUN_ID>/select/dl_<PGS>.json \
  --out runs/<RUN_ID>/interpret/request.json
```

The ancestry grouping in the request is **the same quantity `ANC-1` produced** (`INT-5`) — it
flows `ANC-1` → interpret → `DATA-6`, which versions it. Do not re-derive or re-label it here.

### 2. Call the boundary

```bash
python3 tools/reference_adapter.py --run <RUN_ID> \
  --request runs/<RUN_ID>/interpret/request.json \
  --reference <provider artifact> \
  --out runs/<RUN_ID>/interpret/reference_response.json
```

Exit 3 = no reference (blocked). Exit 5 = the provider says no usable reference could be built —
that is **`INT-2`'s refusal, a correct outcome** (`JC-6`), not an error. Record it as `refuse` with
the provider's reason and the participant count, and hand to `report`, which still has something
honest to say to the person.

### 3. Place the score, and carry the caveats forward

Percentile from the returned distribution. Then read `warnings_for_report` and **carry every
warning into report** — they are not decoration:

- **`INT-4`, assay match.** An array-derived score placed against a WGS-derived reference bakes the
  `R1` imputed/partial-vs-complete bias straight into the percentile. If the layers do not match,
  the report says so.
- **`scoring_parity`.** If reference individuals were scored over all model positions while this
  person was scored over the resolved subset, the percentile is biased and the report says so.
- **`INT-5`, the membership rule.** A cohort is a *set*, so it needs a discrete label or a distance
  cutoff — but the math treats ancestry as a continuous quantity. That fork is open at spec level
  (§17.2); whichever rule the provider used gets named in the report, not hidden.

### 4. Keep `SEL-11`'s floor and `INT-2`'s refusal separate

They are **different failures**, not two ends of one linkage (§16-C):

- the select floor is **uncertainty** — not enough of the score's weight is resolvable, a property
  of the model plus the file;
- `INT-2`'s refusal is **placement** — nothing valid to compare the score against, a property of
  the reference cohort.

Never describe one as the other. They call for different things being said to the person.

### 5. Record

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

While blocked, record `outcome: "stop"` is **wrong** — the data is not broken. Record the blocked
state through `tools/reference_adapter.py`, which writes a `blocked` ledger entry, and leave the
stage's four-outcome judgment unmade, because it genuinely has not been made.
