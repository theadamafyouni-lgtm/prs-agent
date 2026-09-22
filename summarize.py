#!/usr/bin/env python3
"""one document for the whole run. read-only.

    python3 summarize.py                          # writes answer-side/RESULTS.md
    python3 summarize.py --runs answer-side/eval-run-12b --out /tmp/results.md

everything needed to read the eval in one place and nothing else: the table, then
one section per case with the trait, the ancestry pair, the three verdicts, and
the run's own words for why.

it does not re-grade. it reads grade.py so there is exactly one place where a
verdict is decided, and it reports the same checks the CSV carries rather than
collapsing them into one word: outcome, and chosen_model where the key expects a
score.

trait_id is reported in a table of its own and is in none of the verdict tallies.
it is not a check -- grade.py moved it out of the verdict, because a stop row
carries a trait id in the key even though a run that stops at the door never
reaches a trait, and one headline cannot answer both what the run decided and
what it resolved the trait to. the statuses here are grade.py's -- in-key,
not-in-key, none, not-listed, malformed -- and are deliberately not PASS and FAIL.

NOT-GRADED is counted apart from PASS and never folded into it. it means the key
listed nothing to compare against, or the column does not apply to that row --
`chosen_model` on a refusal, where there is no model to pick. counting those as
passes would turn a batch nobody checked into a clean sweep.
"""

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import grade as G

# taken from grade.py rather than restated, so that changing what is graded
# changes it here too. trait_id is not in it, by design.
CHECKS = G.CHECKS


def short(s, n):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + " ..."


