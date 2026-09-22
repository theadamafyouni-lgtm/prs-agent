#!/usr/bin/env python3
"""
build_reference.py -- reference distribution provider for the INT-1 interface.

Builds a PRS reference distribution for ONE patient and ONE scoring model by
scoring a genetically-similar slice of 1000 Genomes phase 3 on the patient's own
resolved variant set, and emits the JSON artifact described in
inputs/reference-distribution.md.

This is a separate provider. It does not import from, read from, or write into
the agent repo. The agent consumes the artifact it did not produce.

Steps:
  1. Regenerate the plink score input from the resolved dosage rows joined to the
     harmonized scoring file. Verified against a known sum of |beta|.
  2. Select the k nearest reference samples in PC space.
  3. Score them with plink2 on the patient's resolved set (same-resolved-set parity).
  4. Emit the artifact.
"""

import argparse
import collections
import datetime
import gzip
import hashlib
import json
import math
import os
import subprocess
import sys

import numpy as np

# The scoring file's own #genome_build header says hg38. That describes the
# AUTHORS' original build. The hm_chr / hm_pos columns are GRCh37, per the
# #HmPOS_build header. We join on hm_chr / hm_pos and ignore chr_name /
# chr_position and #genome_build entirely.
HM_CHR_COL = "hm_chr"
HM_POS_COL = "hm_pos"
EA_COL = "effect_allele"
# Bound from --effect-weight-col at startup. Only six columns are universal across PGS
# Catalog scoring files -- effect_allele, hm_chr, hm_pos, hm_rsID, hm_source and
# hm_inferOtherAllele -- and the weight column is not one of them, which is why
# match_variants.py and weight_coverage.py both take it as an argument too.
WEIGHT_COL = None

QUANTILES = [1, 5, 10, 25, 50, 75, 90, 95, 99]

# The distance curve is reported in full, ALWAYS, independent of --k. Choosing a
# point on this curve -- the cohort boundary -- is the agent's decision, not this
# tool's. --k governs only which cohort actually gets scored in step 3.
CURVE_RANKS = [1, 10, 25, 50, 100, 200, 300, 500, 750, 1000]
COHORT_SNAPSHOT_KS = [50, 300, 1000]
# rank 1 is included because the d1-vs-d300 contrast is what separates "this
# patient sits at the centre of the cloud" from "this patient sits inside a
# cluster", and cohort_fit_note reports that separation. A claim in the artifact
# must carry the measurement it rests on.
BASELINE_RANKS = [1, 10, 50, 100, 300, 500, 1000]
# k values at which superpopulation purity is reported. why_this_k rests on where
# composition breaks down, so the breakdown is measured across a grid rather than
# read off the three cohort snapshots.
PURITY_KS = [50, 100, 150, 200, 250, 300, 350, 400, 500, 750, 1000]
# The log-log slope is read over a multiplicative window and its peak is scanned,
# not sampled at CURVE_RANKS -- reading a peak off ten sampled ranks would put it
# wherever the grid falls rather than where the curve puts it.
SLOPE_WINDOW = 1.6
SLOPE_SCAN_MAX = 1200

# Thresholds that select which verdict cohort_fit_note reports. They exist so the
# note's prose follows from the measurement instead of being written around it: if
# the numbers stop supporting a centrality disclosure, the code stops emitting one.
# Percentiles are "share of the comparison group closer to the centroid / with a
# tighter curve than the patient", so LOW means the patient is the extreme one.
CENTRALITY_PCT_FLAG = 5.0
CENTRALITY_PCT_WATCH = 15.0
SHAPE_PCT_FLAG = 10.0


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def die(msg):
    """Hard stop. Used only for conditions that make the artifact untrustworthy.

    A refusal (INT-2, too few similar samples) is NOT one of these -- that path
    writes a valid artifact and exits 0.
    """
    sys.stderr.write("\nFATAL: %s\n" % msg)
    sys.exit(1)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def variant_key(chrom, pos):
    """Normalise a coordinate to a single joinable string.

    int(pos) collapses any leading-zero / whitespace difference between the two
    files, so the join cannot silently miss on formatting.
    """
    return "%s:%d" % (str(chrom).strip(), int(str(pos).strip()))


# ----------------------------------------------------------------------------
# STEP 1 -- regenerate the score input
# ----------------------------------------------------------------------------

def read_resolved_positions(dosages_path):
    """Return {variant_key: source} for status == 'resolved' rows only.

    Keeps `source` (typed/imputed) so the imputation disclosure in step 4 is
    computed from the data rather than asserted.
    """
    resolved = {}
    n_rows = 0
    status_counts = collections.Counter()

    with open(dosages_path, "r") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        try:
            i_chrom = header.index("chrom")
            i_pos = header.index("pos")
            i_source = header.index("source")
            i_status = header.index("status")
        except ValueError:
            die("dosages file header missing an expected column; got %r" % (header,))

        for line in fh:
            f = line.rstrip("\n").split("\t")
            n_rows += 1
            status = f[i_status]
            status_counts[status] += 1
            if status != "resolved":
                continue
            key = variant_key(f[i_chrom], f[i_pos])
            if key in resolved:
                die("duplicate resolved position %s in dosages file -- the join key "
                    "is not unique and the weight sum would double-count" % key)
            resolved[key] = f[i_source]

    return resolved, n_rows, status_counts


def build_score_input(scoring_gz, resolved, out_path, expected_abs_beta,
                      expected_imputed_abs_beta):
    """Join the resolved positions to the scoring file and emit 'ID EA BETA'.

    The effect_weight string is copied through VERBATIM -- no float round-trip --
    so the emitted file carries exactly the authors' precision. The verification
    sum is computed separately in float via math.fsum.
    """
    abs_betas = []
    abs_betas_by_source = {"typed": [], "imputed": []}
    n_by_source = collections.Counter()
    matched_keys = set()
    n_scoring_rows = 0

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    meta_header = {}

    with gzip.open(scoring_gz, "rt") as fh, open(out_path, "w") as out:
        # READ the '#' preamble, do not skip it. pgs_id, the trait and the harmonized
        # build all live there, and the artifact has to describe THIS file rather than
        # assert a model chosen when the tool was written.
        header = None
        for line in fh:
            if line.startswith("#"):
                s = line.lstrip("#").strip()
                if "=" in s:
                    k, v = s.split("=", 1)
                    meta_header[k.strip()] = v.strip()
                continue
            header = line.rstrip("\n").split("\t")
            break
        if header is None:
            die("scoring file has no column header line")

        for col in (HM_CHR_COL, HM_POS_COL, EA_COL, WEIGHT_COL):
            if col not in header:
                die("scoring file header lacks required column %r; got %r" % (col, header))
        i_chr = header.index(HM_CHR_COL)
        i_pos = header.index(HM_POS_COL)
        i_ea = header.index(EA_COL)
        i_w = header.index(WEIGHT_COL)

        out.write("ID EA BETA\n")
        for line in fh:
            f = line.rstrip("\n").split("\t")
            n_scoring_rows += 1
            hc, hp = f[i_chr], f[i_pos]
            if not hc or not hp or hc == "NA" or hp == "NA":
                continue
            key = variant_key(hc, hp)
            src = resolved.get(key)
            if src is None:
                continue
            if key in matched_keys:
                die("scoring file has a duplicate hm_chr:hm_pos at %s -- the join "
                    "is not 1:1 and the weight sum would double-count" % key)
            matched_keys.add(key)

            w_str = f[i_w]
            ea = f[i_ea]
            out.write("%s %s %s\n" % (key, ea, w_str))

            aw = abs(float(w_str))
            abs_betas.append(aw)
            n_by_source[src] += 1
            if src in abs_betas_by_source:
                abs_betas_by_source[src].append(aw)
            else:
                die("dosages row for %s has an unrecognised source %r; the "
                    "typed/imputed split behind the R1 disclosure would be "
                    "incomplete" % (key, src))

    # fsum, not sum(): 1.09M additions accumulate enough float error to move a
    # 4-decimal comparison.
    total_abs_beta = math.fsum(abs_betas)
    typed_abs = math.fsum(abs_betas_by_source["typed"])
    imputed_abs = math.fsum(abs_betas_by_source["imputed"])

    unmatched = set(resolved) - matched_keys

    stats = {
        "scoring_file_header": meta_header,
        "n_variants_emitted": len(matched_keys),
        "n_scoring_file_rows": n_scoring_rows,
        "n_resolved_positions": len(resolved),
        "n_resolved_not_in_scoring_file": len(unmatched),
        "n_variants_typed": n_by_source["typed"],
        "n_variants_imputed": n_by_source["imputed"],
        "sum_abs_beta": total_abs_beta,
        "sum_abs_beta_typed": typed_abs,
        "sum_abs_beta_imputed": imputed_abs,
        "imputed_weight_fraction": (imputed_abs / total_abs_beta) if total_abs_beta else None,
    }

    # The 19.0372 check verifies the TOTAL. It cannot detect an error in the
    # typed/imputed split, and that split is the number the R1 disclosure
    # reports. So it gets its own cross-check against the value supplied for it.
    # Non-fatal: only the total was mandated as a stop condition. But a
    # disagreement is printed loudly and recorded in the artifact rather than
    # quietly overwritten by the expected value.
    stats["expected_sum_abs_beta_imputed"] = expected_imputed_abs_beta
    if expected_imputed_abs_beta is None:
        stats["imputed_split_verification"] = "not checked (no expected value supplied)"
    elif round(imputed_abs, 4) == round(float(expected_imputed_abs_beta), 4):
        stats["imputed_split_verification"] = "agrees with the supplied value"
    else:
        stats["imputed_split_verification"] = (
            "DISAGREES: computed %.4f, supplied %.4f. The artifact reports the "
            "COMPUTED value." % (imputed_abs, float(expected_imputed_abs_beta)))
        sys.stderr.write(
            "\nWARNING: imputed |beta| cross-check disagrees\n"
            "  computed from the data : %.4f\n"
            "  supplied as expected   : %.4f\n"
            "  The artifact will report the computed value, not the supplied one.\n"
            % (imputed_abs, float(expected_imputed_abs_beta)))

    # VERIFICATION, when a target is supplied. This is a REGRESSION GUARD, not a
    # property of the scoring file: build_score_input joins the scoring file against
    # the RESOLVED SET, so the sum changes whenever the resolved set changes. A value
    # therefore belongs to one run of one patient and model, and there is no correct
    # default. Supplied and mismatched -> stop, the join is wrong. Not supplied -> the
    # computed value is recorded and reported as unchecked, never quietly asserted.
    got = round(total_abs_beta, 4)
    stats["sum_abs_beta_rounded"] = got

    if expected_abs_beta is None:
        stats["expected_sum_abs_beta"] = None
        stats["verification_passed"] = None
        stats["verification_note"] = (
            "NOT verified: no --expected-abs-beta supplied, so nothing checked that the "
            "join reproduced a prior run. The computed value is recorded. Pass %.4f on a "
            "rerun of this same patient and model to make the guard meaningful." % got)
        sys.stderr.write(
            "\nNOTE: step 1 join was NOT verified -- no --expected-abs-beta supplied.\n"
            "  computed sum of |BETA| : %.4f\n"
            "  Pass that value on a rerun of the same patient and model to catch a\n"
            "  changed join. It is a regression guard, not a constant.\n" % got)
        return stats

    want = round(float(expected_abs_beta), 4)
    stats["expected_sum_abs_beta"] = want
    stats["verification_passed"] = (got == want)
    if got != want:
        sys.stderr.write(
            "\nSTEP 1 VERIFICATION FAILED\n"
            "  sum of |BETA| over emitted file : %.4f\n"
            "  expected                        : %.4f\n"
            "  difference                      : %+.6f\n"
            "  rows emitted                    : %d\n"
            "  resolved positions              : %d\n"
            "  resolved not found in scoring   : %d\n"
            % (got, want, total_abs_beta - want, len(matched_keys),
               len(resolved), len(unmatched))
        )
        die("join is wrong -- refusing to proceed with a mismatched variant set")

    return stats


