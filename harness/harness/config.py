"""Paths, constants and the lists the boundary is built out of. LINUX BUILD.

Diverges from the macOS config in five places and nowhere else: SANDBOX_PATH,
KEYCHAIN_DIR, and three entries each in deny_probe_paths and write_probe_paths
that named macOS-only locations (~/Library/Keychains, /private/tmp, ~/.zsh_history).
Every list that defines what the agent must not see is unchanged, so the two
platforms are graded against the same answer-leakage definition.

Everything here is data. The rules that act on it live in staging.py, sandbox.py and
probe.py. Keeping the lists in one file means an audit is a diff of one file, and means the
manifest can record the exact list a run was built against rather than a description of it.

Nothing in this module may import from the rest of the package.
"""
import json
import os

# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------

HARNESS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCT_ROOT = os.path.dirname(HARNESS_ROOT)

AGENT = os.path.join(PRODUCT_ROOT, "agent")
REFDATA_SRC = os.path.join(AGENT, "refdata")
PROVIDER_SRC = os.path.join(PRODUCT_ROOT, "reference-provider")

# Harness-private. 0700. No sandbox profile may ever name a path under here.
VAULT = os.path.join(HARNESS_ROOT, "vault")
VAULT_RUNS = os.path.join(VAULT, "runs")
VAULT_KEYS = os.path.join(VAULT, "keys")

SANDBOXES = os.path.join(HARNESS_ROOT, "sandboxes")
SANDBOX_TOOLS = os.path.join(HARNESS_ROOT, "sandbox_tools")

# ---------------------------------------------------------------------------
# The thirteen pipeline tools
#
# The other eleven files in agent/tools/ are analysis and build-time tools that no skill
# invokes. They are excluded because they are not needed AND because they are the worst
# leak in the directory: rerun_coverage_leaveout.sh lists the whole candidate shortlist,
# leaveout_dr2.sh states the patient's ancestry outright, cg_truth_extract.py hardcodes
# --sample <primary participant>.
# ---------------------------------------------------------------------------

PIPELINE_TOOLS = [
    "lib_run.py",
    "intake_hygiene.py",
    "file_facts.py",
    "normalize.py",
    "record.py",
    "ancestry.py",
    "pgs_catalog.py",
    "weight_coverage.py",
    "impute.py",
    "build_dr2_cohort.py",
    "impute_joint.py",
    "match_variants.py",
    "score_plink2.py",
    "interpret_request.py",
    "reference_adapter.py",
]

EXCLUDED_TOOLS_REASON = {
    "apply_leaveout_dr2.py": "analysis; names SAS hold-outs",
    "cg_truth_compare.py": "truth arm; carries the array-vs-CG adjudication",
    "cg_truth_extract.py": "truth arm; hardcodes --sample <primary participant>",
    "cg_truth_split.py": "truth arm",
    "coverage_breakdown.py": "analysis; names runs/slice-001 and SAS_AF",
    "leaveout_dr2.sh": "analysis; states the patient's ancestry",
    "loo_validate_ancestry.py": "validation",
    "palindromic_fraction.py": "analysis",
    "panel_index.py": "build-time; produces refdata/panel/sites_index",
    "rerun_coverage_leaveout.sh": "analysis; lists the full 7-model candidate shortlist",
    "stage_refdata.sh": "build-time fetcher",
}

SKILLS = ["read-and-reason", "ancestry", "select", "score", "interpret", "report"]

# Docs staged alongside the tools. interfaces/reference-distribution.md is cited by two
# skills and echoed into reference_adapter.py's own output JSON, so its absence would be a
# filesystem error at exactly the moment the pipeline is supposed to correctly refuse.
STAGED_DOCS = [
    ("AGENT.md", "AGENT.md"),
    ("JUDGMENT-CORE.md", "JUDGMENT-CORE.md"),
    ("interfaces/reference-distribution.md", "interfaces/reference-distribution.md"),
]

