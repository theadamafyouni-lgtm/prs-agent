#!/usr/bin/env python3
"""Run the corpus. Three pipeline passes, then one arm-0 pass. Resumable.

    python3 run_corpus.py --plan                 # what it would do, runs nothing
    python3 run_corpus.py                        # everything still outstanding
    python3 run_corpus.py --only pipeline-2      # one block
    python3 run_corpus.py --cases p05,p19        # a few cases, all blocks
    python3 run_corpus.py --concurrency 1        # serial

WHAT IT RUNS

Four blocks, in order, each over the 97 cases in cases.tsv:

    pipeline-1  pipeline-2  pipeline-3     --stop-after select
    arm0-1                                 --bare, minimal transport, task brief

Three passes of the pipeline because the question repeatability is being asked
about is the pipeline's. One arm-0 pass because it is the floor comparison and a
floor does not need a variance estimate.

Blocks run whole, one after another, rather than three passes of each case back
to back. If this dies at hour thirty there are complete passes to compare rather
than a third of every case.

RESUME

A case whose banked directory already exists is skipped. So an interrupted run is
restarted with the same command and picks up where it stopped, and a deliberate
rerun means deleting that one directory first. Nothing is ever overwritten.

A run the environment stopped -- an expired token, a session or usage limit, a run
that died before it said anything -- is not banked in either place, so its case
stays unfinished and the next run of this script tries it again. Nothing needs
deleting for that to work. A run that happened and went badly IS banked: a
malformed result, or none at all, is a finding about the agent rather than about
the machine.

WHERE THINGS LAND

Two places, for two purposes.

    answer-side/<block>/<case>/       the bulk: the run tree minus the rebuildable
                                     gigabytes. Banked and pruned by this script.
    results/<arm>/<pass>/<case>/      four small files, written by the harness
                                     itself: result.json, manifest.json,
                                     transcript.jsonl, network.jsonl.

The arm and the pass come from the block name, so pipeline-2 files its cases under
results/pipeline/pass2/. Neither location is ever overwritten; each skips a case
directory that already exists, and each says so.

DISK

A completed pipeline case writes about 8.5 GB, effectively all of it the ancestry
stage rebuilding a PCA basis. Everything worth keeping -- select, door, session,
the manifest, the transcript -- is under 100 MB. So banking copies those, drops
ancestry/ref and ancestry/work, and then deletes the sandbox entirely. Without
that, three passes would need about 2.5 TB.

The refdata clone is hard-linked and costs nothing, which is what makes running
three at once affordable at all.

THE TOKEN

It comes from /root/.env, a KEY=value file holding CLAUDE_CODE_OAUTH_TOKEN, mode
600 -- not from the launching shell. A shell opened before the .bashrc line was
added does not have it, nothing about that shell looks wrong, and every case then
dies in seconds with `Not logged in`. That has cost three runs. This script now
refuses to start without a credential rather than discovering it 400 cases later.
An environment that already has the variable set still wins.

Signing in to Claude Code anywhere else revokes this machine's session, and it has
happened three times already. A 401 stops the whole batch immediately rather than
spending the remaining cases on it a few seconds each. Sign in, re-pin the CLI to
2.1.224 in that order, and run the same command again.

A CAP IS DIFFERENT: it clears itself. On a usage, session, weekly or rate cap the
runner reads the reset time out of the message, sleeps until then plus a small
margin, and runs the capped case again -- it was never banked, so there is nothing
to clear first. Up to three waits a block, and no single wait longer than eight
hours. If the message does not say when it resets, or the reset is further off than
that, or the three waits are used up, it stops the block -- a wrong or very long
sleep is worse than a stop, because a stop is on the screen in the morning and a
sleep looks like a batch that is working. A waiting block says so in the log every
half hour: `[cap]` lines mean asleep, not dead.
"""

import argparse
import calendar
import json
import os
import queue
import re
import shutil
import subprocess

# The model every case runs on. Pinned full so a corpus is one model throughout.
MODEL = "claude-opus-4-8"
import sys
import threading
import time
import traceback
import uuid

REPO = os.path.expanduser("~/CliGenAI-Lab/prs-agent-product")

# The token loader lives in the harness, not here, so the two entry points cannot drift on
# what counts as authenticated. Imported by path because this script runs from /root rather
# than from the repo tree.
sys.path.insert(0, os.path.join(REPO, "harness"))
from harness import auth  # noqa: E402

