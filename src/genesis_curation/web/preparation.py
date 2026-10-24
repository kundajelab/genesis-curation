"""Bounded study preparation and sample-scoped, targeted evidence selection."""

from __future__ import annotations

import copy
import re
import threading
from pathlib import Path
from typing import Any

from ..contracts.records import fingerprint
from ..curation import harmonize
from ..llm import providers
from ..llm.security import redact
from . import catalog, skills, studies


def select_evidence(
    state: dict[str, Any],
    entries: list[dict[str, str]],
    fields: list[str] | None = None,
    budget: int = 9000,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    terms = set(
        re.findall(
            r"[a-z]+",
            " ".join(
                fields
                or [
                    "organism species genotype cultivar tissue cell type stage treatment assay "
                    "protocol library replicate control transcription factor DNA reference assembly"
                ]
            ),
        )
    )
    scopes = state.get("source_scopes", {})
    ranked = []
    for index, entry in enumerate(entries):
        text = entry["text"]
        accessions = set(re.findall(r"\b(?:GSM|[SED]R[RSX])\d+\b", text))
        own = set(re.findall(r"\b(?:GSM|[SED]R[RSX])\d+\b", str(state["original"])))
        if accessions and own and not accessions.intersection(own):
            continue
        words = set(re.findall(r"[a-z]+", text.lower()))
        scope = scopes.get(entry["source_version"], "sample")
        score = len(terms & words) + (4 if scope == "sample" else 0)
        ranked.append((-score, index, {**entry, "scope": scope}))
    selected, size = [], 0
    for _, _, entry in sorted(ranked, key=lambda row: (row[0], row[1])):
        cost = len(entry["text"]) + 180
        if size + cost <= budget:
            selected.append(entry)
            size += cost
    if not selected:
        raise ValueError("No evidence fits the context budget; provide a smaller source excerpt")
    return selected, {
        "available_passages": len(entries),
        "selected_passages": len(selected),
        "omitted_passages": len(entries) - len(selected),
        "selection": "sample-first field-keyword ranking",
        "character_budget": budget,
    }


def restore_quote_whitespace(
    bundle: dict[str, Any], fields: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Restore unique source spans after whitespace-only model transcription changes.

    The raw invocation remains immutable. No words, punctuation, source identifiers,
    or locators are repaired, and the resulting quotes still pass exact validation.
    """
    restored, repairs = copy.deepcopy(fields), []
    sources = {s["version"]: s for s in bundle["data"]["sources"]}
    for field in restored:
        for citation in field["evidence"]:
            source = sources.get(citation["source_version"])
            if source is None:
                continue
            passage = harmonize.locate(source, citation["locator"])
            quote = citation["quote"]
            if not quote or quote in passage:
                continue
            pattern = "".join(
                r"\s+" if part.isspace() else re.escape(part) for part in re.split(r"(\s+)", quote)
            )
            matches = list(re.finditer(pattern, passage))
            if len(matches) == 1:
                citation["quote"] = matches[0].group()
                repairs.append(
                    {
                        "field": field["field"],
                        "source_version": citation["source_version"],
                        "locator": citation["locator"],
                        "model_quote": quote,
                        "source_quote": citation["quote"],
                        "method": "unique-whitespace-span",
                    }
                )
    return restored, repairs


def verify_scope(state: dict[str, Any], fields: list[dict[str, Any]]) -> None:
    own = set(re.findall(r"\b(?:GSM|[SED]R[RSX])\d+\b", str(state["original"])))
    for field in fields:
        for citation in field["evidence"]:
            quote = citation["quote"]
            cited = set(re.findall(r"\b(?:GSM|[SED]R[RSX])\d+\b", quote))
            if cited and own and not cited.intersection(own):
                raise ValueError("Citation describes a different sample")
            if state.get("source_scopes", {}).get(citation["source_version"]) == "study_context":
                if field["status"] == "known" and not (cited & own):
                    raise ValueError(
                        "Study context needs explicit sample attribution or human review"
                    )


def select_skill(folder: Path, state: dict[str, Any]) -> dict[str, Any]:
    context = " ".join(
        str(f["value"] or "") for f in state["fields"] if f["field"] in {"assay", "protocol"}
    ).lower()
    context += (
        " "
        + " ".join(
            s["data"]["snapshot"]
            for s in state["sources"]
            if state.get("source_scopes", {}).get(s["version"]) != "study_context"
        ).lower()
    )
    if any(
        term in context
        for term in (
            "scrna",
            "snrna",
            "single-cell rna",
            "single-nucleus rna",
            "single cell rna",
            "single nucleus rna",
        )
    ):
        return skills.select(folder, "plant-celltype-metadata")
    if "chip" in context or "dap" in context:
        return skills.select(folder, "plant-binding-metadata")
    return skills.select(folder, "plant-metadata")


def run(
    root: Path, config_path: Path, task: dict[str, Any], cancellation: threading.Event | None = None
) -> dict[str, Any]:
    data, registry = task["payload"], root / "catalog"
    key = data["study"]
    config = providers.select(config_path, data["provider"]) if data.get("provider") else None
    if config and fingerprint(config) != data["provider_hash"]:
        studies.preparation(registry, key, {"status": "FAILED"})
        raise ValueError("Provider configuration changed; submit preparation again")
    if data.get("skill_catalog_hash") and data["skill_catalog_hash"] != fingerprint(
        skills.available(root / "skills")
    ):
        studies.preparation(registry, key, {"status": "FAILED"})
        raise ValueError("Skills changed after submission; submit preparation again")
    if config and config.get("context_window_tokens"):
        from . import table

        return table.run(root, config, task, cancellation)
    studies.preparation(registry, key, {"status": "RUNNING"})
    study = studies.get(registry, key)
    completed = dict(study["preparation"]["completed"])
    failures = dict(study["preparation"]["failures"])
    iterations = dict(study["preparation"].get("investigations", {}))
    selected = data.get("targets")
    records = [s for s in study["records"] if not selected or s["id"] in selected]
    try:
        for state in records:
            if cancellation and cancellation.is_set():
                studies.preparation(registry, key, {"status": "INTERRUPTED"})
                return {"study": key, "cancelled": True}
            sid = state["id"]
            if sid in study["preparation"].get("retrieval_failures", {}):
                failures[sid] = study["preparation"]["retrieval_failures"][sid]
                continue
            expected = data.get("versions", {}).get(sid)
            if expected and expected != state["version"]:
                failures[sid] = "Record changed after submission; newer human work was preserved"
                continue
            field = data.get("field")
            if field:
                counter = (
                    sid + ":" + field + ":" + fingerprint([s["version"] for s in state["sources"]])
                )
                if iterations.get(counter, 0) >= 2:
                    failures[sid] = "Two investigation passes exhausted; human evidence is required"
                    continue
                iterations[counter] = iterations.get(counter, 0) + 1
            elif completed.get(sid) == state["version"]:
                continue
            try:
                if config:
                    skill = select_skill(root / "skills", state)
                    provenance = state.get("preparation_provenance", {})
                    reusable = (
                        not field
                        and not data.get("restart")
                        and provenance.get("workflow_version") == "study-2"
                        and provenance.get("provider_hash") == fingerprint(config)
                        and provenance.get("skill_hash") == skill["sha256"]
                        and provenance.get("source_hashes")
                        == [s["version"] for s in state["sources"]]
                    )
                    if not reusable:
                        state = catalog.run_proposal(
                            registry,
                            sid,
                            state["version"],
                            config,
                            skill,
                            task["actor"],
                            data.get("guidance", ""),
                            cancellation,
                            target_fields=[field] if field else None,
                            preserve_reviews=True,
                        )
                    if not field:
                        unresolved = [
                            f["field"]
                            for f in state["fields"]
                            if f["status"] in {"unknown", "conflicting"}
                            and f["evidence"]
                            and f["field"] not in state["reviews"]
                            and f["field"] not in state.get("recommendations", {})
                        ]
                        for name in unresolved:
                            counter = (
                                sid
                                + ":"
                                + name
                                + ":"
                                + fingerprint([s["version"] for s in state["sources"]])
                            )
                            if iterations.get(counter, 0) >= 1:
                                continue  # Reserve the second pass for a reviewer question.
                            iterations[counter] = iterations.get(counter, 0) + 1
                            state = catalog.run_proposal(
                                registry,
                                sid,
                                state["version"],
                                config,
                                skill,
                                task["actor"],
                                "Investigate the unresolved field using sample-specific evidence. "
                                "Abstain if the evidence does not resolve it.",
                                cancellation,
                                target_fields=[name],
                                preserve_reviews=True,
                            )
                completed[sid] = state["version"]
                failures.pop(sid, None)
            except Exception as exc:
                failures[sid] = redact(str(exc))[:1000]
            studies.preparation(
                registry,
                key,
                {
                    "completed": completed,
                    "failures": failures,
                    "investigations": iterations,
                    "mode": "ai" if config else "source-only",
                },
            )
        if cancellation and cancellation.is_set():
            studies.preparation(registry, key, {"status": "INTERRUPTED"})
            return {"study": key, "cancelled": True}
        studies.preparation(
            registry,
            key,
            {
                "status": "COMPLETED",
                "completed": completed,
                "failures": failures,
                "investigations": iterations,
                "mode": "ai" if config else "source-only",
            },
        )
        return {"study": key, "prepared": len(completed), "failed": len(failures)}
    except Exception:
        studies.preparation(registry, key, {"status": "FAILED"})
        raise
