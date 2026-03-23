from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ConfidenceLevel = Literal["low", "medium", "high"]
NudgeSensitivity = Literal["low", "medium", "high"]
ReviewStatus = Literal["pending_review", "approved", "needs_changes"]


class VisitContext(BaseModel):
    locale: str = Field(default="en-IN")
    specialty: str = Field(default="general_medicine")
    clinician_name: str | None = None


class ClinicalNoteRequest(BaseModel):
    transcript: str = Field(min_length=20, description="Doctor-patient conversation transcript.")
    visit_context: VisitContext = Field(default_factory=VisitContext)
    include_differential_diagnosis: bool = Field(default=False)


class LongitudinalScribeRequest(BaseModel):
    audio_path: str = Field(min_length=1, description="Absolute or workspace-relative path to the audio file.")
    patient_id: str = Field(min_length=1, max_length=120, description="Patient identifier used for historical retrieval.")


class PatientHistoryDebugRequest(BaseModel):
    patient_id: str = Field(min_length=1, max_length=120)
    current_complaint: str = Field(min_length=3, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


class VisionObjectiveResponse(BaseModel):
    media_type: Literal["image", "video"]
    objective_text: str
    model: str
    confidence: ConfidenceLevel = "medium"


class PatientAfterVisitSummaryResponse(BaseModel):
    case_id: str
    audience: Literal["patient"] = "patient"
    reading_level: str = "5th_grade"
    what_we_found: list[str] = Field(default_factory=list)
    what_you_need_to_do_next: list[str] = Field(default_factory=list)
    when_to_get_help: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "This summary is for understanding your visit. If symptoms get worse, contact your clinician."
    )


class RetrievedHistoryItem(BaseModel):
    visit_id: str
    date: str
    score: float
    source: str
    text_chunk: str


class PatientHistoryDebugResponse(BaseModel):
    patient_id: str
    current_complaint: str
    historical_context: str
    retrieved: list[RetrievedHistoryItem] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    quote: str = Field(description="Exact supporting text from the transcript.")
    speaker: Literal["doctor", "patient", "unknown"] = "unknown"


class ExtractedFact(BaseModel):
    value: str
    status: Literal["supported", "unknown", "inferred"] = "supported"
    confidence: ConfidenceLevel = "medium"
    evidence: list[EvidenceItem] = Field(default_factory=list)


class ClinicalEntities(BaseModel):
    symptoms: list[ExtractedFact] = Field(default_factory=list)
    duration: list[ExtractedFact] = Field(default_factory=list)
    severity: list[ExtractedFact] = Field(default_factory=list)
    medical_history: list[ExtractedFact] = Field(default_factory=list)
    medications: list[ExtractedFact] = Field(default_factory=list)
    allergies: list[ExtractedFact] = Field(default_factory=list)
    vitals: list[ExtractedFact] = Field(default_factory=list)


class SoapSection(BaseModel):
    text: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class SoapNote(BaseModel):
    subjective: SoapSection
    objective: SoapSection
    assessment: SoapSection
    plan: SoapSection


class DifferentialDiagnosisItem(BaseModel):
    condition: str
    rationale: str
    confidence: ConfidenceLevel
    clinician_review_required: bool = True


class TreatmentPlanDraft(BaseModel):
    medications: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    advice: list[str] = Field(default_factory=list)
    follow_up: str = "unknown"


class SoapAssessmentItem(BaseModel):
    condition: str
    confidence: ConfidenceLevel
    reason: str


class SoapDraftOutput(BaseModel):
    subjective: str
    objective: str
    assessment: list[SoapAssessmentItem] = Field(default_factory=list)
    plan: TreatmentPlanDraft


class ValidationResult(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)


class ReviewFlag(BaseModel):
    issue: str
    severity: Literal["info", "warning", "critical"]
    recommendation: str


class ClinicalNoteResponse(BaseModel):
    case_id: str | None = None
    summary: str
    entities: ClinicalEntities
    soap_note: SoapNote
    differential_diagnosis: list[DifferentialDiagnosisItem] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    disclaimer: str = (
        "Clinician review required. This output supports documentation and must not be used as a standalone medical decision."
    )


