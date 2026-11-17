---
name: plant-mpra-metadata
description: Curate MPRA and STARR-seq metadata, separating constructs, inputs, readouts
  and biological context.
metadata:
  version: '3'
  title: MPRA and reporter assays
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

# MPRA and reporter assays

Distinguish a paper from its deposited dataset, DNA library input from RNA readout,
construct from biological replicate, and protoplast from whole tissue. Require explicit
species, assay context and accession links. Do not fabricate per-construct measurements from
article summaries.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
