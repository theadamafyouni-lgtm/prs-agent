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

The request also carries the file your raw score was computed from
(`runs/<RUN_ID>/score/plink_score_input.tsv`, as `score_plink2.py` recorded it) with its hash
and plink's own counts. The provider scores the reference cohort from that same file. Every
path in the request must lie inside `runs/<RUN_ID>/`; the harness refuses one that does not.

## 2. Ask the provider, and collect the answer

Building a reference can take hours, longer than one command is allowed to run. So send the
request without waiting:

```bash
python3 comms/harness_client.py request-reference --no-wait \
  --request runs/<RUN_ID>/interpret/request.json
```

It prints a request id (`r-...`) at once. Collect the reply with calls that each fit inside one
command, and repeat while the exit code is 6:

```bash
python3 comms/harness_client.py await-reference --id <that id> --timeout 540
```

You can still ask the person questions while it runs. Send one reference request at a time.

| client exit | what it means | what you do |
|---|---|---|
| 6 | still running | run `await-reference` again |
| 0 | the provider answered; the artifact's path is printed | pass that path to the adapter below |
| 3 | no usable reference could be built, or none was supplied | if a path is printed after the message, pass it to the adapter, which records the refusal; if not, run the adapter without `--reference` |
| 5 | the harness did not hand the request over, or replied with something unexpected; the reason is printed | not a reference outcome. If the reason names a path outside your run, the request named a file it should not have — rebuild it from this run's own files. Otherwise record the stage `blocked` with the printed reason |
| 4 | only from the blocking form (without `--no-wait`): nobody replied in time | use `--no-wait` and `await-reference` instead |

Then validate what came back. **This is the only thing that reads the artifact for you:**

```bash
python3 tools/reference_adapter.py --run <RUN_ID> \
  --request runs/<RUN_ID>/interpret/request.json \
  --reference <the path the client printed> \
  --out runs/<RUN_ID>/interpret/reference_response.json
```

| adapter exit | what it means | what you do |
|---|---|---|
| 0 | a usable reference was built, for this model and this score input | go to §3 |
| 5 | the provider could not build a usable one | **`INT-2`'s refusal, a correct outcome** (`JC-6`). Record `refuse` with the provider's reason and the participant count, and hand to `report`, which still has something honest to say |
| 4 | the artifact fails the contract, or is not for this request: a different model or scoring file, a different score input than the one it claims parity with, or malformed numbers | the adapter has written a `blocked` ledger entry with the detail. **Use no number from the artifact.** Leave the stage's judgment unmade and say why; this is not the provider refusing and not `stop` |
| 3 | no artifact was supplied | the adapter has written a `blocked` ledger entry. Leave the stage's judgment unmade, because none was made. **Do not record `stop`** — the data is not broken |

## 3. Place the score, and carry the caveats forward

`reference_response.json` carries `placement`: of the `n` reference participants' raw scores,
how many are below (`n_below`), equal to (`n_equal`) and above (`n_above`) this person's raw
score. Those counts are exact. Say where the person stands from them — for example "higher
than `n_below` of the `n` people in the comparison group". If you also give a percentile, say
how you turned the counts into one (how ties and the person's own position are counted): that
is a convention you chose, not a measurement. **Never read a position off the quantiles by
interpolating between them** — that is the approximate percentile this stage forbids.

Then read `warnings_for_report` and **carry every warning into report.** They are not
decoration:

- **`INT-4`, assay match.** An array-derived score placed against a WGS-derived reference
  bakes the imputed-versus-complete bias straight into the percentile. If the layers do
  not match, the report says so.
- **`scoring_parity`.** `same-resolved-set` means the cohort was scored from your own score
  input and plink used every one of its variants on both sides. Anything else — `other` with
  the provider's counts, or `full-model-set` — means the two scores cover different variants,
  the placement is biased, and the report says so.
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
  "placement": {"n": 0, "n_below": 0, "n_equal": 0, "n_above": 0},
  "percentile": null, "percentile_convention": null,
  "reference": {"source": "...", "version_string": "...", "n": 0,
                "assay_layer": "...", "scoring_parity": "..."},
  "caveats_for_report": ["..."],
  "reasons": ["..."],
  "evidence": [{"source": "runs/<RUN_ID>/interpret/reference_response.json", "field": "..."}]
}
JSON
```
