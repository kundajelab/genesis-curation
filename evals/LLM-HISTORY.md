# LLM evaluation history

One row per `genesis eval llm` run.

| date | task | split | model | cases | metrics | cost USD | seconds | label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-19 | adjudicate | dev | claude-opus-5-5 | 58 | agreement 0.3333, keep_precision 0.3333, keep_recall 0.4286, uncertain 0.7414, errors 0 | 0.28 | 305 | pilot, prompt v1 |
| 2026-11-19 | triage | dev | claude-opus-5-5 | 96 | match_accepted 0.5789, match_not_excluded 0.5789, exclude_excluded 0.5065, exclude_accepted 0.0, excluded_precision 1.0, errors 45 | 0.97 | 457 | pilot, app prompt |
| 2026-11-19 | adjudicate | dev | claude-opus-5-5 | 58 | agreement 0.5455, keep_precision 0.7692, keep_recall 0.5882, uncertain 0.2414, errors 0 | 0.57 | 164 | pilot rerun, sequential, 16k output |
| 2026-11-19 | triage | dev | claude-opus-5-5 | 96 | match_accepted 0.9474, match_not_excluded 0.9474, exclude_excluded 0.961, exclude_accepted 0.0, excluded_precision 1.0, errors 1 | 2.04 | 320 | pilot rerun, sequential |
| 2026-11-19 | adjudicate | dev | claude-opus-5-5 | 58 | agreement 0.4419, keep_precision 0.6667, keep_recall 0.4, uncertain 0.2586, errors 0 | 0.27 | 77 | run enum, no length caps |
| 2026-11-19 | adjudicate | dev | claude-opus-5-5 | 58 | agreement 0.5769, keep_precision 1.0, keep_recall 0.5111, uncertain 0.1034, errors 0 | 0.26 | 68 | verbatim quote instruction |
| 2026-11-19 | adjudicate | dev | claude-opus-5-5 | 58 | agreement 0.5263, keep_precision 0.8214, keep_recall 0.5111, uncertain 0.0172, errors 0 | 0.28 | 8 | retry skipped runs |
