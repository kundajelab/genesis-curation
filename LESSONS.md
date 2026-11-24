# Curation lessons

Mistakes that sample-sheet curation has made or nearly made, and how each is now caught. Add a
row whenever a scoring round finds a new kind of miss, together with the trap in
[`evals/traps.yaml`](evals/traps.yaml) that would have caught it. The loop that finds them is
in [evals/README.md](evals/README.md).

| Symptom | Cause | Fix | Trap |
| --- | --- | --- | --- |
| QuantSeq and m6A-IP runs kept as RNA-seq | `\b` treats `_` as a word character, so `\bQuantSeq\b` misses `sample_QuantSeq` | Match words between non-letters, not with `\b` | `rna-other-assays-behind-underscores` |
| The DAP-seq workflow rejects a library | Reads were trimmed before upload and vary in length | Check read lengths before keeping a run; leave trimmed uploads out | `dap-trimmed-uploads` |
| bwa refuses to pair reads | Read 2 was uploaded as its own SRA run with different read names | Run the study single-end on read 1 | `dap-mates-in-two-runs` |
| A "paired" run has one file, or the reverse | The ENA layout label doesn't match the files ENA serves | Trust the files, not the label | `rna-paired-served-as-one-file` |
| Few cells pass barcode matching in some runs | One study mixes MiSeq/NovaSeq and other instruments, whose barcode reads have opposite orientation | Probe the barcode orientation for each run | `scatac-interleaved-instruments` |
| Seed RNA libraries appear as DAP-seq | "DAP" in a title means days after pollination | Require DAP-seq context, not the bare word | `dap-days-after-pollination`, `dap-days-after-pollination-lowercase` |
| TSS libraries kept as RNA-seq | Smar2C2 runs are submitted with library strategy RNA-Seq | Flag 5'-end methods by title, whatever the strategy | `rna-smar2c2-labelled-rna-seq` |
| The same reads are counted twice | Studies resubmitted under a new accession (e.g. PRJNA377558 = PRJNA376779) | Compare read counts and first reads; keep the earliest | `rna-duplicate-submissions`, `rna-duplicate-keeps-earliest` |
| A same-title library is dropped as a duplicate | A matching title is not proof of the same reads | Call a duplicate only when the reads match | `tss-same-title-not-duplicate` |
| Bulk ATAC passes the sample check but fails the full run | 200k-pair TSS enrichment can't predict a FRiP of 0.07 | Record full-run QC in `sheets/data/exceptions.yaml` | `atac-low-frip-gsa` |
| PAT-seq kept as RNA-seq | Submitted as RNA-Seq with oligo-dT selection; only the title says PAT-seq | Flag PAT-seq in titles as another assay | `rna-pat-seq-labelled-rna-seq` |
| Transgenic lines kept as wild type | The genotype field says Col-0; only the title names the transgene | Flag "expressing" and "over-expressing" titles | `rna-line-expressing-foreign-gene` |
| The gold disagrees with a correct build | The gold sheets had errors of their own | Check against ENA, then correct the gold with the maintainers' OK ([gold corrections](evals/gold-corrections.md)) | — |
| Mutants kept as wild type | The genotype field gives only the background (Col-0); the BioSample `isolate` says "T-DNA mutant" | Read `isolate`, but only explicit mutant or transgene words: free-text isolates such as "iso1" and "Col-rep2" look like allele names | `rna-isolate-names-the-mutant` |
| Controls left out as treated | Control and cold runs share one BioSample, and the sample was classified from its first run | Classify each run on its own titles | `rna-control-and-treated-runs-share-a-sample` |
| Wild-type runs dropped for "mutants" in the title | The title describes the study; the sample's genotype field says WT | Let a WT genotype field win, but only when it tells the study's samples apart: a field that says WT for every sample may be the background of a transgenic line | `rna-wild-type-field-beats-study-title`, `rna-wild-type-field-is-the-background` |
| Treated samples missing from every sheet | Treatment was a leave-out reason, but the reference genotype under any condition is usable data (genesis-curation#13) | Keep treated samples; record `treatment` and `treatment_text` in runs-qc; `data/treatments.yaml` names treatments stated only in a study description | `rna-growth-protocol-deficit-recorded`, `scatac-light-time-course-is-kept` |
| A scATAC library dropped as "runs differ in read structure" | Some runs carry a 24-base multiome barcode read, others a 16-base read without the spacer (rice SRP438549, genesis-curation#14) | Keep the runs of the chemistry with the most spots; leave the others out on their own rows | — |
| sci-ATAC libraries reported as "no 10x barcode read" | Their 28-base index read (a 6-base sample index, then the combinatorial cell index; SRR27704581) is a barcode read the workflow did not read | Chemistry `sci-atac`: the whole 28-base read is the barcode, no whitelist (genesis-preprocessing scatac-reads) | — |
| Runs listed in the metadata sheet never appear in runs-qc | ENA runs without taxonomy (A. thaliana PRJNA1272052, rice PRJDB17059) and GSA runs escape a taxon search | Builders take the sheet tabs as `--extra-runs`, and every listed run gets a reason; ENA rejects a batch mixing run and experiment accessions | — |
| Mutant and transgenic RNA-seq lines kept as Col-0 | The genotype field gives the background; only the sample alias or title names the line (`bon123pad4-1_MOCK1`, `VP16-GR`, `pER8-LEC2`) | Read the sample alias; add inducible-line markers; record what no rule can see in `exceptions.yaml` | `rna-mutant-named-only-in-alias`, `rna-transgenic-line-in-title`, `rna-allele-in-alias` |
| A treatment label read as a mutant allele | `ENO2-3` (a protein), `Pb0-3` (lead, 0 h) look like `eno2-1` | Count only lower-case alleles in aliases, and not irradiation words | `rna-uppercase-alias-is-not-an-allele` |
| A DAP-seq study's genotype is `unknown` | ENA's run fields have no ecotype; only the BioSample genotype says Col-0 | Set the study's genotype in `dap_studies.yaml` | — |
| Mutant lines kept although the study's other aliases say `WT` | The alias names the line without an allele pattern (`OR35S`, `hy5_ctrl`, `X0`, `rbohdf`) | Compare aliases within each study and review the runs without the wild-type word with the model; record confirmed ones in `exceptions.yaml` (a rule alone is right only three times in four: treated wild type also lacks the word) | `rna-mutant-named-against-wild-type-aliases`, `rna-treated-wild-type-without-wild-type-marker` |
| Protoplasts, callus and cell cultures kept in RNA-seq | The tissue class `cell culture` was recorded but never left out; a model audit of 130 random studies found them in 6 | Leave out `cell culture` samples (after assay flags); match `cell suspension`, not bare `suspension`, which also names bacterial inocula | — |
