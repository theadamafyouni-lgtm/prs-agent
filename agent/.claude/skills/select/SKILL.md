---
name: select
description: Stage 1. From the trait plus the person's genetic ancestry, find candidate scoring models in the PGS Catalog, weigh them on ancestry match and validation quality, apply the coverage floor, recommend one and confirm before using it. Interactive, not silent (SEL-1..SEL-13).
---

# select

**Purpose:** from the trait plus the person's ancestry, find candidate models, weigh them,
recommend one, and confirm before using it.

Runs after the door and after `ancestry` — `SEL-5` needs ancestry up front.

## 0. First move: surface any ancestry mismatch (`ANC-3`)

**Before any model is picked.** If the self-report and the inference disagree, show it, explain
that the math uses the genome because models are keyed to genetic ancestry, and continue unless
the person ends the session. Not a hard blocker. Never silently resolved.

## 1. Resolve the trait to an ontology term (`SEL-1`)

The person says a trait in their own words. Models are filed under ontology terms. **Resolving one
to the other is a judgment, and getting it wrong makes every step after it answer the wrong
question.**

```bash
python3 tools/pgs_catalog.py --run <RUN_ID> --out runs/<RUN_ID>/select/trait_search.json \
    trait-search --term "<trait as the person said it>"
```

The search matches on strings, so it fails in both directions and you have to expect both:

- **It returns nothing for a real trait.** A phrase with a slash, an abbreviation, a British
  spelling, or two conditions in one breath will match no term while the condition itself is well
  populated. Nothing returned is **not** evidence that no model exists. Try the plain clinical name
  and the common name before concluding anything.
- **It returns a term whose models answer a different question.** A term can look like the disease
  and be filed with models for a measurement, a complication, a registry residual category, or a
  different disease with a similar name.

So resolve on what the models under a term actually report, not on the term's label. Read
`trait_reported` — the authors' own words for what the score predicts. **Where a model's
`trait_reported` and its mapped term disagree, the model's own account is the better evidence.**

More than one term can be right. A broader or narrower name for the same disease is still that
disease. What is not right is a term whose models predict something that **causes or precedes**
the condition rather than the condition itself — a risk factor, an exposure, an upstream
measurement. A measurement that *defines* the condition is the condition; one that *predicts* it
is not.

If you cannot resolve the trait to anything the Catalog holds, that is a **refuse**, and say which
of the two it is: nothing is published for this trait, or nothing published answers the question
this person asked.

## 2. Enumerate the candidates (`SEL-1`)

```bash
python3 tools/pgs_catalog.py --run <RUN_ID> --out runs/<RUN_ID>/select/scores_all.json \
    scores --trait-id <TRAIT_ID>
python3 tools/pgs_catalog.py --run <RUN_ID> --out runs/<RUN_ID>/select/performance.json \
    performance --pgs <comma-separated candidate ids>
```

Candidates come only from the PGS Catalog. Nowhere else. The tool fetches and dumps; **you** do
the weighing.

**Enumerate before you rank.** A shortlist assembled from the first few results is not a candidate
pool, and a model you never enumerated cannot be ranked below the one you chose.

`SEL-1` applies no model-class filter, so the catalog can hand you a model `score` cannot compute
— dosage-per-genotype, recessive, dominant, interaction, diplotype, haplotype (`SCO-9`). Whether
select filters on computability or score defines a refusal is **unresolved**. Until it is: check `weight_type` and the scoring file header, prefer an additive weighted sum, and if a
non-additive candidate would otherwise win, **say so rather than silently skipping it**.

## 3. Weigh on ancestry match and validation quality — never on variant count (`SEL-2`)

Ranking is two questions in order: **does this model apply to this person at all**, then **how good
is the evidence that it works**. The first is a floor. The second decides.

### 3a. The floor: does it apply to this person

**Ancestry match means genetic distance between the model's evidence and this person, not a label
match.** A model tagged with the right label but developed and validated somewhere else is not a
match.

A candidate clears the floor if **either** holds:

- it was **developed** in this person's inferred group — read `ancestry_distribution.dev`, and read
  it as a proportion, not a tag. `EUR:100` is European-only development however the evaluation
  reads; or
- it was **evaluated** in this person's group **and the evaluation reports metrics that show the
  score works** — a performance record whose sampleset carries that ancestry, with numbers in it.

