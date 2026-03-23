from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clinic_copilot.agent_runtime import ObserverAgent, ProjectAgentRegistry, ProjectAgentRunner
from clinic_copilot.schemas import AgentRunRequest
from clinic_copilot.service import ClinicalDocumentationService


@dataclass
class LiveBufferState:
    transcript: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ClinicalOrchestrator:
    """Coordinates end-to-end pre/during/post visit workflow for the clinic UI."""

    def __init__(
        self,
        service: ClinicalDocumentationService,
        agent_runner: ProjectAgentRunner | None = None,
        observer_agent: ObserverAgent | None = None,
    ) -> None:
        self._service = service
        self._agent_runner = agent_runner or self._build_default_agent_runner(service)
        self._observer = observer_agent or ObserverAgent()
        self._live_buffers: dict[str, LiveBufferState] = {}

    def pre_visit_briefing(self, patient_id: str, current_complaint: str, top_k: int = 5) -> dict[str, Any]:
        """Fetches RAG-backed historical context for a concise UI briefing payload."""
        payload = self._service.debug_retrieve_patient_history(
            patient_id=patient_id,
            current_complaint=current_complaint,
            top_k=max(1, int(top_k)),
        )
        return {
            "patient_id": payload.get("patient_id", patient_id),
            "current_complaint": payload.get("current_complaint", current_complaint),
            "briefing": payload.get("historical_context", "No relevant historical context found."),
            "retrieved": payload.get("retrieved", []),
        }

    def during_visit_update(
        self,
        case_id: str,
        transcript_chunk: str,
        sensitivity: str = "medium",
    ) -> dict[str, Any]:
        """Maintains a live transcript buffer and emits clinical nudges when rules are met."""
        chunk = transcript_chunk.strip()
        if not chunk:
            return {
                "case_id": case_id,
                "buffer_length": len(self._live_buffers.get(case_id, LiveBufferState()).transcript),
                "elapsed_seconds": 0,
                "nudge": None,
            }

        state = self._live_buffers.get(case_id)
        if state is None:
            state = LiveBufferState(transcript=chunk)
            self._live_buffers[case_id] = state
        else:
            state.transcript = f"{state.transcript}\n{chunk}".strip()

        elapsed = int((datetime.now(UTC) - state.started_at).total_seconds())
        nudge = self._observer.evaluate_transcript(
            transcript=state.transcript,
            elapsed_seconds=elapsed,
            sensitivity=sensitivity,
        )
        return {
            "case_id": case_id,
            "buffer_length": len(state.transcript),
            "elapsed_seconds": elapsed,
            "nudge": nudge,
        }

    def post_visit_finalize(self, case_id: str) -> dict[str, Any]:
        """Runs Scribe, Billing, and Patient agents in parallel and validates consistency."""
        case = self._service.get_case(case_id)
        request = AgentRunRequest(case_id=case.case_id, transcript=case.transcript)

        def run_scribe() -> dict[str, Any]:
            return self._agent_runner.run("clinical_intake_agent", request).result

        def run_billing() -> dict[str, Any]:
            return self._agent_runner.run("billing_optimizer_agent", request).result

        def run_patient() -> dict[str, Any]:
            return self._agent_runner.run("patient_communicator_agent", request).result

        with ThreadPoolExecutor(max_workers=3) as executor:
            scribe_future = executor.submit(run_scribe)
            billing_future = executor.submit(run_billing)
            patient_future = executor.submit(run_patient)

            scribe_result = scribe_future.result()
            billing_result = billing_future.result()
            patient_result = patient_future.result()

        validation = self._cross_check_outputs(
            soap_note_payload=_safe_get(scribe_result, "scribe", "soap_note", default={}),
            billing_payload=billing_result,
            patient_payload=patient_result,
        )

        return {
            "case_id": case_id,
            "pre_sign_validation": validation,
            "sign_allowed": bool(validation.get("sign_allowed", False)),
            "outputs": {
                "scribe": scribe_result,
                "billing": billing_result,
                "patient": patient_result,
            },
        }

    def clear_live_buffer(self, case_id: str) -> None:
        self._live_buffers.pop(case_id, None)

    def _cross_check_outputs(
        self,
        *,
        soap_note_payload: dict[str, Any],
        billing_payload: dict[str, Any],
        patient_payload: dict[str, Any],
    ) -> dict[str, Any]:
        issues: list[str] = []

        soap_text = _flatten_soap(soap_note_payload)
        patient_text = " ".join(
            [
                " ".join(_to_str_list(patient_payload.get("what_we_found", []))),
                " ".join(_to_str_list(patient_payload.get("what_you_need_to_do_next", []))),
                " ".join(_to_str_list(patient_payload.get("when_to_get_help", []))),
            ]
        ).strip()

        if not soap_text:
            issues.append("SOAP note is empty in scribe output.")

        if not patient_text:
            issues.append("Patient summary is empty.")

        matched_cpt = _to_dict_list(billing_payload.get("matched_billable_codes", []))
        matched_icd = _to_dict_list(billing_payload.get("matched_icd10_codes", []))
        if (matched_cpt or matched_icd) and "follow-up" not in patient_text.lower() and "follow up" not in patient_text.lower():
            issues.append("Billing-relevant chart content found, but patient summary lacks clear follow-up guidance.")

        overlap_ratio = _keyword_overlap_ratio(soap_text, patient_text)
        if overlap_ratio < 0.12:
            issues.append(
                "SOAP and patient summary appear weakly aligned. Please review plain-language summary for consistency."
            )

        has_revenue_leakage = bool(billing_payload.get("has_revenue_leakage", False))
        if has_revenue_leakage and "code" not in soap_text.lower():
            issues.append("Revenue leakage flagged; verify SOAP includes sufficient coding evidence details.")

        return {
            "sign_allowed": len(issues) == 0,
            "issues": issues,
            "overlap_ratio": round(overlap_ratio, 3),
            "matched_cpt_count": len(matched_cpt),
            "matched_icd10_count": len(matched_icd),
        }

    def _build_default_agent_runner(self, service: ClinicalDocumentationService) -> ProjectAgentRunner:
        agents_dir = Path(__file__).resolve().parents[2] / "agents"
        registry = ProjectAgentRegistry(agents_dir)
        return ProjectAgentRunner(registry, service)


def _safe_get(payload: dict[str, Any], *keys: str, default: Any) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return current if current is not None else default


def _to_str_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item).strip() for item in items if str(item).strip()]


def _to_dict_list(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _flatten_soap(soap_note_payload: dict[str, Any]) -> str:
    sections = []
    for name in ("subjective", "objective", "assessment", "plan"):
        value = soap_note_payload.get(name)
        if isinstance(value, dict):
            text = str(value.get("text", "")).strip()
        else:
            text = str(value or "").strip()
        if text:
            sections.append(text)
    return " ".join(sections).strip()


def _keyword_overlap_ratio(left_text: str, right_text: str) -> float:
    left = _keyword_set(left_text)
    right = _keyword_set(right_text)
    if not left or not right:
        return 0.0
    overlap = left.intersection(right)
    return len(overlap) / max(1, len(right))


def _keyword_set(text: str) -> set[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "your",
        "you",
        "need",
        "next",
        "what",
        "when",
        "will",
    }
    tokens = [token.strip().lower() for token in text.replace("\n", " ").split()]
    cleaned = {
        "".join(ch for ch in token if ch.isalnum())
        for token in tokens
        if token and len(token) >= 3
    }
    return {token for token in cleaned if token and token not in stop_words}
