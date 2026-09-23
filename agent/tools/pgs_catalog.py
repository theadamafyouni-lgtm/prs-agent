#!/usr/bin/env python3
"""pgs_catalog.py -- mechanical client for the PGS Catalog REST API (SEL-1).

Fetches and dumps. It does not rank candidates, does not filter on quality, does not
decide which model fits, and does not summarise ancestry evidence. Every one of those is
the agent's (SEL-2, SEL-8, ARCH-5); this tool exists so the agent is reading the live
catalog rather than a remembered one.

Version capture per DATA-6 / R6: `download` records the resolved URL actually fetched,
the file's own `#` header verbatim (the authoritative artifact, including #HmPOS_date),
the REST record's date_release and license, the catalog release date, and a retrieval
timestamp. Nothing is pinned; what was used is recorded.

Usage:
  python3 tools/pgs_catalog.py release
  python3 tools/pgs_catalog.py trait-search --term "atrial fibrillation"
  python3 tools/pgs_catalog.py scores --trait-id MONDO_0004981 --out runs/R/select/scores.json
  python3 tools/pgs_catalog.py performance --pgs PGS012535,PGS005168 --out runs/R/select/perf.json
  python3 tools/pgs_catalog.py download --pgs PGS012535 --build GRCh37 --out-dir runs/R/select/models
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_run  # noqa: E402

BASE = "https://www.pgscatalog.org/rest"
UA = "prs-agent-slice/0.1 (research build; mechanical fetch only)"


def get_json(url, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"pgs_catalog.py: GET failed after {retries} tries: {url}\n  {last}")


def get_paginated(url, limit=50):
    """Walk a paginated list endpoint and return every result."""
    out = []
    sep = "&" if "?" in url else "?"
    next_url = f"{url}{sep}limit={limit}"
    while next_url:
        page = get_json(next_url)
        out.extend(page.get("results", []))
        next_url = page.get("next")
    return out


def cmd_release(args):
    return {"catalog_release": get_json(f"{BASE}/release/current"),
            "retrieved_utc": lib_run.utcnow()}


def cmd_trait_search(args):
    q = urllib.parse.quote(args.term)
    d = get_json(f"{BASE}/trait/search?term={q}")
    return {"query": args.term, "retrieved_utc": lib_run.utcnow(),
            "count": d.get("count"), "results": d.get("results", [])}


def cmd_scores(args):
    results = get_paginated(f"{BASE}/score/search?trait_id={args.trait_id}")
    return {
        "trait_id": args.trait_id,
        "retrieved_utc": lib_run.utcnow(),
        "catalog_release": get_json(f"{BASE}/release/current").get("date"),
        "count": len(results),
        "results": results,
    }


def cmd_performance(args):
    out = {}
    for pgs in [p.strip() for p in args.pgs.split(",") if p.strip()]:
        out[pgs] = get_paginated(f"{BASE}/performance/search?pgs_id={pgs}")
    return {"retrieved_utc": lib_run.utcnow(),
            "n_scores_queried": len(out),
            "performance_by_pgs": out}


def cmd_download(args):
    rec = get_json(f"{BASE}/score/{args.pgs}")
    harmonized = (rec.get("ftp_harmonized_scoring_files") or {}).get(args.build) or {}
    url = harmonized.get("positions")
    if not url:
        raise SystemExit(f"pgs_catalog.py: no harmonized {args.build} file listed for {args.pgs}. "
                         f"Available: {list((rec.get('ftp_harmonized_scoring_files') or {}).keys())}")

    os.makedirs(args.out_dir, exist_ok=True)
    dest = os.path.join(args.out_dir, os.path.basename(url))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)

    # The file's own '#' header is the authoritative run-time artifact (R6).
    header, n_data = [], 0
    with gzip.open(dest, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#"):
                header.append(line.rstrip("\n"))
            else:
                n_data += 1
    return {
        "pgs_id": args.pgs,
        "build_requested": args.build,
        "resolved_url": url,
        "local_path": dest,
        "bytes": os.path.getsize(dest),
        "sha256": lib_run.sha256_file(dest),
        "retrieved_utc": lib_run.utcnow(),
        "n_rows_including_column_header": n_data,
        "file_header_verbatim": header,
        "rest_record": {k: rec.get(k) for k in
                        ("id", "name", "date_release", "license", "variants_number",
                         "variants_genomebuild", "weight_type", "method_name", "method_params",
                         "trait_reported", "trait_efo", "ancestry_distribution")},
        "catalog_release_date": get_json(f"{BASE}/release/current").get("date"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    ap.add_argument("--run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("release")
    p = sub.add_parser("trait-search"); p.add_argument("--term", required=True)
    p = sub.add_parser("scores"); p.add_argument("--trait-id", required=True)
    p = sub.add_parser("performance"); p.add_argument("--pgs", required=True)
    p = sub.add_parser("download")
    p.add_argument("--pgs", required=True)
    p.add_argument("--build", required=True, choices=["GRCh37", "GRCh38"])
    p.add_argument("--out-dir", required=True)

    args = ap.parse_args()
    result = {
        "release": cmd_release, "trait-search": cmd_trait_search, "scores": cmd_scores,
        "performance": cmd_performance, "download": cmd_download,
    }[args.cmd](args)
    result["tool"] = "pgs_catalog.py"
    result["source"] = "PGS Catalog REST API (pgscatalog.org) -- SEL-1: models come only from here"

    lib_run.emit(result, args.out)
    if args.run:
        rel = result.get("catalog_release_date") or result.get("catalog_release")
        if isinstance(rel, dict):
            rel = rel.get("date")
        lib_run.ledger_append(args.run, {
            "kind": "facts", "stage": "select", "tool": f"pgs_catalog.py {args.cmd}",
            "catalog_release": rel,
            "artifact": args.out,
        })


if __name__ == "__main__":
    main()
