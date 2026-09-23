#!/usr/bin/env python3
"""Fetch the reference data this harness needs, from the manifest that recorded it.

    python3 fetch_refdata.py --plan              # what it would do, downloads nothing
    python3 fetch_refdata.py                     # selection profile, about 4 GB
    python3 fetch_refdata.py --profile full      # everything, about 10 GB
    python3 fetch_refdata.py --verify            # check what is already here

build/refdata is tens of gigabytes and cannot live in a git repository. It is not
a black box either: build/refdata/MANIFEST.json records every external artifact
with its source URL, its size and its sha256, so this reads that file rather than
carrying a URL list of its own. If the manifest is regenerated, this follows.

THE MANIFEST DESCRIBES ONLY A FIFTH OF build/refdata

Measured 2026-09-23: build/refdata is 49 GB, and all 32 manifest records total
10.3 GB. The other 38 GB is hgdp_1kgp/ and phase3/ (29 GB, fetched by nothing at
all -- see SETUP.md section 8) plus the derived PCA output (8.9 GB). Running this
script to completion does NOT give you a usable build/refdata.

TWO PROFILES, AND WHY THE SMALLER ONE IS THE DEFAULT

    selection   about 4 GB.  10 artifacts. Everything except the Beagle panel.
    full        about 10 GB. Adds the 22 per-chromosome panel files.

The selection benchmark runs read-and-reason, ancestry and select, and stops
before scoring. Imputation is a scoring-stage concern, so no case in this
benchmark ever opens the panel. Downloading 6 GB to leave it untouched is not
caution, it is just 6 GB. Use --profile full when scoring runs.

WHAT THIS DOES NOT DO

It downloads. It does not build. Three of the largest directories under
ancestry-ref are derived rather than fetched: hgdp_basis, build and build_p3chr1
are PCA output.

Two corrections to what this docstring used to say, both verified 2026-09-23.

They are NOT produced by reference-provider/build_reference.py. That script
builds a per-patient INT-1 reference distribution, takes six required arguments,
and contains no network code and no reference to hgdp_basis. The PCA basis comes
from five plink2 commands, reproduced in SETUP.md section 8 and recorded in
prs-agent-private/refdata-recipe/plink.log.

They are NOT derived "from the files this script gets" either. Their input is
ancestry-ref/hgdp_1kgp/, 14 GB that is in no manifest and that nothing in either
repository fetches. Run this script to completion and that input is still absent.

The macOS JRE is the one artifact whose URL is platform-specific
(api.adoptium.net/.../mac/aarch64/...). On Linux it is fetched from the linux/x64
path instead, which the manifest does not know about, and the substitution is
reported rather than done silently.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(REPO, "build")
MANIFEST = os.path.join(BUILD, "refdata", "MANIFEST.json")

# The Beagle imputation panel: one file per autosome. Not touched by any case in
# the selection benchmark, which stops before scoring.
SCORING_ONLY_PREFIXES = ("panel_chr",)

LINUX_JRE = ("https://api.adoptium.net/v3/binary/latest/21/ga/"
             "linux/x64/jre/hotspot/normal/eclipse")


def human(b):
    b = float(b)
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB":
            return "%.1f %s" % (b, u)
        b /= 1024.0
    return "%.1f GB" % b


def load_manifest():
    if not os.path.isfile(MANIFEST):
        sys.exit("no manifest at %s\n"
                 "It is the record of where every external artifact came from, and "
                 "without it this script has nothing to follow. Copy it from a "
                 "machine that has the reference data, or rebuild with "
                 "reference-provider/build_reference.py." % MANIFEST)
    with open(MANIFEST) as fh:
        return json.load(fh)


def wanted(artifacts, profile):
    if profile == "full":
        return list(artifacts)
    return [a for a in artifacts
            if not a.get("key", "").startswith(SCORING_ONLY_PREFIXES)]


def dest_of(a):
    # manifest paths are relative to build/
    return os.path.join(BUILD, a["path"])


def sha256_of(path, cap_mb=None):
    h = hashlib.sha256()
    read = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            read += len(chunk)
            if cap_mb and read > cap_mb * (1 << 20):
                return None
    return h.hexdigest()


def state_of(a):
    """present / wrong-size / missing, without hashing gigabytes every run."""
    p = dest_of(a)
    if not os.path.isfile(p):
        return "missing"
    want = a.get("bytes")
    if want and os.path.getsize(p) != want:
        return "wrong-size"
    return "present"


def fetch(a, url=None):
    p = dest_of(a)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".part"
    src = url or a["url"]
    with urllib.request.urlopen(src) as r, open(tmp, "wb") as out:
        total = a.get("bytes") or 0
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if total:
                pct = 100.0 * got / total
                sys.stdout.write("\r    %-28s %5.1f%%  %s" %
                                 (a["key"][:28], pct, human(got)))
                sys.stdout.flush()
    sys.stdout.write("\r" + " " * 60 + "\r")
    os.replace(tmp, p)
    return os.path.getsize(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=("selection", "full"), default="selection")
    ap.add_argument("--plan", action="store_true", help="report only, download nothing")
    ap.add_argument("--verify", action="store_true",
                    help="sha256 what is already on disk. Slow: it reads every byte.")
    args = ap.parse_args()

    m = load_manifest()
    arts = m.get("artifacts") or []
    keep = wanted(arts, args.profile)
    skipped = len(arts) - len(keep)

    print("manifest   %s" % MANIFEST)
    print("assembled  %s" % m.get("assembled_utc", "?"))
    print("profile    %s   (%d of %d artifacts%s)"
          % (args.profile, len(keep), len(arts),
             ", %d imputation-only skipped" % skipped if skipped else ""))
    print()

    if args.verify:
        bad = 0
        for a in keep:
            p = dest_of(a)
            if not os.path.isfile(p):
                print("MISSING   %s" % a["key"])
                bad += 1
                continue
            want = a.get("sha256")
            if not want:
                print("no hash   %s" % a["key"])
                continue
            got = sha256_of(p)
            if got == want:
                print("ok        %-28s %s" % (a["key"][:28], human(os.path.getsize(p))))
            else:
                print("MISMATCH  %-28s on disk %s..." % (a["key"][:28], (got or "?")[:16]))
                bad += 1
        print()
        print("%d problem(s)" % bad)
        return 1 if bad else 0

    todo, have, bytes_todo = [], 0, 0
    for a in keep:
        st = state_of(a)
        if st == "present":
            have += 1
        else:
            todo.append((a, st))
            bytes_todo += a.get("bytes") or 0

    print("already here : %d" % have)
    print("to fetch     : %d   %s" % (len(todo), human(bytes_todo)))
    if todo:
        print()
        for a, st in todo[:40]:
            print("  %-10s %-26s %s" % (st, a["key"][:26], human(a.get("bytes") or 0)))
        if len(todo) > 40:
            print("  ... and %d more" % (len(todo) - 40))

    st = os.statvfs("/")
    free = st.f_bavail * st.f_frsize
    print()
    print("disk free    : %s" % human(free))
    if bytes_todo and free < bytes_todo * 1.3:
        print("NOT ENOUGH ROOM. The build step needs headroom beyond the downloads "
              "themselves, so this wants about 1.3x what it fetches.")
        if not args.plan:
            return 1

    if args.plan:
        print("\nplan only. nothing downloaded.")
        return 0
    if not todo:
        print("\nnothing to do.")
        return 0

    print()
    failed = []
    for i, (a, _st) in enumerate(todo, start=1):
        url = a["url"]
        note = ""
        if a.get("key") == "jre" and "/mac/" in url:
            # The manifest recorded a macOS JRE because that is where it was
            # assembled. Said out loud rather than substituted quietly.
            url = LINUX_JRE
            note = "  (manifest has the macOS build; fetching linux/x64 instead)"
        print("[%d/%d] %s%s" % (i, len(todo), a["key"], note))
        try:
            n = fetch(a, url)
        except Exception as exc:  # noqa: BLE001
            print("       FAILED: %s" % str(exc)[:160])
            failed.append(a["key"])
            continue
        want = a.get("bytes")
        if want and n != want and not note:
            print("       size differs: got %s, manifest says %s"
                  % (human(n), human(want)))

    print()
    print("%d fetched, %d failed" % (len(todo) - len(failed), len(failed)))
    for k in failed:
        print("  failed: %s" % k)
    print()
    print("Downloads only. The PCA basis under ancestry-ref (hgdp_basis, build) is")
    print("derived from these and still has to be built:")
    print("  python3 reference-provider/build_reference.py")
    print()
    print("Then check the machine as a whole:")
    print("  python3 preflight.py")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
