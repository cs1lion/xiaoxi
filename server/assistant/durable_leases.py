"""Host-issued dispatch leases for C2.

A lease is the only way the durable orchestrator can create a Commission. The
model never sees the lease id (it travels in RunMeta from the host), cannot
change its limits, and cannot mint a new one. Leases live in memory: a host
restart invalidates all of them, which is the safe default for write authority.

Initial scope: scribe only, cheap/medium cost, 1 concurrent, 3 per hour,
30-minute TTL. High-tier and shell workers are not dispatchable from C2; they
stay on the existing manual/UI path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import secrets
import threading
import time
from typing import Any, Callable

IN_FLIGHT = ("briefing", "running", "auditing", "blocked")
ALLOWED_AGENTS = ("scribe",)
ALLOWED_TIERS = ("cheap", "medium")
DEFAULT_TTL = 1800.0
MAX_CONCURRENCY = 1
MAX_PER_HOUR = 3
MODES = ("dry_run", "confirm", "auto")


class LeaseError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class ConfirmationError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass
class PendingConfirmation:
    confirmation_id: str
    action_hash: str
    lease_id: str
    session_id: str
    agent_id: str
    title: str
    brief: str
    thread_id: str
    intent: str
    status: str
    created_at: float
    expires_at: float


def dispatch_action_hash(*, lease_id: str, session_id: str, agent_id: str,
                         title: str, brief: str, thread_id: str = "",
                         intent: str = "") -> str:
    """Bind a confirmation to the exact work, not just to a lease.

    Includes the fields the boss saw in the preview: lease, session, agent,
    title, brief, thread, and intent. A changed title/brief produces a different
    hash, so a confirmation cannot be replayed for another job.
    """
    payload = json.dumps({
        "lease_id": lease_id, "session_id": session_id, "agent_id": agent_id,
        "title": title, "brief": brief, "thread_id": thread_id, "intent": intent,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ConfirmationStore:
    """One-time confirmations for C2 confirm mode. In memory: restart invalidates."""

    def __init__(self, *, clock: Callable[[], float] = time.time, ttl: float = 600.0):
        self.clock = clock
        self.ttl = float(ttl)
        self._pending: dict[str, PendingConfirmation] = {}
        self._lock = threading.RLock()

    def open(self, *, action_hash: str, lease_id: str, session_id: str,
             agent_id: str, title: str, brief: str,
             thread_id: str = "", intent: str = "") -> PendingConfirmation:
        now = self.clock()
        with self._lock:
            for item in self._pending.values():
                if (item.action_hash == action_hash and item.lease_id == lease_id
                        and item.session_id == session_id and item.status == "pending"
                        and now < item.expires_at):
                    return item
            item = PendingConfirmation(
                confirmation_id=secrets.token_urlsafe(24), action_hash=action_hash,
                lease_id=lease_id, session_id=session_id, agent_id=agent_id,
                title=title, brief=brief, thread_id=thread_id, intent=intent,
                status="pending", created_at=now, expires_at=now + self.ttl,
            )
            self._pending[item.confirmation_id] = item
            return item

    def decide(self, confirmation_id: str, approved: bool) -> PendingConfirmation:
        with self._lock:
            item = self._pending.get(confirmation_id)
            if item is None:
                raise ConfirmationError(404, "confirmation_not_found")
            if self.clock() >= item.expires_at:
                raise ConfirmationError(409, "confirmation_expired")
            if item.status != "pending":
                raise ConfirmationError(409, f"confirmation_{item.status}")
            item.status = "approved" if approved else "rejected"
            return item

    def list_pending(self, lease_id: str, session_id: str) -> list[PendingConfirmation]:
        now = self.clock()
        with self._lock:
            return [item for item in self._pending.values()
                    if item.lease_id == lease_id and item.session_id == session_id
                    and item.status == "pending" and now < item.expires_at]

    def list_pending_all(self) -> list[PendingConfirmation]:
        """Every live confirmation, for the boss's workbench.

        The browser has no lease id until a dispatch exists; the confirmation is
        created before the commission. Single-user local host, same-origin only.
        """
        now = self.clock()
        with self._lock:
            return [item for item in self._pending.values()
                    if item.status == "pending" and now < item.expires_at]

    def peek(self, confirmation_id: str) -> PendingConfirmation:
        with self._lock:
            item = self._pending.get(confirmation_id)
            if item is None:
                raise ConfirmationError(404, "confirmation_not_found")
            if self.clock() >= item.expires_at:
                raise ConfirmationError(409, "confirmation_expired")
            return item

    def consume(self, confirmation_id: str, action_hash: str) -> PendingConfirmation:
        with self._lock:
            item = self._pending.get(confirmation_id)
            if item is None:
                raise ConfirmationError(404, "confirmation_not_found")
            if self.clock() >= item.expires_at:
                raise ConfirmationError(409, "confirmation_expired")
            if item.action_hash != action_hash:
                raise ConfirmationError(409, "confirmation_action_changed")
            if item.status != "approved":
                raise ConfirmationError(409, f"confirmation_{item.status}")
            item.status = "consumed"
            return item


def describe_confirmation(item: PendingConfirmation) -> dict[str, Any]:
    return {"confirmation_id": item.confirmation_id, "action_hash": item.action_hash,
            "status": item.status, "expires_at": item.expires_at,
            "agent_id": item.agent_id, "title": item.title, "brief": item.brief}


class ProposalError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass
class DeliveryProposal:
    proposal_id: str
    commission_id: str
    lease_id: str
    session_id: str
    action: str
    evidence_hash: str
    suggested_feedback: str
    status: str
    authorization_id: str
    authorization_expires_at: float
    created_at: float
    expires_at: float


class ProposalStore:
    """Read-only C3 proposals plus a host-only one-time authorization.

    The model can create a proposal; only the host can authorize it. This keeps
    acceptance authority outside the model even though the model does the review.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time,
                 proposal_ttl: float = 1800.0, authorization_ttl: float = 600.0):
        self.clock = clock
        self.proposal_ttl = float(proposal_ttl)
        self.authorization_ttl = float(authorization_ttl)
        self._items: dict[str, DeliveryProposal] = {}
        self._lock = threading.RLock()

    def create(self, *, commission_id: str, lease_id: str, session_id: str,
               action: str, evidence_hash: str,
               suggested_feedback: str = "") -> DeliveryProposal:
        if action not in ("accept", "reject"):
            raise ProposalError(400, "invalid_proposal_action")
        now = self.clock()
        item = DeliveryProposal(
            proposal_id=secrets.token_urlsafe(24), commission_id=commission_id,
            lease_id=lease_id, session_id=session_id, action=action,
            evidence_hash=evidence_hash, suggested_feedback=suggested_feedback[:1200],
            status="pending", authorization_id="", authorization_expires_at=0.0,
            created_at=now, expires_at=now + self.proposal_ttl,
        )
        with self._lock:
            self._items[item.proposal_id] = item
        return item

    def get(self, proposal_id: str) -> DeliveryProposal:
        with self._lock:
            item = self._items.get(proposal_id)
            if item is None:
                raise ProposalError(404, "proposal_not_found")
            if self.clock() >= item.expires_at:
                raise ProposalError(409, "proposal_expired")
            return item

    def list_open(self) -> list[DeliveryProposal]:
        """Pending/authorized proposals for the workbench; consumed ones drop out."""
        now = self.clock()
        with self._lock:
            return [item for item in self._items.values()
                    if item.status in ("pending", "authorized") and now < item.expires_at]

    def authorize(self, proposal_id: str, *, source: str = "boss") -> DeliveryProposal:
        if source != "boss":
            # Automatic acceptance is a separate policy engine, not a model or
            # C3 shortcut. Until that engine exists, only the boss authorizes.
            raise ProposalError(403, "policy_engine_not_configured")
        with self._lock:
            item = self.get(proposal_id)
            if item.status == "authorized":
                if self.clock() < item.authorization_expires_at:
                    # The boss's click is one authorization; asking again while
                    # it is live must go through the same one-time id.
                    raise ProposalError(409, "proposal_authorized")
                # Expired authorization: the boss clicking again issues a new
                # one. The proposal itself is still pending work, not consumed.
                item.status = "pending"
            if item.status != "pending":
                raise ProposalError(409, f"proposal_{item.status}")
            item.status = "authorized"
            item.authorization_id = secrets.token_urlsafe(24)
            item.authorization_expires_at = self.clock() + self.authorization_ttl
            return item

    def consume(self, proposal_id: str, authorization_id: str, *,
                action: str, evidence_hash: str) -> DeliveryProposal:
        with self._lock:
            item = self.get(proposal_id)
            if item.action != action:
                raise ProposalError(409, "proposal_action_mismatch")
            if item.evidence_hash != evidence_hash:
                raise ProposalError(409, "proposal_evidence_changed")
            if item.status != "authorized":
                raise ProposalError(409, f"proposal_{item.status}")
            if not authorization_id or authorization_id != item.authorization_id:
                raise ProposalError(409, "proposal_authorization_mismatch")
            if self.clock() >= item.authorization_expires_at:
                raise ProposalError(409, "proposal_authorization_expired")
            item.status = "consumed"
            return item


