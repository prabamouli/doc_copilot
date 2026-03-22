from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from clinic_copilot.agent_runtime import ProjectAgentRegistry, ProjectAgentRunner
from clinic_copilot.llm import LLMClient
from clinic_copilot.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AgentSummary,
    AuditLogEntry,
    CaseRecord,
    ClinicalNoteRequest,
    NoteAmendmentRequest,
    ReviewDecisionRequest,
)
from clinic_copilot.service import ClinicalDocumentationService
from clinic_copilot.storage import ClinicRepository

app = FastAPI(title="Clinic Copilot MVP", version="0.1.0")
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