# ---------------------------------------------------------------------------
# refdata: what gets deleted from the per-run clone
#
# These trees were produced by subsetting 1000 Genomes through the patient's own array
# positions -- the provenance is preserved verbatim in the .pvar headers as
# `bcftools view -R /tmp/patient_positions.tsv`. Their pruned marker counts (27,405 and
# 9,956) are the two numbers ancestry.json cites as the evidence for its two-reference
# stability check, so staging them hands the agent both the completed projection basis and
# the check it is supposed to earn.
#
# phase3/ stays: it is the clean upstream 1000 Genomes and is a legitimate tool input.
# ---------------------------------------------------------------------------

REFDATA_DELETE = [
    ("ancestry-ref/build", "patient-position-derived PCA basis; 27,405 pruned markers is "
                           "ancestry.json's cited evidence"),
    ("ancestry-ref/build_p3chr1", "patient-position-derived chr1 PCA basis; 9,956 pruned "
                                  "markers is the second half of the stability check"),
    ("ancestry-ref/phase3_sub", "1000 Genomes subset through /tmp/patient_positions.tsv; "
                                "the site list IS the patient's array"),
    ("ancestry_build.log", "records the build recipe and the marker count"),
]

# Kept, but recorded in the manifest as a known soft key: this is the reference that was
# tried and rejected. Its presence plus the absence of any other single-file genome-wide
# reference is a nudge toward the rejected path. See docs/DECISIONS.md.
REFDATA_FLAGGED = [
    ("ancestry-ref/1kg.omni_broad_sanger_combined.b37.vcf.gz",
     "the reference that was tried and rejected; still the only single-file option after "
     "the deletions above, which is a real gap in the skill wording"),
]

# ---------------------------------------------------------------------------
# Private tokens
#
# The launch gate must match the real participant identifiers and one surname, so those
# values cannot be published. They load from a file outside the repository, and the load
# FAILS CLOSED: a missing, unreadable, malformed or empty file raises at import rather than
# returning an empty list. An empty list would not fail -- it would make the gate report
# `passed` while checking nothing, which is worse than having no gate.
# ---------------------------------------------------------------------------

PRIVATE_TOKENS_ENV = "PRS_HARNESS_TOKENS"
PRIVATE_TOKENS_DEFAULT = os.path.expanduser("~/.config/prs-harness/tokens.json")


def _load_private_tokens():
    path = os.environ.get(PRIVATE_TOKENS_ENV) or PRIVATE_TOKENS_DEFAULT
    where = "%s (override with the %s environment variable)" % (path, PRIVATE_TOKENS_ENV)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise RuntimeError("launch-gate token file not found: " + where)
    except (OSError, ValueError) as exc:
        raise RuntimeError("launch-gate token file unreadable or not JSON: %s: %s" % (where, exc))
    if not isinstance(data, dict):
        raise RuntimeError("launch-gate token file must be a JSON object: " + where)
    for key in ("hard", "provider_hard"):
        vals = data.get(key)
        if (not isinstance(vals, list) or not vals
                or not all(isinstance(v, str) and v.strip() for v in vals)):
            raise RuntimeError("launch-gate token file key %r must be a non-empty list of "
                               "non-empty strings: %s" % (key, where))
    run = data.get("deny_eval_run")
    if not isinstance(run, str) or not run.strip() or "/" in run or ".." in run:
        raise RuntimeError("launch-gate token file key 'deny_eval_run' must be a bare "
                           "directory name: " + where)
    return {"hard": list(data["hard"]), "provider_hard": list(data["provider_hard"]),
            "deny_eval_run": run}


_PRIVATE = _load_private_tokens()


# ---------------------------------------------------------------------------
# The launch gate
#
# HARD tokens fail the run. SOFT tokens are recorded and reported but do not fail, because
# each has a legitimate non-leaking occurrence (a five-way superpopulation enumeration, a
# generic numeric string) and failing on them would train us to disable the gate.
# ---------------------------------------------------------------------------

