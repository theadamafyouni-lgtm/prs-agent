---
name: score
description: Stage 2. Match the model to the patient's build, resolve variants and strand, decide what each missing position means from the carried assay class, impute, apply the coverage gate, and compute the weighted sum. Never assumes absence equals reference (SCO-1..SCO-10).
---

# score

**Input:** the normalized file **and the provenance the door carried with it**. Score does **not**
re-derive the assay class — read `source_assay_class` from `runs/<RUN_ID>/door/provenance.json`
(`SCO-5`). That is the key name, not the bare `assay_class`, which exists only in the door's
`classification.json`; `interpret_request.py:64` reads the same field out of the same file the same
way. Read the provenance, never the file extension: a 23andMe txt converted to a VCF looks like a
VCF and has array semantics.

## 1. Build: match the model to the patient, do not lift over (`SCO-2`)

The PGS Catalog harmonizes to both GRCh37 and GRCh38 — a sweep of all 5,450 scores found
5,450/5,450 available in both — so **download the model in the patient's build** and liftover
never arises. `SCO-3`'s fallback survives only for custom non-catalog files; if you ever reach for
it, note that liftover does worse than drop variants: ~11 Mb of the genome is inverted between the
builds, and for a **palindromic SNP in an inverted region the reverse-complemented base matches a
valid forward-strand nucleotide, so the error escapes standard detection** — silent and
uncountable, unlike a dropped variant.

## 2. The core judgment: what does a missing position mean here? (`SCO-5`, `SCO-6`)

Read the kind of file first; what absence means **follows from that**. Read `source_assay_class`
from `runs/<RUN_ID>/door/provenance.json` and apply the rule for the class you actually find. The
door made that call; this stage neither re-derives it nor assumes which class it will be.

> **genotyping-array → impute. Never score-as-reference.** There is no positive hom-ref evidence anywhere in
> an array file. A position is either not on the chip (never interrogated) or an on-chip no-call.
> Assigning dosage 0 would inject the downward missing-SNP bias, and it is the `JC-7` error.

The other classes, and do not apply an array's rule to any of them: **gVCF** — score-as-reference
only inside a hom-ref block with `DP≥10` and `GQ≥20` present, impute low-confidence blocks and
gaps. **WGS** — impute by default, score-as-reference only inside verified callable regions.
**WES** — decide on-target vs off-target first (needs the capture BED), then coverage. In every
case the deciding quantity is **per-site DP from a coverage track, never genome-average depth**: a
30× WGS delivered as a plain variant-only VCF licenses assume-reference at no depth at all.

`GQ≥20` is a genotype-filtering convention, not a threshold traceable to a primary PRS source —
a tunable default, and labelled as one.

## 3. Impute (`SCO-7`, `SCO-10`)

**Imputation is not a last resort.** When the file supports it, impute rather than drop or assume.

Name which imputation you mean. This pipeline does **panel-based imputation** — inference from
this person's own haplotypes. It never does **reference-AF mean-imputation** (filling a genotype
with 2× the population frequency), which inserts a population average and presents it as this
person's dosage. Whether that counts as fabrication under `JC-2` is collision 2, flagged and
unresolved at spec level; this build sidesteps it by never doing it.

**Impute the person JOINTLY with a hold-out cohort, not alone.** Beagle's `DR2` is a per-variant
statistic estimated across the target samples, so one person imputed by themselves leaves it
nothing to vary over and it returns ~0 at almost every site. That is not a low-quality
measurement, it is **no measurement**, and the `SCO-8` gate below then reads unmeasurable
positions as failures. Measured on this build: solo imputation put ~68% of positions at r2~0.

Three steps.

**(a) The positions their file actually types.** The hold-out cohort is masked to these, so the
cohort is genotyped as if on the same chip rather than carrying full sequence.

```bash
bcftools query -f '%CHROM\t%POS\n' runs/<RUN_ID>/door/normalized.vcf.gz \
  > runs/<RUN_ID>/score/patient_typed_positions.tsv
```

**(b) The cohort split.** `--super-pop` is **your ancestry judgment**, read from `inferred_group`
in `runs/<RUN_ID>/ancestry/ancestry.json` -- not a label the person gave you (`ANC-4`). Size and
seed have no defaults and are yours to choose and record.

```bash
python3 tools/build_dr2_cohort.py \
  --panel refdata/ancestry-ref/integrated_call_samples_v3.20130502.ALL.panel \
  --super-pop <your inferred group> --size <you choose> --seed <you choose> \
  --out-dir runs/<RUN_ID>/score/dr2
```

Hold-out and reference are disjoint by construction, so no sample imputes itself. **The batch size
is unjustified** -- 301 is what has been run, reached by widening from 100 until the answer
stabilised, not derived. It trades against DR2 precision. Say which you used and that it was
chosen, not derived.

**(c) Impute.** One Beagle run per chromosome over the patient plus the cohort, so the DR2 that
reaches the gate came out of the same run as the dosages. Nothing is transplanted from elsewhere.