def main():
    here = os.path.expanduser("~/CliGenAI-Lab/prs-agent-product")
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=here)
    ap.add_argument("--runs", default=None, action="append",
                    help="a directory of banked cases, repeatable")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    repo = os.path.abspath(os.path.expanduser(a.repo))
    key = G.read_key(os.path.join(repo, "answer-side", "answer-sets.csv"))
    run_dirs = a.runs or [os.path.join(repo, "answer-side", "eval-run-12")]
    out = a.out or os.path.join(repo, "answer-side", "RESULTS.md")

    cases = {}
    for d in run_dirs:
        cases.update(G.find_cases(os.path.expanduser(d)))

    rows, no_key = [], []
    for case in sorted(cases):
        krow = key.get(case)
        if not krow:
            no_key.append(case)
            continue
        p = cases[case]
        exp = G.expected_from(krow)
        cdir = os.path.dirname(p) if p else G.case_dir(run_dirs, case)
        anc = G.read_ancestry(cdir) if cdir else None

        if p is None:
            # A case that produced no result.json is reported, not dropped. Every
            # run writes one whatever the ending, so its absence is a fact about
            # the run rather than a case that simply has nothing to say.
            rows.append((case, krow, exp, None, "UNGRADEABLE", [],
                         "no %s" % G.RESULT, anc, None))
            continue

        act = G.actual_from(p)
        headline, checks, trait, err = G.grade(exp, act)
        rows.append((case, krow, exp, (None if err else act), headline, checks,
                     err or "", anc, trait))

    headline_counts = collections.Counter(r[4] for r in rows)
    check_counts = {c: collections.Counter() for c in CHECKS}
    trait_counts = collections.Counter()
    for _c, _k, _e, _a, _h, checks, _w, _anc, trait in rows:
        for name, verdict, _detail in checks:
            check_counts[name][verdict] += 1
        if trait:
            trait_counts[trait[0]] += 1

    def tally(counter):
        return ", ".join("%d %s" % (v, k) for k, v in sorted(counter.items())) or "none"

    L = []
    L.append("# selection suite results")
    L.append("")
    L.append("%d case(s) from %s."
             % (len(rows), ", ".join(os.path.basename(d.rstrip("/")) for d in run_dirs)))
    L.append("")
    L.append("**Verdicts.** %s." % tally(headline_counts))
    L.append("")
    L.append("**By check.** The verdict is two checks, scored independently, and a case "
             "can pass one and fail the other. `outcome` is graded on every row; "
             "`chosen_model` only where the key expects a score, because a refusal or a "
             "stop has no model to pick and an id named on the way to declining is "
             "context rather than a wrong answer.")
    L.append("")
    L.append("| check | tally |")
    L.append("|---|---|")
    for c in CHECKS:
        L.append("| `%s` | %s |" % (c, tally(check_counts[c])))
    L.append("")
    L.append("**Trait resolution.** Reported on its own, in none of the tallies above "
             "and in no verdict. What the run resolved the trait to, against what the "
             "key lists.")
    L.append("")
    L.append("| trait id | tally |")
    L.append("|---|---|")
    L.append("| status | %s |" % G.trait_tally(trait_counts))
    L.append("")
    n_ng = sum(check_counts[c]["NOT-GRADED"] for c in CHECKS)
    n_ch = sum(sum(check_counts[c].values()) for c in CHECKS)
    if n_ng:
        L.append("%d of %d checks were NOT-GRADED: either the key listed nothing to "
                 "compare against, or the column does not apply to that row — "
                 "`chosen_model` on a refusal or a stop, where there is no model to "
                 "pick. Nothing was checked either way. These are not passes and are "
                 "not counted as any." % (n_ng, n_ch))
        L.append("")
    if no_key:
        one = len(no_key) == 1
        L.append("%d case director%s under the run directory had no row in the key and "
                 "%s not in this table: %s."
                 % (len(no_key), "y" if one else "ies", "is" if one else "are",
                    ", ".join(no_key)))
        L.append("")
    L.append("Counts, not a rate. The acceptable sets run from 3 models to 21, so a "
             "pass on a 21-model case and a pass on a 3-model case are not the same "
             "result and are not averaged.")
    L.append("")
    # `trait id` sits after the verdict, not between the two checks, so the row
    # does not read as three verdicts of which one happens to be worded oddly.
    L.append("| case | trait | expected | result | outcome | model | verdict | trait id |")
    L.append("|---|---|---|---|---|---|---|---|")
    for case, krow, exp, act, headline, checks, wrong, anc, trait in rows:
        by = {n: v for n, v, _d in checks}
        e = "%s / %d model(s)" % (exp["outcome"] or "?", len(exp["models"]))
        got = "—" if act is None else "%s / %s" % (act["outcome"], act["model"] or "no model")
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s |"
                 % (case, krow.get("trait") or "?", e, got,
                    by.get("outcome", "—"), by.get("chosen_model", "—"),
                    headline, trait[0] if trait else "—"))
    L.append("")
    L.append("---")
    L.append("")

    for case, krow, exp, act, headline, checks, wrong, anc, trait in rows:
        L.append("## %s  %s" % (case, headline))
        L.append("")
        L.append("- **trait asked for** %s" % (krow.get("trait") or "?"))
        selfrep = (krow.get("ancestry") or "").strip()
        if selfrep or anc:
            agree = ""
            if anc and selfrep:
                agree = " (agree)" if anc.lower() in selfrep.lower() else " (differ)"
            L.append("- **ancestry** self-reported %s, projected %s%s"
                     % (selfrep or "not in the key", anc or "not found", agree))
        L.append("- **expected** outcome `%s`, %d acceptable model(s)"
                 % (exp["outcome"] or "?", len(exp["models"])))

        if act is None:
            L.append("- **result** %s" % wrong)
            L.append("")
            L.append("")
            continue

        L.append("- **result** outcome `%s`, model `%s`"
                 % (act["outcome"], act["model"]))
        L.append("- **reached** `%s`%s"
                 % (act["stage_reached"],
                    " — the person ended the session"
                    if act["session_ended_by_person"] else ""))
        for name, verdict, detail in checks:
            L.append("- **%s** %s%s" % (name, verdict, " — " + detail if detail else ""))
        if trait:
            L.append("- **trait id** %s — %s *(reported, not scored)*"
                     % (trait[0], trait[1]))
        for field, why in sorted(act["malformed"].items()):
            # trait_id excluded: the trait line above carries its malformation.
            if field not in CHECKS + ("trait_id",):
                L.append("- **malformed** `%s` %s" % (field, why))
        if exp["reason"] and exp["outcome"] in ("refuse", "stop"):
            L.append("- **the key says** %s" % short(exp["reason"], 300))
        L.append("")
        if act["why"]:
            L.append("> %s" % short(act["why"], 700))
            L.append("")
        L.append("")

    with open(out, "w") as fh:
        fh.write("\n".join(L))
    print("%d cases -> %s" % (len(rows), out))
    print("verdicts: %s" % tally(headline_counts))
    for c in CHECKS:
        print("  %-13s %s" % (c, tally(check_counts[c])))
    if trait_counts:
        print("  %-13s %s   (reported, not scored)"
              % ("trait id", G.trait_tally(trait_counts)))


if __name__ == "__main__":
    main()
