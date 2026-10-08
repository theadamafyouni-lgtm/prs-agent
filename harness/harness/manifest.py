"""The record: the transcript, and the manifest that makes the isolation claim checkable.

A boundary that is documented but not enforced is worse than one that is absent, because it
makes the result look trustworthy. The same is true of a manifest: one that describes what
staging was supposed to do is worse than none, because it reads like evidence. So everything
here is written from what happened -- hashes taken from files on disk, errnos taken from a
probe that actually ran, timings measured.

Two things it deliberately records that are not flattering: what the token grep could not
scan, and what the Spotlight index already contained before the run started.
"""
import getpass
import hashlib
import json
import os
import platform
import socket
import subprocess
import threading
import time

from . import auth, config


class Transcript:
    """Every message that crossed a boundary, in order, append-only."""

    def __init__(self, path):
        self.path = path
        self.n = 0
        # The router and a provider thread both append. Without the lock two records can
        # take the same seq, or interleave inside one line of the JSONL.
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def append(self, direction, kind, text="", question_id=None, meta=None):
        with self._lock:
            self.n += 1
            rec = {
                "seq": self.n,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "direction": direction,
                "kind": kind,
                "question_id": question_id,
                "text": text,
                "meta": meta or {},
            }
            with open(self.path, "a") as fh:
                fh.write(json.dumps(rec, sort_keys=False) + "\n")
                fh.flush()
        return rec

    def read(self):
        out = []
        if not os.path.exists(self.path):
            return out
        with open(self.path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return out


def host_facts():
    def _v(cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            txt = ((r.stdout or "") + (r.stderr or "")).strip()
            return txt.splitlines()[0] if txt else None
        except Exception as exc:  # noqa: BLE001
            return "<unavailable: %s>" % exc

    return {
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "platform": platform.platform(),
        "macos": _v(["sw_vers", "-productVersion"]),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "claude_cli": _v(["claude", "--version"]),
        "plink2": _v([os.path.join(config.REFDATA_SRC, "bin", "plink2"), "--version"]),
        "bcftools": _v(["bcftools", "--version"]),
        "sandbox_exec": os.path.exists("/usr/bin/sandbox-exec"),
    }


def source_revision():
    """The commit this run launched from, and whether the tree matched it, read by the harness
    at launch -- never by the agent.

    Three things the previous version got wrong, each of which made a corpus impossible to
    freeze from its own manifests: a git failure read as clean (bool(None) is False), an
    untracked scratch file read as dirty, and a dirty tree recorded no trace of what differed.
    So: dirty is True/False only when git answered, None when it did not; it counts tracked
    changes, staged or not, and untracked files are listed separately; and a dirty tree
    records its changed paths and the sha256 of `git diff HEAD`, which is enough to check a
    reconstruction byte for byte."""
    def _git(*args, raw=False):
        try:
            r = subprocess.run(["git", "-C", config.PRODUCT_ROOT] + list(args),
                               capture_output=True, timeout=20)
        except Exception:  # noqa: BLE001
            return None
        if r.returncode != 0:
            return None
        # Not .strip(): porcelain lines start with a status column that may be a space.
        return r.stdout if raw else r.stdout.decode(errors="replace").rstrip("\n")

    head = _git("rev-parse", "HEAD")
    tracked = _git("status", "--porcelain=v1", "--untracked-files=no")
    untracked = _git("ls-files", "--others", "--exclude-standard")
    diff = _git("diff", "HEAD", "--binary", raw=True) if tracked else None
    changed = [line[3:] for line in tracked.splitlines() if line.strip()] if tracked else []
    dirty = None if (head is None or tracked is None) else bool(changed)
    return {
        "repo_root": _git("rev-parse", "--show-toplevel"),
        "commit": head,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "tags_at_commit": (_git("tag", "--points-at", "HEAD") or "").split() if head else None,
        "dirty": dirty,
        "dirty_means": "tracked files differ from the commit, staged or not; untracked files "
                       "are listed separately and do not count. null = git did not answer.",
        "changed_paths": changed[:200],
        "n_changed_paths": len(changed),
        "diff_head_sha256": hashlib.sha256(diff).hexdigest() if diff else None,
        "untracked_paths": untracked.splitlines()[:200] if untracked else [],
        # Kept under their old names so earlier readers of the manifest still find them.
        "build_head": head,
        "build_dirty": dirty,
        "note": "Read by the harness at launch, for provenance. The repository's .git is never "
                "staged and is on the denial probe list -- its commit subject lines alone can "
                "leak a prior run's ancestry call.",
    }


class Manifest:
    def __init__(self, run_id, vault_dir):
        self.run_id = run_id
        self.dir = vault_dir
        self.path = os.path.join(vault_dir, "manifest.json")
        os.makedirs(vault_dir, exist_ok=True)
        # A provider thread records its invocations while the main thread may be writing;
        # a dict changed mid-write fails the write.
        self._lock = threading.RLock()
        self.data = {
            "run_id": run_id,
            "harness_version": "1.0",
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "finished_utc": None,
            "host": host_facts(),
            "source_revision": source_revision(),
            "decisions_this_run_was_built_on": DECISIONS,
            "agents": {},
            "boundary": {},
            "staging": {},
            "network": {},
            "int1": {},
            "transcript": {},
            "outcome": {},
            "caveats": [],
        }

    def set(self, key, value):
        with self._lock:
            self.data[key] = value
        return value

    def agent(self, name, **fields):
        with self._lock:
            self.data["agents"].setdefault(name, {}).update(fields)

    def caveat(self, text):
        with self._lock:
            self.data["caveats"].append(text)

    def write(self):
        with self._lock:
            return self._write()

    def _write(self):
        self.data["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            # Redacted here, at the one point every manifest goes through, rather than only
            # at the call sites that happen to pass an environment today. A credential
            # reached /agents/prs/env, /agents/persona/env and /agents/provider/env because
            # the rule lived at the call sites and one of them did not know about it; a rule
            # enforced where the file is written cannot be forgotten by the next one.
            #
            # On the way out, not on the way in: self.data keeps real values for the code
            # that reads them back mid-run, and only the file on disk is redacted. The file
            # is the thing that gets copied into results/ and committed.
            json.dump(auth.redact_tree(self.data), fh, indent=2, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, self.path)
        return self.path


def inventory(root, limit=20000):
    """Every file under root, with hash and size, after the run.

    Diffed against the staging record this shows exactly what the run produced, which is the
    other half of the no-cache claim: not only was nothing inherited, here is everything that
    came into existence.
    """
    from .staging import sha256
    out, truncated = [], False
    for cur, dirs, files in os.walk(root):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            if len(out) >= limit:
                truncated = True
                break
            p = os.path.join(cur, name)
            rel = os.path.relpath(p, root)
            try:
                st = os.lstat(p)
                rec = {"path": rel, "bytes": st.st_size,
                       "mode": oct(st.st_mode & 0o777), "nlink": st.st_nlink}
                if os.path.islink(p):
                    rec["symlink_to"] = os.readlink(p)
                elif st.st_size <= 64 * 1024 * 1024:
                    rec["sha256"] = sha256(p)
                else:
                    rec["sha256"] = "(skipped: larger than 64MB)"
                out.append(rec)
            except OSError:
                continue
        if truncated:
            break
    return {"root": root, "n_files": len(out), "truncated": truncated, "files": out}


DECISIONS = {
    "int1_mismatch": (
        "The harness detects when the provider's staged inputs do not match the requested "
        "model, but does NOT write the refusal. The request goes to the provider agent, "
        "which emits usable:false itself with a reason naming the actual cause. Deciding a "
        "reference cannot be built is what INT-2 is for, and a harness that decides it "
        "bypasses the agent whose existence is the point. Whether the arm was genuinely "
        "exercised is recorded under int1.inputs_match_requested_model."),
    "refdata_patient_derived_trees_deleted": (
        "ancestry-ref/build, ancestry-ref/build_p3chr1, ancestry-ref/phase3_sub and "
        "ancestry_build.log are deleted from the per-run clone. They were produced by "
        "subsetting 1000 Genomes through the patient's own array positions -- the "
        "provenance is preserved verbatim in the .pvar headers as `bcftools view -R "
        "/tmp/patient_positions.tsv` -- and their pruned marker counts, 27,405 and 9,956, "
        "are exactly the two numbers the prior run's ancestry judgment cites as its "
        "evidence. phase3/ stays: it is clean upstream 1000 Genomes."),
    "consequence_of_that_deletion": (
        "The agent builds its own PCA basis, so its coordinates, its cohort and its "
        "percentile will NOT match the prior run's. This run cannot test whether the "
        "numbers reproduce. It tests whether the DECISIONS reproduce -- build identified, "
        "ancestry group, model selected, coverage verdict -- which should be robust to a "
        "different reference basis. If they are not, that is the finding."),
    "reproducibility_is_not_corroboration": (
        "Where a fresh run agrees with a prior one, that is REPRODUCIBILITY, not "
        "corroboration: two runs of the same estimator agreeing is what a deterministic "
        "estimator does. It says nothing about whether the estimator is measuring the right "
        "quantity. Specifically, a fresh run inherits the same single-sample DR2 degeneracy "
        "the prior run's coverage figures rest on -- see docs/DECISION-leaveout-dr2.md. "
        "Describe the state as UN-SUBSTITUTED DR2, never as 'pre-correction': calling the "
        "leave-out substitution a correction assumes the question that is still open."),
    "network": (
        "All egress is denied except a loopback allow-listing proxy. Pre-fetching the PGS "
        "Catalog during staging would hand the agent its candidate list, which is select's "
        "whole job. Every connection, permitted or refused, is in network.transcript."),
}
