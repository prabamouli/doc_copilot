from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ConfidenceLevel = Literal["low", "medium", "high"]
ReviewStatus = Literal["pending_review", "approved", "needs_changes"]


class VisitContext(BaseModel):
    locale: str = Field(default="en-IN")
    specialty: str = Field(default="general_medicine")
    clinician_name: str | None = None


class ClinicalNoteRequest(BaseModel):
    transcript: str = Field(min_length=20, description="Doctor-patient conversation transcript.")
    visit_context: VisitContext = Field(default_factory=VisitContext)
    include_differential_diagnosis: bool = Field(default=False)


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
