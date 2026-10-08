"""The INT-1 handoff.

The harness resolves the request's file references, stages exactly what it has, and hands
the request to a provider agent in its own sandbox. It does not decide whether a reference
can be built.

That last sentence is the whole design. The harness could decide whether the inputs suffice
and write the refusal itself. It does not, because deciding that a usable reference cannot be
built is precisely the call INT-2 exists for, and a harness that makes it bypasses the agent
whose existence is the point. The harness stages honestly, describes what is present and what
is not, and lets the provider agent reach its own conclusion and say why.

WHAT CHANGED 2026-08-04/05. The provider used to be pre-baked for one model in three ways.
Two are gone:

  * `build_reference.py` was de-hardcoded. Input paths, the cohort size, the weight column
    and the sum-of-|BETA| target are now arguments; the target has no default and is an
    OPTIONAL regression guard rather than a mandatory check against one run's constant. The
    artifact's trait and pgs_id are read from the scoring file's own `#` header instead of
    being asserted, so a different model can no longer be mislabelled as PGS005168.
  * `build_reference_panel.py` now exists, so `ref_1kg_<PGS>.vcf.gz` has a generating
    script. A missing panel is no longer a dead end -- the provider is given the tool and
    `phase3/` to build from, and may build one. Rebuilt per run, never cached: a cache would
    mean nothing exercises the build path after the first run, and two runs would not be
    comparable.

What remains model-specific is only the input BASENAMES, which is a staging convention.

`PROVIDER_NATIVE_PGS` therefore no longer means "the only model that can work". It records
which model the pre-staged inputs happen to be for, so a run that got a panel for free is
distinguishable afterwards from one that had to build its own.

What the harness DOES record is whether the inputs matched, so a run where the INT-1 arm was
never truly exercised is visible as such afterwards rather than being read as a pass.
"""
import json
import os
import shutil

from . import config, staging

# The staged basenames. projection.json, ref_pca.eigenvec, ref_pca.eigenval and
# sample_labels.json are read by exactly these names through main()'s
# `I = lambda name: join(args.inputs, name)` -- there is no per-file flag for them, so the
# names are the interface. The rest are passed to the tool as arguments.
PROVIDER_INPUTS = {
    "dosages": "dosages_leaveout_{pgs}.tsv",
    "score_input": "plink_score_input.tsv",
    "scoring": "{pgs}_hmPOS_GRCh37.txt.gz",
    "projection": "projection.json",
    "eigenvec": "ref_pca.eigenvec",
    "eigenval": "ref_pca.eigenval",
    "labels": "sample_labels.json",
    "ref_vcf": "ref_1kg_{pgs}.vcf.gz",
}

# The model the provider's own staged inputs were built for.
PROVIDER_NATIVE_PGS = "PGS005168"

