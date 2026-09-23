#!/usr/bin/env python3
"""grade the selection suite against answer-sets.csv. read-only.

    python3 grade.py
    python3 grade.py --runs answer-side/eval-run-12b --key answer-side/answer-sets.csv
    python3 grade.py --csv answer-side/grades.csv        # also write a table

reports PER CASE. it will not print a single headline number, because the
acceptable sets differ in size from case to case, and a case with many acceptable
models is far easier to pass than one with few, so an average across them would mix
difficulties that a reader cannot see. counts of pass and fail are printed; a rate
is not. the real range, for the cases actually graded, is printed at the end.

it reads exactly two things:
    answer-side/answer-sets.csv        the key
    <runs-dir>/<case>/result.json      what the run concluded

result.json is THE graded artifact and there is no second place to look. a case
with no result.json is UNGRADEABLE and says so. that is a finding about the run
-- every run is supposed to write one, whatever the ending -- and not a prompt
to go hunting for something that might stand in for it.

what this used to do and does not any more: read select/model_choice.json, fall
back to a manifest when that was missing, and derive the model from a three-step
cascade whose last step was a regex over the entire file. that step scraped PGS
ids out of candidate lists and evidence blocks and reported them as the model the
agent had chosen, so runs that refused correctly -- naming the models they were
declining -- read as failures. every field is now taken from result.json exactly
as written, or the row fails saying which field and why. nothing is inferred.

TWO checks make the verdict, scored separately, so a run that reached the right
decision and picked a bad model reads as exactly that instead of as one
undifferentiated FAIL:

    outcome       vs expected_outcome     always. did the run reach the right
                                          decision: score, refuse or stop.
    chosen_model  vs acceptable_models    only where expected_outcome is score.
                                          membership in a ';'-separated list.

the model is gated on the expected outcome because a refuse or a stop row has no
model to pick. a run that declines correctly has already got the verdict right,
and an id it named on the way -- the model it was declining, the one it looked at
first -- is context. that is printed and never scored. the gate used to be the
key's acceptable_models happening to be empty, which reaches the same answer on
every row it covers and is not the rule. the rule is the expected outcome.

and ONE thing reported beside the verdict rather than inside it:

    trait_id      vs acceptable_trait_ids    reported, never scored

trait resolution and model selection are two questions, and a single headline can
only answer one. the stop rows are where that bites: p100-wrong-attachment expects
`stop` on a non-genetic attachment and the key still lists a trait id for it, so a
run that stopped at the door -- before there was a trait to resolve -- failed a
column that had nothing to do with what it was asked to get right. the trait is
now a line in the output and two columns in the csv, in its own vocabulary --
in-key, not-in-key, none, not-listed, malformed -- deliberately not PASS and FAIL
so that nobody reading the table later can sum it back into the verdict.

an empty list in the key means that column is not graded for that row. that is
deliberate, not missing data: all 33 refuse/stop rows have no acceptable_models
because there is no model to name, and 16 refuse rows have no trait id either.

NOT-GRADED is not PASS. it means the key listed nothing to compare against, or
the column does not apply to that row, and it is counted apart from both.

what it does NOT do: decide whether a refusal's stated reason means the same
thing as the key's. it puts the two sentences side by side and says a human has
to read them. a keyword overlap is not agreement, and pretending otherwise
would launder a judgment call into a green tick.
"""

import argparse
import csv
import json
import os
import re
import sys

GROUP_RE = re.compile(r"\b(AFR|AMR|CSA|SAS|EAS|EUR|MID|OTH)\b")

# The shapes result.json promises. These mirror agent/tools/record.py, which
# rejects anything else at write time. Re-checked here because a file being on
# disk is not evidence that the tool wrote it, and because a grader that repairs
# its own input is measuring the repair.
TRAIT_ID_RE = re.compile(r"[A-Za-z]+[_:][0-9]+")
MODEL_ID_RE = re.compile(r"PGS[0-9]{6}")

FINAL_OUTCOMES = ("score", "refuse", "stop")

# The checks that make the verdict, in the order they are reported. trait_id is
# deliberately not here: it is reported beside the verdict, not inside it, and a
# caller that tallies these must not pick it up. summarize.py reads this rather
# than keeping a list of its own.
CHECKS = ("outcome", "chosen_model")

