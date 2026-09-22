---
name: ancestry
description: Infer the person's genetic ancestry from their genome, take their self-report, and surface any disagreement between the two without silently resolving it. Shared by select and interpret (ANC-1..ANC-6). Run after the door and before select, because select needs ancestry up front to choose a model.
---

# ancestry

Shared skill, used by `select` (which model fits this person) and `interpret` (which reference
cohort to build). It runs **after the door** — `ANC-5` holds only if absence is resolved per assay
class before intersecting markers, so the file must be classified first.

## The one rule that governs this whole skill

**The math always uses the genome (`ANC-4`).** Self-report never drives model choice, never drives
reference choice, and never adjusts confidence. It is a **flag, not a signal.**

This is not a stylistic preference. R5 searched for a scheme that numerically weights self-report
against inferred ancestry and **found none**: across All of Us, Ding, Privé and eMERGE the
documented practice is genetic ancestry for the computation, self-report for communication and
checking, and the two are **never algebraically combined**.

The person is asked, is shown any mismatch, and may end the session (§12). What they do not do is
pick which ancestry the math runs on. Offering them that choice would be fake consent dressed as
respect.

## Both sources, in order

### 1. Ask for the self-report

Ask plainly, and make clear it is optional and will not change the computation — only what the
agent tells them and whether a mismatch gets flagged. Accept "I don't know" as a complete answer;
`TEST-4`'s personas include exactly that, and it must not derail anything.

### 2. Infer from the genome

**No PCA basis is staged. You build one, every run.** Any `refdata/ancestry-ref/build`
directory referenced by older notes is deleted from the per-run clone on purpose: it was derived
from a patient's own positions and staging it would hand you a completed projection.

**Build it from the HGDP + 1000 Genomes panel at
`refdata/ancestry-ref/hgdp_1kgp/GRCh37_HGDP+1kGP_ALL`.** That is a plink2 pgen fileset, passed as a
stem without an extension, so there is nothing to concatenate: it is already one genome-wide
fileset covering all autosomes. Write everything under `runs/<RUN_ID>/ancestry/` -- the refdata
clone is read-only to you and a write there fails.

The panel carries **six groups: AFR, AMR, CSA, EAS, EUR, MID**. Note **CSA (Central and South
Asian) rather than SAS**: it is the wider group, so a South Asian person lands in CSA. Read the
group names off `reference_build.json` rather than assuming a vocabulary.

> **Never restrict the reference to the patient's own positions.** Not with `bcftools -R`, not with
> `-T`, not with any site list derived from their file. This is the one construction that looks
> right and is not.
>
> The reason is `ANC-6`'s coverage check below. It reads
> `n_markers_shared_with_reference / marker_set.reference_pruned_markers`, and if the reference was
> built from the patient's markers that ratio is **1.0 by construction**, whatever the projection
> is actually worth. The check reads clean at exactly the moment it should fire, and a 100% figure
> then gets quoted as evidence of the best-calibrated regime when it is a tautology.
>
> If you inherit or build such a reference anyway, say the projection's coverage is **unestablished**
> and do not apply the calibration below. Do not report the ratio.

Choose the parameters and record them; there are no defaults you can lean on:

```bash
python3 tools/ancestry.py build-ref \
  --ref-pfile refdata/ancestry-ref/hgdp_1kgp/GRCh37_HGDP+1kGP_ALL \
  --panel refdata/ancestry-ref/hgdp_1kgp.panel \
  --maf <you choose> --geno <you choose> \
  --prune-window <you choose> --prune-step <you choose> --prune-r2 <you choose> \
  --pcs <you choose> --exclude-palindromic <yes|no> \
  --out-dir runs/<RUN_ID>/ancestry/ref
```

`--exclude-palindromic yes` is the QC choice to beat: A/T and C/G markers cannot be strand-resolved
from alleles alone, and an unresolved flip moves the query in PC space.

**Which chromosomes you include bounds every distance you later quote.** The panel above is
genome-wide and `build-ref` passes `--autosome`, so the basis covers all 22 autosomes unless you
narrow it. If you narrow it, say so rather than implying the genome. And record the marker count
after pruning -- it is the denominator of the coverage check, and the check is only meaningful when
the reference was built independently of the patient.

