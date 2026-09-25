"""Human audit views derived from a validated record replay and approved plan."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
import shlex
from types import SimpleNamespace
from typing import Callable

from artifact_models import (
    ArtifactRecord, BindingPayload, CorrectionWorkUnitPayload,
    FinalReviewCompletedPayload, FindingTransitionPayload, GatePayload,
    GateDecisionPayload,
    GateTransitionPayload, PlanPayload, ReviewPayload, ReviewValidationBindingPayload,
    ScopeExtensionPayload,
    SideEffectPayload, ValidationAttestationPayload, ValidationContentPayload,
    WorkflowCompletionPayload, WorkflowEventPayload, WorkUnitPayload,
)
from artifact_replay import ArtifactReplayResult
from audit_document_contract import (
    OVERALL_SECTIONS, PLAN_APPENDIX_HEADING, PLAN_SECTIONS, SLICE_SECTIONS,
    managed_section,
)
from finding_order import finding_id_sort_key
from finding_reducer import project_record_finding_statuses


_MAX_OUTPUT_LINES = 40
_HEX = re.compile(r"(?<![A-Za-z0-9])[0-9a-fA-F]{12,}(?![A-Za-z0-9])")
_SHORT_DIGEST = re.compile(r"(?i)\b(fingerprint|digest|hash|sha256)(\s*[:=]?\s+)([0-9a-f]{8,11})\b")
_RECORD_ID = re.compile(r"ar1-[0-9a-f]{12,64}")
_PLAN_SLICE = re.compile(r"(?m)^### Slice (?P<id>[1-9][0-9]*)\s*[-–]\s*(?P<title>[^\n]+)$")
_SECTION = re.compile(r"(?m)^(?:\*\*[^\n]+\*\*|#{1,6}[ \t]+)")


class ReadableAuditError(ValueError):
    """An accepted chain cannot be rendered without breaking the document contract."""


def _prose(value: object) -> str:
    """Keep the original words and line breaks, omitting technical identifiers."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = _RECORD_ID.sub("[Recordbezug ausgelassen]", text)
    text = _SHORT_DIGEST.sub(lambda match: match.group(1) + match.group(2) + "[Hash ausgelassen]", text)
    return _HEX.sub("[Hash ausgelassen]", text)


def _quote(value: object) -> str:
    lines = _prose(value).split("\n")
    return "\n".join(">" if not line else f"> {line}" for line in lines)


def _cell(value: object) -> str:
    return _prose(value).replace("|", r"\|").replace("\n", " ").strip()


def _title(value: object) -> str:
    first = _cell(value).split(". ", 1)[0].rstrip(".")
    if len(first) > 110:
        first = first[:107].rsplit(" ", 1)[0] + "…"
    return first


def _slice_section(plan_text: str, slice_id: int) -> tuple[str, str, str]:
    headings = list(_PLAN_SLICE.finditer(plan_text))
    matches = [index for index, item in enumerate(headings) if int(item.group("id")) == slice_id]
    if len(matches) != 1:
        return "", "", ""
    index = matches[0]
    heading = headings[index]
    end = headings[index + 1].start() if index + 1 < len(headings) else len(plan_text)
    body = plan_text[heading.end():end]

    def block(label: str) -> str:
        match = re.search(rf"(?m)^\*\*{re.escape(label)}\*\*[ \t]*$", body)
        if match is None:
            return ""
        after = body[match.end():]
        boundary = _SECTION.search(after)
        return after[:boundary.start() if boundary else len(after)].strip("\n")

    return heading.group("title").strip(), block("Ziel"), block("Akzeptanzkriterien")


@dataclass(frozen=True)
class _Unit:
    slice_id: str
    round_number: int
    correction: bool