```bash
python3 tools/impute_joint.py --run <RUN_ID> \
  --target runs/<RUN_ID>/door/normalized.vcf.gz --patient-id <sample name in that VCF> \
  --holdout-samples runs/<RUN_ID>/score/dr2/dr2_holdout.txt \
  --reference-samples runs/<RUN_ID>/score/dr2/dr2_reference.txt \
  --phase3-dir refdata/ancestry-ref/phase3 --map-dir refdata/maps \
  --typed-positions runs/<RUN_ID>/score/patient_typed_positions.tsv \
  --chromosomes 1-22 --seed <the same seed> --threads 6 --xmx 8g \
  --out-dir runs/<RUN_ID>/score/imputed_joint
```

**This is slow -- roughly 15-25 minutes per chromosome, so several hours for the genome.** That
is the accuracy-over-speed choice and it is expected; do not take a faster path that skips it. It
writes one patient-only VCF per chromosome into `--out-dir`, which is what every stage after this
reads, and the multi-sample cohort output into `--out-dir/cohort/`.

> ### You have to WAIT for it, and waiting is part of the stage
>
> The command runs for hours, which is longer than a single tool call can be. So background it --
> and then **keep polling until it is finished**. Do not report that imputation is running and end
> your turn. **A backgrounded job dies when your turn ends**, and the run is torn down with it: on
> 2026-08-05 exactly that happened, and a run that had done everything right through select lost
> the whole score stage six minutes into chr1.
>
> ```bash
> nohup python3 tools/impute_joint.py --run <RUN_ID> ... \
>   > runs/<RUN_ID>/score/impute_joint.log 2>&1 &
> ```
>
> Then poll. Each poll is a short command, so each one fits inside a tool call:
>
> ```bash
> sleep 600; ls runs/<RUN_ID>/score/imputed_joint/*.vcf.gz 2>/dev/null | grep -vc cohort
> ```
>
> **It is done when `runs/<RUN_ID>/score/imputed_joint/impute_joint.json` exists** -- the tool
> writes it once, at the end, and it carries `n_ok`, `n_failed` and `n_skipped`. Read those before
> going on: a chromosome that failed is recorded there with its stderr rather than raised, so the
> file existing is not by itself success.
>
> Poll on a long interval and keep going. Twenty or so polls at ten minutes each is the expected
> shape of this stage, not a sign something is wrong. If the count stops advancing for several
> polls, read the newest `chr<N>.beagle.log` in that directory before concluding anything.
>
> **Every poll must return. Never block waiting for a file.** A command like
> `until [ -f .../chr4.vcf.gz ]; do sleep 900; done` looks strictly better than repeated short
> polls, and it is what kills this stage. It blocks inside a single tool call, the call is cut
> off at the tool's own ceiling, and when the last such command dies your turn ends with it --
> the harness reads that as the run finishing and tears the sandbox down over the still-running
> job. On 2026-08-06 a run did precisely this: it armed four staged watchers, wrote "watchers
> armed, standing by for imputation to complete", and lost the stage at chr2. The same applies
> to `wait`, to `while [ ! -f ... ]`, and to any single `sleep` long enough to outlast a tool
> call. Ten minutes returns. Fifteen might not.
>
> **The round-trips are not waste, and this is the trap.** Reasoning that repeated status checks
> cost round-trips without changing anything is correct about the checks and wrong about what
> they are for: they are the only thing keeping your turn alive, and your turn is the only thing
> keeping imputation alive. Twenty short calls to protect five hours of compute is the trade.
> Do not optimise them away.
>
> The one thing you must not do is treat "I started it" as "it is done".

`tools/impute.py` is the older single-sample path. It does not produce usable DR2 and is not the
route for a patient being scored.

**The seed is a scoring parameter, not an implementation detail** (`SCO-10`). Stochastic
pre-phasing moves an individual's percentile with no change to the genetic model at all — up to
~9.19% of individuals shift by more than 10 percentile points under a stochastic pipeline versus
~0.1% under a deterministic one. Local Beagle with a fixed seed, not a server pipeline.

**Note what the panel cannot reach.** The staged panel is chr1–22, so chrX model positions cannot
be imputed by any route here. They are not "assumed reference" — they count as unresolved weight
and flow into the gate. Say so rather than letting them vanish.

## 4. Match variants and resolve strand — here, not earlier (`SCO-4`)

```bash
python3 tools/match_variants.py --run <RUN_ID> \
  --scorefile <the model file you downloaded> \
  --chr-col hm_chr --pos-col hm_pos --effect-allele-col effect_allele \
  --other-allele-col hm_inferOtherAllele --effect-weight-col <as the header names it> \
  --imputed-dir runs/<RUN_ID>/score/imputed_joint \
  --typed-vcf runs/<RUN_ID>/door/normalized.vcf.gz \
  --palindromic-policy <you choose> --r2-bar 0.80 \
  --out-dir runs/<RUN_ID>/score
```

**Every flip decision is recorded.** A silent flip during normalization would erase provenance,
which is why strand is resolved at matching time rather than at conversion.

