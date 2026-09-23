# Building the eval VM from a bare box

Ubuntu 24.04 (noble), 8 vCPU / 16 GB / no swap, 160 GB disk. Everything below was read
off the working machine on 2026-09-05 rather than recalled, **except where it says
UNVERIFIED** — those steps have never been run on a genuinely empty box and are the
reason a wipe is not yet safe.

The four known failure points from the last attempt: numpy missing, plink2 not
executable, macOS binaries staged instead of Linux ones, and `setup.sh` / `preflight.py`
/ `fetch_refdata.py` having only ever run where everything was already present.

## 1. System packages

```bash
apt update
apt install -y build-essential bcftools bubblewrap openjdk-17-jre-headless \
               python3-numpy python3-scipy python3-pandas \
               git curl unzip zip tmux
```

The Python packages come from apt, not pip — see §2 for why. `zip` is used when repacking
staged artifacts; `tmux` because both the refdata fetch and a 40-minute run outlive an SSH
session.

`setup.sh` in this repo installs the same set idempotently and is the supported path.
**It differs from the list above in one respect**: it installs `default-jre`, which on
noble resolves to `openjdk-21-jre` and is not headless. The working box runs
`openjdk-17-jre-headless`, which is what the table below records, so this document keeps
17. The two should be reconciled; until they are, prefer the list above on a headless box.

Confirmed present on the working box at these versions:

| package | version |
|---|---|
| bcftools | 1.19-1build2 |
| bubblewrap | 0.9.0-1ubuntu0.1 |
| build-essential | 12.10ubuntu1 |
| openjdk-17-jre-headless | 17.0.20.1+1~24.04 |

`tabix` and `bgzip` come with bcftools; both are at `/usr/bin`.

**bubblewrap is not optional.** It is the sandbox boundary on Linux, the counterpart of
the macOS seatbelt, and `harness/harness/profile.py` generates its profile.

## 2. Python

System python 3.12.3, packages installed system-wide rather than in a venv on the
working box.

Installed by apt in §1, **not by pip**. The harness runs behind an allow-list proxy that
returns 403 for PyPI, so `pip install` cannot reach anything from inside a run. `setup.sh`
and `preflight.py` both say so at their install sites.

VERIFIED 2026-09-23 by import-grep over both repositories. This replaces the earlier
UNVERIFIED note, which asked for exactly this list:

| package | needed by | status |
|---|---|---|
| `numpy` | `build/tools/ancestry.py`, `build/tools/loo_validate_ancestry.py`, `reference-provider/build_reference.py` | **required.** Its absence is the failure that produced four worthless FAIL grades |
| `scipy` | nothing imports it | **install it anyway.** `preflight.py` treats its absence as FAIL and will refuse the machine. Either install it or fix preflight; do not skip it silently |
| `pandas` | nothing imports it | **not required.** Harmless if present |
| `openpyxl` | `archive_results.py`, `audit/audit_pass1.py` (private repo) | **optional.** Both guard the import and fall back to JSON. Not in the §1 list; add it only if you want the spreadsheet output |
| `requests` | nothing, anywhere in either repository | **not required.** It was in the old list by mistake |

The previous wording here told you to `pip3 install numpy pandas openpyxl requests`. That
installed two packages nothing imports, omitted the one package preflight fails on, and
used the one mechanism the proxy blocks.

## 3. Binaries that are not packaged

Four live outside apt, and two of them are the ones that went wrong last time.

| binary | path on the working box | note |
|---|---|---|
| `plink2` | `/usr/local/bin/plink2` | **must be the Linux build and must be `chmod +x`.** Staging a macOS binary here is one of the four known failures |
| `sandbox-exec` | `/usr/bin/sandbox-exec` | **not optional.** The harness launches every agent as `sandbox-exec -f <profile> <cmd>`, a macOS command; this shim translates the profile into bubblewrap arguments, so the harness never learns which platform it is on. It ships in this repo at `harness/sandbox-exec` and nothing installs it but the line below |
| `nextflow` | `/usr/local/bin/nextflow` | needs java, which is why openjdk is above. **UNVERIFIED whether anything still uses it** — see below |
| `claude` | `/usr/bin/claude` | via npm, see below |