class AuditFacts:
    """Index the already validated chain once for all document renderers."""

    def __init__(
        self,
        replay: ArtifactReplayResult,
        *,
        read_blob: Callable[[object], bytes],
        read_plan: Callable[[str, str], str],
    ) -> None:
        self.replay = replay
        self.records = replay.records
        self.read_blob = read_blob
        self.by_id = {record.record_id: record for record in self.records}
        self.units: dict[str, _Unit] = {}
        self.unit_rounds: list[tuple[str, _Unit]] = []
        self.transition_round: dict[int, int] = {}
        self.owner: dict[str, str] = {}
        self.owner_round: dict[str, int] = {}
        self.plan: PlanPayload | None = None
        self.plan_text = ""
        self.final_statuses = dict(project_record_finding_statuses(replay))
        self.finding_events: dict[str, list[FindingTransitionPayload]] = defaultdict(list)
        self.openings: dict[str, FindingTransitionPayload] = {}
        self.reviews: dict[str, list[ReviewPayload]] = defaultdict(list)
        self.validations: dict[str, list[ValidationAttestationPayload]] = defaultdict(list)
        self.bound_validations: dict[tuple[str, int], list[ValidationAttestationPayload]] = defaultdict(list)
        self.gates: dict[str, list[object]] = defaultdict(list)
        self.gate_round: dict[int, int] = {}
        self.gate_decisions: dict[str, list[GatePayload]] = defaultdict(list)
        self.final_review: FinalReviewCompletedPayload | None = None
        self.commits: dict[str, str] = {}
        for record in self.records:
            payload = record.payload
            if isinstance(payload, (WorkUnitPayload, CorrectionWorkUnitPayload)):
                key = record.logical_id.removeprefix("work-unit-")
                unit = _Unit(payload.slice_id, payload.round_number, isinstance(payload, CorrectionWorkUnitPayload) or payload.round_number > 1)
                self.units[key] = unit
                if (key, unit) not in self.unit_rounds:
                    self.unit_rounds.append((key, unit))
            elif isinstance(payload, WorkflowEventPayload) and payload.work_unit_id is not None:
                if payload.round_number is not None and (payload.work_unit_id not in self.units or self.units[payload.work_unit_id].round_number != payload.round_number):
                    unit = _Unit(payload.slice_id, payload.round_number, payload.round_number > 1)
                    self.units[payload.work_unit_id] = unit
                    if (payload.work_unit_id, unit) not in self.unit_rounds:
                        self.unit_rounds.append((payload.work_unit_id, unit))
                for ref in payload.record_refs:
                    self.owner[ref] = payload.work_unit_id
                    if payload.round_number is not None:
                        self.owner_round[ref] = payload.round_number
            elif isinstance(payload, PlanPayload):
                self.plan = payload
            elif isinstance(payload, FindingTransitionPayload):
                self.finding_events[payload.finding_id].append(payload)
                if payload.work_unit_id in self.units:
                    self.transition_round[id(payload)] = self.units[payload.work_unit_id].round_number
                if payload.action == "opened":
                    self.openings[payload.finding_id] = payload
            elif isinstance(payload, ReviewPayload):
                self.reviews[payload.work_unit_id].append(payload)
            elif isinstance(payload, FinalReviewCompletedPayload):
                self.final_review = payload
            elif isinstance(payload, ScopeExtensionPayload):
                self.gates[payload.work_unit_id].append(payload)
                self.gate_round[id(payload)] = self.units[payload.work_unit_id].round_number if payload.work_unit_id in self.units else 1
            elif isinstance(payload, GateTransitionPayload):
                self.gates[payload.work_unit_id].append(payload)
                self.gate_round[id(payload)] = self.units[payload.work_unit_id].round_number if payload.work_unit_id in self.units else 1
            elif isinstance(payload, SideEffectPayload) and payload.effect_class == "git_commit" and payload.phase == "result" and payload.operation[0] == "slice_commit" and payload.result:
                self.commits[payload.operation[1]] = payload.result
        for record in self.records:
            payload = record.payload
            if isinstance(payload, ValidationAttestationPayload):
                unit = self.owner.get(record.record_id)
                if unit is not None:
                    self.validations[unit].append(payload)
            elif isinstance(payload, GateDecisionPayload):
                decision_record = self.by_id.get(payload.gate_record_id)
                if decision_record is not None and isinstance(decision_record.payload, GatePayload):
                    self.gate_decisions[payload.work_unit_id].append(decision_record.payload)
            elif isinstance(payload, ReviewValidationBindingPayload):
                review_record = self.by_id.get(payload.review_record_id)
                attestation_record = self.by_id.get(payload.attestation_record_id)
                if review_record is not None and isinstance(review_record.payload, ReviewPayload) and attestation_record is not None and isinstance(attestation_record.payload, ValidationAttestationPayload):
                    key = review_record.payload.work_unit_id
                    round_number = self.owner_round.get(review_record.record_id)
                    if round_number is not None:
                        self.bound_validations[key, round_number].append(attestation_record.payload)
        if self.plan is not None:
            self.plan_text = read_plan(self.plan.approved_plan_commit, self.plan.work_plan_path)

    def slice_units(self, slice_id: str) -> list[tuple[str, _Unit]]:
        return sorted(((key, unit) for key, unit in self.unit_rounds if unit.slice_id == slice_id), key=lambda pair: (pair[1].round_number, int(pair[0]) if pair[0].isdigit() else pair[0]))

    def round_reviews(self, key: str, round_number: int) -> list[ReviewPayload]:
        return [review for record in self.records if isinstance((review := record.payload), ReviewPayload) and review.work_unit_id == key and self.owner_round.get(record.record_id) == round_number]

    def round_validations(self, key: str, round_number: int) -> list[ValidationAttestationPayload]:
        owned = [att for record in self.records if isinstance((att := record.payload), ValidationAttestationPayload) and self.owner.get(record.record_id) == key and self.owner_round.get(record.record_id) == round_number]
        return list(dict.fromkeys((*owned, *self.bound_validations[key, round_number])))

    def slice_findings(self, slice_id: str) -> list[tuple[str, FindingTransitionPayload]]:
        def belongs(origin: str | None) -> bool:
            if origin is None:
                return False
            if origin.isdigit() and slice_id.isdigit():
                return int(origin) == int(slice_id)
            return origin == slice_id

        return sorted(((fid, opening) for fid, opening in self.openings.items() if belongs(opening.origin_slice_id)), key=lambda item: finding_id_sort_key(item[0]))

    def finding_status(self, finding_id: str) -> tuple[str, str]:
        events = self.finding_events[finding_id]
        latest = events[-1]
        status = _finding_stand(self.final_statuses.get(finding_id, latest.finding_status))
        if status == "offen" and any(item.action == "escalated" for item in events):
            status = "eskaliert"
        return ("Blocker" if latest.severity.value == "BLOCKER" else "Befund", status)

    def round_history(self, slice_id: str) -> str:
        lines: list[str] = []
        for key, unit in self.slice_units(slice_id):
            reviews = self.round_reviews(key, unit.round_number)
            validations = self.round_validations(key, unit.round_number)
            new = sum(1 for fid, item in self.slice_findings(slice_id) if any(event.action == "opened" and event.work_unit_id == key and self.transition_round.get(id(event)) == unit.round_number for event in self.finding_events[fid]))
            closed = sum(1 for events in self.finding_events.values() for event in events if event.action == "status_changed" and _finding_stand(event.finding_status) == "geschlossen" and event.work_unit_id == key and self.transition_round.get(id(event)) == unit.round_number)
            validation = ", ".join(dict.fromkeys(_outcome(result.outcome) for att in validations for result in att.results)) or "noch nicht vorhanden"
            verdict = _verdict(reviews[-1].verdict) if reviews else "noch kein Prüfurteil"
            planning = self.replay.run_identity is not None and self.replay.run_identity.execution_mode == "PLAN_ONLY"
            activity = ("Planrevision" if unit.correction else "Planung") if planning else ("Korrektur" if unit.correction else "Umsetzung")
            lines.append(f"- Runde {unit.round_number}: {activity} · Validierung {validation} · Prüfurteil {verdict} · {new} neu, {closed} geschlossen.")
            for event in self.gates[key]:
                if self.gate_round.get(id(event)) != unit.round_number:
                    continue
                if isinstance(event, ScopeExtensionPayload):
                    paths = ", ".join(item.path for item in event.additions)
                    lines.append(f"- Runde {unit.round_number}: Umfang erweitert um {paths}. Grund: {_cell(event.rationale)}")
                elif isinstance(event, GateTransitionPayload):
                    if event.gate_status == "clear":
                        continue
                    lines.append(f"- Runde {unit.round_number}: Halt ({_gate_reason(event.reason)}). Grund: {_cell(_gate_detail(event.reason, event.detail))}")
            for review in reviews:
                if review.stop_request is not None:
                    lines.append(f"- Runde {unit.round_number}: Stopp des Prüfers. Grund: {_cell(review.stop_request.rationale)}")
        return "\n".join(lines) if lines else "Noch keine Runde."

    def findings_detail(self, slice_id: str) -> str:
        sections: list[str] = []
        for fid, opening in self.slice_findings(slice_id):
            kind, status = self.finding_status(fid)
            pieces = [f"### {fid} – {_title(opening.summary or opening.rationale)}", "", f"Klasse: {kind} · Stand: {status}", "", "Befund:", _quote(opening.rationale), "", "Akzeptanztest:", _quote(opening.acceptance_test or "Nicht angegeben.")]
            for event in self.finding_events[fid]:
                if event.action == "responded":
                    round_number = self.transition_round.get(id(event), event.origin_round_number or "?")
                    decision = "angenommen" if event.response_decision == "accepted" else "abgelehnt" if event.response_decision == "rejected" else "beantwortet"
                    pieces.extend(("", f"Antwort des Implementierers, Runde {round_number} ({decision}):", _quote(event.rationale)))
                elif event.action == "escalated":
                    pieces.extend(("", "Eskalation zum Blocker:", _quote(event.rationale)))
                elif event.action == "status_changed" and _finding_stand(event.finding_status) == "geschlossen":
                    pieces.extend(("", "Abschlussbegründung des Prüfers:", _quote(event.closure_evidence or event.rationale)))
            sections.append("\n".join(pieces))
        return "\n\n".join(sections) if sections else "Keine."

    def validation_detail(self, slice_id: str) -> str:
        sections: list[str] = []
        for key, unit in self.slice_units(slice_id):
            for attestation in self.round_validations(key, unit.round_number):
                content_record = self.by_id.get(attestation.content_record_id)
                content = content_record.payload if content_record is not None else None
                outputs = content.outputs if isinstance(content, ValidationContentPayload) else ()
                for index, result in enumerate(attestation.results):
                    command = shlex.join(result.command.argv)
                    lines = [f"- Runde {unit.round_number}: `{_prose(command)}` · {_outcome(result.outcome)} · Exitcode {result.exit_code}."]
                    if result.outcome != "pass" and index < len(outputs):
                        raw = self.read_blob(outputs[index].compact_output).decode("utf-8", "replace")
                        tail = _prose(raw).splitlines()[-_MAX_OUTPUT_LINES:]
                        fence = "`" * max(3, max((len(match.group()) for line in tail for match in re.finditer(r"`+", line)), default=0) + 1)
                        lines.extend(("", fence, *tail, fence))
                    sections.append("\n".join(lines))
        return "\n\n".join(sections) if sections else "Noch keine Validierung."

    def approval_detail(self, slice_id: str) -> str:
        approved = [review for key, unit in self.slice_units(slice_id) for review in self.round_reviews(key, unit.round_number) if review.verdict == "approved"]
        if not approved:
            return "Noch nicht freigegeben."
        review = approved[-1]
        if review.review_evidence is None:
            return _quote(review.evidence or "Prüfnachweis nicht verfügbar.")
        evidence = review.review_evidence
        return "\n\n".join((
            "Geprüft:\n" + _quote(evidence.dimensions),
            "Größtes Restrisiko:\n" + _quote(evidence.largest_residual_risk),
            "Bruchbedingung:\n" + _quote(evidence.break_condition),
            "Vorab-Risikoanalyse:\n" + _quote(review.pre_mortem or "Nicht angegeben."),
        ))


