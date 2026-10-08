# INT-1 interface — the reference distribution

`interpret` and `report` are built against this contract and **cannot close until a provider
satisfies it.** The reference is being built separately. Nothing in this repo synthesises one:
`tools/reference_adapter.py` refuses when the artifact is absent, because inventing a distribution
to make the pipeline run would be the `JC-2` violation the product exists to avoid.

---

## What the reference is, and is not

**`INT-1` — the reference is built, not looked up.** There is no per-trait PRS percentile
distribution sitting anywhere to query. The distribution is **constructed** for this trait and this
patient by running the same score across a cohort genetically similar to them. "Enough similar
participants are likely to exist" is not the same as "a reference is waiting."

Consequently **`INT-2`'s refusal condition is not "no matched reference exists"** — it is
**"a usable one cannot be built"**: too few sufficiently similar participants to form a stable
distribution. The threshold for "too few" is open at spec level and the provider must state what
it used.

---

## Request — what the agent supplies

```json
{
  "trait": {"id": "MONDO_0004981", "label": "atrial fibrillation"},
  "model": {"pgs_id": "PGS0XXXXX", "hmPOS_build": "GRCh37", "hmPOS_date": "YYYY-MM-DD",
            "sha256": "...of the exact scoring file used..."},
  "patient": {
    "raw_score": 0.0,
    "assay_class": "genotyping-array",
    "ancestry_grouping": {"group": "...", "method": "...", "coordinates": [...]},
    "resolved_positions_manifest": "runs/<run>/score/dosages.tsv",
    "score_input": "runs/<run>/score/plink_score_input.tsv",
    "score_input_sha256": "...of that file...",
    "n_variants_in_score_input": 0,
    "n_variants_in_score": 0,
    "resolved_weight_fraction": 0.0
  }
}
```

`ancestry_grouping` is **the same quantity `ANC-1` produced** — it flows `ANC-1` → here →
`DATA-6`, which versions it. Do not re-derive it.

`score_input` is the file the patient's raw score was computed from (`ID EA BETA`: `chrom:pos`,
the allele actually used after any strand flip, the weight), as `score_plink2.py` recorded it,
with its sha256. `n_variants_in_score_input` and `n_variants_in_score` are plink's own counts
for the patient: variants handed to it and variants it used. The provider scores the cohort
from this same file, which is what `scoring_parity` is checked against.

Every file path in the request must resolve, links included, inside the requesting run's own
`runs/<run>/` directory. The harness refuses a request that names anything else rather than
fetching it.

## Response — what the provider must return

Every field is required. A response missing any of them is not usable and `interpret` refuses.

| Field | Why it is required |
|---|---|
| `usable` (a JSON boolean) + `reason` (a non-empty string) | `INT-2`: the provider states whether a usable reference could be built, and if not, why. A refusal here is a correct outcome. |
| `model.pgs_id` + `model.sha256` | equal to the request's. A reference built from a different scoring file is not a reference for this score, refusal or not. |
| `source` | which dataset. **All of Us and 1000 Genomes are not interchangeable — see the conflict below.** |
| `version_string` | `DATA-6`/`R6`: the All of Us CDR string (`C####Q#R#` / `R####Q#R#`) read off the fully-qualified dataset reference actually queried, or the equivalent release identifier. |
| `ancestry_grouping_definition` | `INT-5`: the same CDR under a different grouping yields different percentiles, so the grouping must travel with the version. |
| `cohort_construction_rule` | `INT-5`/§17.2 fork: a discrete label or a distance cutoff. A cohort is a set, but the math treats ancestry as continuous — whoever builds this **picks a rule for the slice and flags it**; the spec fork stays open. |
| `n_participants` | `INT-2`'s "too few" is judged against this. |
| `too_few_threshold_used` | the value the provider applied, since the spec leaves it open. |
| `assay_layer` | `INT-4`: an array-derived score must be placed against an **array-derived** reference, or the imputed/partial-vs-complete bias (`R1`) is baked into the percentile. |
| `scoring_parity` | how reference scores were computed: same model file, and **whether they used the same resolved variant set as the patient**. See below. |
| `distribution` | the quantiles `p01,p05,p10,p25,p50,p75,p90,p95,p99` (finite, non-decreasing), `mean`, `sd`, `n` (a positive integer), and **`raw_scores`: every participant's raw score, `n` of them**, with `raw_score_sample_ids` naming each one at the same index. The patient is placed by counting against `raw_scores`; nine quantiles would leave interpolation, which is an approximate percentile. On a refusal every value is null. |

