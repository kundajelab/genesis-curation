# Run locally or across servers

Genesis runs on one Linux server by default. Install the project with Pixi and
use Docker with your existing account. `genesis doctor` lists available
runtimes and checks Docker access. It never installs services or changes access.

## Managed clusters

Nextflow submits individual tasks through the site's scheduler. Use an explicit
profile and a site configuration; the presence of a scheduler command does not
change the default.

| Environment | Profile | Container runtime |
| --- | --- | --- |
| Linux server | `local,docker` | Docker |
| Slurm | `slurm,apptainer` | Apptainer |
| Sherlock | `sherlock,apptainer` | Apptainer |
| NERSC | `nersc` | Podman-HPC through the supplied adapter |
| PBS Pro | `pbspro,apptainer` | Apptainer |
| LSF | `lsf,apptainer` | Apptainer |
| SGE | `sge,apptainer` | Apptainer |

Copy a template from `conf/sites/` outside the checkout. Set the account,
partition/QoS, resource limits and paths permitted by your site. Inputs, work
folders and the checkout must be visible from compute nodes. Keep the controller
alive for the run; on NERSC use an approved workflow allocation and project
scratch, not a short-lived login session or home filesystem for work files.

Run pipelines from the genesis-preprocessing checkout:

```bash
# DAP-seq on Sherlock
pixi run pipeline -p sherlock,apptainer /path/samples.tsv --references /path/references \
  -c /path/site.config

# NERSC: the adapter path must be visible from compute nodes
GENESIS_RUNTIME_DIR=/scratch/run/runtime scripts/nersc-podman.sh pixi run pipeline -p nersc \
  /path/samples.tsv --references /path/references -c /path/site.config

# Bulk ATAC uses the same scheduler profiles
pixi run pipeline-atac -p slurm,apptainer /path/atac-samples.tsv --references /path/references \
  -c /path/site.config
```

NERSC submissions are capped at 15 outstanding tasks with five-minute scheduler
polling and at most one submission per ten seconds. These are conservative
starting settings, not a throughput guarantee. Administrators manage scheduler
services, node membership and authentication. Genesis does not configure them.
The Podman-HPC adapter defaults to `workspace/runtime/` under the launch
directory. Set `GENESIS_RUNTIME_DIR` to shared project scratch at NERSC; do not
use controller-local `/tmp`. Successful commands remove their private adapter.
Failed commands retain it for recovery while queued jobs may still need it.

Test remote execution at your site before processing a study; local tests
cannot establish scheduler access or site performance.

## Independent workers and isolated datasets

The campaign controller runs separate analysis jobs on existing Linux workers.
Each worker needs the pinned, clean repository, its installed Pixi environment,
and checksummed inputs already staged at the declared paths. Use approved site
transfer tools; Genesis verifies inputs on the worker before starting analysis.
It does not distribute credentials or copy private datasets automatically.

For DAP-seq, keep each treatment with its control; treatments sharing a control should share
one job. A job's sample sheet must describe a complete, valid analysis unit.
Healthy jobs continue if an unrelated job fails.

A campaign is a versioned JSON file with `workers` and `jobs`:

```json
{
  "schema_version": 1,
  "workers": {
    "server1": {
      "transport": "ssh",
      "host": "existing-ssh-alias",
      "slots": 1,
      "repo": "/project/genesis",
      "python": "/project/genesis-curation/.venv/bin/python",
      "root": "/scratch/genesis-jobs"
    }
  },
  "jobs": [{
    "id": "library-1",
    "worker": "server1",
    "payload": {
      "git_sha": "REPLACE_WITH_FULL_40_CHARACTER_COMMIT",
      "inputs": [{"path": "/data/samples.tsv", "sha256": "REPLACE_WITH_SHA256"}],
      "argv": ["/home/user/.pixi/bin/pixi", "run", "pipeline", "-w", "/scratch", "-n", "library-1",
               "-p", "local,docker", "/data/samples.tsv", "--references", "/data/references",
               "-resume"]
    }
  }]
}
```

Include the sample sheet, reference FASTA and configuration in `inputs`.
Commands are trusted configuration: keep credentials out of arguments and files
submitted to the controller. `transport: local` uses the same worker protocol
without SSH and is suitable for a single server. `slots` caps concurrent jobs,
not the memory of each job; set matching Nextflow resource limits.

```bash
genesis run /local/campaign --manifest campaign.json
genesis status /local/campaign
genesis issues /local/campaign
genesis resolve /local/campaign library-1 --note 'Cause corrected; retry approved'
genesis resume /local/campaign
genesis serve /local/campaign --port 8765
```

The browser page binds only to `127.0.0.1` and has no write endpoints. View it
locally, or through an existing authenticated SSH tunnel. Keep the controller's
SQLite state on a local filesystem. Restart with `resume` to reconnect to
existing worker attempts rather than submit duplicates.

Confirmed temporary failures (exit 75) receive at most two automatic retries.
Other failures enter human review. Unreachable or stale workers are `UNKNOWN`:
capacity remains reserved and no duplicate job is submitted. Restore access or
investigate the worker; an unknown job cannot be manually retried until its
supervisor reports a confirmed terminal failure. If the supervisor was lost,
verify on the worker that its process and scheduler jobs have stopped, then use
`genesis reconcile DIRECTORY JOB --confirmed-stopped --note "verification record"`.
A fresh active heartbeat blocks reconciliation. This records the human decision;
it does not kill processes or cancel jobs. Resource-limit failures need
review and an explicit new configuration; memory requests never grow silently.

## Security boundary

SSH uses existing authentication, strict host-key verification, batch mode and
no agent forwarding. Expired MFA credentials require human renewal. Worker
commands run as the existing user, never with elevated privileges. No keys,
firewall rules, Docker groups or scheduler services are changed. Local paths and
raw task logs can contain sensitive metadata; protect campaign and work folders
according to the dataset's access requirements.

For optional scientific review, use a separate controller-local
[curation registry](curation.md). `serve --registry /local/catalog` adds read-only
curation summaries and approved report downloads. `resolve` authorizes an execution
retry; `reconcile` records that an uncertain attempt has stopped. Neither approves
scientific quality.

References: [Nextflow executors](https://www.nextflow.io/docs/latest/executor.html),
[NERSC Nextflow](https://docs.nersc.gov/jobs/workflow/nextflow/),
[NERSC containers](https://docs.nersc.gov/development/containers/),
[Sherlock Apptainer](https://www.sherlock.stanford.edu/docs/software/containers/apptainer/).
