# The live record of `runs/slice-001/`

**Status:** descriptive, not normative. This records what the run's own artifacts declare about
each other, and marks the places where they declare nothing. It does not decide anything the
artifacts left open.

**Read as of** 2026-07-29, against `ledger.jsonl` at 56 entries, HEAD `624fe90`.
**Written because** three sessions are reading this directory at once and supersession in it is
declared only as prose inside `.decision` fields.

---

## 1. The rule, in four lines

1. **`ledger.jsonl` is the ordering authority.** It is append-only and its `ts` values are
   monotonic across all 56 entries. Every judgment's ledger `ts` matches its artifact's own
   `recorded_utc` exactly.
2. **Filter the ledger by `stage`, then take the LAST row of that stage.** Never the first match.
   The refusals are still in the ledger, unamended, at lines 41/43/44.
3. **A file never knows it is dead.** Supersession is asserted only in the *successor*, only as
   free text. There is no `superseded_by`, `supersedes`, `replaces`, `retracted`, or
   `status_of_record` field anywhere in this run — verified by grep over every JSON. Superseded
   files were deliberately not amended in place: `score_retraction.json` says so
   (*"Recorded as a retraction against the run rather than by amending the original judgment"*).
   **Opening any single file alone can hand you a retracted answer with no warning.**
4. **"Newest file wins" is wrong here, in three specific places** — see §6 T15, T16, T17.

Exactly four files in the whole run contain supersession vocabulary:

```
score/score_retraction.json          score/score_corrected_leaveout.json
interpret/interpret_placed.json      report/report_placed.json
```

---

## 2. Live record per stage

| Stage | LIVE | DEAD | Basis |
|---|---|---|---|
| **door** | `door/classification.json` (22:34:37Z) is the judgment of record. `door/provenance.json` (2026-07-23T01:23:13Z) is what downstream stages read — it self-declares *"INTAKE-2: the stages read THIS record, never the file extension."* | nothing | ledger `kind:"judgment"`; self-declaration. `build_corroboration.json` is explicitly **additive** (*"corroborated a fourth way"*), not superseding |
| **ancestry** | `ancestry/ancestry.json` (2026-07-23T01:58:52Z) — the SAS call | nothing *declared* — but see §4.1: **both artifacts were produced by superseded code** | ledger line 12; no in-run supersession exists |
| **select** | `select/model_choice.json` (2026-07-23T02:54:15Z) — the only artifact run-wide with `chosen_model` | nothing declared | authority rests on **absence of a competitor**, not on any declaration |
| **score** | split by scope — see below | `score/score.json` (`outcome:"refuse"`) | declared chain, §3 |
| **interpret** | `interpret/interpret_placed.json` (2026-07-28T20:50:13Z, `outcome:"score"`) | `interpret/interpret.json` (`outcome:"refuse"`) | *"This SUPERSEDES runs/slice-001/interpret/interpret.json"* |
| **report** | `report/report_placed.json` + `report/report_output_placed.md` (20:51:59Z) | `report/report.json` + `report/report_output.md` | *"SUPERSEDES runs/slice-001/report/report_output.md and report.json"* |

**The score stage has two live records, and neither covers the other's scope:**

- **Gate verdict, resolved fractions, model choice** → `score/score_confirmed_n301.json`
  (2026-07-27T18:18:01Z). *Older* than the shortfall judgment, and still the live answer to
  "which model, and did it clear."
- **The delivered number** → `score/score_shortfall_judgment.json` (2026-07-28T20:49:24Z),
  carrying `-1.307975913` from `score/PGS005168_scored/raw_score.json`.

---

## 3. The score-stage chain, as declared

```
score.json                 refuse    2026-07-23T04:27:35Z
  └─ retracted by ──────►  score_retraction.json          stop   06:20:12Z
        "rests on an invalid measurement and must not be relied on"
        (DR2 from a one-sample imputation degenerates to ~0)
  └─ superseded by ─────►  score_corrected_leaveout.json  score  21:17:57Z
        "CORRECTED RESULT (supersedes the retracted refusal)"  → PGS005168
  └─ confirmed, NOT ────►  score_confirmed_n301.json      score  2026-07-27T18:18:01Z
     replaced, by              "confirmed, not merely re-measured"
  └─ then ──────────────►  score_shortfall_judgment.json  score  2026-07-28T20:49:24Z
        supersedes nothing; adjudicates the 7-variant loss. "Score stands."
```

**The gate never moved.** Both generations use r² bar 0.80, the 0.90 floor, the summed-|β| metric
and palindromic exclusion, with the identical denominator `174.25856081460677` for PGS012535.
Only the statistic fed to the bar was repaired, and only imputed positions were reclassified —
the typed, palindromic, multiallelic and allele-mismatch buckets are byte-identical between the
two files. This is a repaired measurement, **not a loosened gate.**

---

## 4. Live numbers, and the stale ones they replace

