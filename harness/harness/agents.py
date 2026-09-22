"""Launching a Claude Code process inside a Seatbelt sandbox.

Everything below was arrived at empirically. The three that cost the most time:

  USER and LOGNAME must be present or the CLI cannot reach the login keychain and prints
  "Not logged in". This is true unsandboxed too, with `env -i` -- it is an environment
  issue, not a sandbox one, and it looks exactly like a sandbox failure.

  TMPDIR must point inside the sandbox AND the cwd must be inside it, or Python's import
  machinery raises PermissionError while stat-ing a denied sys.path entry. The failure
  surfaces deep in importlib and reads like a corrupt interpreter.

  /usr/bin/python3 is the Xcode shim and prints a harmless `couldn't create cache file
  .../xcrun_db` pair to stderr under the profile, because xcrun reads the real per-user temp
  via confstr and ignores TMPDIR. Exit codes are unaffected. Allowing that one path would
  reintroduce a directory shared across runs, so the noise is accepted and filtered.
"""
import os
import subprocess
import time

from . import auth, config


def build_env(jail_home, tmpdir, proxy_port, extra=None):
    """The complete environment. Anything not listed here is dropped.

    The parent's environment is not inherited at all -- it carries CLAUDE_CODE_* variables,
    the orchestrating session's id, and PWD/OLDPWD naming paths outside the sandbox.

    With exactly one exception, below.
    """
    env = {
        "PATH": config.SANDBOX_PATH,
        "HOME": jail_home,
        "TMPDIR": tmpdir if tmpdir.endswith("/") else tmpdir + "/",
        "USER": os.environ.get("USER", ""),
        "LOGNAME": os.environ.get("LOGNAME", os.environ.get("USER", "")),
        "SHELL": "/bin/zsh",
        "LANG": "en_US.UTF-8",
    }

    # The one CLAUDE_CODE_* variable that is passed through rather than dropped, and the
    # exception the docstring names. On a headless box there is no login keychain and no
    # browser login path, so this IS the credential: without it every case dies in seconds
    # with `Not logged in`, and a whole corpus produces four hundred identical failures.
    # The entry points put it here from /root/.env before anything is staged -- see auth.py.
    #
    # Only when it is set, so a macOS run -- which reaches the credential through the login
    # keychain, and is what USER and LOGNAME above are load-bearing for -- gets exactly the
    # environment it got before this line existed.
    #
    # Its VALUE is never recorded. Both places that write this dict down -- the invocation
    # record in run() below, and the launch-gate record in orchestrator.gate_profiles --
    # put it through auth.redact_env first. That is not politeness: the manifest is copied
    # into results/, which may be committed.
    token = os.environ.get(auth.TOKEN_VAR)
    if token:
        env[auth.TOKEN_VAR] = token
    if proxy_port:
        # Both spellings: curl and most CLIs read the lowercase form, Node reads the upper.
        url = "http://127.0.0.1:%d" % proxy_port
        env["HTTPS_PROXY"] = url
        env["https_proxy"] = url
        env["HTTP_PROXY"] = url
        env["http_proxy"] = url
        env["NO_PROXY"] = ""
    if extra:
        env.update(extra)
    return env


