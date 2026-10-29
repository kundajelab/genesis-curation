"""Pinned text-only GGUF options; availability is not a quality endorsement."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any

PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "gemma-4-12b",
        "name": "Google Gemma 4 12B IT",
        "family": "Gemma 4",
        "repository": "google/gemma-4-12B-it-qat-q4_0-gguf",
        "revision": "29d097773436b69ff9feafd636ab4cf873786537",
        "filename": "gemma-4-12b-it-qat-q4_0.gguf",
        "bytes": 6975879296,
        "sha256": "93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b",
        "quantization": "QAT Q4_0",
        "license": "Apache-2.0",
        "context_ceiling": 262144,
    },
    {
        "id": "gemma-4-e4b",
        "name": "Google Gemma 4 E4B IT",
        "family": "Gemma 4",
        "repository": "google/gemma-4-E4B-it-qat-q4_0-gguf",
        "revision": "4b4a2c1d584be7264f87aac328a1bc739ce81b6c",
        "filename": "gemma-4-E4B_q4_0-it.gguf",
        "bytes": 5154941280,
        "sha256": "676c35070db6dbe52f93e9c864ee0fba4eddea94b9c875d9cb10daff453fbaee",
        "quantization": "QAT Q4_0",
        "license": "Apache-2.0",
        "context_ceiling": 131072,
    },
    {
        "id": "ministral-3-14b",
        "name": "Mistral Ministral 3 14B Instruct",
        "family": "Ministral 3",
        "repository": "mistralai/Ministral-3-14B-Instruct-2512-GGUF",
        "revision": "74fac473c43357d7fb2671713608183cc72496d0",
        "filename": "Ministral-3-14B-Instruct-2512-Q8_0.gguf",
        "bytes": 14359836224,
        "sha256": "8122acc8df446039d41dd9650dabb024d3c6b8dae6eff336d28ac2bddd3bbc13",
        "quantization": "Q8_0",
        "license": "Apache-2.0",
        "context_ceiling": 262144,
    },
)


def catalog(status_path: Path | None = None) -> list[dict[str, Any]]:
    status = {}
    if status_path and status_path.exists():
        with status_path.open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError("Local model status file exceeds 64 KiB")
        status = json.loads(raw)
        if not isinstance(status, dict):
            raise ValueError("Local model status must be an object")
    models = []
    for item in PRESETS:
        receipt = status.get(item["id"], {})
        installed = isinstance(receipt, dict) and receipt.get("sha256") == item["sha256"]
        tested = receipt.get("tested_context") if installed else None
        models.append(
            {
                **item,
                "source": "https://huggingface.co/" + item["repository"],
                "evaluation": "Biological accuracy not evaluated",
                "suggested_context": 32768,
                "installed": installed,
                "tested_context": tested if type(tested) is int and tested > 0 else None,
                "json_probe": installed and receipt.get("json_probe") is True,
            }
        )
    return models


def preset(key: str) -> dict[str, Any]:
    for item in PRESETS:
        if item["id"] == key:
            return dict(item)
    raise ValueError("Unknown local model preset")


def verify(path: Path, item: dict[str, Any]) -> bool:
    if not path.is_file() or path.stat().st_size != item["bytes"]:
        return False
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest() == item["sha256"]


def download(key: str, directory: Path) -> dict[str, Any]:
    """Explicit controller action. Partial files stay on the destination volume."""
    item = preset(key)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / item["filename"]
    if target.exists():
        if not verify(target, item):
            raise ValueError(
                "Existing model does not match the pinned artifact; refusing overwrite"
            )
        return {"path": str(target), "sha256": item["sha256"], "status": "verified"}
    reserve = 2 * 1024**3
    if shutil.disk_usage(directory).free < item["bytes"] + reserve:
        raise ValueError("Insufficient storage: model size plus 2 GiB reserve required")
    temporary = target.with_suffix(".gguf.partial")
    # Exclusive creation prevents two controller requests from sharing a download.
    with temporary.open("xb") as output:
        try:
            url = (
                f"https://huggingface.co/{item['repository']}/resolve/"
                f"{item['revision']}/{item['filename']}"
            )
            with urllib.request.urlopen(url, timeout=120) as response:
                count = 0
                while block := response.read(8 * 1024**2):
                    count += len(block)
                    if count > item["bytes"]:
                        raise ValueError("Downloaded artifact exceeds pinned size")
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if not verify(temporary, item):
                raise ValueError("Downloaded artifact failed size/SHA-256 verification")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.rename(target)
    return {"path": str(target), "sha256": item["sha256"], "status": "verified"}
