# Missing / poorly-imputed weight in a polygenic score: named quantity, run-time computation, published threshold

**Scope.** Three questions plus one crux, for the *score* stage of the PRS agent spec:
(a) is there an established, *named* quantity for how much of a score's predictive content is carried by
missing or poorly-imputed variants; (b) is any such quantity computed *at run time, per individual,
before scoring*, in a real tool or pipeline; (c) is there a *published threshold* on it. Crux: does a
single weight-based metric conflate a rare high-weight variant with a common high-weight variant.

**Convention for this memo.** Each claim is tagged **[STATED]** (the cited source explicitly says it),
**[INFERRED]** (a correct consequence we are constructing, not asserted by any source), or
**[NEGATIVE]** (searched for, not found). Every citation is a resolvable DOI or stable URL. Where the
literature stops, the memo says so rather than assembling a value.

**Provenance of the tool-documentation claims (part b).** The tool-doc facts are traced to raw source
fetches, not recall: pgsc_calc's `--min_overlap` default and definition are read verbatim from the raw
`nextflow_schema.json` (lines 120-126) and `nextflow.config` (line 55) via direct fetch of the GitHub
raw files; PLINK2 and PRSice-2 details are read from their primary docs (cog-genomics.org and the raw
GitHub `docs/` sources). Specific machine-readable column names (`percent_matched`, `n_matched`) were
searched for and **NOT FOUND** in the rendered docs, so they are not asserted — the claim is limited to
the verified wording ("number and percentage of variants … matched," "Passed Matching" column,
`match_status`, `[sampleset]_summary.csv`). **"Crossref-verified" means the DOI metadata (title,
journal, year) was confirmed via the Crossref REST API** — direct doi.org resolution was blocked, and
full text of the paywalled Nature / Nature Medicine papers was **not** obtained; those citations
support the metadata and the pipelines' gating design, not a full-text re-read.

---

## (a) Is there an established, named quantity? -- **No single standard; usage is heterogeneous.**

There is **no named, standardized quantity** in the published literature for "the fraction of a
polygenic score's predictive content carried by missing / poorly-imputed variants." The candidate
formulations come from *adjacent* problems (score-variance theory, per-locus heritability
partitioning, variant-match reporting), not from a purpose-built coverage metric. This extends the
prior review's conclusion -- that a cutoff is "a design decision to be justified, not a value to be
cited" -- back one level: the *metric itself* is also a construction, not a citable standard.

**The shared starting point (STATED).** The additive PRS is a weighted allele count,
`PRS_i = Sum_j x_ij * beta_j` with dosage `0 <= x_ij <= 2` and effect weight `beta_j`. This exact form
appears in Chen et al. 2020 [STATED, doi:10.1186/s13073-020-00801-x], Choi et al. 2020 ("sum of risk
alleles ... weighted by the effect size estimate") [STATED, doi:10.1038/s41596-020-0353-1], and
Dudbridge 2013 (`S_hat = Sum_i beta_hat_i1 * G_i`) [STATED, doi:10.1371/journal.pgen.1003348].

### Candidate 1 -- fraction of summed |weight|:  `Sum_missing |beta_j| / Sum_all |beta_j|`
**[NEGATIVE]** No primary source defines or uses this as a named metric. It is trivially computable
from a scoring file, but it is not grounded in a variance or accuracy model: `|beta|` alone ignores the
allele-frequency weighting that determines how much a locus actually moves the score in a population
(see the crux). It is the weakest candidate precisely because it drops the `2p(1-p)` term.

### Candidate 2 -- fraction of score *variance*:  `Sum_missing beta_j^2*2p_j(1-p_j) / Sum_all beta_j^2*2p_j(1-p_j)`
This is the theoretically motivated candidate. Its two ingredients are each grounded, but the assembled
metric is **not named or standardized anywhere**:
- Chen 2020 decomposes a SNP's contribution to score variability as its imputation inconsistency times
  "its relative contribution to the overall score (captured by percent variability explained, which is
  further a function of **the SNP weight and its allele frequency variance**)" [STATED, Fig. 5c,
  doi:10.1186/s13073-020-00801-x]. "SNP weight and its allele-frequency variance" is `beta_j` and
  `Var(G_j)` -- the variance-share framing, **in words**.
