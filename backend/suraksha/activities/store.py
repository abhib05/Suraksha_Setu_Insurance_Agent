"""Where activities persist what each step produced. `DbStore` writes Postgres and the audit
log; `MemoryStore` keeps everything in lists so workflow tests need no database."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session

from suraksha import audit
from suraksha.contracts import CaseFacts, ClaimPacket, Entitlement, RuleResult, VerifierVerdict
from suraksha.db import models as m
from suraksha.db.base import new_id, session_scope


class Store(Protocol):
    def record(self, case_id: str, actor: str, action: str, *, input: Any = None,
               output: Any = None, detail: dict | None = None,
               rule_versions: list[str] | None = None) -> None: ...  # fmt: skip
    def save_facts(self, facts: CaseFacts) -> None: ...
    def save_entitlements(self, ents: list[Entitlement], results: list[RuleResult]) -> None: ...
    def open_claim(self, case_id: str, claim_id: str, ent: Entitlement) -> None: ...
    def set_claim_status(
        self, claim_id: str, status: str, claim_number: str | None = None
    ) -> None: ...  # noqa: E501
    def save_packet(self, packet: ClaimPacket, verdict: VerifierVerdict) -> None: ...
    def create_task(self, case_id: str, claim_id: str | None, kind: str, detail: str,
                    assignee: str) -> str: ...  # fmt: skip


@dataclass
class MemoryStore:
    events: list[dict] = field(default_factory=list)
    tasks: dict[str, dict] = field(default_factory=dict)
    claims: dict[str, dict] = field(default_factory=dict)
    packets: list[ClaimPacket] = field(default_factory=list)

    def record(self, case_id, actor, action, *, input=None, output=None, detail=None,
               rule_versions=None):  # fmt: skip
        self.events.append({"case_id": case_id, "actor": actor, "action": action,
                            "detail": detail or {}})  # fmt: skip

    def save_facts(self, facts):
        pass

    def save_entitlements(self, ents, results):
        pass

    def open_claim(self, case_id, claim_id, ent):
        self.claims[claim_id] = {"case_id": case_id, "scheme": ent.scheme_id, "status": "open"}

    def set_claim_status(self, claim_id, status, claim_number=None):
        self.claims[claim_id].update(status=status, claim_number=claim_number)

    def save_packet(self, packet, verdict):
        self.packets.append(packet)

    def create_task(self, case_id, claim_id, kind, detail, assignee):
        task_id = f"task_{len(self.tasks) + 1}"
        self.tasks[task_id] = {"case_id": case_id, "claim_id": claim_id, "kind": kind,
                               "detail": detail, "assignee": assignee}  # fmt: skip
        return task_id

    def actions(self, case_id: str | None = None) -> list[str]:
        return [e["action"] for e in self.events if case_id is None or e["case_id"] == case_id]


class DbStore:
    def _s(self):
        return session_scope()

    def record(self, case_id, actor, action, *, input=None, output=None, detail=None,
               rule_versions=None):  # fmt: skip
        with self._s() as s:
            audit.append(s, actor=actor, action=action, case_id=case_id, input=input,
                         output=output, detail=detail, rule_versions=rule_versions)  # fmt: skip

    def save_facts(self, facts: CaseFacts) -> None:
        d = facts.deceased
        with self._s() as s:
            s.merge(
                m.Deceased(
                    case_id=facts.case_id,
                    name_token=d.name_token,
                    date_of_death=d.date_of_death,
                    place_of_death=d.place_of_death,
                    event_type=d.event_type.value,
                    age_at_death=d.age_at_death,
                )
            )
            for c in facts.claimants:
                s.merge(
                    m.Claimant(
                        id=c.claimant_id,
                        case_id=facts.case_id,
                        name_token=c.name_token,
                        relationship=c.relationship.value,
                        is_nominee=c.is_nominee,
                        phone_token=c.phone_token,
                        bank_account_token=c.bank_account_token,
                    )
                )
            for a in facts.assets:
                s.merge(m.Asset(id=a.asset_id, case_id=facts.case_id, type=a.type.value,
                                institution=a.institution, identifier_token=a.identifier_token,
                                card_variant=a.card_variant, last_txn_date=a.last_txn_date,
                    card_issue_date=a.card_issue_date,
                                source=a.source))  # fmt: skip

    def save_entitlements(self, ents: list[Entitlement], results: list[RuleResult]) -> None:
        by_id = {r.entitlement_id: r for r in results}
        with self._s() as s:
            for e in ents:
                r = by_id.get(e.entitlement_id)
                s.merge(m.Entitlement(
                    id=e.entitlement_id, case_id=e.case_id, scheme_id=e.scheme_id.value,
                    asset_id=e.asset_id, strength=e.strength.value,
                    expected_payout_inr=e.expected_payout_inr,
                    evidence=[ev.model_dump() for ev in e.evidence],
                    rule_result=r.model_dump(mode="json") if r else None,
                ))  # fmt: skip

    def open_claim(self, case_id: str, claim_id: str, ent: Entitlement) -> None:
        with self._s() as s:
            s.merge(
                m.Claim(
                    id=claim_id,
                    case_id=case_id,
                    entitlement_id=ent.entitlement_id,
                    scheme_id=ent.scheme_id.value,
                    workflow_id=f"claim-{claim_id}",
                )
            )

    def set_claim_status(self, claim_id: str, status: str, claim_number: str | None = None):
        with self._s() as s:
            claim = s.get(m.Claim, claim_id)
            claim.status = status
            if claim_number:
                claim.claim_number = claim_number

    def save_packet(self, packet: ClaimPacket, verdict: VerifierVerdict) -> None:
        with self._s() as s:
            s.merge(m.Packet(id=f"{packet.claim_id}-v{packet.version}", claim_id=packet.claim_id,
                             version=packet.version, body=packet.model_dump(mode="json"),
                             verdict=verdict.model_dump(mode="json")))  # fmt: skip

    def create_task(self, case_id, claim_id, kind, detail, assignee) -> str:
        with self._s() as s:
            return _new_task(s, case_id, claim_id, kind, detail, assignee)


def _new_task(s: Session, case_id, claim_id, kind, detail, assignee) -> str:
    task = m.Task(id=new_id("task"), case_id=case_id, claim_id=claim_id, kind=kind,
                  detail=detail, assignee=assignee)  # fmt: skip
    s.add(task)
    s.flush()
    audit.append(s, actor="service:workflow", action="task.created", case_id=case_id,
                 detail={"task_id": task.id, "kind": kind, "claim_id": claim_id})  # fmt: skip
    return task.id
