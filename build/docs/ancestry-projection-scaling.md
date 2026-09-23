# Ancestry projection: scale convention, what leave-one-out measured, and the TRACE question

Status: the scale fix is applied. TRACE-proper is **recommended but not adopted** — this document
exists so that decision is on the record with the numbers behind it.

Artifact: `work/loo-validation/loo_validation.json` (gitignored; regenerate with
`python3 tools/loo_validate_ancestry.py --ref-dir refdata/ancestry-ref/build --per-cell 10
--workers 4 --out work/loo-validation/loo_validation.json`, ~2 h on 4 workers).
Harness md5 `7cf49d18…`, `ancestry.py` md5 `2ef9d095…`, HEAD `5f773d5a`, seed `20260728`,
250/250 samples, 0 failures.

## 1. What was wrong

`augmented_pca` returned eigenvectors scaled by `sqrt(eigenvalue)`; the Procrustes target,
plink2's `ref_pca.eigenvec`, is written **unscaled** (verified: all 20 columns have norm 1 to
2.1e-07, eigenvalues live in `ref_pca.eigenval` and span 199.714→1.894). The Procrustes step has
one global scale `rho`, which can only reconcile the two sides if every PC needs the same factor.
It does not: the sqrt factors span 10.27x.

The consequence was measured, not inferred. The best single `rho` landed at 0.141–0.151, leaving
PC1 about 2x too large and PC5–PC20 about 5x too small, and the Procrustes left 55–60% of
reference-side variance unexplained (0.1% after the fix on slice-001). On held-out reference
samples the old code placed them at roughly half their true distance from their own group
centroid and biased `patient_distance_in_sd_units` by about −1.5 SD — i.e. it was **manufacturing**
the centre-ward shrinkage ANC-6 exists to remove.

## 2. What leave-one-out can and cannot measure

A reference sample's true coordinate is known (its row in the committed 2504-sample
`ref_pca.eigenvec`), so holding it out and reprojecting it is the only thing in this repo that
can measure projection error at all. `projection_shrinkage_ratio_for_this_query` cannot: it
compares two estimators with no ground truth.

Three limits must travel with every number below.

- **The 27,405-marker level is degenerate.** At full coverage the augmented set
  {2503 reference + held-out sample} is exactly the original 2504 individuals on the same
  markers, so the augmented PCA reconstructs the reference PCA and ADP recovers truth by
  construction (mean L2 1e-05). It is a control; it proves nothing about estimation.
- **The held-out sample is not meaningfully out-of-sample.** Removing 1 of 2504 buys almost no
  generalization gap — leverage-matched, held-out error sits at the 62/35/1/30th percentile of
  reference samples that were never held out. What is measured is marker-thinning reconstruction
  error under different scaling conventions. That is the right contrast for this question, but it
  is not a generalization estimate for a real patient.
- **Only the paired within-sample contrast is internally valid.** Both arms come from one
  eigendecomposition differing solely by a post-multiply, with byte-identical `G_ref`, marker
  draw, frame map and truth vector. Absolute error levels are not out-of-sample accuracy.

Also: the reference is chr1–3 only, so every "% of markers" is relative to a 27,405-marker
chr1–3 set; pooled rows are superpopulation-**balanced** (50 each), not reference-wide; and the
marker universe itself (LD pruning, MAF/geno filters) was built with the held-out sample present
— common-mode across arms (Jaccard 0.9966), so it cannot bias a paired delta.

**Known cosmetic defect in the artifact.** `stratification.cells{}.norm_range` reports the
`||true||` span of each superpopulation × quintile *pool*, not of the 10 samples actually drawn
from it, so it advertises leverage coverage the run never tested (e.g. SAS_q5 up to 0.534 when
the draw tops out at 0.201). The **actual drawn set** spans `||true||` 0.0489 to 0.2012, median
0.0770, p95 0.1295 — read those figures, not the artifact's. Left unfixed deliberately: editing
`tools/loo_validate_ancestry.py` would change its md5 and break the `harness_md5` match that
lets this artifact be verified against the code that produced it. Fix it at the same time as the
next run, not before.