- The explicit algebra `Var(beta_j*G_j) = beta_j^2*2p_j(1-p_j)` is **[INFERRED]** -- a correct
  consequence of Chen's own PRS formula plus the binomial genotype variance `Var(G_j)=2p_j(1-p_j)` under
  Hardy-Weinberg. **Chen 2020 does not write this algebra.** The `2p(1-p)` genotype-variance term is a
  textbook stat-gen convention (the GCTA framework standardizes genotypes by `sqrt(2p(1-p))`; Yang et al.
  2010 doi:10.1038/ng.608, GCTA 2011 doi:10.1016/j.ajhg.2010.11.011) -- but note the retrieval could
  **not** locate the `2p(1-p)` statement in the fetched Yang 2010 full text and GCTA 2011 full text was
  paywalled, so treat the algebra as **domain-recall / textbook**, not as source-verified STATED
  content. Cite Yang/GCTA for the standardization *framework* only.

### Candidate 3 -- other named quantities that exist but do not answer the question
- **Local SNP heritability** (Shi, Kichaev, Pasaniuc 2016, HESS) [STATED,
  doi:10.1016/j.ajhg.2016.05.013]: a real, named, published per-locus variance-apportionment quantity --
  but it describes the *trait's* genetic architecture (LD-aware, estimated from summary statistics), not
  a *specific published PGS's weight vector*, and needs full summary stats + an LD reference, not just a
  scoring file. Establishes the *concept* of per-locus variance apportionment; does **not** give a
  missing-weight fraction for a score.
- **Whole-score proportion of variance explained (R^2, incl. liability scale)** (Dudbridge 2013 eq. 2
  [STATED, doi:10.1371/journal.pgen.1003348]; a reporting item in Wand et al. 2021 [STATED,
  doi:10.1038/s41586-021-03243-6]): standard, but a *whole-score* empirical metric measured in a
  validation cohort, **not decomposed per variant** -- it cannot yield a missing-content fraction.
- **"Genomic coverage" / variant match rate** (pgsc_calc, Lambert et al. 2024 [STATED,
  doi:10.1038/s41588-024-01937-x]): a **count / presence-based** report of matched vs excluded variants,
  not weight- or variance-weighted, with no acceptability threshold attached (see part b).

### Standard-usage verdict -- **heterogeneous, no standard.**
Three framings coexist and none is adopted as *the* metric: a **variance framing** (weight x AF-variance
-- Chen 2020 qualitatively, plus the Dudbridge/Shi/GCTA variance-partitioning tradition); a **count
framing** (number/fraction of variants present -- Choi 2020 QC, pgsc_calc reporting, Wand 2021 as a
descriptive item); and a **summed-|weight| framing** (no primary source found using it). The
variance-share formula is the most defensible *construction*, but it would be assembled from grounded
parts, not cited as an established metric.

---

## (b) Is it computed at run time, per individual, before scoring? -- **No. Standard tools are count-based or frequency-imputation-based, never weight-based.**

No PRS tool or clinical/biobank pipeline examined computes a per-individual, pre-scoring, **weight- or
variance-weighted** missing-fraction metric from a scoring file + allele-frequency panel. Variant
accounting is by **count**, and missing genotypes are handled by **frequency mean-imputation** -- neither
is weighted by `|beta|` or by variance contribution.

### pgsc_calc (the PGS Catalog Calculator) -- the load-bearing case: **count-based, weight-blind [STATED].**
This is the finding that establishes the gap you are closing.
- **`--min_overlap`** is defined verbatim in the raw pipeline schema as: type `number`, **default 0.75**,
  min 0, max 1, description *"Minimum proportion of variants present in both the score file and input
  target genomic data"* [STATED, nextflow_schema.json lines 120-126; hard-coded `min_overlap = 0.75` in
  nextflow.config line 55]. It is a **proportion of variants** -- a count proportion. **No effect-weight
  or allele-frequency-variance term appears in its definition.** It is a count-based pass/fail bar.
- **The match report** (docs Section 2.3 "Summary") gives *"the number and percentage of variants
  within each score that have been matched, and whether that score passed the --min_overlap threshold
  (Passed Matching column)"*; a per-sampleset `_summary.csv` carries a `match_status` field [STATED,
  pgsc-calc.readthedocs.io/en/latest/explanation/output.html]. Every reported quantity is a **variant
  count or count-percentage**; the accept/reject decision is percent-matched vs `--min_overlap`.
