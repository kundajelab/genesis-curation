"""Measured QC imports, explicit selection policies and reproducible cohort decisions."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import math
import sqlite3
import statistics
import threading
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..contracts.records import canonical, fingerprint, loads, now
from ..llm import progress
from ..registry import store
from . import assessments, catalog, studies

METRICS = {"q30_fraction", "rrna_fraction", "junction_fraction", "mapped_fraction"}
PROFILE = {
    "name": "Bulk poly(A) RNA-seq",
    "assay": "RNA-seq",
    "library_selection": "polyA",
    "thresholds": {},
    "outliers": {"enabled": True, "multiplier": 1.5, "minimum": 20},
}
QC_FIELDS = {
    "organism",
    "assay",
    "library_selection",
    "library_source",
    "platform",
    "strandedness",
    "protocol",
    "library_preparation",
    "reference_assembly",
    "biosample_accession",
    "experiment_accession",
    "accession",
    "cell_resolution",
}


def metadata(state: dict[str, Any]) -> dict[str, Any]:
    checks = assessments.assess(state, state["fields"])
    values = {
        f["field"]: f["value"]
        for f in state["fields"]
        if f["status"] == "known"
        and checks[f["field"]]["support"] == "quoted_value"
        and not checks[f["field"]]["needs_verification"]
    }
    for name, decision in state["reviews"].items():
        if decision["action"] == "approve":
            values[name] = decision["field"]["value"]
        else:
            values.pop(name, None)
    return {k: v for k, v in values.items() if k in QC_FIELDS}


def signature(state: dict[str, Any]) -> dict[str, Any]:
    values = metadata(state)
    fields = {f["field"]: f for f in state["fields"]}
    for name, decision in state["reviews"].items():
        if decision["action"] == "approve":
            fields[name] = decision["field"]
    return {"values": values, "evidence": {k: fields[k]["evidence"] for k in values}}


def invalidate(db: sqlite3.Connection, subject: str) -> None:
    db.execute(
        "UPDATE qc_analyses SET body=json_set(body,'$.stale',json('true')) WHERE id IN "
        "(SELECT analysis FROM qc_results WHERE subject=?)",
        (subject,),
    )


def profile(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(PROFILE):
        raise ValueError("Profile requires name, assay, library_selection, thresholds and outliers")
    result = loads(canonical(value))
    if not isinstance(result["name"], str) or not 1 <= len(result["name"]) <= 100:
        raise ValueError("Name the QC profile")
    if result["assay"] != "RNA-seq" or result["library_selection"] not in {"polyA", "any"}:
        raise ValueError("RNA-seq profiles support polyA or any library selection")
    limits = result["thresholds"]
    if not isinstance(limits, dict) or set(limits) - METRICS:
        raise ValueError("Unknown quality metric")
    for key, rule in limits.items():
        if not isinstance(rule, dict) or not rule or set(rule) - {"min", "max"}:
            raise ValueError("Threshold requires min and/or max")
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
            for v in rule.values()
        ):
            raise ValueError("Quality thresholds use fractions between 0 and 1")
        if rule.get("min", 0) > rule.get("max", 1):
            raise ValueError("Minimum exceeds maximum for " + key)
    out = result["outliers"]
    if not isinstance(out, dict) or set(out) != {"enabled", "multiplier", "minimum"}:
        raise ValueError("Outlier settings require enabled, multiplier and minimum")
    if type(out["enabled"]) is not bool or type(out["minimum"]) is not int or out["minimum"] < 20:
        raise ValueError("Outlier cohorts require at least 20 observations")
    if type(out["multiplier"]) not in (int, float) or not 1 <= out["multiplier"] <= 10:
        raise ValueError("IQR multiplier must be 1–10")
    return result | {"version": fingerprint(result)}


def import_measurements(root: Path, text: str, filename: str, actor: str) -> dict[str, Any]:
    if not filename.endswith((".csv", ".tsv")) or len(text.encode()) > 2 * 1024**2:
        raise ValueError("Upload a CSV/TSV of at most 2 MiB; split larger QC tables")
    rows = list(
        csv.DictReader(io.StringIO(text), delimiter="\t" if filename.endswith(".tsv") else ",")
    )
    if not rows or len(rows) > 15000:
        raise ValueError("QC import requires 1–15,000 measurements")
    required = {
        "subject",
        "metric",
        "value",
        "unit",
        "method",
        "reference",
        "source",
        "pipeline_version",
        "run_id",
    }
    digest = fingerprint([filename, text])
    with store.write(root / "catalog") as db:
        if db.execute("SELECT 1 FROM qc_imports WHERE id=?", (digest,)).fetchone():
            return {"id": digest, "measurements": len(rows), "reused": True}
        observations: list[dict[str, Any]] = []
        seen = set()
        for line, row in enumerate(rows, 2):
            if not required.issubset(row) or any(not str(row[k] or "").strip() for k in required):
                raise ValueError(
                    f"Line {line}: require subject, metric, value, unit, method, "
                    f"reference, source, pipeline_version and run_id"
                )
            sid = row["subject"].strip()
            catalog.read(db, sid)  # UUID identifiers avoid ambiguous accession joins.
            if row["metric"] not in METRICS or row["unit"] not in {"fraction", "percent"}:
                raise ValueError(f"Line {line}: unsupported metric or units")
            key = (sid, row["metric"])
            if key in seen:
                raise ValueError("Duplicate subject/metric in import")
            seen.add(key)
            value = float(row["value"])
            value = value / 100 if row["unit"] == "percent" else value
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"Line {line}: fraction is outside 0–1")
            observations.append(
                {
                    **row,
                    "subject": sid,
                    "normalized_value": value,
                    "normalized_unit": "fraction",
                    "import": digest,
                    "line": line,
                }
            )
        db.execute(
            "INSERT INTO qc_imports VALUES(?,?)",
            (
                digest,
                canonical(
                    {
                        "filename": filename,
                        "text": text,
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "actor": actor,
                        "created": now(),
                    }
                ),
            ),
        )
        for observation in observations:
            db.execute(
                "INSERT INTO qc_observations VALUES(?,?,?) ON CONFLICT(subject,metric) "
                "DO UPDATE SET body=excluded.body",
                (observation["subject"], observation["metric"], canonical(observation)),
            )
            invalidate(db, observation["subject"])
    return {"id": digest, "measurements": len(rows), "reused": False}


def library(values: dict[str, Any], policy: dict[str, Any]) -> dict[str, str]:
    assay = str(values.get("assay", "")).casefold()
    if not assay:
        return {"status": "not_evaluated", "reason": "Assay is unknown"}
    if assay not in {"rna-seq", "rna_seq", "rna seq"}:
        return {"status": "excluded", "reason": "Not established as bulk RNA-seq"}
    source = str(values.get("library_source", "")).strip().casefold()
    if source in {"genomic", "genomic dna", "metagenomic"}:
        return {"status": "excluded", "reason": "Explicit genomic source conflicts with RNA cohort"}
    resolution = str(values.get("cell_resolution", "")).casefold()
    if resolution in {"single-cell", "single-nucleus"}:
        return {
            "status": "excluded",
            "reason": "Single-cell/nucleus library outside the bulk cohort",
        }
    if resolution != "bulk":
        return {"status": "not_evaluated", "reason": "Bulk protocol requires explicit evidence"}
    if policy["library_selection"] == "any":
        return {"status": "pass", "reason": "RNA-seq; no selection restriction"}
    selection = str(values.get("library_selection", "")).strip().casefold()
    if selection in {
        "polya",
        "poly(a)",
        "poly-a",
        "polya selection",
        "oligo-dt",
        "cdna_oligo_dt",
        "cdna_oligo_dt selection",
    }:
        return {"status": "pass", "reason": "Explicit poly(A)/oligo-dT selection"}
    if selection in {
        "random",
        "rrna depletion",
        "ribo-depletion",
        "total rna",
        "random hexamer",
        "size fractionation",
    }:
        return {"status": "excluded", "reason": "Selection is outside this poly(A) cohort"}
    return {
        "status": "not_evaluated",
        "reason": "Selection unresolved; RNA-seq alone does not imply poly(A)",
    }


def start(root: Path, value: object, actor: str) -> str:
    policy = profile(value)
    key = str(uuid.uuid4())
    with store.write(root / "catalog") as db:
        db.execute(
            "INSERT OR IGNORE INTO qc_profiles VALUES(?,?)", (policy["version"], canonical(policy))
        )
        db.execute(
            "INSERT INTO qc_analyses VALUES(?,?)",
            (
                key,
                canonical(
                    {
                        "id": key,
                        "profile": policy,
                        "actor": actor,
                        "created": now(),
                        "status": "QUEUED",
                        "stale": False,
                    }
                ),
            ),
        )
    return key


def read(db: sqlite3.Connection, key: str) -> dict[str, Any]:
    row = db.execute("SELECT body FROM qc_analyses WHERE id=?", (key,)).fetchone()
    if not row:
        raise ValueError("Unknown QC evaluation")
    return loads(row[0])


def fail(root: Path, key: str, reason: str) -> None:
    with store.write(root / "catalog") as db:
        value = read(db, key)
        if value["status"] != "COMPLETED":
            value.update(status="FAILED", error=reason[:1000])
            db.execute("UPDATE qc_analyses SET body=? WHERE id=?", (canonical(value), key))


def snapshot(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        s["id"]: s
        for (body,) in db.execute(
            "SELECT s.body FROM curation_subjects s JOIN curation_result_rows p ON p.subject=s.id "
            "WHERE p.modality='RNA-seq' OR json_extract(s.body,'$.original._modality')='RNA-seq'"
        )
        for s in [loads(body)]
    }


def analyse(root: Path, key: str, cancel: threading.Event | None = None) -> dict[str, Any]:
    registry = root / "catalog"
    progress.report(phase="Loading RNA metadata and measured QC")
    with contextlib.closing(store.connect(registry)) as db:
        analysis = read(db, key)
        if analysis["status"] == "COMPLETED":
            return analysis
        states = snapshot(db)
        measurements: dict[str, dict[str, Any]] = defaultdict(dict)
        for sid, metric, body in db.execute("SELECT * FROM qc_observations"):
            if sid in states:
                measurements[sid][metric] = loads(body)
        membership = dict(
            db.execute("SELECT subject,min(study) FROM curation_members GROUP BY subject")
        )
    policy = analysis["profile"]
    signatures = {sid: signature(s) for sid, s in states.items()}
    inputs = {sid: v["values"] for sid, v in signatures.items()}
    observations_hash = fingerprint(measurements)
    cohorts: dict[str, list[tuple[str, float]]] = defaultdict(list)
    bindings = {}
    for index, (sid, values) in enumerate(inputs.items()):
        if index % 100 == 0:
            progress.report(
                phase="Building comparable QC cohorts", completed=index, total=len(inputs)
            )
        if library(values, policy)["status"] != "pass":
            continue
        if any(
            metric not in measurements[sid]
            or not rule.get("min", 0)
            <= measurements[sid][metric]["normalized_value"]
            <= rule.get("max", 1)
            for metric, rule in policy["thresholds"].items()
        ):
            continue
        for metric, obs in measurements[sid].items():
            if (
                not values.get("organism")
                or not values.get("library_selection")
                or not (values.get("library_preparation") or values.get("protocol"))
            ):
                continue
            group = fingerprint(
                [
                    values.get("organism"),
                    values.get("assay"),
                    values.get("library_selection"),
                    values.get("library_preparation") or values.get("protocol"),
                    values.get("library_source"),
                    values.get("platform"),
                    values.get("strandedness"),
                    obs["reference"],
                    obs["method"],
                    obs["pipeline_version"],
                    metric,
                    obs["normalized_unit"],
                ]
            )
            cohorts[group].append((sid, obs["normalized_value"]))
            bindings[sid, metric] = group
    bounds = {}
    for group, members in cohorts.items():
        vals = [v for _, v in members]
        if len(vals) < policy["outliers"]["minimum"]:
            bounds[group] = {
                "status": "not_evaluated",
                "n": len(vals),
                "reason": "Insufficient comparable observations",
            }
            continue
        q1, _, q3 = statistics.quantiles(vals, n=4, method="inclusive")
        spread = q3 - q1
        bounds[group] = {
            "status": "evaluated" if spread else "not_evaluated",
            "n": len(vals),
            "q1": q1,
            "q3": q3,
            "iqr": spread,
            "lower": q1 - policy["outliers"]["multiplier"] * spread,
            "upper": q3 + policy["outliers"]["multiplier"] * spread,
            "reason": "IQR fences" if spread else "Zero IQR; no outlier judgment",
        }
    results: list[dict[str, Any]] = []
    for sid, values in inputs.items():
        if cancel and cancel.is_set():
            raise ValueError("QC evaluation cancelled; no partial results committed")
        eligible = library(values, policy)
        checks = []
        for metric, rule in policy["thresholds"].items():
            obs = measurements[sid].get(metric)
            value = obs["normalized_value"] if obs else None
            checks.append(
                {
                    "metric": metric,
                    "value": value,
                    "rule": rule,
                    "status": "not_evaluated"
                    if value is None
                    else "pass"
                    if rule.get("min", 0) <= value <= rule.get("max", 1)
                    else "fail",
                }
            )
        quality = (
            "not_evaluated"
            if not checks or any(c["status"] == "not_evaluated" for c in checks)
            else "pass"
        )
        if any(c["status"] == "fail" for c in checks):
            quality = "fail"
        outliers: list[dict[str, Any]] = []
        if policy["outliers"]["enabled"]:
            for metric, obs in measurements[sid].items():
                group = bindings.get((sid, metric))
                fence = bounds.get(
                    group, {"status": "not_evaluated", "reason": "No comparable cohort"}
                )
                flag = (
                    fence["status"] == "evaluated"
                    and not fence["lower"] <= obs["normalized_value"] <= fence["upper"]
                )
                outliers.append(
                    {
                        "metric": metric,
                        "value": obs["normalized_value"],
                        "cohort": group,
                        "fence": fence,
                        "flag": flag,
                    }
                )
        statistical = "flagged" if any(o["flag"] for o in outliers) else "not_evaluated"
        if (
            outliers
            and all(o["fence"]["status"] == "evaluated" for o in outliers)
            and statistical != "flagged"
        ):
            statistical = "pass"
        status = (
            "excluded"
            if eligible["status"] == "excluded" or quality == "fail"
            else "needs_review"
            if statistical == "flagged"
            else "pass"
            if eligible["status"] == quality == "pass"
            and (not policy["outliers"]["enabled"] or statistical == "pass")
            else "not_evaluated"
        )
        result = {
            "subject": sid,
            "name": states[sid]["name"],
            "study": membership.get(sid, ""),
            "species": str(values.get("organism", "Unknown")),
            "metadata": values,
            "metadata_evidence_hash": fingerprint(signatures[sid]),
            "observations": measurements[sid],
            "library": eligible,
            "quality": quality,
            "checks": checks,
            "statistical": statistical,
            "outliers": outliers,
            "status": status,
            "profile_version": policy["version"],
        }
        result["version"] = fingerprint(result)
        results.append(result)
    # One transaction commits a consistent, immutable cohort snapshot.
    with store.write(registry) as db:
        actual = {sid: signature(state) for sid, state in snapshot(db).items()}
        obs_now: dict[str, dict[str, Any]] = defaultdict(dict)
        for sid, metric, body in db.execute("SELECT * FROM qc_observations"):
            if sid in inputs:
                obs_now[sid][metric] = loads(body)
        # Ignore absent empty dictionaries introduced by defaultdict access above.
        membership_now = dict(
            db.execute("SELECT subject,min(study) FROM curation_members GROUP BY subject")
        )
        if (
            membership != membership_now
            or signatures != actual
            or fingerprint({s: v for s, v in measurements.items() if v})
            != fingerprint(dict(obs_now))
        ):
            raise ValueError("Metadata or measurements changed during QC; run again")
        db.executemany(
            "INSERT INTO qc_results VALUES(?,?,?,?,?,?)",
            [
                (key, r["subject"], r["study"], r["species"], r["status"], canonical(r))
                for r in results
            ],
        )
        species_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for result in results:
            species_counts[result["species"]][result["status"]] += 1
        analysis.update(
            status="COMPLETED",
            finished=now(),
            counts=dict(Counter(r["status"] for r in results)),
            species={s: dict(c) for s, c in species_counts.items()},
            denominator="RNA-seq candidate records; not unique biological samples",
            identities={
                field: len({str(v[field]) for v in inputs.values() if v.get(field)})
                for field in ("biosample_accession", "experiment_accession", "accession")
            },
            total=len(results),
            observation_hash=observations_hash,
            cohorts=bounds,
            funnel={
                "records": len(results),
                "library_pass": sum(r["library"]["status"] == "pass" for r in results),
                "quality_pass": sum(
                    r["library"]["status"] == r["quality"] == "pass" for r in results
                ),
                "unflagged": sum(r["status"] == "pass" for r in results),
            },
        )
        db.execute("UPDATE qc_analyses SET body=? WHERE id=?", (canonical(analysis), key))
    progress.report(phase="QC evaluation complete", completed=len(inputs), total=len(inputs))
    return analysis


def listing(root: Path) -> list[dict[str, Any]]:
    with contextlib.closing(store.connect(root / "catalog")) as db:
        return [
            {k: v for k, v in loads(r[0]).items() if k != "cohorts"}
            for r in db.execute("SELECT body FROM qc_analyses ORDER BY rowid DESC LIMIT 20")
        ]


def page(
    root: Path, key: str, study: str = "", status: str = "", cursor: str = "", limit: int = 50
) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        raise ValueError("QC page limit must be 1–100")
    where, args = ["analysis=?"], [key]
    for name, value in (("study", study), ("status", status)):
        if value:
            where.append(name + "=?")
            args.append(value)
    with contextlib.closing(store.connect(root / "catalog")) as db:
        read(db, key)
        total = db.execute(
            "SELECT count(*) FROM qc_results WHERE " + " AND ".join(where), args
        ).fetchone()[0]
        rows = [
            loads(r[0])
            for r in db.execute(
                "SELECT body FROM qc_results WHERE "
                + " AND ".join(where)
                + " AND subject>? ORDER BY subject LIMIT ?",
                (*args, cursor, limit + 1),
            )
        ]
        for row in rows:
            decision = db.execute(
                "SELECT body FROM qc_decisions WHERE analysis=? AND subject=? ORDER BY "
                "sequence DESC LIMIT 1",
                (key, row["subject"]),
            ).fetchone()
            row["decision"] = loads(decision[0]) if decision else None
            row["review_version"] = fingerprint([row["version"], row["decision"]])
    return {
        "items": rows[:limit],
        "total": total,
        "next_cursor": rows[limit - 1]["subject"] if len(rows) > limit else None,
    }


def decide(root: Path, key: str, body: dict[str, Any], actor: str) -> dict[str, int]:
    targets, action, reason = body.get("targets"), body.get("action"), body.get("reason", "")
    if (
        not isinstance(targets, dict)
        or not 1 <= len(targets) <= 100
        or action not in {"retain", "exclude", "defer"}
        or not isinstance(reason, str)
        or not 1 <= len(reason.strip()) <= 2000
    ):
        raise ValueError("Choose 1–100 explicit targets, retain/exclude/defer, and a reason")
    with store.write(root / "catalog") as db:
        analysis = read(db, key)
        if analysis["stale"] or analysis["status"] != "COMPLETED":
            raise ValueError("QC inputs changed or analysis incomplete; evaluate again")
        for sid, version in targets.items():
            record = db.execute(
                "SELECT body FROM qc_results WHERE analysis=? AND subject=?", (key, sid)
            ).fetchone()
            previous = db.execute(
                "SELECT body FROM qc_decisions WHERE analysis=? AND subject=? "
                "ORDER BY sequence DESC LIMIT 1",
                (key, sid),
            ).fetchone()
            previous = loads(previous[0]) if previous else None
            if not record or fingerprint([loads(record[0])["version"], previous]) != version:
                raise ValueError("Stale or unknown QC target")
            if action == "retain" and (
                loads(record[0])["status"] != "needs_review"
                or loads(record[0])["library"]["status"] != "pass"
                or loads(record[0])["quality"] != "pass"
            ):
                raise ValueError(
                    "Retention can resolve an outlier flag, not waive missing or failed "
                    "quality/library checks"
                )
        for sid, version in targets.items():
            db.execute(
                "INSERT INTO qc_decisions(analysis,subject,body) VALUES(?,?,?)",
                (
                    key,
                    sid,
                    canonical(
                        {
                            "version": version,
                            "action": action,
                            "reason": reason.strip(),
                            "actor": actor,
                            "created": now(),
                        }
                    ),
                ),
            )
    return {"decisions": len(targets)}


def export(root: Path, key: str) -> dict[str, Any]:
    with contextlib.closing(store.connect(root / "catalog")) as db:
        db.execute("BEGIN")
        analysis = read(db, key)
        if analysis["stale"] or analysis["status"] != "COMPLETED":
            raise ValueError("QC cohort is stale or incomplete")
        signed = {}
        releases = {}
        for (study_id,) in db.execute(
            "SELECT DISTINCT study FROM qc_results WHERE analysis=? AND study!=''", (key,)
        ):
            study = studies.view(db, study_id)
            if study["approval_current"]:
                release = loads(
                    db.execute(
                        "SELECT body FROM curation_releases WHERE version=?", (study["approval"],)
                    ).fetchone()[0]
                )
                signed.update({r["id"]: r["metadata"] for r in release["records"]})
                releases[study_id] = study["approval"]
        retained, excluded = [], []
        for (raw,) in db.execute(
            "SELECT body FROM qc_results WHERE analysis=? ORDER BY subject", (key,)
        ):
            row = loads(raw)
            previous = db.execute(
                "SELECT body FROM qc_decisions WHERE analysis=? AND subject=? ORDER BY "
                "sequence DESC LIMIT 1",
                (key, row["subject"]),
            ).fetchone()
            decision = loads(previous[0]) if previous else None
            history = [
                loads(r[0])
                for r in db.execute(
                    "SELECT body FROM qc_decisions WHERE analysis=? AND subject=? ORDER "
                    "BY sequence",
                    (key, row["subject"]),
                )
            ]
            row["decision_history"] = history
            include = row["status"] == "pass" or (
                row["status"] == "needs_review" and decision and decision["action"] == "retain"
            )
            include = (
                include
                and row["subject"] in signed
                and not (decision and decision["action"] in {"exclude", "defer"})
            )
            (retained if include else excluded).append(
                {
                    **row,
                    "decision": decision,
                    "signed_metadata": signed.get(row["subject"]),
                    "metadata_signed": row["subject"] in signed,
                }
            )
        return {
            "kind": "qc-filtered-cohort",
            "analysis": analysis,
            "study_releases": releases,
            "retained": retained,
            "excluded": excluded,
            "created": now(),
            "measurement_source": "imported; no preprocessing executed",
            "measurement_imports": {
                r[0]: loads(r[1])
                for r in db.execute(
                    "SELECT id,body FROM qc_imports WHERE id IN (SELECT DISTINCT "
                    "json_extract(value,'$.import') FROM "
                    "qc_results,json_each(qc_results.body,'$.observations') "
                    "WHERE analysis=?)",
                    (key,),
                )
            },
        }
