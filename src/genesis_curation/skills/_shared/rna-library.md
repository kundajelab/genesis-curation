# RNA library metadata

Separate RNA-seq assay from library source and selection. Poly(A)/oligo-dT, rRNA depletion
and total RNA are distinct protocols; RNA-seq alone establishes none of them. Require
positive evidence to distinguish bulk, single-cell and single-nucleus libraries; record
cell_resolution as bulk, single-cell or single-nucleus only with explicit protocol evidence.
Protoplast extraction alone is not single-cell. Preserve strandedness, preparation, platform
and reference assembly/version. Record age with its stated units and starting event;
developmental stage is not an age measurement. Keep genotype, cultivar, tissue, treatment
and environment separate. Biological replicates are not sequencing runs or lanes. Use paper
methods only with an explicit sample or uniquely mapped sample label; a general methods
description does not establish every sample's attributes. Quality metrics must come from
measurements, never from model confidence.