**Resolved-weight fractions — live column is `RESOLVED_FRACTIONS.md` "DR2 n=301", each row
backed to full precision by its own `coverage_leaveout_*.json`:**

| model | live (n=301) | file | verdict vs 0.90 |
|---|---|---|---|
| PGS002050 | 0.9778 | `PGS002050/coverage_leaveout_PGS002050.json` | clears |
| PGS001841 | 0.9751 | `PGS001841/coverage_leaveout_PGS001841.json` | clears |
| **PGS005168** | **0.9655191335386939** | `PGS005168/coverage_leaveout_PGS005168.json` | **clears — this is the scored model** |
| PGS001339 | 0.8264 | `PGS001339/coverage_leaveout_PGS001339.json` | below |
| PGS001340 | 0.8250 | `PGS001340/coverage_leaveout_PGS001340.json` | below |
| PGS012535 | 0.5897731595184909 | `coverage_leaveout_PGS012535.json` | below (ceiling 0.776) |
| PGS012531 | 0.5124 | `PGS012531/coverage_leaveout_PGS012531.json` | below (ceiling 0.735) |

**Never quote these:**

| stale | where it lives | live replacement |
|---|---|---|
| `0.2120` PGS012535 | `score/coverage_PGS012535.json`, `score/score.json`, `report/report_output.md` ("only 21%") | `0.5898` |
| `0.5448` PGS005168 | `score/PGS005168/coverage.json` | `0.9655` |
| `0.9630` PGS005168 | `RESOLVED_FRACTIONS_n100.md` | `0.9655` |
| `0.8269` PGS001340 | `RESOLVED_FRACTIONS_n100.md` | `0.8250` — **moved down**, not rounding |
| `646942` / `1766503` score-input rows | `PGS005168/matching_report.json`, `score/matching_report.json` | `1087803` |
| `-1.308132622` | `score_shortfall_judgment.json` | `-1.307975913`. That file states verbatim: the figure appears *"ONLY as a materiality bound and must never be quoted as the result."* |

**Rule: a bare resolved-weight fraction from this run is uncitable.** Always quote it with its DR2
generation — n=1 (void), n=100, or n=301.

---

## 5. Undeclared — do not resolve these silently

These are places where two artifacts disagree and **nothing in the run says which wins.** They are
listed so three sessions reach the same "unknown", not three different answers.

**4.1 — Both ancestry artifacts were produced by code that has since been fixed.** Commit `5f773d5`
("stop scaling augmented PCA eigenvectors by sqrt(eigenvalue)") postdates them by ~6 days. This is
*verified, not inferred*: `projection.json`'s `shrinkage_note` is byte-for-byte the deleted side of
that commit's diff. `docs/ancestry-projection-scaling.md` states the old code
*"biased `patient_distance_in_sd_units` by about −1.5 SD"* — which is the exact field
`ancestry.json` headlines as `-1.05 sd`. Neither JSON records an `ancestry.py` version or commit
SHA. No post-fix ancestry artifact exists for this run. **The SAS call stands as the recorded
decision; its numeric fields — `projection_shrinkage_ratio_for_this_query` (1.1442),
`patient_distance_in_sd_units`, the ADP coordinates — should not be quoted.**

**4.2 — `select/model_choice.json` still says `chosen_model: "PGS012535"`. The model delivered is
PGS005168.** The descent is narrated only in score-stage prose. `model_choice.json` carries its own
conditional (*"the next candidate down is PGS005168"*) but no record that it fired.
**A reader of `select/` alone gets the wrong model.**

**4.3 — `score/matching_report.json` is neither blessed nor retracted.** Its r²-dependent counts
derive from the invalidated DR2; its palindromic/strand counts do not. `score_retraction.json`
enumerates a `what_still_stands` list and this file appears in neither that list nor any retraction.
It is also PGS012535-scoped and writes an output that was never scored.

**4.4 — The scored input file is outside the run tree.** `raw_score.json.score_input` names
`reference-provider/work/plink_score_input.tsv` (1,087,803 rows). The run tree's two
`plink_score_input.tsv` files hold 1,766,503 and 646,942 rows and are both stale. The lineage
arithmetic reconciles (646942 + 443136 − 2275 = 1087803) but no artifact states it.

**4.5 — `interpret.json`'s evidence was destroyed.** It cites
`interpret/reference_response.json` for *"reference_unavailable, interpret_can_proceed=false"*.
That file was overwritten in place and now reads `status: reference_valid`,
`interpret_can_proceed: true`. The supersession of `interpret.json` *is* declared; the destruction
of its evidence is not, and the 2026-07-23 content is unrecoverable.

**4.6 — Door: hom-ref wording.** `classification.json` says *"there is no positive hom-ref evidence
anywhere in this file to license dosage 0"*, beside `build_corroboration.json`'s `hom_rr: 301847`.
Two scopes are available (absent positions vs all positions); neither file states which is meant.

