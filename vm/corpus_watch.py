#!/usr/bin/env python3
"""A small site for watching the corpus batch, readable from a phone.

    python3 corpus_watch.py --token something-unguessable
    python3 corpus_watch.py --token ... --no-transcripts
    python3 corpus_watch.py --token ... --bind 127.0.0.1

Four pages plus a detail view:

    /<token>            where the batch is, at a glance
    /<token>/cases      every case against every pass
    /<token>/case/<id>  one case: stage timings, artifacts, the conversation
    /<token>/grade      grade.py's verdicts, per block
    /<token>/storage    where the disk is going

READS ONLY. It cannot start or stop anything and never writes to answer-side.
Grading shells out to grade.py, which also only reads, and the result is cached
so refreshing does not re-grade 97 cases.

WHAT THE COLOURS MEAN

Four states, keyed on the banked directory because that is what resume reads:

    green   banked       done, with a usable result.json
    red     failed       banked, but the result is missing or malformed. A real
                         finding about that run, and the only state anyone owns
    amber   running      in flight now
    grey    outstanding  not banked: never attempted, or attempted and correctly
                         left unbanked because the environment stopped it

Red and grey used to be the same colour, and a usage cap that burned through two
passes therefore rendered 194 red squares under `0/97 - 97 failed`. Nothing had
failed. A status page that cries failure at work still to do is worse than no
status page, because it is believed once and then never again.

NO JAVASCRIPT. Every page is built on the server and refreshed by a meta tag. An
earlier client-rendered version came up blank on mobile Safari, which is the
worst failure a status page can have: it looks like the batch is gone when the
batch is fine.

WHAT IS EXPOSED

Case ids, verdicts, chosen model identifiers, stage timings, machine vitals and,
unless --no-transcripts is passed, the full persona conversations. Bound to
0.0.0.0 the only lock is the token in the URL. That is weak but proportionate for
a two-day batch and it is the only thing that works from a phone with nothing
installed. Use --bind 127.0.0.1 and an SSH tunnel if it is not.
"""

import argparse
import csv
import html
import json
import os
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.expanduser("~/CliGenAI-Lab/prs-agent-product")
OUT = os.path.join(REPO, "answer-side")
CASES = "/root/cases.tsv"
LOG = os.path.join(OUT, "corpus.log")
INDEX = os.path.join(REPO, "build", "sim", "personas", "eval", "index.md")

# The longest case seen so far ran 87 minutes. A case older than this with
# nothing banked has really died.
RUNNING_WINDOW_MIN = 120

BLOCKS = [
    ("pass 1", "eval-run-97-p1", "pipeline", "grade"),
    ("pass 2", "eval-run-97-p2", "pipeline", "grade"),
    ("pass 3", "eval-run-97-p3", "pipeline", "grade"),
    ("arm 0", "arm0-run-97-p1", "no ranking rules", "arms"),
]

SHOW_TRANSCRIPTS = True
_grade_cache = {}
_du_cache = {}

# The case scan stats 388 directories and, for the banked ones, opens a JSON
# each. On a phone that is the difference between a page that appears and one
# that hangs. Case state changes only when something banks, roughly every ten
# minutes, so a short cache costs nothing in freshness and makes the second tap
# instant. Fifteen seconds, not minutes: the page should never show something
# meaningfully older than the tab you came from.
_scan_cache = {"when": 0.0, "rows": None}
SCAN_CACHE_SECONDS = 15


def scan(read_choice=True):
    """state and choice for every case in every block, cached briefly."""
    if _scan_cache["rows"] is not None and \
            (time.time() - _scan_cache["when"]) < SCAN_CACHE_SECONDS:
        return _scan_cache["rows"]
    cases = case_list()
    live = running_now()
    live_ids = {r["run_id"] for r in live}
    rows = {"cases": cases, "live": live, "blocks": {}}
    for _label, outdir, _sub, _kind in BLOCKS:
        col = {}
        for c in cases:
            col[c] = banked_state(outdir, c, live_ids)
        rows["blocks"][outdir] = col
    _scan_cache.update({"when": time.time(), "rows": rows})
    return rows


# ------------------------------------------------------------------ reading

def case_list():
    rows = []
    try:
        with open(CASES) as fh:
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if len(p) == 3:
                    rows.append(p[0])
    except OSError:
        return []
    rows.sort(key=lambda c: int(re.match(r"p(\d+)", c).group(1)))
    return rows


def case_index():
    out = {}
    try:
        for line in open(INDEX):
            if not line.startswith("| p"):
                continue
            c = [x.strip() for x in line.split("|")]
            out[c[1]] = {"participant": c[2], "file": c[3], "trait": c[4],
                         "expected": c[10] if len(c) > 10 else ""}
    except OSError:
        pass
    return out


def running_now():
    sb = os.path.join(REPO, "harness", "sandboxes")
    out = []
    try:
        names = os.listdir(sb)
    except OSError:
        return out
    for n in names:
        p = os.path.join(sb, n)
        if not os.path.isdir(p):
            continue
        patient = ""
        try:
            for f in sorted(os.listdir(os.path.join(p, "prs"))):
                if f.endswith((".txt", ".zip", ".vcf", ".gz", ".csv", ".pdf")):
                    patient = f
                    break
        except OSError:
            pass
        out.append({"run_id": n, "patient": patient,
                    "minutes": (time.time() - os.path.getctime(p)) / 60.0,
                    "started": os.path.getctime(p)})
    out.sort(key=lambda r: r["started"], reverse=True)
    for r in out:
        case, outdir = case_for_run(r["run_id"])
        r["case"], r["block"] = case, outdir
        r.update(live_progress(r["run_id"]))
    return out


