# What an "absent position" means, per genome-file type

**Scope.** This document defines what it means for a genomic position to be *absent* — to have no record, or an ambiguous record — in each of four file types the scoring stage may receive: whole-genome sequencing (WGS) variant callsets, whole-exome sequencing (WES) callsets, genotyping-array data, and gVCF. For each it answers the three sub-questions: (1) does absent imply homozygous-reference or merely no coverage; (2) are reference/confidence calls present; (3) how do consumer-grade exports vary. It closes with a decision table of the rules an agent should apply.

**How to read the tags.** Every claim is tagged so the manuscript can tell evidence from extrapolation:

- **[SPEC]** — stated in a format specification (VCF/BCF).
- **[DOC]** — stated in a primary tool or vendor document (caller docs, PLINK docs, vendor help pages).
- **[PRACTICE]** — what a tool or vendor *does in practice*, sourced from observed behavior (worked examples, third-party technical reviews) rather than from the tool's own specification. On consumer exports especially, [DOC] and [PRACTICE] come apart, and the tag says which one you are reading.
- **[INFERENCE]** — our extrapolation, going beyond what any cited source states. Flagged explicitly.
- **[NOT ESTABLISHED]** — the primary literature does not answer the question. Stated plainly rather than papered over.

A structural point that governs everything below, and the reason the four types cannot share one rule: **the VCF specification defines a file *format*; it does not define what the *absence of a record* means in a given callset.** The spec fixes how a missing genotype is written (`./.`) and how reference blocks may be encoded (§5.5), but the meaning of "position X has no line in this file" is set by the process that produced the file — the caller, its mode, and the pipeline's filtering — not by the format [SPEC: Danecek 2011, doi:10.1093/bioinformatics/btr330; VCFv4.3 spec §1.6.2, §5.5]. Assay class is therefore load-bearing: an absent position means something different in each type, and that difference is what this document pins down.

---

## 1. WGS (variant-only VCF)

A "WGS VCF" in ordinary use is a **variant-only callset**: the caller emits a line only where it called a difference from the reference. This is the default output of every mainstream germline caller.

**Does absent imply hom-ref or no coverage?** Neither, on its own — and this is the central hazard. In a variant-only WGS VCF, a position with no record can be either (a) confidently homozygous-reference or (b) not covered / not callable, and **the file alone does not distinguish them** [INFERENCE, from the combination below]. The inference is forced by what the format and the callers do:

- The format carries no record for non-variant sites in a standard VCF, so absence is not itself informative [SPEC: VCFv4.3 §5, the file lists variant sites; non-variant representation requires the separate gVCF conventions of §5.2/§5.5].
- Callers emit variant-only output by default and gate reference/non-variant reporting behind explicit flags: FreeBayes writes "all SNPs, INDELs, and haplotype variants between the reference and aln.bam" by default and requires `--gvcf` (coverage at non-called sites) or `--report-monomorphic` to emit non-variant information [DOC: FreeBayes README; Garrison & Marth 2012, doi:10.48550/arXiv.1207.3907]. `bcftools call` emits variants by default; `-g/--gvcf` is what produces "gVCF blocks of homozygous REF calls" [DOC: bcftools manual].
- Because the same blank line covers both "called reference here" and "never looked here," recovering which one requires evidence outside the VCF — the BAM/CRAM coverage, or a companion gVCF/reference track [INFERENCE].

The practical consequence: **treating every absent position in a variant-only WGS VCF as homozygous-reference silently misclassifies every no-coverage and no-call site as hom-ref.** For a PRS that reads specific loci, an absent risk-allele position is not evidence of the reference allele; it is evidence of nothing until coverage is confirmed [INFERENCE].

**Are reference/confidence calls present?** No, by construction — a variant-only VCF contains no non-variant records and therefore no per-site reference-confidence values (no `GQ`, `DP`, or `MIN_DP` for absent positions) [INFERENCE from SPEC §5]. Whether a *present* record is a genuine variant versus a caller-emitted reference call is a separate matter: DeepVariant, for instance, can emit a record at a candidate site that it then calls `0/0` or `./.` — meaning the model processed the region and decided non-variant, sometimes because of "low mapping quality," "low base quality," or "overall low coverage" [DOC: DeepVariant FAQ; Poplin 2018, doi:10.1038/nbt.4235]. So "a record exists" and "a variant exists" are not the same, and "no record" and "reference" are not the same.

