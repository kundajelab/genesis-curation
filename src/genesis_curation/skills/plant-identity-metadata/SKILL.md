---
name: plant-identity-metadata
description: Resolve plant taxonomy and sample identity without propagating labels
  between samples.
metadata:
  version: '3'
  title: Taxonomy and sample identity
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

# Taxonomy and sample identity

Preserve original taxon names, ranks, hybrids and cultivar groups. Keep tissue, stage,
genotype and treatment separate. Do not use sample labels as tissues or fill missing values
from neighboring samples. Link only exact experiment identities; shared BioProjects are not
duplicates.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