CASES = "/root/cases.tsv"
OUT = os.path.join(REPO, "answer-side")
PERSONAS = os.path.join(REPO, "build", "sim", "personas", "eval")
BRIEF = os.path.join(REPO, "harness", "briefs", "catalog-minimal.txt")

MIN_FREE_GB = 25          # three live sandboxes plus margin
KEEP = ("select", "door", "session", "ancestry")

# A cap, as it appears in the agent's output, matched case-insensitively.
#
# These match the NOUN, not the sentence. The list used to read "hit your session limit",
# and on 2026-09-07 the account hit `You've hit your weekly limit` -- a limit the list did
# not name, so nothing recognised it. Every remaining case then failed in nine seconds
# apiece and pass 2 and most of pass 3 were spent on it. A wording the vendor controls is
# not something to pattern-match a whole sentence against; the noun is the durable part.
#
# Separate from the harness's ENVIRONMENT_FAILURE_MARKERS on purpose: the harness
# classifies every environment failure alike, because none of them may be banked, while
# only a cap is certain to hit every case after this one too AND is the only one that
# clears itself. That is what makes a cap a wait rather than a skip or a stop.
CAP_MARKERS = ("session limit", "usage limit", "weekly limit", "rate limit",
               "hit your limit", "quota")

# The reset time inside a cap message. Two shapes seen so far -- `resets 6pm (UTC)` and
# `resets 11:40pm (UTC)` -- so the minutes are optional. Anchored on the word `resets`
# rather than on the surrounding sentence, for the same reason CAP_MARKERS matches the
# noun: the sentence around it has already changed once.
CAP_RESET_RE = re.compile(r"\bresets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)

# A dead token, as it appears in the output. PHRASES, not fragments, and that cost two
# launches. This used to match the bare strings "OAuth", "401" and "revoked", case
# sensitively, against the manifest tails alone. When the cap fix widened the search to the
# whole case log, the bare "oauth" started matching the harness's own startup line --
#
#     auth        : file (CLAUDE_CODE_OAUTH_TOKEN read from /root/.env)
#
# -- which EVERY SUCCESSFUL RUN prints. So the branch that means "the token is dead, stop
# the batch" fired on the line that proves the token is alive, on the first case, every
# time. Three cases started, three stopped, nothing banked, and each run had in fact
# succeeded.
#
# The lesson is not that those three fragments were badly chosen. It is that a search was
# widened to a stream this project had just started writing to, and nothing tested the two
# together. That is what check_markers.py exists for: it builds a synthetic successful-run
# log out of run_harness.py's own print statements and asserts that no marker here fires
# on it. Add a print to the harness that says "quota" and that test goes red.
TOKEN_DEAD_MARKERS = ("failed to authenticate", "oauth session expired",
                      "oauth token has expired", "not logged in", "invalid api key",
                      "revoked")

# 401 is three digits, and a run id is run-%Y%m%d-%H%M%S-<hex6>, so `run-20260907-140401-`
# contains it -- 504 of the 86400 seconds in a day, plus whatever the hex digests hold. As
# a bare substring it would have stopped a batch on roughly one case in 170, at random,
# long after anyone was still looking for it. Word-bounded it matches the status code and
# not the timestamp.
TOKEN_401_RE = re.compile(r"\b401\b")

MAX_CAP_WAITS = 3         # per block. A real account problem must not become a loop.
CAP_MARGIN = 180          # seconds past the stated reset, for clock skew on either side
CAP_PAST_GRACE = 120      # a reset this recently past is imminent, not tomorrow's
CAP_HEARTBEAT = 1800      # how often a waiting block says it is still alive

# The longest single wait. A parsed reset can be nearly a day out, and three of those is
# most of a week spent asleep -- which is worse than stopping, because a stop is on the
# screen in the morning and a long sleep looks exactly like a batch that is working. Past
# this it stops the block and says how long it would have had to wait.
MAX_CAP_SLEEP = 8 * 3600

_cap_lock = threading.Lock()
_cap_state = {"waits": 0, "until": 0.0}

# Under ancestry, keep the files and drop every subdirectory.
#
# The first version named the directories to drop: ref and work, which is what
# the tool writes when it runs as intended. That is not what happens. The agent
# builds the reference basis wherever it decides to, and across one pass it chose
# `exec-shim`, `shim`, `prefilter` and `hgdp_attempt`. Two of those survived
# banking at 48 and 61 GB and filled the disk twice.
#
# A name list cannot work against a directory the agent names itself. The shape,
# though, is stable: every judgment and log is a FILE (ancestry.json,
# projection.json, build_ref.log) and every basis is a DIRECTORY. So keep files,
# drop directories, and a name nobody has thought of yet is handled.
PRUNE_SUBDIRS_UNDER = ("ancestry",)

BLOCKS = [
    ("pipeline-1", "eval-run-97-p1", ["--stop-after", "select"]),
    # arm 0 runs SECOND, not last. Three pipeline passes plus arm 0 is about 70
    # hours of compute and the deadline is 72 hours out, so the last block in
    # this list is the one that will not happen. Arm 0 is the floor condition the
    # main claim rests on, and its cases take 4-14 minutes rather than 40, so it
    # is both the most valuable block and the cheapest. Passes 2 and 3 are the
    # repeatability evidence, which is worth having and is not what the paper
    # turns on.
    ("arm0-1", "arm0-run-97-p1",
     ["--bare", "--brief-file", BRIEF, "--transport", "minimal"]),
    ("pipeline-2", "eval-run-97-p2", ["--stop-after", "select"]),
    ("pipeline-3", "eval-run-97-p3", ["--stop-after", "select"]),
]


def arm_and_pass(block):
    """"pipeline-2" -> ("pipeline", "pass2"). The block name IS the arm and the pass.

    Derived rather than stored beside the block, so the two cannot drift: a block renamed
    without its results/ destination renamed would file a pass under another pass's name,
    and nothing downstream would notice.
    """
    m = re.fullmatch(r"([A-Za-z0-9]+)-(\d+)", block)
    if not m:
        raise ValueError(
            "block name %r is not <arm>-<pass number>, so results/ has no arm and no pass "
            "to file its cases under" % block)
    return m.group(1), "pass" + m.group(2)


# At import, so a block named in a way results/ cannot express is one error at startup
# rather than 97 cases filed somewhere nobody looks.
for _block, _outdir, _extra in BLOCKS:
    arm_and_pass(_block)

_print_lock = threading.Lock()
_stop = threading.Event()
_stop_reason = []

# The proxy picks its loopback port by scanning 18800-18899 and binding the first
# free one, but bind and listen are separate calls, so two runs starting in the
# same second can both bind before either listens and the second dies with
# EADDRINUSE. Rather than patch shared harness code that also runs on the Mac,
# launches are spaced. LAUNCH_GAP is held by a lock so it applies to retries and
# to a resumed batch, not only to the first three threads.
LAUNCH_GAP = 25.0
_launch_lock = threading.Lock()
_last_launch = [0.0]


def wait_for_launch_slot():
    with _launch_lock:
        gap = LAUNCH_GAP - (time.time() - _last_launch[0])
        if gap > 0:
            time.sleep(gap)
        _last_launch[0] = time.time()


def say(msg):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
    with _print_lock:
        print(line, flush=True)
        with open(LOG, "a") as fh:
            fh.write(line + "\n")


def free_gb():
    st = os.statvfs("/")
    return (st.f_bavail * st.f_frsize) // (1024 ** 3)


def _utc(ts):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def _hms(seconds):
    seconds = max(0, int(seconds))
    return "%dh%02dm" % (seconds // 3600, (seconds % 3600) // 60)


def parse_reset(text, now=None):
    """The epoch second a cap message says the limit resets, or None if it does not say.

    The stated time is UTC and carries no date, so it is today's if that is still ahead and
    tomorrow's otherwise: `resets 6pm` read at 9pm means 6pm tomorrow.

    One exception, which is CAP_PAST_GRACE. A reset a couple of minutes in the past is
    clock skew, or a message written as the cap was already expiring -- not a wait until
    tomorrow. Rolling that forward a full day would idle the box for 24 hours to avoid
    waiting 2 minutes, which is the failure this whole mechanism exists to prevent.

    Returns None rather than guessing on anything it does not recognise. A wrong sleep is
    worse than a stop: a stop is visible in the morning, and a 20-hour sleep looks exactly
    like a batch that is working.
    """
    match = CAP_RESET_RE.search(text or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    hour = hour % 12 + (12 if match.group(3).lower() == "pm" else 0)

    now = time.time() if now is None else now
    t = time.gmtime(now)
    reset = calendar.timegm((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, 0, 0, 0))
    if reset < now - CAP_PAST_GRACE:
        reset += 86400
    return reset


def cap_refusal(text):
    """Why wait_for_cap declined to wait, in the words the stop message needs.

    Re-derived rather than carried back, so the three reasons stay written next to the
    three checks that produce them and cannot drift into describing the wrong one.
    """
    reset = parse_reset(text)
    if reset is None:
        return "the message did not say when it resets"
    wait = reset + CAP_MARGIN - time.time()
    if wait > MAX_CAP_SLEEP:
        return ("it does not reset for %s, past the %s a single wait is allowed"
                % (_hms(wait), _hms(MAX_CAP_SLEEP)))
    return "this block had already waited %d times" % MAX_CAP_WAITS


def reset_cap_waits():
    """Per block, so three waits in pass 1 do not deny pass 2 its own three."""
    with _cap_lock:
        _cap_state["waits"] = 0
        _cap_state["until"] = 0.0


def wait_for_cap(case, text):
    """Sleep until the cap resets. True if the case should be run again.

    False means the runner will not wait: either the message did not say when it resets, or
    this block has waited MAX_CAP_WAITS times already. The caller then stops the block,
    which is what every cap did before this existed.

    Three cases are in flight at once, so three threads can hit the same cap within seconds
    of each other. The first schedules the wait and the others join it, rather than each
    counting a wait of its own and each stacking another margin on the end.

    The sleep is in CAP_HEARTBEAT chunks against _stop rather than one long time.sleep, so
    the log shows a batch that is asleep rather than one that is dead, and so a stop from
    anywhere else lands within half an hour instead of six.
    """
    reset = parse_reset(text)
    if reset is None:
        return False

    too_long = reset + CAP_MARGIN - time.time()
    if too_long > MAX_CAP_SLEEP:
        say("[cap] CAP HIT on %s, but it does not reset for %s (at %s), past the %s "
            "ceiling. Stopping the block rather than sleeping through it."
            % (case, _hms(too_long), _utc(reset), _hms(MAX_CAP_SLEEP)))
        return False

    with _cap_lock:
        joining = _cap_state["until"] > time.time()
        if joining:
            wake = _cap_state["until"]
        else:
            if _cap_state["waits"] >= MAX_CAP_WAITS:
                return False
            _cap_state["waits"] += 1
            wake = reset + CAP_MARGIN
            _cap_state["until"] = wake
        nth = _cap_state["waits"]

    if joining:
        say("[cap] %s hit the same cap; joining the wait already set for %s"
            % (case, _utc(wake)))
    else:
        say("")
        say("[cap] CAP HIT on %s at %s. Nothing was banked, so no case was lost."
            % (case, _utc(time.time())))
        say("[cap] the message says it resets %s; waking at %s (+%ds margin), %s from now."
            % (_utc(reset), _utc(wake), CAP_MARGIN, _hms(wake - time.time())))
        say("[cap] wait %d of %d for this block. THE BATCH IS ASLEEP, NOT DEAD."
            % (nth, MAX_CAP_WAITS))

    while not _stop.is_set():
        left = wake - time.time()
        if left <= 0:
            break
        _stop.wait(min(CAP_HEARTBEAT, left))
        left = wake - time.time()
        if left > 0 and not _stop.is_set():
            say("[cap] still waiting on the cap, %s to go (wakes %s)" % (_hms(left),
                                                                         _utc(wake)))

    if _stop.is_set():
        return False
    say("[cap] the cap window is over. Re-running %s, which was never banked." % case)
    return True


def load_cases():
    rows = []
    with open(CASES) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                rows.append(tuple(parts))
    rows.sort(key=lambda r: int(re.match(r"p(\d+)", r[0]).group(1)))
    return rows


def manifest_of(run_id):
    p = os.path.join(REPO, "harness", "vault", "runs", run_id, "manifest.json")
    try:
        with open(p) as fh:
            return json.load(fh)
    except Exception:
        return {}


def bank(run_id, dest):
    """Keep the judgment and the record. Drop the rebuildable bulk."""
    os.makedirs(dest, exist_ok=True)
    src = os.path.join(REPO, "harness", "sandboxes", run_id, "prs", "runs", run_id)
    if os.path.isdir(src):
        for name in sorted(os.listdir(src)):
            s = os.path.join(src, name)
            if not os.path.isdir(s):
                shutil.copy2(s, os.path.join(dest, name))
                continue
            if name not in KEEP:
                continue
            d = os.path.join(dest, name)
            if name in PRUNE_SUBDIRS_UNDER:
                # files only, no recursion into whatever the agent built
                os.makedirs(d, exist_ok=True)
                for f in sorted(os.listdir(s)):
                    fp = os.path.join(s, f)
                    if os.path.isfile(fp):
                        shutil.copy2(fp, os.path.join(d, f))
            else:
                shutil.copytree(s, d, dirs_exist_ok=True)
    vault = os.path.join(REPO, "harness", "vault", "runs", run_id)
    for name in ("manifest.json", "transcript.jsonl"):
        p = os.path.join(vault, name)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(dest, name))
    with open(os.path.join(dest, "run_id.txt"), "w") as fh:
        fh.write(run_id + "\n")


def teardown(run_id):
    """Delete the sandbox. It is frozen read-only at the end of a run."""
    sb = os.path.join(REPO, "harness", "sandboxes", run_id)
    if not os.path.isdir(sb):
        return
    subprocess.run(["chmod", "-R", "u+w", sb], capture_output=True)
    shutil.rmtree(sb, ignore_errors=True)


def _log_text(path):
    """A case log as text, and it must never raise.

    `errors="replace"` is load-bearing rather than tidy. A case log is whatever the harness
    and the agent wrote to stdout, which is arbitrary bytes, and one invalid UTF-8 sequence
    made `open(path).read()` raise UnicodeDecodeError -- which is a ValueError, not an
    OSError, so the `except OSError` this used to carry did not catch it.

    That was survivable while the only reader was the under-a-minute branch. It stopped
    being survivable when the cap check started reading every case log to search it, which
    put a raise on the main path of every case. The message names no error, no exception
    and no traceback, so it does not even turn up in a grep for those.

    Everything read here is lowercased and substring-searched, so a replaced byte cannot
    change an answer.
    """
    try:
        with open(path, errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def run_one(case, participant, patient, block, outdir, extra):
    dest = os.path.join(OUT, outdir, case)
    if os.path.isdir(dest):
        return "skip", None

    persona = os.path.join(PERSONAS, case + ".json")
    logdir = os.path.join(OUT, outdir)
    os.makedirs(logdir, exist_ok=True)
    clog = os.path.join(logdir, case + ".log")

    # One iteration per attempt. A cap is the only thing that goes round again: the run it
    # capped was never banked, so re-running the case is the whole of the recovery and
    # there is nothing to clear first. Everything inside is re-checked on the retry,
    # including free disk, which an hours-long wait can have changed underneath us.
    while True:
        if _stop.is_set():
            return "stop", None

        if free_gb() < MIN_FREE_GB:
            _stop_reason.append("only %dGi free, floor is %dGi" % (free_gb(), MIN_FREE_GB))
            _stop.set()
            return "stop", None

        outcome = run_one_attempt(case, patient, block, extra, persona, logdir, clog, dest)
        if outcome != _RETRY:
            return outcome


# Returned by one attempt to mean "the cap has reset, go round again". Not a status the
# worker ever sees.
_RETRY = object()


def run_one_attempt(case, patient, block, extra, persona, logdir, clog, dest):
    # The run id is chosen here rather than by the harness, so it is known before
    # the process starts. Otherwise it is only readable once the case ENDS: the
    # harness prints it on its first line, and Python block-buffers to a file, so
    # that line sits in a 4KB buffer for the whole run. Nothing watching from
    # outside can tell which case a live sandbox belongs to until it is over,
    # which makes a status page much less useful than it should be.
    run_id = "run-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    with open(os.path.join(logdir, case + ".runid"), "w") as fh:
        fh.write(run_id + "\n")

    # Pinned full, not by alias. An alias moves when the line-up moves, and a model
    # change mid-corpus is the same problem as a spec change mid-corpus.
    #
    # --arm and --pass change nothing about the run. They tell the harness where to put the
    # four small files it writes for every run, so the result is findable at
    # results/<arm>/<pass>/<case>/ rather than only under a run id. Banking to answer-side/
    # below is unchanged and still separate: that is the bulk, this is the answer.
    arm, pass_name = arm_and_pass(block)
    cmd = ([sys.executable, os.path.join(REPO, "harness", "run_harness.py"), "run",
            "--run-id", run_id, "--model", MODEL, "--arm", arm, "--pass", pass_name,
            "--persona", persona, "--patient", patient] + extra)
    wait_for_launch_slot()
    t0 = time.time()
    with open(clog, "w") as fh:
        subprocess.run(cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT)
    mins = (time.time() - t0) / 60.0

    # Trust the harness's own line if it landed; fall back to what we asked for.
    # errors="replace" for the same reason as _log_text: this is arbitrary agent output,
    # and a decode error here would raise on the one line that identifies the run.
    try:
        with open(clog, errors="replace") as fh:
            for line in fh:
                if line.startswith("run id"):
                    run_id = line.split()[-1].strip()
                    break
    except OSError:
        pass

    m = manifest_of(run_id)
    outcome = m.get("outcome") or {}
    tail = (outcome.get("prs_stdout_tail") or "") + (outcome.get("prs_stderr_tail") or "")

    # Both the manifest's tails and the runner's own log for this case. A cap kills a run
    # in about nine seconds, which is early enough that the manifest can be thin or absent,
    # and the message is then only in the log. Searching one and not the other is how a cap
    # goes unrecognised.
    haystack = (tail + "\n" + _log_text(clog)).lower()

    # BOTH OF THE NEXT TWO CHECKS RUN BEFORE THE "died in seconds" ONE BELOW, and that
    # ordering is the fix rather than a detail. A cap kills a case in nine seconds, so the
    # under-a-minute branch used to return `fail` for every capped case and neither of
    # these was ever reached. On 2026-09-07 that is what let a dead cap burn pass 2 and
    # most of pass 3: the marker list was wrong AND the check it guarded was unreachable.
    # Dying fast is a symptom shared by a port collision, a dead token and a cap, so what
    # killed the run has to be read before the speed of it is.

    # A dead token cannot be fixed while nobody is awake, and every remaining
    # case would spend itself on it in seconds. Stop the whole batch.
    if (any(mk in haystack for mk in TOKEN_DEAD_MARKERS)
            or TOKEN_401_RE.search(haystack)):
        _stop_reason.append(
            "the token is dead (case %s). Sign in, THEN re-pin the CLI to 2.1.224, "
            "in that order, then run this again." % case)
        _stop.set()
        teardown(run_id)
        return "stop", None

    # A cap is not a dead token: there is nothing to sign in to and nothing to re-pin, and
    # unlike every other environment failure it clears itself at a stated time. So the
    # runner waits it out and runs the case again rather than ending the block and sitting
    # idle until somebody notices in the morning. Nothing was banked, so the retry needs no
    # cleanup. If the message does not say when it resets, or this block has waited its
    # three times, it falls back to what it always did and stops.
    if any(mk in haystack for mk in CAP_MARKERS):
        teardown(run_id)
        if wait_for_cap(case, haystack):
            return _RETRY
        if _stop.is_set():
            return "stop", None
        _stop_reason.append(
            "the account hit a cap on case %s and the runner did not wait it out: %s. "
            "Nothing was banked, so no case was lost -- what is still outstanding is "
            "exactly what to run once it resets." % (case, cap_refusal(haystack)))
        _stop.set()
        return "stop", None

    # A run that produced no manifest, or finished in under a minute, did not run.
    # Banking it would create a directory that looks complete, counts as done, and
    # is skipped forever afterwards because resume works by directory existence.
    # This is what a proxy port collision looked like: 0.0 minutes, rc=None, no
    # judgment file, banked as OK. Left unbanked it is simply retried.
    if not m:
        return "fail", "no manifest for %s (%.1fm); the harness raised, see %s" % (
            run_id, mins, os.path.basename(clog))
    if mins < 1.0:
        text = _log_text(clog)
        why = "unknown"
        if "Address already in use" in text:
            why = "proxy port collision"
        elif "Traceback" in text:
            why = text.strip().splitlines()[-1][:120]
        teardown(run_id)
        return "fail", "died in %.0fs: %s" % (mins * 60, why)

    # The harness has already decided whether this run happened or whether its environment
    # stopped it, and a run the environment stopped must not be banked here either.
    # answer-side/ resumes on directory existence exactly as results/ does, so banking one
    # would mark the case done and it would never be retried -- leaving the corpus holding
    # real results and unretried failures that look identical from outside. Unbanked, it is
    # simply picked up by the next run of this script.
    pub = m.get("published") or {}
    if pub.get("state") == "environment-failure":
        teardown(run_id)
        return "fail", "the environment stopped it, nothing banked: %s" % (
            pub.get("environment_failure") or "see the manifest")

    bank(run_id, dest)
    teardown(run_id)
    try:
        os.remove(os.path.join(logdir, case + ".runid"))
    except OSError:
        pass

    rc = outcome.get("prs_returncode")
    # The transcript's own count, which is the same source the harness's environment check
    # reads. `outcome.messages_routed` is not a key finish() has ever set, so this field
    # printed None for every case ever run -- and "how many messages did it route" is
    # exactly the number that separates a run which died before it said anything from one
    # that talked to the person and then failed.
    routed = (m.get("transcript") or {}).get("n_messages")
    honoured = (m.get("partial_run") or {}).get("honoured")
    choice = "?"
    # result.json is the run's answer and every run writes one. model_choice.json
    # is kept only so runs banked before 2026-09-05 still display.
    rp = os.path.join(dest, "result.json")
    cp = os.path.join(dest, "select", "model_choice.json")
    if os.path.isfile(rp):
        try:
            with open(rp) as fh:
                j = json.load(fh)
            choice = str(j.get("chosen_model") or j.get("outcome") or "none")[:40]
        except Exception:
            choice = "result.json unreadable"
    elif os.path.isfile(cp):
        try:
            with open(cp) as fh:
                j = json.load(fh)
            choice = str(j.get("chosen_model") or j.get("outcome") or "none")[:40] + " (old artifact)"
        except Exception:
            choice = "unreadable"
    elif block.startswith("arm0"):
        choice = "n/a (bare)"
    else:
        choice = "NO result.json"

    # What the harness did with results/, reported here rather than left in the manifest:
    # `exists` means that case was already filed and this run's copy was not written, which
    # is the same skip this function opens with and has to be as visible.
    return "ok", "rc=%s routed=%s honoured=%s %s results=%s (%.1fm, %dGi free)" % (
        rc, routed, honoured, choice, pub.get("state") or "?", mins, free_gb())


def worker(q, results):
    """One pool thread, and it may not end without saying why.

    A worker that stopped used to leave nothing behind. Three of the ways it can end are
    silent by construction:

      * `SystemExit` in a thread is swallowed by threading itself -- the thread dies and
        not one character reaches stderr, unlike every other exception, which at least
        prints `Exception in thread ...`.
      * anything raised outside the narrow try below -- in a say(), in results.append,
        in q.task_done -- was never caught at all.
      * returning normally because the block was stopped, which is correct but looks
        identical to the first two from outside.

    All three leave a pool quietly running short, and a pool of two where three were asked
    for looks exactly like a batch whose cases have got slower. So every exit is logged,
    and a death takes the block down with it rather than leaving the rest of the corpus to
    run at an unrecorded concurrency: the cases would still be graded, and nothing would
    say they were produced by a machine in a different state.
    """
    try:
        why = _work(q, results)
        say("[worker] exiting: %s" % why)
    except BaseException as exc:  # noqa: BLE001 - including SystemExit, deliberately
        tb = traceback.format_exc()
        # Stop the block FIRST, before trying to write anything down. If say() is itself
        # what broke -- a full disk, a closed stdout -- then logging raises a second time
        # inside this handler, and the thread dies exactly as silently as it did before
        # this existed. The stop is two assignments and cannot fail; the logging can.
        _stop_reason.append(
            "a worker thread died with %s: %s. Its traceback should be in the log above. "
            "The block was stopped rather than run on with a smaller pool, because a "
            "corpus produced at an unrecorded concurrency is not the corpus that was "
            "asked for." % (type(exc).__name__, exc))
        _stop.set()
        try:
            say("[worker] DIED: %s: %s" % (type(exc).__name__, exc))
            for line in tb.rstrip().splitlines():
                say("[worker]   %s" % line)
        except BaseException:  # noqa: BLE001
            # Last resort, and deliberately not say(): if the logger is the casualty,
            # stderr is the only channel left, and a bare thread death with no output at
            # all is the thing this whole handler exists to prevent.
            try:
                sys.stderr.write("worker died and could not log it: %s: %s\n%s\n"
                                 % (type(exc).__name__, exc, tb))
                sys.stderr.flush()
            except BaseException:  # noqa: BLE001
                pass


def _work(q, results):
    """The worker's actual loop. Returns why it stopped, for the caller to log."""
    while not _stop.is_set():
        try:
            item = q.get_nowait()
        except queue.Empty:
            return "the queue is empty, nothing left to take"
        case, participant, patient, block, outdir, extra, n, total = item
        say("[%s %d/%d] start %s" % (block, n, total, case))
        try:
            status, detail = run_one(case, participant, patient, block, outdir, extra)
        except Exception as exc:  # noqa: BLE001
            # One case going wrong is a case failure, not a pool failure: it is logged
            # and the worker takes the next one. Only something escaping THIS is fatal.
            status, detail = "fail", "%s: %s" % (type(exc).__name__, exc)
        if status == "ok":
            say("[%s %d/%d] OK   %s   %s" % (block, n, total, case, detail))
        elif status == "skip":
            say("[%s %d/%d] skip %s (already banked)" % (block, n, total, case))
        elif status == "stop":
            # With the reason, at the moment it happens. It used to be recorded only in
            # _stop_reason, which is not printed until every block has finished, so the
            # line that mattered arrived hours after the thing it explained.
            say("[%s %d/%d] STOPPING on %s -- %s"
                % (block, n, total, case,
                   _stop_reason[-1] if _stop_reason else "reason not recorded"))
        else:
            say("[%s %d/%d] FAIL %s   %s" % (block, n, total, case, detail))
        results.append((block, case, status))
        q.task_done()
    return "the block was stopped"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--cases")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    # First, ahead of reading the case list and ahead of --plan. A plan is what you run to
    # confirm the night will work, and a plan that says "97 cases to run" on a box that
    # cannot authenticate is precisely the false confidence this check exists to remove.
    # Exits with the remedy rather than warning: nobody reads a warning at 2am.
    token_source, token_line = auth.require_token()

    cases = load_cases()
    if args.cases:
        want = {c.strip() for c in args.cases.split(",")}
        cases = [r for r in cases if r[0] in want or r[0].split("-")[0] in want]
    blocks = [b for b in BLOCKS if not args.only or b[0] == args.only]

    global LOG
    LOG = os.path.join(OUT, "corpus.log")
    os.makedirs(OUT, exist_ok=True)

    todo = []
    for block, outdir, extra in blocks:
        pending = [r for r in cases
                   if not os.path.isdir(os.path.join(OUT, outdir, r[0]))]
        todo.append((block, outdir, extra, pending))

    print("cases in list        : %d" % len(cases))
    print("concurrency          : %d" % args.concurrency)
    # Every case is launched with subprocess.run and no env=, so each run_harness.py
    # inherits what require_token just put in this process's environment and finds it
    # already set.
    print("auth                 : %s (%s)" % (token_source, token_line))
    print("free disk            : %dGi (floor %dGi)" % (free_gb(), MIN_FREE_GB))
    print()
    total_pending = 0
    for block, outdir, _extra, pending in todo:
        print("%-12s -> answer-side/%-18s %3d to run, %3d already banked"
              % (block, outdir, len(pending), len(cases) - len(pending)))
        total_pending += len(pending)
    print()
    print("total runs outstanding: %d" % total_pending)
    print("at ~25 min a case and %d at a time, roughly %.0f hours"
          % (args.concurrency, total_pending * 25.0 / 60.0 / max(args.concurrency, 1)))

    if args.plan:
        print("\nplan only. nothing ran.")
        return

    if free_gb() < MIN_FREE_GB:
        sys.exit("\nonly %dGi free, floor is %dGi. nothing ran." % (free_gb(), MIN_FREE_GB))

    say("=" * 66)
    say("CORPUS RUN. %d outstanding, %d at a time." % (total_pending, args.concurrency))
    say("cli: %s" % (subprocess.run(["claude", "--version"], capture_output=True,
                                    text=True).stdout or "?").strip())

    results = []
    for block, outdir, extra, pending in todo:
        if _stop.is_set():
            break
        if not pending:
            say("%s: nothing outstanding, skipping" % block)
            continue
        say("")
        say("---- %s : %d case(s) ----" % (block, len(pending)))
        # Per block: three waits in pass 1 must not leave pass 2 with none.
        reset_cap_waits()
        q = queue.Queue()
        for i, (case, participant, patient) in enumerate(pending, start=1):
            q.put((case, participant, patient, block, outdir, extra, i, len(pending)))
        threads = [threading.Thread(target=worker, args=(q, results), daemon=True)
                   for _ in range(max(1, args.concurrency))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    say("")
    say("=" * 66)
    ok = sum(1 for _b, _c, s in results if s == "ok")
    fail = [(b, c) for b, c, s in results if s == "fail"]
    say("banked this session: %d" % ok)
    say("failed: %d" % len(fail))
    for b, c in fail:
        say("  %s %s" % (b, c))
    if _stop.is_set():
        say("")
        say("STOPPED EARLY: %s" % (_stop_reason[0] if _stop_reason else "unknown"))
        say("re-run the same command to pick up where this left off.")
    say("free disk: %dGi" % free_gb())
    say("")
    say("grade the pipeline blocks with grade.py --runs answer-side/eval-run-97-p1")
    say("grade the arm-0 block with grade.py --runs answer-side/arm0-run-97-p1")


if __name__ == "__main__":
    main()
