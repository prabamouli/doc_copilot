from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clinic_copilot.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AgentSummary,
    CaseRecord,
    ClinicalNoteRequest,
)
from clinic_copilot.service import ClinicalDocumentationService


class ProjectAgentRegistry:
    def __init__(self, agents_dir: Path) -> None:
        self._agents_dir = agents_dir

    def list_agents(self) -> list[AgentSummary]:
        summaries: list[AgentSummary] = []
        for path in sorted(self._agents_dir.glob("*/agent.json")):
            payload = json.loads(path.read_text())
            agent = payload["agent"]
            summaries.append(
                AgentSummary(
                    id=agent["id"],
                    name=agent["name"],
                    description=agent["description"],
                    version=agent["version"],
                )
            )
        return summaries

    def get_agent_payload(self, agent_id: str) -> dict[str, Any]:
        path = self._agents_dir / agent_id / "agent.json"
        if not path.exists():
            raise KeyError(agent_id)
        return json.loads(path.read_text())


class ProjectAgentRunner:
    def __init__(self, registry: ProjectAgentRegistry, service: ClinicalDocumentationService) -> None:
        self._registry = registry
        self._service = service

    def list_agents(self) -> list[AgentSummary]:
        return self._registry.list_agents()

    def run(self, agent_id: str, request: AgentRunRequest) -> AgentRunResponse:
        metadata = self._registry.get_agent_payload(agent_id)["agent"]

        if agent_id == "clinical_intake_agent":
            return AgentRunResponse(
                agent_id=agent_id,
                agent_name=metadata["name"],
                result=self._run_clinical_intake(request),
            )
        if agent_id == "note_safety_reviewer":
            return AgentRunResponse(
                agent_id=agent_id,
                agent_name=metadata["name"],
                result=self._run_note_safety_reviewer(request),
            )
        if agent_id == "review_queue_orchestrator":
            return AgentRunResponse(
                agent_id=agent_id,
                agent_name=metadata["name"],
                result=self._run_review_queue_orchestrator(),
            )

        raise KeyError(agent_id)

    def _run_clinical_intake(self, request: AgentRunRequest) -> dict[str, Any]:
        transcript = request.transcript or request.context.get("transcript")
        if not transcript:
            raise ValueError("clinical_intake_agent requires transcript input")

        note_request = ClinicalNoteRequest(
            transcript=transcript,
            visit_context=request.visit_context,
            include_differential_diagnosis=request.include_differential_diagnosis,
        )
        case = self._service.generate_note(note_request)
        return {
            "case_id": case.case_id,
            "patient_label": case.patient_label,
            "summary": case.note.summary,
            "review_status": case.review_status,
            "entities": case.note.entities.model_dump(),
            "soap_note": case.note.soap_note.model_dump(),
            "review_flags": [flag.model_dump() for flag in case.note.review_flags],
        }

    def _run_note_safety_reviewer(self, request: AgentRunRequest) -> dict[str, Any]:
        case = self._resolve_case(request)
        findings = [
            {
                "issue": flag.issue,
                "severity": flag.severity,
                "recommendation": flag.recommendation,
            }
            for flag in case.note.review_flags
        ]
        if not findings:
            findings.append(
                {
                    "issue": "No major review flags generated",
                    "severity": "info",
                    "recommendation": "Clinician should still confirm transcript alignment before sign-off.",
                }
            )
        return {
            "case_id": case.case_id,
            "patient_label": case.patient_label,
            "valid": not any(item["severity"] == "critical" for item in findings),
            "issues": findings,
        }

    def _run_review_queue_orchestrator(self) -> dict[str, Any]:
        ranked = sorted(
            self._service.list_cases(),
            key=lambda case: (
                case.review_status != "pending_review",
                self._highest_severity_rank(case),
                case.updated_at,
            ),
        )
        return {
            "queue_size": len(ranked),
            "ranked_cases": [
                {
                    "case_id": case.case_id,
                    "patient_label": case.patient_label,
                    "review_status": case.review_status,
                    "top_issue": case.note.review_flags[0].issue if case.note.review_flags else "No major issue flagged",
                    "recommended_action": self._recommended_action(case),
                }
                for case in ranked
            ],
        }

    def _resolve_case(self, request: AgentRunRequest) -> CaseRecord:
        case_id = request.case_id or request.context.get("case_id")
        if not case_id:
            raise ValueError("Agent request requires case_id")
        return self._service.get_case(case_id)

    def _highest_severity_rank(self, case: CaseRecord) -> int:
        rank_map = {"critical": 0, "warning": 1, "info": 2}
        if not case.note.review_flags:
            return 3
        return min(rank_map.get(flag.severity, 3) for flag in case.note.review_flags)

    def _recommended_action(self, case: CaseRecord) -> str:
        if case.review_status == "needs_changes":
            return "Review clinician feedback and amend the note before approval."
        if case.note.review_flags:
            return "Address flagged documentation gaps before sign-off."
        return "Ready for clinician review and approval."
