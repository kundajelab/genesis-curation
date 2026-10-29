#!/usr/bin/env python3
"""Load a neutral local demo snapshot; no source service is contacted."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from genesis_curation.web.workspace import import_demo
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--file", type=Path, required=True)
args = parser.parse_args()
print(json.dumps(import_demo(args.root, args.file)))