class CaseRecord(BaseModel):
    case_id: str
    patient_label: str
    transcript: str
    visit_context: VisitContext
    note: ClinicalNoteResponse
    review_status: ReviewStatus = "pending_review"
    clinician_feedback: str = ""
    created_at: str
    updated_at: str


class ReviewDecisionRequest(BaseModel):
    status: Literal["approved", "needs_changes"]
    clinician_feedback: str = Field(default="", max_length=4000)
    reviewed_by: str = Field(min_length=2, max_length=120)


class NoteAmendmentPayload(BaseModel):
    summary: str = Field(min_length=10, max_length=4000)
    subjective: str = Field(min_length=5, max_length=4000)
    objective: str = Field(min_length=5, max_length=4000)
    assessment: str = Field(min_length=5, max_length=4000)
    plan: str = Field(min_length=5, max_length=4000)
    symptoms: list[str] = Field(default_factory=list)
    duration: list[str] = Field(default_factory=list)
    severity: list[str] = Field(default_factory=list)
    medical_history: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    vitals: list[str] = Field(default_factory=list)


class NoteAmendmentRequest(BaseModel):
    edited_by: str = Field(min_length=2, max_length=120)
    reason: str = Field(default="", max_length=2000)
    note: NoteAmendmentPayload


class AuditLogEntry(BaseModel):
    id: int
    case_id: str
    event_type: str
    actor: str
    details: str
    created_at: str


class AgentSummary(BaseModel):
    id: str
    name: str
    description: str
    version: str


class AgentRunRequest(BaseModel):
    transcript: str | None = None
    visit_context: VisitContext = Field(default_factory=VisitContext)
    include_differential_diagnosis: bool = False
    case_id: str | None = None
    context: dict[str, str] = Field(default_factory=dict)


class AgentRunResponse(BaseModel):
    agent_id: str
    agent_name: str
    result: dict


class AgentRuntimeLogEntry(BaseModel):
    id: int
    run_id: str
    case_id: str
    agent_id: str
    agent_name: str
    event_type: str
    payload: dict
    created_at: str


class ConversationCaptureRequest(BaseModel):
    transcript: str = Field(min_length=5, max_length=20000)


class ConversationCaptureEntry(BaseModel):
    id: int
    case_id: str
    speaker: Literal["doctor", "patient", "unknown"]
    text: str
    captured_at: str


class ConversationCaptureResult(BaseModel):
    case_id: str
    captured_count: int


class OrchestratorPreVisitRequest(BaseModel):
    patient_id: str = Field(min_length=1, max_length=120)
    current_complaint: str = Field(min_length=3, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


class OrchestratorPreVisitResponse(BaseModel):
    patient_id: str
    current_complaint: str
    briefing: str
    retrieved: list[RetrievedHistoryItem] = Field(default_factory=list)


class OrchestratorDuringVisitRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=120)
    transcript_chunk: str = Field(min_length=1, max_length=5000)
    sensitivity: NudgeSensitivity = "medium"


class OrchestratorDuringVisitResponse(BaseModel):
    case_id: str
    buffer_length: int
    elapsed_seconds: int
    nudge: dict[str, Any] | None = None


class OrchestratorPostVisitResponse(BaseModel):
    case_id: str
    sign_allowed: bool
    pre_sign_validation: dict[str, Any]
    outputs: dict[str, Any]


class OfflineReadinessCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class OfflineReadinessResponse(BaseModel):
    workspace: str
    requested_models: list[str] = Field(default_factory=list)
    database_mode: str
    checks: list[OfflineReadinessCheck] = Field(default_factory=list)
    ready: bool


VoiceCommandIntent = Literal[
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
]

VoiceActionCode = Literal[
    "none",
    "approve_note",
    "request_changes",
    "navigate_agents",
    "navigate_audit",
    "navigate_note_studio",
]


class VoiceCommandRequest(BaseModel):
    case_id: str = Field(default="")
    text: str = Field(min_length=1, max_length=2000, description="Transcribed voice command text.")


class VoiceCommandResponse(BaseModel):
    intent: VoiceCommandIntent
    response_text: str = Field(description="Human-readable response to be spoken back to the clinician.")
    action_code: VoiceActionCode = Field(default="none", description="Frontend navigation/action trigger.")
    data: dict[str, Any] = Field(default_factory=dict)
