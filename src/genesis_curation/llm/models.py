"""Read-only Hugging Face inspection; model weights and code are never loaded here."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote
from urllib.request import Request, build_opener

from ..contracts.records import fingerprint, identity, loads, now, record
from .providers import NoRedirect
from .security import credential

MAX_METADATA = 8 * 1024 * 1024


def model_id(value: str) -> str:
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value)
        or ".." in value
    ):
        raise ValueError("Use a namespace/model Hugging Face repository ID")
    return value


def fetch(url: str, secret: str = "") -> dict[str, Any]:
    headers = {"User-Agent": "Genesis-model-inspection/1"}
    if secret:
        headers["Authorization"] = "Bearer " + secret
    with build_opener(NoRedirect()).open(Request(url, headers=headers), timeout=20) as response:
        raw = response.read(MAX_METADATA + 1)
    if len(raw) > MAX_METADATA:
        raise ValueError("Model metadata exceeds inspection budget")
    return loads(raw.decode())


def inspect(
    identifier: str,
    revision: str,
    snapshot: dict[str, Any] | None = None,
    token_env: str | None = None,
) -> dict[str, Any]:
    identifier = model_id(identifier)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", revision):
        raise ValueError("Revision must be a simple branch/tag or full commit SHA")
    if snapshot is None:
        secret = credential({"env": token_env}) if token_env else ""
        api = fetch(
            f"https://huggingface.co/api/models/{identifier}/revision/{quote(revision)}?blobs=true",
            secret,
        )
        sha = api.get("sha", "")
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ValueError("Hub did not resolve an immutable revision")
        # raw endpoints return repository text without CDN redirects or weight downloads.
        config = fetch(f"https://huggingface.co/{identifier}/raw/{sha}/config.json", secret)
        tokenizer = fetch(
            f"https://huggingface.co/{identifier}/raw/{sha}/tokenizer_config.json", secret
        )
        snapshot = {"api": api, "config": config, "tokenizer": tokenizer}
    api, config, tokenizer = snapshot["api"], snapshot["config"], snapshot["tokenizer"]
    if api.get("gated") is not False and api.get("gated") not in ("auto", "manual"):
        raise ValueError("Repository access/gating metadata is unknown")
    sha = api.get("sha", "")
    if api.get("id") != identifier or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Model identity/revision mismatch")
    if re.fullmatch(r"[a-f0-9]{40}", revision) and revision != sha:
        raise ValueError("Resolved revision differs from requested immutable snapshot")
    selected = []
    for item in api.get("siblings", []):
        name = item["rfilename"]
        if name.startswith("/") or ".." in name.split("/") or any(c in name for c in "\n\r\x00"):
            raise ValueError("Unsafe model repository path")
        if not name.endswith((".safetensors", ".json", ".model", ".txt", ".tiktoken", ".jinja")):
            continue
        lfs = item.get("lfs") or {}
        size = lfs.get("size", item.get("size"))
        if type(size) is not int or size < 0:
            raise ValueError("Model file size missing; inspect blobs metadata before planning")
        digest = lfs.get("sha256", lfs.get("oid"))
        selected.append(
            {"path": name, "size": size, "sha256": digest, "git_oid": item.get("blobId")}
        )
    weight_bytes = sum(item["size"] for item in selected if item["path"].endswith(".safetensors"))
    if any(item["path"].endswith(".safetensors") and not item["sha256"] for item in selected):
        raise ValueError("Safetensors weights require Hub LFS SHA256 metadata")
    if any(not item["sha256"] and not item["git_oid"] for item in selected):
        raise ValueError("Every selected model file needs a pinned LFS or Git blob identity")
    license_name = (api.get("cardData") or {}).get("license")
    if not isinstance(license_name, str):
        license_name = None
    length = config.get("max_position_embeddings", config.get("n_positions"))
    if type(length) is not int or length <= 0:
        length = None
    template = tokenizer.get("chat_template")
    if not isinstance(template, str):
        template = None
    limitations = [
        "Downloadable/open weights do not establish an open-source license",
        "Architecture/runtime compatibility and KV-cache memory require site review",
        "File hashes authenticate bytes against the inspected Hub snapshot, not its author",
    ]
    if config.get("auto_map") or tokenizer.get("auto_map"):
        limitations.append("Repository declares custom code; trust_remote_code remains disabled")
    data = {
        "model_id": identifier,
        "requested_revision": revision,
        "revision": sha,
        "license": license_name,
        "gated": bool(api.get("gated", False)),
        "architecture": config.get("architectures", []),
        "context_length": length,
        "chat_template": template,
        "weight_format": "safetensors" if weight_bytes else "unknown",
        "weight_bytes": weight_bytes,
        "quantization": config.get("quantization_config"),
        "parameter_count": (api.get("safetensors") or {}).get("total"),
        "selected_disk_bytes": sum(item["size"] for item in selected),
        "files": selected,
        "source_hash": fingerprint(snapshot),
        "retrieved_at": now(),
        "runtime_compatibility": "UNVERIFIED",
        "limitations": limitations,
    }
    return record(
        "model_inspection", data, identity("model", identifier, sha, fingerprint(data)), version=2
    )
