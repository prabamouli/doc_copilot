from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from clinic_copilot.agent_runtime import ObserverAgent, ProjectAgentRegistry, ProjectAgentRunner
from clinic_copilot.llm import LLMClient
from clinic_copilot.logging_safety import install_phi_redaction_filter
from clinic_copilot.offline_readiness import evaluate_offline_readiness
from clinic_copilot.orchestrator import ClinicalOrchestrator
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
    OfflineReadinessResponse,
    OrchestratorDuringVisitRequest,
    OrchestratorDuringVisitResponse,
    OrchestratorPostVisitResponse,
    OrchestratorPreVisitRequest,
    OrchestratorPreVisitResponse,
    PatientTimelineSummaryRequest,
    PatientTimelineSummaryResponse,
    RagMedicalValidationRequest,
    RagMedicalValidationResponse,
    FullOutputValidationRequest,
    FullOutputValidationResponse,
    CriticReviewRequest,
    CriticReviewResponse,
    DiagnosisConfidenceScoreRequest,
    DiagnosisConfidenceScoreResponse,
    PatientFriendlySummaryRequest,
    PatientFriendlySummaryResponse,
    PrescriptionDraftRequest,
    PrescriptionDraftResponse,
    PatientHistoryDebugRequest,
    PatientHistoryDebugResponse,
    PatientAfterVisitSummaryResponse,
    ReviewDecisionRequest,
    VisionObjectiveResponse,
    VoiceCommandRequest,
    VoiceCommandResponse,
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
orchestrator = ClinicalOrchestrator(service=service, agent_runner=agent_runner, observer_agent=observer_agent)


@app.on_event("startup")
def startup_seed() -> None:
    install_phi_redaction_filter()
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


@app.post("/v1/patient-history/timeline-summary", response_model=PatientTimelineSummaryResponse)
def summarize_patient_timeline(request: PatientTimelineSummaryRequest) -> PatientTimelineSummaryResponse:
    try:
        payload = service.summarize_patient_timeline(request.past_records)
        return PatientTimelineSummaryResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Patient timeline summarization failed: {exc}") from exc


@app.post("/v1/diagnosis/rag-validate", response_model=RagMedicalValidationResponse)
def rag_validate_diagnosis(request: RagMedicalValidationRequest) -> RagMedicalValidationResponse:
    try:
        payload = service.rag_validate_diagnosis(diagnosis=request.diagnosis, context=request.context)
        return RagMedicalValidationResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"RAG diagnosis validation failed: {exc}") from exc


@app.post("/v1/validation/full-output", response_model=FullOutputValidationResponse)
def validate_full_output(request: FullOutputValidationRequest) -> FullOutputValidationResponse:
    try:
        payload = service.validate_full_clinical_output(request.full_output)
        return FullOutputValidationResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Full output validation failed: {exc}") from exc


@app.post("/v1/critic/review", response_model=CriticReviewResponse)
def critic_review(request: CriticReviewRequest) -> CriticReviewResponse:
    try:
        payload = service.critic_review_output(request.output)
        return CriticReviewResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Critic review failed: {exc}") from exc


@app.post("/v1/diagnosis/confidence-score", response_model=DiagnosisConfidenceScoreResponse)
def diagnosis_confidence_score(request: DiagnosisConfidenceScoreRequest) -> DiagnosisConfidenceScoreResponse:
    try:
        payload = service.score_diagnosis_confidence(request.diagnosis)
        return DiagnosisConfidenceScoreResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Diagnosis confidence scoring failed: {exc}") from exc


@app.post("/v1/patient-summary/friendly", response_model=PatientFriendlySummaryResponse)
def patient_friendly_summary(request: PatientFriendlySummaryRequest) -> PatientFriendlySummaryResponse:
    try:
        payload = service.generate_patient_friendly_summary(request.soap_note)
        return PatientFriendlySummaryResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Patient-friendly summary generation failed: {exc}") from exc


@app.post("/v1/prescription/generate", response_model=PrescriptionDraftResponse)
def generate_prescription_draft(request: PrescriptionDraftRequest) -> PrescriptionDraftResponse:
    try:
        payload = service.generate_prescription_draft(request.treatment)
        return PrescriptionDraftResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Prescription draft generation failed: {exc}") from exc


@app.post("/v1/orchestrator/pre-visit", response_model=OrchestratorPreVisitResponse)
def orchestrator_pre_visit(request: OrchestratorPreVisitRequest) -> OrchestratorPreVisitResponse:
    try:
        payload = orchestrator.pre_visit_briefing(
            patient_id=request.patient_id,
            current_complaint=request.current_complaint,
            top_k=request.top_k,
        )
        return OrchestratorPreVisitResponse.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Pre-visit orchestration failed: {exc}") from exc