**Expect coverage to look low, and do not read that as a fault.** A genome-wide pruned basis runs to
a few hundred thousand markers and a consumer array carries a fraction of them, so a shared-marker
ratio in the low tens of percent is the honest figure for an array. A ratio near 1.0 is the warning
sign, not the good one -- see the tautology note above.

**A reference build is not reproducible across runs.** `build-ref` writes fixed filenames into
`--out-dir` with no version guard, so coordinates from one build cannot be compared against
another's. Two runs on the same person will not produce the same numbers. State your group call
with its uncertainty rather than as a coordinate that would survive a rerun.


Then project:

```bash
python3 tools/ancestry.py project --run <RUN_ID> \
  --ref-dir runs/<RUN_ID>/ancestry/ref \
  --patient-vcf runs/<RUN_ID>/door/normalized.vcf.gz \
  --pcs 20 --k-neighbours 50 \
  --out runs/<RUN_ID>/ancestry/projection.json
```

**`ANC-6`: use the bias-corrected coordinates.** The tool reports both. Simple projection —
multiplying the query by reference loadings — has a shrinkage bias that pulls the query toward the
origin, i.e. toward the centre of the reference cloud. The bias is **directional, not random**: it
deepens as marker coverage falls, and it is strongest for admixed genomes. It was measured by
leave-one-out over reference samples, and the figures live in
`docs/ancestry-projection-scaling.md`.

**Those figures were measured on a different reference and do not transfer.** They came from a
chr1-3 1000 Genomes basis; this skill now builds a genome-wide HGDP+1kGP one. Read them for the
shape of the effect, never as thresholds to check your coverage against. **No coverage figure in
that document has been re-measured on this panel.** If you want a number for this run, the
artifact carries two of its own: `shrinkage_profile_measured_on_reference`, which is an in-sample
check that the per-PC scale calibration is right, and
`projection_shrinkage_ratio_for_this_query`, which is how far the two estimators landed apart.
Read the artifact's own `shrinkage_note` before quoting either.

**Those are the naive path's numbers, and they are not a validation of the corrected one.** Nothing
in that artifact establishes that `patient_pcs_adp_corrected` is accurate, and it does not rank the
two paths against each other in either direction: the simple path there gets 20 free per-PC scales
fitted against truth while ADP gets one global rho and a rotation, and truth is an in-sample
coordinate ADP can reproduce by construction. The artifact contains numbers that look like a
head-to-head. They are not one.

So read `patient_pcs_adp_corrected`. **Do not substitute `patient_pcs_simple_projection` or
`distance_to_centroid_simple_projection` for it**, in the group call or in the report — nothing
here licenses that swap, and the naive path's one measured property is a 35%+ contraction toward
the centre.

Treat the corrected coordinate's **precision as unquantified** — which bounds the coordinate, not
your conclusion. Make it concrete: coverage is `n_markers_shared_with_reference` over
`marker_set.reference_pruned_markers`. At ~50% the scale correction is decisive — and that is the
**highest coverage anyone has measured**, because at full coverage the leave-one-out degenerates
(the held-out sample reconstructs the reference decomposition, so nothing is estimated and the test
reports nothing). Above ~50%, read the correction as expected-good by extrapolation, not as
validated. Near ~14% the correction is a wash, so quote distances as
approximate and do not lean on small gaps between groups. Below ~4% it is worse than **the old
sqrt-scaled convention it replaced** — worse than its own predecessor, not worse than the simple
path — so say the projection is unreliable rather than reporting a coordinate. No floor is
enforced anywhere, so this check is yours to make.

The **simple** path's contraction is **strongest for admixed genomes** — its coefficient by
superpopulation runs AMR 0.587 (worst), SAS 0.619, EUR 0.631, AFR 0.658, EAS 0.751 (least
affected), at full coverage, same ordering and lower values as coverage falls. These are the naive
path's figures; there is no equivalent per-group accuracy number for the corrected coordinate and
none should be quoted. So the bias distorts the `FRAME-3` population most, but do **not** tell the
person it is a non-European phenomenon: EAS is the least affected of the five and EUR sits in the
middle. If you characterise the limitation in the report, say admixed, not non-European.