def run_id_in_log(outdir, case):
    """The run id for a case, from the marker the runner writes before it starts.

    The harness prints the id on its first line, but Python block-buffers to a
    file, so that line does not reach disk until the process exits. Reading only
    the log means a live case cannot be named until it is finished. So the runner
    now picks the id itself and drops a <case>.runid beside the log the moment it
    launches; the log is the fallback for anything banked before that change.
    """
    marker = os.path.join(OUT, outdir, case + ".runid")
    try:
        with open(marker) as fh:
            v = fh.read().strip()
        if v:
            return v
    except OSError:
        pass
    try:
        with open(os.path.join(OUT, outdir, case + ".log")) as fh:
            for line in fh:
                if line.startswith("run id"):
                    return line.split()[-1].strip()
    except OSError:
        pass
    return None


def choice_of(d):
    cp = os.path.join(d, "select", "model_choice.json")
    if not os.path.isfile(cp):
        return None
    try:
        with open(cp) as fh:
            j = json.load(fh)
        return str(j.get("chosen_model") or j.get("outcome") or "")[:34]
    except Exception:
        return "unreadable"


def result_state(d):
    """Did this banked case produce a usable result? "banked" or "failed", and why.

    Presence and shape only. Whether the run was RIGHT is grade.py's question and is
    never answered here; this asks the far smaller one the runner itself asks, which is
    whether there is an answer at all.

    Read from result.json rather than from the manifest's own `result.valid` verdict,
    which would be the more authoritative source. The manifest is 250-320 KB and this
    runs for 388 cases on every scan; the result file is a few hundred bytes. That is the
    difference between a page that appears on a phone and one that hangs, which is the
    worst failure a status page can have.

    A directory banked before result.json existed reads as failed, correctly: every run
    in this corpus writes one, so its absence is a finding rather than an era.
    """
    p = os.path.join(d, "result.json")
    if not os.path.isfile(p):
        return "failed", "no result.json"
    try:
        with open(p) as fh:
            j = json.load(fh)
    except (OSError, ValueError):
        return "failed", "result.json unreadable"
    if not isinstance(j, dict) or not j.get("outcome"):
        return "failed", "result.json names no outcome"
    return "banked", None


def banked_state(outdir, case, live_ids=()):
    """One of banked, failed, running, outstanding -- and they are three different things.

    THE DIRECTORY IS THE FACT, because the directory is what resume reads. A case with a
    banked directory has been done and will not be attempted again; a case without one is
    still on the list whatever happened to it. So:

      banked       the directory is there and holds a usable result. Done.
      failed       the directory is there and the result is missing or malformed. A real
                   finding about that run, and the only state that is anybody's fault.
      running      no directory yet, but something is in flight for it right now.
      outstanding  no directory. Never attempted, or attempted and deliberately not
                   banked because the environment stopped it -- a dead token, a usage
                   cap, a run that died before it said anything. NOT a failure: the
                   runner is supposed to leave those unbanked so a resume picks them up.

    That last one used to return "failed", and it is why a cap that burned through pass 2
    and pass 3 painted 97 red squares under `0/97 - 97 failed`. Nothing had failed. Those
    cases had been correctly left for the next run.
    """
    d = os.path.join(OUT, outdir, case)
    if os.path.isdir(d):
        state, why = result_state(d)
        detail = choice_of(d) if state == "banked" else why
        return state, detail, os.path.getmtime(d)
    marker = os.path.join(OUT, outdir, case + ".runid")
    log = os.path.join(OUT, outdir, case + ".log")
    if os.path.isfile(marker) or os.path.isfile(log):
        started = os.path.getmtime(marker if os.path.isfile(marker) else log)
        age = (time.time() - started) / 60.0
        rid = run_id_in_log(outdir, case)
        # A running case usually has an EMPTY log: Python block-buffers when
        # stdout is a file, so the harness's first line does not reach disk until
        # the process exits. The run id is therefore only readable once the case
        # is over, and age is the honest signal while it is in flight. Without
        # this every running case reads as a failure.
        if (rid and rid in live_ids) or age < RUNNING_WINDOW_MIN:
            return "running", None, started
        # Attempted, banked nothing, not running. `started` is kept so the block
        # header can tell "tried and left for later" from "never touched".
        return "outstanding", None, started
    return "outstanding", None, None


def ledger_stages(d):
    """Per-stage wall time, from the ledger's own timestamps."""
    p = os.path.join(d, "ledger.jsonl")
    rows = []
    try:
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        return []
    order, seen = [], {}
    for r in rows:
        st, ts = r.get("stage") or "?", r.get("ts")
        if not ts:
            continue
        try:
            t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            continue
        if st not in seen:
            seen[st] = {"stage": st, "first": t, "last": t, "tools": [], "n": 0}
            order.append(st)
        s = seen[st]
        s["first"], s["last"] = min(s["first"], t), max(s["last"], t)
        s["n"] += 1
        tool = r.get("tool")
        if tool and tool not in s["tools"]:
            s["tools"].append(tool)
    stages = [seen[s] for s in order]
    for s in stages:
        s["minutes"] = (s["last"] - s["first"]) / 60.0
    return stages


def artifacts(d):
    out = []
    for stage in ("door", "ancestry", "select", "session"):
        sd = os.path.join(d, stage)
        if not os.path.isdir(sd):
            continue
        files = []
        for f in sorted(os.listdir(sd)):
            fp = os.path.join(sd, f)
            if os.path.isfile(fp):
                files.append((f, os.path.getsize(fp)))
        if files:
            out.append((stage, files, sum(b for _f, b in files)))
    return out


def read_transcript(path, limit=400):
    rows = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        return []
    # person->agent repeats persona->harness verbatim; keep one of each exchange
    return [r for r in rows if r.get("direction") != "persona->harness"][:limit]


