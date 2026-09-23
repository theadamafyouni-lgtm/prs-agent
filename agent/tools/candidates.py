#!/usr/bin/env python3
"""List PGS Catalog candidates per eval case, as facts, sorted so the plausible ones float up.

WHAT THIS IS NOT: a ranker. It does not decide which model is right, and it must not,
because deciding is what the agent is being graded on. If the answer set were built by
the same logic the agent runs, the eval would measure whether the agent can reproduce a
script rather than whether it judges well. So every column here is a fact read off the
Catalog, and the sort is a convenience for reading, not a verdict.

The sort puts development-ancestry matches first, then evaluation-ancestry matches, then
variant count descending. That ordering is a reading aid. A model at the top is not
"the answer" and a model at the bottom is not disqualified: on 2026-08-07 the two
best ancestry-matched models both lost on coverage and the third was delivered.

Ancestry comes from the projections, which are measurements rather than judgments, so
feeding it in does not leak anything the answer side should not have.

Per trait: one trait-search, one scores call, then one performance call per model.
Cached to disk, so a rerun costs nothing and the 34 traits stay well inside the
~100 calls/minute ceiling.

usage:
  candidates.py --index <index.md> --ancestry-dir <dir of *.projection.json> \\
                --out <candidates.csv> [--cache <dir>] [--trait "type 2 diabetes"]
"""
import argparse
import csv
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "pgs_catalog.py")

# The Catalog writes development ancestry as free English and the panel writes group codes.
# This maps one to the other and nothing else -- it is vocabulary, not judgment.

# Catalog terms per trait, chosen by hand from the search results. Several where the
# right candidate list spans more than one Catalog entry.
TRAIT_TERMS = {
    "depression": ["MONDO_0002050", "MONDO_0002009"],          # depressive disorder + MDD
    "gout": ["MONDO_0005393"],                                  # not chondrocalcinosis
    "prostate cancer": ["MONDO_0005159", "MONDO_0008315"],      # carcinoma 93 + cancer 7
    "LDL cholesterol": ["EFO_0004611", "EFO_0004195"],          # measurement 123, not the 10-model entry
    "breast cancer": ["MONDO_0004989", "MONDO_0007254"],        # carcinoma 145 + cancer 10
    "melanoma": ["MONDO_0005105", "MONDO_0005012"],             # melanoma 48 + cutaneous 6
    "rheumatoid arthritis": ["MONDO_0008383"],                  # not the ACPA subtypes
    "hypothyroidism": ["MONDO_0005420"],                        # not Hashimoto specifically
    "BMI / obesity": ["MONDO_0011122"],
    "Crohn's disease / IBD": ["MONDO_0005011"],
    "gallstone disease": ["MONDO_0012672", "MONDO_0005346"],    # same thing, two entries
    "high myopia": ["HP_0000545"],
    "kidney stones": ["MONDO_0008171", "MONDO_0024647"],        # kidney + urinary tract
    "colorectal cancer": ["MONDO_0005575", "MONDO_0002032", "MONDO_0006519"],
    "stroke": ["MONDO_0005098", "MONDO_0011057"],               # narrow + wide
}

BROAD_TO_GROUP = {
    "european": "EUR",
    "east asian": "EAS",
    "south asian": "CSA",
    "central asian": "CSA",
    "south/central asian": "CSA",
    "african": "AFR",
    "african american or afro-caribbean": "AFR",
    "african unspecified": "AFR",
    "sub-saharan african": "AFR",
    "hispanic or latin american": "AMR",
    "greater middle eastern (middle eastern, north african or persian)": "MID",
    "additional asian ancestries": "EAS",
    "additional diverse ancestries": None,
    "not reported": None,
    "multi-ancestry (including european)": "MULTI",
    "multi-ancestry (excluding european)": "MULTI",
}