**4.7 — Door: a judgment predates its own evidence.** `build_corroboration.json`
(2026-07-22T23:42:21Z) cites `provenance.json` (2026-07-23T01:23:13Z) as its first evidence source
— a gap of 1h41m in the wrong direction. The values are internally consistent; only the ordering
is anomalous.

**4.8 — Ledger integrity.** Two entries point at files that no longer exist (`/tmp/proj_chr1.json`,
`/tmp/palcheck/matching_report.json`). Six point at paths whose content was later overwritten,
worst being line 42 → `interpret/reference_response.json`, overwritten 5.5 days later. About 30
artifacts on disk have no ledger row at all — including **the entire n=301 leave-out evidence base**
that `score_confirmed_n301.json` rests on.

---

## 6. Traps

| # | Trap | Wrong answer it yields | Guard |
|---|---|---|---|
| T1 | `report/report_output.md` — a complete, warm, patient-facing **refusal letter** with no supersession marker | *"I could only account for 59% at best… only 21%"* → "no AF score is possible" | never read a `report/*.md` without first reading the newest `report/*.json` `.decision` |
| T2 | `score.json` has the canonical stage filename and is the **oldest** judgment | `refuse`, *"plink2 was never run"* | sort score-stage judgments by `recorded_utc`, read all of them |
| T3 | `<stage>.json` and `<stage>_placed.json` coexist | the bare name is the earlier one | `_placed` wins |
| T4 | `PGS*/coverage.json` vs `PGS*/coverage_leaveout_*.json` — **byte-identical `mode_caveat` strings**, verified | PGS005168 `0.5448` → "fails the floor" | if both exist, the plain one is dead |
| T5 | `RESOLVED_FRACTIONS_n100.md` self-titles *"with valid DR2"* and bolds its column `resolved (VALID)` | `0.9630`, `0.8269` | the winner is declared only in `score_confirmed_n301.json`, in neither `.md` |
| T6 | glob `score/*.json` | returns 6 retracted-generation files and **misses live coverage for 6 of 7 models** (they sit one level down) | glob `score/**/coverage_leaveout_*.json` |
| T7 | `score/imputed/imputation_report.json` was overwritten by a chr2-only re-run: `n_ok: 1` | "only 1 chromosome was imputed" | verify with `ls imputed/*.vcf.gz` (all 22 present) |
| T8 | "the refusal was retracted" | → "so PGS012535 is fine now" | **wrong.** `score_retraction.json.what_still_stands`: the PGS012535 palindromic loss *"does not depend on DR2"*. Its 0.776 ceiling never moved. PGS012535 and PGS012531 remain un-scoreable |
| T9 | newest file in `score/` is `score_shortfall_judgment.json` | it contains no gate verdict, no resolved fractions, no model choice | those live in the *older* `score_confirmed_n301.json` |
| T10 | newest file at a cited path | `interpret.json`'s citation now resolves to a file saying the opposite | §4.5 |
| T11 | newest bytes in `select/` are four post-decision downloads (latest +15h20m, 156 MB, for a "strictly dominated" candidate) | none contains a decision | the decision is the older `model_choice.json` |

---

## 7. What is still binding

Not everything old is dead. These survive all three DR2 generations, per
`score_retraction.json.what_still_stands` (verbatim):

- *"The strand-ambiguity loss in PGS012535 (22.4% of weight) is a separate, real finding and does
  not depend on DR2."*
- *"SCO-8's second condition (high-beta locus) did not fire and does not depend on DR2 either."*
- *"The select-stage coverage ESTIMATE is unaffected: it uses panel membership, not DR2."*
- *"The door, ancestry and select stages are untouched by this."*
- The REP-14 medical-advice refusal in `report_placed.json.explain_vs_medical_boundary` is live in
  **both** report generations.

---

## 8. How this was determined

`ledger.jsonl` read in full (56 entries, ordering verified monotonic). Every `.json`/`.md` in the
run enumerated. Whole tree grepped for supersession vocabulary — 4 hits, all in successors — and
for machine-readable supersession fields — zero. All headline numbers re-read from the files:
`score.json.outcome`, `model_choice.json.chosen_model`, the four coverage fractions with their
`mode_caveat` strings, `raw_score.json.score_input`, and line counts of all three candidate score
inputs. Ancestry staleness established by diffing `git show 5f773d5 -- tools/ancestry.py` against
the literal strings on disk, which does not depend on any timestamp argument.

**Weakness worth naming:** the artifact format has no provenance field for the code that wrote it.
Staleness in `ancestry/` was detectable only through an accidental string match against a commit
diff. That is a gap in the format, not a bookkeeping detail — a future stale artifact may not leave
such a trace.

---

## 9. What would change this document

A new ledger entry for any stage; a post-fix ancestry re-run written back to
`runs/slice-001/ancestry/`; a select-stage record of the SEL-12 descent to PGS005168; or any
artifact that declares a winner for one of the eight undeclared items in §5.
