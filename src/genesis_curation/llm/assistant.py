"""Bounded read-only assistant over explicitly selected catalog evidence."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from ..contracts.records import fingerprint
from ..registry import store
from .providers import invoke
from .security import schema


def tool(directory: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """No raw SQL, filesystem path, subprocess or write capability is exposed."""
    with contextlib.closing(store.connect(directory)) as db:
        if name == "search" and set(arguments) <= {"text", "limit"}:
            limit = arguments.get("limit", 10)
            if type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError("Assistant search limit must be 1–20")
            return {"results": store.search(db, text=arguments.get("text"), limit=limit)}
        if name in {"show", "history", "evidence"} and set(arguments) == {"dataset"}:
            row = store.current(db, arguments["dataset"])
            if name == "show":
                return row
            if name == "history":
                return {"results": store.history(db, arguments["dataset"], limit=20, after=0)}
            return store.get(db, "manifest", row["manifest"])
    raise ValueError("Unsupported read-only tool or arguments")


def ask(
    directory: Path, config: dict[str, Any], question: str, datasets: list[str], classification: str
) -> dict[str, Any]:
    if not 1 <= len(datasets) <= 10 or not 1 <= len(question) <= 8000:
        raise ValueError("Select 1–10 datasets and a question of at most 8000 characters")
    evidence = [tool(directory, "show", {"dataset": dataset}) for dataset in datasets]
    with contextlib.closing(store.connect(directory)) as db:
        for dataset in datasets:
            actual = store.classification(db, dataset)
            if (
                classification not in store.CLASSIFICATIONS
                or store.CLASSIFICATIONS[actual] > store.CLASSIFICATIONS[classification]
            ):
                raise ValueError(
                    "Question classification would downgrade stored source restrictions"
                )
    hashes = [fingerprint(value) for value in evidence]
    result = invoke(
        directory,
        config,
        task="ask",
        data={"question": question, "catalog": list(zip(hashes, evidence, strict=True))},
        classification=classification,
        source_hashes=hashes,
        specification=schema("answer-v1"),
        mock={
            "answer": "Mock catalog response; inspect the selected records for "
            "scientific interpretation",
            "citations": hashes,
        },
    )
    if not set(result["data"]["response"]["citations"]).issubset(hashes):
        raise ValueError("Assistant returned an unsupported evidence citation")
    return result