# ----------------------------------------------------------------------------
# STEP 2 -- select the reference cohort
# ----------------------------------------------------------------------------

def load_eigenvec(path, n_pcs):
    """Return (iids, coords[n_samples, n_pcs])."""
    iids = []
    rows = []
    with open(path, "r") as fh:
        header = fh.readline().lstrip("#").rstrip("\n").split("\t")
        # tolerate FID/IID or IID-only layouts
        if header[0] == "FID":
            i_iid, first_pc = 1, 2
        else:
            i_iid, first_pc = 0, 1
        n_avail = len(header) - first_pc
        if n_avail < n_pcs:
            die("eigenvec has %d PCs but the projection used %d" % (n_avail, n_pcs))
        for line in fh:
            f = line.rstrip("\n").split("\t")
            iids.append(f[i_iid])
            rows.append([float(x) for x in f[first_pc:first_pc + n_pcs]])
    return iids, np.asarray(rows, dtype=float)


def load_eigenval(path, n_pcs):
    """Eigenvalues of the reference PCA.

    Not used in any distance. Loaded so ancestry_grouping_definition can report how
    unevenly the variance is spread across the PCs it weights equally -- a property
    of the distance rule this provider is built on, and one a reader needs in order
    to know how much to make of which samples land either side of the k-th
    neighbour.
    """
    try:
        with open(path, "r") as fh:
            vals = [float(x) for x in fh if x.strip()]
    except (IOError, OSError, ValueError):
        return None
    return vals[:n_pcs] or None


