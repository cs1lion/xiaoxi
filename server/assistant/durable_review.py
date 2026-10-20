"""Read-only delivery review for C3.

The model never commits an acceptance. This module turns the stored audit and
delivery into a review surface: L1/L2 findings, rubric hits, artifact checks,
recommendation, and an evidence hash that proposals bind to.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ARTIFACT_RULES = {"required_fields", "artifacts_exist", "no_unwired_dry_run", "wired_not_dry"}


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _artifact_checks(delivery: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    artifacts = delivery.get("artifacts") if isinstance(delivery, dict) else []
    for item in artifacts if isinstance(artifacts, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        ref = item.get("ref")
        exists: bool | None = None
        if kind == "file" and isinstance(ref, str) and ref.strip():
            try:
                path = Path(ref)
                exists = path.is_file() if path.is_absolute() else None
            except (OSError, ValueError):
                exists = False
        checks.append({
            "kind": kind,
            "ref": ref if isinstance(ref, str) else None,
            "exists": exists,
            "rechecked": exists is not None,
        })
    return checks[:12]


def build_review(evaluator, commission: dict[str, Any]) -> dict[str, Any]:
    status = str(commission.get("status") or "")
    audit = commission.get("audit") if isinstance(commission.get("audit"), dict) else {}
    delivery = commission.get("delivery") if isinstance(commission.get("delivery"), dict) else {}
    findings = audit.get("findings") if isinstance(audit.get("findings"), list) else []
    layer = str(audit.get("layer") or "none")
    rubric_map = {str(item.get("id")): item for item in evaluator.rubrics.list(active_only=True)}
    normalized = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rule_id = str(finding.get("rule_id") or "")
        rubric = rubric_map.get(rule_id) or {}
        finding_layer = layer if layer in ("l1", "l2") else str(rubric.get("layer") or "l2")
        normalized.append({
            "rule_id": rule_id,
            "severity": str(finding.get("severity") or "major"),
            "detail": str(finding.get("detail") or "")[:400],
            "layer": finding_layer,
            "rule": str(rubric.get("rule") or "")[:400] if rubric else "",
        })
    blockers = [item for item in normalized if item["severity"] == "blocker"]
    l1_findings = [item for item in normalized if item["layer"] == "l1"]
    rubric_hits = [item for item in normalized if item["layer"] == "l2"]
    artifact_checks = _artifact_checks(delivery)
    artifact_findings = [item for item in normalized if item["rule_id"] in ARTIFACT_RULES]
    artifact_recheck_failed = any(item.get("exists") is False for item in artifact_checks)
    # Strictly "verified" means every artifact was rechecked and exists. A note
    # or a relative/unknown ref is not verified, even when L1 accepted it.
    artifacts_verified = bool(artifact_checks) and all(
        item.get("rechecked") is True and item.get("exists") is True for item in artifact_checks)
    passed = audit.get("passed") is True
    # A blocker at either layer is not acceptable; a file artifact that vanished
    # after the audit is also not acceptable. Non-blocking L2 findings stay
    # visible to the boss.
    can_accept = (status == "ready_to_report" and passed and not blockers
                  and bool(artifact_checks) and not artifact_recheck_failed)
    recommendation = "accept" if can_accept else "reject"
    evidence = {
        "commission_id": str(commission.get("id") or ""),
        "status": status,
        "retry_count": int(commission.get("retry_count") or 0),
        "audit": {
            "passed": audit.get("passed"),
            "layer": audit.get("layer"),
            "created_at": audit.get("created_at"),
            "usage": audit.get("usage") if isinstance(audit.get("usage"), dict) else {},
            # Findings can change while passed/layer stay the same (for example a
            # warning added to a passing review). Bind them so an old proposal
            # cannot be reused.
            "findings": normalized[:8],
            "blockers": blockers[:8],
        },
        "delivery": {
            "summary": str(delivery.get("summary") or "")[:600],
            "artifacts": (delivery.get("artifacts") or [])[:12],
            "intercepted": str(delivery.get("intercepted") or "")[:400],
        },
        "artifact_checks": artifact_checks,
    }
    return {
        "commission_id": str(commission.get("id") or ""),
        "status": status,
        "reviewable": status == "ready_to_report",
        "passed": passed,
        "layer": layer,
        "findings": normalized[:8],
        "blockers": blockers[:8],
        "l1_findings": l1_findings[:8],
        "rubric_hits": rubric_hits[:8],
        "artifact_checks": artifact_checks,
        "artifacts_verified": artifacts_verified,
        "artifact_recheck_failed": artifact_recheck_failed,
        "artifact_unverified_count": sum(1 for item in artifact_checks if item.get("rechecked") is not True),
        "artifact_findings": artifact_findings[:8],
        "recommendation": recommendation,
        "can_accept": can_accept,
        "can_reject": status == "ready_to_report",
        "reason": str(audit.get("scolding") or "")[:600],
        "delivery_summary": str(delivery.get("summary") or "")[:600],
        "evidence_hash": _hash(evidence),
        "evidence": evidence,
        "context_is_untrusted_data": True,
        "grants_authorization": False,
    }


__all__ = ["build_review"]