- The docs explicitly discourage lowering the bar -- a section is titled *"Adjusting --min_overlap is a
  bad idea"* and failure raises *"ZeroMatchesError: All scores fail to meet match threshold 0.75"*
  [STATED, pgsc-calc.readthedocs.io/en/latest/explanation/match.html].

**Plainly: `--min_overlap` and pgsc_calc's aggregate match-rate reporting are weight-blind. They count
variants without regard to effect size -- a matched/total variant proportion.** Replacing that count-based
bar with a weight-based one is therefore a genuine gap: the standard tool's acceptability gate treats a
missing high-weight variant and a missing negligible-weight variant identically.

*(Assay-class note, STATED and relevant to the spec: pgsc_calc's own docs state "WGS data are not
natively supported by the calculator (as homozygous REF sites are excluded from the variant sites)" -- so
its match accounting counts variant absence but does not, in its accounting, disambiguate **why** a
position is absent across assay classes. That distinction is exactly what the product treats as
load-bearing; the standard tool does not carry it.)*

### PLINK 2.0 `--score` [STATED, cog-genomics.org/plink/2.0/score]
*"Missing genotypes contribute an amount proportional to the loaded (via --read-freq) or imputed allele
frequency. To throw out missing observations instead ... use the no-mean-imputation modifier."* Allele
frequency enters **only as the mean-imputation fill value**; variant accounting is by count
(`list-variants` writes the used-variant list). No weighted missing-fraction field.

### PRSice-2 [STATED, doi:10.1093/gigascience/giz082; raw GitHub docs]
`--missing` options: `MEAN_IMPUTE` (default, "proportional to imputed allele frequency"), `SET_ZERO`,
`CENTER`; `--use-ref-maf` uses reference-sample MAF. Per-genotype, frequency-based missingness handling.
No weighted missing-fraction QC output.

### LDpred2 / bigsnpr, PRS-CS [NEGATIVE]
Score-*construction* (weight-estimation) tools. They do not take an individual genome + AF panel and emit
a per-person missing-weight-fraction; no such run-time field is documented. Recorded as absence.

### Clinical / biobank pipelines [STATED + NEGATIVE]
GenoVA (Hao et al. 2022, doi:10.1038/s41591-022-01767-6) and eMERGE's 10-PRS implementation (Lennon et
al. 2024, doi:10.1038/s41591-024-02796-z) gate return-of-results on the **PRS distribution**
(percentile / odds-ratio thresholds); per-sample QC in this class is **genotyping call-rate /
sample-missingness** (count/rate-based). **[NEGATIVE]** Neither reports a per-individual
missing-*score-weight*-fraction metric.

---

## (c) Is there a published threshold at which a score is unreportable? -- **No. Confirmed negative.**

**[NEGATIVE] No published numeric threshold on missing-score-weight-fraction exists.** This confirms the
prior review directly, now checked against the primary reporting frameworks and clinical pipelines:
- **PRS Reporting Standards (PRS-RS; Wand et al. 2021, Nature)** [STATED,
  doi:10.1038/s41586-021-03243-6] is a *study* reporting framework (minimal information to interpret and
  evaluate PRS in development/validation), **not** a per-individual reportability cutoff.
- **ACMG points-to-consider on clinical PRS (2023)** [STATED, doi:10.1016/j.gim.2023.100803] is
  **qualitative** guidance, with no numeric missing-weight cutoff.
- No PGS Catalog submission requirement, clinical return-of-results paper (GenoVA, eMERGE), or reporting
  framework specifies a missing-score-weight-fraction value at which a score is declared unreportable.
- The **only** run-time gate any standard tool ships is pgsc_calc's **count-based** `--min_overlap = 0.75`
  -- and as established in (b), that is a variant-count proportion, **not** a weight-based reportability
  bar.

**Plainly, and for the record:** there is no bias-vs-missing-weight curve, no per-trait acceptability
table, and no numeric weight-fraction cutoff in the literature. A reportability threshold on missing
*weight* is a design decision to be justified in the spec, not a value to be cited.

---

