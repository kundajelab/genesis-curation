---
name: plant-discovery
description: Find candidate plant datasets through bounded archive and literature
  searches with auditable source links.
metadata:
  version: '3'
  title: Search and source linking
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

# Search and source linking

Use archive APIs first, then literature and domain-restricted web search. Record query,
cursor, errors and source hashes. Accession-seeded replay is not independent discovery. A
citing paper is not necessarily the originating paper; require an explicit accession
relationship. Search snippets are leads, not proof. Search the requested taxa and assay with
bounded typed actions. Keep biological samples, experiments and sequencing runs distinct.
Archive relations establish identity; similar titles do not. Inclusion requires evidence for
each requested constraint; missing evidence means uncertain, not excluded. Conventional
pooled tissue protocols can establish bulk without the word bulk. Protoplast extraction
alone does not establish single cells: require barcode or individually indexed cell/nucleus
evidence. Respect source limits and report partial coverage.

## Required guidance

- [Evidence](../_shared/evidence.md) — loaded with this skill.
