#!/usr/bin/env python3
"""collect every graded run into answer-side/results/ and one xlsx.

    python3 archive_results.py

read-only against the runs. it does not regrade and it does not compute a
verdict of its own: it calls grade.py per run directory,
takes their CSV, and writes it out in two shapes.

    answer-side/results/
        pipeline/pass-1/<case>.json      one file per case, every column
        pipeline/pass-1/summary.json     counts + provenance for that run
        pipeline/pass-2/...
        arm0/pass-1/...
        arm0/pass-2/...
        <other arms>/...
        all-results.xlsx                 one tab per run, plus `comparison`

the comparison tab is per case, one column per run, so a case that moved
between passes is visible on one row. counts are printed per run and never
averaged across them, for the reason grade.py gives: the acceptable sets run
from 3 models to 21.
"""

import csv
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.expanduser("~/CliGenAI-Lab/prs-agent-product")

# label -> (directory under answer-side, which grader)
# anything else that looks like a run directory is picked up automatically
# below and graded with grade.py under its own name.
KNOWN = [
    ("pipeline/pass-1", "eval-run-12",  "grade"),
    ("pipeline/pass-2", "eval-run-12b", "grade"),
    ("arm0/pass-1",     "arm0-run-12",  "arms"),
    ("arm0/pass-2",     "arm0-run-12b", "arms"),
]

# columns the comparison tab wants, with the names each grader might use
VERDICT_KEYS = ("verdict",)
MODEL_KEYS = ("actual_model", "chosen_model", "model", "chose")
EXPECTED_KEYS = ("expected_models", "expected", "expected_kind")
WRONG_KEYS = ("what_was_wrong", "wrong", "note")


def pick(row, keys):
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            return v
    return ""


def looks_like_run(path):
    """a directory holding at least two case subdirectories with a manifest."""
    if not os.path.isdir(path):
        return False
    n = 0
    for name in os.listdir(path):
        if os.path.isfile(os.path.join(path, name, "manifest.json")):
            n += 1
            if n >= 2:
                return True
    return False


def run_grader(kind, rundir):
    """call the grader and hand back its CSV rows. never regrades anything."""
    script = "grade.py"
    flag = "--runs"
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    cmd = [sys.executable, os.path.join(REPO, script), flag, rundir, "--csv", tmp]
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    rows = []
    if os.path.isfile(tmp) and os.path.getsize(tmp):
        with open(tmp, newline="") as fh:
            rows = list(csv.DictReader(fh))
    os.unlink(tmp)
    return rows, p.stdout, p.returncode


def main():
    answer = os.path.join(REPO, "answer-side")
    out_root = os.path.join(answer, "results")

    targets = []
    claimed = set()
    for label, d, kind in KNOWN:
        path = os.path.join(answer, d)
        if looks_like_run(path):
            targets.append((label, path, kind))
            claimed.add(d)
        else:
            print("skip  %-16s %s not present or empty" % (label, d))

    # anything else under answer-side that is a run and was not named above.
    # arm 1 and arm 2 live under names this script does not hardcode, so they
    # are found rather than assumed.
    for name in sorted(os.listdir(answer)):
        if name in claimed or name == "results":
            continue
        path = os.path.join(answer, name)
        if looks_like_run(path):
            targets.append((name.replace("-", "_"), path, "arms"))

    if not targets:
        sys.exit("no run directories found under %s" % answer)

    sheets = []          # (label, fieldnames, rows)
    per_case = {}        # case -> {label: "VERDICT  model"}
    labels = []

    for label, path, kind in targets:
        rows, stdout, rc = run_grader(kind, path)
        if not rows:
            print("WARN  %-16s grader returned no rows (exit %d)" % (label, rc))
            print("      %s" % (stdout.strip().splitlines() or ["no output"])[-1])
            continue

        d = os.path.join(out_root, label)
        os.makedirs(d, exist_ok=True)

        counts = {}
        for r in rows:
            case = (r.get("case_id") or r.get("case") or "").strip()
            if not case:
                continue
            with open(os.path.join(d, case + ".json"), "w") as fh:
                json.dump(r, fh, indent=2, sort_keys=True)
            v = pick(r, VERDICT_KEYS) or "?"
            counts[v] = counts.get(v, 0) + 1
            m = pick(r, MODEL_KEYS)
            per_case.setdefault(case, {})[label] = (v + ("  " + m if m else "")).strip()

        with open(os.path.join(d, "summary.json"), "w") as fh:
            json.dump({
                "label": label,
                "run_directory": os.path.relpath(path, REPO),
                "graded_by": "grade.py",
                "cases": len(rows),
                "counts": counts,
                "note": ("counts, not rates. the acceptable sets run from 3 models "
                         "to 21, so a pass on a 21-model case and a pass on a "
                         "3-model case are not the same result."),
            }, fh, indent=2, sort_keys=True)

        sheets.append((label, list(rows[0].keys()), rows))
        labels.append(label)
        print("ok    %-16s %d cases  %s" % (
            label, len(rows), "  ".join("%s=%d" % kv for kv in sorted(counts.items()))))

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment
    except ImportError:
        print("\nopenpyxl not available, wrote the json tree only")
        return

    wb = Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("comparison")
    head = ["case"] + labels
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for case in sorted(per_case):
        ws.append([case] + [per_case[case].get(l, "") for l in labels])
    ws.column_dimensions["A"].width = 30
    for i in range(len(labels)):
        ws.column_dimensions[chr(ord("B") + i)].width = 26
    ws.freeze_panes = "B2"

    for label, fields, rows in sheets:
        name = label.replace("/", "-")[:31]
        s = wb.create_sheet(name)
        s.append(fields)
        for c in s[1]:
            c.font = Font(bold=True)
        for r in rows:
            s.append([r.get(f, "") for f in fields])
        for i, f in enumerate(fields):
            col = s.cell(row=1, column=i + 1).column_letter
            s.column_dimensions[col].width = min(48, max(12, len(f) + 4))
            for cell in s[col]:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        s.freeze_panes = "A2"

    xlsx = os.path.join(out_root, "all-results.xlsx")
    wb.save(xlsx)
    print("\njson tree -> %s" % out_root)
    print("workbook  -> %s" % xlsx)
    print("\nthe comparison tab is per case, one column per run. a case that")
    print("changed between passes shows it on one row.")


if __name__ == "__main__":
    main()
