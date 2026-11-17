# Built-in curation skills

A fresh install includes 14 versioned, hashed guidance packs. No separate skill
checkout, download or hosted service is needed. The GUI lists them under Models
and skills; `genesis curation skills` returns the same catalog. Study preparation
selects modality guidance automatically. Single-record AI settings also allow an
explicit skill or no-skill comparison.

## Modality coverage

| Data | Automatic pack | Main safeguards |
| --- | --- | --- |
| Bulk RNA-seq | `plant-rna-metadata` | Selection, strandedness, biological context, age versus stage |
| Single-cell/nucleus RNA | `plant-celltype-metadata` | RNA rules plus author labels versus inferred labels; abstain without expression evidence |
| Bulk ATAC and scATAC | `plant-atac-metadata` | Library protocol, controls, barcodes and modality evidence |
| TSS | `plant-tss-metadata` | CAGE/RAMPAGE/nanoPARE and related protocol distinctions |
| DAP-seq and TF ChIP-seq | `plant-binding-metadata` | TF donor versus assayed DNA, target identity and explicit control relationships |
| WGBS | `plant-methylation-metadata` | Conversion method, CG/CHG/CHH distinctions, controls and measured QC |
| MPRA/STARR-seq | `plant-mpra-metadata` | DNA input versus RNA readout, constructs, replicates and protoplast context |
| Genome metadata | `plant-genome-metadata` | Assembly accession/version, subgenome and provenance |
| Unclassified records | `plant-metadata` | Evidence, unknowns, identity and review safeguards |

Routing uses library modality before broad source-text hints. Recognized assay names
also select the pack when accession intake has no Discover tab label. A known WGBS
library does not become a single-cell RNA task because its description mentions an
RNA comparison. A selected skill is guidance, not a verified assay classification.

## Full workflow coverage

| Stage | Guidance and execution |
| --- | --- |
| Discover | `plant-discovery` is used directly by the bounded agentic mining planner; archive queries and source recipes are implemented by the application |
| Retrieve and link | Shared evidence/identity rules plus `plant-identity-metadata`; the application preserves source snapshots and checks exact sample/experiment links |
| Literature enrichment | Shared sample-attribution rules apply automatically; `plant-literature-evidence` is available for focused review. Paper retrieval and binding are implemented by the application |
| Normalize and propose | Modality pack plus shared rules on original values, vocabularies, conflicts and unsupported claims |
| Imported QC | `plant-measured-qc` supplies review guidance; configured measurements, units and thresholds are evaluated by code, not by the LLM |
| Human review and release | `plant-study-review` supplies review guidance; version checks, reviewer decisions, audit history and clean-export sign-off are enforced by code |

Lifecycle packs are available for explicit selection; they are not autonomous agents
executing downloads, approving studies or running pipelines. Every built-in pack
includes shared rules about exact evidence, untrusted source instructions, paper
attribution, ontology mappings and human approval. Discovery uses the same catalog
entry shown in the UI, rather than a separate hidden instruction block.

## Sources and limits

The metadata rules follow the separation of study, sample and protocol information
in [GEO submission guidance](https://www.ncbi.nlm.nih.gov/geo/info/seq.html) and
[GEO validation requirements](https://www.ncbi.nlm.nih.gov/geo/info/validation.html).
Source/change records support the [FAIR provenance principle](https://www.gofair.foundation/r1-2).
[ENCODE WGBS standards](https://www.encodeproject.org/data-standards/wgbs/) inform
measurement provenance and control reporting; their thresholds are not treated as
universal plant standards.

The cell-type pack cites the pinned Alex Borowsky plant-celltype-annotation reference
and applies its evidence/uncertainty principles. This is metadata guidance, not an
installation or validation of that entire expression-analysis workflow. Without
marker/expression evidence, inferred labels must remain unknown.

The quantitative QC evaluator is currently RNA-focused. Other modalities have
metadata guidance, not fully validated assay-specific numerical QC. Genome skills
do not download assemblies or execute BUSCO; no sequencing preprocessing or model
training is included. Search coverage still depends on source access and configured
web search. Skills cannot guarantee complete discovery or biological accuracy.

## Extension and verification

Custom instruction-only JSON packs go in the runtime's `skills/` directory. They
require `id`, `version`, `title`, `instructions`, and `references`; IDs must be unique,
and the instruction budget is 16,000 characters. Skill-supplied code is never run.
Custom packs are selectable explicitly; they do not silently replace automatic
modality routing. Changing a pack changes its hash and invalidates affected cached
preparation. References and examples are not evidence about a new sample.

`tests/verify_skills.py` checks all nine modality groups, accession-based routing,
wrong-modality hints, shared discovery instructions, hashes and common safeguards
without external skill files. `scripts/check.sh` includes this regression along with
provider, evidence, review, QC and scale checks. These are software tests; a blinded
expert-labelled biological benchmark is still required before claiming accuracy.

Acceptance on 2026-11-17: full `scripts/check.sh` PASS (19 verification programs,
plus lint, formatting and type checks); GUI build PASS; all 14 packs visible in the
browser with zero browser errors; CLI skill/provider list output PASS. No paid model
calls or biological-accuracy claims were made in this skill-coverage acceptance run.