@app.post("/v1/orchestrator/during-visit", response_model=OrchestratorDuringVisitResponse)
def orchestrator_during_visit(request: OrchestratorDuringVisitRequest) -> OrchestratorDuringVisitResponse:
    try:
        payload = orchestrator.during_visit_update(
            case_id=request.case_id,
            transcript_chunk=request.transcript_chunk,
            sensitivity=request.sensitivity,
        )
        return OrchestratorDuringVisitResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"During-visit orchestration failed: {exc}") from exc


@app.post("/v1/orchestrator/post-visit/{case_id}", response_model=OrchestratorPostVisitResponse)
def orchestrator_post_visit(case_id: str) -> OrchestratorPostVisitResponse:
    try:
        payload = orchestrator.post_visit_finalize(case_id=case_id)
        return OrchestratorPostVisitResponse.model_validate(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Post-visit orchestration failed: {exc}") from exc


@app.get("/v1/admin/offline-readiness", response_model=OfflineReadinessResponse)
def admin_offline_readiness(prepull: bool = Query(default=False)) -> OfflineReadinessResponse:
    try:
        workspace = Path(__file__).resolve().parents[2]
        payload = evaluate_offline_readiness(workspace=workspace, prepull=prepull)
        return OfflineReadinessResponse.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Offline readiness check failed: {exc}") from exc


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


@app.get("/v1/cases/{case_id}/patient-avs", response_model=PatientAfterVisitSummaryResponse)
def get_patient_after_visit_summary(case_id: str) -> PatientAfterVisitSummaryResponse:
    try:
        payload = service.generate_patient_after_visit_summary(case_id)
        return PatientAfterVisitSummaryResponse.model_validate(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Patient summary generation failed: {exc}") from exc


@app.get("/v1/cases/{case_id}/conversation-capture", response_model=list[ConversationCaptureEntry])
def list_conversation_capture(case_id: str, limit: int = Query(default=100, ge=1, le=500)) -> list[ConversationCaptureEntry]:
    try:
        return service.list_conversation_captures(case_id=case_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Case not found") from exc


@app.post("/v1/vision-agent/analyze", response_model=VisionObjectiveResponse)
async def vision_agent_analyze(
    media_file: UploadFile = File(...),
    media_type: str = Form("image"),
) -> VisionObjectiveResponse:
    selected_type = media_type.strip().lower()
    if selected_type not in {"image", "video"}:
        content_type = str(media_file.content_type or "")
        selected_type = "video" if content_type.startswith("video/") else "image"

    suffix = Path(media_file.filename or "upload.bin").suffix or (".mp4" if selected_type == "video" else ".jpg")
    with NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(await media_file.read())

    try:
        payload = service.analyze_visual_objective(media_path=str(temp_path), media_type=selected_type)
        return VisionObjectiveResponse.model_validate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"VisionAgent analysis failed: {exc}") from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


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
            sensitivity = str(payload.get("sensitivity", "medium"))

            if not transcript:
                await websocket.send_json({"type": "ack", "status": "ignored", "reason": "empty_transcript"})
                continue

            nudge = observer_agent.evaluate_transcript(
                transcript=transcript,
                elapsed_seconds=elapsed_seconds,
                sensitivity=sensitivity,
            )
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


# ── Voice Assistant ──────────────────────────────────────────────────────────

_VOICE_INTENT_OPTIONS = ", ".join([
    "approve_note",
    "request_changes",
    "run_safety_review",
    "run_billing",
    "get_case_summary",
    "get_symptoms",
    "dictate_soap",
    "get_medical_info",
    "navigate_agents",
    "navigate_audit",
    "navigate_note_studio",
    "unknown",
])


def _build_voice_prompt(text: str, case_context: str) -> str:
    return (
        "You are a clinical voice assistant helping a doctor. "
        "Classify the spoken command and craft a concise, helpful response.\n\n"
        f"Case context:\n{case_context}\n\n"
        f"Doctor said: \"{text}\"\n\n"
        "Return a JSON object with exactly these keys:\n"
        f"- intent: one of [{_VOICE_INTENT_OPTIONS}]\n"
        "- action_code: one of [none, approve_note, request_changes, navigate_agents, navigate_audit, navigate_note_studio]\n"
        "- response_text: a single sentence to be spoken back to the clinician\n"
        "- data: an object with any relevant extracted fields (may be empty)\n\n"
        "Rules: approve_note/request_changes map to those action_codes; "
        "navigate_* intents map to their action_code; all others use action_code=none. "
        "For dictate_soap, parse the dictation into {subjective, objective, assessment, plan} fields in data."
    )


@app.post("/v1/voice-assistant/command", response_model=VoiceCommandResponse)
def voice_assistant_command(req: VoiceCommandRequest) -> VoiceCommandResponse:
    # Build case context string
    case_context = "No specific case loaded."
    case_data: dict = {}
    if req.case_id:
        try:
            case = service.get_case(req.case_id)
            note = case.note
            summary = getattr(note, "summary", "") if note else ""
            status = case.review_status or "unknown"
            flags = len(getattr(note, "review_flags", []) or []) if note else 0
            case_context = (
                f"Case ID: {case.case_id} | Status: {status} | "
                f"Patient: {case.patientLabel} | Summary: {summary[:200]} | "
                f"Review flags: {flags}"
            )
            case_data = {"case_id": case.case_id, "review_status": status}
        except KeyError:
            case_context = f"Case '{req.case_id}' not found."

    # Call LLM
    llm_client = LLMClient()
    try:
        prompt = _build_voice_prompt(req.text, case_context)
        result = llm_client._call_json(prompt, priority="Standard")  # noqa: SLF001
        intent = str(result.get("intent", "unknown"))
        action_code = str(result.get("action_code", "none"))
        response_text = str(result.get("response_text", "I didn't catch that. Could you repeat?"))
        extra_data: dict = dict(result.get("data", {}))
    except Exception:
        # Fallback: pattern-match common commands
        lowered = req.text.lower()
        if any(w in lowered for w in ("approve", "sign off", "sign the note")):
            intent, action_code = "approve_note", "approve_note"
            response_text = "Approving the current note now."
        elif any(w in lowered for w in ("change", "revision", "amend", "flag")):
            intent, action_code = "request_changes", "request_changes"
            response_text = "Flagged the note for changes."
        elif "safety" in lowered or "review" in lowered:
            intent, action_code = "run_safety_review", "none"
            response_text = "Running a safety review on this case."
        elif "billing" in lowered:
            intent, action_code = "run_billing", "none"
            response_text = "Running the billing optimizer."
        elif "symptom" in lowered:
            intent, action_code = "get_symptoms", "none"
            response_text = "Opening the symptoms panel."
        elif "agent" in lowered:
            intent, action_code = "navigate_agents", "navigate_agents"
            response_text = "Navigating to the agents workspace."
        elif "audit" in lowered or "log" in lowered:
            intent, action_code = "navigate_audit", "navigate_audit"
            response_text = "Opening the audit log."
        elif "note" in lowered or "studio" in lowered:
            intent, action_code = "navigate_note_studio", "navigate_note_studio"
            response_text = "Opening Note Studio."
        else:
            intent, action_code = "unknown", "none"
            response_text = "I didn't understand that command. Try saying 'approve note' or 'open Note Studio'."
        extra_data = {}

    # Validate intent/action_code fall within allowed literal values
    _valid_intents = {
        "approve_note", "request_changes", "run_safety_review", "run_billing",
        "get_case_summary", "get_symptoms", "dictate_soap", "get_medical_info",
        "navigate_agents", "navigate_audit", "navigate_note_studio", "unknown",
    }
    _valid_actions = {"none", "approve_note", "request_changes", "navigate_agents", "navigate_audit", "navigate_note_studio"}
    if intent not in _valid_intents:
        intent = "unknown"
    if action_code not in _valid_actions:
        action_code = "none"

    # Execute backend side-effects
    if intent == "approve_note" and req.case_id:
        try:
            from clinic_copilot.schemas import ReviewDecisionRequest as _RDR  # noqa: PLC0415
            service.review_case(req.case_id, _RDR(status="approved", reviewed_by="voice_assistant", clinician_feedback="Approved via voice command."))
            response_text = "Note approved successfully."
        except Exception as exc:
            response_text = f"Could not approve the note: {exc}"

    elif intent == "request_changes" and req.case_id:
        try:
            from clinic_copilot.schemas import ReviewDecisionRequest as _RDR  # noqa: PLC0415
            service.review_case(req.case_id, _RDR(status="needs_changes", reviewed_by="voice_assistant", clinician_feedback="Changes requested via voice command."))
            response_text = "Note flagged for changes."
        except Exception as exc:
            response_text = f"Could not flag for changes: {exc}"

    return VoiceCommandResponse(
        intent=intent,  # type: ignore[arg-type]
        response_text=response_text,
        action_code=action_code,  # type: ignore[arg-type]
        data={**case_data, **extra_data},
    )
