#!/bin/bash
# Prepare a fresh Ubuntu machine to run the harness. Linux only.
#
#   sudo bash setup.sh
#   python3 preflight.py
#
# Idempotent: safe to run twice. It installs what is missing, fixes what is
# wrong, and then runs preflight.py and refuses to claim success unless preflight
# agrees.
#
# WHAT IT CANNOT DO, AND WHY THAT MATTERS
#
# Two things are left for a person, and the demo does not work without them.
#
#   The reference data. build/refdata is about 49 GB: the HGDP+1kGP panel, a
#   1000 Genomes phase 3 subset, GRCh37 and GRCh38 FASTA, liftOver chains and
#   recombination maps. It is far past what git will hold, so it is not in this
#   repository and cannot be.
#
#   COPY IT FROM A MACHINE THAT HAS IT. That is not a preference, it is the only
#   way. Verified 2026-09-23: about 29 GB of it -- ancestry-ref/hgdp_1kgp/ and
#   ancestry-ref/phase3/ -- is fetched by no script in either repository and has
#   no URL anywhere in the tracked tree. fetch_refdata.py gets 4 GB of the 49.
#   Earlier text here said to build it with reference-provider/build_reference.py;
#   that script builds a per-patient INT-1 distribution and has no network code.
#   See SETUP.md section 8.
#
#   Signing in to Claude Code. It is interactive and cannot be scripted. Note the
#   order, because it is not the obvious one: sign in FIRST, then pin the CLI
#   version, because signing in silently upgrades the CLI and the newer versions
#   cannot open their scratch directory inside the sandbox. Getting this backwards
#   produces cases that die in under a second with an EPERM that reads like a
#   permissions problem and is a version problem.
#
# So `git clone && bash setup.sh` does not give you a working machine. It gives
# you a machine that is missing exactly two things and tells you what they are.

set -u
CLI_VERSION=2.1.224
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REFDATA="$REPO/build/refdata"
say(){ echo "==> $*"; }
warn(){ echo "    ! $*"; }

if [ "$(id -u)" != "0" ]; then
  echo "run this as root: sudo bash setup.sh"
  exit 1
fi

say "system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# bubblewrap is the sandbox. The python packages are NOT optional: tools/ancestry.py
# imports numpy, and without it the ancestry stage refuses, select refuses for want
# of an ancestry to rank on, and every score case fails for a reason that has
# nothing to do with the agent.
#
# apt rather than pip on purpose: the harness runs behind an allow-list proxy that
# blocks PyPI with a 403, so pip cannot reach anything from inside a run.
apt-get install -y -qq \
    bubblewrap bcftools python3-numpy python3-scipy python3-pandas \
    default-jre curl unzip zip tmux >/dev/null
say "  bubblewrap $(bwrap --version 2>&1 | awk '{print $2}')"

say "node and the CLI"
if ! command -v node >/dev/null; then
  apt-get install -y -qq nodejs npm >/dev/null
fi
NODE_MAJOR=$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)
if [ "${NODE_MAJOR:-0}" -lt 22 ]; then
  warn "node $(node --version) is older than the CLI wants (>=22)."
  warn "It runs with an EBADENGINE warning. Upgrade if anything behaves oddly."
fi
if ! command -v claude >/dev/null; then
  npm install -g "@anthropic-ai/claude-code@${CLI_VERSION}" >/dev/null 2>&1
fi
say "  claude $(claude --version 2>&1 | head -1)"

say "the sandbox-exec shim"
# The harness launches every agent as `sandbox-exec -f <profile> <cmd>`, which is
# a macOS command. Rather than patch that call site in three files and keep them
# in step with the macOS original, a shim of the same name translates the profile
# into bubblewrap arguments, so the harness never learns which platform it is on.
if [ -f "$REPO/harness/sandbox-exec" ]; then
  install -m 755 "$REPO/harness/sandbox-exec" /usr/bin/sandbox-exec
  say "  installed /usr/bin/sandbox-exec"
else
  warn "harness/sandbox-exec not found in the repo; the harness cannot launch"
fi

say "tool binaries in refdata"
mkdir -p "$REFDATA/bin"
# plink2: the system package is the same program. Copied rather than symlinked
# because refdata is bind-mounted read-only into the sandbox and a symlink out of
# it would dangle.
if [ ! -x "$REFDATA/bin/plink2" ]; then
  if command -v plink2 >/dev/null; then
    cp "$(command -v plink2)" "$REFDATA/bin/plink2"
  else
    warn "plink2 is not installed on this machine and is not in refdata/bin."
    warn "Get it from https://www.cog-genomics.org/plink/2.0/"
  fi
fi
if [ ! -f "$REFDATA/bin/liftOver" ]; then
  curl -sSL -o "$REFDATA/bin/liftOver" \
    https://hgdownload.soe.ucsc.edu/admin/exe/linux.x86_64/liftOver || \
    warn "could not download liftOver"
fi
# The execute bit, explicitly, every time.
#
# This is the single most expensive mistake made setting this up. plink2 was
# copied into place mode 644. refdata is read-only inside the sandbox, so the
# agent could not chmod it; it built a shim directory with an executable copy
# instead, which pulled 48 GB of reference data into its own output and filled
# the disk. Nothing looked broken until the disk ran out, because the agent
# documented what it had done and carried on correctly.
chmod 755 "$REFDATA/bin/"* 2>/dev/null
for b in plink2 liftOver; do
  if [ -x "$REFDATA/bin/$b" ]; then
    say "  $b $(("$REFDATA/bin/$b" --version 2>&1 || "$REFDATA/bin/$b" 2>&1) | head -1 | cut -c1-50)"
  fi
done

say "context hygiene"
# Any CLAUDE.md the agent can see is loaded into its context automatically. One
# sat at the filesystem root on this machine and would have entered every single
# run; the launch gate caught it, which is the only reason it was found.
for f in /CLAUDE.md "$HOME/CLAUDE.md" "$HOME/.claude/CLAUDE.md"; do
  if [ -f "$f" ]; then
    mv "$f" "$f.moved-by-setup" && say "  moved $f aside"
  fi
done

say "reference data"
if [ -d "$REFDATA/ancestry-ref" ] && [ -d "$REFDATA/panel" ]; then
  say "  present, $(du -sh "$REFDATA" 2>/dev/null | cut -f1)"
else
  warn "build/refdata is missing or incomplete. About 49 GB, not in this repo."
  warn "Copy it from a machine that has it. It cannot be rebuilt here: 29 GB of"
  warn "it (ancestry-ref/hgdp_1kgp, ancestry-ref/phase3) is fetched by no script"
  warn "in either repository. See SETUP.md section 8."
fi

echo
say "preflight"
echo
python3 "$REPO/preflight.py"
rc=$?
echo
if [ "$rc" = "0" ]; then
  say "ready. Sign in if you have not: run \`claude\`, then re-pin:"
  say "  npm install -g @anthropic-ai/claude-code@${CLI_VERSION}"
  say "in that order, because signing in upgrades the CLI."
else
  say "not ready. Fix what preflight lists above and run it again."
fi
exit $rc