```bash
install -m 755 harness/sandbox-exec /usr/bin/sandbox-exec
```

Until 2026-09-23 this document named `sandbox-exec` only in §6's table of what the public
repo holds, and never as a thing to install. `setup.sh` installs it; following this
document by hand did not, and the result is a box where every agent launch fails.
`preflight.py` checks for it.

**nextflow may be vestigial.** `git grep -l nextflow` over the tracked tree matches four
files — `.gitignore`, this document, `build/.gitignore` and
`build/docs/research-R9-missing-weight-fraction.md` — and none of them is code.
`grep -rn nextflow harness/ build/tools/` returns nothing. The `build/.nextflow.log*`
files on the working box are dated 2026-07-28, before the current harness. It is installed
here and kept in the list because **a box without it has not been tested**, not because
anything is known to need it.

`liftOver` and `beagle.jar` live under `build/refdata/bin/` and come with the refdata,
not with the system. **The `liftOver` in refdata must be the Linux build**, same trap as
plink2.

## 4. Node and the Claude CLI

```bash
apt install -y nodejs npm
npm install -g @anthropic-ai/claude-code@2.1.224
claude --version    # must print 2.1.224
```

**Pin the version and pin it after any sign-in.** Newer versions cannot open their
scratch directory inside the sandbox, so every case dies in under a second with what
reads like a permissions error. This is the single most expensive thing to get wrong.

## 5. Authentication

```bash
# on a machine with a browser:
claude setup-token          # prints a one-year token, once

# on the VM:
echo "export CLAUDE_CODE_OAUTH_TOKEN='<token>'" >> ~/.bashrc
chmod 600 ~/.bashrc
```

**There is no browser login path on a headless box**, and the four-hourly OAuth refresh
does not work there, which is what made every previous batch die every few hours.

Two things learned on 2026-09-05:

- **`build_env` in `harness/harness/agents.py` builds the sandbox environment from
  scratch and drops anything not listed.** The token has to be passed through
  explicitly; a plain export in the shell does not reach the agent.
- **A shell that predates the `.bashrc` edit does not have it**, and then every case
  fails in seconds with `Not logged in`. Reading the token from a file rather than the
  environment is on the backlog for exactly this.

Move any stored credentials aside once the token works, so it cannot lose to them:
`mv ~/.claude/.credentials.json ~/.claude/.credentials.json.bak`.

## 6. The repositories

Two, and the VM needs both.

| repo | visibility | holds |
|---|---|---|
| `theadamafyouni-lgtm/prs-agent` | public | the agent, the harness, the grader, `sandbox-exec` |
| `theadamafyouni-lgtm/prs-agent-private` | private | personas, answer key, launch-gate tokens, corpus tooling, the 582 original results |

The split is not tidiness. Each persona pairs a real Personal Genome Project
participant's survey answers with an invented medical history, and the gate tokens are
the participant identifiers themselves. Neither can be public. The Mac's old repository,
with the full history, is `prs-agent-copy`; it is private and nothing here uses it.

```bash
gh auth login                      # the private clone needs it
cd /root
git clone https://github.com/theadamafyouni-lgtm/prs-agent.git CliGenAI-Lab/prs-agent-product
git clone https://github.com/theadamafyouni-lgtm/prs-agent-private.git prs-agent-private
```

Then restore what the public repo cannot carry:

```bash
P=/root/CliGenAI-Lab/prs-agent-product
V=/root/prs-agent-private

install -d -m 700 ~/.config/prs-harness
install -m 600 "$V/gate/tokens.json" ~/.config/prs-harness/tokens.json

mkdir -p "$P/build/sim/personas/eval" "$P/answer-side" "$P/vm"
cp "$V"/personas/*                 "$P/build/sim/personas/eval/"
cp "$V/answer-key/answer-sets.csv" "$P/answer-side/"
cp "$V"/batch-scripts/*.sh         "$P/harness/"
cp "$V"/vm/*.py                    "$P/vm/"
cp "$V"/vm-scripts/*               /root/
```