def run_catalog(args, cache_dir, key):
    """One catalog call, cached. The cache is what keeps 34 traits under the rate limit."""
    path = os.path.join(cache_dir, key + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    out = subprocess.run([sys.executable, CATALOG] + args, capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write(f"  catalog call failed for {key}: {out.stderr[:200]}\n")
        return None
    try:
        d = json.loads(out.stdout)
    except json.JSONDecodeError:
        sys.stderr.write(f"  unparseable response for {key}\n")
        return None
    json.dump(d, open(path, "w"))
    time.sleep(0.4)
    return d


def group_of(broad):
    if not broad:
        return None
    return BROAD_TO_GROUP.get(broad.strip().lower(), None)


def dev_ancestries(score):
    """Groups the score was DEVELOPED in: GWAS samples and score-development samples."""
    out = set()
    for s in score.get("samples_variants", []) or []:
        g = group_of(s.get("ancestry_broad"))
        if g:
            out.add(g)
    return out


def _perf_records(perf):
    """Flatten the performance response into a list of records.

    The response is keyed by PGS id -- {"performance_by_pgs": {"PGS000014": [...]}} --
    rather than carrying a flat "results" list the way the other endpoints do. Reading
    it as "results" returns nothing and raises nothing, which is exactly the silent-drop
    shape this project keeps finding, so it is pulled into its own function where the
    key is stated once.
    """
    out = []
    for records in ((perf or {}).get("performance_by_pgs", {}) or {}).values():
        out.extend(records or [])
    return out


def eval_ancestries(perf):
    """Groups the score was EVALUATED in, from the performance records."""
    out = set()
    for r in _perf_records(perf):
        s = r.get("sampleset", {}) or {}
        for samp in s.get("samples", []) or []:
            g = group_of(samp.get("ancestry_broad"))
            if g:
                out.add(g)
    return out


def read_index(path):
    """case_id, trait, participant, expected outcome. Only score cases need candidates."""
    cases = []
    for line in open(path, encoding="utf-8"):
        if not line.startswith("| p"):
            continue
        f = [c.strip() for c in line.split("|")]
        if len(f) < 12:
            continue
        cases.append({"case_id": f[1], "participant": f[2], "trait": f[4], "outcome": f[10]})
    return cases


def read_ancestry(d):
    """Continental call per participant, taken as the nearest group by ADP distance."""
    out = {}
    for p in glob.glob(os.path.join(d, "*.projection.json")):
        pid = os.path.basename(p).replace(".projection.json", "").split("_")[0]
        j = json.load(open(p))
        dist = sorted(j["distance_to_superpopulations"].items(),
                      key=lambda kv: kv[1]["distance_to_centroid_adp"])
        out[pid] = dist[0][0]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True)
    ap.add_argument("--ancestry-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default="/tmp/pgs-cache")
    ap.add_argument("--trait", help="do one trait only, for checking the output by hand first")
    a = ap.parse_args()

    os.makedirs(a.cache, exist_ok=True)
    cases = [c for c in read_index(a.index) if c["outcome"] == "score"]
    anc = read_ancestry(a.ancestry_dir)
    missing = sorted({c["participant"] for c in cases} - set(anc))
    if missing:
        sys.stderr.write(f"no projection for: {', '.join(missing)}\n")

    if a.trait:
        cases = [c for c in cases if c["trait"] == a.trait]

    traits = sorted({c["trait"] for c in cases})
    sys.stderr.write(f"{len(cases)} score cases, {len(traits)} distinct traits\n")

    trait_models = {}
    for t in traits:
        tids = TRAIT_TERMS.get(t)
        if tids is None:
            safe = "".join(ch if ch.isalnum() else "_" for ch in t)
            ts = run_catalog(["trait-search", "--term", t], a.cache, f"trait_{safe}")
            if not ts or not ts.get("results"):
                sys.stderr.write(f"  NO TRAIT MATCH: {t}\n")
                trait_models[t] = []
                continue
            if ts["count"] > 1:
                # Not pinned and ambiguous. Say so loudly rather than quietly taking the
                # first: taking the first is how depression became bipolar disorder.
                sys.stderr.write(f"  UNPINNED and {ts['count']} matches: {t} -> using "
                                 f"{ts['results'][0]['id']} ({ts['results'][0]['label']})\n")
            tids = [ts["results"][0]["id"]]

        # Merge across terms, dedupe by PGS id. A score indexed under two conditions is
        # one candidate, not two.
        seen, models = set(), []
        for tid in tids:
            sc = run_catalog(["scores", "--trait-id", tid], a.cache, f"scores_{tid}")
            for m in (sc or {}).get("results", []):
                if m["id"] not in seen:
                    seen.add(m["id"])
                    # Remember which term found it. First term wins, which matters only
                    # where terms overlap, and there it records the more specific route in.
                    m["_matched_term"] = tid
                    models.append(m)
        sys.stderr.write(f"  {t} -> {'+'.join(tids)}, {len(models)} models\n")
        trait_models[t] = models

    rows = []
    for c in cases:
        person_group = anc.get(c["participant"])
        for m in trait_models.get(c["trait"], []):
            perf = run_catalog(["performance", "--pgs", m["id"]], a.cache, f"perf_{m['id']}")
            dev = dev_ancestries(m)
            ev = eval_ancestries(perf)
            hm = m.get("ftp_harmonized_scoring_files", {}) or {}
            rows.append({
                "case_id": c["case_id"],
                "participant": c["participant"],
                "person_ancestry": person_group or "",
                "trait": c["trait"],
                "pgs_id": m["id"],
                "name": m.get("name", ""),
                "url": "https://www.pgscatalog.org/score/" + m["id"] + "/",
                "trait_reported": (m.get("trait_reported") or "").replace(",", ";"),
                "matched_term": m.get("_matched_term", ""),
                "n_variants": m.get("variants_number", ""),
                "dev_ancestries": ";".join(sorted(dev)),
                "eval_ancestries": ";".join(sorted(ev)),
                "dev_matches_person": "yes" if person_group in dev else "no",
                "eval_matches_person": "yes" if person_group in ev else "no",
                "dev_is_multi": "yes" if "MULTI" in dev else "no",
                "n_perf_records": len(_perf_records(perf)),
                "year": (m.get("publication", {}) or {}).get("date_publication", "")[:4],
                "has_b37": "yes" if "GRCh37" in hm else "no",
                "has_b38": "yes" if "GRCh38" in hm else "no",
            })

    def sort_key(r):
        """Ancestry, then validation depth, then recency. Variant count last.

        Variant count is a coverage-shaped quantity and coverage is a floor, never a
        sort, so it breaks ties rather than setting the order. n_perf_records is a crude
        stand-in for validation depth -- it counts how many times anyone measured the
        score, not how well it did -- and it is the only depth signal the Catalog gives
        without reading each metric.
        """
        try:
            n = -int(r["n_variants"])
        except (ValueError, TypeError):
            n = 0
        try:
            year = -int(r["year"])
        except (ValueError, TypeError):
            year = 0
        return (r["case_id"],
                r["dev_matches_person"] != "yes",
                r["eval_matches_person"] != "yes",
                -int(r["n_perf_records"] or 0),
                year,
                n)

    rows.sort(key=sort_key)
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)

    sys.stderr.write(f"\n{len(rows)} candidate rows -> {a.out}\n")
    if rows:
        per = {}
        for r in rows:
            per.setdefault(r["case_id"], []).append(r)
        both = sum(1 for cid, rs in per.items()
                   if any(x["dev_matches_person"] == "yes" and x["eval_matches_person"] == "yes" for x in rs))
        none = sum(1 for cid, rs in per.items()
                   if not any(x["dev_matches_person"] == "yes" for x in rs))
        sys.stderr.write(f"cases with at least one dev+eval ancestry match : {both} of {len(per)}\n")
        sys.stderr.write(f"cases with NO development-ancestry match at all : {none} of {len(per)}\n")


if __name__ == "__main__":
    main()