`projection_shrinkage_ratio_for_this_query` is **not** a measurement of that bias, and must not be
quoted as one. It is `||simple|| / ||ADP||` for this person — two estimators compared against each
other, with no ground truth, because a true coordinate would require this person to have been in
the reference decomposition, which is exactly what they are not. It cannot tell you which estimator
is off, or in which direction. Read it as nothing more than how far apart the two estimates landed,
and read the artifact's `shrinkage_note` before quoting it at all.

### 3. Judge the group yourself

The tool gives you distances, never a label. Read:

- `distance_to_superpopulations[*].distance_to_centroid_adp` — how far the person sits from each
  reference group's centre;
- `patient_distance_in_sd_units` — that distance expressed against the group's own spread, which is
  what tells you whether the person is a typical member or an outlier sitting between groups;
- `nearest_reference_samples.by_superpopulation` and `.by_population` — the local neighbourhood,
  which catches a case the centroid distance can hide;
- `n_markers_shared_with_reference` — how much data the projection actually had.

Then state the group **and your uncertainty**. Continental-scale assignment is data-cheap: a few
dozen purpose-selected ancestry-informative markers suffice, and a consumer array carries ~600k
(Kosoy 2009; LASER recovers continental ancestry at 0.001× coverage). So a confident continental
call from an array is well supported. **Sub-continental resolution is not** — it needs roughly 100×
more information — so do not claim it.

**Do not invent a posterior cutoff.** `ANC-1` refers to a random-forest posterior threshold and an
`OTH` assignment rule; R5 records both as **not established** in the retrieved literature. Say what
the distances are and what you conclude from them, with the uncertainty attached.

Known deviations from `ANC-1` as written — carry them into what you tell the person if they matter
to the result (full list in OPEN_QUESTIONS Q3). Three of the four that used to sit here have
closed: the basis is now genome-wide rather than chromosomes 1-3, it is HGDP + 1000 Genomes rather
than 1000 Genomes alone, and MID is present. **What remains is that there is no trained random
forest, and so no posterior and no `OTH` bin.**

Do not read the closures as a general improvement in accuracy. They remove three known objections;
nothing here measures what the new basis is worth, and the calibration figures above were taken on
the old one. `reference_build.json` states what the panel actually carried, derived from the panel
file rather than asserted — read it rather than this paragraph if the two ever disagree.

## When the two disagree (`ANC-2`, `ANC-3`)

**Disagreement is the ~1-in-8 path, not an edge case.** All of Us puts self-reported/inferred
concordance at **87.7%**, and the disagreement concentrates in admixed people — the same people
the product already serves worst.

**They are not two measurements of one quantity.** Self-reported race/ethnicity is a social and
cultural construct; inferred ancestry is similarity to reference genomes. A mismatch is therefore
**not an error in one of them to be corrected**. Saying "your test says otherwise" mis-models the
problem and is also just wrong.

How to handle it:

- **Surface it at the start of select, before any model is picked** (`ANC-3`). Not after. Not in
  the report.
- **Never silently pick one** (`ANC-2`).
- **It is not a hard blocker.** The default is to continue, on the genome, with the mismatch shown
  (`ANC-3`, `ANC-4`).
- Explain *why* the genome is what the math uses: scoring models are keyed to genetic ancestry, so
  matching on the genome is what makes the model applicable at all. Frame it as a property of the
  models, not as a correction to the person.
- The person may end the session (§12). Record that as `session-ended-by-person` — it is **not** a
  refusal and **not** a stop.

## Record it

```bash
python3 tools/record.py --run <RUN_ID> --stage ancestry --name ancestry --judgment - <<'JSON'
{
  "decision": "...group, with uncertainty...",
  "outcome": "proceed",
  "inferred_group": "...", "self_reported": "...", "agreement": "agree|disagree|no-self-report",
  "distances_cited": {...},
  "used_for_math": "inferred (ANC-4)",
  "surfaced_to_person": true,
  "reasons": ["..."],
  "evidence": [{"source": "runs/<RUN_ID>/ancestry/projection.json", "field": "..."}]
}
JSON
```