HARD_TOKENS = [
    "slice-001",
    *_PRIVATE["hard"],
    "PGS005168", "PGS012535", "PGS012531", "PGS002050",
    "PGS001841", "PGS001340", "PGS001339", "PGS003725",
    "atrial fibrillation", "atrial_fibrillation",
    "MONDO_0004981",
    "-1.307", "1.307975913",
    "live-record",
]

SOFT_TOKENS = [
    ("SAS", "appears in a five-way superpopulation enumeration in ancestry.py and "
            "ancestry/SKILL.md:95"),
    ("OPEN_QUESTIONS", "a citation pointer, not answer content -- the skills and three "
                       "tools cite Q1/Q3/Q6 and each states the substance before citing, "
                       "so the agent never needs the file. The file itself is never staged "
                       "and is on the denial probe list. The agent is told in its brief "
                       "that it is out of scope, so the pointers do not send it hunting."),
    ("patient_positions", "matches `def patient_positions(vcf)` in weight_coverage.py, "
                          "which is a function name. The real case -- reference data subset "
                          "through the patient's own array positions -- is caught precisely "
                          "by the .pvar header scan in staging._assert_no_patient_positions, "
                          "which greps provenance headers rather than source code."),
    ("0.963", "the delivered coverage figure; also a plausible generic float"),
    ("0.966", "the delivered coverage figure as a percentage"),
    ("27405", "the pruned marker count of the deleted PCA basis"),
    ("9956", "the chr1-only pruned marker count"),
]

# Binary and archive extensions the token grep skips. Recorded in the manifest so the gate's
# blind spot is stated rather than implied.
# The three sandboxes are different boundaries and need different lists. One list applied
# everywhere would be a list nobody can satisfy, and the way that fails is that somebody
# turns the gate off.
#
#   prs       the full list. It must learn the trait by asking and the model by selecting.
#   provider  a reduced list. The model id and the trait arrive IN the request it is
#             answering, so seeing them in its own tooling tells it nothing it was not
#             handed. What it must not see is the patient, the outcome, or another run.
#   persona   not enforced. The persona file is the person's own account of themselves --
#             it is the input, not a key -- and it never enters the PRS sandbox.
PROVIDER_HARD_TOKENS = [
    "slice-001",
    *_PRIVATE["provider_hard"],
    "-1.307", "1.307975913",
    "live-record",
]

TOKEN_POLICY = {
    "prs": {"enforced": True, "hard": None,
            "why": "the agent under test; it must learn the trait by asking and the model "
                   "by selecting"},
    "provider": {"enforced": True, "hard": PROVIDER_HARD_TOKENS,
                 "why": "the model id and trait are in the request it is answering, so they "
                        "are inputs rather than answers. The patient, the score and any "
                        "other run remain out of bounds."},
    "persona": {"enforced": False, "hard": None,
                "why": "the persona file is the person's own account of themselves. It is "
                       "the input to this sandbox and never enters the PRS one."},
}

GREP_SKIP_EXT = {
    ".gz", ".bgz", ".zip", ".bz2", ".tbi", ".csi", ".bref3", ".pgen", ".pvar", ".psam",
    ".bed", ".bim", ".fam", ".jar", ".fa", ".fai", ".gzi", ".bin", ".sscore", ".eigenvec",
    ".eigenval", ".afreq", ".so", ".dylib", ".class",
}
GREP_MAX_BYTES = 8 * 1024 * 1024

# ---------------------------------------------------------------------------
# Scrub rules: applied to the staged COPY, never to agent/.
#
# These files are inside the sandbox, so no boundary helps. pgs_catalog.py is the worst of
# them -- it is the first tool select invokes and its docstring hands over the trait and two
# of the exact candidate models in the very command the agent is about to run.
# ---------------------------------------------------------------------------

