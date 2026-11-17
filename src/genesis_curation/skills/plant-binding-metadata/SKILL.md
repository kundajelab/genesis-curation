---
name: plant-binding-metadata
description: Curate DAP-seq and TF ChIP-seq metadata, distinguishing assayed DNA,
  TF donors and controls.
metadata:
  version: '3'
  title: Plant TF-binding metadata
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

# Plant TF-binding metadata

Distinguish TF tags, TF donors and assayed DNA species; distinguish input/IgG/empty-vector controls
from untreated biological conditions. Control assignment needs explicit sample
relationships; no majority-label filling.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
