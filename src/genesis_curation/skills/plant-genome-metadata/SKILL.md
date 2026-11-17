---
name: plant-genome-metadata
description: Curate assembly references and provenance; do not run genome processing
  or invent QC.
metadata:
  version: '3'
  title: Genome references
  includes:
  - ../_shared/evidence.md
  references:
  - title: Genesis metadata vocabulary
    id: genesis-plant-terms/4
  - title: GEO sample metadata and protocols
    id: https://www.ncbi.nlm.nih.gov/geo/info/seq.html
  - title: FAIR provenance principle
    id: https://www.gofair.foundation/r1-2
---

# Genome references

Record assembly accession and version, organism, subgenome and provenance. A local path is
not a public download or a verified assembly. Imported BUSCO, mapping and QC values remain
external claims; do not report processing success.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