**Which callers / versions.** The convention that absence ≈ (hom-ref OR no-coverage), indistinguishable, holds across GATK HaplotypeCaller in its default (non-GVCF) mode [DOC: McKenna 2010, doi:10.1101/gr.107524.110; DePristo 2011, doi:10.1038/ng.806], FreeBayes [DOC: Garrison & Marth 2012], DeepVariant [DOC: Poplin 2018], and `bcftools call` [DOC: Danecek 2021, doi:10.1093/gigascience/giab008]. **DRAGEN** follows the same pattern with its own documentation: the germline small-variant caller emits a VCF by default, and reference-confidence output is a separate opt-in mode (`--vc-emit-ref-confidence`, set to `GVCF` for banded blocks or `BP_RESOLUTION` per site; banded is the default *within* gVCF mode). In gVCF mode DRAGEN writes `<NON_REF>` hom-ref blocks alongside variants, and it uses reads assigned to `<NON_REF>` "to determine if the position can be called as a homozygous reference, as opposed to remaining uncalled," with the confidence carried as `FORMAT/GQ` (germline) [DOC: Illumina DRAGEN documentation, "gVCF Output" support-docs.illumina.com/SW/DRAGEN_v39/Content/SW/DRAGEN/SMVgVCFOutput.htm; "gVCF and Joint VCF Mode"]. So a DRAGEN *VCF* carries the WGS variant-only ambiguity; a DRAGEN *gVCF* resolves it (see §4). **VarScan2** (Koboldt 2012, doi:10.1101/gr.129684.111) is a pileup-based somatic/germline caller; **[NOT ESTABLISHED]**: we did not retrieve primary documentation stating VarScan2's exact default handling of non-variant sites, so treat its absent-position semantics as "assume the general variant-only convention until the run's own caller/version is known," not as separately confirmed. Because the product records caller and version at run time, the agent should read those fields rather than assume.

---

## 2. WES (variant-only VCF, capture-restricted)

WES is WGS's semantics plus one extra source of absence: the assay only interrogates a **capture target** — the exonic (and near-exonic) regions the capture kit was designed to pull down.

**Does absent imply hom-ref or no coverage?** A WES VCF inherits the WGS ambiguity (absent = hom-ref OR no-coverage, indistinguishable [INFERENCE, as §1]) **and adds a third, dominant category: outside capture.** The exome is a small, pre-selected fraction of the genome; targeted capture plus massively parallel sequencing interrogates only the targeted regions [DOC: Ng 2009, doi:10.1038/nature08250]. Positions outside the capture target are absent because they were never assayed — not because they are reference [INFERENCE from the capture-restriction fact]. Even within the target, coverage is non-uniform and incomplete, so intra-target positions can be absent for the ordinary WGS reasons [DOC: Bamshad 2011, doi:10.1038/nrg3031, which describes exome capture and its uneven/incomplete coverage].

The load-bearing rule for WES: **an absent position must first be classified as inside-target vs outside-target.** Outside the capture target, absence carries no genotype information at all. Inside the target, it collapses to the WGS case. Distinguishing the two requires the capture kit's target BED, which is a property of the assay, not of the VCF [INFERENCE].

**Are reference/confidence calls present?** No — as with WGS, a variant-only WES VCF has no non-variant records and no per-site reference-confidence values [INFERENCE from SPEC §5]. A WES *gVCF* (see §4) can carry them, but only within the regions the pipeline emitted, which in practice track the capture target.

**Consumer-export variation.** Whole-exome sequencing is primarily a clinical/research assay; consumer WES exports are far less common than consumer array or consumer WGS. **[NOT ESTABLISHED]**: we did not find primary documentation of how consumer-grade WES exports (as opposed to clinical WES reports) represent absent positions, so no consumer-WES export rule is asserted here.

---

## 3. Genotyping array

