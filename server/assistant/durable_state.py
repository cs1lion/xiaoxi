"""Shared authority state for the durable orchestrator (C2–C4).

The sidecar-facing router (`/internal/durable`) and the browser-facing workbench
router (`/api/executive/durable`) must see the same leases, confirmations and
proposals. Otherwise the boss approves a confirmation that no dispatch will ever
consume. Everything here is in memory: a host restart revokes write authority.

The commit helpers live here too, so the internal host routes and the boss's
workbench click run the exact same review/consume/ledger path — the browser is
a second caller, not a second implementation of acceptance.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .durable_leases import (
    ConfirmationStore, LeaseError, LeaseStore, ProposalError, ProposalStore,
)
from .durable_review import build_review


class DurableState:
    def __init__(self, ledger, *, leases: LeaseStore | None = None,
                 confirmations: ConfirmationStore | None = None,
                 proposals: ProposalStore | None = None):
        self.leases = leases or LeaseStore(ledger)
        self.confirmations = confirmations or ConfirmationStore()
        self.proposals = proposals or ProposalStore()
        self.commit_results: dict[str, dict[str, Any]] = {}
        self.dispatch_keys: dict[tuple[str, str], str] = {}
        self.answer_keys: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.clarification_attempts: dict[str, int] = {}
        self.background: set[asyncio.Task] = set()


def audit(target, commission_id: str, action: str, status: int = 200,
          detail: str = "") -> None:
    # Callable with either the AssistantService or the ExecutiveService: the
    # sidecar router has the former, the workbench router the latter.
    executive = getattr(target, "executive", target)
    try:
        executive.telemetry.record_event({
            "commission_id": commission_id,
            "agent_id": "orchestrator",
            "step_idx": 0,
            "model": f"orchestrator:{action}",
            "status_code": status,
            "error": detail[:240] if status >= 400 else "",
        })
    except Exception:
        pass


def _previous(state: DurableState, proposal_id: str, action: str) -> dict[str, Any] | None:
    previous = state.commit_results.get(proposal_id)
    if previous is None:
        return None
    if previous.get("action") != action:
        raise ProposalError(409, "proposal_action_mismatch")
    return {**previous["result"], "duplicate": True}


def finalize_accept(target, state: DurableState, proposal_id: str,
                    authorization_id: str) -> dict[str, Any]:
    service = getattr(target, "executive", target)
    previous = _previous(state, proposal_id, "accept")
    if previous is not None:
        return previous
    item = state.proposals.get(proposal_id)
    if item.action != "accept":
        raise ProposalError(409, "proposal_action_mismatch")
    try:
        commission = service.ledger.get(item.commission_id)
    except Exception:
        raise ProposalError(404, "commission_not_found")
    review = build_review(service.evaluator, commission)
    if not review["can_accept"]:
        raise ProposalError(409, "accept_not_allowed")
    state.proposals.consume(item.proposal_id, authorization_id, action="accept",
                            evidence_hash=str(review["evidence_hash"]))
    try:
        updated = service.accept(item.commission_id)
    except ValueError as exc:
        raise ProposalError(409, str(exc))
    result = {"proposal_id": item.proposal_id, "commission": updated, "action": "accept"}
    state.commit_results[proposal_id] = {"action": "accept", "result": result}
    audit(service, item.commission_id, "accept")
    return {**result, "duplicate": False}


def finalize_reject(target, state: DurableState, proposal_id: str,
                    authorization_id: str, feedback: str) -> dict[str, Any]:
    service = getattr(target, "executive", target)
    previous = _previous(state, proposal_id, "reject")
    if previous is not None:
        return previous
    item = state.proposals.get(proposal_id)
    if item.action != "reject":
        raise ProposalError(409, "proposal_action_mismatch")
    try:
        commission = service.ledger.get(item.commission_id)
    except Exception:
        raise ProposalError(404, "commission_not_found")
    review = build_review(service.evaluator, commission)
    state.proposals.consume(item.proposal_id, authorization_id, action="reject",
                            evidence_hash=str(review["evidence_hash"]))
    try:
        rejected = service.reject(item.commission_id, feedback[:2000])
    except ValueError as exc:
        raise ProposalError(409, str(exc))
    result = {"proposal_id": item.proposal_id, **rejected, "action": "reject"}
    state.commit_results[proposal_id] = {"action": "reject", "result": result}
    audit(service, item.commission_id, "reject")
    return {**result, "duplicate": False}


class DurableApprovals:
    """The host's view of the sidecar's in-memory per-call approval ledger.

    The sidecar owns the grant; the host only relays the boss's decision. Pending
    entries are keyed by (tool, action_hash) so approving one call cannot open a
    different call of the same tool.
    """

    def __init__(self, executive, *, timeout: float = 10.0):
        self.executive = executive
        self.timeout = timeout

    def _target(self, commission_id: str) -> tuple[str, dict[str, str], dict[str, Any]]:
        try:
            commission = self.executive.ledger.get(commission_id)
        except Exception:
            raise LookupError("commission_not_found")
        entry = self.executive.dispatcher.agent_entry(str(commission.get("agent_id") or ""))
        if not entry or entry.get("adapter") != "durable":
            raise LookupError("commission_not_durable")
        endpoint = ""
        provider = entry.get("endpoint_provider")
        if callable(provider):
            try:
                endpoint = str(provider() or "")
            except Exception:
                endpoint = ""
        endpoint = endpoint or str(entry.get("endpoint") or "").strip()
        if not endpoint:
            raise ConnectionError("durable_endpoint_unavailable")
        headers = {"content-type": "application/json"}
        token = entry.get("token") or ""
        if isinstance(token, str) and token:
            headers["authorization"] = f"Bearer {token}"
        return endpoint, headers, commission

    async def pending(self, commission_id: str) -> list[dict[str, Any]]:
        endpoint, headers, _ = self._target(commission_id)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            listed = await client.get(f"{endpoint}/v1/approvals",
                                      params={"commission_id": commission_id},
                                      headers=headers)
            listed.raise_for_status()
            payload = listed.json()
        items = payload.get("pending") or []
        return [item for item in items if isinstance(item, dict)]

    async def decide(self, commission_id: str, *, tool: str, action_hash: str = "",
                     approved: bool = True) -> list[dict[str, Any]]:
        endpoint, headers, _ = self._target(commission_id)
        payload: dict[str, Any] = {"commission_id": commission_id, "tool": tool}
        if action_hash:
            payload["action_hash"] = action_hash
        if not approved:
            payload["revoke"] = True
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            posted = await client.post(f"{endpoint}/v1/approvals",
                                       headers=headers, json=payload)
            posted.raise_for_status()
            body = posted.json()
        items = body.get("pending") or []
        return [item for item in items if isinstance(item, dict)]


__all__ = [
    "DurableState", "DurableApprovals", "audit", "finalize_accept", "finalize_reject",
    "LeaseError", "ProposalError",
]