def counts_text(counts):
    """{'SAS': 294, 'AFR': 3} -> 'SAS 294, AFR 3', largest first."""
    return ", ".join("%s %d" % (g, n) for g, n in
                     sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def load_panel(path):
    """Return {sample: (pop, super_pop)}."""
    labels = {}
    with open(path, "r") as fh:
        header = fh.readline().rstrip("\n").split("\t")
        try:
            i_s, i_p, i_sp = (header.index("sample"), header.index("pop"),
                              header.index("super_pop"))
        except ValueError:
            die("panel file header missing sample/pop/super_pop; got %r" % (header,))
        for line in fh:
            if not line.strip():
                continue
            f = line.rstrip("\n").split("\t")
            labels[f[i_s]] = (f[i_p], f[i_sp])
    return labels


def counts_for(iids, labels, idx):
    """Population and superpopulation counts for a set of row indices."""
    pop = collections.Counter()
    sup = collections.Counter()
    for i in idx:
        p, s = labels.get(iids[i], ("UNKNOWN", "UNKNOWN"))
        pop[p] += 1
        sup[s] += 1
    return dict(pop), dict(sup)


def neighbour_selfcheck(iids, coords, labels, patient_vecs, projection):
    """Reproduce the agent's own recorded k=50 nearest-neighbour result.

    Purpose: confirm our coordinates live in the SAME decomposition as the
    eigenvec -- same axis order, sign and scale. If neither patient vector
    reproduces the agent's result, the spaces do not match and every distance
    downstream is meaningless. Stop hard rather than reconcile.
    """
    recorded = projection.get("nearest_reference_samples") or {}
    k_rec = recorded.get("k")
    want_pop = recorded.get("by_population")
    want_sup = recorded.get("by_superpopulation")
    if not (k_rec and want_pop):
        return {"performed": False,
                "note": "projection.json carries no nearest_reference_samples block to check against"}

    results = {}
    for name, vec in patient_vecs.items():
        d = np.linalg.norm(coords - vec, axis=1)
        order = np.argsort(d, kind="stable")[:k_rec]
        got_pop, got_sup = counts_for(iids, labels, order)
        # A tie straddling the k-th boundary would make the comparison
        # order-dependent rather than wrong. Detect and say so.
        ds = np.sort(d, kind="stable")
        boundary_tie = bool(k_rec < len(ds) and ds[k_rec - 1] == ds[k_rec])
        results[name] = {
            "matches": (got_pop == want_pop and
                        (want_sup is None or got_sup == want_sup)),
            "by_population": got_pop,
            "by_superpopulation": got_sup,
            "boundary_tie_at_k": boundary_tie,
        }

    any_match = any(r["matches"] for r in results.values())
    return {
        "performed": True,
        "k_checked": k_rec,
        "recorded_by_population": want_pop,
        "recorded_by_superpopulation": want_sup,
        "per_vector": results,
        "any_vector_reproduces_agent_result": any_match,
    }


def distance_curve(d_sorted, n_available):
    """Distance to the r-th nearest neighbour across CURVE_RANKS, 1-indexed.

    Reported regardless of --k. The shape of this curve is what says how well the
    panel fits the patient; a single k hides it.
    """
    return collections.OrderedDict(
        (r, float(d_sorted[r - 1])) for r in CURVE_RANKS if r <= n_available)


def cohort_snapshots(iids, labels, order, n_available):
    """Population / superpopulation composition at several candidate cohort sizes."""
    out = collections.OrderedDict()
    for kk in COHORT_SNAPSHOT_KS:
        if kk > n_available:
            continue
        pop, sup = counts_for(iids, labels, order[:kk])
        out[kk] = {"population_counts": pop, "superpopulation_counts": sup}
    return out


def reference_baseline(coords, ranks, chunk=128):
    """For every reference sample, its distance to its OWN r-th nearest neighbour
    within the reference set, excluding itself.

    This is the scale the patient's curve is read against: what "close" looks like
    for someone who genuinely belongs to this panel. Without it the patient's
    distances are bare numbers with no reference frame.

    Computed by direct chunked subtraction rather than the Gram-matrix identity,
    which loses precision at small distances -- and small distances are exactly
    what the low ranks measure.
    """
    n = coords.shape[0]
    out = {r: np.empty(n, dtype=float) for r in ranks}
    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        block = np.linalg.norm(
            coords[start:stop, None, :] - coords[None, :, :], axis=2)
        for i in range(start, stop):
            block[i - start, i] = np.inf   # exclude self
        block.sort(axis=1)
        for r in ranks:
            if r <= n - 1:
                out[r][start:stop] = block[:, r - 1]
            else:
                out[r][start:stop] = np.nan
    return out


def percentile_of(value, arr):
    """Where `value` falls in `arr`, as a percentile. 0 = closer than everyone."""
    finite = arr[np.isfinite(arr)]
    if not len(finite):
        return None
    return float(100.0 * np.mean(finite < value))


def _log_log_slope(d_sorted, r, n_available, window=SLOPE_WINDOW):
    """d(log distance)/d(log rank) at rank r, over a multiplicative window."""
    lo, hi = max(2, int(r / window)), min(n_available, int(r * window))
    if hi <= lo or d_sorted[lo - 1] <= 0:
        return None
    return float((math.log(d_sorted[hi - 1]) - math.log(d_sorted[lo - 1]))
                 / (math.log(hi) - math.log(lo)))


def curve_diagnostics(d_sorted, sup_sorted, n_available, k):
    """Everything why_this_k rests on: where the distance curve says the local
    neighbourhood runs out, and where composition says the same.

    The local log-log slope is the elbow test. A uniformly filled d-dimensional
    cloud gives a constant 1/d, so the only "elbow" available to read is a
    departure from constant -- and the rank where that departure peaks is the rank
    at which the curve is climbing fastest, i.e. where the local neighbourhood is
    being exhausted. Scanned rather than sampled, because reading the peak off ten
    curve ranks would put it wherever the sampling grid happens to fall.
    """
    out = collections.OrderedDict()

    slope = collections.OrderedDict()
    for r in CURVE_RANKS:
        s = _log_log_slope(d_sorted, r, n_available)
        if s is not None:
            slope["d%d" % r] = s
    out["log_log_slope"] = slope
    out["log_log_slope_definition"] = (
        "d(log distance)/d(log rank), over a %.1fx window either side of each rank. "
        "Constant means a uniformly filled cloud with no boundary; the value is "
        "1/(local dimension). A peak marks the rank where distance is climbing "
        "fastest, which is where the local neighbourhood runs out."
        % SLOPE_WINDOW)

    # Locate the peak by a fine scan, and report how wide it is. A narrow band is a
    # boundary you can read; a band spanning most of the curve is not.
    scan_hi = min(n_available - 1, SLOPE_SCAN_MAX)
    scanned = [(r, _log_log_slope(d_sorted, r, n_available))
               for r in range(5, scan_hi)]
    scanned = [(r, s) for r, s in scanned if s is not None]
    if scanned:
        peak_r, peak_s = max(scanned, key=lambda rs: rs[1])
        band = [r for r, s in scanned if s >= 0.95 * peak_s]
        out["slope_peak"] = {
            "rank": peak_r,
            "slope": peak_s,
            "band_within_95pct_of_peak": [min(band), max(band)],
            "scan_range": [5, scan_hi],
            "note": ("The rank at which the distance curve climbs fastest, and the "
                     "range of ranks within 5%% of that peak. Scanned over ranks "
                     "5-%d; a peak at the scan edge would be an edge effect and not "
                     "a feature." % scan_hi),
        }

    # Superpopulation purity across k, and the last k at which the cohort is a
    # single group. This is the axis k was actually chosen on.
    purity = collections.OrderedDict()
    for kk in PURITY_KS:
        if kk > n_available:
            continue
        c = collections.Counter(sup_sorted[:kk])
        top, n_top = c.most_common(1)[0]
        purity[kk] = {"largest_superpopulation": top, "n": int(n_top),
                      "pct": round(100.0 * n_top / kk, 1),
                      "n_other": int(kk - n_top)}
    out["superpopulation_purity_by_k"] = purity
    first_other = next((i + 1 for i, g in enumerate(sup_sorted) if g != sup_sorted[0]),
                       None)
    out["last_k_single_superpopulation"] = (first_other - 1) if first_other else n_available
    out["first_rank_of_a_second_superpopulation"] = first_other

    # Boundary degeneracy. If the k-th and (k+1)-th neighbours are separated by far
    # less than the typical local gap, then which samples land inside the cohort is
    # a property of where an integer k cuts the ordering, not of the patient.
    if 1 <= k < n_available:
        lo, hi = max(0, k - 50), min(n_available, k + 50)
        gaps = np.diff(d_sorted[lo:hi])
        out["boundary_degeneracy"] = {
            "d_k": float(d_sorted[k - 1]),
            "d_k_plus_1": float(d_sorted[k]),
            "gap_at_boundary": float(d_sorted[k] - d_sorted[k - 1]),
            "median_gap_within_50_ranks": float(np.median(gaps)) if len(gaps) else None,
            "note": ("A boundary gap at or below the local median gap means cohort "
                     "membership at the margin is set by the choice of an integer k, "
                     "not by any separation in the data."),
        }
    return out


def metric_sensitivity(coords, patient_vec, order, k, sup_arr, src, eigenvals):
    """How much of the neighbour ordering is decided by PCs that carry little
    variance, and does the cohort's superpopulation mixture survive weighting them
    down?

    This exists because the artifact reports a cohort composition, and a reader
    will take a count like "AFR 3" for a statement about the patient unless told
    what produced it. Euclidean distance over plink eigenvectors weights every PC
    equally -- the columns are all normalised to the same variance -- so PCs that
    hold a few percent of the eigenvalue mass get the same say as the ones that
    separate continental groups. Whether that matters is measurable, so it is
    measured rather than left as a caveat.
    """
    if not eigenvals or len(eigenvals) != coords.shape[1]:
        return None
    ev = np.asarray(eigenvals, dtype=float)
    total = float(ev.sum())
    if total <= 0:
        return None

    # How many leading PCs it takes to hold TOP_SHARE of the eigenvalue mass.
    TOP_SHARE = 0.85
    cum = np.cumsum(ev) / total
    n_top = int(np.searchsorted(cum, TOP_SHARE) + 1)

    sq = (coords - patient_vec) ** 2
    tail_share = sq[:, n_top:].sum(axis=1) / sq.sum(axis=1)
    sel = order[:k]

    out = collections.OrderedDict()
    out["definition"] = (
        "Distances here are Euclidean over %d plink2 eigenvector columns, which are "
        "all normalised to the same variance, so every PC carries equal weight in "
        "the ordering regardless of how much variance it explains."
        % coords.shape[1])
    out["n_leading_pcs_holding_%d_pct_of_eigenvalue_mass" % int(100 * TOP_SHARE)] = n_top
    out["leading_pc_eigenvalue_share"] = round(float(cum[n_top - 1]), 4)
    out["trailing_pc_share_of_squared_distance_panel_wide"] = round(
        float(np.median(tail_share)), 4)
    out["trailing_pc_share_of_squared_distance_within_cohort"] = round(
        float(np.median(tail_share[sel])), 4)
    out["share_note"] = (
        "Median share of squared distance contributed by PC%d and above. Panel-wide "
        "the leading PCs still do real work, because they are what separates the "
        "continental groups. Within the selected cohort they do almost none: at that "
        "scale the neighbours are already alike on the leading PCs, so the ordering "
        "is decided by the trailing ones." % (n_top + 1))

    # Does the mixture survive weighting PCs by the variance they actually explain?
    dw = np.linalg.norm((coords - patient_vec) * np.sqrt(ev), axis=1)
    sel_w = np.argsort(dw, kind="stable")[:k]
    counts_w = collections.Counter(sup_arr[sel_w])
    off_src = [i for i in sel if sup_arr[i] != src]
    retained = [i for i in off_src if i in set(sel_w)]
    out["eigenvalue_weighted_recheck"] = {
        "definition": ("the same k nearest neighbours, with each PC scaled by the "
                       "square root of its eigenvalue so the ordering is weighted by "
                       "the variance each PC explains"),
        "superpopulation_counts": dict(counts_w),
        "n_outside_source_group_under_equal_weighting": len(off_src),
        "n_of_those_still_selected_under_eigenvalue_weighting": len(retained),
        "note": ("A count that survives both weightings is a fact about the patient's "
                 "position. One that does not is a fact about the metric."),
    }
    return out


def select_cohort(iids, coords, labels, patient_vec, k, min_cohort, max_kth_distance,
                  eigenvals=None):
    d = np.linalg.norm(coords - patient_vec, axis=1)
    order = np.argsort(d, kind="stable")
    n_available = len(iids)
    n_selected = min(k, n_available)
    sel = order[:n_selected]
    sel_d = d[sel]

    def rank_distance(r):
        """Distance to the r-th nearest neighbour, 1-indexed. None if r > n."""
        return float(d[order[r - 1]]) if r <= n_available else None

    pop_counts, sup_counts = counts_for(iids, labels, sel)

    d_sorted = d[order]
    sup_sorted = [labels.get(iids[i], ("UNKNOWN", "UNKNOWN"))[1] for i in order]
    info = {
        "n_available": n_available,
        "n_selected": int(n_selected),
        "k_requested": k,
        "neighbour_distances": {
            "d1": rank_distance(1),
            "d10": rank_distance(10),
            "d50": rank_distance(50),
            "dk": float(sel_d[-1]) if n_selected else None,
            "k_for_dk": int(n_selected),
        },
        # reported independent of --k; the boundary is the agent's call
        "distance_curve": distance_curve(d_sorted, n_available),
        "curve_diagnostics": curve_diagnostics(d_sorted, sup_sorted, n_available,
                                               int(n_selected)),
        "cohort_snapshots": cohort_snapshots(iids, labels, order, n_available),
        "population_counts": pop_counts,
        "superpopulation_counts": sup_counts,
        "selected_samples": [iids[i] for i in sel],
        "selected_distances": [float(x) for x in sel_d],
    }

    # The group the cohort is drawn from -- a property of the SELECTED SET, not a
    # label for the patient. It exists so the fit assessment reads the patient's
    # curve against the samples the cohort is actually made of, and so the metric
    # check below knows which members are the marginal ones.
    info["cohort_source_group"] = (max(sup_counts, key=sup_counts.get)
                                   if sup_counts else None)
    sup_arr = np.array([labels.get(i, ("UNKNOWN", "UNKNOWN"))[1] for i in iids])
    info["metric_sensitivity"] = metric_sensitivity(
        coords, patient_vec, order, int(n_selected), sup_arr,
        info["cohort_source_group"], eigenvals)

    # INT-2 refusal conditions. Both are correct outcomes, not errors.
    #
    # The reason string must name the ACTUAL cause. A cohort can fall short of
    # --min-cohort for two quite different reasons, and conflating them would
    # put a false claim about the reference panel into a clinical artifact:
    #   (a) the panel genuinely holds fewer samples than the threshold, or
    #   (b) the panel is large but --k asked for fewer than --min-cohort.
    # Only (a) is "too few similar participants exist".
    refusals = []
    if n_selected < min_cohort:
        if n_available < min_cohort:
            refusals.append(
                "the reference panel holds only %d samples in total, below the "
                "--min-cohort threshold of %d; too few participants to form a "
                "stable distribution" % (n_available, min_cohort))
        else:
            refusals.append(
                "cohort of %d is below the --min-cohort threshold of %d because "
                "--k was set to %d. %d reference samples were available; this is a "
                "configuration shortfall, NOT a finding that too few genetically "
                "similar participants exist"
                % (n_selected, min_cohort, k, n_available))
    dk = info["neighbour_distances"]["dk"]
    if max_kth_distance is not None and dk is not None and dk > max_kth_distance:
        refusals.append(
            "distance to the %dth nearest neighbour is %.6f, beyond the "
            "--max-kth-distance limit of %.6f; the nearest available reference "
            "samples are not genetically similar enough to this patient"
            % (n_selected, dk, max_kth_distance))
    info["refusals"] = refusals
    return info


# ----------------------------------------------------------------------------
# STEP 3 -- score the cohort with plink2
# ----------------------------------------------------------------------------

COMPLEMENT = {"A": "T", "T": "A", "C": "G", "G": "C"}


def run_plink2(plink2, argv, label):
    """Run plink2, echo the interesting log lines, die on failure."""
    cmd = [plink2] + argv
    print("    $ %s" % " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:] if proc.stdout else "")
        die("plink2 failed during %s (exit %d)" % (label, proc.returncode))
    return proc.stdout or ""


def import_reference(plink2, vcf, prefix):
    """VCF -> pgen. IDs are chrom:pos:ref:alt, NOT chrom:pos.

    '@:#' would collide at the positions carrying more than one record, and a
    collision is exactly where a silent drop would happen. Making the ID unique
    keeps every record addressable so the choice between them is made here,
    explicitly, rather than by whichever tool deduplicates first.
    """
    if os.path.exists(prefix + ".pgen") and os.path.exists(prefix + ".pvar"):
        print("    reusing existing %s.pgen (delete to force re-import)" % prefix)
        return
    run_plink2(plink2, ["--vcf", vcf, "--set-all-var-ids", "@:#:$r:$a",
                        "--new-id-max-allele-len", "1000",
                        "--make-pgen", "--out", prefix], "VCF import")


def read_pvar(path):
    """(chrom,pos) -> [(id, ref, alt), ...] preserving file order."""
    pmap = collections.defaultdict(list)
    n = 0
    with open(path, "r") as fh:
        cols = None
        for line in fh:
            if line.startswith("##"):
                continue
            f = line.rstrip("\n").split("\t")
            if line.startswith("#"):
                cols = {name: i for i, name in enumerate(f)}
                continue
            if cols is None:
                die("%s has no #CHROM header line" % path)
            n += 1
            pmap[variant_key(f[cols["#CHROM"]], f[cols["POS"]])].append(
                (f[cols["ID"]], f[cols["REF"]], f[cols["ALT"]]))
    return pmap, n


def load_alleles_for(scoring_gz, wanted):
    """{key: (effect_allele, other_allele)} for a small set of positions."""
    out = {}
    if not wanted:
        return out
    with gzip.open(scoring_gz, "rt") as fh:
        header = None
        for line in fh:
            if line.startswith("#"):
                continue
            header = line.rstrip("\n").split("\t")
            break
        i_chr, i_pos = header.index(HM_CHR_COL), header.index(HM_POS_COL)
        i_ea = header.index(EA_COL)
        i_oa = header.index("other_allele") if "other_allele" in header else None
        for line in fh:
            f = line.rstrip("\n").split("\t")
            key = variant_key(f[i_chr], f[i_pos])
            if key in wanted:
                out[key] = (f[i_ea], f[i_oa] if i_oa is not None else None)
    return out


def resolve_score_variants(score_input, pvar_map, scoring_gz, out_path):
    """Parity check + explicit resolution of multi-record positions.

    Rewrites the score file onto the reference's unique variant IDs. Every
    variant that does not survive is counted and its weight recorded -- a
    shortfall must be visible in the artifact, never silently absorbed.

    Multi-record resolution, in order:
      1. the record whose {REF,ALT} exactly equals the model's {EA,OA}
      2. failing that, the record whose alleles merely contain EA
      3. if neither leaves exactly one candidate, DROP and record it
    """
    # First pass: which of our positions are multi-record? Only those need the
    # other_allele, so we avoid holding 1.09M extra strings in memory.
    multi_keys = set()
    with open(score_input, "r") as fh:
        fh.readline()
        for line in fh:
            key = line.split(" ", 1)[0]
            if len(pvar_map.get(key, ())) > 1:
                multi_keys.add(key)
    allele_map = load_alleles_for(scoring_gz, multi_keys)

    acc = {
        "n_emitted": 0,
        "n_present_in_vcf": 0,
        "n_missing_from_vcf": 0,
        "n_multi_record_positions": 0,
        "n_multi_resolved_by_exact_allele_match": 0,
        "n_multi_resolved_by_effect_allele_only": 0,
        "n_multi_dropped_unresolvable": 0,
        "n_allele_mismatch": 0,
        "n_allele_mismatch_strand_flip_only": 0,
        "n_final_scored": 0,
    }
    abs_beta_lost = []
    missing_examples, mismatch_examples = [], []
    multi_decisions = []

    with open(score_input, "r") as fh, open(out_path, "w") as out:
        fh.readline()
        out.write("ID EA BETA\n")
        for line in fh:
            key, ea, beta = line.rstrip("\n").split(" ")
            acc["n_emitted"] += 1
            recs = pvar_map.get(key)

            if not recs:
                acc["n_missing_from_vcf"] += 1
                abs_beta_lost.append(abs(float(beta)))
                if len(missing_examples) < 10:
                    missing_examples.append(key)
                continue
            acc["n_present_in_vcf"] += 1

            if len(recs) == 1:
                vid, ref, alt = recs[0]
                if ea in (ref,) or ea in alt.split(","):
                    out.write("%s %s %s\n" % (vid, ea, beta))
                    acc["n_final_scored"] += 1
                else:
                    acc["n_allele_mismatch"] += 1
                    abs_beta_lost.append(abs(float(beta)))
                    flip = COMPLEMENT.get(ea)
                    if flip and (flip == ref or flip in alt.split(",")):
                        acc["n_allele_mismatch_strand_flip_only"] += 1
                    if len(mismatch_examples) < 10:
                        mismatch_examples.append((key, ea, ref, alt))
                continue

            # multi-record position
            acc["n_multi_record_positions"] += 1
            ea_m, oa_m = allele_map.get(key, (ea, None))
            exact = [r for r in recs
                     if oa_m is not None and {r[1], r[2]} == {ea_m, oa_m}]
            loose = [r for r in recs if ea_m == r[1] or ea_m in r[2].split(",")]

            chosen, how = None, None
            if len(exact) == 1:
                chosen, how = exact[0], "exact {EA,OA} == {REF,ALT} match"
                acc["n_multi_resolved_by_exact_allele_match"] += 1
            elif len(loose) == 1:
                chosen, how = loose[0], "only record carrying the effect allele"
                acc["n_multi_resolved_by_effect_allele_only"] += 1
            else:
                acc["n_multi_dropped_unresolvable"] += 1
                abs_beta_lost.append(abs(float(beta)))
                how = "DROPPED -- %d candidates matched, cannot choose" % len(loose)

            multi_decisions.append({
                "position": key,
                "model_effect_allele": ea_m,
                "model_other_allele": oa_m,
                "records_present": ["%s (%s/%s)" % (r[0], r[1], r[2]) for r in recs],
                "chosen": chosen[0] if chosen else None,
                "rule_applied": how,
                "abs_beta": abs(float(beta)),
            })
            if chosen:
                out.write("%s %s %s\n" % (chosen[0], ea, beta))
                acc["n_final_scored"] += 1

    acc["sum_abs_beta_not_scored"] = math.fsum(abs_beta_lost)
    acc["missing_examples"] = missing_examples
    acc["allele_mismatch_examples"] = [
        {"position": k, "model_ea": e, "vcf_ref": r, "vcf_alt": a}
        for k, e, r, a in mismatch_examples]
    acc["multi_record_decisions"] = multi_decisions
    return acc


def score_cohort(plink2, prefix, resolved_score, keep_path, sample_ids, out_prefix):
    with open(keep_path, "w") as fh:
        fh.write("#IID\n")
        for s in sample_ids:
            fh.write("%s\n" % s)

    log = run_plink2(plink2, ["--pfile", prefix, "--keep", keep_path,
                              "--score", resolved_score, "1", "2", "3", "header",
                              "cols=+scoresums,+nallele",
                              "--out", out_prefix], "scoring")

    # plink2's own count of what it actually used -- authoritative, not inferred
    n_processed = None
    for line in log.splitlines():
        s = line.strip()
        if s.startswith("--score:") and "variants processed" in s:
            for tok in s.split():
                if tok.isdigit():
                    n_processed = int(tok)
                    break
    return log, n_processed


def read_sscore(path):
    """-> (iids, {column: np.array})"""
    iids, cols, data = [], None, collections.defaultdict(list)
    with open(path, "r") as fh:
        header = fh.readline().lstrip("#").rstrip("\n").split("\t")
        cols = {n: i for i, n in enumerate(header)}
        if "IID" not in cols:
            die("%s has no IID column; got %r" % (path, header))
        for line in fh:
            f = line.rstrip("\n").split("\t")
            iids.append(f[cols["IID"]])
            for name, i in cols.items():
                if name == "IID":
                    continue
                try:
                    data[name].append(float(f[i]))
                except ValueError:
                    pass
    return iids, {k: np.asarray(v) for k, v in data.items() if len(v) == len(iids)}


# ----------------------------------------------------------------------------
# STEP 4 -- emit the artifact
# ----------------------------------------------------------------------------

def summarise(scores):
    if scores is None or not len(scores):
        return dict([("p%02d" % q, None) for q in QUANTILES] +
                    [("mean", None), ("sd", None), ("min", None),
                     ("max", None), ("n", 0)])
    out = collections.OrderedDict()
    for q in QUANTILES:
        out["p%02d" % q] = float(np.percentile(scores, q))
    out["mean"] = float(np.mean(scores))
    out["sd"] = float(np.std(scores, ddof=1)) if len(scores) > 1 else None
    out["min"] = float(np.min(scores))
    out["max"] = float(np.max(scores))
    out["n"] = int(len(scores))
    return out


def _fit_assessment(cohort):
    """Decide from the measurements whether a centrality effect is distorting cohort
    selection, and write the paragraph that follows from THAT decision.

    -> (verdict, prose)

    The previous version of this function wrote one fixed argument and substituted
    numbers into it. That is how it survived a sign change in its own data: the
    conclusion was in the format string, so no measurement could dislodge it. This
    one selects the conclusion first, against CENTRALITY_PCT_* / SHAPE_PCT_FLAG, and
    can return "no effect detected" -- the outcome the old shape could not express.
    """
    cen = cohort["centrality"]
    curve = cohort["distance_curve"]
    pvb = cohort["patient_vs_baseline"]
    panel_base = cohort["reference_baseline"]
    by_sup = cohort["reference_baseline_by_superpopulation"]
    pct_by_sup = cohort["patient_vs_baseline_by_superpopulation"]
    shape = cohort["curve_shape"]
    src = cohort["cohort_source_group"]

    pct_central = cen["pct_of_panel_closer_to_centroid"]
    pct_central_src = cen.get("pct_of_source_group_closer_to_centroid")
    src_n = cohort["superpopulation_counts"].get(src, 0)
    shape_src = shape["by_superpopulation"].get(src, {})
    shape_pct = shape_src.get("patient_percentile")

    centrality_flagged = pct_central < CENTRALITY_PCT_FLAG
    centrality_watch = pct_central < CENTRALITY_PCT_WATCH
    shape_flagged = shape_pct is not None and shape_pct < SHAPE_PCT_FLAG

    if centrality_flagged:
        verdict = "centrality effect present"
    elif centrality_watch or shape_flagged:
        verdict = "borderline -- see the numbers"
    else:
        verdict = "no centrality effect detected"

    # --- part 1: what was tested, and the centrality answer -------------------
    parts = [
        "Two things can make a selected cohort look closer than it should. The "
        "patient may sit at the centre of the reference cloud, where everything is "
        "near; or the patient may sit inside a genuinely dense cluster, where the "
        "closeness is real. Both are measured here rather than assumed. VERDICT: %s."
        % verdict,
        "Centrality: the patient's distance to the panel centroid is %.6f against a "
        "panel median of %.6f (5th percentile %.6f, 95th %.6f). %.1f%% of the %d "
        "reference samples are closer to the centroid than the patient%s."
        % (cen["patient_distance_to_centroid"],
           cen["panel_median_distance_to_centroid"],
           cen["panel_p5_distance_to_centroid"],
           cen["panel_p95_distance_to_centroid"],
           pct_central, cohort["n_available"],
           ("" if pct_central_src is None else
            ", and %.1f%% of %s -- the group supplying %d of the %d selected "
            "neighbours -- are" % (pct_central_src, src, src_n, cohort["n_selected"]))),
    ]
    if centrality_flagged:
        parts.append(
            "That is an extreme position: the patient is more central than all but "
            "%.1f%% of the panel, so the neighbours selected around them are closer "
            "than their true genetic neighbourhood warrants." % pct_central)
    else:
        parts.append(
            "That is an ordinary position inside the panel, not an extreme one, so "
            "cohort selection is not being pulled in by a point sitting at the middle "
            "of the reference cloud.")

    # --- part 2: the rank profile, and the baseline that makes it readable -----
    d1_meds = {g: by_sup[g]["d1"] for g in by_sup}
    tightest = min(d1_meds, key=d1_meds.get)
    loosest = max(d1_meds, key=d1_meds.get)
    p1 = pvb.get(1, {}).get("percentile_vs_panel")
    p300 = pvb.get(300, {}).get("percentile_vs_panel")
    src_pct = pct_by_sup.get(src, {})

    parts.append(
        "Rank profile, against the whole panel: the patient runs from the %.1fth "
        "percentile at d1 (distance %.6f) to the %.1fth at d300 (%.6f). Read alone "
        "that looks like flattening. It is mostly the baseline. The all-panel "
        "baseline mixes groups of very different density -- median d1 spans %.6f "
        "(%s) to %.6f (%s) -- and its median d1 of %.6f sits BELOW %s's %.6f while "
        "its median d300 of %.6f sits ABOVE %s's %.6f. A patient near %s is "
        "therefore scored as loose at d1 and tight at d300 by the mixture alone."
        % (p1, curve.get(1), p300, curve.get(300),
           d1_meds[tightest], tightest, d1_meds[loosest], loosest,
           panel_base[1]["median"], src, by_sup[src]["d1"],
           panel_base[300]["median"], src, by_sup[src]["d300"], src))

    have = [(r, src_pct["d%d" % r]) for r in BASELINE_RANKS
            if src_pct.get("d%d" % r) is not None]
    prof = ", ".join("%.1fth at d%d" % (v, r) for r, v in have)
    s1_, s300 = src_pct.get("d1"), src_pct.get("d300")
    if s1_ is not None and s300 is not None and p1 is not None and p300 is not None:
        tight_r, tight_v = min(have, key=lambda rv: rv[1])
        parts.append(
            "Against %s alone -- the only baseline the cohort is actually drawn from "
            "-- the d1-to-d300 drift that the whole argument rests on nearly "
            "vanishes: the %.1fth percentile at d1 and the %.1fth at d300, a move of "
            "%.1f points where the all-panel baseline showed %.1f. Full profile: %s. "
            "The patient's tightest point relative to %s is the %.1fth percentile at "
            "d%d, and the profile returns to the middle of the group by d300. A point "
            "sitting at the centre of the cloud looks nothing like this: its "
            "percentile falls as rank rises and stays down, because the whole panel "
            "keeps closing in on it."
            % (src, s1_, s300, abs(s1_ - s300), abs(p1 - p300), prof, src,
               tight_v, tight_r))
    else:
        parts.append("Against %s alone the profile is: %s." % (src, prof))

    # --- part 3: the scale-free statistic -------------------------------------
    parts.append(
        "The scale-free statistic settles it, because a group simply being denser or "
        "sparser than the panel cannot move it. Taking %s: the patient's is %.3f "
        "against a %s median of %.3f, the %.1fth percentile of %s members' own "
        "curves. %s"
        % (shape["definition"], shape["patient"], src, shape_src.get("median"),
           shape_pct, src,
           ("The patient's neighbourhood has the same shape as an ordinary member of "
            "the group the cohort comes from, which is what a fit looks like and is "
            "not what a centrality artifact looks like."
            if not shape_flagged else
            "That is flatter than the group's own curves, which is the shape a "
            "centrality artifact produces.")))

    # --- part 4: guard the no-label stance ------------------------------------
    parts.append(
        "Naming %s here is a statement about which reference samples the cohort is "
        "built from, not a group assignment for the patient. It appears because "
        "using the all-panel baseline instead is what produces the false flattening "
        "signal above. See ancestry_grouping_definition.why_no_label."
        % src)

    return verdict, " ".join(parts)


def _why_this_k(cohort, args, fit_verdict):
    """The k justification, rebuilt from the curve and composition measurements.

    The previous version dismissed the distance curve as untrustworthy because of a
    centrality effect recorded in cohort_fit_note. Whether that effect exists is now
    decided per run, so this text branches on the current verdict instead of
    assuming the answer -- and when the curve IS usable it has to say what the curve
    actually shows, which is not the same as endorsing k.
    """
    diag = cohort["curve_diagnostics"]
    src = cohort["cohort_source_group"]
    k = cohort["k_requested"]
    n_sel = cohort["n_selected"]
    n_src = cohort["superpopulation_counts"].get(src, 0)
    purity = diag["superpopulation_purity_by_k"]
    slope = diag["log_log_slope"]
    peak = diag.get("slope_peak")
    bd = diag.get("boundary_degeneracy") or {}
    pure_k = diag["last_k_single_superpopulation"]
    first_other = diag["first_rank_of_a_second_superpopulation"]

    traj = ", ".join("%.1f%% at k=%d" % (purity[kk]["pct"], kk)
                     for kk in (350, 500, 1000) if kk in purity)

    if fit_verdict != "no centrality effect detected":
        head = (
            "k was chosen on COHORT COMPOSITION, not on the distance curve. "
            "cohort_fit_note records a centrality effect (%s), which compresses the "
            "distances and makes them an untrustworthy basis for reading an elbow."
            % fit_verdict)
        upper = None
    elif peak:
        lo_b, hi_b = peak["band_within_95pct_of_peak"]
        upper = max(hi_b, pure_k)
        head = (
            "k was chosen on COHORT COMPOSITION, and the distance curve now "
            "corroborates that choice rather than being set aside for it. "
            "cohort_fit_note measured the centrality effect that would compress the "
            "curve and found none, so the curve can be read. Its log-log slope rises "
            "from %.3f at d10 to a peak of %.3f at rank %d and falls away after -- "
            "the rank where distance climbs fastest, which is where the patient's "
            "local neighbourhood runs out. Ranks %d-%d are within 5%% of that peak. "
            "Composition puts the same edge in nearly the same place: the cohort is "
            "entirely %s through k=%d and the first neighbour from a second "
            "superpopulation arrives at rank %s. Those are independent readings -- "
            "one from distances, one from panel labels -- and they land within a few "
            "dozen ranks of each other, which is the corroboration neither gives on "
            "its own and the reason the curve is quoted here at all."
            % (slope.get("d10", float("nan")), peak["slope"], peak["rank"],
               lo_b, hi_b, src, pure_k, first_other))
    else:
        upper = pure_k
        head = (
            "k was chosen on COHORT COMPOSITION. cohort_fit_note finds no centrality "
            "effect compressing the distance curve, but no slope peak could be "
            "located in it, so the curve is not doing any work in this choice.")

    body = (
        "Composition past that point degrades steadily rather than cliff-edging: at "
        "k=%d the cohort is %d/%d (%.1f%%) %s; it is %s."
        % (k, n_src, n_sel, 100.0 * n_src / n_sel, src, traj))

    tail = (
        "k=%d sits past both readings -- %d ranks beyond the purity break at %d%s -- "
        "and buys cohort size (%.1fx the --min-cohort floor of %d, and a percentile "
        "estimate resting on %d scores rather than %d) at the cost of admitting the "
        "first samples from beyond the local neighbourhood. That is the trade, and "
        "the measurements do not make it: read strictly off either indicator, k would "
        "fall between %d and %d."
        % (k, k - pure_k, pure_k,
           ("" if not peak else
            " and %d beyond the top of the slope band at %d"
            % (k - peak["band_within_95pct_of_peak"][1],
               peak["band_within_95pct_of_peak"][1])),
           float(n_sel) / args.min_cohort, args.min_cohort, n_sel, pure_k,
           min(pure_k, upper or pure_k), max(pure_k, upper or pure_k)))

    if bd.get("median_gap_within_50_ranks"):
        tail += (
            " The boundary is also near-degenerate: d%d is %.6f and d%d is %.6f, a "
            "gap of %.2e against a local median gap of %.2e, so which samples fall "
            "just inside the cohort is set by the choice of an integer k rather than "
            "by any separation in the data. Read "
            "selected_cohort_superpopulation_counts with that in mind."
            % (k, bd["d_k"], k + 1, bd["d_k_plus_1"], bd["gap_at_boundary"],
               bd["median_gap_within_50_ranks"]))

    return " ".join([head, body, tail])


def build_artifact(args, projection, s1, cohort, parity, score_stats,
                   scoring_sha256, plink2_version):
    usable = not cohort["refusals"]
    reason = ("a usable reference distribution was built"
              if usable else "; ".join(cohort["refusals"]))

    rb = projection.get("reference_build") or {}
    nd = cohort["neighbour_distances"]
    curve = cohort["distance_curve"]

    # Decided once, up here, because both cohort_construction_rule.why_this_k and
    # cohort_fit_note are written from it. The two contradicted each other in an
    # earlier build -- why_this_k asserted a centrality effect that cohort_fit_note's
    # own numbers no longer showed -- and sharing one verdict is what stops that
    # recurring.
    verdict, assessment = _fit_assessment(cohort)

    eigenvals = cohort.get("eigenvals")
    if not (eigenvals and eigenvals[-1]):
        eigenvals = None
    ms = cohort.get("metric_sensitivity") if eigenvals else None
    ms_n_top = None
    if ms:
        ms_n_top = next((v for k_, v in ms.items()
                         if k_.startswith("n_leading_pcs_holding_")), None)
    if ms_n_top is None:
        ms = None

    art = collections.OrderedDict()
    art["usable"] = usable
    art["reason"] = reason
    art["generated_utc"] = datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    art["provider"] = "build_reference.py (independent reference distribution provider)"

    # Read from the scoring file's own '#' header. NEVER asserted: a hardcoded model id
    # would silently mislabel this artifact the moment a different scoring file was
    # passed, and a confidently wrong label on a clinical artifact is worse than a
    # missing one. A None here means the header did not carry the field.
    hdr = s1.get("scoring_file_header") or {}
    art["trait"] = {
        "id": hdr.get("trait_efo"),
        "label": hdr.get("trait_reported"),
        "mapped": hdr.get("trait_mapped"),
        "read_from": "the scoring file's own # header, not asserted by this tool",
    }
    art["model"] = {
        "pgs_id": hdr.get("pgs_id"),
        "pgs_name": hdr.get("pgs_name"),
        "hmPOS_build": hdr.get("HmPOS_build"),
        "hmPOS_date": hdr.get("HmPOS_date"),
        "genome_build_declared": hdr.get("genome_build"),
        "variants_number_declared": hdr.get("variants_number"),
        "weight_type": hdr.get("weight_type"),
        "sha256": scoring_sha256,
        "coordinate_columns_used": "%s / %s" % (HM_CHR_COL, HM_POS_COL),
        "effect_weight_column_used": args.effect_weight_col,
        "note": "#genome_build describes the authors' ORIGINAL build and is often "
                "different from the coordinates used here. The hm_chr / hm_pos columns "
                "this tool reads are in #HmPOS_build. Both are recorded above so the "
                "difference is visible rather than resolved silently.",
    }

    art["source"] = ("1000 Genomes phase 3 (Beagle-distributed v5a reference panel), "
                     "2,504 samples, GRCh37")
    art["version_string"] = "1kg.phase3.v5a.b37"
    art["version_string_note"] = (
        "1000 Genomes release identifier, supplied as the equivalent of the All of "
        "Us CDR string the contract names. The PCA sample set derives from "
        "integrated_call_samples_v3.20130502.")

    art["ancestry_grouping_definition"] = {
        "grouping": "none -- continuous position in PC space, no discrete label",
        "method": ("20-PC projection of the patient into a 1000 Genomes phase 3 PCA "
                   "(%s markers after pruning), then k-nearest-neighbour selection by "
                   "Euclidean distance in that 20-PC space. No population label is "
                   "assigned to the patient by this provider."
                   % rb.get("n_markers_after_pruning")),
        "pc_vector_used": ("patient_pcs_adp_corrected" if args.pc_source == "adp"
                           else "patient_pcs_simple_projection"),
        "pcs_used": projection.get("pcs_used"),
        "distance_metric_note": (
            ("Euclidean distance over %d PCs weights every PC equally: the plink2 "
             "eigenvector columns are all normalised to the same variance, so a PC "
             "explaining almost nothing counts as much as one separating continents. "
             "The %d leading PCs hold %.0f%% of the eigenvalue mass, and PC1 is %.0fx "
             "PC%d -- yet inside the selected cohort those leading PCs do almost no "
             "ordering work: PC%d and above carry %.1f%% of the squared distance "
             "there, against %.1f%% panel-wide. At cohort scale the neighbours are "
             "already alike on the PCs that carry ancestry, so what separates them is "
             "the rest. This is a pre-existing property of the rule, unchanged by the "
             "upstream scale fix."
             % (projection.get("pcs_used"), ms_n_top, 100.0 * ms["leading_pc_eigenvalue_share"],
                eigenvals[0] / eigenvals[-1], projection.get("pcs_used"),
                ms_n_top + 1,
                100.0 * ms["trailing_pc_share_of_squared_distance_within_cohort"],
                100.0 * ms["trailing_pc_share_of_squared_distance_panel_wide"]))
            if ms else
            ("Euclidean distance over %d PCs weights every PC equally. The reference "
             "eigenvalues were not available to quantify how unevenly the variance is "
             "spread across them." % (projection.get("pcs_used") or 0))),
        "cohort_composition_note": (
            ("The selected cohort is not superpopulation-homogeneous: %s. That is "
             "what a distance rule produces, not a defect in it -- the rule takes "
             "whoever is nearest and never promised a single group. But the mixture "
             "is an artifact of the metric rather than a finding about the patient, "
             "and that is measured, not assumed: re-running the same k with each PC "
             "weighted by the variance it explains gives %s, and %d of the %d members "
             "from outside %s survive. A count that survives both weightings would be "
             "a fact about where the patient sits; one that does not is a fact about "
             "how the distance is defined. See distance_metric_note."
             % (counts_text(cohort["superpopulation_counts"]),
                counts_text(ms["eigenvalue_weighted_recheck"]["superpopulation_counts"]),
                ms["eigenvalue_weighted_recheck"]["n_of_those_still_selected_under_eigenvalue_weighting"],
                ms["eigenvalue_weighted_recheck"]["n_outside_source_group_under_equal_weighting"],
                cohort["cohort_source_group"]))
            if ms else
            ("The selected cohort is not superpopulation-homogeneous: %s. That is "
             "what a distance rule produces, not a defect in it -- the rule takes "
             "whoever is nearest and never promised a single group. The full "
             "composition is reported in cohort_construction_rule; nothing about it "
             "is inferred here." % counts_text(cohort["superpopulation_counts"]))),
        "metric_sensitivity": ms,
        "why_no_label": (
            "The upstream ancestry stage DID assign a group for this patient, and the "
            "interpret request carried it here. This provider declines to use it. "
            "Selection is by distance in PC space alone: no sample is included or "
            "excluded by its panel label, and this artifact attaches no label to the "
            "patient. The contract's INT-5 / 17.2 fork permits a distance rule in "
            "place of a discrete label; this is that. The two do not produce the same "
            "cohort -- a label rule draws from every panel member carrying the label "
            "however far away they sit, while this rule takes the nearest %d and lets "
            "the labels fall where they may -- which is exactly why the resulting "
            "neighbour counts must not be read back as a label. Two measured facts "
            "make them particularly unsafe to read that way. Members from outside the "
            "largest group enter only in the outer part of the cohort -- the first at "
            "rank %s of %d -- and the distances there are near-degenerate, so moving "
            "k by a handful of samples changes the mixture (see "
            "cohort_construction_rule.why_this_k). And the ordering that puts them "
            "there is dominated by low-variance PCs, per distance_metric_note and the "
            "recheck in metric_sensitivity. The mixture is a fact about the metric "
            "and about where an integer k cuts a near-continuous ordering, not a "
            "finding about this patient's ancestry. One place the panel's labels DO "
            "enter is the choice of k itself, which was sized partly on cohort "
            "composition (see cohort_construction_rule.why_this_k). That sets a "
            "boundary, not a label: no sample is included or excluded by its label, "
            "no label is attached to the patient, and the selection rule remains "
            "distance. It is recorded here rather than left implicit."
            % (cohort["n_selected"],
               cohort["curve_diagnostics"]["first_rank_of_a_second_superpopulation"],
               cohort["n_selected"])),
        "k": cohort["k_requested"],
        "selected_cohort_superpopulation_counts": cohort["superpopulation_counts"],
        "versioned_record_note": (
            "INT-5 requires the grouping to travel with the version, and "
            "reference_adapter.py's version_capture records this block as that "
            "grouping. A distance rule is not fully described by its method string: "
            "the same rule at a different k, or over a differently weighted PC space, "
            "selects a different cohort and yields a different percentile. k, the "
            "resulting composition, and the weighting convention are therefore "
            "recorded here rather than only in cohort_construction_rule, so the "
            "versioned record is sufficient on its own."),
        "upstream_deviation_note_verbatim": rb.get("deviation_note"),
    }

    art["cohort_construction_rule"] = {
        "rule": ("k-nearest neighbours in 20-PC space by Euclidean distance, k=%d, "
                 "selected from all %d reference samples"
                 % (cohort["k_requested"], cohort["n_available"])),
        "k": cohort["k_requested"],
        "min_cohort_threshold": args.min_cohort,
        "max_kth_distance_threshold": args.max_kth_distance,
        "why_this_k": _why_this_k(cohort, args, verdict),
        "neighbour_distances": {"d1": nd["d1"], "d10": nd["d10"],
                                "d50": nd["d50"],
                                "d%d" % nd["k_for_dk"]: nd["dk"]},
        "distance_curve": curve,
        "distance_curve_diagnostics": cohort["curve_diagnostics"],
        "cohort_composition_at_candidate_k": cohort["cohort_snapshots"],
        "selected_cohort_population_counts": cohort["population_counts"],
        "selected_cohort_superpopulation_counts": cohort["superpopulation_counts"],
    }

    art["n_participants"] = score_stats.get("n_scored", 0)
    art["too_few_threshold_used"] = args.min_cohort
    art["too_few_threshold_note"] = (
        "With %d reference samples always available and k=%d, this count-based "
        "threshold cannot bind; it fires only on misconfiguration. The live "
        "refusal lever for similarity is --max-kth-distance, set to %s for this "
        "run." % (cohort["n_available"], cohort["k_requested"],
                  args.max_kth_distance if args.max_kth_distance is not None
                  else "no check"))

    art["assay_layer"] = {
        "reference": "1000 Genomes phase 3 whole-genome sequencing, hard-called genotypes",
        "patient": "consumer genotyping array, imputed",
        "assay_matched": False,
        "statement": (
            "These are NOT assay-matched. The reference is WGS-derived; the "
            "patient's data is array-derived and imputed. INT-4 argues for an "
            "array-derived reference specifically because an array-derived score "
            "placed against a WGS reference inherits the R1 imputed/partial-"
            "vs-complete bias. 1000 Genomes has no comparable array layer, so that "
            "protection is not available here and the mismatch is disclosed rather "
            "than corrected. See also imputation_shrinkage_note."),
    }

    art["scoring_parity"] = "same-resolved-set"
    art["scoring_parity_detail"] = {
        "statement": ("Reference individuals were scored over the same positions "
                      "the patient resolved, not over the full model."),
        "n_model_variants": s1["n_scoring_file_rows"],
        "n_patient_resolved": s1["n_resolved_positions"],
        "n_variants_in_score_file": s1["n_variants_emitted"],
        "sum_abs_beta": round(s1["sum_abs_beta"], 6),
        "sum_abs_beta_verified_against": s1["expected_sum_abs_beta"],
        "n_present_in_reference_vcf": parity["n_present_in_vcf"],
        "n_missing_from_reference_vcf": parity["n_missing_from_vcf"],
        "n_allele_mismatch": parity["n_allele_mismatch"],
        "n_allele_mismatch_strand_flip_only": parity["n_allele_mismatch_strand_flip_only"],
        "n_variants_plink2_processed": score_stats.get("n_processed"),
        "sum_abs_beta_not_scored": round(parity["sum_abs_beta_not_scored"], 8),
        "parity_shortfall": (s1["n_variants_emitted"] - (score_stats.get("n_processed") or 0)),
        "scoring_tool": plink2_version,
        "score_convention": (
            "distribution reports SCORE1_SUM, the dosage-weighted sum of effect "
            "weights (the conventional PRS raw score). The mean-of-alleles form "
            "(SCORE1_AVG) is reported alongside as distribution_score_avg so a "
            "convention mismatch with the patient's raw_score is detectable rather "
            "than silent."),
    }

    art["multiallelic_handling"] = {
        "n_positions_with_multiple_reference_records": parity["n_multi_record_positions"],
        "decision_rule": (
            "Reference variant IDs were set to chrom:pos:ref:alt rather than "
            "chrom:pos, so co-located records stay individually addressable and no "
            "tool silently deduplicates them. At each such position the record "
            "whose {REF,ALT} exactly equals the model's {effect_allele, "
            "other_allele} was selected. A position that did not resolve to exactly "
            "one candidate would have been dropped and counted, not guessed."),
        "n_resolved_by_exact_allele_match": parity["n_multi_resolved_by_exact_allele_match"],
        "n_resolved_by_effect_allele_only": parity["n_multi_resolved_by_effect_allele_only"],
        "n_dropped_unresolvable": parity["n_multi_dropped_unresolvable"],
        "decisions": parity["multi_record_decisions"],
    }

    art["imputation_shrinkage_note"] = {
        "statement": (
            "%.4f of the patient's %.4f resolved |beta| (%.0f%%) comes from imputed "
            "rather than directly typed positions. Imputation pulls those dosages "
            "toward the reference panel's allele frequencies, which biases the "
            "patient's percentile toward the centre of the distribution."
            % (s1["sum_abs_beta_imputed"], s1["sum_abs_beta"],
               100.0 * s1["imputed_weight_fraction"])),
        "sum_abs_beta_total": round(s1["sum_abs_beta"], 4),
        "sum_abs_beta_imputed": round(s1["sum_abs_beta_imputed"], 4),
        "sum_abs_beta_typed": round(s1["sum_abs_beta_typed"], 4),
        "imputed_weight_fraction": round(s1["imputed_weight_fraction"], 4),
        "n_variants_imputed": s1["n_variants_imputed"],
        "n_variants_typed": s1["n_variants_typed"],
        "direction": "toward the centre of the distribution",
        "magnitude": "not measured",
        "status": "This is a disclosure, not a correction. No adjustment was applied.",
    }

    cen = cohort["centrality"]
    effect_found = verdict != "no centrality effect detected"
    src = cohort["cohort_source_group"]

    art["cohort_fit_note"] = {
        "finding": verdict,
        "statement": (
            ("The patient's PC coordinates are more central than %.1f%% of the %d "
             "reference samples: distance to the panel centroid is %.6f, against a "
             "panel median of %.6f and a 5th percentile of %.6f. The cohort was "
             "therefore selected around a point sitting near the centre of the "
             "reference cloud, which makes the selected neighbours appear closer "
             "than the patient's true genetic neighbourhood warrants."
             if effect_found else
             "The patient's PC coordinates sit at an ordinary distance from the "
             "panel centroid: %.6f, with %.1f%% of the %d reference samples closer "
             "to the centroid than the patient, against a panel median of %.6f and "
             "a 5th percentile of %.6f. The cohort was not selected around an "
             "unusually central point, and no centrality effect is disclosed here. "
             "See `finding` and `shrinkage_vs_fit_assessment` for what was measured "
             "to reach that.")
            % ((cen["pct_of_panel_closer_to_centroid"], cohort["n_available"],
                cen["patient_distance_to_centroid"],
                cen["panel_median_distance_to_centroid"],
                cen["panel_p5_distance_to_centroid"]) if effect_found else
               (cen["patient_distance_to_centroid"],
                cen["pct_of_panel_closer_to_centroid"], cohort["n_available"],
                cen["panel_median_distance_to_centroid"],
                cen["panel_p5_distance_to_centroid"]))),
        "shrinkage_vs_fit_assessment": assessment,
        "centrality_measurement": cen,
        "curve_shape": cohort["curve_shape"],
        "distance_curve": curve,
        "panel_baseline": cohort["reference_baseline"],
        "panel_baseline_by_superpopulation": cohort["reference_baseline_by_superpopulation"],
        "panel_baseline_definition": (
            "For each reference sample, the distance to its OWN r-th nearest "
            "neighbour within the panel, excluding itself. Median and 5th/95th "
            "percentile across all %d samples. This is what 'close' looks like for "
            "someone who genuinely belongs to this panel."
            % cohort["n_available"]),
        "patient_vs_baseline": cohort["patient_vs_baseline"],
        "patient_vs_baseline_by_superpopulation": cohort["patient_vs_baseline_by_superpopulation"],
        "baseline_frame_note": (
            "The all-panel percentiles and the per-superpopulation percentiles "
            "answer different questions and can disagree sharply. The panel mixes "
            "groups whose own neighbour distances differ by nearly 2x, so a patient "
            "near a mid-density group drifts across the all-panel percentiles "
            "without anything about that patient changing. The per-group figures "
            "are the ones the assessment reads; the all-panel figures are kept "
            "because they are what the drift shows up in."),
        "cohort_source_group": src,
        "cohort_source_group_definition": (
            "The largest superpopulation among the selected neighbours (%s, %d of "
            "%d). It names which reference samples the cohort is built from, so the "
            "fit assessment can be read against the right baseline. It is NOT a "
            "group assignment for the patient -- see "
            "ancestry_grouping_definition.why_no_label."
            % (src, cohort["superpopulation_counts"].get(src, 0),
               cohort["n_selected"])),
        "direction": ("toward the centre of the distribution" if effect_found
                      else "no directional effect detected"),
        "magnitude": ("not measured" if effect_found else
                      "measured and null; see centrality_measurement and curve_shape"),
        "status": (("This is a disclosure, not a correction. No adjustment was "
                    "applied to the patient's coordinates or to the cohort.")
                   if effect_found else
                   ("No disclosure is made. No adjustment was applied to the "
                    "patient's coordinates or to the cohort, and none is called "
                    "for by these measurements. The measurements are reported in "
                    "full so the null result is checkable rather than asserted.")),
        "combined_effect_with_imputation": (
            ("This bias and imputation_shrinkage_note push the same way. Imputation "
             "pulls the patient's SCORE toward the panel mean; projection shrinkage "
             "pulls the COHORT SELECTION toward the panel centre. They act on "
             "different quantities by different mechanisms, but both move the "
             "resulting percentile toward the 50th. Neither is corrected here and "
             "the combined magnitude is not measured.")
            if effect_found else
            ("Nothing here compounds with imputation_shrinkage_note. That note "
             "stands on its own measurement and is unaffected by this one: it "
             "concerns the patient's SCORE, this concerns COHORT SELECTION, and "
             "only the score effect is present. Any downstream text that lists a "
             "projection-shrinkage caveat alongside the imputation caveat is "
             "carrying a caveat this artifact does not support.")),
    }

    art["distribution"] = summarise(score_stats.get("scores_sum"))
    art["distribution_score_avg"] = summarise(score_stats.get("scores_avg"))
    art["distribution_note"] = (
        "Raw scores, computed on the patient's resolved variant set. "
        "distribution is SCORE1_SUM; distribution_score_avg is SCORE1_AVG."
        if usable else
        "No distribution was built. The provider refused under INT-2; every "
        "quantile is null rather than zero, because a zero would be a fabricated "
        "value. See `reason`.")

    art["self_check_pc_space"] = cohort.get("selfcheck")
    return art


# ----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Build a PRS reference distribution artifact (INT-1 provider).")
    ap.add_argument("--inputs", default="inputs", help="input directory")
    ap.add_argument("--workdir", default="work", help="intermediate files")
    ap.add_argument("--out", default="reference_distribution.json", help="artifact path")
    ap.add_argument("--dosages", required=True,
                    help="dosages.tsv from match_variants.py (chrom/pos/source/status)")
    ap.add_argument("--scoring", required=True,
                    help="the PGS Catalog scoring file (gzipped) in the patient's build")
    ap.add_argument("--ref-vcf", required=True,
                    help="reference panel VCF the cohort is scored from")
    ap.add_argument("--effect-weight-col", required=True,
                    help="the weight column, read off the scoring file's own header. "
                         "Not assumed: only six columns are universal across PGS Catalog "
                         "files and this is not one of them.")
    ap.add_argument("--k", type=int, required=True,
                    help="number of nearest reference samples to select. No default: the "
                         "cohort size is a judgment about how much of the neighbourhood "
                         "to include, and it is not derived from anything.")
    ap.add_argument("--min-cohort", type=int, required=True,
                    help="refuse below this cohort size. No default, for the same reason.")
    ap.add_argument("--max-kth-distance", type=float, default=None,
                    help="refuse if the kth neighbour is further than this "
                         "(default: no check)")
    ap.add_argument("--pc-source", choices=["adp", "simple"], default="adp",
                    help="which patient PC vector from projection.json (default adp)")
    ap.add_argument("--expected-abs-beta", type=float, default=None,
                    help="OPTIONAL regression guard: sum of |BETA| over the join, taken "
                         "from a prior run of THIS patient and model. No default -- the "
                         "value belongs to a run, not to the tool. Omitted: not checked, "
                         "and the artifact says so.")
    ap.add_argument("--expected-imputed-abs-beta", type=float, default=None,
                    help="OPTIONAL cross-check on the imputed share of sum |BETA|. "
                         "Reported, never enforced.")
    ap.add_argument("--plink2", default="./plink2", help="path to the plink2 binary")
    args = ap.parse_args()

    # k <= 0 would make order[:k] select n_available+k samples (k<0) or an empty
    # cohort whose distances are None (k==0), which then crashes the summary
    # print. Neither is a meaningful request; reject at the boundary.
    global WEIGHT_COL
    WEIGHT_COL = args.effect_weight_col

    if args.k < 1:
        die("--k must be >= 1 (got %d)" % args.k)
    if args.min_cohort < 1:
        die("--min-cohort must be >= 1 (got %d)" % args.min_cohort)

    I = lambda name: os.path.join(args.inputs, name)
    dosages = args.dosages
    scoring = args.scoring
    projection_path = I("projection.json")
    eigenvec = I("ref_pca.eigenvec")
    eigenval = I("ref_pca.eigenval")
    panel = I("integrated_call_samples_v3.20130502.ALL.panel")
    score_input = os.path.join(args.workdir, "plink_score_input.tsv")

    for p in (dosages, scoring, projection_path, eigenvec, panel):
        if not os.path.exists(p):
            die("required input missing: %s" % p)

    with open(projection_path) as fh:
        projection = json.load(fh)

    # ---- STEP 1 ----------------------------------------------------------
    print("=" * 72)
    print("STEP 1  regenerate the score input")
    print("=" * 72)
    resolved, n_dosage_rows, status_counts = read_resolved_positions(dosages)
    print("  dosage data rows            : %d" % n_dosage_rows)
    for st, n in sorted(status_counts.items()):
        print("    %-16s        : %d" % (st, n))

    s1 = build_score_input(scoring, resolved, score_input, args.expected_abs_beta,
                           args.expected_imputed_abs_beta)
    print("  scoring file rows           : %d" % s1["n_scoring_file_rows"])
    print("  variants emitted            : %d  (typed %d, imputed %d)"
          % (s1["n_variants_emitted"], s1["n_variants_typed"], s1["n_variants_imputed"]))
    print("  resolved not in scoring file: %d" % s1["n_resolved_not_in_scoring_file"])
    if s1["expected_sum_abs_beta"] is None:
        print("  sum |BETA|                  : %.4f   (NOT verified, no target supplied)"
              % s1["sum_abs_beta"])
    else:
        print("  sum |BETA|                  : %.4f   (expected %.4f)  OK"
              % (s1["sum_abs_beta"], s1["expected_sum_abs_beta"]))
    print("    of which typed positions  : %.4f" % s1["sum_abs_beta_typed"])
    print("    of which imputed positions: %.4f  (%.1f%%)  [%s]"
          % (s1["sum_abs_beta_imputed"], 100.0 * s1["imputed_weight_fraction"],
             s1["imputed_split_verification"]))
    print("  score file                  : %s" % score_input)

    # ---- STEP 2 ----------------------------------------------------------
    print()
    print("=" * 72)
    print("STEP 2  select the reference cohort")
    print("=" * 72)
    n_pcs = projection.get("pcs_used")
    if not isinstance(n_pcs, int) or n_pcs < 1:
        die("projection.json has no usable pcs_used value")

    vec_field = {"adp": "patient_pcs_adp_corrected",
                 "simple": "patient_pcs_simple_projection"}
    patient_vecs = {}
    for name, field in vec_field.items():
        v = projection.get(field)
        if v is None:
            die("projection.json lacks %s" % field)
        if len(v) < n_pcs:
            die("%s has %d values but pcs_used is %d" % (field, len(v), n_pcs))
        patient_vecs[name] = np.asarray(v[:n_pcs], dtype=float)

    iids, coords = load_eigenvec(eigenvec, n_pcs)
    eigenvals = load_eigenval(eigenval, n_pcs)
    labels = load_panel(panel)
    print("  reference samples           : %d" % len(iids))
    print("  PCs used                    : %d" % n_pcs)
    missing_labels = [s for s in iids if s not in labels]
    if missing_labels:
        die("%d eigenvec samples have no panel label (e.g. %s)"
            % (len(missing_labels), missing_labels[:3]))

    expected_n = (projection.get("reference_build") or {}).get("n_reference_samples")
    if expected_n is not None and len(iids) != expected_n:
        die("eigenvec has %d samples but projection.json records %d for this build"
            % (len(iids), expected_n))

    # self-check: are we in the same PC space the agent used?
    check = neighbour_selfcheck(iids, coords, labels, patient_vecs, projection)
    if check.get("performed"):
        print("  self-check vs agent's recorded k=%d nearest:" % check["k_checked"])
        print("    recorded : %s" % json.dumps(check["recorded_by_population"], sort_keys=True))
        for name in ("adp", "simple"):
            r = check["per_vector"][name]
            print("    %-7s: %s  -> %s%s"
                  % (name, json.dumps(r["by_population"], sort_keys=True),
                     "MATCH" if r["matches"] else "no match",
                     "  [tie at the k-th boundary]" if r["boundary_tie_at_k"] else ""))
        if not check["any_vector_reproduces_agent_result"]:
            die("neither patient vector reproduces the agent's recorded k=%d "
                "neighbour result. The eigenvec and the projection are not in the "
                "same PC space, so every distance below would be meaningless. "
                "Stopping rather than reconciling." % check["k_checked"])
        if not check["per_vector"][args.pc_source]["matches"]:
            print("    NOTE: --pc-source %s does NOT reproduce the agent's result "
                  "(the other vector does). Proceeding as instructed; this is "
                  "recorded in the artifact." % args.pc_source)

    cohort = select_cohort(iids, coords, labels, patient_vecs[args.pc_source],
                           args.k, args.min_cohort, args.max_kth_distance,
                           eigenvals=eigenvals)
    nd = cohort["neighbour_distances"]
    print("  pc-source                   : %s" % args.pc_source)
    print("  cohort scored in step 3     : %d  (--k %d, available %d)"
          % (cohort["n_selected"], cohort["k_requested"], cohort["n_available"]))

    # --- the distance curve, and the baseline that gives it scale -----------
    print()
    print("  DISTANCE CURVE -- reported in full regardless of --k.")
    print("  Baseline: each reference sample's distance to its OWN r-th nearest")
    print("  neighbour within the panel (median, and 5th-95th percentile).")
    print()
    base = reference_baseline(coords, BASELINE_RANKS)
    base_stats = {}
    for r in BASELINE_RANKS:
        a = base[r][np.isfinite(base[r])]
        base_stats[r] = {
            "median": float(np.median(a)),
            "p5": float(np.percentile(a, 5)),
            "p95": float(np.percentile(a, 95)),
        }
    cohort["reference_baseline"] = base_stats

    # Per-superpopulation baselines, AND the patient's percentile against each.
    # The all-panel median mixes groups whose own neighbour distances differ by
    # nearly 2x, and that mixing alone drifts a patient's all-panel percentile
    # across ranks with nothing about the patient changing. Separating that
    # artifact from a real centrality effect is the whole job of cohort_fit_note,
    # so the per-group percentiles -- not just the medians -- belong in the
    # artifact. Emitting all five groups is deliberate: the assessment can then
    # cite the group the cohort is drawn from without this tool ever choosing a
    # label for the patient.
    sup_arr = np.array([labels[i][1] for i in iids])
    base_by_sup = collections.OrderedDict()
    pct_by_sup = collections.OrderedDict()
    for g in sorted(set(sup_arr)):
        m = sup_arr == g
        base_by_sup[g] = collections.OrderedDict(
            ("d%d" % r, float(np.median(base[r][m][np.isfinite(base[r][m])])))
            for r in BASELINE_RANKS)
        pct_by_sup[g] = collections.OrderedDict(
            ("d%d" % r, percentile_of(cohort["distance_curve"][r], base[r][m]))
            for r in BASELINE_RANKS if r in cohort["distance_curve"])
    cohort["reference_baseline_by_superpopulation"] = base_by_sup
    cohort["patient_vs_baseline_by_superpopulation"] = pct_by_sup

    # Scale-free curve shape: how much wider the neighbourhood is at rank 300 than
    # at rank 1. A group being denser or sparser than the panel cannot move this,
    # which is what makes it the cleanest test of whether the patient's curve is
    # genuinely flatter than that of the people the cohort is drawn from.
    SHAPE_LO, SHAPE_HI = 1, 300
    if SHAPE_HI in cohort["distance_curve"] and SHAPE_HI in base:
        shape_pat = (cohort["distance_curve"][SHAPE_HI]
                     / cohort["distance_curve"][SHAPE_LO])
        shape_ref = base[SHAPE_HI] / base[SHAPE_LO]
        cohort["curve_shape"] = {
            "definition": ("d%d / d%d -- the width of the neighbourhood at rank %d "
                           "relative to rank %d, which is scale-free"
                           % (SHAPE_HI, SHAPE_LO, SHAPE_HI, SHAPE_LO)),
            "patient": float(shape_pat),
            "panel_median": float(np.median(shape_ref)),
            "patient_percentile_vs_panel": percentile_of(shape_pat, shape_ref),
            "by_superpopulation": collections.OrderedDict(
                (g, {"median": float(np.median(shape_ref[sup_arr == g])),
                     "patient_percentile": percentile_of(shape_pat,
                                                         shape_ref[sup_arr == g])})
                for g in sorted(set(sup_arr))),
        }
    else:
        cohort["curve_shape"] = {
            "definition": "d%d / d%d" % (SHAPE_HI, SHAPE_LO),
            "patient": None, "panel_median": None,
            "patient_percentile_vs_panel": None,
            "by_superpopulation": {},
            "note": "not computed -- fewer than %d reference samples available"
                    % SHAPE_HI,
        }

    print("    rank        patient    panel median    panel p5-p95        patient pctile")
    print("    " + "-" * 74)
    patient_vs_baseline = {}
    for r, dist in cohort["distance_curve"].items():
        if r in base_stats:
            b = base_stats[r]
            pct = percentile_of(dist, base[r])
            patient_vs_baseline[r] = {"patient": dist, "percentile_vs_panel": pct}
            print("    d%-9d %-10.6f %-15.6f %-19s %.1f%%"
                  % (r, dist, b["median"],
                     "%.6f - %.6f" % (b["p5"], b["p95"]), pct))
        else:
            print("    d%-9d %-10.6f %-15s %-19s %s"
                  % (r, dist, "-", "-", "-"))
    cohort["patient_vs_baseline"] = patient_vs_baseline

    # Centrality: is the patient sitting at the middle of the cloud? This is what
    # distinguishes "well matched" from "projection-shrunk", and it feeds
    # cohort_fit_note. Reported against the panel AND against the group the cohort
    # is drawn from, because the panel-wide figure alone cannot tell an unusually
    # central patient from one sitting normally inside a group that is itself
    # nearer the centre than average.
    centroid = coords.mean(axis=0)
    r_ref = np.linalg.norm(coords - centroid, axis=1)
    r_pat = float(np.linalg.norm(patient_vecs[args.pc_source] - centroid))
    src_group = cohort["cohort_source_group"]
    cohort["centrality"] = {
        "patient_distance_to_centroid": r_pat,
        "pct_of_panel_closer_to_centroid": float(100.0 * np.mean(r_ref < r_pat)),
        "cohort_source_group": src_group,
        "pct_of_source_group_closer_to_centroid": (
            float(100.0 * np.mean(r_ref[sup_arr == src_group] < r_pat))
            if src_group is not None and (sup_arr == src_group).any() else None),
        "source_group_median_distance_to_centroid": (
            float(np.median(r_ref[sup_arr == src_group]))
            if src_group is not None and (sup_arr == src_group).any() else None),
        "panel_median_distance_to_centroid": float(np.median(r_ref)),
        "panel_p5_distance_to_centroid": float(np.percentile(r_ref, 5)),
        "panel_p95_distance_to_centroid": float(np.percentile(r_ref, 95)),
        "patient_distance_to_centroid_other_vector": float(np.linalg.norm(
            patient_vecs["simple" if args.pc_source == "adp" else "adp"] - centroid)),
        "recorded_projection_shrinkage_ratio": projection.get(
            "projection_shrinkage_ratio_for_this_query"),
        "recorded_projection_shrinkage_ratio_note": (
            "Copied from projection.json for provenance. It is NOT read as evidence "
            "of a centrality effect here, and nothing in this artifact is derived "
            "from it. Upstream states it compares two estimators with no ground "
            "truth and so cannot identify which is biased or in which direction; "
            "the direction question is settled by the measurements in this block "
            "instead. Note also that the vector used here (%s) is the corrected "
            "estimator, not the one the ANC-6 shrinkage claim is about."
            % ("patient_pcs_adp_corrected" if args.pc_source == "adp"
               else "patient_pcs_simple_projection")),
    }
    cohort["eigenvals"] = eigenvals
    cohort["selfcheck"] = check
    print()
    print("    patient pctile = %% of reference samples whose own r-th neighbour is")
    print("    CLOSER than the patient's. 50%% means the patient sits like a typical")
    print("    panel member at that rank; high means the panel fits them worse.")
    print("    (Patient distances are over 2504 samples, each reference sample's own")
    print("    over the other 2503 -- a negligible asymmetry, noted for honesty.)")

    # The same profile against the group the cohort is drawn from. The all-panel
    # column above mixes groups of very different density and drifts on its own;
    # this is the column the fit verdict is actually read off.
    src_g = cohort["cohort_source_group"]
    if src_g:
        print()
        print("    vs %s alone (%d of the %d selected neighbours) -- the baseline the"
              % (src_g, cohort["superpopulation_counts"].get(src_g, 0),
                 cohort["n_selected"]))
        print("    cohort is drawn from, and the one the fit verdict is read off:")
        print("      " + "  ".join(
            "d%d %.1f%%" % (r, pct_by_sup[src_g]["d%d" % r])
            for r in BASELINE_RANKS if pct_by_sup[src_g].get("d%d" % r) is not None))
        cs = cohort["curve_shape"]
        if cs.get("patient") is not None:
            print("      curve shape d300/d1 %.3f vs %s median %.3f  -> %.1f%% of %s"
                  % (cs["patient"], src_g,
                     cs["by_superpopulation"][src_g]["median"],
                     cs["by_superpopulation"][src_g]["patient_percentile"], src_g))
        print("      centrality: %.1f%% of the panel and %.1f%% of %s are closer to"
              % (cohort["centrality"]["pct_of_panel_closer_to_centroid"],
                 cohort["centrality"]["pct_of_source_group_closer_to_centroid"],
                 src_g))
        print("                  the centroid than the patient")

    # --- composition at candidate cohort boundaries -------------------------
    print()
    print("  COHORT COMPOSITION at candidate boundaries:")
    for kk, snap in cohort["cohort_snapshots"].items():
        print("    k=%-5d superpop %s" % (kk, json.dumps(snap["superpopulation_counts"], sort_keys=True)))
        print("            pop      %s" % json.dumps(snap["population_counts"], sort_keys=True))
    print()
    print("  Cohort actually scored (--k %d): superpop %s"
          % (cohort["k_requested"], json.dumps(cohort["superpopulation_counts"], sort_keys=True)))
    print("                                  pop      %s"
          % json.dumps(cohort["population_counts"], sort_keys=True))
    print("  d1=%.6f  d10=%.6f  d50=%.6f  d%d=%.6f"
          % (nd["d1"], nd["d10"], nd["d50"], nd["k_for_dk"], nd["dk"]))
    if cohort["refusals"]:
        print("  INT-2 REFUSAL               : %s" % cohort["refusals"][0])

    # ---- STEP 3 ----------------------------------------------------------
    scoring_sha256 = sha256_of(scoring)
    plink2_version = run_plink2(args.plink2, ["--version"], "version check").strip()
    score_stats = {}
    parity = None

    if cohort["refusals"]:
        # INT-2 refusal. Do not score: there is no defensible cohort. Still write
        # a complete, valid artifact and exit 0 -- refusing IS the correct outcome.
        print()
        print("=" * 72)
        print("STEP 3  SKIPPED -- INT-2 refusal, no cohort to score")
        print("=" * 72)
        parity = {k: 0 for k in (
            "n_present_in_vcf", "n_missing_from_vcf", "n_multi_record_positions",
            "n_multi_resolved_by_exact_allele_match",
            "n_multi_resolved_by_effect_allele_only", "n_multi_dropped_unresolvable",
            "n_allele_mismatch", "n_allele_mismatch_strand_flip_only")}
        parity["sum_abs_beta_not_scored"] = 0.0
        parity["multi_record_decisions"] = []
    else:
        print()
        print("=" * 72)
        print("STEP 3  score the cohort on the patient's resolved set")
        print("=" * 72)
        ref_vcf = args.ref_vcf
        if not os.path.exists(ref_vcf):
            die("reference VCF missing: %s" % ref_vcf)
        ref_prefix = os.path.join(args.workdir, "ref")
        import_reference(args.plink2, ref_vcf, ref_prefix)

        pvar_map, n_pvar = read_pvar(ref_prefix + ".pvar")
        print("  reference records loaded    : %d at %d distinct positions"
              % (n_pvar, len(pvar_map)))

        resolved_score = os.path.join(args.workdir, "plink_score_input.resolved.tsv")
        parity = resolve_score_variants(score_input, pvar_map, scoring, resolved_score)

        print("  PARITY CHECK (patient's resolved set vs the reference panel)")
        print("    emitted in step 1         : %d" % parity["n_emitted"])
        print("    present in reference VCF  : %d" % parity["n_present_in_vcf"])
        print("    MISSING from reference VCF: %d" % parity["n_missing_from_vcf"])
        if parity["missing_examples"]:
            print("      e.g. %s" % ", ".join(parity["missing_examples"][:5]))
        print("    allele mismatch (excluded): %d  (of which strand-flip-only: %d)"
              % (parity["n_allele_mismatch"], parity["n_allele_mismatch_strand_flip_only"]))
        for ex in parity["allele_mismatch_examples"][:5]:
            print("      e.g. %s model EA=%s vs VCF %s/%s"
                  % (ex["position"], ex["model_ea"], ex["vcf_ref"], ex["vcf_alt"]))
        print("    multi-record positions    : %d" % parity["n_multi_record_positions"])
        for dec in parity["multi_record_decisions"]:
            print("      %-16s %s -> %s"
                  % (dec["position"], "/".join(dec["records_present"]),
                     dec["chosen"] or "DROPPED"))
            print("      %-16s rule: %s  (|beta| %.3e)"
                  % ("", dec["rule_applied"], dec["abs_beta"]))
        print("    dropped unresolvable      : %d" % parity["n_multi_dropped_unresolvable"])
        print("    -> variants in score file : %d" % parity["n_final_scored"])
        print("    |beta| not scored         : %.6e" % parity["sum_abs_beta_not_scored"])

        keep_path = os.path.join(args.workdir, "cohort.keep")
        out_prefix = os.path.join(args.workdir, "cohort_score")
        _log, n_processed = score_cohort(args.plink2, ref_prefix, resolved_score,
                                         keep_path, cohort["selected_samples"],
                                         out_prefix)
        iids_scored, cols = read_sscore(out_prefix + ".sscore")

        shortfall = parity["n_final_scored"] - (n_processed or 0)
        print("  plink2 variants processed   : %s" % n_processed)
        print("  shortfall vs score file     : %d %s"
              % (shortfall, "" if shortfall == 0 else "  <-- FLAGGED, see artifact"))
        print("  samples scored              : %d (requested %d)"
              % (len(iids_scored), cohort["n_selected"]))
        if len(iids_scored) != cohort["n_selected"]:
            print("  WARNING: plink2 scored a different number of samples than selected")

        ac = cols.get("ALLELE_CT", cols.get("NALLELE_CT"))
        if ac is not None:
            print("  ALLELE_CT per sample        : min %d, max %d (2 x variants = %d)"
                  % (ac.min(), ac.max(), 2 * (n_processed or 0)))
            if ac.min() != ac.max():
                print("  NOTE: ALLELE_CT varies across samples -- there is missingness "
                      "in the reference and plink2 mean-imputed it.")

        score_stats = {
            "n_processed": n_processed,
            "n_scored": len(iids_scored),
            "shortfall": shortfall,
            "scores_sum": cols.get("SCORE1_SUM"),
            "scores_avg": cols.get("SCORE1_AVG"),
        }

    # ---- STEP 4 ----------------------------------------------------------
    print()
    print("=" * 72)
    print("STEP 4  emit the artifact")
    print("=" * 72)
    art = build_artifact(args, projection, s1, cohort, parity, score_stats,
                         scoring_sha256, plink2_version)
    with open(args.out, "w") as fh:
        json.dump(art, fh, indent=2)
        fh.write("\n")

    print("  usable                      : %s" % art["usable"])
    print("  reason                      : %s" % art["reason"])
    print("  n_participants              : %s" % art["n_participants"])
    d = art["distribution"]
    if d["n"]:
        print("  DISTRIBUTION (SCORE1_SUM, %d participants):" % d["n"])
        for q in QUANTILES:
            print("    p%-4d %+.6f" % (q, d["p%02d" % q]))
        print("    mean  %+.6f    sd %.6f" % (d["mean"], d["sd"]))
        print("    min   %+.6f    max %+.6f" % (d["min"], d["max"]))
    else:
        print("  DISTRIBUTION                : null (refusal; quantiles are null, "
              "not zero)")
    print("  artifact                    : %s" % args.out)


if __name__ == "__main__":
    main()
