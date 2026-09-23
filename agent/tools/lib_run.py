"""Shared helpers for the tools. Mechanical only -- no domain judgment lives here.

A "run" is one pass of the agent over one patient file. Everything a run produces
lands under runs/<run-id>/, including an append-only ledger.jsonl that records both
the mechanical facts gathered and the judgments the agent made on them (DATA-6).
"""
import datetime
import hashlib
import json
import os
import subprocess
import sys

RUNS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")

# The outcomes a stage may report. The first four are spec §3's; `proceed` is an
# intermediate step inside a stage; `session-ended-by-person` is spec §12's explicit
# non-outcome, recorded so it is never miscounted as a refusal.
OUTCOMES = {"score", "refuse", "stop", "ask", "proceed", "session-ended-by-person"}


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def run_dir(run_id, stage=None, create=True):
    d = os.path.join(RUNS_DIR, run_id)
    if stage:
        d = os.path.join(d, stage)
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_json(path):
    with open(path) as fh:
        return json.load(fh)


def ledger_append(run_id, entry):
    """Append one record to the run's append-only ledger."""
    path = os.path.join(run_dir(run_id), "ledger.jsonl")
    entry = dict(entry)
    entry.setdefault("ts", utcnow())
    with open(path, "a") as fh:
        fh.write(json.dumps(entry, sort_keys=False) + "\n")
    return path


def tool_version(cmd):
    """Capture a tool's self-reported version verbatim (R6: read it off the object
    that travelled with the result, not off documentation)."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        text = (out.stdout or "") + (out.stderr or "")
        return text.strip().splitlines()[0] if text.strip() else None
    except Exception as exc:  # noqa: BLE001 - version capture must never break a run
        return f"<unavailable: {exc}>"


def emit(obj, out_path=None):
    """Write JSON to out_path if given, and always print it to stdout."""
    text = json.dumps(obj, indent=2)
    if out_path:
        write_json(out_path, obj)
        print(f"wrote {out_path}", file=sys.stderr)
    print(text)
    return obj