**Restore the token file before running anything.** `harness/harness/config.py` loads it
at import and fails closed: without it every harness command, `--help` included, raises
and names the path. That is deliberate. An empty token list would let the launch gate
report `passed` while checking nothing.

`$V/results/` and `$V/graded/` are the 582 original runs and their grades. They are not
restored; they are what a rerun is compared against.

`preflight.py` checks the tokens, the personas and the key.

## 7. The Linux port

The committed harness is already the Linux build: `config.py` says so in its
docstring, and `profile.py` and `probe.py` were confirmed byte-identical to the VM's
working copies on 2026-09-15. The three patches that produced it are kept in the
private repo under `vm-scripts/`, restored to `/root` by section 6, in case a fresh
clone turns out to need them:

- `patch_clone_refdata_linux.py` — hard links instead of APFS copy-on-write
- `patch_hardlink_gate_linux.py` — the launch gate's clone step
- `patch_neutral_patient_name.py` — filename neutralisation

**UNVERIFIED whether a fresh clone needs them.** If the gate run in section 10 fails at
staging, look here first. Read each before running it.

## 8. Reference data, about 24 GB

```bash
python3 fetch_refdata.py            # pulls from build/refdata/MANIFEST.json
python3 reference-provider/build_reference.py
```

**UNVERIFIED — this is the step most likely to fail.** `fetch_refdata.py` has never run
on a box that did not already have the data, and `build_reference.py` builds the PCA
basis, which is the slow half. Budget hours, not minutes.

What has to end up present:

- `build/refdata/ancestry-ref/hgdp_1kgp/` — the HGDP+1kGP panel, the projection basis
- `build/refdata/ancestry-ref/hgdp_basis/prune.prune.in` — 230,243 pruned markers
- `build/refdata/panel/` — the imputation panel, bref3 per chromosome
- `build/refdata/fasta/GRCh37.fa.gz` — 751 MB bgzipped, plus `.fai` and `.gzi`
- `build/refdata/maps/` — genetic maps
- `build/refdata/bin/` — `plink2`, `liftOver`, `beagle.jar`, `bref3.jar`, the JRE

## 9. Fixtures and personas

Personas come from the private repo, restored by section 6 to
`build/sim/personas/eval/`, 99 of them.

The ten damaged fixtures are **built, not downloaded**: `make_broken_fixtures.py`
(private repo, `vm-scripts/`, restored to `/root`)
damages a real assay and writes a `.provenance.json` beside each recording what was done
and the source checksum. They live in `pgp-candidates/<participant>/broken/`.

**`pgp-candidates/` is in neither repository.** Confirmed on 2026-09-15: it holds the
real participant genomes and the CG truth files, and no path under it is tracked. The
files are public PGP data, but **there is no script that fetches them**, so a wiped
box has no way to get them back except a copy from somewhere else. Back it up before
any wipe. The broken fixtures are built from it, so they go with it.

## 10. Prove it before trusting it

```bash
cd harness && ./run_harness.py gate --persona p40      # stages everything, launches nothing
cd harness && ./run_harness.py run  --persona p40 --model claude-opus-4-8
```

The gate is free and catches a staging or scrub failure. The run costs tokens and is the
only thing that proves the whole path. A good run writes `result.json` and the manifest
carries `present: true, valid: true, errors: []`.

## What this document is not

**It has not been executed end to end on an empty machine.** It is a record of a working
box plus the four failures already known, which is better than nothing and is not the
same as a tested procedure. Three sections are marked UNVERIFIED and they are the ones
that will cost the day: the real Python requirements, the port-patch order, and the
refdata fetch.

**Do not wipe the working box until this has been run somewhere else**, or there is no
way back.