def grade_block(outdir, kind, max_age=300):
    """grade.py's own verdicts, cached. Never re-implemented here."""
    hit = _grade_cache.get(outdir)
    if hit and (time.time() - hit["when"]) < max_age:
        return hit["rows"], hit["err"]
    d = os.path.join(OUT, outdir)
    rows, err = [], None
    dirs = [x for x in os.listdir(d)
            if os.path.isdir(os.path.join(d, x))] if os.path.isdir(d) else []
    if not dirs:
        err = "nothing banked yet"
    else:
        script = "grade.py"
        flag = "--runs"
        tmp = "/tmp/.watch-%s.csv" % outdir
        try:
            subprocess.run(["python3", os.path.join(REPO, script), flag,
                            os.path.join("answer-side", outdir), "--csv", tmp],
                           cwd=REPO, capture_output=True, text=True, timeout=240)
            if os.path.isfile(tmp) and os.path.getsize(tmp):
                with open(tmp, newline="") as fh:
                    rows = list(csv.DictReader(fh))
        except subprocess.TimeoutExpired:
            err = "grade.py took longer than four minutes"
        except Exception as exc:  # noqa: BLE001
            err = str(exc)[:200]
    _grade_cache[outdir] = {"rows": rows, "err": err, "when": time.time()}
    return rows, err


def vitals():
    st = os.statvfs("/")
    free = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    total = (st.f_blocks * st.f_frsize) / (1024 ** 3)
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = 0.0
    mem = None
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                mem = int(line.split()[1]) / (1024.0 ** 2)
                break
    except OSError:
        pass
    n = 0
    try:
        r = subprocess.run(["pgrep", "-c", "claude"], capture_output=True, text=True)
        n = int((r.stdout or "0").strip() or 0)
    except Exception:
        pass
    return {"free_gb": free, "total_gb": total, "load": load,
            "mem_free_gb": mem, "n_claude": n}


def du(path, max_age=180):
    """Bytes under a path, cached. du on refdata takes seconds and the answer
    barely moves, so it is not worth doing on every page load."""
    hit = _du_cache.get(path)
    if hit and (time.time() - hit[1]) < max_age:
        return hit[0]
    try:
        r = subprocess.run(["du", "-sb", path], capture_output=True, text=True,
                           timeout=180)
        b = int((r.stdout or "0").split()[0])
    except Exception:
        b = 0
    _du_cache[path] = (b, time.time())
    return b


def live_progress(run_id):
    """What a running case is doing right now, from its own ledger.

    The ledger is written inside the sandbox at
    prs/runs/<run>/ledger.jsonl and is appended as each tool finishes, so its
    last line is the most recent thing that actually happened: which stage, which
    tool, and when. That is a better answer than "a sandbox is open", which is
    all the directory listing can tell you.

    Which stage directories exist is a second, coarser signal, and it is kept
    because a stage can be created before its first tool call lands in the
    ledger.
    """
    base = os.path.join(REPO, "harness", "sandboxes", run_id, "prs", "runs", run_id)
    out = {"stage": None, "tool": None, "since_min": None, "stages": []}
    try:
        out["stages"] = sorted(d for d in os.listdir(base)
                               if os.path.isdir(os.path.join(base, d))
                               and d not in ("env", "toolshim"))
    except OSError:
        return out
    last = None
    try:
        with open(os.path.join(base, "ledger.jsonl")) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except ValueError:
                        pass
    except OSError:
        pass
    if last:
        out["stage"] = last.get("stage")
        out["tool"] = last.get("tool") or last.get("kind")
        ts = last.get("ts")
        if ts:
            try:
                t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                out["since_min"] = (time.time() - t) / 60.0
            except ValueError:
                pass
    return out


def case_for_run(run_id):
    """Which case a live run belongs to.

    Reads the .runid markers the runner drops at launch, so a case is nameable
    from its first second rather than only once it has finished.
    """
    for _label, outdir, _s, _k in BLOCKS:
        d = os.path.join(OUT, outdir)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".runid"):
                case = f[:-6]
            elif f.endswith(".log"):
                case = f[:-4]
            else:
                continue
            if run_id_in_log(outdir, case) == run_id:
                return case, outdir
    return None, None


def stage_order_hint(stages):
    """Stage directories in the order the pipeline runs them, not alphabetical."""
    order = ["door", "ancestry", "select", "score", "interpret", "report", "session"]
    return [s for s in order if s in stages] + [s for s in stages if s not in order]