BRIEF = """\
You are the reference-distribution provider for one request against the INT-1 contract.

You have been given: the request, `build_reference.py`, a `plink2` binary, an `inputs/`
directory, and `CONTRACT.md`. Nothing else. You cannot see the patient, the conversation, the
agent that sent this, or any other run.

Read `CONTRACT.md` first -- it is the interface you must satisfy. Then read
`inputs/INPUTS.md`, which lists exactly which input files are present and which are absent.
Then read the request.

Your job is to decide whether a usable reference distribution can be built for THIS request,
and then either build it or say why not.

Two outcomes are correct:

  * You build it. Write the artifact to the path given below.

  * You cannot build it. Write a well-formed artifact to the same path with
    `"usable": false` and a `reason` that names the ACTUAL cause. Per INT-2 a refusal is a
    correct outcome, not a failure -- but only if the reason is true. "Too few genetically
    similar participants exist" and "the inputs for this model are not present" are
    completely different claims, and putting the wrong one into a clinical artifact is worse
    than refusing loudly.

Whichever you do, every field the contract requires must be present. A refusal artifact still
needs `source`, `version_string`, `ancestry_grouping_definition`, `cohort_construction_rule`,
`n_participants`, `too_few_threshold_used`, `assay_layer`, `scoring_parity` and
`distribution` -- with nulls where a refusal makes a value meaningless, not zeros. A zero
quantile is a claim; a null is an absence.

## The tools, and how they are invoked

`build_reference.py` takes every input as an argument -- there are no defaults to fall back
on, because a default would be one run's value applied to every other run:

    python3 build_reference.py \\
      --dosages inputs/<the dosages file named in INPUTS.md> \\
      --score-input inputs/plink_score_input.tsv \\
      --scoring inputs/<the scoring file> \\
      --ref-vcf inputs/<the reference panel VCF> \\
      --request request.json \\
      --effect-weight-col <the weight column, read off the scoring file's own header> \\
      --reference-assay "<what assay produced the reference genotypes, in your words>" \\
      --k <how many nearest reference samples to select> \\
      --min-cohort <refuse below this cohort size> \\
      --k-rationale "<why you chose these values>" \\
      --workdir work --out {out}

`--score-input` is the file the patient's own score was computed from. The cohort is scored
from that same file by the same plink2 recipe, and the tool reports how many of its variants
plink2 actually used for each side; `scoring_parity` follows from those counts.

`--k` and `--min-cohort` are yours to choose and the contract asks you to state what you
used -- `cohort_construction_rule` and `too_few_threshold_used` are required fields. There is
no derived value for either; say what you chose and why, in `--k-rationale`.

`--max-kth-distance` is also available: refuse when the k-th nearest reference sample is
further than that distance in PC space. It has no default and the tool applies no check
without it. Whether to set one, and to what, is your call; the artifact records the value
used, or that there was none, and `--k-rationale` is where to say why.

`--expected-abs-beta` is OPTIONAL and is a regression guard against a PRIOR run of this same
patient and model. Do not compute it from the very data it would check -- that turns a real
check into a no-op. Omitted, the tool records the computed value and states plainly that the
join was not verified, which is the honest position on a first run.

**If `inputs/INPUTS.md` says the reference panel VCF is absent, you can build one.**
`build_reference_panel.py` extracts the model's positions from the 1000 Genomes panel:

    python3 build_reference_panel.py \\
      --scoring inputs/<the scoring file> \\
      --chr-col hm_chr --pos-col hm_pos \\
      --phase3-dir <the phase3 directory named in INPUTS.md> \\
      --chromosomes 1-22 --workers 4 --threads 2 \\
      --out inputs/<the reference panel basename INPUTS.md names as absent>

`--workers` extracts that many chromosomes concurrently and `--threads` is bcftools
threads within each one; they multiply, so keep their product near the core count.

How long it takes scales with the model's position count, so there is no single figure:
roughly half an hour for a model of about 1.1M positions at the concurrency above, and
proportionally longer for a larger one. Treat that as an order of magnitude and not a
deadline. **Running longer than the estimate is not a reason to refuse** -- if you decide a
usable distribution cannot be built, the `reason` must name the actual cause, and "it was
slower than the brief said" is not one.

Build it at the MODEL's positions, which is what the command
above does: a patient's imputed dosages come out of this same panel, so building at the
model's positions covers every position the patient could have resolved. Building at the
patient's positions instead would leave the cohort scored over a different variant set from
the patient, and that is measured -- a 0.55% mismatch moved a percentile by 8.3 points.

Do not edit either tool. If one will not run for this request, that is a finding to report in
`reason`, not a thing to patch around.

Write your artifact to: {out}
Working directory: {cwd}
"""


class AmbiguousInput(Exception):
    """More than one candidate for an input that must be unique.

    Refusing is the only safe answer. slice-001 really does carry both
    ancestry/projection.json and ancestry-fixed/projection.json, and they differ materially
    -- different nearest neighbours, different corrected PCs. Both record the same
    reference_build, which is what makes picking the wrong one silent: the provider's
    neighbour_selfcheck only verifies the projection against its OWN recorded neighbours, so
    a stale-but-self-consistent projection passes every check and lands a wrong cohort, a
    wrong distribution and a wrong percentile.
    """


