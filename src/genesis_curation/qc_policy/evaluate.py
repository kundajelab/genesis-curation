"""Apply finite declarative rules; never execute policy code or change measurements."""

from __future__ import annotations

from typing import Any

from ..contracts.records import (
    Finding,
    QCAssessment,
    SourceEvidence,
    Subject,
    fingerprint,
    validate,
)
from . import builtin


def diagnostic_policy() -> dict[str, Any]:
    return builtin("plant-diagnostic-v1")


def evaluate(
    manifest: dict[str, Any],
    validation: dict[str, Any],
    policy: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    validate(manifest, "manifest")
    validate(validation, "validation")
    validate(policy, "policy")
    if validation["data"]["manifest_version"] != manifest["version"]:
        raise ValueError("Validation and manifest revisions differ")
    data, config = manifest["data"], policy["data"]
    compatible = all(
        not config[key] or actual in config[key]
        for key, actual in (
            ("assays", data["assay"]),
            ("species", data["species"]),
            ("references", data["reference"]["id"]),
            ("protocols", metadata.get("protocol")),
        )
    )
    findings = []
    for rule in config["rules"]:
        candidates = [
            m["data"] for m in data["measurements"] if m["data"]["metric"] == rule["metric"]
        ]
        matches = [
            m
            for m in candidates
            if all(
                m[key] == rule[key]
                for key in ("metric_version", "definition", "units", "denominator")
            )
            and (rule["depth"] == "any" or m["depth"] == rule["depth"])
        ]
        evidence = SourceEvidence(
            "registry:policy:" + policy["id"], policy["version"], rule["id"], "controller"
        )
        observed: Any = None
        if not compatible:
            result = "NOT_APPLICABLE"
        elif len(matches) != 1 or matches[0]["value"] is None:
            result = "NOT_EVALUATED" if candidates else rule["missing"]
            observed = "missing, ambiguous or incompatible metric definition"
        else:
            metric = matches[0]
            observed = metric["value"]
            evidence = SourceEvidence(**metric["evidence"])
            if rule["minimum_evidence"] > int(evidence.sha256 is not None):
                result = "NOT_EVALUATED"
            elif rule["threshold"] is None:
                result = "NOT_EVALUATED"
            else:
                passed = {
                    "minimum": observed >= rule["threshold"],
                    "maximum": observed <= rule["threshold"],
                    "equals": observed == rule["threshold"],
                }[rule["operator"]]
                result = "PASS" if passed else rule["severity"]
        findings.append(
            Finding(
                rule["id"],
                Subject(manifest["id"], manifest["version"], "library"),
                result,
                rule["rationale"],
                observed,
                evidence,
            ).export()
        )
    results = {f["data"]["result"] for f in findings}
    outcome = next(
        (
            state
            for state in ("ERROR", "WARN", "NOT_EVALUATED", "PASS", "NOT_APPLICABLE")
            if state in results
        ),
        "NOT_EVALUATED",
    )
    return QCAssessment(
        manifest["id"],
        manifest["version"],
        validation["version"],
        policy,
        findings,
        outcome,
        fingerprint(metadata),
    ).export()
