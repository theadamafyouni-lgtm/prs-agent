# Input formats and standard conversions: a documentation-grounded basis for assay-class classification and provenance-preserving normalization

**Scope.** This document describes (1) what each input format an agent might receive for polygenic scoring *is*, at the record level, so it can be classified; (2) what the standard conversion tools *do to the data* — what they add, drop, and infer — along the consumer-text → VCF → PLINK path that is the common route to a scoring-ready file; and (3) what "array semantics" look like in a VCF *after* a consumer text file has been converted, i.e. the residual signatures that let a downstream stage tell that a VCF was born from an array rather than from sequencing.

**Out of scope (belongs to R3).** What an *absent position* means in each assay class (unmeasured vs. outside capture vs. calling-dependent) is not treated here. This document describes format structure and conversion mechanics; it does not adjudicate the semantics of missingness.

**Convention.** Claims are marked **[SOURCE]** when a cited primary source states them directly, and **[INFERENCE]** when they are our synthesis extrapolating past what any single source asserts. Where the literature does not settle a point it is marked **[NOT ESTABLISHED]**.

---

## 1. The six input formats at the record level

### 1.1 VCF (Variant Call Format)

- VCF is a text (optionally BGZF-compressed; binary counterpart BCF) format for representing variants **called and anchored against a reference genome**. Each data line has eight mandatory columns — CHROM, POS, ID, REF, ALT, QUAL, FILTER, INFO — optionally followed by FORMAT and per-sample genotype columns. **[SOURCE: Danecek et al. 2011, *Bioinformatics* 27(15):2156–2158, DOI 10.1093/bioinformatics/btr330; VCF spec, https://github.com/samtools/hts-specs]**
- The REF column shows the reference bases; the ALT column the alternate haplotype(s); the coordinate refers to the first reference base. VCF can express SNVs, MNVs, insertions, deletions, and simple structural variants. **[SOURCE: Danecek et al. 2011; Garrison et al. 2022, *PLoS Comput Biol* 18(5):e1009123, DOI 10.1371/journal.pcbi.1009123]**
- The GT subfield encodes the genotype; the allele separator marks phase (`|` phased, `/` unphased); missing values are `.`. **[SOURCE: Danecek et al. 2011]**
- **Critical for classification:** whether a VCF contains records only at variant sites, or *also* carries "invariant"/all-sites records, depends entirely on how the callset was generated. GATK's own documentation states: "there may only be records for sites where a variant was identified, or there may also be 'invariant' records." **[SOURCE: GATK, "VCF - Variant Call Format," https://gatk.broadinstitute.org/hc/en-us/articles/360035531692]** → A bare `.vcf` extension therefore does **not** by itself tell you the assay class or the completeness model; the record content must be inspected. **[INFERENCE]**

### 1.2 gVCF (genomic VCF)

- A gVCF is a valid VCF that additionally carries a record for **all** sites (or interval blocks), whether or not a variant was called there — its purpose is to make every position representable for later joint genotyping. **[SOURCE: GATK, "GVCF - Genomic Variant Call Format," https://gatk.broadinstitute.org/hc/en-us/articles/360035531812]**
- Signatures that identify a GATK-style gVCF:
  - a `<NON_REF>` symbolic allele in the ALT field of records, declared in the header as `##ALT=<ID=NON_REF,...>`; **[SOURCE: GATK GVCF doc; GATK issue #6243, https://github.com/broadinstitute/gatk/issues/6243]**
  - non-variant **block** records carrying an `INFO/END` tag marking where the reference-confidence block ends, with FORMAT fields like `GT:DP:GQ:MIN_DP:PL` and `GT` typically `0/0`; **[SOURCE: GATK GVCF doc]**
  - a `##GVCFBlock` header line defining the GQ bands used to group non-variant sites. Two emission modes exist: `BP_RESOLUTION` (one record per site) and `GVCF` (banded blocks). **[SOURCE: GATK GVCF doc; broadinstitute/gatk-docs GVCF FAQ]**
- Nuance worth recording: the `<NON_REF>` header text ("any possible alternative allele") is acknowledged by GATK developers to be imprecise — in practice it means any allele *other than* the reference and any already-listed ALT. **[SOURCE: GATK issue #6243]**
- The genotype calls emitted by HaplotypeCaller *in gVCF mode* are explicitly described by GATK as non-final intermediates, to be re-genotyped by `GenotypeGVCFs`. **[SOURCE: GATK legacy FAQ, "What is a GVCF..."]** → A gVCF should be treated as a pre-joint-genotyping intermediate, not a finished callset. **[INFERENCE]**

### 1.3 Raw consumer export — 23andMe text

- A tab-separated ASCII text file, one line per SNP, with a `#`-prefixed comment header. Four columns: **rsid, chromosome, position, genotype**, where genotype is the two alleles concatenated (e.g. `AG`). **[SOURCE: bcftools "Converting from 23andMe to VCF," https://samtools.github.io/bcftools/howtos/convert.html; Cheng, "23andMe to plink," https://www.jade-cheng.com/au/23andme-to-plink/]**
- Genotypes are reported **on the plus (+) strand** of a named reference assembly — GRCh37 ("Build 37"/Annotation Release 104) for the classic download, GRCh38 in the newer Browse feature. **[SOURCE: 23andMe Customer Care, "Raw Genotype Data Technical Details," https://eu.customercare.23andme.com/hc/en-us/articles/115002090907]**
- No-calls are written `--` (not a genotype). Haploid contexts (male X/Y, mitochondrial) may report a single allele. Some markers use internal `i`-prefixed IDs rather than rsIDs. Typical size ~550,000–700,000 markers depending on chip version (v3/v4/v5). **[SOURCE: 23andMe Technical Details; dnaexplore.ai 2026 file walkthrough]**
- A given chromosome+position may appear on more than one line (different marker IDs), and their reported genotypes can even differ. **[SOURCE: Cheng, "23andMe to plink"]** → duplicate-position handling is a required normalization step, not an edge case. **[INFERENCE]**

### 1.4 Raw consumer export — AncestryDNA text

- Also tab-delimited with a `#`-prefixed header block, but **five** columns: **rsid, chromosome, position, allele1, allele2** — the two alleles are in *separate* columns rather than concatenated. **[SOURCE: h600.org "Microarray File Formats," reproducing the AncestryDNA header; biostars 399381, https://www.biostars.org/p/399381/]**
- The header states positions are on **human reference build 37.1** and genotypes are reported **on the forward (+) strand**; it also records the array version (e.g. "AncestryDNA array version: V1.0"). **[SOURCE: h600.org, AncestryDNA header text]**
- Allele fields use A/C/G/T plus `I`/`D` (insertion/deletion) and `--` (no-call). **[SOURCE: h600.org]**
- Format differences across vendors are real and load-bearing for parsing: 23andMe/AncestryDNA use tab-separated `.txt`; MyHeritage/FamilyTreeDNA use comma-separated `.csv`; header conventions differ (23andMe leads with `#` comments, AncestryDNA with a metadata block + header row). **[SOURCE: helixline.in raw-DNA format reference; beholdgenealogy.com five-company comparison]**

### 1.5 Genotyping array (native / manifest-level)

- A genotyping array measures genotypes at a **fixed, pre-designed set** of marker positions (the manifest), not the whole genome — SNP genotyping "reads specific positions" and "can miss variants that exist between the tested positions." **[SOURCE: dnaexplore.ai; helixline.in]** The consumer text files in 1.3–1.4 are exports of such array calls.
- Strand is governed by a naming convention. Illumina arrays use the **TOP/BOT** convention (also expressible as plus/minus or forward/reverse); the SNP alleles in a manifest are listed in A/B order per TOP/BOT, not REF/ALT order. **[SOURCE: Illumina, "How to interpret DNA strand and allele information for Infinium genotyping array data," https://knowledge.illumina.com/microarray/general/microarray-general-reference_material-list/000001489; Illumina TOP/BOT tech note, https://www.illumina.com/documents/products/technotes/technote_topbot.pdf]**
- **The load-bearing ambiguity:** for **A/T and C/G** SNPs (strand-ambiguous / palindromic), the alleles are reverse-complements of each other, so the alleles alone cannot fix the strand; only the probe sequence compared to the reference (or an external allele-frequency prior) resolves it. **[SOURCE: PMC4441213 Illumina exome-array QC; StrandScript, Chen et al. 2017, *Bioinformatics* 33(15):2399, https://academic.oup.com/bioinformatics/article/33/15/2399/3204987; PMC6099125 "Is 'forward' the same as 'plus'?"]**

### 1.6 WES and WGS

- WES (whole-exome) and WGS (whole-genome) are **assays**, not distinct file formats: the file an agent receives from either is normally a **VCF** (variant-sites) or **gVCF** (all-sites/blocks) produced by a caller such as GATK HaplotypeCaller or freebayes. **[SOURCE: Garrison et al. 2022; GATK GVCF doc]**
- The exome array/WES capture is **targeted** — it "targets the exome plus rare SNPs" — whereas WGS spans the genome. **[SOURCE: PMC4441213]** At the file level this shows up as which regions carry records (and, in a gVCF, which regions carry reference-confident blocks vs. no data). **[INFERENCE — the format-level observation; the *semantics* of an absent position is R3's question and not resolved here.]**
- The PGS Catalog Calculator explicitly notes WGS input "will probably encounter match rate errors" without preprocessing, and that compatible gVCFs can be produced from WGS but support is still maturing. **[SOURCE: pgsc_calc, "How do I prepare my input genomes?," https://pgsc-calc.readthedocs.io/en/latest/how-to/prepare.html; Seqera pgsc_calc page]**

---

## 2. What the standard conversions do to the data

The common route from a consumer export to a scoring-ready file is **text → VCF (bcftools) → PLINK binary (or PLINK 2 pgen)**. Each hop transforms the data in specific, documentable ways.

### 2.1 Consumer text → VCF via `bcftools convert --tsv2vcf`

Canonical command (bcftools; SAMtools/BCFtools, Danecek et al. 2021, *GigaScience* 10(2):giab008, DOI 10.1093/gigascience/giab008):

```
bcftools convert -c ID,CHROM,POS,AA -s SampleName -f ref.fa --tsv2vcf in.txt -Oz -o out.vcf.gz
```
**[SOURCE: bcftools(1) manual, https://samtools.github.io/bcftools/bcftools.html#convert; bcftools convert howto]**

What this conversion does:

1. **Requires an external reference FASTA (`-f`) and a sample name (`-s`).** The reference is mandatory because the input text has no REF/ALT — only a genotype call. The tool reads the reference base at each position to assign REF, and derives ALT from the observed alleles. **[SOURCE: bcftools convert howto; bcftools manual]**
2. **It emits homozygous-reference genotypes explicitly.** The conversion prints a summary that tallies genotype classes — `Hom RR`, `Het RA`, `Hom AA`, `Het AA`, plus `Missing GTs` and `Rows skipped` — confirming that sites whose genotype equals the reference are written out as `0/0` records, not dropped. **[SOURCE: bcftools convert howto — reports these tallied classes; the specific numeric counts on the example page are not reproduced here.]** This is the single most important consequence for later classification (see §3).
3. **It is SNP-only.** Indels ("deletions and insertions") are skipped and counted under `Rows skipped`. **[SOURCE: bcftools convert howto]**
4. **No-calls become missing genotypes.** `--` entries are counted as `Missing GTs` and rendered as missing (`./.`). **[SOURCE: bcftools convert howto]**
5. **It reports "non-reference het" counts** as a QC signal — a large `Het AA` (both alleles non-reference) count is a red flag for strand/build mismatch between the text file and the FASTA. **[SOURCE: bcftools convert howto]** → This means the conversion's correctness is contingent on the FASTA build matching the file's stated build; a mismatch silently produces wrong REF/ALT assignments. **[INFERENCE, grounded in the tool's own QC guidance.]**
6. **The reference build must be supplied out-of-band.** The text file states its build only in a comment header (GRCh37 for classic 23andMe/AncestryDNA); the conversion does not verify it — the operator must pick a matching FASTA (e.g. GRCh37 from Ensembl). **[SOURCE: bcftools convert howto; biostars 374149; biostars 399381 for AncestryDNA]**

**AncestryDNA specificity:** because AncestryDNA has five columns (alleles split), the two allele columns must be handled to fit `--tsv2vcf`'s expected genotype column; users concatenate `allele1`+`allele2` or use the same `-c ID,CHROM,POS,AA` mapping against a GRCh37 FASTA. **[SOURCE: biostars 399381]**

### 2.2 The alternative path: consumer text → VCF via PLINK `--recode vcf`

- PLINK can ingest a 23andMe file directly with `--23file` and emit a VCF with `--recode vcf`. **[SOURCE: PLINK 1.9, https://www.cog-genomics.org/plink/1.9/input; Ubuntu/Hexmos plink1.9 manpages]**
- **Known limitation that erases information:** for a homozygous site, PLINK-based export "has no ALT allele if the genotype is homozygous" — PLINK stores only the *observed* alleles, so a monomorphic (all-reference-looking) site yields a record with ALT undefined (`.`). **[SOURCE: biostars 374149 discussion]** The mechanism is PLINK's A1/A2 allele model (§2.3): a site where only one allele was observed has no second allele to place in ALT. **[INFERENCE, grounded in the PLINK format definition below.]**
- Consequence: the bcftools path (reference-aware, assigns REF/ALT from FASTA) and the PLINK path (observation-only) produce **different VCFs from the same input** — the bcftools output distinguishes "reference" from "alternate," the naive PLINK output may not. **[INFERENCE]** For provenance preservation this argues for a reference-aware conversion and for recording which tool/version performed it.

### 2.3 VCF (or consumer text) → PLINK binary `.bed/.bim/.fam`

PLINK binary filesets are the classic downstream scoring target (second-generation PLINK: Chang et al. 2015, *GigaScience* 4:7, DOI 10.1186/s13742-015-0047-8).

- **`.bed`** — the packed binary genotype matrix; "primary representation of genotype calls at biallelic variants," 2 bits per genotype (hom-A1, het, hom-A2, missing), variant-major. **[SOURCE: PLINK 1.9 file-format reference, https://www.cog-genomics.org/plink/1.9/formats]**
- **`.bim`** — one line per variant: chromosome code, variant ID, genetic distance (Morgans; `0` = unspecified), base-pair coordinate, then **A1** and **A2** alleles. **[SOURCE: PLINK 1.9 formats; Marees et al. protocol, https://link.springer.com/protocol/10.1007/978-1-0716-0199-0_3]**
- **`.fam`** — one line per sample (FID, IID, pedigree, sex, phenotype). **[SOURCE: PLINK 1.9 formats]**

What this conversion does to the data:

1. **Collapses to biallelic.** The `.bed` model represents biallelic genotypes only; multiallelic sites must be split or are dropped. **[SOURCE: PLINK 1.9 formats; Marees et al., "SNPs with three or four alleles ... most software developers ignore them ... 'split' a triallelic SNP"]**
2. **Reduces the genotype to a 2-bit allele-count code.** Per-read depth, genotype likelihoods (PL/GL), and site quality (QUAL) present in a VCF are **not represented** in a PLINK1 `.bed`; the format carries hard calls only. **[SOURCE: PLINK 1.9 formats — the `.bed` stores only the four genotype codes]** → dosage/likelihood information requires PLINK 2 `.pgen` and is otherwise lost. **[INFERENCE, grounded in the PLINK1 format definition; PLINK 2 formats add dosage.]**
3. **Re-labels alleles as A1/A2 by frequency, not by reference.** By default the minor allele is A1 (5th column) and the major allele A2 (6th column); the reference-genome allele is placed in A2 *only if* reference information is provided. **[SOURCE: Marees et al. protocol]** → Absent an explicit reference (`--ref-from-fa` / `--keep-allele-order` in practice), the VCF's REF/ALT orientation can be **silently reassigned** in the `.bed`, which is a provenance hazard for effect-allele matching in scoring. **[INFERENCE, grounded in the A1/A2 frequency default.]**
4. **Recodes chromosome names to integer codes.** 23andMe's `X, Y, XY, MT` become PLINK codes `23, 24, 25, 26`. **[SOURCE: Cheng, "23andMe to plink"]**
5. **Drops phase.** PLINK genotype codes are unordered allele counts; any phase present in a VCF `GT` is not retained. **[INFERENCE, grounded in the `.bed` genotype-code definition.]**

### 2.4 What the scoring stage itself expects and does

- `pgsc_calc` (the PGS Catalog Calculator; Lambert et al. 2021, *Nat Genet* 53:420, DOI 10.1038/s41588-021-00783-5; new-functionality preprint medRxiv 10.1101/2024.05.29.24307783) accepts target genotypes in **PLINK bfile/pfile or VCF**, and requires the operator to declare the target genome build via `--target_build` (only GRCh37 or GRCh38 supported). **[SOURCE: pgsc_calc GitHub README; pgsc_calc getting-started, https://pgsc-calc.readthedocs.io/en/latest/getting-started.html]**
- Its variant-matching step handles **strand alignment, ambiguous (palindromic) matches, multiallelic matches, and duplicates**, and emits an auditable log of how every scoring-file variant matched (or why it was excluded). **[SOURCE: pgsc_calc, "Why not just use plink2 --score?," https://pgsc-calc.readthedocs.io/en/latest/explanation/plink2.html]** → The strand-ambiguity problem of §1.5 is not eliminated by conversion; it is deferred to and explicitly managed at the matching step. **[INFERENCE, grounded in the pgsc_calc matching documentation.]**
- Low variant coverage or a mismatched build "can introduce substantial uncertainty," which is exactly the failure mode of feeding an array-derived (sparse) VCF, or a build-mismatched VCF, to a scoring model without checking match rate. **[SOURCE: pgsc_calc outputs doc, https://pgsc-calc.readthedocs.io/en/latest/explanation/output.html]**

---

## 3. What "array semantics" look like in a VCF after a consumer text → VCF conversion

After §2.1, the file is a syntactically ordinary VCF. It is nonetheless recognizably **array-derived**. The following are the residual signatures — the basis for classifying a VCF as array-origin rather than sequencing-origin. Each individual signature is sourced; the *use of the combination as a classifier* is inference.

1. **Records only at the array's fixed marker set.** The VCF has on the order of ~500k–700k biallelic SNP records (chip-dependent), sparse and non-contiguous across the genome, rather than the millions-to-tens-of-millions of a WGS callset. **[SOURCE: marker counts — 23andMe Technical Details, dnaexplore.ai; sparsity is intrinsic to array design — PMC4441213]**
2. **Explicit homozygous-reference (`0/0`) records.** Because `--tsv2vcf` writes out every genotyped site including reference-matching ones (`Hom RR` count in §2.1), an array-derived VCF contains dense `0/0` calls at the marker positions. A typical sequencing variant-only VCF, by contrast, mostly omits `0/0` sites. **[SOURCE: bcftools convert howto for the emission behavior; GATK VCF doc for the "records only where variant identified" alternative]** → The presence of many `0/0` records at a *sparse, fixed* set of positions — but **without** gVCF block structure — is a strong array signature. **[INFERENCE]**
3. **No reference-confidence apparatus.** Unlike a gVCF, there is no `<NON_REF>` allele, no `INFO/END` blocks, and no `##GVCFBlock` header — the `0/0` calls are point assertions at measured markers, carrying no model of the un-typed space between them. **[SOURCE: GATK GVCF doc defines this apparatus, whose absence distinguishes an array VCF]** **[INFERENCE for the "absence ⇒ array" direction.]**
4. **SNP-only content.** Indels were dropped at conversion (§2.1), so an array-derived VCF is essentially free of indel records. **[SOURCE: bcftools convert howto]**
5. **No sequencing-style per-genotype quality.** There is no `DP`, `GQ`, `PL`/`GL`, or informative `QUAL` — the array's own confidence metrics (e.g. GenCall/GenTrain scores) are not part of the text export and are not reconstructed by conversion. **[SOURCE: 23andMe/AncestryDNA exports carry only genotype calls — 23andMe Technical Details, h600.org AncestryDNA header; bcftools tsv2vcf produces a minimal `FORMAT=GT` record — bcftools manual example output]** **[INFERENCE for the completeness of the absence.]**
6. **rsID-keyed, build-specific coordinates.** The ID column is populated from the array's rsIDs (including vendor-internal `i`/`kgp` IDs that may lack a dbSNP entry), and POS is on the file's stated build (GRCh37 for classic consumer exports) — not necessarily the build of the scoring model. **[SOURCE: 23andMe Technical Details; h600.org; helixline.in on build differences]** → A liftover may be required before matching; the PGS Catalog can supply harmonized scoring files across GRCh37/GRCh38 to meet the target rather than lifting the sample. **[SOURCE: pgsc_calc getting-started]**
7. **Residual strand ambiguity at palindromic SNPs.** Even though consumer exports declare a plus/forward strand, the alleles alone cannot verify orientation at A/T and C/G markers; that ambiguity survives conversion into the VCF and must be resolved at scoring-time matching. **[SOURCE: PMC6099125; StrandScript; pgsc_calc plink2 explanation]**
8. **Duplicate-position residue.** Because the source text can list one position under multiple marker IDs (§1.3), the converted VCF can contain duplicate-position records unless de-duplicated. **[SOURCE: Cheng, "23andMe to plink"]**

**Net:** an array-origin VCF is distinguishable from a sequencing-origin VCF by the *conjunction* of {sparse fixed-marker layout, explicit point `0/0` with no gVCF block apparatus, SNP-only, no DP/GQ/PL, rsID-keyed GRCh37 coordinates, palindromic-strand ambiguity}. No single field is a proof; the pattern is. **[INFERENCE]**

---

## 4. Implications for classification and provenance-preserving normalization

These follow from §§1–3; they are framed as inferences the spec can adopt, each traceable to the sourced facts above.

1. **File extension ≠ assay class.** `.vcf`/`.vcf.gz` can hold a sequencing callset, a gVCF, or an array-derived callset; `.txt` can be 23andMe (4-col) or AncestryDNA (5-col) or another vendor. Classification must read content (header, column count, record density, presence of `<NON_REF>`/`END`/`GVCFBlock`, `0/0` density, indel presence, FORMAT fields), not the name. **[INFERENCE, grounded in §1.1, §3.]**
2. **Normalizing to one standard format is lossy in specific, recordable ways.** The text→VCF→PLINK path adds inferred REF/ALT (from an out-of-band FASTA), drops indels, drops quality/dosage/phase, and can reassign allele orientation. To normalize "without erasing provenance," the pipeline must **record at run time**: the source assay class and vendor/chip, the declared build, the reference FASTA used, the conversion tool + version (bcftools vs PLINK path differ materially), and the variant-matching/strand decisions — rather than pinning versions. **[INFERENCE, grounded in §2 and consistent with the project's live-API/run-time-versioning constraint.]**
3. **The choice of conversion tool is itself provenance.** The reference-aware bcftools path and the observation-only PLINK `--recode vcf` path produce different REF/ALT semantics from identical input (§2.2); which was used changes what the effect-allele match means downstream. **[SOURCE for the divergence: biostars 374149; INFERENCE for the provenance implication.]**
4. **Strand ambiguity is not resolved by conversion — it is deferred.** Palindromic-SNP orientation survives into the normalized file and is settled only at scoring-time matching, where `pgsc_calc` handles it explicitly and logs it. A normalization step that silently "flips" palindromic SNPs without recording the decision erases provenance. **[SOURCE: pgsc_calc plink2 explanation, PMC6099125; INFERENCE for the "don't silently flip" prescription.]**
5. **[NOT ESTABLISHED]** There is no single published, canonical algorithm for *classifying* an arbitrary genomic file into {array-derived VCF, sequencing VCF, gVCF, WES, WGS} purely from content. The signatures in §3 are drawn from format specifications and tool documentation and are internally consistent, but the literature does not provide a validated, benchmarked classifier for this decision; treating the §3 conjunction as a heuristic (not a proof) is the defensible position.

---

## References (all resolvable)

1. Danecek P, et al. The variant call format and VCFtools. *Bioinformatics* 27(15):2156–2158 (2011). DOI: 10.1093/bioinformatics/btr330
2. VCF/BCF specification, samtools/hts-specs. https://github.com/samtools/hts-specs
3. Garrison E, et al. A spectrum of free software tools for processing the VCF variant call format. *PLoS Comput Biol* 18(5):e1009123 (2022). DOI: 10.1371/journal.pcbi.1009123
4. Danecek P, et al. Twelve years of SAMtools and BCFtools. *GigaScience* 10(2):giab008 (2021). DOI: 10.1093/gigascience/giab008
5. bcftools(1) manual (convert). https://samtools.github.io/bcftools/bcftools.html#convert
6. bcftools "Converting from 23andMe to VCF." https://samtools.github.io/bcftools/howtos/convert.html
7. GATK, "VCF - Variant Call Format." https://gatk.broadinstitute.org/hc/en-us/articles/360035531692
8. GATK, "GVCF - Genomic Variant Call Format." https://gatk.broadinstitute.org/hc/en-us/articles/360035531812
9. GATK issue #6243 (NON_REF definition). https://github.com/broadinstitute/gatk/issues/6243
10. 23andMe Customer Care, "Raw Genotype Data Technical Details." https://eu.customercare.23andme.com/hc/en-us/articles/115002090907
11. Cheng J, "23andMe to plink." https://www.jade-cheng.com/au/23andme-to-plink/
12. AncestryDNA raw-data header, reproduced at h600.org "Microarray File Formats." https://h600.org/wiki/Microarray+File+Formats
13. biostars, "convert ancestry.com text file to vcf." https://www.biostars.org/p/399381/
14. biostars, "bcf convert 23andme to vcf." https://www.biostars.org/p/374149/
15. PLINK 1.9 file-format reference. https://www.cog-genomics.org/plink/1.9/formats
16. PLINK 1.9 standard data input (`--23file`). https://www.cog-genomics.org/plink/1.9/input
17. Chang CC, et al. Second-generation PLINK. *GigaScience* 4:7 (2015). DOI: 10.1186/s13742-015-0047-8
18. Marees AT, et al. Data Management and Summary Statistics with PLINK. *Methods Mol Biol* (2020). DOI: 10.1007/978-1-0716-0199-0_3
19. Illumina, "How to interpret DNA strand and allele information for Infinium genotyping array data." https://knowledge.illumina.com/microarray/general/microarray-general-reference_material-list/000001489
20. Illumina, "TOP/BOT Strand and A/B Allele" tech note. https://www.illumina.com/documents/products/technotes/technote_topbot.pdf
21. Illumina exome genotyping array clustering and QC. PMC4441213. https://pmc.ncbi.nlm.nih.gov/articles/PMC4441213/
22. Chen CY, et al. StrandScript. *Bioinformatics* 33(15):2399 (2017). https://academic.oup.com/bioinformatics/article/33/15/2399/3204987
23. "Is 'forward' the same as 'plus'? … SNP allele nomenclature." PMC6099125. https://pmc.ncbi.nlm.nih.gov/articles/PMC6099125/
24. Lambert SA, et al. The Polygenic Score Catalog. *Nat Genet* 53:420–425 (2021). DOI: 10.1038/s41588-021-00783-5
25. PGS Catalog: new functionality and tools (preprint). medRxiv (2024). DOI: 10.1101/2024.05.29.24307783
26. pgsc_calc documentation — getting started, prepare input genomes, "why not just plink2 --score?," outputs. https://pgsc-calc.readthedocs.io/
27. pgsc_calc GitHub. https://github.com/PGScatalog/pgsc_calc
28. helixline.in, raw-DNA data format reference. https://helixline.in/blog/what-is-raw-dna-data
29. dnaexplore.ai, "What's in Your 23andMe Raw Data File?" (2026). https://dnaexplore.ai/blog/what-is-in-your-23andme-raw-data