## Crux -- rare vs common high-weight variant: **your hypothesis is confirmed in direction, with one refinement that matters for the spec.**

**Confirmed [direction].** A rare high-weight variant and a common high-weight variant do **not** behave
the same, and a single weight-based scalar conflates them.

The per-locus contribution to **population** score variance is `beta_j^2*2p_j(1-p_j)` [INFERRED algebra;
the dependence itself is STATED in Chen 2020 as "a function of the SNP weight and its allele frequency
variance"]. This term is maximized near `p = 0.5` and vanishes as `p -> 0`:
- A **rare** high-weight variant contributes **~0 to population variance** even at large `|beta|`
  (`2p(1-p) -> 0`) [INFERRED].
- A **common** high-weight variant contributes **a lot** to population variance (`2p(1-p)` near its max)
  [INFERRED].

At the **individual** level, one person's locus term is `x_ij*beta_j` [STATED, Chen 2020 formula]. A
carrier of a rare high-weight allele has a large per-person term, and imputation error is most severe
exactly where the variant is rare -- so a single flipped dosage moves a large chunk of *that person's*
score. This is not just algebra; Chen 2020 states it empirically: *"the range of accuracy achieved is
larger for rare genetic variants"*; single-SNP variability events are *"high weight variants ... like LPA
and ANRIL for CAD and APOE for Alzheimer's"*; and *"this individual-level imputation variability is
masked by the approximately equivalent average accuracy when evaluated at a population level"* [all
STATED, doi:10.1186/s13073-020-00801-x]. That is precisely the individual-vs-population dissociation your
hypothesis predicts.

**The refinement -- the part that changes the spec requirement, not just confirms it [INFERRED, on a
STATED foundation].** Your framing was "a single weight-based metric may conflate them, and a
variance-based metric fixes it." The first half is right; the second half is the trap. The two metrics do
not differ by *correctness* -- they index **two different targets**:

- **Summed-|beta| coverage** tracks **individual-level instability**. It (roughly) flags the rare
  high-weight variant as heavy -- which is correct *for one person*, because that variant can swing an
  individual's score.
- **Variance coverage** `beta^2*2p(1-p)` tracks **population-variance impact**. It correctly downweights
  the rare variant for population purposes -- but by construction it then **under-counts the very variant
  that dominates individual instability.**

So neither scalar serves both regimes. A `|beta|`-weighted metric over-states the rare variant's
*population* importance; a variance-weighted metric under-states its *individual* importance. **Population
coverage and individual-level instability are distinct quantities and require distinct accounting.** For a
per-individual product that scores one person at a time, the individual-instability regime -- where the
rare high-weight poorly-imputed variant lives -- is the one that produces what Chen 2020 calls "singular
PRS calculations that differ substantially from a [WGS] gold standard score" [STATED]. A single
weight-based number cannot be the whole gate; the spec should name *which* target its threshold indexes,
and should treat the rare-high-weight-poorly-imputed case as its own condition (dosage uncertainty on a
large-`beta` locus), not as a small slice of a summed-|weight| fraction.

---

## Where the literature stops (for the manuscript)

- **STATED and solid:** the PRS formula; Chen 2020's decomposition-in-words and its empirical
  individual-vs-population dissociation; that pgsc_calc / PLINK2 / PRSice-2 accounting is count- or
  frequency-based and weight-blind; that no reporting framework or clinical pipeline sets a weighted
  missing-fraction reportability threshold.
- **INFERRED (correct, but constructed -- flag in the manuscript):** the explicit `beta^2*2p(1-p)`
  per-variant variance formula; the `p -> 0` limits; the claim that `|beta|`-coverage and
  variance-coverage index different targets. These follow rigorously from the STATED PRS formula plus
  textbook binomial genotype variance, but **no source writes them for this purpose.**
- **NEGATIVE (record explicitly):** no named standard metric; no per-individual run-time weight-based
  computation in any tool; no published numeric threshold; no bias-vs-missing-weight curve; no per-trait
  acceptability table. The `2p(1-p)` algebra could not be verified as STATED in the fetched Yang 2010
  text (GCTA 2011 paywalled) -- it is anchored as textbook/domain-recall.

*Evidence table: see `missing_weight_evidence_table.csv` -- one row per source with the exact
quantity/field, its definition, and what it does and does not establish.*
