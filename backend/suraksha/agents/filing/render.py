"""Render a ClaimPacket as a printable HTML page with each field's source and the verifier's
findings beside it. Vault tokens are revealed through `reveal` (which the caller audits)."""

from collections.abc import Callable
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from suraksha.agents.filing.forms import FORMS
from suraksha.contracts import ClaimPacket, RuleResult, VerifierVerdict
from suraksha.services.registry import load_scheme

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
)
TITLES = {
    "pmjjby-claim-form": "PMJJBY claim form",
    "pmsby-claim-form": "PMSBY claim form",
    "rupay-claim-form": "RuPay card accident claim form",
    "rupay-bank-declaration": "Card-issuing bank's declaration",
}


def render_packet(
    packet: ClaimPacket,
    verdict: VerifierVerdict | None = None,
    rule_result: RuleResult | None = None,
    summary: str | None = None,
    reveal: Callable[[str], str] = lambda token: token,
) -> str:
    labels = {
        template: {f.key: f.label for f in spec}
        for template, spec in FORMS[packet.scheme_id].items()
    }
    findings = list(verdict.findings) if verdict else []
    used: set[int] = set()

    def for_field(template: str, name: str) -> list:
        hits = []
        for i, f in enumerate(findings):
            if f.target in (f"{template}.{name}", name):
                hits.append(f)
                used.add(i)
        return hits

    forms = []
    for form in packet.forms:
        rows = []
        for f in form.fields:
            value = f.value
            if value and value.startswith("tok_"):
                value = reveal(value)
            src = f.source_ref
            rows.append({
                "label": labels.get(form.template_id, {}).get(f.name, f.name),
                "value": value,
                "source": f"{src.kind.replace('_', ' ')} · {src.ref}" if src else None,
                "findings": for_field(form.template_id, f.name),
            })  # fmt: skip
        forms.append({"title": TITLES.get(form.template_id, form.template_id), "rows": rows})
    packet_findings = [f for i, f in enumerate(findings) if i not in used]

    return _env.get_template("packet.html.j2").render(
        packet=packet, forms=forms, verdict=verdict, packet_findings=packet_findings,
        summary=summary, cover_letter=packet.cover_letter,
        scheme_name=load_scheme(packet.scheme_id)["name"],
        rule_ids=", ".join(rule_result.rule_ids) if rule_result else "",
    )  # fmt: skip
