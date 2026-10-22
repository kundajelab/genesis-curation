# Vendored SPP cross-correlation script

Vendored without modifications from kundajelab/phantompeakqualtools.

File revision: 6984a713aba0218b76bacc63f2fb5087425fd6a3
Source: https://github.com/kundajelab/phantompeakqualtools/blob/6984a713aba0218b76bacc63f2fb5087425fd6a3/run_spp.R
BSD 3-Clause license is included.

The inspected Sherlock file at
/oak/stanford/groups/akundaje/marinovg/code/spp/spp_package/run_spp.R
matches upstream revision c8d1feba69e33ffff80ae96397236c6adf683a30 (March 2012),
ignoring whitespace and an added library(caTools) statement. Its inspected SHA256:
b519f0b9861e7085a9e4b4dce61c161d38cdd65fab5cdce5d58b29be34289ce4.

February 2013 upstream revision 50717f350877868a337039f26940eb0b0131332c
changed the NSC/RSC baseline from the minimum correlation to correlation at the
largest tested shift. Later changes include SOCK workers, early dependency
loading, and compatibility with newer SPP versions. The workflow retains the
historical explicit shift range (-s=-0:2:400), but scores can differ because of
the baseline change and aligner differences. The current workflow aligns full
reads for the main analysis and first-read QC, but supplies SPP with a separate
alignment of first reads cut to 50 bases. See the
[DAP analysis choices](https://github.com/kundajelab/genesis-preprocessing/blob/main/docs/usage/dapseq-reference.md#analysis-choices).

The workflow supplies GNU awk for the script's BAM-to-tagAlign conversion.