A genotyping array does not sequence; it interrogates a **fixed, predetermined set of markers** — the probes designed onto the chip. Everything about "absent" follows from that.

**Does absent imply hom-ref or no coverage?** In array data there are two distinct kinds of "absent," and they must not be conflated:

- **Position not on the chip** — the overwhelming majority of the genome. The array only reports the markers it was designed to test; a position not among those markers is simply not measured. This is not "no coverage" in the sequencing sense and it is certainly not reference — it is *not interrogated* [DOC: 23andMe genotypes a "predetermined set of locations in the genome"; PLINK's marker-based .bim/.map model, Purcell 2007, doi:10.1086/519795; Chang 2015, doi:10.1186/s13742-015-0047-8].
- **Marker on the chip but no confident call ("no-call")** — a genotyped position the calling algorithm could not resolve. In PLINK's text format this is encoded explicitly: an allele of `0` means "no call" in a `.ped`/`.map` pair [DOC: PLINK 1.9 file-format documentation]. This is analogous to `./.` in VCF.

Neither kind means homozygous-reference. A called marker, by contrast, is reported as an actual genotype (`AA`, `AG`, ...) whether or not it matches the reference; array genotypes are reported relative to a strand/allele model, not as "difference from reference" [DOC: 23andMe reports genotypes on the positive strand of the reference assembly]. So for arrays: **present-and-called → a real genotype; on-chip-but-no-call → unresolved; not-on-chip → not measured.** None of these is a reference-inferred call.

**Are reference/confidence calls present?** There is no non-variant/reference-block concept for arrays — the file is a list of assayed markers with their genotypes, not a per-base reference track [INFERENCE from the marker-based model]. Array data does not carry sequencing-style per-site confidence (`GQ`/`DP`); a marker is either called or a no-call [DOC: PLINK no-call encoding].

**Imputation caveat.** Many array-based workflows *impute* untyped positions from a reference panel, producing genotype estimates (with a probability/dosage) at positions the chip never measured [DOC: Howie 2009, doi:10.1371/journal.pgen.1000529; Marchini 2010, doi:10.1038/nrg2796]. **[INFERENCE]**: if the file the agent receives is post-imputation, "present" no longer means "directly assayed," and an imputation quality score (not a sequencing confidence) governs how much to trust it; whether a given consumer file is imputed is generally not self-evident from the genotype column alone. Living DNA, for example, explicitly states its raw file contains no imputed markers [DOC: Living DNA help], which establishes that vendors differ on this — some impute, some do not.

**Consumer-export variation.** Consumer array raw data is the best-documented consumer case. 23andMe delivers a tab-separated text file (rsid, chromosome, position, genotype); an uncalled marker appears as `--`, the "not determined" result when the algorithm cannot confidently call [DOC: 23andMe, "How 23andMe Reports Genotypes," customercare.23andme.com/hc/en-us/articles/212883677; "Raw Genotype Data Technical Details"]. The chip tests only its predetermined markers, so a "negative" (reference-looking or absent) result does not establish that an untested variant is absent [DOC: 23andMe raw-data documentation]. When such a file is converted to VCF, the `--` no-calls become missing genotypes and non-SNP rows (indels) are commonly skipped, as the bcftools `--tsv2vcf` worked example shows (it reports "Missing GTs" and "Rows skipped" counts) [PRACTICE: bcftools convert howto]. Other consumer array vendors (Living DNA, AncestryDNA, MyHeritage, FTDNA) use the same broad tab-separated, fixed-marker shape with a per-vendor no-call token and different marker sets [DOC: Living DNA help; PRACTICE: third-party comparisons of overlapping marker counts across vendors].

---

## 4. gVCF

A gVCF ("genomic VCF") is the file type explicitly designed to remove the WGS ambiguity: it records **evidence at non-variant positions too**, so that absence and reference can be told apart.

**Does absent imply hom-ref or no coverage?** In a gVCF, most positions are *present* — either as variant records or as non-variant reference blocks — precisely so that "reference" is stated rather than inferred from absence. The GATK documentation is explicit: the key difference between a regular VCF and a gVCF is that the gVCF has records for all sites, whether there is a variant call there or not, and the records include an estimate of how confident the caller is that a site is homozygous-reference [DOC: GATK "GVCF — Genomic Variant Call Format," gatk.broadinstitute.org/hc/en-us/articles/360035531812; archived at github.com/broadinstitute/gatk-docs]. The spec provides the mechanism: reference-only blocks are encoded with a symbolic non-reference allele (`<*>`, preferred over `<NON_REF>`) and an `END` tag spanning the block [SPEC: VCFv4.3 §5.5]. Therefore, within a gVCF's emitted intervals, **absence of a record generally means outside the emitted region (e.g., not covered / not emitted), while a covered reference position is present as a `0/0` block, not absent** [INFERENCE from the "records for all sites" design; the boundary of what was emitted is set by the run]. This flips the WGS default: a position *inside* a reference block is affirmatively hom-ref (to the stated confidence); a position with genuinely no record was not emitted, which typically means no/insufficient coverage [INFERENCE].

**Are reference/confidence calls present?** Yes — that is the point of the format. Reference blocks carry per-block confidence: `GT` `0/0`, `DP`, `GQ`, `MIN_DP` (the minimum depth within the block), and `PL` [SPEC: VCFv4.3 §5.5 example records; DOC: GATK gVCF article defines `MIN_DP` and the `##GVCFBlock` GQ bands]. Two emission modes differ in resolution: `-ERC BP_RESOLUTION` gives an individual record at every site; `-ERC GVCF` groups non-variant sites into blocks banded by genotype quality (GQ ranges declared in the `##GVCFBlock` header line) [DOC: GATK gVCF article]. **Critically, "reference call present" does not mean "high confidence":** a reference block's `GQ` is the reference-confidence score and can be very low — GATK's own support notes a block with `GQ` of 3 supported by a single read [DOC: GATK community thread on interpreting non-variant blocks]. So an agent must read the block's `GQ`/`DP`/`MIN_DP`, not merely note that a `0/0` block exists [INFERENCE].

DRAGEN's gVCF implementation matches this design independently: reference blocks carry `GT:AD:DP:GQ:MIN_DP:PL` with the `<NON_REF>` allele and `END`-spanned hom-ref blocks, banded by GQ (`--vc-gvcf-gq-bands`); GVCF (banded) vs BP_RESOLUTION (per-site) are the two emission modes, banded on by default [DOC: Illumina DRAGEN "gVCF and Joint VCF Mode" and "Genotyper Options"]. DRAGEN also notes that gVCF entries are not necessarily contiguous — "the file might contain gaps that are not covered by either a variant line or a hom-ref block" — which is a concrete confirmation that a genuinely absent record in a gVCF means *not emitted* (no/insufficient support), not reference [DOC: Illumina DRAGEN "gVCF Output"].

**Joint-calling / all-sites nuance.** gVCFs are the input to joint genotyping (GATK GenotypeGVCFs; DRAGEN gVCF Genotyper / Joint Genotyper), which uses the per-sample reference-confidence estimates across a cohort [DOC: GATK GenotypeGVCFs tool doc; Illumina DRAGEN gVCF Genotyper Options]. Note that a superficially "all-sites" VCF from other tools (e.g., UnifiedGenotyper `EMIT_ALL_SITES`) is not a true reference-confidence gVCF and cannot be used for joint calling in the same way [DOC: GATK gVCF article]. **[INFERENCE]**: for the scoring stage this matters only insofar as the agent should confirm the file is a genuine reference-confidence gVCF (look for `<NON_REF>`/`<*>` ALT and `##GVCFBlock`/`MIN_DP` headers) before trusting reference blocks as affirmative hom-ref.

**Consumer-export variation.** gVCF is a bioinformatics-pipeline artifact; **[NOT ESTABLISHED]** whether any consumer vendor delivers a true reference-confidence gVCF to end users as its standard product (as opposed to a variant-only VCF). Consumer WGS export behavior is covered in §5 below, where the important divergence between vendor documentation and observed practice lives.

---

## 5. Consumer-grade export variation (cross-cutting)

The user flagged two consumer tracks; they differ sharply in how well-documented they are.

### 5a. Consumer array / DTC raw data — well documented [DOC]
Covered in §3: fixed-marker tab-separated text, no-call token (`--` for 23andMe/Living DNA; `0` in PLINK-format exports), no reference blocks, only assayed markers present, per-vendor marker sets, and per-vendor imputation policy. This case is documented in vendor help pages and is the most reliable consumer case to reason about [DOC: 23andMe, Living DNA].

### 5b. Consumer WGS (Dante Labs, Nebula, Veritas, sequencing.com) — divergence between spec and practice
This is where **what the vendor says** and **what the file actually contains** come apart, and the tag matters most.

- **Vendor's own documentation: [NOT ESTABLISHED].** The consumer-WGS vendors advertise delivery of a "VCF file" and unrestricted access to BAM/VCF/FASTQ [DOC: Dante Labs product page], but we did not find vendor-authored documentation specifying whether the delivered VCF is variant-only or all-sites, whether homozygous-reference or no-call positions are included, or whether it is a gVCF. On the vendors' own sources, the absent-position semantics of consumer WGS VCFs is **not established**.
- **Observed practice (third-party): [PRACTICE].** A third-party technical review reports that Dante Labs delivers a *regular* (non-genome) VCF — actually two, one for SNPs and one for indels — containing only PASS variants, and that **both homozygous-reference calls and no-calls are excluded**; it then warns that assuming every coordinate not in the file is homozygous-reference "may not always be correct" precisely because low-quality/no-call sites were also dropped [PRACTICE: sequencing.com, "Dante Labs Review"]. Independent user accounts describe the same variant-only shape ("all values that are not in the VCF file are the same as the reference" — an assumption the user themselves makes) [PRACTICE: Kessler, Behold Genealogy blog]. This is observed behavior of one vendor at one time, not a spec, and vendor pipelines change.

The agent-relevant conclusion: for consumer WGS, the safe default is the **WGS variant-only** interpretation of §1 (absent = hom-ref OR dropped-no-call, indistinguishable), and the specifically dangerous failure mode — documented as observed practice, not spec — is that **no-calls are dropped alongside hom-ref**, so "absent ⇒ hom-ref" understates missingness [PRACTICE, as above]. Whether a given consumer WGS file is variant-only or a genome/all-sites VCF must be checked from the file's own records (presence of `<NON_REF>`/`<*>`, `##GVCFBlock`, reference blocks), not assumed from the vendor name [INFERENCE].

---

## 6. Decision table — rules for interpreting "missing"

Columns are scenarios an agent encounters; each cell gives the rule. Empty-rule cases are marked **NOT ESTABLISHED** rather than filled with an uncitable guess.

| File type | Position absent (no record) | Present but `./.` / no-call | Present `0/0` / reference | Reference-confidence available? | Consumer-export caveat |
|---|---|---|---|---|---|
| **WGS (variant-only VCF)** | **Ambiguous: hom-ref OR no-coverage/no-call — indistinguishable from the VCF alone.** Do not assume hom-ref. Confirm coverage (BAM/CRAM or companion gVCF) before treating a locus as reference. [INFERENCE from SPEC §5 + caller DOCs] | Explicit no-call: caller could not genotype. Treat as missing, not reference. [SPEC: `./.`] | Present only if the caller emitted it (e.g., DeepVariant candidate called reference). A `0/0` record ≠ absence; read its `GQ`/`DP`. [DOC: DeepVariant FAQ] Caller default output is variant-only across GATK/FreeBayes/DeepVariant/DRAGEN/bcftools; reference confidence is a separate gVCF mode. [DOC: DRAGEN gVCF Output; others as §1] | **No** for absent sites (variant-only files carry no per-site reference confidence). [INFERENCE from SPEC §5] | See consumer WGS cell below. |
| **WES (variant-only VCF)** | **First split inside-target vs outside-target.** Outside capture target → not assayed, no genotype information (NOT reference). Inside target → collapses to the WGS rule (hom-ref OR no-coverage). Requires the capture BED. [DOC: Ng 2009; Bamshad 2011; INFERENCE] | As WGS: explicit no-call, treat as missing. [SPEC: `./.`] | As WGS. | **No** for absent sites; a WES gVCF carries confidence only within emitted (≈target) regions. [INFERENCE] | Consumer-grade WES export semantics **NOT ESTABLISHED** (no primary source found). |
| **Genotyping array** | **Two subcases. Marker not on chip → not measured (NOT reference, NOT "no coverage"). Marker on chip but absent from file → treat as no-call.** Never infer hom-ref from absence. [DOC: 23andMe fixed markers; PLINK marker model] | No-call: on-chip marker the algorithm could not resolve (`--` for 23andMe/Living DNA; allele `0` in PLINK). Missing, not reference. [DOC: PLINK; 23andMe] | A called genotype (`AA`/`AG`/…) — a real genotype relative to the strand/allele model, whether or not it matches reference. [DOC: 23andMe] | **No** — no sequencing-style per-site confidence; marker is called or no-call. If file is post-imputation, a separate imputation quality/dosage applies. [DOC: PLINK; imputation INFERENCE] | Best-documented consumer case: fixed markers, `--`/`0` no-call token, per-vendor marker sets and imputation policy. [DOC: 23andMe, Living DNA] |
| **gVCF** | **Absent generally = outside the emitted region (typically no/insufficient coverage), because gVCFs aim to have a record for every emitted site.** A covered reference position is present as a `0/0` block, not absent. [DOC: GATK gVCF article; INFERENCE] | Present `./.` can occur (e.g., post-joint-calling representation); treat as no-call/missing at that site. [SPEC; PRACTICE: GATK GenotypeGVCFs output] | **Affirmative hom-ref within a reference block — but to the stated confidence only.** Read `GQ`/`DP`/`MIN_DP`; a block's `GQ` can be very low (e.g., 3 on a single read). [SPEC §5.5; DOC: GATK] | **Yes** — reference blocks carry `GT 0/0`, `DP`, `GQ`, `MIN_DP`, `PL`; GQ bands in `##GVCFBlock`. Verify it is a true reference-confidence gVCF (`<NON_REF>`/`<*>` ALT), not an `EMIT_ALL_SITES` look-alike. [SPEC §5.5; DOC: GATK] | Whether any consumer vendor ships a true gVCF as standard: **NOT ESTABLISHED**. |
| **Consumer WGS VCF** (Dante, Nebula, Veritas, sequencing.com) | Default to the WGS rule (ambiguous). **Documented danger (observed practice, not vendor spec): no-calls are dropped alongside hom-ref, so absence understates missingness — "absent ⇒ hom-ref" is unsafe.** [PRACTICE: sequencing.com review] Confirm file shape from its own records. | Present as `./.` only if the export retained no-calls; observed practice for at least one vendor is that no-calls are excluded. [PRACTICE] | If present, a called reference/variant record; but observed practice is that hom-ref is excluded from variant-only consumer exports. [PRACTICE] | **Not from vendor docs (NOT ESTABLISHED); in observed variant-only exports, no.** [NOT ESTABLISHED / PRACTICE] | Vendor's **own** documentation of VCF contents (variant-only vs all-sites, gVCF or not): **NOT ESTABLISHED**. Do not rely on vendor name; inspect the file. |

**One rule that spans every row:** absence is never self-interpreting. In exactly one file type — a genuine reference-confidence gVCF — is "reference" stated affirmatively at covered positions; in all others, "reference" at an absent position is an inference that must be justified by coverage or capture information the VCF alone does not carry.

---

## References (resolvable)

Specifications and tool docs:
- VCF/BCF specification (GA4GH / hts-specs), VCFv4.3: https://samtools.github.io/hts-specs/VCFv4.3.pdf ; VCFv4.4: https://samtools.github.io/hts-specs/VCFv4.4.pdf
- Danecek P, et al. The variant call format and VCFtools. *Bioinformatics* 2011. doi:10.1093/bioinformatics/btr330
- Danecek P, et al. Twelve years of SAMtools and BCFtools. *GigaScience* 2021. doi:10.1093/gigascience/giab008
- bcftools manual: https://samtools.github.io/bcftools/bcftools.html ; 23andMe→VCF conversion howto: https://samtools.github.io/bcftools/howtos/convert.html
- GATK, "GVCF — Genomic Variant Call Format": https://gatk.broadinstitute.org/hc/en-us/articles/360035531812-GVCF-Genomic-Variant-Call-Format (archived: https://github.com/broadinstitute/gatk-docs)
- GATK GenotypeGVCFs tool doc: https://gatk.broadinstitute.org/hc/en-us/articles/13832766863259-GenotypeGVCFs
- FreeBayes README: https://github.com/freebayes/freebayes ; Garrison E, Marth G. Haplotype-based variant detection from short-read sequencing. arXiv 2012. doi:10.48550/arXiv.1207.3907
- DeepVariant docs/FAQ: https://github.com/google/deepvariant ; Poplin R, et al. A universal SNP and small-indel variant caller using deep neural networks. *Nat Biotechnol* 2018. doi:10.1038/nbt.4235
- Illumina DRAGEN documentation: "gVCF Output" https://support-docs.illumina.com/SW/DRAGEN_v39/Content/SW/DRAGEN/SMVgVCFOutput.htm ; "gVCF and Joint VCF Mode" (v3.7) https://support.illumina.com/content/dam/illumina-support/help/Illumina_DRAGEN_Bio_IT_Platform_v3_7_1000000141465/Content/SW/Informatics/Dragen/gVCFandJointVCFmode_fDG.htm ; "Genotyper Options" / "gVCF Genotyper Options" https://support-docs.illumina.com/SW/DRAGEN_v40/Content/SW/DRAGEN/gVCFGeontyperOptions_fDG.htm
- PLINK documentation (file formats, no-call encoding): https://www.cog-genomics.org/plink/1.9/formats ; Purcell S, et al. PLINK: a tool set for whole-genome association and population-based linkage analyses. *Am J Hum Genet* 2007. doi:10.1086/519795 ; Chang CC, et al. Second-generation PLINK. *GigaScience* 2015. doi:10.1186/s13742-015-0047-8

Variant callers / methods:
- McKenna A, et al. The Genome Analysis Toolkit. *Genome Res* 2010. doi:10.1101/gr.107524.110
- DePristo MA, et al. A framework for variation discovery and genotyping using next-generation DNA sequencing data. *Nat Genet* 2011. doi:10.1038/ng.806
- Koboldt DC, et al. VarScan 2. *Genome Res* 2012. doi:10.1101/gr.129684.111

WES / capture:
- Ng SB, et al. Targeted capture and massively parallel sequencing of 12 human exomes. *Nature* 2009. doi:10.1038/nature08250
- Bamshad MJ, et al. Exome sequencing as a tool for Mendelian disease gene discovery. *Nat Rev Genet* 2011. doi:10.1038/nrg3031

Imputation:
- Howie BN, et al. A flexible and accurate genotype imputation method. *PLoS Genet* 2009. doi:10.1371/journal.pgen.1000529
- Marchini J, Howie B. Genotype imputation for genome-wide association studies. *Nat Rev Genet* 2010. doi:10.1038/nrg2796

Consumer vendor sources:
- 23andMe, "How 23andMe Reports Genotypes": https://customercare.23andme.com/hc/en-us/articles/212883677-How-23andMe-Reports-Genotypes ; "Raw Genotype Data Technical Details": https://eu.customercare.23andme.com/hc/en-us/articles/115002090907-Raw-Genotype-Data-Technical-Details
- Living DNA, "How do I interpret my raw data?": https://support.livingdna.com/hc/en-us/articles/360028382892
- Dante Labs whole-genome product page: https://dantelabs.com/products/whole-genome-sequencing
- sequencing.com, "Dante Labs Review" (third-party observed VCF-content behavior): https://sequencing.com/blog/post/dante-labs-whole-genome-sequencing-worth-it

*Note on source status:* the 23andMe/Living DNA and GATK/PLINK/caller items are primary vendor or tool documentation. The Dante Labs *VCF-content* behavior in §5b is third-party observed practice (tagged [PRACTICE]); the vendors' own specification of that behavior was not found and is marked [NOT ESTABLISHED].