## 3. Does removing the sqrt scaling reduce error?

**It depends on marker coverage, and at the coverage a real array has, it is a wash.**

Pre-registered primary endpoint — ADP PC1–4 RMSE in reference-sd units, paired sqrt minus
no-sqrt, at 3,809 markers (13.9%, what a raw 23andMe v4 array carries against a reference
LD-pruned from full 1000 Genomes):

    delta = +0.01753 sd units, t = +2.42, n = 250, 95% CI [+0.00331, +0.03175]
    sign test: no-sqrt better in 98/250, sqrt better in 152/250

The mean favours the fix and the interval excludes zero, but the **majority of samples are
slightly better under the old code**. The positive mean is carried by a minority with large
gains. Read as equivocal.

Across coverage levels (ADP PC1–4 RMSE, sd units):

| markers | coverage | sqrt | no-sqrt | TRACE | paired sqrt−nosqrt |
|---|---|---|---|---|---|
| 27,405 | 100% | 0.574 | 0.000 | 0.004 | degenerate control |
| 14,662 | 53.5% | 0.534 | 0.118 | 0.042 | +0.416, t=+34.3, 250/250 |
| 3,809 | 13.9% | 0.433 | 0.416 | 0.124 | +0.018, t=+2.4, 98/250 |
| 987 | 3.6% | 0.404 | 0.593 | 0.260 | −0.189, t=−32.4, sqrt wins 247/250 |

There is a clean monotone crossover: the fix wins decisively above ~50% coverage, is a wash near
14%, and **loses** below ~4%. At 53.5% the benefit is uniform across leverage quintiles (+0.39 to
+0.44, every t > 13), so it is not an artifact of where samples sit. At 13.9% the per-quintile
PC1–4 deltas are all non-significant (t = 0.33–1.54) while scale-free relative error shows a
leverage gradient — the fix helps samples far from the centre (q5 +0.093, t=+9.5) and hurts
core samples (q1 −0.115, t=−7.6).

**The fix should still ship.** At 53.5% — where `eval-S01` actually sits — it is decisive
and uniform. The old code's behaviour at full coverage is indefensible on its own terms: it
leaves 55% of the reference configuration unexplained and halves every centroid distance. The
13.9% wash is a reason to treat the sparse regime as unsolved, not a reason to keep a convention
that is wrong by construction.

## 4. The claim in docs/spec.md:259

The spec states the bias "pulls an admixed or non-European query toward the center of the
reference cloud… The bias is directional, not random." Tested two ways.

**As shrinkage of the simple projection** (the estimator the claim is actually about), measured
at full coverage by the projection coefficient `(est·true)/(true·true)`, which is unbiased under
isotropic error — 1.0 means no radial bias:

    overall 0.649, contracted in 100% of 250 samples
    AMR 0.587   SAS 0.619   EUR 0.631   AFR 0.658   EAS 0.751

This is the one measurement in the artifact the full-coverage level does *not* degenerate: the
simple path never touches the augmented decomposition, so its numbers are a genuine out-of-sample
result at every level. The contraction deepens as coverage falls — 0.649 / 0.637 / 0.588 / 0.457
across the four levels — and is 100% of samples at all of them. Quote 0.649 only with "at full
coverage" attached; it is the mildest of the four.

The directional shrinkage is **confirmed** — 35% contraction, every single sample, no exceptions.
It is worst for AMR, the admixed group, which supports that half of the claim. But it is *not*
ordered by "non-European": EAS is the least affected group and EUR sits mid-pack. The blanket
"non-European" framing is not what the data show; "worst for admixed" is.

**As an interaction** — does the fix buy more in some groups than in EUR? On scale-free relative
error at 13.9%: AFR +0.100 (t=+4.03), AMR +0.047 (t=+2.31), EAS +0.045 (t=+2.02), all excluding
zero; SAS −0.004 (n.s.). At 53.5%: AFR +0.130 and EAS +0.128 both strongly significant, AMR and
SAS not. So the defect did cost AFR (and EAS) more than EUR — but again SAS, a non-European
group, shows nothing, so the effect does not track "non-European" as a category.