def _outcome(raw: str) -> str:
    return {"pass": "grün", "fail": "rot", "unavailable": "nicht verfügbar"}.get(raw, "nicht verfügbar")


def _finding_stand(raw: str) -> str:
    return {"open": "offen", "closed": "geschlossen"}.get(raw, "offen")


def _verdict(raw: str) -> str:
    return {"approved": "freigegeben", "denied": "abgelehnt", "stop": "gestoppt"}.get(raw, "offen")


def _gate_reason(raw: str) -> str:
    return {
        "stop_request": "Stoppanforderung",
        "scope_extension": "Umfangserweiterung",
        "quota_resume_diff": "geänderter Stand bei Wiederaufnahme",
    }.get(raw, raw.replace("_", " "))


def _gate_detail(reason: str, detail: str | None) -> str:
    if detail is None:
        return _gate_reason(reason)
    if reason == "stop_request" and " | " in detail:
        return detail.split(" | ", 1)[1]
    return detail


def render_slice(facts: AuditFacts, slice_id: int, *, implementation: str = "Noch nicht dokumentiert.", deviations: str = "Keine.") -> str:
    sid = str(slice_id)
    spec = next((item for item in facts.plan.slices if item.slice_id == sid), None) if facts.plan else None
    title, goal, acceptance = _slice_section(facts.plan_text, slice_id) if facts.plan else ("", "", "")
    title = title or (spec.summary if spec is not None else f"Slice {slice_id}")
    goal = goal or ("Im freigegebenen Plan nicht angegeben." if facts.plan else "Kein freigegebener Plan vorhanden.")
    acceptance = acceptance or ("Im freigegebenen Plan nicht angegeben." if facts.plan else "Kein freigegebener Plan vorhanden.")
    paths = spec.paths if spec is not None else next((record.payload.paths for record in facts.records if isinstance(record.payload, (WorkUnitPayload, CorrectionWorkUnitPayload)) and record.payload.slice_id == sid), ())
    scope = "\n".join(f"- `{path}`" for path in paths) if paths else "Kein freigegebener Plan vorhanden."
    units = facts.slice_units(sid)
    reviews = [review for key, unit in units for review in facts.round_reviews(key, unit.round_number)]
    approved = next(((key, unit, review) for key, unit in reversed(units) for review in reversed(facts.round_reviews(key, unit.round_number)) if review.verdict == "approved"), None)
    findings = facts.slice_findings(sid)
    if approved is not None:
        closed = sum(1 for fid, _ in findings if facts.finding_status(fid)[1] == "geschlossen")
        validation = [result.outcome for att in facts.round_validations(approved[0], approved[1].round_number) for result in att.results]
        status = f"Freigegeben in Runde {approved[1].round_number} · {len(findings)} Befund{'e' if len(findings) != 1 else ''}, {closed} geschlossen · Validierung {'grün' if validation and all(item == 'pass' for item in validation) else 'rot' if validation else 'nicht verfügbar'}"
    else:
        status = f"In Arbeit · Runde {units[-1][1].round_number}" if units else "Noch nicht begonnen"
    reference = f"Technischer Bezug: Lauf `{facts.records[0].run_id}`, Arbeitseinheit(en) {', '.join(dict.fromkeys(key for key, _ in units)) or 'keine'}; Nachweise in der Recordkette."
    bodies = {
        "status": status, "reference": reference,
        "goal": goal, "acceptance": acceptance, "scope": scope,
        "history": facts.round_history(sid), "findings": facts.findings_detail(sid),
        "validation": facts.validation_detail(sid), "approval": facts.approval_detail(sid),
    }
    pieces = [f"# Slice {slice_id} von {len(facts.plan.slices) if facts.plan else '?'} – {title}", ""]
    for key, heading in SLICE_SECTIONS:
        if key == "reference":
            from audit_document_contract import marker
            pieces.append("\n".join((marker(key, "begin"), bodies[key], marker(key, "end"), "")))
        else:
            pieces.append(managed_section(key, heading, body=bodies[key]))
        if key == "scope":
            pieces.extend(("## Umsetzung", "", _quote(implementation.strip() or "Noch nicht dokumentiert."), "", "## Abweichungen vom Plan", "", _quote(deviations.strip() or "Keine."), ""))
    return "\n".join(pieces).rstrip() + "\n"