# The vocabulary of the trait report, in reporting order: the one that means the
# run resolved what the key lists, then the four that mean something else. Not
# PASS / FAIL / NOT-GRADED, and not by accident -- a status that shares its words
# with the verdict gets counted with the verdict eventually. Callers tally through
# trait_tally() rather than sorting these alphabetically, which would bury
# `not-in-key` between `none` and `not-listed`.
TRAIT_STATUSES = ("in-key", "not-in-key", "none", "not-listed", "malformed")

RESULT = "result.json"

_MISSING = object()


def read_key(path):
    rows = {}
    with open(path, "r", errors="replace") as fh:
        for r in csv.DictReader(fh):
            cid = (r.get("case_id") or "").strip()
            if cid:
                rows[cid] = r
    return rows


def _idlist(cell):
    return [x.strip() for x in (cell or "").split(";") if x.strip()]


def expected_from(row):
    """what this case is supposed to do, read off the key row's own columns.

    three columns, read literally. the previous version took the expected outcome
    from the first word of the rationale, which is prose written for a person and
    was never a dependable place to keep grading logic.
    """
    return {
        "outcome": (row.get("expected_outcome") or "").strip(),
        "trait_ids": _idlist(row.get("acceptable_trait_ids")),
        "models": _idlist(row.get("acceptable_models")),
        "reason": (row.get("rationale") or "").strip() or None,
    }