SCRUB_RULES = [
    # (relative path in sandbox, literal to find, replacement, why)
    ("tools/pgs_catalog.py", "atrial fibrillation", "type 2 diabetes",
     "docstring example trait, in the first tool select runs"),
    ("tools/pgs_catalog.py", "PGS012535,PGS005168", "PGS000001,PGS000002",
     "docstring example names two real candidates"),
    ("tools/pgs_catalog.py", "PGS012535", "PGS000001", "docstring example model id"),
    ("tools/pgs_catalog.py", "MONDO_0004981", "MONDO_0000001",
     "the trait's ontology id -- names the trait as precisely as the label does, in the "
     "docstring of the first tool select runs"),
    ("tools/weight_coverage.py", "PGS012535", "PGS000001", "docstring example model id"),
    ("tools/match_variants.py", "PGS012535", "PGS000001", "docstring example model id"),
    # The flow diagram splits the trait across two lines, so it takes two rules. Both
    # replacements are the same width as the original to keep the ASCII box aligned.
    # Width-preserving, and checked as such by staging._assert_scrub_widths. The flow
    # diagram is the first thing the agent reads; a box whose borders no longer line up
    # reads as a corrupted file and invites the agent to go looking for an intact copy.
    ("AGENT.md", '"atrial', '"sample',
     "the trait in the flow diagram, read before the agent has asked anything"),
    ("AGENT.md", 'fibrillation"', '   condition"',
     "second half of the trait in the flow diagram"),
    # The INT-1 contract's example request carries the trait twice, as label and as
    # ontology id. The contract has to be staged -- two skills cite it and the adapter
    # echoes its path into the blocked-response JSON -- so the example gets neutralised
    # instead. The shape of the request is what the agent needs; the trait is not.
    ("interfaces/reference-distribution.md", '"MONDO_0004981", "label": "atrial fibrillation"',
     '"MONDO_0000001", "label": "example trait"',
     "the trait, in the contract's own example request"),
    # Same file, staged into the provider sandbox under a different name. The provider's
    # request carries the real trait; there is no reason for the contract's worked example
    # to carry it as well.
    ("CONTRACT.md", '"MONDO_0004981", "label": "atrial fibrillation"',
     '"MONDO_0000001", "label": "example trait"',
     "the trait, in the contract's example request, provider copy"),
]

# Applied to every staged text file after the literal rules above.
SCRUB_RUN_ID_PATTERN = r"\bslice-001\b"
SCRUB_RUN_ID_REPLACEMENT = "<RUN_ID>"

# ---------------------------------------------------------------------------
# The denial probe. Every path here is attempted, under the identical profile, by a probe
# process at launch. An unexpected success fails the run.
#
# This is what turns "we thought about it" into "we checked this run".
# ---------------------------------------------------------------------------

