"""ENA metadata for sheet building: run searches and sample attributes, cached on disk.

Every request is cached as JSON keyed by its URL and form data, so a rebuild reads the same
snapshot; delete the cache folder to refresh. Sample attributes are also kept per accession
(`samples.jsonl`), so a larger search only fetches the new samples. Requests retry with
backoff on 429, 5xx and network errors; at most `workers` (8) run at once, since ENA
throttles clients above about 16.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..web.mining_sources import ENA as PORTAL

BROWSER_XML = "https://www.ebi.ac.uk/ena/browser/api/xml/"
SAMPLE_BATCH = 80
RUN_FIELDS = (
    "run_accession",
    "study_accession",
    "secondary_study_accession",
    "sample_accession",
    "experiment_accession",
    "tax_id",
    "scientific_name",
    "library_strategy",
    "library_source",
    "library_selection",
    "library_layout",
    "instrument_platform",
    "instrument_model",
    "read_count",
    "base_count",
    "study_title",
    "experiment_title",
    "sample_title",
    "sample_description",
    "sample_alias",
    "tissue_type",
    "tissue_lib",
    "dev_stage",
    "ecotype",
    "cultivar",
    "strain",
    "variety",
    "isolate",
    "cell_type",
    "host_genotype",
    "experimental_factor",
    "library_construction_protocol",
    "fastq_ftp",
    "fastq_bytes",
    "fastq_md5",
)

Run = dict[str, str]


@dataclass(frozen=True)
class Sample:
    accession: str
    title: str = ""
    description: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    missing: bool = False

    def to_json(self) -> dict[str, object]:
        return {
            "accession": self.accession,
            "title": self.title,
            "description": self.description,
            "attributes": self.attributes,
            "missing": self.missing,
        }

    @classmethod
    def from_json(cls, value: dict[str, object]) -> Sample:
        attributes = value.get("attributes")
        return cls(
            accession=str(value["accession"]),
            title=str(value.get("title") or ""),
            description=str(value.get("description") or ""),
            attributes={str(k): str(v) for k, v in attributes.items()}
            if isinstance(attributes, dict)
            else {},
            missing=bool(value.get("missing")),
        )


def parse_samples(xml: str) -> list[Sample]:
    """SAMPLE records of an ENA browser XML document; attribute tags are lower-cased and the
    first value of a repeated tag wins."""
    samples = []
    for node in ET.fromstring(xml).findall("SAMPLE"):
        attributes: dict[str, str] = {}
        for item in node.findall(".//SAMPLE_ATTRIBUTE"):
            tag = (item.findtext("TAG") or "").strip().lower()
            attributes.setdefault(tag, (item.findtext("VALUE") or "").strip())
        samples.append(
            Sample(
                accession=node.get("accession") or "",
                title=(node.findtext("TITLE") or "").strip(),
                description=(node.findtext("DESCRIPTION") or "").strip(),
                attributes=attributes,
            )
        )
    return samples


def parse_tsv(text: str) -> list[Run]:
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    return [{k: (v or "").strip() for k, v in row.items() if k is not None} for row in reader]


Fetcher = Callable[[str, bytes | None], str]


def http_fetch(url: str, data: bytes | None) -> str:
    request = Request(url, data=data, headers={"User-Agent": "genesis-curation"})
    with urlopen(request, timeout=600) as response:
        return response.read().decode()


class Archive:
    """Cached ENA access. `fetcher` is replaceable for offline tests; `offline` refuses any
    request that is not already cached."""

    def __init__(
        self,
        cache: Path,
        workers: int = 8,
        attempts: int = 6,
        fetcher: Fetcher = http_fetch,
        offline: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache = cache
        self.workers = min(workers, 8)
        self.attempts = attempts
        self.fetcher = fetcher
        self.offline = offline
        self.sleep = sleep
        self.requests = 0
        self.lock = threading.Lock()

    def request(self, url: str, form: dict[str, str] | None = None, keep: bool = True) -> str:
        """The response text, from the cache when present; `keep=False` skips caching (for
        sample XML, which `samples` keeps per accession instead)."""
        key = hashlib.sha256(json.dumps([url, form], sort_keys=True).encode()).hexdigest()
        path = self.cache / "requests" / f"{key[:2]}" / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())["text"]
        if self.offline and keep:
            raise ValueError(f"Not cached and offline: {url} {form or ''}".strip())
        data = urlencode(form).encode() if form else None
        for attempt in range(self.attempts):
            try:
                text = self.fetcher(url, data)
                break
            except (HTTPError, URLError, TimeoutError, ConnectionError) as error:
                code = error.code if isinstance(error, HTTPError) else None
                if code is not None and code < 500 and code != 429:
                    raise ValueError(f"ENA request failed: HTTP {code} for {url}") from error
                if attempt == self.attempts - 1:
                    raise ValueError(f"ENA request failed after retries: {error}") from error
                retry = error.headers.get("Retry-After", "") if isinstance(error, HTTPError) else ""
                self.sleep(min(120.0, float(retry) if retry.isdigit() else 2.0 ** (attempt + 1)))
        else:  # pragma: no cover - the loop always breaks or raises
            raise AssertionError
        with self.lock:
            self.requests += 1
        if not keep:
            return text
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_text(json.dumps({"url": url, "form": form, "text": text}))
        part.replace(path)
        return text

    def search_runs(
        self, taxon: int, strategy: str, fields: Iterable[str] = RUN_FIELDS
    ) -> list[Run]:
        """Every public run of the taxon (not its sub-taxa) with this library strategy."""
        form = {
            "result": "read_run",
            "query": f'tax_eq({taxon}) AND library_strategy="{strategy}"',
            "fields": ",".join(fields),
            "format": "tsv",
            "limit": "0",
        }
        return parse_tsv(self.request(PORTAL + "search", form))

    def samples(self, accessions: Iterable[str]) -> dict[str, Sample]:
        """Sample titles and attributes, from `samples.jsonl` or ENA in batches of 80.
        Accessions ENA does not return are recorded as missing and not asked again."""
        store = self.cache / "samples.jsonl"
        known: dict[str, Sample] = {}
        if store.exists():
            for line in store.read_text().splitlines():
                if line.strip():
                    sample = Sample.from_json(json.loads(line))
                    known[sample.accession] = sample
        wanted = sorted({a for a in accessions if a})
        todo = [a for a in wanted if a not in known]
        if todo:
            if self.offline:
                raise ValueError(f"{len(todo)} samples not cached and offline, e.g. {todo[0]}")
            batches = [todo[i : i + SAMPLE_BATCH] for i in range(0, len(todo), SAMPLE_BATCH)]
            store.parent.mkdir(parents=True, exist_ok=True)
            with store.open("a") as handle, ThreadPoolExecutor(self.workers) as pool:
                for batch, found in zip(batches, pool.map(self.sample_batch, batches), strict=True):
                    for accession in batch:
                        sample = found.get(accession, Sample(accession, missing=True))
                        known[accession] = sample
                        handle.write(json.dumps(sample.to_json()) + "\n")
        return {a: known[a] for a in wanted}

    def sample_batch(self, batch: list[str]) -> dict[str, Sample]:
        xml = self.request(BROWSER_XML + ",".join(batch), keep=False)
        return {sample.accession: sample for sample in parse_samples(xml)}