def network_pulse(run_id, bins=30, seconds_per_bin=60):
    """Connections per minute for one live run, newest bin last.

    The proxy writes one line per connection to vault/runs/<id>/network.jsonl,
    allowed and denied alike. A run that is thinking looks identical to a run
    that has hung from the outside; this is the difference.
    """
    p = os.path.join(REPO, "harness", "vault", "runs", run_id, "network.jsonl")
    now = time.time()
    counts = [0] * bins
    allowed = denied = 0
    last = None
    try:
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                ev = r.get("event") or ""
                if ev.startswith("proxy_"):
                    continue
                if ev == "deny":
                    denied += 1
                else:
                    allowed += 1
                ts = r.get("ts") or r.get("time")
                t = None
                if ts:
                    try:
                        t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                    except ValueError:
                        t = None
                if t is None:
                    continue
                last = t if last is None else max(last, t)
                age = now - t
                idx = bins - 1 - int(age // seconds_per_bin)
                if 0 <= idx < bins:
                    counts[idx] += 1
    except OSError:
        return None
    return {"counts": counts, "allowed": allowed, "denied": denied,
            "quiet_min": ((now - last) / 60.0) if last else None,
            "per_min": counts[-1]}


def spark(counts, height=26):
    peak = max(counts) or 1
    o = ["<div style='display:flex;gap:1px;align-items:flex-end;height:%dpx;"
         "background:var(--panel);padding:4px;border-radius:3px'>" % (height + 8)]
    for c in counts:
        h = max(1, round(height * c / peak))
        col = "var(--empty)" if c == 0 else "var(--C)"
        o.append("<i style='flex:1 1 0;min-width:1px;height:%dpx;background:%s;"
                 "border-radius:1px' title='%d'></i>" % (h, col, c))
    o.append("</div>")
    return "".join(o)


def batch_alive():
    """Is the runner actually running?

    The page used to decide this by grepping the log tail for STOPPED EARLY,
    which is wrong in the one case that matters: after a restart the old stop
    message is still inside the tail window, so a healthy batch reads as stopped
    while simultaneously showing three cases in flight. Two contradictory facts
    on the same screen is worse than either alone, because it teaches you to
    distrust the page.

    The process either exists or it does not. Ask the kernel.
    """
    try:
        r = subprocess.run(["pgrep", "-f", "run_corpus.py"],
                           capture_output=True, text=True, timeout=10)
        return bool((r.stdout or "").strip())
    except Exception:
        return None  # unknown, which is not the same as stopped


def log_tail(n=14):
    try:
        with open(LOG) as fh:
            return [l.rstrip("\n") for l in fh.readlines()[-n:]]
    except OSError:
        return []


# ------------------------------------------------------------------ rendering

CSS = """
:root{--ground:#0F1519;--panel:#162026;--rule:#22323A;--ink:#DCE6EA;
      --dim:#7B939E;--faint:#4A5F69;--A:#4CAF6D;--G:#E3A03C;--T:#D6534A;
      --C:#4E9FD1;--empty:#1E2A31;--O:#33454E}
*{box-sizing:border-box}
html,body{margin:0;background:var(--ground);color:var(--ink)}
body{font:400 15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
     padding:0 16px calc(48px + env(safe-area-inset-bottom));
     -webkit-text-size-adjust:100%}
a{color:var(--C);text-decoration:none}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
      font-variant-numeric:tabular-nums}
nav{display:flex;gap:2px;margin:0 -16px 0;padding:0 16px;overflow-x:auto;
    border-bottom:1px solid var(--rule);position:sticky;top:0;
    background:var(--ground);z-index:3;-webkit-overflow-scrolling:touch}
nav a{padding:13px 12px;font-size:13px;color:var(--dim);white-space:nowrap;
      border-bottom:2px solid transparent}
nav a.on{color:var(--ink);border-bottom-color:var(--C)}
header{padding:20px 0 14px}
.state{display:flex;align-items:baseline;gap:10px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--A);flex:none;
     transform:translateY(-1px)}
.dot.warn{background:var(--G)}.dot.bad{background:var(--T)}
h1{font:600 19px/1.2 system-ui;margin:0;letter-spacing:-.01em}
.sub{color:var(--dim);font-size:13px;margin-top:4px}
.count{margin-left:auto;font-size:13px;color:var(--dim)}
.eyebrow{font-size:11px;letter-spacing:.12em;text-transform:uppercase;
         color:var(--faint);margin:24px 0 10px}
.track{margin:0 0 18px}
.trackhead{display:flex;align-items:baseline;gap:8px;margin-bottom:7px}
.trackhead b{font:600 15px system-ui}
.trackhead .of{color:var(--dim);font-size:13px}
.trackhead .pct{margin-left:auto;font-size:13px;color:var(--dim)}
.pile{display:flex;gap:1px;height:26px;background:var(--panel);padding:4px;
      border-radius:3px;overflow:hidden}
.tick{flex:1 1 0;min-width:1px;background:var(--empty);border-radius:1px}
.tick.banked{background:var(--A)}.tick.failed{background:var(--T)}
.tick.running{background:var(--G)}
/* Outstanding is a state, not an absence, so it is drawn rather than left blank --
   muted enough that a wall of it never reads as a wall of failures. */
.tick.outstanding{background:var(--O)}
.recent{margin-top:7px;font-size:12.5px;color:var(--dim);
        display:flex;flex-wrap:wrap;gap:4px 14px}
.recent b{color:var(--ink);font-weight:500}
.panel{background:var(--panel);border-radius:4px;padding:12px 14px}
.row{display:flex;gap:10px;align-items:baseline;padding:4px 0}
.row .id{color:var(--dim);font-size:12.5px}
.row .r{margin-left:auto;font-size:13px;color:var(--G)}
.vitals{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;
        background:var(--rule);border-radius:4px;overflow:hidden}
.vital{background:var(--panel);padding:12px 14px}
.vital .k{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--faint)}
.vital .v{font-size:21px;margin-top:3px}
.vital .v small{font-size:12px;color:var(--dim);margin-left:3px}
.vital.alarm .v{color:var(--T)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font:500 11px system-ui;letter-spacing:.08em;
   text-transform:uppercase;color:var(--faint);padding:6px 4px;
   border-bottom:1px solid var(--rule)}
td{padding:7px 4px;border-bottom:1px solid var(--rule);vertical-align:top}
td.c{text-align:center;width:36px}
.pill{display:inline-block;min-width:20px;padding:2px 5px;border-radius:3px;
      font:500 11px ui-monospace,monospace;background:var(--empty);color:var(--faint)}
.pill.pass{background:rgba(76,175,109,.16);color:var(--A)}
.pill.fail{background:rgba(214,83,74,.16);color:var(--T)}
.pill.ungr,.pill.run{background:rgba(227,160,60,.16);color:var(--G)}
.bar{height:6px;background:var(--empty);border-radius:3px;overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;background:var(--C)}
.msg{margin:0 0 12px;padding:11px 13px;border-radius:8px;font-size:14px;
     line-height:1.55;white-space:pre-wrap;word-break:break-word}
.msg.agent{background:var(--panel);border-left:2px solid var(--C)}
.msg.person{background:#17281F;border-left:2px solid var(--A)}
.msg .who{font:500 10.5px system-ui;letter-spacing:.1em;text-transform:uppercase;
          color:var(--faint);margin-bottom:5px;display:flex;gap:8px}
.msg .who span{margin-left:auto;font-weight:400}
pre{background:var(--panel);border-radius:4px;padding:12px;overflow-x:auto;
    font-size:11.5px;line-height:1.65;color:var(--dim);margin:0;white-space:pre;
    -webkit-overflow-scrolling:touch}
footer{margin-top:26px;color:var(--faint);font-size:11.5px}
.note{color:var(--dim);font-size:12.5px;margin:8px 0 0}
"""


def e(x):
    return html.escape(str(x if x is not None else ""))


def human(b):
    b = float(b)
    for unit in ("B", "K", "M", "G"):
        if b < 1024 or unit == "G":
            return ("%.0f%s" % (b, unit)) if unit == "B" else ("%.1f%s" % (b, unit))
        b /= 1024.0
    return "%.1fG" % b


def shell(token, page, body, refresh=20):
    nav = [("", "now"), ("live", "live"), ("cases", "cases"),
           ("grade", "grading"), ("storage", "storage")]
    o = ["<!doctype html><html lang=en><head><meta charset=utf-8>",
         "<meta name=viewport content='width=device-width,initial-scale=1,"
         "viewport-fit=cover'>"]
    if refresh:
        o.append("<meta http-equiv=refresh content=%d>" % refresh)
    o.append("<meta name=color-scheme content=dark><title>corpus</title>")
    o.append("<style>%s</style></head><body><nav>" % CSS)
    for slug, label in nav:
        href = "/%s%s" % (token, ("/" + slug) if slug else "")
        o.append("<a href='%s'%s>%s</a>"
                 % (href, " class=on" if page == slug else "", e(label)))
    o.append("</nav>")
    o.append(body)
    o.append("<footer>reads only &middot; %s</footer></body></html>"
             % ("refreshes every %ds" % refresh if refresh else "not refreshing"))
    return "".join(o)


def render_live_case(r, big=False):
    """One running case: which case it is, which stage, and its network pulse."""
    title = r.get("case") or r.get("patient") or r["run_id"][-13:]
    o = ["<div class=panel style='margin-bottom:10px'><div class=row>"
         "<b style='font:500 14px system-ui'>%s</b>"
         "<span class='r mono'>%dm</span></div>" % (e(title), round(r["minutes"]))]

    stage = r.get("stage")
    if stage:
        since = r.get("since_min")
        o.append("<div class=row style='padding-top:2px'>"
                 "<span class='pill run'>%s</span>"
                 "<span class=id>%s</span>"
                 "<span class='r mono' style='color:var(--dim)'>%s</span></div>"
                 % (e(stage), e(r.get("tool") or ""),
                    ("%dm ago" % round(since)) if since is not None else ""))

    done = stage_order_hint(r.get("stages") or [])
    if done:
        o.append("<div class=recent style='margin-top:6px'>")
        for st in done:
            on = (st == stage)
            o.append("<span style='color:%s'>%s</span>"
                     % ("var(--G)" if on else "var(--faint)", e(st)))
        o.append("</div>")

    if not r.get("case"):
        o.append("<div class=note>%s &middot; the case name is not readable until the "
                 "run ends, because the harness's output is block-buffered to a "
                 "file</div>" % e(r["run_id"][-13:]))

    pulse = network_pulse(r["run_id"])
    if pulse:
        o.append(spark(pulse["counts"], height=40 if big else 26))
        q = pulse["quiet_min"]
        o.append("<div class=note>%d calls this minute &middot; %d allowed, %d denied%s"
                 "</div>" % (pulse["per_min"], pulse["allowed"], pulse["denied"],
                             (" &middot; <b style='color:var(--G)'>silent for %d min</b>"
                              % round(q)) if q and q > 5 else ""))
    o.append("</div>")
    return "".join(o)


def page_overview(token):
    sc = scan()
    cases, live = sc["cases"], sc["live"]
    tracks = []
    for label, outdir, sub, _k in BLOCKS:
        marks, banked, failed, running, outstanding, recent = [], 0, 0, 0, 0, []
        touched = False
        col = sc["blocks"][outdir]
        for c in cases:
            state, ch, when = col[c]
            marks.append((c, state))
            # Anything with a timestamp has been attempted at some point, banked or
            # not. That is what separates a block the runner has not reached from one
            # it worked on and left unbanked.
            if when is not None:
                touched = True
            if state == "banked":
                banked += 1
                if ch:
                    recent.append((c, ch, when or 0))
            elif state == "failed":
                failed += 1
            elif state == "running":
                running += 1
            else:
                outstanding += 1
        recent.sort(key=lambda x: x[2], reverse=True)
        tracks.append((label, sub, marks, banked, failed, running, outstanding,
                       touched, len(cases), recent[:6]))

    tail = log_tail()
    alive = batch_alive()
    # Only trust the log's stop message when the process really is gone. After a
    # restart the old message lingers in the tail for a while.
    stopped = (alive is False) and any(
        "STOPPING" in l or "STOPPED EARLY" in l for l in tail)
    last = os.path.getmtime(LOG) if os.path.isfile(LOG) else None
    quiet = ((time.time() - last) / 60.0) if last else None
    done = sum(t[3] for t in tracks)
    allc = sum(t[6] for t in tracks)
    pct = round(100.0 * done / allc) if allc else 0

    if stopped:
        mood, word = "bad", "stopped"
    elif alive is False and pct >= 100:
        mood, word = "", "finished"
    elif alive is False:
        mood, word = "bad", "the runner is not running"
    elif not live and quiet and quiet > 45:
        mood, word = "warn", "quiet for %d min" % round(quiet)
    elif not live:
        mood, word = "warn", "between cases"
    else:
        mood, word = "", "running"

    o = ["<header><div class=state><span class='dot %s'></span><h1>%s</h1>"
         "<span class='count mono'>%d / %d</span></div>"
         "<div class=sub>%d in flight &middot; %d%% banked &middot; %s</div></header>"
         % (mood, e(word), done, allc, len(live), pct, time.strftime("%H:%M:%S"))]

    if stopped:
        o.append("<div class=panel style='border:1px solid var(--T)'>"
                 "<b style='color:var(--T)'>the batch stopped early</b>"
                 "<div class=note>If the token was revoked: sign in, then re-pin the "
                 "CLI to 2.1.224, in that order, then run the same command again. It "
                 "resumes where it left off.</div></div>")

    o.append("<div class=eyebrow>passes</div>")
    for (label, sub, marks, banked, failed, running, outstanding, touched,
         total, recent) in tracks:
        p = round(100.0 * banked / total) if total else 0
        # A block the runner has not reached says so, rather than counting 97 of
        # something. Only real failures are ever called failures.
        if not touched:
            bits = ["not started"]
        else:
            bits = []
            if running:
                bits.append("%d running" % running)
            if failed:
                bits.append("%d failed" % failed)
            if outstanding:
                bits.append("%d outstanding" % outstanding)
        extra = ("".join(" &middot; " + b for b in bits))
        o.append("<div class=track><div class=trackhead><b>%s</b>"
                 "<span class=of>%s</span><span class='pct mono'>%d/%d%s &nbsp;%d%%"
                 "</span></div><div class=pile>"
                 % (e(label), e(sub), banked, total, extra, p))
        for c, state in marks:
            o.append("<i class='tick %s' title='%s'></i>" % (state, e(c)))
        o.append("</div>")
        if recent:
            o.append("<div class=recent>")
            for c, ch, _w in recent:
                o.append("<span><a href='/%s/case/%s'>%s</a> <b>%s</b></span>"
                         % (token, e(c), e(c.split("-")[0]), e(ch)))
            o.append("</div>")
        o.append("</div>")

    o.append("<div class=eyebrow>running now</div>")
    if live:
        for r in live:
            o.append(render_live_case(r))
    else:
        o.append("<div class=panel><div class=row><span class=id>no sandbox open"
                 "</span></div></div>")

    v = vitals()
    o.append("<div class=eyebrow>machine</div><div class=vitals>")
    for k, val, unit, alarm in (
            ("disk free", "%.0f" % v["free_gb"],
             "Gi of %.0f" % v["total_gb"], v["free_gb"] < 25),
            ("memory free", "%.1f" % (v["mem_free_gb"] or 0), "Gi",
             (v["mem_free_gb"] or 9) < 2),
            ("load", "%.2f" % v["load"], "of 8", v["load"] > 12),
            ("agents", v["n_claude"], "processes", False)):
        o.append("<div class='vital%s'><div class=k>%s</div><div class='v mono'>%s"
                 "<small>%s</small></div></div>"
                 % (" alarm" if alarm else "", e(k), e(val), e(unit)))
    o.append("</div>")
    o.append("<div class=eyebrow>log</div><pre>%s</pre>"
             % e("\n".join(tail) or "nothing yet"))
    return shell(token, "", "".join(o))


def page_live(token):
    """Only the things that actually change second to second.

    Deliberately narrow. The expensive part of this site is the case scan, which
    stats 388 directories, and case state changes about once every ten minutes,
    so refreshing it every two seconds would spend the machine's attention on
    nothing. What does move is the proxy log and the kernel's own counters, and
    reading those is a file tail and a statvfs.

    Cost per load is a few milliseconds against three agents that are mostly
    waiting on the network, so this cannot slow the batch down.
    """
    live = running_now()
    v = vitals()
    o = ["<header><h1>live</h1><div class=sub>network and machine only, every two "
         "seconds &middot; %s</div></header>" % time.strftime("%H:%M:%S")]

    if not live:
        o.append("<div class=panel><span class=id>no sandbox open</span></div>")
    for r in live:
        o.append(render_live_case(r, big=True))

    o.append("<div class=eyebrow>machine</div><div class=vitals>")
    for k, val, unit, alarm in (
            ("disk free", "%.1f" % v["free_gb"],
             "Gi of %.0f" % v["total_gb"], v["free_gb"] < 25),
            ("memory free", "%.1f" % (v["mem_free_gb"] or 0), "Gi",
             (v["mem_free_gb"] or 9) < 2),
            ("load", "%.2f" % v["load"], "of 8", v["load"] > 12),
            ("agents", v["n_claude"], "processes", False)):
        o.append("<div class='vital%s'><div class=k>%s</div><div class='v mono'>%s"
                 "<small>%s</small></div></div>"
                 % (" alarm" if alarm else "", e(k), e(val), e(unit)))
    o.append("</div>")
    o.append("<div class=note>Each bar is one minute of proxy connections, thirty "
             "minutes wide, newest on the right. A run that is thinking looks the same "
             "as a run that has hung from anywhere else; this is where the difference "
             "shows.</div>")
    return shell(token, "live", "".join(o), refresh=2)


def page_cases(token):
    sc = scan()
    cases = sc["cases"]
    idx = case_index()
    o = ["<header><h1>cases</h1><div class=sub>%d cases, one row each, one column "
         "per pass. Tap a case for its stages and its conversation.</div></header>"
         % len(cases), "<table><tr><th>case</th><th>trait</th>"]
    for label, _od, _s, _k in BLOCKS:
        o.append("<th class=c>%s</th>" % e(label.replace("pass ", "p")))
    o.append("</tr>")
    glyph = {"banked": "&#9679;", "failed": "&times;", "running": "&#9679;",
             "outstanding": "&middot;"}
    cls = {"banked": "pass", "failed": "fail", "running": "run", "outstanding": ""}
    for c in cases:
        meta = idx.get(c, {})
        o.append("<tr><td><a href='/%s/case/%s'>%s</a></td>"
                 "<td style='color:var(--dim)'>%s</td>"
                 % (token, e(c), e(c.split("-")[0]), e(meta.get("trait", ""))))
        for _label, outdir, _s, _k in BLOCKS:
            state, ch, _w = sc["blocks"][outdir][c]
            o.append("<td class=c><span class='pill %s' title='%s'>%s</span></td>"
                     % (cls[state], e(ch or state), glyph[state]))
        o.append("</tr>")
    o.append("</table>")
    return shell(token, "cases", "".join(o), refresh=60)


def page_case(token, case):
    idx = case_index().get(case, {})
    live = {r["run_id"]: r for r in running_now()}
    o = ["<header><h1>%s</h1>"
         "<div class=sub>%s &middot; %s &middot; expected %s</div>"
         "<div class=sub>%s</div></header>"
         % (e(case), e(idx.get("participant", "?")), e(idx.get("trait", "?")),
            e(idx.get("expected", "?")), e(idx.get("file", "")))]

    newest_dir, newest_when, live_run = None, 0.0, None
    o.append("<div class=eyebrow>passes</div><div class=panel>")
    for label, outdir, _s, _k in BLOCKS:
        state, ch, when = banked_state(outdir, case, set(live))
        # A failed bank is still a directory full of evidence, and it is the one you
        # most want to open. Its stages and artifacts are shown like any other.
        if state in ("banked", "failed") and (when or 0) > newest_when:
            newest_dir, newest_when = os.path.join(OUT, outdir, case), when or 0
        if state == "running":
            rid = run_id_in_log(outdir, case)
            if rid and rid in live:
                live_run = rid
        cls = {"banked": "pass", "failed": "fail", "running": "run"}.get(state, "")
        o.append("<div class=row><span class=id>%s</span>"
                 "<span class='pill %s'>%s</span>"
                 "<span class='r mono' style='color:var(--ink)'>%s</span></div>"
                 % (e(label), cls, e(state), e(ch or "")))
    o.append("</div>")

    if newest_dir:
        stages = ledger_stages(newest_dir)
        if stages:
            total = sum(s["minutes"] for s in stages) or 1.0
            o.append("<div class=eyebrow>stages, most recent banked pass</div>"
                     "<div class=panel>")
            for s in stages:
                o.append("<div style='padding:7px 0'>"
                         "<div class=row><b style='font:500 14px system-ui'>%s</b>"
                         "<span class='r mono'>%.1fm</span></div>"
                         "<div class=bar><i style='width:%.1f%%'></i></div>"
                         "<div class=note>%d tool calls &middot; %s</div></div>"
                         % (e(s["stage"]), s["minutes"],
                            100.0 * s["minutes"] / total, s["n"],
                            e(", ".join(s["tools"][:6]))))
            o.append("</div><div class=note>Wall time from the ledger's own "
                     "timestamps, first tool call to last within each stage. The gaps "
                     "between stages are the agent deciding rather than a tool running "
                     "and are not counted here, so these do not sum to the case's "
                     "elapsed time.</div>")

        arts = artifacts(newest_dir)
        if arts:
            o.append("<div class=eyebrow>artifacts</div><div class=panel>")
            for stage, files, total_b in arts:
                o.append("<div class=row><b style='font:500 14px system-ui'>%s</b>"
                         "<span class='r mono' style='color:var(--ink)'>%s</span></div>"
                         % (e(stage), human(total_b)))
                for f, b in files[:14]:
                    o.append("<div class=row><span class='id mono'>%s</span>"
                             "<span class='r mono' style='color:var(--dim)'>%s</span>"
                             "</div>" % (e(f), human(b)))
            o.append("</div>")

    if SHOW_TRANSCRIPTS:
        if live_run:
            rows = read_transcript(os.path.join(REPO, "harness", "vault", "runs",
                                                live_run, "transcript.jsonl"))
        elif newest_dir:
            rows = read_transcript(os.path.join(newest_dir, "transcript.jsonl"))
        else:
            rows = []
        if live_run:
            pulse = network_pulse(live_run)
            if pulse:
                o.append("<div class=eyebrow>network, last 30 minutes</div>")
                o.append(spark(pulse["counts"], height=34))
                q = pulse["quiet_min"]
                o.append("<div class=note>%d calls this minute &middot; %d allowed, "
                         "%d denied%s</div>"
                         % (pulse["per_min"], pulse["allowed"], pulse["denied"],
                            (" &middot; silent for %d min" % round(q))
                            if q and q > 5 else ""))
        o.append("<div class=eyebrow>conversation%s</div>"
                 % (" &middot; live" if live_run else ""))
        if not rows:
            o.append("<div class=panel><span class=id>nothing yet</span></div>")
        for r in rows:
            who = "agent" if (r.get("direction") or "").startswith("agent") else "person"
            o.append("<div class='msg %s'><div class=who>%s<span>%s</span></div>%s</div>"
                     % (who, e(who), e((r.get("ts") or "")[11:19]),
                        e(r.get("text") or "")))
    return shell(token, "cases", "".join(o), refresh=30 if live_run else 0)


def page_grade(token):
    o = ["<header><h1>grading</h1><div class=sub>grade.py's own verdicts. This page "
         "decides nothing itself; it runs the grader and shows what it said."
         "</div></header>"]
    for label, outdir, sub, kind in BLOCKS:
        rows, err = grade_block(outdir, kind)
        o.append("<div class=eyebrow>%s &middot; %s</div>" % (e(label), e(sub)))
        if err:
            o.append("<div class=panel><span class=id>%s</span></div>" % e(err))
            continue
        counts = {}
        for r in rows:
            v = (r.get("verdict") or "?").upper()
            counts[v] = counts.get(v, 0) + 1
        o.append("<div class=panel><div class=row>")
        for k in sorted(counts):
            o.append("<span class='pill %s' style='margin-right:8px'>%s %d</span>"
                     % ({"PASS": "pass", "FAIL": "fail"}.get(k, "ungr"),
                        e(k.title()), counts[k]))
        o.append("<span class='r mono' style='color:var(--faint)'>%d of %d banked"
                 "</span></div></div>" % (len(rows), len(case_list())))
        if rows:
            # Every graded case, failures first. An earlier version listed only
            # the failures under a count of the passes, which read as though one
            # case existed when four had been graded.
            rank = {"FAIL": 0, "UNGRADEABLE": 1, "PASS": 2}
            rows = sorted(rows, key=lambda r: (
                rank.get((r.get("verdict") or "").upper(), 1),
                str(r.get("case_id") or r.get("case") or "")))
            o.append("<table><tr><th>case</th><th>verdict</th>"
                     "<th>chose / why</th></tr>")
            for r in rows:
                cid = str(r.get("case_id") or r.get("case") or "?")
                v = (r.get("verdict") or "?").upper()
                what = (r.get("what_was_wrong") or r.get("note")
                        or r.get("actual_model") or r.get("actual") or "")
                o.append("<tr><td><a href='/%s/case/%s'>%s</a></td>"
                         "<td><span class='pill %s'>%s</span></td>"
                         "<td style='color:var(--dim)'>%s</td></tr>"
                         % (token, e(cid), e(cid.split("-")[0]),
                            {"PASS": "pass", "FAIL": "fail"}.get(v, "ungr"),
                            e(v.title()), e(str(what)[:130])))
            o.append("</table>")
    o.append("<div class=note>Counts, not rates. The acceptable sets run from 2 to 21 "
             "models, so a pass on a 21-model case and a pass on a 2-model case are not "
             "the same result and must not be averaged into one. Grading is cached for "
             "five minutes.</div>")
    return shell(token, "grade", "".join(o), refresh=120)


def page_storage(token):
    v = vitals()
    used = v["total_gb"] - v["free_gb"]
    parts = [
        ("shared reference data", os.path.join(REPO, "build", "refdata"),
         "One copy. Every case hard-links it, which costs nothing, so this is paid "
         "once no matter how many cases run."),
        ("live sandboxes", os.path.join(REPO, "harness", "sandboxes"),
         "What is running right now. A pipeline case writes about 8.5 GB while it "
         "works, nearly all of it the ancestry stage rebuilding a PCA basis, and the "
         "sandbox is deleted the moment the case is banked."),
        ("banked results", OUT,
         "What is kept: the judgment, the record and the door artifacts. The ancestry "
         "basis is dropped at banking, which is the difference between tens of "
         "megabytes a case and 8.5 GB."),
        ("patient files", os.path.join(REPO, "pgp-candidates"), ""),
        ("harness vault", os.path.join(REPO, "harness", "vault"),
         "Manifests and transcripts, one per run, kept for every run ever made."),
    ]
    o = ["<header><h1>storage</h1><div class=sub>%.0f Gi used, %.0f Gi free of %.0f"
         "</div><div class=bar style='height:8px;margin-top:10px'>"
         "<i style='width:%.1f%%;background:%s'></i></div></header>"
         % (used, v["free_gb"], v["total_gb"], 100.0 * used / v["total_gb"],
            "var(--T)" if v["free_gb"] < 25 else "var(--C)")]
    o.append("<div class=eyebrow>where it goes</div>")
    for label, path, why in parts:
        b = du(path)
        o.append("<div class=panel style='margin-bottom:8px'><div class=row>"
                 "<b style='font:500 14px system-ui'>%s</b>"
                 "<span class='r mono' style='color:var(--ink)'>%s</span></div>"
                 % (e(label), human(b)))
        if why:
            o.append("<div class=note>%s</div>" % e(why))
        o.append("</div>")

    banked = 0
    for _l, outdir, _s, _k in BLOCKS:
        d = os.path.join(OUT, outdir)
        if os.path.isdir(d):
            banked += len([x for x in os.listdir(d)
                           if os.path.isdir(os.path.join(d, x))])
    if banked:
        per = du(OUT) / float(banked)
        o.append("<div class=eyebrow>per case</div><div class=panel>"
                 "<div class=row><span class=id>%d banked so far</span>"
                 "<span class='r mono' style='color:var(--ink)'>%s each</span></div>"
                 "<div class=note>388 runs at that rate is roughly %.0f GB in "
                 "total.</div></div>"
                 % (banked, human(per), per * 388 / (1024.0 ** 3)))
    o.append("<div class=note>Sizes are cached for three minutes; du over the "
             "reference data takes a few seconds and the answer barely moves.</div>")
    return shell(token, "storage", "".join(o), refresh=120)


# ------------------------------------------------------------------ serving

def make_handler(token):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            path = self.path.split("?")[0].rstrip("/")
            base = "/" + token
            try:
                if path == base:
                    self._send(200, page_overview(token))
                elif path == base + "/live":
                    self._send(200, page_live(token))
                elif path == base + "/cases":
                    self._send(200, page_cases(token))
                elif path == base + "/grade":
                    self._send(200, page_grade(token))
                elif path == base + "/storage":
                    self._send(200, page_storage(token))
                elif path.startswith(base + "/case/"):
                    case = path[len(base) + 6:]
                    if case in case_list():
                        self._send(200, page_case(token, case))
                    else:
                        self._send(404, "no such case\n", "text/plain")
                else:
                    self._send(404, "not here\n", "text/plain")
            except Exception as exc:  # noqa: BLE001
                self._send(500, "the page failed to build: %r\n" % exc, "text/plain")
    return H


def main():
    global SHOW_TRANSCRIPTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--no-transcripts", action="store_true",
                    help="hide the persona conversations")
    args = ap.parse_args()
    SHOW_TRANSCRIPTS = not args.no_transcripts

    if len(args.token) < 12:
        raise SystemExit("use a token of at least 12 characters; it is the only lock "
                         "on this site")

    ip = "your-vm-ip"
    try:
        ip = (subprocess.run(["hostname", "-I"], capture_output=True,
                             text=True).stdout or "").split()[0]
    except Exception:
        pass

    print("watching  %s" % OUT)
    print("cases     %d" % len(case_list()))
    print()
    print("open      http://%s:%d/%s" % (ip, args.port, args.token))
    print()
    print("  now       the batch at a glance")
    print("  live      network and machine, refreshing every 2s")
    print("  cases     every case against every pass, tap one for detail")
    print("  grading   grade.py's verdicts")
    print("  storage   where the disk is going")
    print()
    if SHOW_TRANSCRIPTS:
        print("TRANSCRIPTS ARE ON: anyone with this URL can read the full persona")
        print("conversations. Pass --no-transcripts to hide them.")
    else:
        print("transcripts hidden.")
    if args.bind == "0.0.0.0":
        print("Bound to 0.0.0.0, so the token in the URL is the only lock.")
    print()
    print("reads only, writes nothing. ctrl-c to stop.")
    HTTPServer((args.bind, args.port), make_handler(args.token)).serve_forever()


if __name__ == "__main__":
    main()
