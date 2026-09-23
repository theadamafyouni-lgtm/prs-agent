# Run-time version capture for traceability

**Scope.** For a live (non-pinned) product, a result is traceable only if, at run time,
the agent records the version of every external reference it consulted — read from the
object that traveled with the result, not from a version looked up separately. This
section specifies what to capture for the three reference sources and separates what the
sources document from what we infer.

**Accepted premise (project rule).** References move. A moved reference may yield a
different percentile on re-run. The goal of run-time capture is not to freeze the world;
it is to make a re-run's difference *explainable after the fact* rather than merely
observed.

---

## (a) PGS Catalog scoring models

Capture at three levels, because each answers a different question.

### 1. The scoring file's own header (authoritative)
Every PGS Catalog scoring file — and every harmonized `_hmPOS_` file — begins with
`#`-prefixed metadata. This header is the primary run-time artifact: it travels with the
file and identifies the exact model instance offline. Record verbatim:

| Field | Meaning |
|---|---|
| `#format_version` | scoring-file schema version (e.g. `2.0`) |
| `#pgs_id` | e.g. `PGS000001` |
| `#pgs_name` | |
| `#trait_efo` | mapped trait ontology term |
| `#genome_build` | build of the *original* weights (may be `NR` = not reported) |
| `#variants_number` | |
| `#weight_type` | |
| `#pgp_id`, `#citation` | source publication |
| `#HmPOS_build` | build the harmonized positions were mapped to (`GRCh37`/`GRCh38`) |
| `#HmPOS_date` | **date of harmonization — the single most important reproducibility field** |

`#HmPOS_date` matters most: it dates the position-mapping step, which is the part of the
file that can change between downloads even when the underlying published weights do not.

### 2. The per-score REST record (provenance)
`GET https://www.pgscatalog.org/rest/score/{pgs_id}` returns (field names confirmed live
against the API): `id`, `name`, `ftp_scoring_file`, `ftp_harmonized_scoring_files` (a dict
keyed by `GRCh37`/`GRCh38`, each with a `positions` FTP URL on `ftp.ebi.ac.uk`),
`variants_number`, `variants_genomebuild`, `weight_type`, `method_name`, `method_params`,
`ancestry_distribution`, `matches_publication`, `date_release`, and `license`.
Record: `date_release`, the resolved harmonized-file URL actually downloaded, and
`license` (the license text governs whether the result may even be reported).

### 3. The catalog release resolved against (global state)
`GET https://www.pgscatalog.org/rest/release/current` returns `date`, `score_count`,
`performance_count`, `publication_count`, `efotrait_count`, `notes`, and the lists
`released_score_ids` / `released_publication_ids` / `released_performance_ids` /
`released_new_trait_ids`. Record `date` — the closest thing the catalog has to a global
"database version," pinning catalog state at the moment the `select` stage chose the model.

**Sourced vs. inferred.**
- *Sourced:* header fields, REST field names, and release fields are read directly from the
  live API and a live scoring file (`PGS000001_hmPOS_GRCh38.txt.gz`). The catalog is
  described as "an open database for reproducibility and systematic evaluation" in its
  primary publication — Lambert SA et al., *Nature Genetics* 2021,
  doi:10.1038/s41588-021-00783-5, PMID 33692568, PMC11165303 (DOI+PMCID+PMID verified live
  via NCBI id-converter and Europe PMC).
- *Inferred (mark in spec):* that recording release `date` + per-file `#HmPOS_date` is
  *sufficient* to reconstruct the exact file later. The catalog publishes no formal
  guarantee that `date`+`pgs_id` resolves byte-identically forever, so treat header capture
  (level 1) as authoritative and REST fields as provenance around it.
- *Not established:* the REST API exposes **no schema/API version field of its own**.
  Traceability rests on release `date` + per-object dates. Do not invent an API version;
  record the retrieval timestamp alongside the release `date`.

---

## (b) All of Us reference distribution

**There is no standalone "reference distribution version" object.** The population reference
a percentile is computed against derives from a specific **Curated Data Repository (CDR)
release**, and the version *is* that release string.

### The version string and where it lives
CDR releases carry a coded identifier: Controlled Tier looks like `C2022Q4R13` (this string
is CDR v7 Controlled Tier); Registered Tier looks like `R2024Q3R8` (CDR v8 Registered Tier).
At run time this string is encoded in the **BigQuery dataset reference actually queried** —
e.g. `fc-aou-cdr-prod-ct.R2024Q3R8` (legacy) vs `wb-affable-acorn-7941.R2024Q3R9`
(Researcher Workbench 2.0). **Record the fully-qualified dataset reference string of every
query used to build the reference distribution;** the `C####Q#R#` / `R####Q#R#` suffix is
the version.

### Two things must be captured, not one
1. the **CDR release string** (identifies the tabular/phenotype snapshot and, within it, the
   srWGS/array genomic release the calls came from); and
2. the **ancestry / genetic-similarity grouping** used to stratify the reference
   distribution — a percentile is only meaningful within its ancestry-matched group, and the
   same CDR yields different percentiles under different grouping definitions.

### Operational constraint on re-runnability
Only the three most recent CDR versions are available in the Researcher Workbench at any
time (DRC data-management/retention policy); older workspaces (v3–v6) are not eligible for
migration. This is exactly the accepted "moved reference" case: the reference distribution
is not archivable indefinitely on AoU's side, so the run-time record of the CDR string is the
only durable trace of which distribution produced a given percentile.

