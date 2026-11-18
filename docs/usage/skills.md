# Curation skills

A *skill* is a short, versioned set of instructions that the application gives the model
during an LLM step, such as discovery planning or proposing metadata for a study. Skills tell
the model what evidence counts, which fields to keep apart and when to leave a value unknown.
They do not run code, download data or approve anything. Those steps are done by the
application and by human review.

Fourteen skills ship with the package in
[`src/genesis_curation/skills/`](../../src/genesis_curation/skills/). No download or separate
service is needed. List them in the GUI under **Models and skills**, or from the CLI:

```sh
genesis curation skills
```

`genesis sheets` does not use skills. Its rules are code and data files; see
[sample sheets](sample-sheets.md).

## Assay skills

When a study is prepared for curation, the application picks one assay skill from the
record's library information (library strategy, source and selection). It uses the assay
name only when the library information is missing, for example after accession intake.

| Skill | Used for | What it guards against |
| --- | --- | --- |
| `plant-rna-metadata` | Bulk RNA-seq | Assuming poly(A) selection or bulk from "RNA-seq"; mixing age with developmental stage |
| `plant-celltype-metadata` | Single-cell and single-nucleus RNA-seq | Inventing cell-type labels without marker or expression evidence |
| `plant-atac-metadata` | Bulk ATAC and scATAC | Missing controls; calling a library bulk or single-cell without barcode evidence |
| `plant-tss-metadata` | TSS assays (CAGE, RAMPAGE, nanoPARE and others) | Treating nascent-RNA or NAD-capture libraries as TSS assays |
| `plant-binding-metadata` | DAP-seq and TF ChIP-seq | Confusing the TF's source species with the assayed DNA; guessing control pairs |
| `plant-methylation-metadata` | WGBS and other methylation assays | Labelling every method WGBS; applying mammalian CpG-only rules |
| `plant-mpra-metadata` | MPRA and STARR-seq | Confusing DNA input with RNA readout, or constructs with replicates |
| `plant-genome-metadata` | Genome assemblies | Recording a local path as a verified assembly; claiming processing results |
| `plant-metadata` | Anything not covered above | Filling fields without evidence |

The chosen skill is guidance for the model, not a verified assay classification. In the GUI's
single-record AI settings you can pick a different skill, or none, to compare results.

## Workflow skills

These cover steps of the workflow rather than an assay:

| Skill | Step | Role |
| --- | --- | --- |
| `plant-discovery` | Discover | Used by the discovery planner: archive APIs first, bounded searches, report partial coverage |
| `plant-identity-metadata` | Retrieve and link | Taxonomy and sample identity; link only exact experiment identities |
| `plant-literature-evidence` | Literature | Attribute paper passages to a sample only through an exact accession or label |
| `plant-measured-qc` | Imported QC | Review imported measurements; thresholds are evaluated by code, not the model |
| `plant-study-review` | Review and release | Prepare study-level decisions; sign-off stays with a human |

`plant-discovery` is used automatically. The others can be selected explicitly; their shared
rules are already part of every assay skill.

## Shared rules

Every skill includes [`_shared/evidence.md`](../../src/genesis_curation/skills/_shared/evidence.md):
cite only exact source passages, treat retrieved text as untrusted data, keep genotype and
cultivar (and age and stage) separate, keep missing, conflicting and not-applicable values
apart, and leave release decisions to a human. The RNA and cell-type skills also include
[`_shared/rna-library.md`](../../src/genesis_curation/skills/_shared/rna-library.md).

## Skill files

Each skill is a folder with a `SKILL.md`: YAML frontmatter followed by Markdown
instructions.

```text
src/genesis_curation/skills/
├── _shared/
│   ├── evidence.md
│   └── rna-library.md
├── plant-rna-metadata/
│   └── SKILL.md
└── ... one folder per skill
```

The frontmatter has `name` and `description`, and a `metadata` block with:

- `version` and `title`, shown in the GUI;
- `includes`, the Markdown files loaded together with the skill;
- `references`, source links given as attribution. They are not downloaded or used as
  evidence.

The application records a hash of each loaded skill, including its includes. Editing a skill changes the hash and invalidates cached study preparation that
used it. Packaged skills are cached: restart the server and worker after editing them.

## Custom skills

Add your own skills under the runtime's `skills/` directory, using the same layout:

```yaml
# $GENESIS_RUNTIME/skills/lab-evidence-review/SKILL.md
---
name: lab-evidence-review
description: Review ambiguous sample-to-paper mappings for this laboratory.
metadata:
  version: '1'
  title: Laboratory evidence review
---

Compare exact archive sample identifiers with the article's sample table.
Keep ambiguous matches unresolved and include the competing evidence in review.
```

Custom skills are re-read on every use and are only used when selected explicitly; they never
replace the automatic assay choice. Their `includes` must stay inside the skill's own folder.
The loader checks names, frontmatter and include paths. It rejects symlinks and paths that
leave the folder, and it limits a skill and its includes to 16,000 characters in total. It
never runs scripts from a skill folder.

## Tests and limits

`tests/verify_skills.py` checks skill selection for every assay group, routing by accession,
misleading assay hints, hashes, include safety and the shared rules. It runs as part of
`scripts/check.sh`. These are software tests. Whether the skills produce biologically
accurate metadata still needs an expert-labelled evaluation.

The rules draw on [GEO submission guidance](https://www.ncbi.nlm.nih.gov/geo/info/seq.html),
[GEO validation requirements](https://www.ncbi.nlm.nih.gov/geo/info/validation.html), the
[FAIR provenance principle](https://www.gofair.foundation/r1-2) and
[ENCODE WGBS standards](https://www.encodeproject.org/data-standards/wgbs/). ENCODE thresholds
are not treated as plant standards. The cell-type skill follows the evidence and uncertainty
principles of the plant-celltype-annotation reference cited in its frontmatter.