**An ancestry listed in the metadata with no performance record behind it is a label with nothing
behind it.** Say that plainly rather than counting it as evidence. This is common enough that you
should check the performance records rather than trusting `ancestry_distribution.eval`.

**Read the ancestry strings by prefix, not by equality.** The Catalog writes them in long forms, so
an exact string comparison silently drops whole populations and a model reads as having no
in-ancestry evidence when it has plenty.

Nothing below the floor is acceptable, however good its numbers look elsewhere.

### 3b. Above the floor: how good is the evidence

Rank on the quality of the evidence **in this person's group**. In rough order of what moves a
model up:

- **An effect the score produced on its own** beats one produced with covariates. A per-SD OR, HR
  or beta is the PGS's own effect. **A headline AUROC can be mostly age, sex and principal
  components.** Where a record reports both, read the covariate-free figure and the increment over
  covariates separately, and rank on those. A model whose incremental value over the covariates is
  near zero adds nothing, whatever its headline says.
- **An interval that excludes the null.** An effect whose confidence interval crosses 1.0, or zero
  for a correlation, is not evidence of anything. Treat it as absent, not as weak.
- **Evaluation in a cohort the model was not trained in.** Check the development sample against the
  evaluation cohorts by name. A model evaluated in the cohort it was fitted in is reporting
  in-sample performance, and that is not independent evidence.
- **Sample size and case count.** A hundred cases and a thousand are not the same claim. Say the
  number rather than treating any published metric as equivalent.
- **Independent lines of evidence, not repetitions of one.** Several scores from a single study,
  differing only by algorithm or weighting, are one line of evidence. So are several evaluations of
  one model within one cohort. Depth is how many separate studies and separate populations support
  it, not how many rows the Catalog holds.
- **Consistency.** Contradictory metrics for the same model across cohorts — strong in one, near
  chance in another — are themselves a finding about that model's reliability, not noise to average
  away.

### 3c. Out regardless of how good the numbers are

A model can clear the floor, have excellent evidence, and still be the wrong instrument:

- **It answers a different question.** A model that predicts severity among people who already have
  the condition does not predict who gets it. A model for a subtype does not answer a general
  question about the disease, and a model for a complication of the disease is not a model for the
  disease. Read what the model reports, not the term it is filed under.
- **It requires a fact this person's file cannot supply.** Some models are fitted within a stratum —
  a BMI band, an age band, a treatment group. If nothing in the file or the conversation determines
  which stratum this person is in, choosing one is a guess. Ask if the person can answer it; if they
  cannot, the model is unusable for them.
- **It targets a different population than the one whose weights it carries.** Some scores are built
  for one group using another group's summary statistics, and the naming often says so. The target
  is what matters.
- **It cannot be computed as an additive weighted sum.** See `SCO-9` and §2.

### 3d. What does not rank

**Raw variant count is the wrong variable.** Density is non-monotonic: a population-optimised
670–850k array can beat a globally-designed 1.9M one (doi:10.1038/s41598-022-22215-y). A model with
7 million variants is not better than one with 2,000 — and against a sparse consumer array it may
be much worse. Do not sort on it, and do not let it stand in for quality.

**Coverage does not sort either.** It is a floor (`SEL-11`), applied after the ranking.

**Recency does not sort.** A newer score is not a better one.

### 3e. Say what the choice costs

Every candidate you rank has a cost, and the one you recommend has one too. Name it: the evidence
is thin, or in-sample, or covariate-inclusive, or from one study, or in a cohort unlike this person.
**A recommendation with no stated cost is one you have not finished making.**

## 4. Ask the follow-ups that change the choice (`SEL-3`)

Select is **interactive, not silent**. Model validation is not uniform: some models were built or
validated on already-diagnosed patients, others on a general or intermediate-risk cohort, and that
changes which model fits *this* person.

Ask what you need — for example whether they have been diagnosed with the trait, and when, or which
of two conditions they meant. Judge whether the answer is a **hard blocker** (you genuinely cannot
pick a model without it) or something you can proceed past with the uncertainty stated (`JC-4`). If
it is a hard blocker and the person cannot or will not answer, that is a **refuse**, not a guess
(`JC-5`).

**Asking is a step, not an outcome.** It resolves once they answer, and then the stage carries on to
its judgment. A run never ends on a question.

Expect the person not to cooperate cleanly. "I don't know", an unsure answer, a contradiction of
something said earlier, or an unrelated question mid-flow are all normal and must not derail the
stage.

## 5. The coverage floor (`SEL-11`) — a floor, not a sort

