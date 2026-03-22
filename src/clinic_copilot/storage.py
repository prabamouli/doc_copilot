from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from clinic_copilot.config import settings
from clinic_copilot.schemas import (
    AuditLogEntry,
    CaseRecord,
    ConversationCaptureEntry,
    ClinicalNoteRequest,
    ClinicalNoteResponse,
    ExtractedFact,
    NoteAmendmentRequest,
    ReviewDecisionRequest,
)


_EMBEDDER: Any | None = None
_EMBEDDER_MODEL_NAME: str | None = None


def _db_path() -> Path:
    raw = settings.database_url.removeprefix("sqlite:///")
    return Path(raw).resolve()


def _vector_db_url() -> str:
    explicit = settings.vector_database_url.strip()
    if explicit:
        return explicit
    if settings.database_url.startswith(("postgres://", "postgresql://")):
        return settings.database_url
    return ""


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
        self._vector_db_url = _vector_db_url()
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    text TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS clinical_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id TEXT NOT NULL,
                    visit_id TEXT NOT NULL,
                    text_chunk TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_clinical_embeddings_patient
                ON clinical_embeddings (patient_id)
                """
            )

        self._ensure_pgvector_table()

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

        try:
            self.store_note_chunks(
                patient_id=_patient_id_from_label(patient_label),
                visit_id=case_id,
                note_text=_note_to_text(note),
            )
        except Exception:
            # Vector indexing failures should not block primary case persistence.
            pass

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

        try:
            self.store_note_chunks(
                patient_id=_patient_id_from_label(case.patient_label),
                visit_id=case_id,
                note_text=_note_to_text(note),
            )
        except Exception:
            pass

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

    def capture_conversation(self, case_id: str, captures: list[tuple[str, str]]) -> int:
        if not captures:
            return 0
        captured_at = _now()
        with _connect() as connection:
            connection.executemany(
                """
                INSERT INTO conversation_captures (case_id, speaker, text, captured_at)
                VALUES (?, ?, ?, ?)
                """,
                [(case_id, speaker, text, captured_at) for speaker, text in captures],
            )
        self.log_event(case_id, "conversation_capture", "system", f"Captured {len(captures)} entries")
        return len(captures)

    def list_conversation_captures(self, case_id: str, limit: int = 100) -> list[ConversationCaptureEntry]:
        with _connect() as connection:
            rows = connection.execute(
                """
                SELECT id, case_id, speaker, text, captured_at
                FROM conversation_captures
                WHERE case_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (case_id, limit),
            ).fetchall()
        return [ConversationCaptureEntry.model_validate(dict(row)) for row in rows]

    def store_note_chunks(self, patient_id: str, visit_id: str, note_text: str) -> int:
        chunks = _semantic_chunks(note_text, patient_id=patient_id)
        if not chunks:
            return 0

        embeddings = _generate_embeddings(chunks)
        timestamp = _now()

        if self._vector_db_url.startswith(("postgres://", "postgresql://")):
            return self._store_note_chunks_pgvector(
                patient_id=patient_id,
                visit_id=visit_id,
                chunks=chunks,
                embeddings=embeddings,
            )

        with _connect() as connection:
            connection.execute(
                "DELETE FROM clinical_embeddings WHERE patient_id = ? AND visit_id = ?",
                (patient_id, visit_id),
            )
            connection.executemany(
                """
                INSERT INTO clinical_embeddings (patient_id, visit_id, text_chunk, embedding_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        patient_id,
                        visit_id,
                        chunk,
                        json.dumps(embedding),
                        timestamp,
                    )
                    for chunk, embedding in zip(chunks, embeddings)
                ],
            )
        return len(chunks)

    def search_note_chunks(self, patient_id: str, query_text: str, top_k: int = 5) -> list[dict[str, Any]]:
        if not query_text.strip():
            return []

        try:
            query_embedding = _generate_embeddings([query_text])[0]
        except Exception:
            return []

        if self._vector_db_url.startswith(("postgres://", "postgresql://")):
            return self._search_note_chunks_pgvector(
                patient_id=patient_id,
                query_embedding=query_embedding,
                top_k=top_k,
            )

        with _connect() as connection:
            rows = connection.execute(
                """
                SELECT visit_id, text_chunk, embedding_json
                FROM clinical_embeddings
                WHERE patient_id = ?
                """,
                (patient_id,),
            ).fetchall()

        scored: list[tuple[float, str, str]] = []
        for row in rows:
            try:
                embedding = json.loads(row["embedding_json"])
                score = _cosine_similarity(query_embedding, embedding)
            except Exception:
                continue
            scored.append((score, row["visit_id"], row["text_chunk"]))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"visit_id": visit_id, "text_chunk": text_chunk, "score": round(score, 6)}
            for score, visit_id, text_chunk in scored[: max(1, top_k)]
        ]

    def list_note_chunks(self, patient_id: str, limit: int = 500) -> list[dict[str, Any]]:
        if self._vector_db_url.startswith(("postgres://", "postgresql://")):
            try:
                import psycopg
            except Exception as exc:
                raise RuntimeError("psycopg is required for pgvector chunk listing") from exc

            with psycopg.connect(self._vector_db_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT visit_id, text_chunk, created_at
                        FROM clinical_embeddings
                        WHERE patient_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (patient_id, max(1, int(limit))),
                    )
                    rows = cursor.fetchall()

            return [
                {
                    "visit_id": row[0],
                    "text_chunk": row[1],
                    "created_at": str(row[2]),
                    "embedding": None,
                }
                for row in rows
            ]

        with _connect() as connection:
            rows = connection.execute(
                """
                SELECT visit_id, text_chunk, embedding_json, created_at
                FROM clinical_embeddings
                WHERE patient_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (patient_id, max(1, int(limit))),
            ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            try:
                embedding = json.loads(row["embedding_json"])
            except Exception:
                embedding = None
            results.append(
                {
                    "visit_id": row["visit_id"],
                    "text_chunk": row["text_chunk"],
                    "created_at": row["created_at"],
                    "embedding": embedding,
                }
            )
        return results

    def embed_query(self, query_text: str) -> list[float]:
        if not query_text.strip():
            return []
        return _generate_embeddings([query_text])[0]

    def _ensure_pgvector_table(self) -> None:
        if not self._vector_db_url.startswith(("postgres://", "postgresql://")):
            return

        try:
            import psycopg
        except Exception:
            return

        embedding_dimension = max(1, int(settings.embedding_dimension))
        with psycopg.connect(self._vector_db_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS clinical_embeddings (
                        id BIGSERIAL PRIMARY KEY,
                        patient_id TEXT NOT NULL,
                        visit_id TEXT NOT NULL,
                        text_chunk TEXT NOT NULL,
                        embedding vector({embedding_dimension}) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_clinical_embeddings_patient
                    ON clinical_embeddings (patient_id)
                    """
                )
                try:
                    cursor.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idx_clinical_embeddings_embedding
                        ON clinical_embeddings
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100)
                        """
                    )
                except Exception:
                    # Index creation may fail on small datasets or restricted roles.
                    pass
            connection.commit()

    def _store_note_chunks_pgvector(
        self,
        patient_id: str,
        visit_id: str,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> int:
        try:
            import psycopg
        except Exception as exc:
            raise RuntimeError("psycopg is required for pgvector storage") from exc

        with psycopg.connect(self._vector_db_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM clinical_embeddings WHERE patient_id = %s AND visit_id = %s",
                    (patient_id, visit_id),
                )
                for chunk, embedding in zip(chunks, embeddings):
                    cursor.execute(
                        """
                        INSERT INTO clinical_embeddings (patient_id, visit_id, text_chunk, embedding, created_at)
                        VALUES (%s, %s, %s, (%s)::vector, NOW())
                        """,
                        (patient_id, visit_id, chunk, _pgvector_literal(embedding)),
                    )
            connection.commit()
        return len(chunks)

    def _search_note_chunks_pgvector(
        self,
        patient_id: str,
        query_embedding: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        try:
            import psycopg
        except Exception as exc:
            raise RuntimeError("psycopg is required for pgvector search") from exc

        vector_literal = _pgvector_literal(query_embedding)
        with psycopg.connect(self._vector_db_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT visit_id, text_chunk, 1 - (embedding <=> (%s)::vector) AS similarity
                    FROM clinical_embeddings
                    WHERE patient_id = %s
                    ORDER BY embedding <=> (%s)::vector
                    LIMIT %s
                    """,
                    (vector_literal, patient_id, vector_literal, max(1, int(top_k))),
                )
                rows = cursor.fetchall()
        return [
            {
                "visit_id": row[0],
                "text_chunk": row[1],
                "score": float(row[2]) if row[2] is not None else 0.0,
            }
            for row in rows
        ]

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


def _patient_id_from_label(patient_label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", patient_label.lower()).strip("-")
    return normalized or "unknown-patient"


def _note_to_text(note: ClinicalNoteResponse) -> str:
    parts = [
        note.summary,
        note.soap_note.subjective.text,
        note.soap_note.objective.text,
        note.soap_note.assessment.text,
        note.soap_note.plan.text,
    ]
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _semantic_chunks(text: str, patient_id: str, max_chars: int = 420) -> list[str]:
    return SemanticChunker(max_chars=max_chars).chunk(text=text, patient_id=patient_id)


class SemanticChunker:
    def __init__(self, max_chars: int = 420, overlap_sentences: int = 1, similarity_threshold: float = 0.58) -> None:
        self._max_chars = max(180, int(max_chars))
        self._overlap_sentences = max(0, int(overlap_sentences))
        self._similarity_threshold = similarity_threshold
        self._section_markers = (
            "chief complaint",
            "history of present illness",
            "hpi",
            "review of systems",
            "ros",
            "past medical history",
            "social history",
            "family history",
            "medications",
            "allergies",
            "physical exam",
            "objective",
            "assessment",
            "plan",
        )

    def chunk(self, text: str, patient_id: str) -> list[str]:
        cleaned = " ".join(text.split())
        if not cleaned:
            return []

        sentences = self._split_sentences(cleaned)
        if not sentences:
            sentences = [cleaned[: self._max_chars]]

        embeddings: list[list[float]] = []
        try:
            embeddings = _generate_embeddings(sentences)
        except Exception:
            embeddings = []

        context_header = self._contextual_header(cleaned, patient_id)

        chunks: list[str] = []
        buffer: list[str] = []
        current_len = 0

        for index, sentence in enumerate(sentences):
            sentence_len = len(sentence)
            if buffer and (
                current_len + sentence_len + 1 > self._max_chars
                or self._is_topic_shift(sentences, embeddings, index)
            ):
                chunk_text = self._compose_chunk(context_header, buffer)
                if chunk_text:
                    chunks.append(chunk_text)

                overlap = buffer[-self._overlap_sentences :] if self._overlap_sentences else []
                buffer = list(overlap)
                current_len = sum(len(item) for item in buffer) + max(0, len(buffer) - 1)

            buffer.append(sentence)
            current_len += sentence_len + 1

        if buffer:
            chunk_text = self._compose_chunk(context_header, buffer)
            if chunk_text:
                chunks.append(chunk_text)

        deduped: list[str] = []
        seen: set[str] = set()
        for chunk in chunks:
            key = chunk.lower().strip()
            if key and key not in seen:
                deduped.append(chunk)
                seen.add(key)
        return deduped[:80]

    def _split_sentences(self, text: str) -> list[str]:
        raw_segments = re.split(
            r"(?<=[.!?])\s+|\s+(?=(?:subjective|objective|assessment|plan|social history|physical exam|family history|hpi|ros)\s*[:\-])",
            text,
            flags=re.IGNORECASE,
        )
        return [segment.strip() for segment in raw_segments if segment and segment.strip()]

    def _is_topic_shift(self, sentences: list[str], embeddings: list[list[float]], index: int) -> bool:
        if index <= 0:
            return False

        previous_sentence = sentences[index - 1].lower()
        current_sentence = sentences[index].lower()

        if any(marker in current_sentence and marker not in previous_sentence for marker in self._section_markers):
            return True

        if embeddings and len(embeddings) > index:
            similarity = _cosine_similarity(embeddings[index - 1], embeddings[index])
            return similarity < self._similarity_threshold

        return False

    def _compose_chunk(self, header: str, sentences: list[str]) -> str:
        body = " ".join(item.strip() for item in sentences if item.strip()).strip()
        if not body:
            return ""
        return f"{header} {body}".strip()

    def _contextual_header(self, text: str, patient_id: str) -> str:
        age = "unknown"
        age_match = re.search(r"\b(\d{1,3})\s*(?:yo|yrs?|years?\s*old)\b", text, flags=re.IGNORECASE)
        if age_match:
            age = age_match.group(1)

        gender = "unknown"
        lowered = text.lower()
        if any(token in lowered for token in (" female ", " woman ", " she ", " her ")):
            gender = "female"
        elif any(token in lowered for token in (" male ", " man ", " he ", " his ")):
            gender = "male"

        primary_diagnosis = "unknown"
        diagnosis_match = re.search(
            r"(?:assessment|diagnosis)\s*[:\-]\s*([^.;\n]{5,120})",
            text,
            flags=re.IGNORECASE,
        )
        if diagnosis_match:
            primary_diagnosis = diagnosis_match.group(1).strip()
        else:
            first_sentence = text.split(".")[0].strip()
            if first_sentence:
                primary_diagnosis = first_sentence[:90]

        return (
            "[Context "
            f"patient_id={patient_id}; "
            f"age={age}; "
            f"gender={gender}; "
            f"primary_diagnosis={primary_diagnosis}]"
        )


def _generate_embeddings(texts: list[str]) -> list[list[float]]:
    model = _load_embedding_model()
    vectors = model.encode(texts, normalize_embeddings=True)
    return [_fit_embedding_dimension(list(map(float, vector))) for vector in vectors]


def _load_embedding_model() -> Any:
    global _EMBEDDER, _EMBEDDER_MODEL_NAME

    if _EMBEDDER is not None and _EMBEDDER_MODEL_NAME == settings.embedding_model:
        return _EMBEDDER

    try:
        from sentence_transformers import SentenceTransformer
    except Exception as exc:
        raise RuntimeError("sentence-transformers is required for clinical embedding generation") from exc

    _EMBEDDER = SentenceTransformer(
        settings.embedding_model,
        local_files_only=not settings.allow_remote_model_downloads,
    )
    _EMBEDDER_MODEL_NAME = settings.embedding_model
    return _EMBEDDER


def _fit_embedding_dimension(vector: list[float]) -> list[float]:
    target_dim = max(1, int(settings.embedding_dimension))
    if len(vector) == target_dim:
        return vector
    if len(vector) > target_dim:
        return vector[:target_dim]
    return vector + [0.0] * (target_dim - len(vector))


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b:
        return 0.0

    size = min(len(vec_a), len(vec_b))
    if size == 0:
        return 0.0

    dot = sum(vec_a[i] * vec_b[i] for i in range(size))
    norm_a = math.sqrt(sum(vec_a[i] * vec_a[i] for i in range(size)))
    norm_b = math.sqrt(sum(vec_b[i] * vec_b[i] for i in range(size)))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _pgvector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