def deny_probe_paths(home):
    p = os.path.join
    return [
        (p(AGENT, "runs/slice-001/ledger.jsonl"), "the prior run's ordered decision history"),
        (p(AGENT, "runs/slice-001/ancestry/ancestry.json"), "the ancestry call"),
        (p(AGENT, "runs/slice-001/score/RESOLVED_FRACTIONS.md"), "the coverage table"),
        (p(AGENT, "runs/slice-001/report/report_output_placed.md"), "the finished report"),
        (p(AGENT, "runs/slice-001/truth-cg"), "the Complete Genomics truth arm"),
        (p(AGENT, "runs/" + _PRIVATE["deny_eval_run"]), "a second scenario's full key"),
        (p(AGENT, "BUILD_LOG.md"), "states the ancestry call and the distance"),
        (p(AGENT, "OPEN_QUESTIONS.md"), "Q10 carries the final corrected answer"),
        (p(AGENT, "oracle"), "independent pgsc_calc verification outputs"),
        (p(AGENT, ".git"), "tracked docs; log subjects alone leak"),
        (p(AGENT, "sim/personas"), "the persona files"),
        (p(AGENT, "refdata/ancestry-ref/build"), "deleted from the clone; assert the "
                                                 "original is unreachable too"),
        (p(PRODUCT_ROOT, "reference-provider/work/cohort_score.sscore"),
         "the 300 per-sample scores that ARE the distribution"),
        (p(PRODUCT_ROOT, "reference-provider/reference_distribution.json"),
         "the finished INT-1 artifact"),
        (p(PRODUCT_ROOT, "frontend/prs_ui/answers.py"),
         "answer key hiding in application code"),
        (p(PRODUCT_ROOT, "pgp-candidates"), "thirteen Complete Genomics truth files"),
        (p(PRODUCT_ROOT, "pgp-fixtures"), "four more truth files"),
        (p(PRODUCT_ROOT, "old-git-history"), "bare repo; log subjects leak the ancestry call"),
        (p(PRODUCT_ROOT, "build-inputs-backup-docs/prd.md"),
         "a second copy of the patient-naming PRD outside agent/"),
        (os.path.expanduser("~/Documents/ProjectVault"), "the Obsidian vault. Absent on "
         "the VM, so this probe returns `absent` rather than `denied` there, which is "
         "reported as its own state and is not a pass"),
        (os.path.expanduser("~/.claude/projects"), "prior session transcripts"),
        (os.path.expanduser("~/.claude/file-history"), "verbatim content snapshots"),
        (os.path.expanduser("~/.claude/paste-cache"), "pasted answer-key excerpts"),
        (os.path.expanduser("~/.claude/history.jsonl"), "prompt history"),
        (os.path.expanduser("~/.claude/.credentials.json"),
         "the real token file. The profile binds a COPY of this to the jail HOME, so the "
         "original path must still be unreachable by its own name"),
        (os.path.expanduser("~/.bash_history"), "shell history"),
        # The macOS list probes /private/tmp/claude-<uid>, because there the CLI's
        # scratch root is shared across projects and listing it would expose other
        # sessions. There is no such path here: TMPDIR points inside the sandbox and
        # /tmp is a private tmpfs that dies with the run, so the host's /tmp is not
        # reachable under any name. Probing "/tmp" would read the private tmpfs, come
        # back READABLE, and fail the run for the one thing that is working correctly.
        # Nothing replaces it, and that absence is the record.
        (VAULT, "the harness's own private state"),
    ]


# Write probes. Seatbelt enforces file-write* even though chmod and chflags do not.
def write_probe_paths(sandbox_root):
    return [
        (os.path.join(REFDATA_SRC, ".harness_write_probe"),
         "the original refdata must be immutable to the sandboxed process"),
        (os.path.join(sandbox_root, "refdata", ".harness_write_probe"),
         "the per-run clone must be read-only to the agent"),
        (os.path.expanduser("~/.claude/.harness_write_probe"), "the real CLI state dir"),
        (os.path.expanduser("~/Documents/ProjectVault/.harness_write_probe"),
         "vault-writing skills must not reach it. Absent on the VM"),
        (os.path.join(VAULT, ".harness_write_probe"), "the harness's own state"),
        (os.path.expanduser("~/.claude/.harness_write_probe_real"),
         "the real CLI state dir, by its own path"),
    ]


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

# The proxy permits exactly these hosts. pgs_catalog.py cannot run without either of them.
PROXY_ALLOW_HOSTS = [
    "www.pgscatalog.org",
    "ftp.ebi.ac.uk",
]

# The CLI's own hosts, kept separate from the data hosts above because they are the transport
# the agent runs on rather than anything it reads. Until 2026-08-06 these were written at the
# call site in orchestrator.py, which meant the list this file records was not the list a run
# was built against -- the thing this module's docstring says it exists to prevent.
#
# platform.claude.com was added 2026-08-06 after two runs died on `401 OAuth access token has
# expired`. In run-20260806-010140-be6ab8 the CLI hit it four times in six seconds
# (13:02:53-58), the proxy denied all four because it was not listed, and the run stopped six
# seconds later. Without it an access token that expires mid-run cannot be refreshed from
# inside the sandbox, so every run has a hard ceiling at whatever was left on the token it
# started with. The evidence is the retry loop and the timing, not a confirmed identification
# of the refresh endpoint; a run surviving past an expiry is what would confirm it.
CLI_ALLOW_HOSTS = [
    "api.anthropic.com",
    "statsig.anthropic.com",
    "platform.claude.com",
]

PROXY_PORT_RANGE = (18800, 18899)

