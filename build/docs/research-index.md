# research index

what each research item asked, which file holds it, what it settled, and where that landed. the ledger of *decisions* is [[research-log]]; this is the map of *files*.

**read the depth column before citing anything.** it comes from [[research-log]]'s provenance table: `primary` = the paper was read, `verified table` = the file's own evidence table with DOIs checked, `traced` = identifier traced to a search result but the number never confirmed against the page, `extracted` = we have the fold chat's reading, not the source, `ours` = our own measurement, no paper.

## the items

| item | file(s) | question | what it settled | lands as | depth |
|---|---|---|---|---|---|
| **R1** | `research-R1-comparability-bias.md` + `-citations.csv` | is array-imputed PGS comparable to WGS-derived, and is there a missing-fraction threshold? | r = 0.87–0.99 across 23 arrays × 6 ancestries, so heavy imputation isn't disqualifying. **density is non-monotonic**, so coverage can't be the sort. **no published threshold exists.** All of Us is not a lookup. stochastic imputation alone moves >10 percentile points in 36.4% AFR vs 4.7% EUR. | `SCO-8`, `SEL-2`, `SEL-11`, `INT-1`, `INT-2`, `REP-3` | primary + verified table |
| **R2** | `research-R2-missing-position.md` (CS), `-gemini.md`, `-decision-matrix-plain.md`, `-matrix.html`, `-het-dropout-by-depth.png` | can absence in a file be treated as reference? | **no.** absence ≠ reference without positive evidence: a gVCF hom-ref block, a callable BED, or per-site depth. `DP ≥ 10` puts het-dropout under 0.1%. | `SCO-6`, `JC-7` | traced |
| **R3** | `research-R3-file-types.md` (CS), `-gemini.md` | what file types do people actually hand us, and what does each guarantee? | assay classes and what each can and can't license. feeds the door. | `INTAKE-1` | extracted |
| **R4** | `research-R4-dual-build-sweep.md` | is every catalog model available in both builds? | **5,450/5,450, zero exceptions.** so pick the matching build, never lift over. `SCO-3`'s liftover fallback has no catalog case. | `SCO-2`, `SCO-3` | ours |
| **R5** | `research-R5-ancestry-conflict.md` + `-evidence.csv`, `-references.csv`, `-references.bib` | self-reported vs genetically inferred ancestry — which does the math use? | **the genome, always.** self-report is a flag, never a signal. 87.7% concordance in All of Us. accuracy decays continuously along genetic distance, so "ancestry match" = distance, not a label. names the projection method and the shrinkage correction. | `ANC-1`, `ANC-3`, `ANC-4`, `ANC-5`, `SEL-2`, §16-D, NEW §11 | verified table |
| **R6** | `research-R6-version-traceability.md` | what has to be recorded to reproduce a score later? | the capture contract: record from the object that travelled with the result. and **the All of Us reference expires** — recording the CDR buys an audit trail, not reproducibility. | `DATA-6` | extracted |
| **R7** | *(no file — dead)* | the WGS blocker | **dead.** killed by the S13 measurement 2026-07-15: a variant-only WGS is exactly what a consumer receives, so there was no blocker. `BLOCK-3` stale, §16-A's reasoning dead. | `BLOCK-1`, `BLOCK-3`, §16-A | ours |
| **R8a** | `research-R8a-conversions-and-provenance.md` | what does format conversion do to a file? | **PLINK and bcftools produce different VCFs from the same 23andMe file.** which converter ran is itself provenance. and **no validated classifier exists** for recognizing an array-derived VCF — the signatures are a defensible heuristic, flagged NOT ESTABLISHED. | `INTAKE-1`, `INTAKE-2`, §17.1 | extracted |
| **R8b** | `research-R8b-what-people-hand-us.md` | practitioner knowledge on real consumer files | routed to Gemini deliberately for practitioner sources. **spec yes, manuscript no** — three rows rest on forum/blog sources. | — | extracted |
| **R9** | `research-R9-missing-weight-fraction.md` + `-evidence.csv` | is there a defensible missing-weight threshold, and does a tool compute it? | **no threshold, and no tool.** `--min_overlap` is variant-count, weight-blind. individual instability ≠ population variance, and β²·2p(1−p) → 0 as p → 0, so variance coverage under-counts the exact variant that swings one person. **manuscript contribution.** | `SCO-8`, `SEL-11`, §16-C, §17.1 | verified table |
| **R10** | *(no file)* | the three threshold values | resolved to **"no published number exists"** by R1 and R9, so there was nothing to write up. the values themselves were handed to the PRD to choose and defend — see [[prd]] §3 (0.90 weighted-coverage floor, 5% high-β condition, 0.80 INFO/r² bar, all labelled chosen-not-derived). | `SCO-8`, [[prd]] | — |

## the fold files (research → spec)

these sit in this folder but they're the *transfer* layer, not research:

- `fold-list.md` — everything extracted, before triage
- `fold-reconciliation.md` — what went to the spec chat, requirement-level
- `fold-blockers.md` — the reasoning we kept
- `fold-handoff.md` — the brief for the fold chat
- `research-runbook.md` — how a research item is run

the fold chat was firewalled from [[research-log]], [[cong-decisions-log]] and [[idea]] so its extraction stayed independent. don't collapse that.

## still open, from [[research-log]]

- **collision 7** — INFO/r² bar. R2-CS says 0.3 permissive / 0.8 stringent (a tool default); R2-gemini says 0.8 clinical, no 0.3 tier. the product is neither. **the PRD picked 0.80 as a design choice**, which resolves it operationally without resolving it on evidence.
- **collision 8** — WES off-target: R2-CS says impute, R2-gemini says it can't be. CS's own Gap 3 admits the quality is unquantified. lands on `SLICE-5`.
- **collision 6** — variant-only VCF is the most-expected input; R8b and R2-gemini both say reject it for WGS, cong's impute rule says don't. sits in §17.3 looking more neutral than it is.
- **the non-additive / haplotype row-level pass** — the column counts were mislabeled and retracted. the requirement stands; the numbers need a TRUE-row count, not a column count.

## housekeeping

- **the two Gemini media folders are archived**, `_archive/media-R2-gemini/` and `_archive/media-R3-gemini/` (91 PNGs). they were inline math glyphs from the Docs export — single symbols like *i*, *N*, *β* — not figures. `research-R2-missing-position-gemini.md` and `-R3-file-types-gemini.md` still carry `![](./media-R2-gemini/...)` references, so a few inline symbols now show as broken links in those two files. the prose is unaffected and the CS versions of R2 and R3 are the ones that were folded.
- **the one real figure is `research-R2-het-dropout-by-depth.png`** — kept in place.
- **no R7 or R10 file exists.** [[prs-agent-hub]] said "ten files R1-R10"; it's eight R-numbers with files (R8 split a/b). corrected there.

## related
- [[prs-agent-hub]]
- [[research-log]] — the decision ledger
- [[fold-reconciliation]]
- [[fold-blockers]]
- [[prd]] — where R10's values landed