**Sourced vs. inferred.**
- *Sourced:* the version-string convention, the dataset-reference paths, and the
  three-version retention window (AoU User Support release notes and Workbench 2.0 migration
  docs).
- *Inferred (mark in spec):* that the dataset reference string is the *right thing to capture
  as the version* — it is the only run-time-visible carrier of the release identifier in a
  query.
- *Not established:* a documented environment variable exposing the CDR version
  programmatically. The migration docs discuss configuring environment variables but do not
  name a CDR-version variable. **Do not hardcode an env-var name.** The safe, guaranteed
  requirement is: record the fully-qualified BigQuery dataset reference of every query used to
  build the reference distribution — it necessarily contains the version string.

---

## (c) Imputation reference panels

The version is a **job parameter plus a set of tool versions**, and both appear only in the
imputation job's own metadata. Capture from the job record, not from a config you set or docs
you read (the server updates tool versions in place under a stable panel label).

### What to record
1. **Reference panel + submit parameters** — `refpanel` (current TOPMed value
   `apps@topmed-r3`; earlier jobs `r2`), `build` (`hg38`), `phasing` (`eagle`), `population`
   (`all`), and the `r2Filter` / Rsq threshold. The r2 filter changes which variants survive,
   hence which PGS positions are present.
2. **Server + tool versions from the job record** — TOPMed Imputation Server v2.0.0 fronts
   Minimac4 (4.1.6) for imputation and Eagle (v2.4 / v2.4.1) for phasing with the r3 panel.
   The API job JSON carries an `application` field (e.g. `"Genotype Imputation (Minimac4)
   1.8.0"`) with its own independent version number — capture that field verbatim rather than
   trusting docs, which drift.
3. **Job identity** — the API job record includes a stable `id`
   (e.g. `job-20230627-204701-307`), submission timestamps, and a downloadable QC report.
   Record the job `id` and **archive the QC report** as the run-time artifact — it is the
   panel-version equivalent of the PGS file header.

### Why this is load-bearing for assay class
Panel version + `r2Filter` together determine which positions get imputed and at what
quality. A PGS variant present after imputation against `topmed-r3` may be absent (or below
the r2 cutoff) against `topmed-r2` — so the panel version is load-bearing for interpreting an
absent position, exactly as the project's assay-class principle requires. The panel is on
hg38 coordinates; record the submitted `build`, since a liftOver occurred if the input was
hg19.

**Sourced vs. inferred.**
- *Sourced:* panel identifiers, tool versions, the `application`/`id`/QC-report fields, and
  the submit parameters (TOPMed Imputation Server / Michigan Imputation Server docs and API
  reference).
- *Inferred (mark in spec):* capturing `application` + QC report *from the job record* rather
  than trusting the docs' stated versions is our recommendation, not a documented
  reproducibility procedure — but it follows from the server updating tool versions in place
  under an unchanged panel label.
- *Not established:* the panels themselves (TOPMed r2/r3 haplotypes) are **not publicly
  downloadable**. This is the strongest "moved reference" case: you cannot archive the panel,
  only its identifier and the server's tool versions. If TOPMed retires `r3` or changes what
  `apps@topmed-r3` points to, re-imputation can differ, and the job-level record captured at
  run time is the only defense.

---

## Capture contract (summary)

| Source | Version carrier recorded at run time | Authoritative artifact | Not established / caveat |
|---|---|---|---|
| PGS Catalog model | file header (`#format_version`, `#pgs_id`, `#HmPOS_build`, `#HmPOS_date`) + REST `date_release` + `/rest/release/current` `date` | the `#`-header of the downloaded harmonized file | no API/schema version field exists |
| All of Us reference | fully-qualified BigQuery dataset reference (`C####Q#R#` / `R####Q#R#` suffix) + ancestry grouping definition | the dataset reference string in every query | no standalone version object; only 3 CDRs kept live; no confirmed env-var name |
| Imputation panel | `refpanel`, `build`, `phasing`, `population`, `r2Filter` + job `application` field + job `id` | archived QC report + job JSON | panels not publicly downloadable; server tool versions change in place |

**Through-line for the spec:** for each source, the run-time record must come from the object
that traveled with the result — file header, dataset reference string, job record — not from a
version looked up separately, because in all three cases the looked-up version can drift while
the embedded one is fixed at the moment of use. That is what makes a re-run's differing
percentile explainable rather than merely different.

---

### Citations
- PGS Catalog primary publication: Lambert SA, Gil L, Jupp S, Ritchie SC, Xu Y, Buniello A,
  McMahon A, Abraham G, Chapman M, Parkinson H, Danesh J, MacArthur JAL, Inouye M. "The
  Polygenic Score Catalog as an open database for reproducibility and systematic evaluation."
  *Nature Genetics* 53, 420–425 (2021). doi:10.1038/s41588-021-00783-5 · PMID 33692568 ·
  PMC11165303. (DOI, PMID, and PMCID cross-verified live via NCBI id-converter and Europe PMC.)
- PGS Catalog REST API: https://www.pgscatalog.org/rest/ (release + score endpoints queried live).
- PGS Catalog scoring-file format & downloads: https://www.pgscatalog.org/downloads/
- All of Us CDR release notes / Researcher Workbench: https://support.researchallofus.org/
- TOPMed Imputation Server docs & API: https://topmedimpute.readthedocs.io/ ,
  https://statgen.github.io/tis-v2-docs/