class OutsideRun(Exception):
    """A path the agent supplied resolves outside the requesting run's own directory.

    The harness copies these files with its own authority, into a sandbox the requesting
    agent cannot reach. A request naming /etc/..., another run, or a symlink that points out
    of the run would have the harness fetch it on the agent's behalf. Refused, not staged.
    """


def contained(path, root):
    """realpath(path) if it lies inside realpath(root), else None.

    Symlinks are resolved before the comparison, so a link inside the run that points out
    of it is outside.
    """
    rp, rr = os.path.realpath(path), os.path.realpath(root)
    return rp if rp == rr or rp.startswith(rr + os.sep) else None


class ProviderHandoff:
    def __init__(self, sandbox_root, record, transcript):
        self.root = sandbox_root
        self.inputs = os.path.join(sandbox_root, "inputs")
        self.workdir = os.path.join(sandbox_root, "work")
        self.out = os.path.join(sandbox_root, "reference_distribution.json")
        self.record = record
        self.transcript = transcript
        self.staged = {}
        self.absent = {}
        self.request = None
        self.pgs = None
        self.ambiguous = None
        self.refused = None
        self.basis = None
        self.run_dir = None
        # Set at stage() time: whether the provider has what it needs to build a missing
        # reference panel itself. Recorded so a run where it could not is distinguishable
        # from one where it chose not to.
        self.phase3_dir = os.path.join(config.REFDATA_SRC, "ancestry-ref", "phase3")
        self.panel_buildable = os.path.isdir(self.phase3_dir)

    # -- staging ---------------------------------------------------------
    def stage(self, request_path, prs_sandbox_root, run_dir):
        """Copy in what exists, name what does not, and record both.

        Every file taken from the PRS sandbox on the strength of a path the agent wrote must
        resolve inside `run_dir`, the requesting run's own runs/<RUN>/ directory.
        """
        os.makedirs(self.inputs, exist_ok=True)
        os.makedirs(self.workdir, exist_ok=True)
        self.run_dir = run_dir

        real = contained(request_path, run_dir)
        if real is None:
            raise OutsideRun("the request %r resolves to %s, outside the requesting run's "
                             "directory %s" % (request_path, os.path.realpath(request_path),
                                               run_dir))
        request_path = real
        with open(request_path) as fh:
            self.request = json.load(fh)
        self.pgs = ((self.request.get("model") or {}).get("pgs_id") or "").strip()

        # The request is copied, never linked. Its own file references point into the PRS
        # sandbox, which the provider cannot reach, so the copy is rewritten to name the
        # local staged files instead -- otherwise the provider would read paths it cannot
        # open and could mistake an unreachable path for a missing input.
        req_local = os.path.join(self.root, "request.json")
        local = dict(self.request)
        patient = dict(local.get("patient") or {})
        patient["resolved_positions_manifest_original"] = patient.get(
            "resolved_positions_manifest")
        patient["resolved_positions_manifest"] = os.path.join(
            "inputs", PROVIDER_INPUTS["dosages"].format(pgs=self.pgs or "UNKNOWN"))
        patient["score_input_original"] = patient.get("score_input")
        patient["score_input"] = os.path.join("inputs", PROVIDER_INPUTS["score_input"])
        local["patient"] = patient
        local["_harness_note"] = (
            "Paths rewritten to the local inputs/ directory. The original values, which "
            "pointed into the requesting agent's sandbox, are preserved alongside.")
        with open(req_local, "w") as fh:
            json.dump(local, fh, indent=2)
        self.record.add(req_local, request_path, "int1-request")

        want = {k: v.format(pgs=self.pgs or "UNKNOWN") for k, v in PROVIDER_INPUTS.items()}

        # From the requesting run.
        self._try(want["dosages"], self._resolve_patient_path("resolved_positions_manifest",
                                                              prs_sandbox_root),
                  "the patient's resolved per-variant dosages, from the requesting run")
        self._try(want["score_input"], self._resolve_patient_path("score_input",
                                                                  prs_sandbox_root),
                  "the score input the patient's raw score was computed from; the cohort "
                  "is scored from this same file")
        self._try(want["scoring"], self._resolve_scoring(),
                  "the harmonized scoring file the requesting run actually used")
        projection = self._resolve_projection()
        self._try(want["projection"], projection,
                  "the patient's PCA projection, from the requesting run")

        # The PCA basis. It MUST be the one the patient's projection was computed against;
        # a mismatched pair puts the patient's coordinates in a different decomposition from
        # the panel's, and every distance downstream is meaningless. build_reference.py has
        # a hard self-check for exactly this and stops rather than reconciling. The basis is
        # identified by the projection's own record of it, and its labels travel with it:
        # they are the labels the projection's recorded neighbours were counted with.
        base = self._resolve_pca_basis(prs_sandbox_root, projection)
        self._try(want["eigenvec"], os.path.join(base, "ref_pca.eigenvec") if base else None,
                  "the PCA basis the patient's projection was computed against")
        self._try(want["eigenval"], os.path.join(base, "ref_pca.eigenval") if base else None,
                  "eigenvalues of that basis; absent, the artifact silently changes shape")
        self._try(want["labels"], os.path.join(base, "sample_labels.json") if base else None,
                  "that basis's own sample-to-population labels, the ones ancestry.py "
                  "counted the projection's recorded neighbours with")

        # The model-specific reference VCF. Present for exactly one model.
        native = os.path.join(config.PROVIDER_SRC, "inputs",
                              PROVIDER_INPUTS["ref_vcf"].format(pgs=PROVIDER_NATIVE_PGS))
        self._try(want["ref_vcf"], native if self.pgs == PROVIDER_NATIVE_PGS else None,
                  "1000 Genomes genotypes at this model's positions")

        self._write_inputs_readme(want)
        return self.summary()

    def stage_guarded(self, request_path, prs_sandbox_root, run_dir):
        """stage(), but an ambiguous input is a reported refusal rather than a coin flip,
        and a path outside the requesting run is refused rather than fetched."""
        try:
            return self.stage(request_path, prs_sandbox_root, run_dir)
        except AmbiguousInput as exc:
            self.ambiguous = str(exc)
            return self.summary()
        except OutsideRun as exc:
            self.refused = str(exc)
            return self.summary()

    def _try(self, basename, src, why):
        dest = os.path.join(self.inputs, basename)
        if src and os.path.exists(src):
            shutil.copyfile(src, dest)
            rec = self.record.add(dest, src, "provider-input", note=why)
            self.staged[basename] = {"source": src, "sha256": rec.get("sha256"),
                                     "bytes": rec.get("bytes"), "why": why}
        else:
            self.absent[basename] = {"why_wanted": why,
                                     "looked_for": src or "(nothing to look for)"}

    # -- resolution ------------------------------------------------------
    def _resolve_patient_path(self, field, prs_root):
        """A file the request's patient block names, resolved against the sandbox.

        The request is authoritative for which file. Guessing a filename here would risk
        handing over a different variant set from the one the score was computed on, and
        every parse would succeed -- the failure would be silent and would land in the
        percentile. Where the file is, though, is checked rather than taken: it must resolve
        inside the requesting run's directory, or the request is refused.
        """
        p = ((self.request.get("patient") or {}).get(field) or "")
        if not p:
            return None
        cands = [p if os.path.isabs(p) else os.path.join(prs_root, p)]
        # The request may name a path relative to the run root rather than the sandbox root.
        cands.append(os.path.join(prs_root, p.lstrip("./")))
        for cand in cands:
            if not os.path.lexists(cand):
                continue
            real = contained(cand, self.run_dir)
            if real is None:
                raise OutsideRun("patient.%s %r resolves to %s, outside the requesting "
                                 "run's directory %s" % (field, p, os.path.realpath(cand),
                                                         self.run_dir))
            if os.path.isfile(real):
                return real
        return None

    @staticmethod
    def _all(root, pred):
        """Every match, in a deterministic order."""
        hits = []
        for cur, dirs, files in os.walk(root):
            dirs.sort()
            for f in sorted(files):
                if pred(cur, f):
                    hits.append(os.path.join(cur, f))
        return hits

    @staticmethod
    def _one(hits, what):
        if not hits:
            return None
        if len(hits) > 1:
            raise AmbiguousInput("%s is ambiguous: %d candidates -- %s" % (what, len(hits), hits))
        return hits[0]

    def _in_run(self, hit):
        real = contained(hit, self.run_dir)
        if real is None:
            raise OutsideRun("%s resolves to %s, outside the requesting run's directory %s"
                             % (hit, os.path.realpath(hit), self.run_dir))
        return real

    def _resolve_scoring(self):
        if not self.pgs:
            return None
        # Exact basename, not startswith: `PGS0_hmPOS_GRCh38.txt.gz` also starts with the
        # model id, and staging it under the GRCh37 name would present a build mismatch to
        # the provider as a match.
        want = PROVIDER_INPUTS["scoring"].format(pgs=self.pgs)
        hit = self._one(self._all(self.run_dir, lambda c, f: f == want), "the scoring file")
        return self._in_run(hit) if hit else None

    def _resolve_projection(self):
        hit = self._one(self._all(self.run_dir, lambda c, f: f == "projection.json"),
                        "projection.json")
        return self._in_run(hit) if hit else None

    def _resolve_pca_basis(self, prs_root, projection_path):
        """The basis directory the patient's projection was computed against.

        ancestry.py copies the basis's reference_build.json, generated_utc and all, into
        projection.json as `reference_build`, so the basis is the directory whose own
        reference_build.json equals that record exactly. Anything looser is ambiguous in a
        sandbox that holds both refdata/ancestry-ref/hgdp_basis and a basis the agent built
        under runs/<RUN>/: both carry ref_pca.eigenvec and ref_pca.afreq. No match leaves the
        basis absent, which the provider sees and reports.
        """
        try:
            with open(projection_path) as fh:
                want = json.load(fh).get("reference_build")
        except (TypeError, OSError, ValueError, AttributeError):
            want = None
        if not want:
            self.basis = {"dir": None, "why": "projection.json carries no reference_build"}
            return None
        dirs, n_bases = [], 0
        for cur, d, files in os.walk(prs_root):
            d.sort()
            if "ref_pca.eigenvec" not in files or "ref_pca.afreq" not in files:
                continue
            n_bases += 1
            try:
                with open(os.path.join(cur, "reference_build.json")) as fh:
                    if json.load(fh) == want:
                        dirs.append(cur)
            except (OSError, ValueError):
                continue
        self.basis = {"n_basis_directories_found": n_bases, "candidates": dirs,
                      "matched_on": "reference_build.json equal to projection.json's "
                                    "reference_build"}
        staged = ("ref_pca.eigenvec", "ref_pca.eigenval", "sample_labels.json")

        def fingerprint(d):
            return tuple(staging.sha256(os.path.join(d, f))
                         if os.path.isfile(os.path.join(d, f)) else None for f in staged)

        # Copies of one basis are one basis: if every candidate's staged files hash the
        # same, the choice between them changes nothing that is staged.
        if len(dirs) > 1 and len({fingerprint(d) for d in dirs}) == 1:
            dirs = dirs[:1]
            self.basis["note"] = "identical copies; the first in walk order was staged"
        base = self._one(dirs, "the PCA basis directory")
        if base:
            for f in staged:
                path = os.path.join(base, f)
                if os.path.lexists(path) and contained(path, prs_root) is None:
                    raise OutsideRun("%s resolves to %s, outside the PRS sandbox"
                                     % (path, os.path.realpath(path)))
        self.basis["dir"] = base
        return base

    def _write_inputs_readme(self, want):
        lines = [
            "# What is in this directory",
            "",
            "`build_reference.py` reads `projection.json`, `ref_pca.eigenvec`,",
            "`ref_pca.eigenval` and `sample_labels.json` from here by those exact names; there",
            "is no flag for them. The others are passed as arguments -- see the brief. The",
            "request itself is `request.json`, one directory up.",
            "",
            "## Present",
            "",
        ]
        for name in sorted(self.staged):
            s = self.staged[name]
            lines.append("- `%s` (%s bytes) -- %s" % (name, s["bytes"], s["why"]))
        lines += ["", "## Absent", ""]
        if not self.absent:
            lines.append("- nothing; every input the tool asks for is present.")
        for name in sorted(self.absent):
            lines.append("- `%s` -- %s" % (name, self.absent[name]["why_wanted"]))
        lines += [
            "",
            "## The verification target has no default, on purpose",
            "",
            "`--expected-abs-beta` is the sum of |BETA| over the join between the scoring",
            "file and the patient's resolved set. It is a REGRESSION GUARD against a prior",
            "run of this same patient and model, not a property of the scoring file -- the",
            "value changes whenever the resolved set changes, so there is no correct default",
            "and the tool no longer carries one.",
            "",
            "Omit it on a first run. The tool records the computed value and states that the",
            "join was not verified, which is true. **Do not compute a value from the very",
            "data it is meant to check and pass that** -- it would turn a real check into a",
            "no-op while making the artifact claim a verification happened.",
            "",
            "`--expected-imputed-abs-beta` is the same shape, reported and never enforced.",
        ]
        if self.panel_buildable:
            lines += [
                "",
                "## The reference panel VCF, if it is absent above",
                "",
                "`build_reference_panel.py` is in the working directory and `phase3/` is",
                "readable at:",
                "",
                "    %s" % self.phase3_dir,
                "",
                "so a missing panel is not a dead end. How long it takes scales with",
                "the model's position count -- roughly half an hour for a model of about",
                "1.1M positions at the concurrency the brief gives, proportionally longer",
                "for a larger one, and overrunning it is not a reason to refuse.",
                "See the brief for the invocation. Whether it is worth building for",
                "this request, or whether something else about the request makes a usable",
                "distribution impossible anyway, is your call to make and to state.",
            ]
        p = os.path.join(self.inputs, "INPUTS.md")
        with open(p, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        self.record.add(p, "(generated)", "provider-input-manifest")

    # -- reporting -------------------------------------------------------
    def summary(self):
        """What the harness knows about the fit. Recorded; never shown to the provider."""
        matched = self.pgs == PROVIDER_NATIVE_PGS
        return {
            "requested_pgs_id": self.pgs,
            "provider_native_pgs_id": PROVIDER_NATIVE_PGS,
            "inputs_match_requested_model": matched,
            "staged": self.staged,
            "absent": self.absent,
            "int1_arm_exercised": matched and not self.ambiguous and not self.refused,
            "ambiguous_input": self.ambiguous,
            "refused_path": self.refused,
            "pca_basis": self.basis,
            "panel_buildable": self.panel_buildable,
            "note": (
                "The pre-staged reference VCF and two input basenames are specific to %s. "
                "When the request names that model the panel is staged and the reference is "
                "built directly. When it names another, the panel is absent -- but since "
                "2026-08-05 build_reference_panel.py and phase3/ are provided, so the "
                "provider CAN build one rather than being forced to refuse. Which it does "
                "is its own call; the harness does not write the refusal on its behalf and "
                "does not build the panel on its behalf either. "
                "`inputs_match_requested_model` therefore no longer means 'the only case "
                "that can work' -- it means the panel came for free rather than being built."
                % PROVIDER_NATIVE_PGS),
        }

    def brief(self):
        return BRIEF.format(out=self.out, cwd=self.root)

    # -- result ----------------------------------------------------------
    def read_artifact(self):
        if not os.path.exists(self.out):
            return None, "the provider produced no artifact at %s" % self.out
        try:
            with open(self.out) as fh:
                art = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            return None, "the provider's artifact is not readable JSON: %s" % exc
        return art, None

    def deliver_to(self, dest_path):
        """Copy the artifact into the requesting sandbox and hash both ends."""
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        shutil.copyfile(self.out, dest_path)
        return {
            "from": self.out, "to": dest_path,
            "sha256_source": staging.sha256(self.out),
            "sha256_delivered": staging.sha256(dest_path),
            "bytes": os.path.getsize(dest_path),
        }