`--palindromic-policy` has no default. A/T and C/G markers are reverse complements of each other,
so alleles alone cannot fix orientation; resolving them properly needs the chip's own `.strand`
mapping file, which needs the chip generation — and a consumer export's chip generation is
typically **inferred from the header, not declared**, so check what the door actually established
before relying on it. Choose `exclude` unless you have a reason not to, and say what it
cost: excluded palindromic markers become unresolved weight, counted in the gate.

## 5. The coverage gate (`SCO-8`) — measured now, not estimated

```bash
python3 tools/weight_coverage.py measure --run <RUN_ID> \
  --scorefile <model file> --chr-col hm_chr --pos-col hm_pos \
  --effect-weight-col <as named> --dosage-tsv runs/<RUN_ID>/score/dosages.tsv \
  --r2-bar 0.80 --high-beta-fraction 0.05 \
  --out runs/<RUN_ID>/score/coverage_<PGS>.json
```

Two conditions, and a stop:

1. **Weighted coverage** — the fraction of the score's **weight** on positions you could not
   resolve, not the variant count. Refuse if resolved Σ|β| < **0.90**. The count-based
   `--min_overlap 0.75` bar is explicitly rejected: count is the wrong variable.
2. **A named condition on high-β loci** — refuse if any single unresolved locus carries **≥5%** of
   total Σ|β|. **Reported individually, by locus, never summed into the fraction.** This is the
   case the aggregate is blind to: one unresolved dominant locus can swing this person's score by
   up to 2|β| even when the fraction looks fine.
3. **Stop if the data is broken** — including the internal inconsistency in §6.

All three values are **design choices, chosen and defended, not derived** — no published number
exists. Say that when you use them. And the honest consequence, which is intended: a
stringent r² bar plus a 0.90 floor means an admixed patient, whose absent high-weight
positions are also the hardest to impute well, can trip a refusal. That is `FRAME-3`'s honest
limit made operational, and a correct refusal is a correct outcome.

## 6. Compute (`SCO-9`)

```bash
python3 tools/score_plink2.py --run <RUN_ID> \
  --score-input runs/<RUN_ID>/score/plink_score_input.tsv \
  --imputed-dir runs/<RUN_ID>/score/imputed_joint --out-dir runs/<RUN_ID>/score
```

**Dosage field: DS, auto-detected.** With no `--dosage-field` flag the tool picks the field per
target VCF — **`DS` when the VCF header declares `FORMAT/DS`, else `GP`**. Beagle always emits
`DS`, and plink2 *refuses* `dosage=GP` on a file that also carries `DS`, so scoring the Beagle
output with a fixed `GP` fails on every chromosome and returns `raw_score 0.0`. `--dosage-field`
still overrides when you need a specific field. The recorded `dosage_field_used` per chromosome
shows which was taken.

**Multiallelic records are excluded at import (`--import-max-alleles 2`).** Beagle emits ~30k
multiallelic dosage records per chromosome from the 1kg panel, and plink2 cannot parse a
multiallelic dosage — it aborts during `--vcf` import. The exclusion has to be an *import-time*
filter (`--import-max-alleles`); the post-import `--max-alleles` runs too late, after the dosage
parse that errors.

**The residual-missing rule, which must not be softened.** `no-mean-imputation` alone is not the
policy — turning mean-imputation off makes plink score a missing genotype as **zero dosage**,
which is absence-treated-as-reference, the very error the coverage architecture exists to prevent.
The real guarantee is architectural: the scorer only ever runs on a completed dosage set, so it
never faces a missing genotype. `no-mean-imputation` is a guard against the back door, not the
rule itself.

**Check `internal_consistency` in the output.** If plink used fewer variants than it was handed,
the pipeline is internally inconsistent and that is a **stop** — not a fill of any kind, not zero,
not mean.

**Check the model class.** Some catalog models are not weighted sums at all (dosage-per-genotype,
recessive, dominant, interaction, diplotype, haplotype). A weighted-sum computation cannot compute
those. `SEL-1` sources with no model-class filter, so the handoff can carry an uncomputable model;
the fork is unresolved, so surface it rather than resolving it silently.

## 7. Hand back to select if needed (`SEL-12`)

If the measured number is **materially worse** than the estimate select worked from, that is the
loop firing as designed, not a failure. Go back to `select`, take the next candidate down the
ranked list, and record why. Never re-score a model already scored; out of candidates → refuse.

## 8. Record

```bash
python3 tools/record.py --run <RUN_ID> --stage score --name score --judgment - <<'JSON'
{
  "decision": "...", "outcome": "score|refuse|stop|ask",
  "requirement_ids": ["SCO-6", "SCO-8", "..."],
  "model": "...", "raw_score": 0.0,
  "coverage": {"resolved_weight_fraction": 0.0, "measured_not_estimated": true,
               "high_beta_unresolved_loci": []},
  "missing_position_policy": "impute (array; SCO-6). No position scored as reference.",
  "imputation": {"panel": "...", "seed": "...", "deterministic": true},
  "strand": {"flips_recorded": 0, "palindromic_policy": "..."},
  "reasons": ["..."],
  "evidence": [{"source": "runs/<RUN_ID>/score/...", "field": "..."}]
}
JSON
```