def render_plan_appendix(facts: AuditFacts) -> str:
    # An IMPLEMENT chain carries the approved plan as one record but does not
    # contain the preceding PLAN_ONLY review chain. Keep that absence explicit.
    if facts.replay.run_identity is not None and facts.replay.run_identity.execution_mode != "PLAN_ONLY":
        bodies = {"history": "Planprüfung liegt im Planungslauf.", "findings": "Planbefunde liegen im Planungslauf.", "approval": "Freigabe im Planungslauf; Nachweise in dessen Recordkette."}
    else:
        bodies = {"history": facts.round_history("1"), "findings": facts.findings_detail("1"), "approval": facts.approval_detail("1")}
    parts = [f"## {PLAN_APPENDIX_HEADING}", ""]
    for key, heading in PLAN_SECTIONS:
        parts.append(managed_section(key, heading, level=3, body=bodies[key]))
    return "\n".join(parts).rstrip() + "\n"


def render_overall(facts: AuditFacts, *, task: str, branch: str) -> str:
    completion = next((record.payload.outcome for record in reversed(facts.records) if isinstance(record.payload, WorkflowCompletionPayload)), None)
    stand = "abgeschlossen" if completion == "completed" else "fehlgeschlagen" if completion in {"failed", "stopped"} else "läuft"
    overview = ["| Slice | Titel | Stand | Commit | Runden | Befunde |", "|---:|---|---|---|---:|---:|"]
    specs = facts.plan.slices if facts.plan else tuple(
        SimpleNamespace(slice_id=sid, summary=f"Slice {sid}")
        for sid in sorted({unit.slice_id for _, unit in facts.unit_rounds}, key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
    )
    for spec in specs:
        units = facts.slice_units(spec.slice_id)
        reviews = [review for key, unit in units for review in facts.round_reviews(key, unit.round_number)]
        status = "freigegeben" if any(review.verdict == "approved" for review in reviews) else "in Arbeit" if units else "ausstehend"
        commit = facts.commits.get(spec.slice_id, "–")
        if commit != "–":
            commit = commit[:8]
        overview.append(f"| {spec.slice_id} | {_cell(spec.summary)} | {status} | {commit} | {len(units)} | {len(facts.slice_findings(spec.slice_id))} |")
    if not specs:
        overview.append("| – | Noch kein freigegebener Plan | – | – | – | – |")
    finding_rows = ["| ID | Herkunft | Klasse | Stand | Titel |", "|---|---|---|---|---|"]
    for fid, opening in sorted(facts.openings.items(), key=lambda item: finding_id_sort_key(item[0])):
        kind, state = facts.finding_status(fid)
        origin_id = str(int(opening.origin_slice_id)) if opening.origin_slice_id and opening.origin_slice_id.isdigit() else opening.origin_slice_id
        origin = "Plan" if facts.replay.run_identity is not None and facts.replay.run_identity.execution_mode == "PLAN_ONLY" else f"Slice {origin_id}" if origin_id else "Plan"
        finding_rows.append(f"| {fid} | {origin} | {kind} | {state} | {_title(opening.summary or opening.rationale)} |")
    if facts.final_review:
        for item in facts.final_review.new_findings:
            finding_rows.append(f"| {item.finding_id} | Abnahme | {'Blocker' if item.severity.value == 'BLOCKER' else 'Befund'} | offen | {_cell(item.summary)} |")
    if len(finding_rows) == 2:
        finding_rows.append("| – | – | – | – | Keine. |")
    holds = []
    for key in dict.fromkeys((*facts.gates, *facts.gate_decisions)):
        events = facts.gates[key]
        decisions = iter(facts.gate_decisions[key])
        for index, event in enumerate(events):
            if isinstance(event, GateTransitionPayload):
                if event.gate_status == "clear":
                    continue
                decision = next(decisions, None)
                following = next((item for item in events[index + 1:] if isinstance(item, GateTransitionPayload)), None)
                resumed = following is not None and following.gate_status == "clear"
                verdict = _verdict(decision.decision) if decision is not None else "fortgesetzt" if resumed else "ausstehend"
                rationale = _cell(decision.rationale) if decision is not None else "Keine gesonderte Entscheidung aufgezeichnet." if resumed else "Noch keine Entscheidung."
                holds.append(f"- Arbeitseinheit {key}: Anlass {_gate_reason(event.reason)}. Grund: {_cell(_gate_detail(event.reason, event.detail))} Entscheidung: {verdict}. Begründung: {rationale}")
            elif isinstance(event, ScopeExtensionPayload):
                holds.append(f"- Arbeitseinheit {key}: Anlass Umfangserweiterung. Grund: {_cell(event.rationale)} Entscheidung: freigegeben.")
        for decision in decisions:
            holds.append(f"- Arbeitseinheit {key}: Anlass {_gate_reason(decision.gate_kind)}. Grund: Entscheidung ohne aufgezeichneten Halt. Entscheidung: {_verdict(decision.decision)}. Begründung: {_cell(decision.rationale)}")
    acceptance = "Noch kein Abnahmereview."
    if facts.final_review:
        final = facts.final_review
        new = []
        for item in final.new_findings:
            new.extend((f"### {item.finding_id} – {_cell(item.summary)}", "", f"Klasse: {'Blocker' if item.severity.value == 'BLOCKER' else 'Befund'} · Stand: offen", "", "Befund:", _quote(item.summary), "", "Akzeptanztest:", _quote(item.acceptance_test), ""))
        acceptance = "\n".join(("Abnahmereview abgeschlossen.", "", "Geprüft:", _quote(final.review_evidence.dimensions), "", "Größtes Restrisiko:", _quote(final.review_evidence.largest_residual_risk), "", "Bruchbedingung:", _quote(final.review_evidence.break_condition), "", "Vorab-Risikoanalyse:", _quote(final.pre_mortem), "", *new)).strip()
    bodies = {"meta": f"Aufgabe: {task} · Zielbranch: `{branch}` · Lauf: `{facts.records[0].run_id}` · Stand: {stand}", "overview": "\n".join(overview), "findings": "\n".join(finding_rows), "holds": "\n".join(holds) if holds else "Keine.", "acceptance-review": acceptance}
    pieces = [f"# Gesamtaudit – {task}", ""]
    for key, heading in OVERALL_SECTIONS:
        pieces.append(managed_section(key, heading, body=bodies[key]))
    return "\n".join(pieces).rstrip() + "\n"


def read_approved_plan(repository_root: object, commit: str, path: str) -> str:
    """Read only the plan bytes bound by the approved Plan record."""
    import subprocess
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"], cwd=repository_root,
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise ReadableAuditError("approved plan cannot be read at its bound commit")
    return result.stdout.decode("utf-8")


def authored_slice_sections(markdown: str) -> tuple[str, str]:
    """Preserve the two author-owned bodies across projection updates."""
    def body(heading: str, fallback: str) -> str:
        match = re.search(rf"(?m)^## {re.escape(heading)}[ \t]*$", markdown)
        if match is None:
            return fallback
        after = markdown[match.end():]
        end = re.search(r"(?m)^## [^\n]+$", after)
        extracted = after[:end.start() if end else len(after)].strip()
        if extracted and all(not line.strip() or line.startswith(">") for line in extracted.splitlines()):
            extracted = "\n".join(line[2:] if line.startswith("> ") else line[1:] if line.startswith(">") else line for line in extracted.splitlines())
        return extracted or fallback
    return body("Umsetzung", body("Durchgeführte Änderungen", "Noch nicht dokumentiert.")), body("Abweichungen vom Plan", "Keine.")
