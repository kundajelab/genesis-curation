# Evidence and repeat-curation checks

This follow-up audits the saved nine-record public-metadata pilot and addresses
specific failures found there. It does not establish general biological accuracy.

## Changes

- Assay classification now reads attached ENA/SRA experiment library descriptors
  when the experiment accession matches the record exactly. Study context, other
  experiments and multi-experiment documents cannot supply that evidence. Original
  metadata remains unchanged. Library controls and contradictory strategies still
  enter exclusion/review; scATAC still requires barcode/modality review.
- Single-cell RNA-seq and single-nucleus RNA-seq stay in RNA-seq rather than being
  classified as scATAC merely because they mention single cells.
- The model checklist explicitly includes missing genotype, cultivar, treatment,
  developmental stage, age, selection and cell resolution. These are evidence
  search requests, not instructions to invent missing values or new approval gates.
- Cultivar is visible separately from genotype in the default GUI table and draft
  tabular exports. The pilot's Col-0 value was already present as cultivar; a blank
  genotype column did not mean that the value had been lost.
- Study re-curation no longer silently clears earlier proposed fields when the
  model omits them. Prior proposals are labelled as such in the prompt, never used
  as independent evidence. Explicit unknown responses can withdraw proposals;
  authoritative source conflicts and existing human decisions retain precedence.

## Validation

An independent XML attribute comparison over the stored pilot snapshots found
24/24 exact matches across nine records. Only explicit, matching-sample attributes
were compared. This excludes inferred descriptions, omitted attributes, paper
attribution and assay interpretation; it is a consistency check, not a blinded
accuracy benchmark or an independent evaluation dataset.

The revised context checklist was tested on the three PRJDB3559 records using the
configured hosted Opus endpoint. Preparation completed without failures in 39.2 s,
with two review items left. Cumulative conservative reservations rose from $3.623268
to $4.247476, below the existing $5 cap. These numbers are not provider billing.

That live retry exposed the omission bug: some earlier proposed platform,
environment, replicate and taxonomy values disappeared. The retention fix was
subsequently verified using synthetic inference: omitted values and recommendations
survive, explicit unknown responses clear them, and neither creates human approval.
The final retention fix has not received another paid live run. Earlier field values
remain in the pilot's immutable history and before/after report; this report does
not silently restore test outputs or call the reduced review count an accuracy gain.

Regression coverage includes cross-library isolation, study-context exclusion,
conflicting archive strategies, attached control protocols, RNA modality routing,
restricted field investigations, cultivar exports and proposal retention. The GUI
build and a synthetic browser check passed, with the cultivar column visible and
no browser errors. Final `scripts/check.sh`: PASS, including lint, formatting,
source/test type checks, all 18 verification programs and the 15,000-record
synthetic scale fixture. Biological accuracy and 15,000 live annotations remain
explicitly NOT RUN by that offline suite.

## Remaining work

An expert should label a held-out set spanning RNA-seq, bulk/scATAC, TSS and binding
assays, with sample-specific evidence and explicit unknowns. The existing blinded
audit workflow can compare those labels with frozen proposals. Until that evaluation
exists, neither completeness nor expert-level accuracy is claimed. Local-model
inference and large live campaigns also remain untested in this acceptance pass.
