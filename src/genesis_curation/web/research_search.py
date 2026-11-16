"Bounded repository and metasearch adapters; results are leads, not evidence of eligibility."

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..contracts.records import dump, fingerprint, load, now
from . import intake
from .discovery_catalog import ASSAYS, KNOWN_REPOSITORIES


def search(
    root: Path, source: str, query: str, cancel: threading.Event | None = None, offset: int = 0
) -> dict[str, Any]:
    if not query.strip() or len(query) > 4000 or offset < 0:
        raise ValueError("Invalid search query")
    if source == "web":
        health_path = root / "search-health.json"
        health = load(health_path) if health_path.exists() else {}
        available = [
            engine for engine in ("google", "brave") if health.get(engine, 0) <= time.time()
        ]
        if not available:
            return {
                "source": source,
                "query": query,
                "rows": [],
                "errors": [["web", "Engines cooling down; archive searches continue"]],
                "coverage": "unavailable",
                "next_offset": None,
                "retrieved_at": now(),
                "cached": False,
            }
        url = "http://127.0.0.1:18888/search?" + urlencode(
            {
                "q": query,
                "format": "json",
                "pageno": offset // 20 + 1,
                "engines": ",".join(available),
            }
        )
        cache = root / "search-cache" / (fingerprint(url) + ".json")
        if cache.exists():
            from datetime import UTC, datetime

            old = load(cache)
            if (
                datetime.now(UTC) - datetime.fromisoformat(old["retrieved_at"])
            ).total_seconds() < 86400:
                if not old.get("errors"):
                    return old | {"cached": True}
        if cancel and cancel.is_set():
            raise ValueError("Search cancelled")
        with urllib.request.urlopen(url, timeout=20) as response:
            raw = response.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise ValueError("Search response exceeded budget")
        data = json.loads(raw)
        for failure in data.get("unresponsive_engines", []):
            if isinstance(failure, list) and failure and failure[0] in available:
                health[failure[0]] = time.time() + 900
        dump(health_path, health)
        rows = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "summary": r.get("content", ""),
                "engines": r.get("engines", []),
            }
            for r in data.get("results", [])[:20]
        ]
        result = {
            "source": source,
            "query": query,
            "rows": rows,
            "retrieved_at": now(),
            "cached": False,
            "errors": data.get("unresponsive_engines", []),
            "coverage": "partial" if data.get("unresponsive_engines") else "returned",
            "next_offset": offset + 20 if len(rows) == 20 else None,
            "response_hash": fingerprint(data),
        }
        cache.parent.mkdir(parents=True, exist_ok=True)
        dump(cache, result)
        intake.retain(
            root, {"url": url, "text": raw.decode(), "retrieved_at": result["retrieved_at"]}
        )
        return result
    if source == "papers":
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urlencode(
            {"query": query, "format": "json", "resultType": "core", "pageSize": 25}
        )
        raw = intake.fetch(root, url, cancel)
        data = json.loads(raw["text"])
        rows = [
            {
                "title": r.get("title", ""),
                "url": "https://europepmc.org/article/" + r.get("source", "MED") + "/" + r["id"],
                "summary": r.get("abstractText", ""),
                "doi": r.get("doi", ""),
                "pmid": r.get("pmid", ""),
                "pmcid": r.get("pmcid", ""),
            }
            for r in data.get("resultList", {}).get("result", [])
        ]
    elif source in {"sra", "assembly", "geo", "bioproject", "biosample"}:
        db = {"geo": "gds", "assembly": "assembly"}.get(source, source)
        base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
        raw = intake.fetch(
            root,
            base
            + "esearch.fcgi?"
            + urlencode(
                {"db": db, "term": query, "retmode": "json", "retmax": 25, "retstart": offset}
            ),
            cancel,
        )
        data = json.loads(raw["text"])["esearchresult"]
        ids = data.get("idlist", [])
        rows = []
        if ids:
            detail = intake.fetch(
                root,
                base
                + "esummary.fcgi?"
                + urlencode({"db": db, "id": ",".join(ids), "retmode": "json"}),
                cancel,
            )
            summaries = json.loads(detail["text"])["result"]
            for key in ids:
                item = summaries.get(key, {})
                accession = item.get("accession") or item.get("assemblyaccession") or key
                title = item.get("title") or item.get("assemblyname") or accession
                rows.append(
                    {
                        "title": str(title),
                        "url": "https://www.ncbi.nlm.nih.gov/" + db + "/" + str(accession),
                        "summary": json.dumps(item),
                        "accession": accession,
                    }
                )
        data = {"count": data.get("count", 0), "rows": rows}
    elif source == "gsa":
        url = "https://ngdc.cncb.ac.cn/search/api/specific?" + urlencode(
            {"q": query, "db": "gsa", "size": 20, "start": offset}
        )
        raw = intake.fetch(root, url, cancel)
        data = json.loads(raw["text"])
        container = data.get("result", {}).get("data", {})
        records = container.get("data", [])
        if not isinstance(records, list):
            raise ValueError("GSA search response is unavailable")
        rows = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "summary": json.dumps(r.get("attrs", {})),
                "accession": r.get("id", ""),
            }
            for r in records
        ]
    else:
        raise ValueError("Unsupported search source")
    return {
        "source": source,
        "query": query,
        "rows": rows,
        "url": raw["url"],
        "retrieved_at": raw["retrieved_at"],
        "response_hash": fingerprint(raw["text"]),
        "next_offset": offset + 25 if len(rows) == 25 and source not in {"papers", "gsa"} else None,
    }


def recipes(
    species: list[str],
    assays: list[str],
    goal: str,
    constraints: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    queries = []
    for organism in species:
        for assay in assays:
            recipe = ASSAYS[assay]
            terms = " OR ".join('"' + t + '"' for t in recipe["terms"])
            base = f'"{organism}" ({terms})'
            for source in (
                ["assembly", "web"]
                if assay == "Genomes"
                else ["geo", "ena", "sra", "papers", "gsa", "web"]
            ):
                queries.append(
                    {
                        "source": source,
                        "query": base,
                        "species": organism,
                        "assay": assay,
                        "goal": goal,
                        "offset": 0,
                    }
                )
            domains = " OR ".join("site:" + r["domain"] for r in KNOWN_REPOSITORIES)
            queries.append(
                {
                    "source": "web",
                    "query": base + " (" + domains + ")",
                    "species": organism,
                    "assay": assay,
                    "goal": goal,
                    "offset": 0,
                }
            )
    # Keep broad archive coverage; explicit requirements add focused searches.
    terms = list((constraints or {}).values())
    if terms:
        queries.extend(
            [
                {
                    **query,
                    "terms": terms,
                    "query": query["query"]
                    + " "
                    + " ".join('"' + term.replace('"', " ") + '"' for term in terms),
                }
                for query in list(queries)
            ]
        )
    return queries


def accession(text: str) -> str:
    return next(iter(re.findall(r"\b(?:GSE|[SED]RP|PRJ[EDNC][AB]|CRA)\d+\b", text)), "")
