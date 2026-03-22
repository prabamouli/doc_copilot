from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from clinic_copilot.agent_runtime import ObserverAgent, ProjectAgentRegistry, ProjectAgentRunner
from clinic_copilot.llm import LLMClient
from clinic_copilot.regulatory_vault import RegulatoryVaultMiddleware
from clinic_copilot.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AgentRuntimeLogEntry,
    AgentSummary,
    AuditLogEntry,
    CaseRecord,
    ClinicalNoteRequest,
    ClinicalNoteResponse,
    ConversationCaptureEntry,
    ConversationCaptureRequest,
    ConversationCaptureResult,
    LongitudinalScribeRequest,
    NoteAmendmentRequest,
    PatientHistoryDebugRequest,
    PatientHistoryDebugResponse,
    ReviewDecisionRequest,
)
from clinic_copilot.service import ClinicalDocumentationService
from clinic_copilot.storage import ClinicRepository

app = FastAPI(title="Clinic Copilot MVP", version="0.1.0")
app.add_middleware(RegulatoryVaultMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

repository = ClinicRepository()
service = ClinicalDocumentationService(LLMClient(), repository)
agent_registry = ProjectAgentRegistry(Path(__file__).resolve().parents[2] / "agents")
agent_runner = ProjectAgentRunner(agent_registry, service)
observer_agent = ObserverAgent()


@app.on_event("startup")
def startup_seed() -> None:
    service.seed_demo_case()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/demo-case", response_model=CaseRecord)
def get_demo_case() -> CaseRecord:
    try:
        return service.seed_demo_case()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load demo case: {exc}") from exc


@app.get("/v1/cases", response_model=list[CaseRecord])
def list_cases() -> list[CaseRecord]:
    return service.list_cases()


@app.get("/v1/agents", response_model=list[AgentSummary])
def list_agents() -> list[AgentSummary]:
    return agent_runner.list_agents()


@app.post("/v1/agents/{agent_id}/run", response_model=AgentRunResponse)
def run_agent(agent_id: str, request: AgentRunRequest) -> AgentRunResponse:
    try:
        return agent_runner.run(agent_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/agent-run-logs", response_model=list[AgentRuntimeLogEntry])
def list_agent_run_logs(
    run_id: str | None = Query(default=None),
    case_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[AgentRuntimeLogEntry]:
    return [
        AgentRuntimeLogEntry.model_validate(item)
        for item in agent_runner.list_runtime_audit_events(run_id=run_id, case_id=case_id, limit=limit)
    ]


@app.get("/v1/cases/{case_id}", response_model=CaseRecord)
def get_case(case_id: str) -> CaseRecord:
    try:
        return service.get_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


@app.post("/v1/clinical-note", response_model=CaseRecord)
def create_clinical_note(request: ClinicalNoteRequest) -> CaseRecord:
    try:
        return service.generate_note(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {exc}") from exc


@app.post("/v1/haystack-pipeline", response_model=ClinicalNoteResponse)
def run_haystack_pipeline(request: ClinicalNoteRequest) -> ClinicalNoteResponse:
    try:
        return service.generate_note_with_haystack(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Haystack pipeline failed: {exc}") from exc


@app.post("/v1/haystack-longitudinal-scribe", response_model=ClinicalNoteResponse)
def run_haystack_longitudinal_scribe(request: LongitudinalScribeRequest) -> ClinicalNoteResponse:
    try:
        return service.generate_longitudinal_note_from_audio(
            audio_path=request.audio_path,
            patient_id=request.patient_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Longitudinal scribe pipeline failed: {exc}") from exc


@app.post("/v1/patient-history/retrieve", response_model=PatientHistoryDebugResponse)
def retrieve_patient_history(request: PatientHistoryDebugRequest) -> PatientHistoryDebugResponse:
    try:
        payload = service.debug_retrieve_patient_history(
            patient_id=request.patient_id,
            current_complaint=request.current_complaint,
            top_k=request.top_k,
        )
        return PatientHistoryDebugResponse.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Patient history retrieval failed: {exc}") from exc


@app.post("/v1/cases/{case_id}/review", response_model=CaseRecord)
def review_case(case_id: str, review: ReviewDecisionRequest) -> CaseRecord:
    try:
        return service.review_case(case_id, review)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


@app.post("/v1/cases/{case_id}/amend", response_model=CaseRecord)
def amend_case(case_id: str, amendment: NoteAmendmentRequest) -> CaseRecord:
    try:
        return service.amend_case(case_id, amendment)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


@app.get("/v1/audit-logs", response_model=list[AuditLogEntry])
def audit_logs(case_id: str | None = Query(default=None)) -> list[AuditLogEntry]:
    return service.audit_logs(case_id=case_id)


@app.post("/v1/cases/{case_id}/conversation-capture", response_model=ConversationCaptureResult)
def capture_conversation(case_id: str, payload: ConversationCaptureRequest) -> ConversationCaptureResult:
    try:
        captured = service.capture_conversation_snapshot(case_id=case_id, transcript=payload.transcript)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    return ConversationCaptureResult(case_id=case_id, captured_count=captured)


@app.get("/v1/cases/{case_id}/conversation-capture", response_model=list[ConversationCaptureEntry])
def list_conversation_capture(case_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[ConversationCaptureEntry]:
    try:
        return service.list_conversation_captures(case_id=case_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


@app.websocket("/ws/clinical-nudges")
async def websocket_clinical_nudges(websocket: WebSocket) -> None:
    await websocket.accept()
    last_nudge_id: str | None = None
    try:
        while True:
            payload = await websocket.receive_json()
            transcript = str(payload.get("transcript", "")).strip()
            elapsed_seconds = int(payload.get("elapsed_seconds", 0))
            case_id = str(payload.get("case_id", "unknown"))

            if not transcript:
                await websocket.send_json({"type": "ack", "status": "ignored", "reason": "empty_transcript"})
                continue

            nudge = observer_agent.evaluate_transcript(transcript=transcript, elapsed_seconds=elapsed_seconds)
            if nudge is None:
                await websocket.send_json({"type": "ack", "status": "ok", "case_id": case_id})
                continue

            nudge_id = str(nudge.get("id", ""))
            if nudge_id and nudge_id == last_nudge_id:
                await websocket.send_json({"type": "ack", "status": "duplicate_suppressed", "case_id": case_id})
                continue

            last_nudge_id = nudge_id or last_nudge_id
            await websocket.send_json({"type": "clinical_nudge", "case_id": case_id, "payload": nudge})
    except WebSocketDisconnect:
        return