def read_ancestry(case_dir):
    """the projected group call, if the run got that far. printed, never graded.

    takes the CASE directory. it used to take the graded artifact's path and call
    os.path.dirname on it, which is correct only when that artifact sits at the
    top of the case directory. under the nested layout it resolved one level too
    deep -- <case>/select/ancestry/ancestry.json -- and every nested case reported
    "not found", silently dropping the self-report comparison for a whole pass.

    the field name is not guaranteed across runs, so this tries the obvious keys
    and then falls back to the group vocabulary itself. it returns what it found
    and where, never a guess.
    """
    for name in ("ancestry_ancestry.json", os.path.join("ancestry", "ancestry.json")):
        p = os.path.join(case_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            raw = open(p, "r", errors="replace").read()
            j = json.loads(raw)
        except (OSError, ValueError):
            return None
        for k in ("group", "continental_group", "superpopulation",
                  "most_similar_population", "call", "ancestry_group"):
            v = j.get(k) if isinstance(j, dict) else None
            if isinstance(v, str) and GROUP_RE.search(v):
                return GROUP_RE.search(v).group(0)
        hit = GROUP_RE.search(raw)
        return hit.group(0) if hit else None
    return None


def find_cases(root):
    """every case directory under root, mapped to its result.json path or None.

    directories are enumerated, not artifacts. keying off the artifact meant a run
    that never wrote one was not discovered at all -- it did not fail, it vanished
    from the table, and a missing case is the one thing a grader must not hide.
    """
    out = {}
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        p = os.path.join(d, RESULT)
        out[name] = p if os.path.isfile(p) else None
    return out


def actual_from(path):
    """what the run concluded, taken from result.json exactly as written.

    every field is returned as found. a field that does not hold what it promised
    is recorded in `malformed` and the row fails on it; it is never repaired,
    substituted or scraped out of somewhere else.
    """
    try:
        with open(path, "r", errors="replace") as fh:
            d = json.load(fh)
    except (OSError, ValueError) as e:
        return {"error": "could not read %s: %s" % (path, e)}
    if not isinstance(d, dict):
        return {"error": "%s does not hold a JSON object" % path}

    bad = {}

    outcome = d.get("outcome", _MISSING)
    if outcome is _MISSING:
        bad["outcome"] = "field is absent"
    elif outcome == "ask":
        bad["outcome"] = ("is 'ask', which is not a final outcome -- an ask resolves "
                          "once the person answers, so a run never ends on it")
    elif outcome not in FINAL_OUTCOMES:
        bad["outcome"] = "is %r, not one of score, refuse, stop" % (outcome,)

    trait = d.get("trait_id", _MISSING)
    if trait is _MISSING:
        bad["trait_id"] = "field is absent"
    elif trait is not None and not (isinstance(trait, str)
                                    and TRAIT_ID_RE.fullmatch(trait)):
        bad["trait_id"] = "is %r, not a bare ontology id or null" % (trait,)

    model = d.get("chosen_model", _MISSING)
    if model is _MISSING:
        bad["chosen_model"] = "field is absent"
    elif model is not None and not (isinstance(model, str)
                                    and MODEL_ID_RE.fullmatch(model)):
        bad["chosen_model"] = "is %r, not a bare PGS id or null" % (model,)

    ended = d.get("session_ended_by_person", _MISSING)
    if not isinstance(ended, bool):
        bad["session_ended_by_person"] = "is %r, not true or false" % (
            None if ended is _MISSING else ended,)

    return {
        "outcome": None if outcome is _MISSING else outcome,
        "trait_id": None if trait is _MISSING else trait,
        "model": None if model is _MISSING else model,
        "stage_reached": d.get("stage_reached"),
        "session_ended_by_person": None if ended is _MISSING else ended,
        "why": (d.get("why") or "").strip() if isinstance(d.get("why"), str) else "",
        "malformed": bad,
        "path": path,
    }


def check_outcome(exp, act):
    if "outcome" in act["malformed"]:
        return "FAIL", "result.json outcome %s" % act["malformed"]["outcome"]
    if not exp["outcome"]:
        return "NOT-GRADED", "the key row has no expected_outcome"
    if act["outcome"] == exp["outcome"]:
        return "PASS", None
    return "FAIL", "expected %s, result.json says %s" % (exp["outcome"], act["outcome"])


def check_model(exp, act):
    """the model, graded only where the key expects a score.

    a refuse or a stop row has no model to pick, so there is nothing to be right
    or wrong about. the run that declines correctly has already been graded on
    that, by check_outcome; an id it named on its way -- the model it was
    declining, the one it looked at first -- is reporting, and failing a run for
    it would be scoring the same decision twice and getting a different answer the
    second time.

    the gate is the KEY's expected outcome, not the run's. a case the key says
    should score is graded on the model whatever the run did, so a run that was
    supposed to select and refused instead reads FAIL on both: it reached the
    wrong decision, and it named no model.
    """
    value = act["model"]
    malformed = act["malformed"].get("chosen_model")

    if exp["outcome"] != "score":
        where = exp["outcome"] or "unset"
        if malformed:
            return "NOT-GRADED", ("not graded on chosen_model where the expected outcome "
                                  "is %s; result.json chosen_model %s" % (where, malformed))
        if value:
            return "NOT-GRADED", ("not graded on chosen_model where the expected outcome "
                                  "is %s; the run named %s on the way" % (where, value))
        return "NOT-GRADED", None

    accepted = exp["models"]
    if not accepted:
        if value:
            return "NOT-GRADED", ("the key lists no acceptable models for this score row; "
                                  "the run named %s anyway" % value)
        return "NOT-GRADED", None
    if malformed:
        return "FAIL", "result.json chosen_model %s" % malformed
    if value is None:
        return "FAIL", ("the key lists %d acceptable models and result.json has "
                        "chosen_model null" % len(accepted))
    if value in accepted:
        return "PASS", None
    return "FAIL", "%s is not in the acceptable set of %d" % (value, len(accepted))


def trait_report(exp, act):
    """what the run resolved the trait to, next to what the key lists.

    reported, never scored, and returned in its own slot rather than in `checks`
    so a caller that tallies verdicts cannot pick it up. that is the whole reason
    it moved: a stop row carries a trait id in the key even though a run that
    stops at the door never reaches a trait, so grading this alongside the model
    made one headline answer two questions and get the second one wrong.

    the statuses are not PASS and FAIL, deliberately. a status that shares its
    words with the verdict gets summed into the verdict eventually, by a reader or
    by a script, and then this is back where it started.
    """
    value = act["trait_id"]
    malformed = act["malformed"].get("trait_id")
    listed = exp["trait_ids"]

    if malformed:
        return "malformed", "result.json trait_id %s" % malformed
    if not listed:
        if value:
            return "not-listed", ("the key lists no trait id for this row; the run "
                                  "resolved %s" % value)
        return "not-listed", "the key lists no trait id for this row and the run resolved none"
    if value is None:
        return "none", ("result.json has trait_id null; the key lists %d: %s"
                        % (len(listed), "; ".join(listed)))
    if value in listed:
        return "in-key", "%s, one of the %d the key lists" % (value, len(listed))
    return "not-in-key", ("%s is not among the %d the key lists: %s"
                          % (value, len(listed), "; ".join(listed)))


def trait_tally(counts):
    """the trait statuses as one line, in TRAIT_STATUSES order.

    anything not in that tuple is appended rather than dropped: a status this does
    not recognise is a bug in trait_report, and swallowing it would hide the bug
    and lose the case at the same time.
    """
    known = [(k, counts[k]) for k in TRAIT_STATUSES if counts.get(k)]
    rest = [(k, v) for k, v in sorted(counts.items()) if k not in TRAIT_STATUSES and v]
    return ", ".join("%d %s" % (v, k) for k, v in known + rest) or "none"


def grade(exp, act):
    """two verdicts, one headline, and the trait reported alongside.

    the headline is the worse of the two, but both are always returned, so a run
    that reached the right decision and picked a bad model reads as a split rather
    than as a single FAIL that hides which half worked.

    the trait comes back in its own slot and never in `checks`, which is what
    keeps it out of every tally downstream without each caller having to remember
    to exclude it.
    """
    if act.get("error"):
        return "UNREADABLE", [], None, act["error"]

    checks = [
        ("outcome",) + check_outcome(exp, act),
        ("chosen_model",) + check_model(exp, act),
    ]
    trait = trait_report(exp, act)
    verdicts = [c[1] for c in checks]
    if "FAIL" in verdicts:
        headline = "FAIL"
    elif "PASS" in verdicts:
        headline = "PASS"
    else:
        # Nothing in the key to compare against on either graded check.
        headline = "UNGRADEABLE"
    return headline, checks, trait, None


def main():
    here = os.path.expanduser("~/CliGenAI-Lab/prs-agent-product")
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=here)
    ap.add_argument("--key", default=None)
    ap.add_argument("--runs", default=None, action="append",
                    help="a directory of banked cases, repeatable")
    ap.add_argument("--csv", default=None, help="also write the table here")
    args = ap.parse_args()

    repo = os.path.abspath(os.path.expanduser(args.repo))
    key_path = args.key or os.path.join(repo, "answer-side", "answer-sets.csv")
    run_dirs = args.runs or [os.path.join(repo, "answer-side", "eval-run-12")]

    if not os.path.isfile(key_path):
        sys.exit("no key at %s" % key_path)
    key = read_key(key_path)

    cases = {}
    for d in run_dirs:
        cases.update(find_cases(os.path.expanduser(d)))

    if not cases:
        sys.exit("no case directories found under: %s" % ", ".join(run_dirs))

    n_missing = sum(1 for p in cases.values() if p is None)
    print("key   %s   %d rows" % (key_path, len(key)))
    print("runs  %d case(s), %d with %s" % (len(cases), len(cases) - n_missing, RESULT))
    print()

    table, counts = [], {}
    for case in sorted(cases):
        p = cases[case]
        row = key.get(case)
        if not row:
            print("%-30s UNGRADEABLE   no row named %r in the key" % (case, case))
            print()
            counts["UNGRADEABLE"] = counts.get("UNGRADEABLE", 0) + 1
            continue

        exp = expected_from(row)
        cdir = os.path.dirname(p) if p else case_dir(run_dirs, case)
        anc = read_ancestry(cdir) if cdir else None
        self_rep = (row.get("ancestry") or "").strip()

        if p is None:
            print("%-30s UNGRADEABLE" % case)
            print("   trait      %s" % (row.get("trait") or "?"))
            print("   expected   %s" % (exp["outcome"] or "?"))
            print("   wrong      no %s. every run writes one, whatever the ending, so "
                  "its" % RESULT)
            print("              absence is a fact about this run rather than a reason to")
            print("              grade something else in its place.")
            print()
            counts["UNGRADEABLE"] = counts.get("UNGRADEABLE", 0) + 1
            table.append(_row(case, row, exp, None, "UNGRADEABLE",
                              [], "no %s" % RESULT, self_rep, anc, p))
            continue

        act = actual_from(p)
        headline, checks, trait, err = grade(exp, act)
        counts[headline] = counts.get(headline, 0) + 1

        if headline == "UNREADABLE":
            print("%-30s UNREADABLE   %s" % (case, err))
            print()
            table.append(_row(case, row, exp, None, headline, [], err, self_rep, anc, p))
            continue

        split = "  ".join("%s %s" % (n, v) for n, v, _d in checks)
        print("%-30s %-12s %s" % (case, headline, split))
        print("   trait      %s" % (row.get("trait") or "?"))
        print("   expected   outcome %s | %d acceptable model(s)"
              % (exp["outcome"] or "?", len(exp["models"])))
        print("   result     outcome %s | model %s" % (act["outcome"], act["model"]))
        # its own line, below the verdict and outside it. see trait_report.
        print("   trait id   %-11s %s" % (trait[0], trait[1]))
        print("   reached    %s%s"
              % (act["stage_reached"],
                 "   session ended by the person" if act["session_ended_by_person"] else ""))

        if self_rep or anc:
            # self-report and projection disagree for 2 of the 12 participants,
            # and that disagreement is a finding rather than an error, so this
            # is printed and never graded. failing a correct projection against
            # a self-report would grade the person, not the agent.
            mark = ""
            if anc and self_rep:
                mark = "  agree" if anc.lower() in self_rep.lower() else "  differ"
            print("   ancestry   self-report %s | projected %s%s"
                  % (self_rep or "?", anc or "not found", mark))

        for name, verdict, detail in checks:
            if detail:
                print("   %-10s %s -- %s" % (name, verdict, detail))
        for field, why in sorted(act["malformed"].items()):
            # trait_id is excluded because the trait line above already carries
            # its malformation, with status `malformed`.
            if field not in CHECKS + ("trait_id",):
                print("   malformed  %s %s" % (field, why))
        if act["why"]:
            print("   why        %s" % act["why"][:220])
        if exp["reason"] and exp["outcome"] in ("refuse", "stop"):
            print("   key says   %s" % exp["reason"][:220])
        print()

        table.append(_row(case, row, exp, act, headline, checks, "", self_rep, anc, p,
                          trait))

    print("-" * 62)
    for k in ("PASS", "FAIL", "UNGRADEABLE", "UNREADABLE"):
        if counts.get(k):
            print("%-12s %d" % (k, counts[k]))
    print()
    # the range is read off the key for the cases graded here, never hardcoded: a
    # number written into this file goes stale the first time the key changes.
    sizes = sorted(len(_idlist(key[c].get("acceptable_models"))) for c in cases
                   if c in key and (key[c].get("expected_outcome") or "").strip() == "score")
    if sizes:
        print("counts, not a rate. the acceptable sets for these cases run %d to %d"
              % (sizes[0], sizes[-1]))
        print("models, so a pass on a %d-model case and a pass on a %d-model case are"
              % (sizes[-1], sizes[0]))
        print("not the same result and must not be averaged into one.")
    else:
        print("counts, not a rate.")
    print()
    print("the verdict is two checks: outcome always, chosen_model only where the")
    print("key expects a score. trait_id is reported on its own line and in its")
    print("own columns and is in none of these counts -- a trait resolved wrong")
    print("and a model picked wrong are different failures, and one headline")
    print("cannot carry both.")

    if args.csv and table:
        out = os.path.expanduser(args.csv)
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(table[0].keys()))
            w.writeheader()
            w.writerows(table)
        print()
        print("table -> %s" % out)


