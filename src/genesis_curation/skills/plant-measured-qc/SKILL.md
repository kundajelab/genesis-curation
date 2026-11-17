---
name: plant-measured-qc
description: Review imported QC measurements and cohort policies; do not substitute
  model confidence for measurements.
metadata:
  version: '3'
  title: Imported quality measurements
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

# Imported quality measurements

Import measurements with units, method, reference, sample identity and provenance. Missing
values are not zero. Evaluate only explicitly configured thresholds; keep species, assay and
library-selection cohorts separate. Statistical outliers are review candidates, not
automatic failures. Never infer mapping, BUSCO, FRiP or replicate quality from an abstract
or LLM confidence. The current quantitative QC evaluator is RNA-focused; other assay QC
needs explicit policy.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
