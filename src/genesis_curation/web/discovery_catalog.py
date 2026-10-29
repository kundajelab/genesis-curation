"""Versioned assay and source recipes shared by discovery, curation and the UI."""

from __future__ import annotations

from typing import Any

VERSION = "plant-discovery/2"
ASSAYS: dict[str, dict[str, Any]] = {
    "RNA-seq": {
        "modality": "RNA-seq",
        "strategies": ["RNA-Seq"],
        "terms": ["RNA-seq", "RNA sequencing"],
    },
    "ATAC-seq": {
        "modality": "Bulk ATAC",
        "strategies": ["ATAC-seq", "OTHER"],
        "terms": ["ATAC-seq", "ATAC sequencing"],
    },
    "scATAC-seq": {
        "modality": "scATAC",
        "strategies": ["ATAC-seq", "OTHER"],
        "terms": ["scATAC", "snATAC", "single cell ATAC", "single nucleus ATAC", "multiome"],
    },
    "TSS": {
        "modality": "TSS",
        "strategies": ["OTHER", "RNA-Seq"],
        "terms": [
            "CAGE",
            "RAMPAGE",
            "nanoPARE",
            "TSS-seq",
            "csRNA-seq",
            "STRIPE-seq",
            "nanoCAGE",
            "PEAT",
            "TIF-seq",
            "5GRO-seq",
            "5 DGE",
            "oligo-capping",
            "NET-CAGE",
            "Smar2C2",
            "NAD-seq",
            "NAD captureSeq",
            "TREseq",
        ],
    },
    "DAP-seq": {
        "modality": "DAP-seq",
        "strategies": ["ChIP-Seq", "OTHER"],
        "terms": ["DAP-seq", "DNA affinity purification sequencing", "ampDAP-seq"],
    },
    "ChIP-seq": {
        "modality": "ChIP-seq",
        "strategies": ["ChIP-Seq"],
        "terms": ["ChIP-seq", "chromatin immunoprecipitation"],
    },
    "WGBS": {
        "modality": "WGBS",
        "strategies": ["Bisulfite-Seq"],
        "terms": ["WGBS", "whole genome bisulfite", "methylome"],
    },
    "MPRA": {
        "modality": "MPRA",
        "strategies": ["OTHER", "RNA-Seq"],
        "terms": [
            "MPRA",
            "STARR-seq",
            "massively parallel reporter",
            "promoter library",
            "terminator library",
        ],
    },
    "Genomes": {
        "modality": "Genomes",
        "strategies": ["WGS"],
        "terms": ["genome assembly", "reference genome", "pangenome"],
    },
}
MODALITIES = [
    "DAP-seq",
    "TSS",
    "Bulk ATAC",
    "scATAC",
    "MPRA",
    "Genomes",
    "RNA-seq",
    "ChIP-seq",
    "WGBS",
    "Other",
]
SOURCES = [
    {"id": "geo", "name": "GEO", "host": "www.ncbi.nlm.nih.gov", "mode": "archive"},
    {"id": "ena", "name": "ENA / SRA", "host": "www.ebi.ac.uk", "mode": "archive"},
    {"id": "sra", "name": "NCBI SRA", "host": "www.ncbi.nlm.nih.gov", "mode": "archive"},
    {"id": "papers", "name": "Europe PMC / PubMed", "host": "www.ebi.ac.uk", "mode": "literature"},
    {"id": "gsa", "name": "GSA", "host": "ngdc.cncb.ac.cn", "mode": "public pages"},
    {"id": "assembly", "name": "NCBI Assembly", "host": "www.ncbi.nlm.nih.gov", "mode": "assembly"},
    {"id": "web", "name": "Google / web search", "host": "", "mode": "SearXNG"},
]
KNOWN_REPOSITORIES = [
    {"name": "JGI / Phytozome", "domain": "phytozome-next.jgi.doe.gov"},
    {"name": "JGI Plant Gene Atlas", "domain": "plantgeneatlas.jgi.doe.gov"},
    {"name": "Ensembl Plants", "domain": "plants.ensembl.org"},
    {"name": "Plant Cistrome", "domain": "neomorph.salk.edu"},
    {"name": "ChIP-Hub", "domain": "biobigdata.nju.edu.cn"},
    {"name": "ReMap", "domain": "remap.univ-amu.fr"},
    {"name": "Expression Atlas", "domain": "ebi.ac.uk"},
    {"name": "GSA / NGDC", "domain": "ngdc.cncb.ac.cn"},
]