# ---------------------------------------------------------------------------
# Process environment. Anything not listed is dropped.
#
# USER and LOGNAME are load-bearing: without them the CLI cannot reach the login keychain
# and prints "Not logged in". This is true unsandboxed too -- it is an environment issue,
# not a sandbox one.
# ---------------------------------------------------------------------------

ENV_ALLOW = ["PATH", "USER", "LOGNAME", "SHELL", "LANG", "TERM"]

# Linux. /usr/local/bin first because plink2 and the Linux liftOver live there;
SANDBOX_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Keys copied from the real ~/.claude.json into the jail HOME. An allow-list, not a
# strip-list, so a new key added by a CLI upgrade is excluded by default rather than
# included by default.
CLAUDE_JSON_SEED_KEYS = [
    "oauthAccount",
    "userID",
    "machineID",
    "installMethod",
    "hasCompletedOnboarding",
    "lastOnboardingVersion",
    "firstStartTime",
    "numStartups",
    "migrationVersion",
]

# No keychain on Linux. The CLI reads ~/.claude/.credentials.json, which the
# bubblewrap profile binds in as a single read-only file. Kept as a name so
# profile.build_profile's allow_keychain argument still resolves.
KEYCHAIN_DIR = os.path.expanduser("~/.claude")

# ---------------------------------------------------------------------------
# Timings, measured. These size the transport rather than being guessed at.
# ---------------------------------------------------------------------------

TIMEOUT_ASK_PERSON = 900          # a human may be typing
# The provider may have to BUILD its reference panel before it can build a distribution,
# and the panel is proportional to the model. Measured 2026-08-05: 1,113,668 variants took
# ~34 min via bcftools view -T over phase3, one chromosome at a time. PGS012535 has
# 7,356,472 variants -- 6.6x, so ~4h. At the old 1800s the provider was killed six times
# over, five hours into a run, after imputation had already succeeded.
#
# The panel is rebuilt per run rather than cached, deliberately: a cache means nothing
# exercises the build path after the first run, and two runs are then not comparable.
# Faster is worth having -- the per-chromosome bcftools calls are independent and could run
# in parallel -- but faster, not cached.
TIMEOUT_PROVIDER = 21600          # 6h: reference panel build + distribution
# Joint imputation (tools/impute_joint.py) is the binding term, and it is slow on purpose:
# accuracy over speed. Measured 2026-08-04 on chr20 at n=301 hold-outs -- ~24 min/chromosome,
# of which Beagle is about half and the per-chromosome reference rebuild the other half. The
# whole genome is therefore ~5.7h, plus ~40 min for the rest of the pipeline.
#
# Three measured negatives behind that number, so nobody re-derives them. All on chr20,
# 2026-08-04, Beagle time only:
#   full ref 2203 samples, 302 targets, 1,739,315 markers ...... 180s
#   same, reference cut to 31,904 markers (typed + model) ...... 174s   (54x fewer markers, -3%)
#   full ref 2454 samples, 51 targets .......................... 200s   (6x fewer targets, +11%)
# So neither marker count nor target count is the cost. Reference SAMPLE count is, and it looks
# linear: +11% samples gave +11% time. That rules out both restricting the reference to the
# positions the score needs and shrinking the hold-out batch. Beagle is also CPU-only Java with
# a sequential forward-backward pass, so there is no GPU path.
#
# The one lever left is the per-chromosome reference rebuild, which is about half the wall time
# (chr20: 374s total, 180s of it Beagle). Prebuilding a bref3 panel per superpopulation would
# take the genome from ~5.7h to ~2.9h. Not done: it fixes the hold-out set at panel-build time,
# so --size and --seed stop being agent judgments.
# ~5h joint imputation + up to ~4h inside the provider handoff + everything else. 8h was
# not enough for a large model; 14h leaves headroom without being unbounded. If a run needs
# more than this, the answer is a faster panel build, not a larger number.
TIMEOUT_AGENT_TOTAL = 50400       # 14h
POLL_INTERVAL = 0.2
