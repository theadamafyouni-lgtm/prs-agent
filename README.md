# prs-agent

An LLM agent that reads one person's genotype file and chooses a polygenic
scoring model **for that person**, or refuses and says why. Plus a benchmark that
grades the choice.

Existing tools take the scoring model as an input. The PGS Catalog Calculator is
passed a score identifier; PRScalc's user picks one from a list; impute.me's user
navigates to a disease and gets whatever that module holds. The question of which
model suits *this* person, with *this* file, for *this* trait, and what using it
costs, is answered before any of them starts, by somebody else, usually once for
everybody.

Most catalogued models were developed in European cohorts and lose accuracy when
read on a genome from another ancestry. So the person for whom a genetic answer
would matter most is often the person for whom the available answer is least
valid. This picks per person instead, and declines when there is nothing
defensible to pick.

**A refusal is a correct answer.** That is not a hedge, it is the design. If
someone's file cannot support a defensible score for their trait, producing a
number anyway is the failure.

---

## What you need

A Linux machine. Ubuntu 22.04 or 24.04, 8 cores, 16 GB RAM, and **at least 60 GB
of free disk**. A single case takes 25 to 40 minutes.

You will also need a Claude account, because the agent under test is Claude Code
running inside a sandbox.

The macOS path exists but is not in this repository yet. See
[Platform](#platform).

---

## Setup

Four steps, and two of them are slow.

```bash
git clone <this repo> && cd prs-agent
sudo bash setup.sh                    # packages, sandbox, tool binaries
python3 fetch_refdata.py              # about 24 GB of public reference data
python3 reference-provider/build_reference.py   # builds the PCA basis, slow
python3 preflight.py                  # says whether the machine is ready
```

Then sign in, **in this order**:

```bash
claude                                             # sign in, then /exit
npm install -g @anthropic-ai/claude-code@2.1.224   # re-pin AFTER signing in
```

The order matters and is not the obvious one. Signing in silently upgrades the
CLI, and newer versions cannot open their scratch directory inside the sandbox.
Get it backwards and every case dies in under a second with a permissions error
that is really a version error.

`preflight.py` is the thing to run whenever something behaves strangely. Every
environment problem we hit was silent: a missing Python package made the agent
refuse *correctly* for a reason that had nothing to do with the case; a tool
binary without its execute bit made another agent build a 48 GB workaround and
document it neatly. Nothing looked broken either time.

---

## Run one case

```bash
python3 harness/run_harness.py run \
    --persona "$PWD/build/sim/personas/eval/<persona>.json" \
    --patient <path/to/genotype-file> \
    --stop-after select
```

The personas and the genotype files they point at are not in this repository. They are held in a private companion repository and are available on request.

`--persona` needs an absolute path. `--stop-after select` runs the three stages
this benchmark grades and stops before scoring, which is what makes a case take
forty minutes instead of ten hours.

### What happens

The harness stages a sandbox, runs a launch gate, starts the agent under
bubblewrap and starts a second agent that plays the person. They talk through a
message file; neither can see the other's directory.

The agent works through three stages, writing a judgment at each:

| stage | what it decides |
|---|---|
| `read-and-reason` | what this file is: assay, vendor, genome build, what a missing call means |
| `ancestry` | where the person sits in PC space, projected from the genome, not asked |
| `select` | which model, at what cost, or that there isn't one |

Every stage returns one of four outcomes and says which: **score, refuse, stop,
ask**.

### What you get

A run directory with the judgment from each stage, the full conversation, a
ledger of every tool call, and a manifest recording what was staged, what the
network did, and what the boundary checks found.

The interesting file is `select/model_choice.json`:

```
outcome: proceed
model:   PGS003725

PGS003725 (GPS_Mult, Patel AP et al., Nat Med 2023), 1296172 variants,
weight_type=beta, LDpred2. Chosen for an admixed EUR+CSA person in primary
prevention. Shown to the person with the reasoning under a TRANSPARENCY gate
(SEL-6), not a decision gate: the best ancestry-matched candidate is also the
best-covered one, so there was no genuine fork to hand them. They acknowledged
it: 'Yes, that sits right. Record it.'
```

That is the contribution in one file. Not the number, the choice and its cost.

---

## The benchmark

99 personas at `build/sim/personas/eval/`, built on 12 real Personal Genome
Project participants. Each persona's self-reported ancestry is that participant's
own survey answer, and each named file is an assay that participant actually has.
Age, sex, trait, family history and conversational manner are written.

`index.md` in that directory is the case list: participant, file, trait, and the
expected outcome.

Cases are graded against an **acceptable set** of models rather than one right
answer, because selection has several defensible answers. Set sizes run from 2 to
21 depending on what the catalogue offers for that trait and ancestry, which is
why results are reported per case and never as a single rate: a pass on a
21-model case and a pass on a 2-model case are not the same result.

```bash
python3 grade.py --runs answer-side/<your-run-directory>
```

### The answer key is not in this repository

`answer-sets.csv` holds the acceptable models and the expected refusal reasons.
It is deliberately absent. A benchmark whose answers are public cannot test
anything afterwards, including a later version of this agent.

The personas, the case index and the graders are all here, so the benchmark can
be inspected, criticised and extended. Only the answers are held back. Ask if you
need them for review.

---

## How it is isolated

The agent runs in a bubblewrap mount namespace that starts empty. Everything it
can reach is listed in a generated profile and nothing else exists: not the
answer key, not other cases, not the rest of the filesystem. A path the profile
forgets is invisible rather than permitted.

Reference data is bind-mounted read-only, so nothing can be left behind for a
later run to find. All network egress goes through a local allow-list proxy that
logs every connection.

Before each run a launch gate checks three things and refuses to start if any
fail: that no staged file contains an answer-key string, that a probe running
under the agent's own profile cannot read or write anything it should not, and
that the jail is clean.

This is measured every run, not assumed. Results are in each run's
`manifest.json`.

---

## Platform

Linux only, for now.

The original harness ran on macOS under `sandbox-exec` with a seatbelt profile,
and the results in the accompanying manuscript came from there. This repository
carries the Linux port: bubblewrap, with a small `sandbox-exec` shim so the
harness does not need to know which platform it is on.

The two are not the same boundary. Seatbelt could not be made deny-default
without breaking the dynamic loader, so the macOS profile is allow-default with
denials layered on. Bubblewrap is the other way round. The Linux one is the
stronger of the two, but a run from one is not bit-for-bit a run from the other,
and that is worth saying before anyone compares them.

---

## Repository layout

```
build/
  AGENT.md, JUDGMENT-CORE.md    what the agent is told
  .claude/skills/               one skill per stage, prose not code
  tools/                        extraction only, no interpretation
  interfaces/                   contracts with the reference provider
  sim/personas/eval/            the 99 cases and index.md
harness/
  run_harness.py                run one case
  harness/                      staging, profile, probe, proxy, orchestrator
  sandbox_tools/                the only file the agent gets for talking out
  briefs/                       the task text for the control arms
reference-provider/             builds the reference distribution
grade.py                        grader
preflight.py, setup.sh          environment
fetch_refdata.py                the 24 GB of public reference data
```

Not in the repository, and cannot be: `build/refdata` (tens of gigabytes, fetched
by `fetch_refdata.py`), the participants' genotype files, and the answer key.

---

## Two things worth knowing before you run this

**The agent judges. Tools only extract.** No script decides anything a domain
expert could disagree with. Counting delimiters in a file is mechanical;
concluding it is a genotyping array on GRCh37 is not, so no detector script does
it. A rule engine fails silently the moment the rule is wrong, and catching
exactly that is the point.

**Coverage is measured by weight, not by counting variants.** The fraction of a
model's total |β| that this person's file can actually resolve. It can veto a
model and it can never promote one; ranking is on ancestry match and validation
quality.