```bash
python3 tools/pgs_catalog.py --out runs/<RUN_ID>/select/dl_<PGS>.json \
    download --pgs <PGS_ID> --build <patient's build> --out-dir runs/<RUN_ID>/select/models

# read the header of the file you actually downloaded, every time (SEL-13)
python3 tools/file_facts.py --input <the downloaded .txt.gz> \
    --out runs/<RUN_ID>/select/scorefile_facts_<PGS>.json

python3 tools/weight_coverage.py estimate \
    --scorefile <the downloaded .txt.gz> \
    --chr-col hm_chr --pos-col hm_pos --effect-weight-col <as the header actually names it> \
    --patient-vcf runs/<RUN_ID>/door/normalized.vcf.gz \
    --panel-index refdata/panel/sites_index \
    --high-beta-fraction 0.05 \
    --run <RUN_ID> --out runs/<RUN_ID>/select/coverage_est_<PGS>.json
```

**Read the caveat in the output and mean it.** This estimate is structurally blind to imputability:
it sees weight sitting on positions missing from the patient's file, but it cannot see r²/INFO until
imputation actually runs. `resolved_weight_fraction` here is an **upper bound** on what `score` will
measure. Say "estimate" to the person, not "coverage".

The gate is `SCO-8`'s two conditions:

1. **Weighted coverage** — refuse if resolved Σ|β| < **0.90**.
2. **High-β condition** — refuse if any single unresolved locus carries **≥ 5%** of total Σ|β|,
   reported **individually by locus, never summed into the fraction**.

Below the floor, **refuse** (`SEL-10`) — do not offer "want it anyway?". Offering a hopeless choice
is its own fake consent. Above it, the survivors keep their ancestry/validation ranking; coverage
does not reorder them.

## 6. Confirm before use — which gate (`SEL-4`, `SEL-6`/`SEL-7`)

The line is **not** technical difficulty. It is who the judgment belongs to:

| | |
|---|---|
| **Technical judgment → yours** (`SEL-8`) | which model validates better, whether coverage is enough, whether the build matches. Handing these to the person is **fake consent**. |
| **Value judgment → theirs** (`SEL-9`) | whether to proceed with a weak estimate, given it barely says anything. Only they can make that call. |

- **Transparency gate (default, `SEL-6`)** — you picked a clearly-best model. Show the choice and
  the reasoning in plain language; the person acknowledges. A stamp, honestly labelled as one.
- **Decision gate (triggered, `SEL-7`)** — a real fork you should not resolve alone. The person
  decides. A fork of this kind looks like: the model that matches their ancestry best is the one
  their file covers worst, and every fallback costs something different — a worse-matched model, a
  weaker effect, or metrics that contradict each other. If you meet that trade it is theirs, and it
  is above the floor, not below it.

## 7. The select → score → reselect loop (`SEL-12`)

Because §5 selects on an *estimate*, a candidate can look acceptable here and prove worse once
scored. Take the top-ranked candidate → let `score` produce the real number → if it is **materially
worse**, come back and take the next candidate down the list.

- **You decide what "materially worse" means, each time.** There is deliberately no threshold; a
  threshold here would be a rule engine, and `ARCH-5` says the agent judges.
- **Never re-score a model already scored.** Out of candidates → **refuse**.
- **First acceptable wins.** Do not score every candidate and compare — that is selecting on the
  outcome.

## 8. Record the choice

```bash
python3 tools/record.py --run <RUN_ID> --stage select --name model_choice --judgment - <<'JSON'
{
  "decision": "...",
  "outcome": "proceed|refuse|ask",
  "requirement_ids": ["SEL-2", "SEL-11", "..."],
  "trait_id": "...", "chosen_model": "...", "gate_type": "transparency|decision",
  "candidates_considered": [{"pgs_id": "...", "why_ranked_here": "...", "cost_of_choosing_it": "..."}],
  "questions_asked": [{"question": "...", "answer": "...", "hard_blocker": true}],
  "coverage_estimate": {"resolved_weight_fraction": 0.0, "is_an_estimate": true},
  "reasons": ["..."],
  "evidence": [{"source": "runs/<RUN_ID>/select/...", "field": "..."}]
}
JSON
```

`trait_id` is one ontology id as a bare string. `chosen_model` is one PGS id as a bare string —
not an object, not a list, and not an id with a name or citation appended. Everything else you
weighed goes in `candidates_considered`.