Recommend narrowing the spec's wording to what is supported: directional centre-ward shrinkage in
100% of samples, strongest for admixed (AMR) samples, with group differences that are real but
not summarised by "non-European."

## 5. Recommendation: adopt TRACE-proper (not done here)

TRACE/FRAPOSA scale **both** sides by the square root of their own eigenvalues, fit in that
space, then divide back. That is not equivalent to scaling neither — a rotation does not commute
with a non-scalar diagonal — and it measures better at every coverage level where the test is
informative:

| markers | no-sqrt PC1–4 | TRACE PC1–4 | ratio | paired | projection coef (no-sqrt → TRACE) |
|---|---|---|---|---|---|
| 27,405 | 0.0001 | 0.0035 | 0.03x | t=+16.1, no-sqrt 250/250 | 1.000 → 1.002 |
| 14,662 | 0.118 | 0.042 | 2.79x | t=−28.4, TRACE 248/250 | 0.803 → 0.914 |
| 3,809 | 0.416 | 0.124 | 3.35x | t=−35.9, TRACE 250/250 | 0.370 → 0.645 |
| 987 | 0.593 | 0.260 | 2.28x | t=−31.4, TRACE 249/250 | 0.187 → 0.451 |

TRACE is **2.3–3.4x better on the PCs that carry ancestry at each of the three thinned levels**, on
essentially every sample, and it is far less radially shrunk. Critically, **it does not have the
sparse-coverage regression**: where no-sqrt collapses at 987 markers, TRACE degrades gracefully.

The 27,405 row is the degenerate control and is the reason the claim is scoped to the thinned
levels: there no-sqrt reconstructs the reference PCA by construction (error 1e-04), so TRACE's
3.5e-03 "loses" 250/250 against a number that is an artifact of the test, not an estimate. Do not
read that row as evidence against TRACE, and do not read it as evidence for it.

The one place TRACE is worse is total 20-dimensional L2 (e.g. 0.083 vs 0.067 at 13.9%), because
its PC5–PC20 error is larger. That matters only because the distance and neighbour code weights
all 20 PCs equally — see §6.

Adopting it means changing `augmented_pca` to return eigenvalues too, scaling the reference side
by `sqrt(ref_pca.eigenval)`, fitting there, and dividing the query estimate back. It is a larger
change than a one-line scale fix and deserves its own review and its own validation run; the
harness already supports it as a third arm.

## 6. Open, not addressed here

- **No floor on shared markers.** Nothing in `ancestry.py` or the ancestry skill bounds
  `n_markers_shared_with_reference`, and below ~4% coverage the current convention is measurably
  worse than the one it replaced. A floor, or reducing `--pcs` when the shared set is small so
  poorly-determined trailing PCs never enter the Procrustes fit, would close it.
- **Distances weight noise equally with signal.** In plink eigenvec units every PC column has the
  same variance, so `distance_to_centroid_adp` gives PC20 (eigenvalue 1.89) the same weight as
  PC1 (199.71). PC1–4 hold 353.5 of the 393.1 top-20 eigenvalue mass; the other 16 PCs dominate
  the distance. Pre-existing, unchanged by the fix, and the reason the 20-d L2 metric above
  disagrees with the PC1–4 metric.
- **`marker_set.present_on_this_array` is circular** for this reference build: 1000G was
  intersected with one patient's array positions before the PCA was built
  (`bcftools view -R /tmp/patient_positions.tsv`), so "27,405 of 27,405" is guaranteed by
  construction. Against a properly built reference the same array carries 13.9%.
- **Nothing here certifies the production path end to end.** The harness draws query and
  reference from the same pfile, so it never exercises query-side missingness, the shared-set
  AF/ALT filter, or the allele-orientation join.
