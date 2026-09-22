"""No stop-the-batch marker may fire on the output of a SUCCESSFUL run.

This is the test that was missing. Two changes, each harmless alone, combined to stop
every batch on its first case: run_harness.py started printing a line naming
CLAUDE_CODE_OAUTH_TOKEN, and run_corpus.py started searching the whole case log for
"oauth". Nothing tested the writer and the reader together, so nothing caught it.

So the log here is not hand-written. It is assembled out of run_harness.py's own print
statements, read from its source, plus the harness vocabulary that can reach a case log.
Add a print to the harness containing a marker word and this goes red.
"""
import ast
import importlib.util as iu
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
spec = iu.spec_from_file_location("rc", os.path.join(ROOT, "vm", "run_corpus.py"))
rc = iu.module_from_spec(spec); spec.loader.exec_module(rc)
from harness import auth, config  # noqa: E402

fails = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        fails.append(name)
    print("  %-4s %-58s %s" % ("ok" if ok else "FAIL", name,
                               "" if ok else "got %r want %r" % (got, want)))


def printed_strings(path):
    """Every string literal that reaches stdout from this module, from its own source."""
    out = []
    tree = ast.parse(open(path).read())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                out.append(sub.value)
    return out


# 1. What run_harness prints. %-placeholders left in: they cannot introduce a marker.
lines = printed_strings(os.path.join(ROOT, "harness", "run_harness.py"))
check("harness print statements found", len(lines) > 30)

# 2. The auth line as the VM actually renders it -- the string that caused this.
lines.append("auth        : file (%s read from /root/.env)" % auth.TOKEN_VAR)
lines.append("auth        : environment (%s was already set; /root/.env was not read)"
             % auth.TOKEN_VAR)

# 3. Paths and ids the harness echoes, including a run id that contains 401.
RUN_ID = "run-20260907-140401-a1b2c3"
lines += [
    "run id      : %s" % RUN_ID,
    "sandboxes   : /root/CliGenAI-Lab/prs-agent-product/harness/sandboxes/%s" % RUN_ID,
    "vault       : /root/CliGenAI-Lab/prs-agent-product/harness/vault/runs/%s" % RUN_ID,
    "results     : results/pipeline/pass1/p07-flutter-verdict",
    "brief sha256            9f4013a7c8b24019e0a4401ff2c3d4e5",
]

# 4. The gate prints the answer-key tokens it grepped for, by name.
lines += list(config.HARD_TOKENS) + [t for t, _why in config.SOFT_TOKENS]

# 5. And the transport/decision prose the harness echoes into its summary.
lines.append(config.TOKEN_POLICY["prs"]["why"])
lines.append(config.TOKEN_POLICY["provider"]["why"])

LOG = "\n".join(lines)
haystack = LOG.lower()          # exactly what run_one_attempt builds

print("synthetic successful-run log: %d lines, %d chars" % (len(lines), len(LOG)))

print("\nthe dead-token check must not fire on a successful run")
hit = [m for m in rc.TOKEN_DEAD_MARKERS if m in haystack]
check("no dead-token phrase matches", hit, [])
check("no word-bounded 401 matches", bool(rc.TOKEN_401_RE.search(haystack)), False)
check("  even though the log does contain the digits 401", "401" in haystack)
check("  and does contain the token variable name", auth.TOKEN_VAR.lower() in haystack)

print("\nthe cap check must not fire on a successful run either")
hit = [m for m in rc.CAP_MARKERS if m in haystack]
check("no cap marker matches (quota, rate limit and the rest)", hit, [])

print("\nand the real signals are still caught")
for text, why in [
    ("API Error: 401 Unauthorized", "401 as a status code"),
    ("Failed to authenticate with Anthropic", "failed to authenticate"),
    ("OAuth session expired", "expired session"),
    ("Not logged in. Run `claude login`.", "no session"),
    ("your credentials were revoked", "revoked"),
]:
    h = text.lower()
    fired = (any(m in h for m in rc.TOKEN_DEAD_MARKERS) or rc.TOKEN_401_RE.search(h))
    check("%-38s still stops the batch" % why, bool(fired))
for text in ["You've hit your weekly limit · resets 6pm (UTC)", "Claude usage limit reached",
             "rate limit exceeded", "monthly quota exhausted"]:
    check("%-38s still a cap" % text[:38],
          any(m in text.lower() for m in rc.CAP_MARKERS))

print("\nthe regression, reproduced against the OLD rule")
old_fires = ("oauth" in haystack or "401" in haystack or "revoked" in haystack)
check("the old bare-fragment rule DOES fire on this log", old_fires)
print("       (that is the bug: %s)"
      % ("'oauth' and '401' both matched" if old_fires else "-"))

print("\n%s" % ("ALL PASS" if not fails else "FAILURES: %s" % fails))
sys.exit(1 if fails else 0)
