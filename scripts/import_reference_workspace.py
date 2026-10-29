#!/usr/bin/env python3
"""Import a local read-only Sheet snapshot into the demo workspace, preserving all tabs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from genesis_curation.web.workspace import import_workbook

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--workbook', type=Path, required=True)
args = parser.parse_args()
print(json.dumps(import_workbook(args.root, args.workbook)))
