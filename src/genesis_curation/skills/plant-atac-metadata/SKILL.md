---
name: plant-atac-metadata
description: Curate bulk and single-cell ATAC libraries, controls and barcode/modality
  evidence.
metadata:
  version: '3'
  title: ATAC libraries and controls
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

# ATAC libraries and controls

Verify transposase accessibility at library level. Separate genomic DNA, input and non-ATAC
controls. For single-cell/nucleus and multiome, require barcode and modality evidence. Do
not infer bulk from absence of single-cell words.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
