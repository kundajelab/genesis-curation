# RNA curation validation — 2026-11-16

This report covers the RNA evidence workflow, measured-QC import/review and cohort
selection changes. Validation used an isolated runtime; the live demo database and
original reference sources were not modified.

| Check | Result |
| --- | --- |
| `scripts/check.sh` | PASS: lint, formatting, source/test type checks and all offline verification scripts |
| Node 24 frontend build | PASS |
| Isolated browser: QC import → evaluate → study decision → sign-off → export | PASS; no JavaScript errors; first feedback 64 ms |
| 15,000 synthetic records / 1,500 studies | PASS; cached page p95 1.8 ms, selection preview 81 ms, queue acknowledgement 225 ms |
| Restart recovery | PASS; completed work retained, remaining 14,999 records stayed queued |
| Public RNA metadata intake | PASS: 200 records, 67 studies, 17 species; 400/400 requested sample/experiment XML documents retrieved |
| Live plant taxonomy | PASS: exact scientific name and plant ancestry verified for all 17 species |
| Live LLM annotation pilot | BLOCKED: existing local Qwen server returned grammar errors and CUDA illegal-memory-access errors |
| Biological annotation accuracy | NOT EVALUATED; no independent expert gold labels |
| Read-level biological QC | NOT EVALUATED; no real measured-QC table imported |
| 15,000-record live annotation / two-day throughput | NOT RUN |

The convenience sample has 10 archive rows per species plus 30 cases with
non-poly(A)/unknown library selection. It contains 12 records each for Marchantia,
Chlamydomonas, Gossypium, Vitis, both Solanum species, both Arabidopsis species,
Glycine, Medicago, Oryza, Brachypodium and Triticum, and 11 each for Setaria italica,
Setaria viridis, Zea and Sorghum. Selection manifests and source snapshots are retained
in the private pilot runtime; no sequencing data were downloaded.

Archive extraction supported 606 of 1,200 selected core field positions before live
annotation; 594 remained unresolved. This is source-coverage counting, not accuracy.
The failed live run recorded one metadata request and five diagnostic probes using
Qwen3.8-27B-Q8_0. None produced an accepted annotation. A separate tiny transport check
succeeded, but that does not establish structured-inference readiness. Disabling
server-side JSON grammar in the isolated pilot did not resolve the server failures.
No token totals or monetary cost were reported for the failed audited requests.

The synthetic QC scenario has 67 records. Of these, 24 pass, one is flagged, 40 are
not evaluated (missing measurement, insufficient cohort size or zero IQR), and two
fall outside the bulk poly(A) policy. Human retention of the flagged record plus study
sign-off exports 25 records; the other 42 remain in the audit bundle. Tests verify
units, provenance, immutable history, stale metadata/measurement/review rejection,
missing-data behavior and separation of metadata sign-off from QC release.

A larger live run should wait for a healthy structured-inference backend, an expert
review of the pilot annotations, agreed QC thresholds and imported measured results.
The measured software timings do not estimate biological curation throughput.
