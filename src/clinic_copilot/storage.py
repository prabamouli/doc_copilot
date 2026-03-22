from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from clinic_copilot.config import settings
from clinic_copilot.schemas import (
    AuditLogEntry,
    CaseRecord,
    ClinicalNoteRequest,
    ClinicalNoteResponse,
    ExtractedFact,
    NoteAmendmentRequest,
    ReviewDecisionRequest,
)


def _db_path() -> Path:
    raw = settings.database_url.removeprefix("sqlite:///")
    return Path(raw).resolve()


@contextmanager
def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_db_path())
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


class ClinicRepository:
    def __init__(self) -> None:
        self._ensure_db()

    def _ensure_db(self) -> None:
        db_file = _db_path()
        db_file.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    patient_label TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    visit_context_json TEXT NOT NULL,
                    note_json TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    clinician_feedback TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def create_case(
        self,
        request: ClinicalNoteRequest,
        note: ClinicalNoteResponse,
        patient_label: str = "Demo Patient",
        actor: str = "system",
    ) -> CaseRecord:
        case_id = uuid4().hex[:12]
        timestamp = _now()
        note.case_id = case_id
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO cases (
                    case_id, patient_label, transcript, visit_context_json, note_json,
                    review_status, clinician_feedback, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    patient_label,
                    request.transcript,
                    request.visit_context.model_dump_json(),
                    note.model_dump_json(),
                    "pending_review",
                    "",
                    timestamp,
                    timestamp,
                ),
            )
        self.log_event(case_id, "case_created", actor, "Clinical note generated")
        return self.get_case(case_id)

    def seed_demo_case(self, request: ClinicalNoteRequest, note: ClinicalNoteResponse) -> CaseRecord:
        existing = self.list_cases(limit=1)
        if existing:
            return existing[0]
        return self.create_case(request, note, patient_label="Seeded Demo Patient", actor="demo_seed")

    def seed_demo_cases(self, demo_cases: list[tuple[str, ClinicalNoteRequest, ClinicalNoteResponse]]) -> list[CaseRecord]:
        existing = self.list_cases(limit=50)
        if existing:
            return existing

        created_cases: list[CaseRecord] = []
        for patient_label, request, note in demo_cases:
            created_cases.append(self.create_case(request, note, patient_label=patient_label, actor="demo_seed"))
        return created_cases

    def list_cases(self, limit: int = 20) -> list[CaseRecord]:
        with _connect() as connection:
            rows = connection.execute(
                "SELECT * FROM cases ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_case(row) for row in rows]

    def get_case(self, case_id: str) -> CaseRecord:
        with _connect() as connection:
            row = connection.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        return self._row_to_case(row)

    def review_case(self, case_id: str, review: ReviewDecisionRequest) -> CaseRecord:
        self.get_case(case_id)
        updated_at = _now()
        with _connect() as connection:
            connection.execute(
                """
                UPDATE cases
                SET review_status = ?, clinician_feedback = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (review.status, review.clinician_feedback, updated_at, case_id),
            )
        self.log_event(
            case_id,
            "review_submitted",
            review.reviewed_by,
            f"Status={review.status}; Feedback={review.clinician_feedback or 'none'}",
        )
        return self.get_case(case_id)

    def amend_case(self, case_id: str, amendment: NoteAmendmentRequest) -> CaseRecord:
        case = self.get_case(case_id)
        note = case.note.model_copy(deep=True)
        note.summary = amendment.note.summary
        note.soap_note.subjective.text = amendment.note.subjective
        note.soap_note.objective.text = amendment.note.objective
        note.soap_note.assessment.text = amendment.note.assessment
        note.soap_note.plan.text = amendment.note.plan
        note.entities.symptoms = _facts_from_values(amendment.note.symptoms)
        note.entities.duration = _facts_from_values(amendment.note.duration)
        note.entities.severity = _facts_from_values(amendment.note.severity)
        note.entities.medical_history = _facts_from_values(amendment.note.medical_history)
        note.entities.medications = _facts_from_values(amendment.note.medications)
        note.entities.allergies = _facts_from_values(amendment.note.allergies)
        note.entities.vitals = _facts_from_values(amendment.note.vitals)
        updated_at = _now()
        with _connect() as connection:
            connection.execute(
                """
                UPDATE cases
                SET note_json = ?, review_status = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (note.model_dump_json(), "pending_review", updated_at, case_id),
            )
        self.log_event(
            case_id,
            "note_amended",
            amendment.edited_by,
            f"Reason={amendment.reason or 'not provided'}",
        )
        return self.get_case(case_id)

    def list_audit_logs(self, case_id: str | None = None, limit: int = 50) -> list[AuditLogEntry]:
        with _connect() as connection:
            if case_id:
                rows = connection.execute(
                    "SELECT * FROM audit_logs WHERE case_id = ? ORDER BY id DESC LIMIT ?",
                    (case_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [AuditLogEntry.model_validate(dict(row)) for row in rows]

    def log_event(self, case_id: str, event_type: str, actor: str, details: str) -> None:
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs (case_id, event_type, actor, details, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (case_id, event_type, actor, details, _now()),
            )

    def _row_to_case(self, row: sqlite3.Row) -> CaseRecord:
        visit_context = json.loads(row["visit_context_json"])
        note = json.loads(row["note_json"])
        return CaseRecord.model_validate(
            {
                "case_id": row["case_id"],
                "patient_label": row["patient_label"],
                "transcript": row["transcript"],
                "visit_context": visit_context,
                "note": note,
                "review_status": row["review_status"],
                "clinician_feedback": row["clinician_feedback"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _facts_from_values(values: list[str]) -> list[ExtractedFact]:
    cleaned_values = [value.strip() for value in values if value.strip()]
    return [
        ExtractedFact(
            value=value,
            status="inferred",
            confidence="high",
            evidence=[],
        )
        for value in cleaned_values
    ]