### `scoring_parity` is the one most likely to be got wrong

`R1` is unresolved: if the patient's score is computed on an imputed/partial variant set but the
reference was built on more complete data, the percentile is **subtly biased**, and the extreme
consumer case — a few-hundred-thousand-marker array placed against a WGS-scale reference — is the
far end of that bias. So the provider must declare one of:

- `same-resolved-set` — reference individuals scored from the patient's own `score_input` by the
  patient's plink2 recipe, and plink used every one of its variants for the patient and for every
  reference participant. Removes the bias by construction. Preferred. Claimed only when
  `scoring_parity_detail.score_input_sha256` equals the request's `score_input_sha256` and the
  counts in `scoring_parity_detail` agree; the adapter refuses the claim otherwise.
- `full-model-set` — reference scored over all model positions. **Then the percentile carries the
  R1 bias and the report must say so**, quantified if possible.
- `other` — described, in `scoring_parity_detail.statement`, with the counts: for example the
  reference scored from the patient's score input but plink used fewer of its variants there
  than for the patient. The percentile carries a bias of the same kind and the report says so.

`pgsc_calc`'s built-in adjustment is explicitly **not** the answer here, since it is not this
product's scoring tool.

---

## Known conflict, unresolved — read before implementing

**Spec §9 `INT-1` names All of Us. The build brief names a 1000 Genomes reference.** They are not
interchangeable, and two spec requirements land on the difference:

1. **`INT-4` is argued specifically from All of Us's GDA genotyping-array layer (>312,920
   participants).** That is what makes an assay-matched reference *buildable*. 1000 Genomes is
   WGS-derived and has no comparable array layer, so the spec's own argument does not transfer,
   and an array-derived score placed against it inherits exactly the `R1` bias `INT-4` exists to
   prevent.
2. **`INT-2` gets much tighter.** All of Us's diversity is the stated reason enough similar
   participants are likely. 1000 Genomes phase 3 is 2,504 samples total; a single continental
   group is in the hundreds (SAS n=489). Whether that supports a stable per-trait distribution is a
   real question.

This interface is deliberately **source-agnostic** so either provider can satisfy it — but the
`source`, `assay_layer` and `n_participants` fields make the mismatch **visible in the response**
rather than silent in the percentile. Tracked as OPEN_QUESTIONS Q2.

---

## Using it

The request goes to the provider through the transport, and the build can take hours, so it is
sent without waiting and collected in calls that each fit inside one command (exit 6 means still
running; call again):

```bash
python3 comms/harness_client.py request-reference --no-wait \
    --request runs/<RUN_ID>/interpret/request.json          # prints a request id
python3 comms/harness_client.py await-reference --id <id> --timeout 540
```

The path it prints goes to the adapter:

```bash
python3 tools/reference_adapter.py --run <RUN_ID> \
    --request runs/<RUN_ID>/interpret/request.json \
    --reference <path to the provider's artifact> \
    --out runs/<RUN_ID>/interpret/reference_response.json
```

Without `--reference` it exits 3, `reference_unavailable`. With an artifact that fails any check
above it exits 4, `reference_invalid`. A provider refusal exits 5, `reference_unusable`. In each
case `interpret` records a **blocked** state or the refusal; it does not fall back to anything.

On exit 0 the response carries `placement`: `n`, and how many of the `n` raw scores are below,
equal to and above the patient's raw score. Counts, by exact comparison, and nothing else. Turning
them into a percentile takes a convention for ties and for the patient's own position; that is
the agent's to choose and to state, so the adapter does not.
