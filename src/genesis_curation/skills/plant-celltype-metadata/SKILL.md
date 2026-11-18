---
name: plant-celltype-metadata
description: Curate single-cell or single-nucleus RNA metadata and evidence-supported
  cell labels; abstain without expression evidence.
metadata:
  version: '3'
  title: Plant single-cell metadata
  includes:
  - ../_shared/evidence.md
  - ../_shared/rna-library.md
  references:
  - title: 'Alex Borowsky: plant-celltype-annotation v0.2.1'
    id: https://github.com/alexborowsky/plant-celltype-annotation/tree/7c99b333b60e235b57e2a45efba42c032544037b
---

# Plant single-cell metadata

Preserve author-reported cell_type separately from inferred_cell_type. A new inferred label
requires supplied RNA marker/expression evidence, anatomical plausibility and uncertainty.
Distinguish cell identity from stress or cell state; homology is a hypothesis, not marker
validation. Missing expression evidence means abstain. Never treat ATAC gene activity as
measured RNA expression.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
- [RNA library](../_shared/rna-library.md) — loaded with this skill.