def describe_proposal(item: DeliveryProposal) -> dict[str, Any]:
    return {"proposal_id": item.proposal_id, "commission_id": item.commission_id,
            "action": item.action, "status": item.status,
            "evidence_hash": item.evidence_hash,
            "suggested_feedback": item.suggested_feedback,
            "expires_at": item.expires_at,
            "authorization_expires_at": item.authorization_expires_at or None}


@dataclass
class Lease:
    lease_id: str
    session_id: str
    user_intent: str
    mode: str
    created_at: float
    expires_at: float
    allowed_agents: tuple[str, ...] = ALLOWED_AGENTS
    dispatches: list[float] = field(default_factory=list)
    commissions: set[str] = field(default_factory=set)


class LeaseStore:
    def __init__(self, ledger: Any, *, clock: Callable[[], float] = time.time,
                 ttl: float = DEFAULT_TTL):
        self.ledger = ledger
        self.clock = clock
        self.ttl = float(ttl)
        self._leases: dict[str, Lease] = {}
        self._lock = threading.RLock()

    def issue(self, session_id: str, user_intent: str, mode: str | None = None,
              *, allowed_agents: tuple[str, ...] | None = None) -> dict[str, Any]:
        if not isinstance(session_id, str) or not session_id.strip():
            raise LeaseError(400, "session_id_required")
        if not isinstance(user_intent, str) or not user_intent.strip():
            # No current user intent, no write authority. Memory/note text alone
            # must never be able to open a dispatch lease.
            raise LeaseError(400, "user_intent_required")
        selected = (mode or os.environ.get("XIAOXI_DURABLE_LEASE_MODE") or "dry_run").strip()
        if selected not in MODES:
            raise LeaseError(400, "invalid_lease_mode")
        agents = tuple(str(item) for item in (allowed_agents or ALLOWED_AGENTS)
                       if isinstance(item, str) and item)
        if not agents:
            raise LeaseError(400, "allowed_agents_required")
        now = self.clock()
        lease = Lease(lease_id=secrets.token_urlsafe(24), session_id=session_id.strip(),
                      user_intent=user_intent.strip()[:400], mode=selected,
                      allowed_agents=agents, created_at=now, expires_at=now + self.ttl)
        with self._lock:
            self._leases[lease.lease_id] = lease
        return self.describe(lease)

    @staticmethod
    def describe(lease: Lease) -> dict[str, Any]:
        return {
            "lease_id": lease.lease_id,
            "session_id": lease.session_id,
            "user_intent_hash": hashlib.sha256(lease.user_intent.encode("utf-8")).hexdigest()[:16],
            "mode": lease.mode,
            "expires_at": lease.expires_at,
            "max_concurrency": MAX_CONCURRENCY,
            "max_per_hour": MAX_PER_HOUR,
            "max_cost_tier": "medium",
            "allowed_agents": list(lease.allowed_agents),
        }

    def get(self, lease_id: str) -> Lease:
        with self._lock:
            lease = self._leases.get(lease_id)
        if lease is None:
            raise LeaseError(403, "lease_not_found")
        return lease

    def validate(self, lease_id: str, session_id: str, *, agent_id: str | None = None,
                 cost_tier: str | None = None, for_write: bool = False,
                 allow_modes: tuple[str, ...] = ("auto",)) -> Lease:
        lease = self.get(lease_id)
        if lease.session_id != session_id:
            raise LeaseError(403, "lease_session_mismatch")
        if self.clock() >= lease.expires_at:
            raise LeaseError(403, "lease_expired")
        if agent_id is not None and agent_id not in lease.allowed_agents:
            raise LeaseError(403, "worker_not_in_lease")
        if cost_tier is not None and cost_tier not in ALLOWED_TIERS:
            raise LeaseError(403, "cost_tier_not_allowed")
        if not for_write:
            return lease
        if lease.mode not in allow_modes:
            # dry_run/confirm are previews unless a one-time confirmation was
            # consumed; the route passes allow_modes=("confirm",) only then.
            raise LeaseError(409, "lease_not_auto")
        now = self.clock()
        lease.dispatches = [item for item in lease.dispatches if now - item < 3600]
        if len(lease.dispatches) >= MAX_PER_HOUR:
            raise LeaseError(429, "lease_hour_limit")
        live = 0
        for commission_id in list(lease.commissions):
            try:
                if str(self.ledger.get(commission_id).get("status")) in IN_FLIGHT:
                    live += 1
            except Exception:
                continue
        if live >= MAX_CONCURRENCY:
            raise LeaseError(429, "lease_concurrency_limit")
        return lease

    def record_dispatch(self, lease: Lease, commission_id: str) -> None:
        with self._lock:
            lease.dispatches.append(self.clock())
            lease.commissions.add(commission_id)

    def commission_allowed(self, lease: Lease, commission_id: str) -> bool:
        return commission_id in lease.commissions


__all__ = ["LeaseError", "LeaseStore", "ConfirmationError", "ConfirmationStore",
           "ProposalError", "ProposalStore", "dispatch_action_hash",
           "describe_confirmation", "describe_proposal",
           "ALLOWED_AGENTS", "ALLOWED_TIERS", "MODES"]