def case_dir(run_dirs, case):
    """where a case lives when it wrote no result.json to locate it by."""
    for d in run_dirs:
        p = os.path.join(os.path.expanduser(d), case)
        if os.path.isdir(p):
            return p
    return None


def _row(case, row, exp, act, headline, checks, wrong, self_rep, anc, path, trait=None):
    by = {n: v for n, v, _d in checks}
    detail = "; ".join("%s: %s" % (n, d) for n, _v, d in checks if d)
    act = act or {}
    # the trait report, in two columns of its own. it is not folded into
    # what_was_wrong: nothing it can say is a failure.
    t_status, t_note = trait if trait else ("", "")
    return {
        "case_id": case,
        "trait": row.get("trait"),
        "expected_outcome": exp["outcome"],
        "expected_trait_ids": ";".join(exp["trait_ids"]),
        "expected_models": ";".join(exp["models"]),
        "actual_outcome": act.get("outcome") or "",
        "actual_trait_id": act.get("trait_id") or "",
        "actual_model": act.get("model") or "",
        "stage_reached": act.get("stage_reached") or "",
        "session_ended_by_person": ("" if act.get("session_ended_by_person") is None
                                    else str(act.get("session_ended_by_person"))),
        "why": act.get("why") or "",
        "verdict": headline,
        "outcome_verdict": by.get("outcome", ""),
        "model_verdict": by.get("chosen_model", ""),
        "trait_id_status": t_status,
        "trait_id_note": t_note or "",
        "what_was_wrong": wrong or detail,
        "self_reported_ancestry": self_rep,
        "projected_ancestry": anc or "",
        "artifact": path or "",
    }


if __name__ == "__main__":
    main()
