
### Disk headroom

Build application images on the development host, then transfer the image to the demo.
Keep the current image and one explicitly tagged `genesis-curation-web:previous` rollback.
Do not prune volumes or model files. App/worker Docker logs rotate at 10 MiB × 3.
Monitor the **data filesystem**: `/api/storage` warns below 6 GiB and blocks new
queued work below 2 GiB. The worker latches a pause before claiming another job;
Review shows a **Resume background work** button after space is recovered. An in-flight
request may finish before this check. Campaign checkpoints and original records remain.
Only remove identified caches/obsolete application images; never automatically delete
SQLite, evidence, model weights, Conda environments or other applications.

### Study review

Review uses one study checklist, with lazy evidence and paginated sample summaries.
Curation's **Review study** link opens that same checklist. Decisions stay within the
study; **Next study** is explicit. Select affected samples before applying shared
changes. Each correction still requires evidence for every selected sample. Drafts,
original → prepared values, immutable decisions and versioned exports are retained.
Study identity uses the source accession (unassigned records remain separate), not
campaign IDs, assay or LLM batch size. No aliases are inferred from shared papers.
Known inventory records missing from membership block sign-off. New membership
requires preparation again, and invalidates a prior current sign-off without deleting it.
