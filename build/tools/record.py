#!/usr/bin/env python3
"""record.py -- store a judgment the agent made, with its reasons and its evidence.

The agent writes the judgment; this tool only validates the shape and files it.
It does not evaluate whether the judgment is correct -- it cannot, and must not try.

Two modes, one principle: check the shape, file it, never judge the content.

  --judgment   one stage's judgment, into runs/<RUN_ID>/<stage>/<name>.json
  --result     the run's single result, into runs/<RUN_ID>/result.json

What --judgment enforces (mechanically):
  * `outcome` is one of the six recognised values,
  * `reasons` is a non-empty list of non-empty strings,
  * `evidence` is a non-empty list, each item pointing at a fact file and a field path,
  * a refuse / stop / ask carries `requirement_ids` naming what drove it.

That last one is the point: a refusal has to cite the requirement it is enforcing, so
"the agent refused" is never an unexamined verdict (JC-6, and spec §16-G's open
refuse-vs-stop boundary stays reviewable).

What --result enforces is narrower and stricter, because result.json is read by a
grader rather than by a person, and a grader cannot see that a field is wrong -- only
that it is present. Every rule below exists because its absence has mis-graded a run:
an `ask` filed as a final outcome when the run had not actually ended, a `chosen_model`
carrying a citation or a parenthetical so the id no longer matched the key, a trait
recorded as a name instead of an ontology id, or a stage block still holding the
template's "...". A run writes exactly one result.json, and it is the last thing it
writes.

Usage:
  python3 tools/record.py --run RUN_ID --stage door --judgment path/to/judgment.json
  cat judgment.json | python3 tools/record.py --run RUN_ID --stage door --judgment -
  python3 tools/record.py --run RUN_ID --result path/to/result.json
  cat result.json | python3 tools/record.py --run RUN_ID --result -
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib_run  # noqa: E402

REQUIRE_IDS_FOR = {"refuse", "stop", "ask"}

# The six stages, in flow order. `stage_reached` names one of them.
STAGES = ["door", "ancestry", "select", "score", "interpret", "report"]

# A run ends on exactly one of these. `ask` is deliberately absent: it is a step that
# resolves once the person answers, so a run never ends on it. A result filed as `ask`
# is either a run that has not finished or one whose real outcome went unrecorded, and
# accepting it would let both grade as though they had completed.
FINAL_OUTCOMES = {"score", "refuse", "stop"}

# Shapes, not vocabularies -- this tool does not know which traits or models are real,
# and must not. A bare ontology id has no spaces; a trait NAME does. A bare model id is
# exactly PGS followed by six digits; "PGS012536 (Smith 2021)" is not, and that is the
# form that has broken grading before.
TRAIT_ID_RE = re.compile(r"[A-Za-z]+[_:][0-9]+")
MODEL_ID_RE = re.compile(r"PGS[0-9]{6}")

# What each stage block carries beyond `outcome`. A stage that did not run is omitted
# entirely; a stage that ran reports all of its fields.
STAGE_FIELDS = {
    "door": ["assay_class", "genome_build"],
    "ancestry": ["inferred_group", "agreement"],
    "select": ["candidates_considered"],
}

# Template text left where a value belongs. Kept deliberately short and unambiguous:
# a wider list would reject real domain values ("none" is a legitimate answer for
# `agreement` when the person gave no self-report) and a validator that rejects true
# statements is one people route around.
PLACEHOLDERS = {"...", "....", "…", "tbd", "t.b.d.", "todo", "n/a", "<...>", "xxx",
                "string", "example", "fill me in"}


def fail(msg):
    print(f"record.py: REJECTED -- {msg}", file=sys.stderr)
    raise SystemExit(2)


def validate(j):
    if not isinstance(j, dict):
        fail("judgment must be a JSON object")
    for field in ("decision", "outcome", "reasons", "evidence"):
        if field not in j:
            fail(f"missing required field: {field}")
    if j["outcome"] not in lib_run.OUTCOMES:
        fail(f"outcome {j['outcome']!r} not one of {sorted(lib_run.OUTCOMES)}")
    if not isinstance(j["decision"], str) or not j["decision"].strip():
        fail("decision must be a non-empty string")
    if not isinstance(j["reasons"], list) or not j["reasons"]:
        fail("reasons must be a non-empty list")
    for r in j["reasons"]:
        if not isinstance(r, str) or not r.strip():
            fail("every reason must be a non-empty string")
    if not isinstance(j["evidence"], list) or not j["evidence"]:
        fail("evidence must be a non-empty list -- a judgment with no evidence is not recordable")
    for e in j["evidence"]:
        if not isinstance(e, dict) or "source" not in e:
            fail("every evidence item needs at least a 'source' (a fact file or artifact path)")
    if j["outcome"] in REQUIRE_IDS_FOR:
        ids = j.get("requirement_ids")
        if not isinstance(ids, list) or not ids:
            fail(f"outcome {j['outcome']!r} requires 'requirement_ids' naming what drove it "
                 f"(e.g. [\"SCO-8\", \"SEL-10\"])")
    return j


# ---------------------------------------------------------------------------
# result.json
# ---------------------------------------------------------------------------

def _is_placeholder(v):
    return isinstance(v, str) and (not v.strip() or v.strip().lower() in PLACEHOLDERS)


def _require_str(obj, key, where):
    v = obj.get(key)
    if not isinstance(v, str) or not v.strip():
        fail(f"{where}.{key} must be a non-empty string, got {type(v).__name__}: {v!r}")
    if _is_placeholder(v):
        fail(f"{where}.{key} is still the template placeholder {v!r}. Omit the block if "
             f"that stage did not run; fill it in if it did.")
    return v


def _bare_id(obj, key, pattern, example, why):
    """A bare id string, or null. Never a list, an object, or an id with text attached."""
    v = obj[key]
    if v is None:
        return None
    if not isinstance(v, str):
        fail(f"{key} must be a bare string like {example!r}, or null -- not a "
             f"{type(v).__name__}. {why}")
    if not pattern.fullmatch(v):
        fail(f"{key} must be exactly one bare id like {example!r}, or null. Got {v!r}. {why}")
    return v


def _model_list(block, name):
    v = block["candidates_considered"]
    if not isinstance(v, list):
        fail(f"stages.{name}.candidates_considered must be a list of bare PGS ids, got "
             f"{type(v).__name__}")
    for i, m in enumerate(v):
        if not isinstance(m, str) or not MODEL_ID_RE.fullmatch(m):
            fail(f"stages.{name}.candidates_considered[{i}] must be a bare id like "
                 f"'PGS012536'; got {m!r}")
    return v


def validate_result(r, run_id=None):
    """The shape of runs/<RUN_ID>/result.json. Raises SystemExit(2) on any violation.

    Mirrored by the harness, which re-validates the file it reads back rather than
    trusting that this ran. If you change a rule here, change it there too --
    harness/harness/orchestrator.py, validate_result().
    """
    if not isinstance(r, dict):
        fail("result must be a JSON object")

    for field in ("case_id", "run_id", "stage_reached", "decided_at", "outcome",
                  "trait_id", "chosen_model", "session_ended_by_person", "why", "stages"):
        if field not in r:
            fail(f"missing required field: {field}")

    # The key is required; a value is not. Nothing in the brief tells the agent which
    # case it is running -- the case id lives harness-side -- so demanding a non-empty
    # one would be demanding an invention, and JC-2 is the rule with no exceptions. It
    # stays mandatory-but-nullable so the agent states that it does not know rather than
    # omitting the field, and so a harness that later passes one in needs no change here.
    if r["case_id"] is not None:
        _require_str(r, "case_id", "result")
    rid = _require_str(r, "run_id", "result")
    if run_id is not None and rid != run_id:
        fail(f"run_id {rid!r} does not match --run {run_id!r}")

    if r["stage_reached"] not in STAGES:
        fail(f"stage_reached {r['stage_reached']!r} not one of {STAGES}")

    # Which stage made the judgment, which is not always the stage the run reached: a run
    # can refuse at `select` and still reach `report` to deliver that refusal. Where the
    # two differ is exactly what a grader of refusals wants, and `stage_reached` cannot
    # say it. So a `decided_at` that differs from `stage_reached` is not an error, and
    # nothing here should ever start requiring them to agree. Null is not one of the six.
    if r["decided_at"] not in STAGES:
        fail(f"decided_at {r['decided_at']!r} not one of {STAGES}")

    outcome = r["outcome"]
    if outcome == "ask":
        fail("outcome 'ask' is not a final outcome. An ask is a step that resolves once "
             "the person answers -- a run never ends on it. Record what it resolved to: "
             "score, refuse or stop. If the person left before answering, that is "
             "session_ended_by_person true, and the outcome is still the one the stage "
             "reached on the evidence it already had.")
    if outcome not in FINAL_OUTCOMES:
        fail(f"outcome {outcome!r} not one of {sorted(FINAL_OUTCOMES)}")

    _bare_id(r, "trait_id", TRAIT_ID_RE, "MONDO_0000001",
             "A trait name is not a trait id.")
    _bare_id(r, "chosen_model", MODEL_ID_RE, "PGS012536",
             "An id with a citation, a parenthetical or a score name appended is not a "
             "bare id, and is what has broken grading before.")

    if not isinstance(r["session_ended_by_person"], bool):
        fail("session_ended_by_person must be true or false, not "
             f"{type(r['session_ended_by_person']).__name__}")

    _require_str(r, "why", "result")

    if outcome == "score" and r["chosen_model"] is None:
        fail("outcome is 'score' but chosen_model is null -- a score is only a result "
             "alongside the model it was computed with")

    stages = r["stages"]
    if not isinstance(stages, dict):
        fail(f"stages must be an object keyed by stage name, got {type(stages).__name__}")
    for name in stages:
        if name not in STAGES:
            fail(f"stages has an unknown stage {name!r}; expected one of {STAGES}")
        block = stages[name]
        if not isinstance(block, dict):
            fail(f"stages.{name} must be an object, got {type(block).__name__}")
        oc = block.get("outcome")
        if oc not in lib_run.OUTCOMES:
            fail(f"stages.{name}.outcome {oc!r} not one of {sorted(lib_run.OUTCOMES)}")
        for field in STAGE_FIELDS.get(name, []):
            if field not in block:
                fail(f"stages.{name} is missing {field!r}. Omit the whole block if that "
                     f"stage did not run; do not file it with the field absent.")
            if field == "candidates_considered":
                _model_list(block, name)
            else:
                _require_str(block, field, f"stages.{name}")

    reached = r["stage_reached"]
    if reached in STAGE_FIELDS and reached not in stages:
        fail(f"stage_reached is {reached!r} but stages carries no {reached!r} block. The "
             f"stage the run reached has to report what it did there.")

    # Stricter than the rule above, and for all six stages rather than the three that
    # carry required fields: a judgment cannot have come from a stage that did not run.
    decided = r["decided_at"]
    if decided not in stages:
        fail(f"decided_at is {decided!r} but stages carries no {decided!r} block. The "
             f"stage that made the judgment is by definition one that ran.")

    return r


def _file_result(args, r):
    validate_result(r, run_id=args.run)
    r.setdefault("recorded_utc", lib_run.utcnow())
    path = os.path.join(lib_run.run_dir(args.run), "result.json")
    lib_run.write_json(path, r)
    lib_run.ledger_append(args.run, {
        "kind": "result",
        "stage_reached": r["stage_reached"],
        "outcome": r["outcome"],
        "trait_id": r["trait_id"],
        "chosen_model": r["chosen_model"],
        "session_ended_by_person": r["session_ended_by_person"],
        "artifact": os.path.relpath(path, lib_run.RUNS_DIR),
    })
    print(f"recorded result {r['outcome']} at {r['stage_reached']} "
          f"(model {r['chosen_model']})  -> {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--stage", choices=STAGES + ["session"],
                    help="required with --judgment; result.json carries stage_reached instead")
    ap.add_argument("--judgment", help="path to a JSON file, or - for stdin")
    ap.add_argument("--result", help="path to a JSON file, or - for stdin")
    ap.add_argument("--name", help="filename stem under runs/<run>/<stage>/ (default: judgment)")
    args = ap.parse_args()

    if bool(args.judgment) == bool(args.result):
        fail("pass exactly one of --judgment (one stage's judgment) or --result (the "
             "run's single result.json)")

    src = args.judgment or args.result
    raw = sys.stdin.read() if src == "-" else open(src).read()
    try:
        j = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"not valid JSON: {exc}")

    if args.result:
        return _file_result(args, j)

    if not args.stage:
        fail("--judgment requires --stage")

    validate(j)
    j.setdefault("stage", args.stage)
    j.setdefault("recorded_utc", lib_run.utcnow())

    stem = args.name or "judgment"
    path = os.path.join(lib_run.run_dir(args.run, args.stage), f"{stem}.json")
    lib_run.write_json(path, j)
    lib_run.ledger_append(args.run, {
        "kind": "judgment",
        "stage": args.stage,
        "decision": j["decision"],
        "outcome": j["outcome"],
        "requirement_ids": j.get("requirement_ids", []),
        "artifact": os.path.relpath(path, lib_run.RUNS_DIR),
    })
    print(f"recorded {j['outcome']}: {j['decision']}  -> {path}")


if __name__ == "__main__":
    main()