class SandboxedClaude:
    """One agent: a profile, a jail HOME, a cwd, and an environment."""

    def __init__(self, name, sandbox_root, profile_path, jail_home, env,
                 model=None, allowed_tools=None, disallowed_tools=None,
                 append_system_prompt=None, setting_sources="project,local"):
        self.name = name
        self.sandbox_root = sandbox_root
        self.profile_path = profile_path
        self.jail_home = jail_home
        self.env = env
        self.model = model
        self.allowed_tools = allowed_tools
        self.disallowed_tools = disallowed_tools
        self.append_system_prompt = append_system_prompt
        self.setting_sources = setting_sources
        self.invocations = []

    def _argv(self, prompt, session_id=None, resume=None, output_format="text"):
        argv = ["/usr/bin/sandbox-exec", "-f", self.profile_path, "claude",
                "-p", prompt, "--output-format", output_format]
        # Only the sandbox's own settings apply. With HOME jailed the user scope is empty
        # anyway, but naming it keeps a future ~/.claude from silently coming back into
        # scope -- and it is what stops the account's vault-writing skills from loading.
        if self.setting_sources:
            argv += ["--setting-sources", self.setting_sources]
        if self.model:
            argv += ["--model", self.model]
        if self.allowed_tools:
            argv += ["--allowedTools"] + list(self.allowed_tools)
        if self.disallowed_tools:
            argv += ["--disallowedTools"] + list(self.disallowed_tools)
        if self.append_system_prompt:
            argv += ["--append-system-prompt", self.append_system_prompt]
        if session_id:
            argv += ["--session-id", session_id]
        if resume:
            argv += ["--resume", resume]
        # The kernel sandbox is the boundary. The CLI's own permission layer is not one --
        # a subagent demonstrated python3 -c open() walking straight through a deny rule
        # that blocked both the Read tool and cat. Leaving prompts on would only stall an
        # unattended run against a wall that does not hold anyway.
        argv += ["--permission-mode", "bypassPermissions"]
        return argv

    def run(self, prompt, timeout, session_id=None, resume=None, output_format="text",
            log_path=None):
        argv = self._argv(prompt, session_id=session_id, resume=resume,
                          output_format=output_format)
        t0 = time.time()
        proc = subprocess.run(argv, cwd=self.sandbox_root, env=self.env,
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
        elapsed = time.time() - t0

        stderr = _drop_xcrun_noise(proc.stderr or "")
        rec = {
            "agent": self.name,
            "argv": _redact(argv),
            "cwd": self.sandbox_root,
            # Redacted, not copied. This record goes into the manifest and into the vault
            # log, and the manifest is copied into results/. build_env passes the OAuth
            # token through, so the verbatim copy this used to be wrote a live credential
            # into the evidence tree once per invocation.
            "env": auth.redact_env(self.env),
            "returncode": proc.returncode,
            "elapsed_seconds": round(elapsed, 2),
            "stdout_bytes": len(proc.stdout or ""),
            "stderr_tail": stderr[-4000:],
            "session_id": session_id or resume,
        }
        self.invocations.append(rec)
        if log_path:
            _append_log(log_path, rec, proc.stdout or "", stderr)
        return proc.returncode, (proc.stdout or ""), stderr, rec


def _redact(argv):
    """Keep the prompt out of the manifest's argv line.

    The prompt is recorded in full in the transcript, where it belongs. Repeating it inside
    argv would put a person's answer into a field people scan for paths and flags.
    """
    out, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            out.append("<prompt: %d chars>" % len(a))
            skip = False
            continue
        out.append(a)
        if a in ("-p", "--append-system-prompt"):
            skip = True
    return out


def _drop_xcrun_noise(text):
    keep = [l for l in text.splitlines()
            if "xcrun_db" not in l and "couldn't create cache file" not in l]
    return "\n".join(keep)


def _append_log(path, rec, stdout, stderr):
    import json
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps({"invocation": rec, "stdout": stdout, "stderr": stderr},
                            sort_keys=False) + "\n")


def collect_agent_transcript(jail_home, dest_dir):
    """Copy the CLI's own session transcripts out of the jail before teardown.

    The jail HOME is where the CLI writes its session store, so this is the agent's own
    record of what it did -- distinct from the harness's transcript, which only sees
    messages that crossed a boundary. Both go in the vault; where they disagree is
    interesting.
    """
    import shutil
    src = os.path.join(jail_home, ".claude", "projects")
    if not os.path.isdir(src):
        return {"present": False, "files": []}
    os.makedirs(dest_dir, exist_ok=True)
    copied = []
    for cur, _dirs, files in os.walk(src):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            s = os.path.join(cur, name)
            d = os.path.join(dest_dir, "%s__%s" % (os.path.basename(cur), name))
            try:
                shutil.copyfile(s, d)
                copied.append({"from": s, "to": d, "bytes": os.path.getsize(d)})
            except OSError:
                continue
    return {"present": True, "files": copied}
