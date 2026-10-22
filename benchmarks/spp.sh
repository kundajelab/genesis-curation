#!/usr/bin/env bash
set -euo pipefail
mkdir -p qc-bin
ln -sf "$(command -v gawk)" qc-bin/awk
export PATH="$PWD/qc-bin:$PATH"
cp -- "$1" spp-input.bam
Rscript "$2" -c=spp-input.bam -p="$3" -rf -s=-0:2:400 -savp=spp.pdf -out=spp.tsv
