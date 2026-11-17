---
name: plant-methylation-metadata
description: Curate plant DNA methylation library methods, sequence contexts and measured
  control evidence.
metadata:
  version: '3'
  title: DNA methylation libraries
  includes:
  - ../_shared/evidence.md
  references:
  - title: Genesis metadata vocabulary
    id: genesis-plant-terms/4
  - title: GEO sample metadata and protocols
    id: https://www.ncbi.nlm.nih.gov/geo/info/seq.html
  - title: FAIR provenance principle
    id: https://www.gofair.foundation/r1-2
  - title: ENCODE WGBS standards (not universal plant thresholds)
    id: https://www.encodeproject.org/data-standards/wgbs/
---

# DNA methylation libraries

Distinguish WGBS, reduced-representation bisulfite, enzymatic conversion and other
methylation assays by protocol; do not relabel all as WGBS. Preserve conversion chemistry,
genomic versus organellar context and reference version in protocol/notes when no dedicated
field exists. Keep CG, CHG and CHH contexts distinct when reported. Conversion controls,
coverage and replicate agreement require measured evidence. Do not import mammalian CpG-only
criteria or fixed QC thresholds into plant studies without a documented assay policy.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
