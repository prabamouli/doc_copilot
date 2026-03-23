from pathlib import Path

from clinic_copilot.agent_runtime import ProjectAgentRegistry, ProjectAgentRunner
from clinic_copilot.llm import LLMClient
from clinic_copilot.orchestrator import ClinicalOrchestrator
from clinic_copilot.service import ClinicalDocumentationService
from clinic_copilot.storage import ClinicRepository


def main() -> None:
    repo = ClinicRepository()
    service = ClinicalDocumentationService(LLMClient(), repo)
    service.seed_demo_case()

    registry = ProjectAgentRegistry(Path(__file__).resolve().parents[1] / "agents")
    runner = ProjectAgentRunner(registry, service)
    orchestrator = ClinicalOrchestrator(service=service, agent_runner=runner)

    case = service.list_cases()[0]
    patient_id = case.patient_label.lower().replace(" ", "-")

    briefing = orchestrator.pre_visit_briefing(
        patient_id=patient_id,
        current_complaint=case.note.summary or case.transcript,
        top_k=3,
    )

    orchestrator.during_visit_update(case.case_id, "Patient: I have chest pain since morning.", sensitivity="high")
    orchestrator.during_visit_update(case.case_id, "Doctor: Tell me more about when it started.", sensitivity="high")

    # Make elapsed time large enough to hit nudge threshold deterministically.
    orchestrator._live_buffers[case.case_id].started_at = orchestrator._live_buffers[case.case_id].started_at.replace(
        year=2025
    )
    nudge_event = orchestrator.during_visit_update(
        case.case_id,
        "Patient: Pain is still there.",
        sensitivity="high",
    )

    post_visit = orchestrator.post_visit_finalize(case.case_id)

    print("SMOKE: pre_visit_retrieved=", len(briefing.get("retrieved", [])))
    print("SMOKE: nudge_triggered=", bool(nudge_event.get("nudge")))
    print("SMOKE: sign_allowed=", post_visit.get("sign_allowed"))
    print(
        "SMOKE: validation_issues=",
        len(post_visit.get("pre_sign_validation", {}).get("issues", [])),
    )
    print(
        "SMOKE: patient_items=",
        len(post_visit.get("outputs", {}).get("patient", {}).get("what_you_need_to_do_next", [])),
    )


if __name__ == "__main__":
    main()
