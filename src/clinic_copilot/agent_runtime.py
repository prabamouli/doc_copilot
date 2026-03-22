from __future__ import annotations

import json
import importlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from clinic_copilot.config import settings
from clinic_copilot.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AgentSummary,
    CaseRecord,
    ClinicalNoteRequest,
    ClinicalNoteResponse,
    ReviewFlag,
)
from clinic_copilot.service import ClinicalDocumentationService


HIGH_RISK_MEDICATIONS: dict[str, str] = {
    "warfarin": "Bleeding risk requires explicit interaction review.",
    "digoxin": "Narrow therapeutic index; dosing and renal status must be verified.",
    "lithium": "Narrow therapeutic index; monitor renal and thyroid effects.",
    "methotrexate": "Requires careful toxicity monitoring and contraindication screening.",
    "insulin": "Hypoglycemia risk requires close dose and meal timing alignment.",
    "clozapine": "Requires strict hematologic monitoring and follow-up.",
    "amiodarone": "High interaction burden and organ toxicity risk.",
}


DEFAULT_CPT_CODES: list[dict[str, Any]] = [
    {
        "code": "93000",
        "procedure_key": "electrocardiogram",
        "description": "Electrocardiogram, routine ECG with at least 12 leads; with interpretation and report",
        "synonyms": ["ecg", "ekg", "electrocardiogram", "12 lead"],
        "estimated_value_usd": 45.0,
    },
    {
        "code": "71046",
        "procedure_key": "chest_xray_2_view",
        "description": "Radiologic exam, chest; 2 views",
        "synonyms": ["chest xray", "chest x-ray", "cxr", "x ray chest"],
        "estimated_value_usd": 58.0,
    },
    {
        "code": "87880",
        "procedure_key": "rapid_strep_test",
        "description": "Infectious agent antigen detection by immunoassay with direct optical observation; Streptococcus",
        "synonyms": ["rapid strep", "strep test", "throat swab"],
        "estimated_value_usd": 28.0,
    },
    {
        "code": "81002",
        "procedure_key": "urinalysis_dipstick",
        "description": "Urinalysis, by dip stick or tablet reagent; non-automated, without microscopy",
        "synonyms": ["urinalysis", "urine dip", "urine test", "dipstick"],
        "estimated_value_usd": 16.0,
    },
    {
        "code": "94640",
        "procedure_key": "nebulizer_treatment",
        "description": "Pressurized or nonpressurized inhalation treatment for acute airway obstruction",
        "synonyms": ["nebulizer", "neb treatment", "inhalation treatment"],
        "estimated_value_usd": 36.0,
    },
    {
        "code": "96372",
        "procedure_key": "therapeutic_injection",
        "description": "Therapeutic, prophylactic, or diagnostic injection, subcutaneous or intramuscular",
        "synonyms": ["intramuscular injection", "im injection", "subcutaneous injection", "shot given"],
        "estimated_value_usd": 22.0,
    },
    {
        "code": "20552",
        "procedure_key": "trigger_point_injection",
        "description": "Injection(s); single or multiple trigger point(s), one or two muscle groups",
        "synonyms": ["trigger point injection", "muscle injection"],
        "estimated_value_usd": 85.0,
    },
    {
        "code": "17000",
        "procedure_key": "cryotherapy_lesion_destruction",
        "description": "Destruction of premalignant lesion; first lesion",
        "synonyms": ["cryotherapy", "cryo treatment", "lesion destruction"],
        "estimated_value_usd": 72.0,
    },
]


DEFAULT_ICD10_CODES: list[dict[str, Any]] = [
    {
        "code": "R42",
        "condition_key": "dizziness_giddiness",
        "description": "Dizziness and giddiness",
        "synonyms": ["dizziness", "giddiness", "vertigo"],
    },
    {
        "code": "R53.83",
        "condition_key": "other_fatigue",
        "description": "Other fatigue",
        "synonyms": ["fatigue", "tiredness", "lethargy"],
    },
    {
        "code": "R50.9",
        "condition_key": "fever_unspecified",
        "description": "Fever, unspecified",
        "synonyms": ["fever", "pyrexia"],
    },
    {
        "code": "J02.9",
        "condition_key": "acute_pharyngitis_unspecified",
        "description": "Acute pharyngitis, unspecified",
        "synonyms": ["sore throat", "pharyngitis"],
    },
    {
        "code": "R05.9",
        "condition_key": "cough_unspecified",
        "description": "Cough, unspecified",
        "synonyms": ["cough"],
    },
]


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


class RuntimeAuditStore:
    """Persistent audit logging for multi-agent runs (SQLite by default, Postgres optional)."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._is_sqlite = database_url.startswith("sqlite:///")
        self._is_postgres = database_url.startswith("postgresql://") or database_url.startswith("postgres://")
        self._ensure_table()

    def log_event(
        self,
        run_id: str,
        case_id: str,
        agent_id: str,
        agent_name: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        created_at = _now()
        payload_json = json.dumps(payload, separators=(",", ":"))
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            db_file.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_file) as connection:
                connection.execute(
                    """
                    INSERT INTO agent_run_logs (
                        run_id, case_id, agent_id, agent_name, event_type, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, case_id, agent_id, agent_name, event_type, payload_json, created_at),
                )
            return

        if self._is_postgres:
            try:
                psycopg = importlib.import_module("psycopg")
            except Exception as exc:  # pragma: no cover - optional runtime dependency
                raise RuntimeError("Postgres URL configured but psycopg is not installed") from exc

            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO agent_run_logs (
                            run_id, case_id, agent_id, agent_name, event_type, payload_json, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (run_id, case_id, agent_id, agent_name, event_type, payload_json, created_at),
                    )
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for audit store: {self._database_url}")

    def list_events(
        self,
        *,
        run_id: str | None = None,
        case_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            with sqlite3.connect(db_file) as connection:
                connection.row_factory = sqlite3.Row
                query = (
                    "SELECT id, run_id, case_id, agent_id, agent_name, event_type, payload_json, created_at "
                    "FROM agent_run_logs"
                )
                where_parts: list[str] = []
                params: list[Any] = []
                if run_id:
                    where_parts.append("run_id = ?")
                    params.append(run_id)
                if case_id:
                    where_parts.append("case_id = ?")
                    params.append(case_id)
                if where_parts:
                    query += " WHERE " + " AND ".join(where_parts)
                query += " ORDER BY id DESC LIMIT ?"
                params.append(limit)
                rows = connection.execute(query, params).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                payload_text = row["payload_json"]
                try:
                    payload = json.loads(payload_text)
                except Exception:
                    payload = {"raw": payload_text}
                result.append(
                    {
                        "id": row["id"],
                        "run_id": row["run_id"],
                        "case_id": row["case_id"],
                        "agent_id": row["agent_id"],
                        "agent_name": row["agent_name"],
                        "event_type": row["event_type"],
                        "payload": payload,
                        "created_at": row["created_at"],
                    }
                )
            return result

        if self._is_postgres:
            try:
                psycopg = importlib.import_module("psycopg")
            except Exception as exc:  # pragma: no cover - optional runtime dependency
                raise RuntimeError("Postgres URL configured but psycopg is not installed") from exc

            where_parts: list[str] = []
            params: list[Any] = []
            if run_id:
                where_parts.append("run_id = %s")
                params.append(run_id)
            if case_id:
                where_parts.append("case_id = %s")
                params.append(case_id)

            query = (
                "SELECT id, run_id, case_id, agent_id, agent_name, event_type, payload_json, created_at "
                "FROM agent_run_logs"
            )
            if where_parts:
                query += " WHERE " + " AND ".join(where_parts)
            query += " ORDER BY id DESC LIMIT %s"
            params.append(limit)

            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    cursor.execute(query, params)
                    rows = cursor.fetchall()

            return [
                {
                    "id": row[0],
                    "run_id": row[1],
                    "case_id": row[2],
                    "agent_id": row[3],
                    "agent_name": row[4],
                    "event_type": row[5],
                    "payload": row[6],
                    "created_at": row[7],
                }
                for row in rows
            ]

        raise ValueError(f"Unsupported database URL for audit store: {self._database_url}")

    def _ensure_table(self) -> None:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            db_file.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_file) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_run_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        case_id TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        agent_name TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            return

        if self._is_postgres:
            try:
                psycopg = importlib.import_module("psycopg")
            except Exception as exc:  # pragma: no cover - optional runtime dependency
                raise RuntimeError("Postgres URL configured but psycopg is not installed") from exc

            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS agent_run_logs (
                            id BIGSERIAL PRIMARY KEY,
                            run_id TEXT NOT NULL,
                            case_id TEXT NOT NULL,
                            agent_id TEXT NOT NULL,
                            agent_name TEXT NOT NULL,
                            event_type TEXT NOT NULL,
                            payload_json JSONB NOT NULL,
                            created_at TEXT NOT NULL
                        )
                        """
                    )
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for audit store: {self._database_url}")


class CptCodeStore:
    """Local billable CPT code store backed by SQLite or Postgres."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._is_sqlite = database_url.startswith("sqlite:///")
        self._is_postgres = database_url.startswith("postgresql://") or database_url.startswith("postgres://")
        self._ensure_table()
        self._seed_codes()

    def list_codes(self) -> list[dict[str, Any]]:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            with sqlite3.connect(db_file) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT code, procedure_key, description, synonyms_json, estimated_value_usd FROM cpt_codes ORDER BY code"
                ).fetchall()
            return [
                {
                    "code": row["code"],
                    "procedure_key": row["procedure_key"],
                    "description": row["description"],
                    "synonyms": json.loads(row["synonyms_json"]),
                    "estimated_value_usd": float(row["estimated_value_usd"]),
                }
                for row in rows
            ]

        if self._is_postgres:
            psycopg = importlib.import_module("psycopg")
            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT code, procedure_key, description, synonyms_json, estimated_value_usd FROM cpt_codes ORDER BY code"
                    )
                    rows = cursor.fetchall()
            return [
                {
                    "code": row[0],
                    "procedure_key": row[1],
                    "description": row[2],
                    "synonyms": row[3],
                    "estimated_value_usd": float(row[4] or 0),
                }
                for row in rows
            ]

        raise ValueError(f"Unsupported database URL for CPT store: {self._database_url}")

    def _ensure_table(self) -> None:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            db_file.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_file) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cpt_codes (
                        code TEXT PRIMARY KEY,
                        procedure_key TEXT NOT NULL,
                        description TEXT NOT NULL,
                        synonyms_json TEXT NOT NULL,
                        estimated_value_usd REAL NOT NULL DEFAULT 0
                    )
                    """
                )
                columns = [row[1] for row in connection.execute("PRAGMA table_info(cpt_codes)").fetchall()]
                if "estimated_value_usd" not in columns:
                    connection.execute("ALTER TABLE cpt_codes ADD COLUMN estimated_value_usd REAL NOT NULL DEFAULT 0")
            return

        if self._is_postgres:
            psycopg = importlib.import_module("psycopg")
            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS cpt_codes (
                            code TEXT PRIMARY KEY,
                            procedure_key TEXT NOT NULL,
                            description TEXT NOT NULL,
                            synonyms_json JSONB NOT NULL,
                            estimated_value_usd DOUBLE PRECISION NOT NULL DEFAULT 0
                        )
                        """
                    )
                    cursor.execute(
                        "ALTER TABLE cpt_codes ADD COLUMN IF NOT EXISTS estimated_value_usd DOUBLE PRECISION NOT NULL DEFAULT 0"
                    )
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for CPT store: {self._database_url}")

    def _seed_codes(self) -> None:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            with sqlite3.connect(db_file) as connection:
                for item in DEFAULT_CPT_CODES:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO cpt_codes (code, procedure_key, description, synonyms_json, estimated_value_usd)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            item["code"],
                            item["procedure_key"],
                            item["description"],
                            json.dumps(item["synonyms"]),
                            float(item.get("estimated_value_usd", 0.0)),
                        ),
                    )
                    connection.execute(
                        "UPDATE cpt_codes SET estimated_value_usd = ? WHERE code = ?",
                        (float(item.get("estimated_value_usd", 0.0)), item["code"]),
                    )
            return

        if self._is_postgres:
            psycopg = importlib.import_module("psycopg")
            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    for item in DEFAULT_CPT_CODES:
                        cursor.execute(
                            """
                            INSERT INTO cpt_codes (code, procedure_key, description, synonyms_json, estimated_value_usd)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (code) DO UPDATE
                            SET procedure_key = EXCLUDED.procedure_key,
                                description = EXCLUDED.description,
                                synonyms_json = EXCLUDED.synonyms_json,
                                estimated_value_usd = EXCLUDED.estimated_value_usd
                            """,
                            (
                                item["code"],
                                item["procedure_key"],
                                item["description"],
                                json.dumps(item["synonyms"]),
                                float(item.get("estimated_value_usd", 0.0)),
                            ),
                        )
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for CPT store: {self._database_url}")


class Icd10CodeStore:
    """Local ICD-10 code store backed by SQLite or Postgres."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._is_sqlite = database_url.startswith("sqlite:///")
        self._is_postgres = database_url.startswith("postgresql://") or database_url.startswith("postgres://")
        self._ensure_table()
        self._seed_codes()

    def list_codes(self) -> list[dict[str, Any]]:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            with sqlite3.connect(db_file) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute(
                    "SELECT code, condition_key, description, synonyms_json FROM icd10_codes ORDER BY code"
                ).fetchall()
            return [
                {
                    "code": row["code"],
                    "condition_key": row["condition_key"],
                    "description": row["description"],
                    "synonyms": json.loads(row["synonyms_json"]),
                }
                for row in rows
            ]

        if self._is_postgres:
            psycopg = importlib.import_module("psycopg")
            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    cursor.execute("SELECT code, condition_key, description, synonyms_json FROM icd10_codes ORDER BY code")
                    rows = cursor.fetchall()
            return [
                {
                    "code": row[0],
                    "condition_key": row[1],
                    "description": row[2],
                    "synonyms": row[3],
                }
                for row in rows
            ]

        raise ValueError(f"Unsupported database URL for ICD-10 store: {self._database_url}")

    def _ensure_table(self) -> None:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            db_file.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_file) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS icd10_codes (
                        code TEXT PRIMARY KEY,
                        condition_key TEXT NOT NULL,
                        description TEXT NOT NULL,
                        synonyms_json TEXT NOT NULL
                    )
                    """
                )
            return

        if self._is_postgres:
            psycopg = importlib.import_module("psycopg")
            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS icd10_codes (
                            code TEXT PRIMARY KEY,
                            condition_key TEXT NOT NULL,
                            description TEXT NOT NULL,
                            synonyms_json JSONB NOT NULL
                        )
                        """
                    )
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for ICD-10 store: {self._database_url}")

    def _seed_codes(self) -> None:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            with sqlite3.connect(db_file) as connection:
                for item in DEFAULT_ICD10_CODES:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO icd10_codes (code, condition_key, description, synonyms_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            item["code"],
                            item["condition_key"],
                            item["description"],
                            json.dumps(item["synonyms"]),
                        ),
                    )
            return

        if self._is_postgres:
            psycopg = importlib.import_module("psycopg")
            with psycopg.connect(self._database_url) as connection:  # type: ignore[no-untyped-call]
                with connection.cursor() as cursor:
                    for item in DEFAULT_ICD10_CODES:
                        cursor.execute(
                            """
                            INSERT INTO icd10_codes (code, condition_key, description, synonyms_json)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (code) DO NOTHING
                            """,
                            (
                                item["code"],
                                item["condition_key"],
                                item["description"],
                                json.dumps(item["synonyms"]),
                            ),
                        )
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for ICD-10 store: {self._database_url}")

class IntakeAgent:
    def __init__(self, config: dict[str, Any], service: ClinicalDocumentationService) -> None:
        self.config = config
        self._service = service

    @property
    def id(self) -> str:
        return str(self.config["agent"]["id"])

    @property
    def name(self) -> str:
        return str(self.config["agent"]["name"])

    def run(self, request: ClinicalNoteRequest) -> dict[str, Any]:
        note = self._service.generate_note_with_haystack(request)
        symptoms = [item.model_dump() for item in note.entities.symptoms]
        vitals = [item.model_dump() for item in note.entities.vitals]
        return {
            "transcript": request.transcript,
            "symptoms": symptoms,
            "vitals": vitals,
            "entities": note.entities.model_dump(),
            "draft_note": note.model_dump(),
        }


class SafetyAgent:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @property
    def id(self) -> str:
        return str(self.config["agent"]["id"])

    @property
    def name(self) -> str:
        return str(self.config["agent"]["name"])

    def run(self, transcript: str, note_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        lowered = transcript.lower()
        medications = _extract_medications(transcript, note_payload)
        allergies = _extract_allergies(note_payload)

        findings: list[dict[str, str]] = []

        if medications and not allergies:
            findings.append(
                {
                    "issue": "Medication mentioned but allergy status is missing",
                    "severity": "warning",
                    "recommendation": "Confirm and document allergy status before finalizing medication plan.",
                }
            )

        if "warfarin" in medications and any(item in lowered for item in ("ibuprofen", "diclofenac", "naproxen")):
            findings.append(
                {
                    "issue": "Potential interaction: warfarin with NSAID",
                    "severity": "critical",
                    "recommendation": "Review bleeding risk and safer analgesic alternatives.",
                }
            )

        if "methotrexate" in medications and any(item in lowered for item in ("pregnan", "trying to conceive")):
            findings.append(
                {
                    "issue": "Methotrexate mentioned with potential pregnancy context",
                    "severity": "critical",
                    "recommendation": "Escalate immediately and verify contraindication status.",
                }
            )

        for medication in medications:
            if medication in HIGH_RISK_MEDICATIONS:
                findings.append(
                    {
                        "issue": f"High-risk medication requires explicit review: {medication}",
                        "severity": "warning",
                        "recommendation": HIGH_RISK_MEDICATIONS[medication],
                    }
                )

        unique_findings = _dedupe_findings(findings)
        return {
            "valid": not any(item["severity"] == "critical" for item in unique_findings),
            "high_risk_medications": sorted(medications.intersection(HIGH_RISK_MEDICATIONS.keys())),
            "findings": unique_findings,
        }


class ScribeAgent:
    def __init__(self, config: dict[str, Any], safety_tool: Callable[[str, dict[str, Any] | None], dict[str, Any]]) -> None:
        self.config = config
        self._safety_tool = safety_tool

    @property
    def id(self) -> str:
        return str(self.config["agent"]["id"])

    @property
    def name(self) -> str:
        return str(self.config["agent"]["name"])

    def run(self, transcript: str, intake_output: dict[str, Any]) -> dict[str, Any]:
        draft_note_payload = intake_output.get("draft_note", {})
        note = ClinicalNoteResponse.model_validate(draft_note_payload)

        # Agent-as-a-Tool: Scribe invokes SafetyAgent when high-risk medication is detected.
        medications = _extract_medications(transcript, note.model_dump())
        safety_result: dict[str, Any] | None = None
        if medications.intersection(HIGH_RISK_MEDICATIONS.keys()):
            safety_result = self._safety_tool(transcript, note.model_dump())
            note.review_flags.extend(_review_flags_from_findings(safety_result.get("findings", [])))

        note.disclaimer = (
            "Clinician review required. This multi-agent draft supports documentation and "
            "must not be used as a standalone medical decision."
        )

        return {
            "summary": note.summary,
            "entities": note.entities.model_dump(),
            "soap_note": note.soap_note.model_dump(),
            "review_flags": [item.model_dump() for item in note.review_flags],
            "safety_tool_invoked": bool(safety_result),
            "safety_result": safety_result,
            "clinical_note": note.model_dump(),
        }


class LocalNliSentenceGuard:
    """Post-generation guard that strips unsupported Assessment/Plan sentences."""

    def __init__(self, model_name: str = "microsoft/deberta-v3-large-mnli") -> None:
        self._model_name = model_name
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._label_to_index: dict[str, int] = {}
        self._load_error: str | None = None

    @property
    def available(self) -> bool:
        self._ensure_loaded()
        return self._model is not None and self._tokenizer is not None and self._torch is not None

    @property
    def load_error(self) -> str:
        self._ensure_loaded()
        return self._load_error or "NLI sentence guard unavailable"

    def strip_unsupported_sentences(
        self,
        note: ClinicalNoteResponse,
        transcript: str,
    ) -> tuple[ClinicalNoteResponse, list[dict[str, Any]]]:
        if not self.available:
            return note, []

        transcript_chunks = _split_text_into_sentences(transcript)
        if not transcript_chunks:
            return note, []

        updated_note = note.model_copy(deep=True)
        warnings: list[dict[str, Any]] = []

        for section_name in ("assessment", "plan"):
            original_text = getattr(updated_note.soap_note, section_name).text
            sentences = _split_text_into_sentences(original_text)
            if not sentences:
                continue

            retained: list[str] = []
            for sentence in sentences:
                supporting = _retrieve_supporting_evidence(sentence, transcript_chunks, top_k=4)
                entailment, neutral, contradiction = self._score_sentence_against_evidence(sentence, supporting)
                if neutral > 0.5 or contradiction > 0.5:
                    warnings.append(
                        {
                            "section": section_name,
                            "sentence": sentence,
                            "neutral_score": round(neutral, 4),
                            "contradiction_score": round(contradiction, 4),
                            "evidence": supporting,
                            "reason": "Stripped by NLI guardrail (neutral/contradiction > 0.5)",
                        }
                    )
                    continue
                retained.append(sentence)

            cleaned = " ".join(retained).strip()
            if not cleaned:
                cleaned = "unknown"
            getattr(updated_note.soap_note, section_name).text = cleaned

        return updated_note, warnings

    def _score_sentence_against_evidence(self, sentence: str, evidence_chunks: list[str]) -> tuple[float, float, float]:
        if not evidence_chunks:
            return (0.0, 1.0, 0.0)

        entailment_scores: list[float] = []
        neutral_scores: list[float] = []
        contradiction_scores: list[float] = []

        for evidence in evidence_chunks:
            encoded = self._tokenizer(
                evidence,
                sentence,
                truncation=True,
                max_length=384,
                padding=True,
                return_tensors="pt",
            )
            with self._torch.no_grad():
                logits = self._model(**encoded).logits
                probs = self._torch.softmax(logits, dim=-1)[0]

            entailment_scores.append(float(probs[self._label_index("entailment")]))
            neutral_scores.append(float(probs[self._label_index("neutral")]))
            contradiction_scores.append(float(probs[self._label_index("contradiction")]))

        return (
            max(entailment_scores, default=0.0),
            max(neutral_scores, default=0.0),
            max(contradiction_scores, default=0.0),
        )

    def _label_index(self, label: str) -> int:
        return self._label_to_index.get(label, {"contradiction": 0, "neutral": 1, "entailment": 2}[label])

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if self._load_error is not None:
            return

        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch
        except Exception as exc:
            self._load_error = str(exc)
            return

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self._model_name,
                local_files_only=not settings.allow_remote_model_downloads,
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self._model_name,
                local_files_only=not settings.allow_remote_model_downloads,
            )
            self._model.eval()
            self._torch = torch

            id2label = {
                int(index): str(label).lower()
                for index, label in self._model.config.id2label.items()
            }
            for idx, label in id2label.items():
                if "contrad" in label:
                    self._label_to_index["contradiction"] = idx
                elif "neutral" in label:
                    self._label_to_index["neutral"] = idx
                elif "entail" in label:
                    self._label_to_index["entailment"] = idx
        except Exception as exc:
            self._load_error = str(exc)


class BillingOptimizerAgent:
    def __init__(self, config: dict[str, Any], cpt_store: CptCodeStore, icd10_store: Icd10CodeStore) -> None:
        self.config = config
        self._cpt_store = cpt_store
        self._icd10_store = icd10_store

    @property
    def id(self) -> str:
        return str(self.config["agent"]["id"])

    @property
    def name(self) -> str:
        return str(self.config["agent"]["name"])

    def run(self, note: ClinicalNoteResponse) -> dict[str, Any]:
        soap_text = _normalize_text(_soap_as_text(note))
        note_sentences = _split_text_into_sentences(_soap_as_text(note))
        note_dump = _normalize_text(json.dumps(note.model_dump(), separators=(",", ":")))
        explicit_codes = set(re.findall(r"\b\d{5}\b", note_dump))

        potential_leakage: list[dict[str, Any]] = []
        revenue_flags: list[dict[str, Any]] = []
        matched_codes: list[dict[str, Any]] = []
        for code in self._cpt_store.list_codes():
            synonyms = [str(item).lower().strip() for item in code.get("synonyms", []) if str(item).strip()]
            if not synonyms:
                continue

            mentioned_in_soap = any(_phrase_in_text(term, soap_text) for term in synonyms)
            if not mentioned_in_soap:
                continue

            matched_codes.append(
                {
                    "cpt_code": code["code"],
                    "procedure": code["procedure_key"],
                    "description": code["description"],
                    "estimated_value_usd": float(code.get("estimated_value_usd", 0.0)),
                }
            )

            if code["code"] in explicit_codes:
                continue

            evidence_sentence = _find_evidence_sentence(note_sentences, synonyms) or note.soap_note.assessment.text
            estimated_value = round(float(code.get("estimated_value_usd", 0.0)), 2)

            potential_leakage.append(
                {
                    "flag": "Potential Revenue Leakage",
                    "cpt_code": code["code"],
                    "procedure": code["procedure_key"],
                    "reason": "Procedure documented in SOAP note without explicit billing code.",
                    "suggestion": (
                        f"Add CPT {code['code']} ({code['description']}) if clinically appropriate; "
                        f"estimated recoverable value ${estimated_value:.2f}."
                    ),
                }
            )
            revenue_flags.append(
                {
                    "flag": "Revenue Optimization Flag",
                    "suggested_code": code["code"],
                    "estimated_recovered_usd": estimated_value,
                    "procedure": code["procedure_key"],
                    "evidence_sentence": evidence_sentence,
                }
            )

        assessment_text = _normalize_text(note.soap_note.assessment.text)
        matched_icd10: list[dict[str, Any]] = []
        potential_icd10_leakage: list[dict[str, Any]] = []
        for code in self._icd10_store.list_codes():
            synonyms = [str(item).lower().strip() for item in code.get("synonyms", []) if str(item).strip()]
            if not synonyms:
                continue

            mentioned_in_assessment = any(_phrase_in_text(term, assessment_text) for term in synonyms)
            if not mentioned_in_assessment:
                continue

            matched_icd10.append(
                {
                    "icd10_code": code["code"],
                    "condition": code["condition_key"],
                    "description": code["description"],
                }
            )

            if code["code"] in explicit_codes:
                continue

            potential_icd10_leakage.append(
                {
                    "flag": "Potential Revenue Leakage",
                    "icd10_code": code["code"],
                    "condition": code["condition_key"],
                    "reason": "Assessment suggests a billable diagnosis not captured in summary.",
                    "suggestion": f"Review diagnosis documentation and consider ICD-10 {code['code']} ({code['description']}).",
                }
            )

        return {
            "matched_billable_codes": matched_codes,
            "potential_revenue_leakage": potential_leakage,
            "revenue_optimization_flags": revenue_flags,
            "estimated_recovered_usd_total": round(
                sum(float(item.get("estimated_recovered_usd", 0.0)) for item in revenue_flags),
                2,
            ),
            "matched_icd10_codes": matched_icd10,
            "potential_icd10_leakage": potential_icd10_leakage,
            "has_revenue_leakage": bool(potential_leakage or potential_icd10_leakage),
            "has_revenue_optimization_flags": bool(revenue_flags),
        }


class AgentRuntime:
    """Coordinates Intake -> Safety -> Scribe with auditable execution logs."""

    def __init__(self, registry: ProjectAgentRegistry, service: ClinicalDocumentationService) -> None:
        self._registry = registry
        self._service = service
        self._audit_store = RuntimeAuditStore(settings.database_url)
        self._cpt_store = CptCodeStore(settings.database_url)
        self._icd10_store = Icd10CodeStore(settings.database_url)

        self._intake_agent = IntakeAgent(self._registry.get_agent_payload("clinical_intake_agent"), service)
        self._safety_agent = SafetyAgent(self._registry.get_agent_payload("note_safety_reviewer"))

        scribe_config = self._load_scribe_config()
        self._scribe_agent = ScribeAgent(scribe_config, self._safety_agent.run)
        self._nli_sentence_guard = LocalNliSentenceGuard()
        self._billing_agent = BillingOptimizerAgent(
            self._registry.get_agent_payload("billing_optimizer_agent"),
            self._cpt_store,
            self._icd10_store,
        )

    def orchestrate(self, request: AgentRunRequest) -> dict[str, Any]:
        transcript = request.transcript or request.context.get("transcript")
        if not transcript:
            raise ValueError("Agent runtime requires transcript input")

        run_id = uuid4().hex[:12]
        case_id = request.case_id or f"runtime-{run_id}"

        note_request = ClinicalNoteRequest(
            transcript=transcript,
            visit_context=request.visit_context,
            include_differential_diagnosis=request.include_differential_diagnosis,
        )

        self._audit_store.log_event(
            run_id,
            case_id,
            self._intake_agent.id,
            self._intake_agent.name,
            "agent_started",
            {"step": "intake"},
        )
        intake_output = self._intake_agent.run(note_request)
        self._audit_store.log_event(
            run_id,
            case_id,
            self._intake_agent.id,
            self._intake_agent.name,
            "agent_completed",
            {
                "step": "intake",
                "symptom_count": len(intake_output.get("symptoms", [])),
                "vitals_count": len(intake_output.get("vitals", [])),
            },
        )

        self._audit_store.log_event(
            run_id,
            case_id,
            self._scribe_agent.id,
            self._scribe_agent.name,
            "agent_started",
            {"step": "scribe"},
        )
        scribe_output = self._scribe_agent.run(transcript, intake_output)

        filtered_warnings: list[dict[str, Any]] = []
        clinical_note_payload = scribe_output.get("clinical_note")
        if isinstance(clinical_note_payload, dict):
            try:
                generated_note = ClinicalNoteResponse.model_validate(clinical_note_payload)
                generated_note, filtered_warnings = self._nli_sentence_guard.strip_unsupported_sentences(
                    generated_note,
                    transcript,
                )

                if filtered_warnings:
                    generated_note.review_flags.extend(
                        _review_flags_from_findings(
                            [
                                {
                                    "issue": f"Hallucination warning ({item['section']}): stripped unsupported sentence",
                                    "severity": "critical",
                                    "recommendation": item["sentence"],
                                }
                                for item in filtered_warnings
                            ]
                        )
                    )

                scribe_output["clinical_note"] = generated_note.model_dump()
                scribe_output["soap_note"] = generated_note.soap_note.model_dump()
                scribe_output["review_flags"] = [item.model_dump() for item in generated_note.review_flags]
                scribe_output["nli_stripped_sentences"] = filtered_warnings
            except Exception:
                pass

        self._audit_store.log_event(
            run_id,
            case_id,
            self._scribe_agent.id,
            self._scribe_agent.name,
            "agent_completed",
            {
                "step": "scribe",
                "safety_tool_invoked": scribe_output.get("safety_tool_invoked", False),
                "review_flag_count": len(scribe_output.get("review_flags", [])),
                "nli_stripped_sentences": len(filtered_warnings),
            },
        )

        if filtered_warnings:
            self._audit_store.log_event(
                run_id,
                case_id,
                self._scribe_agent.id,
                self._scribe_agent.name,
                "hallucination_warning",
                {"warnings": filtered_warnings},
            )

        if scribe_output.get("safety_tool_invoked"):
            safety_result = scribe_output.get("safety_result", {})
            self._audit_store.log_event(
                run_id,
                case_id,
                self._safety_agent.id,
                self._safety_agent.name,
                "agent_called_as_tool",
                {
                    "high_risk_medications": safety_result.get("high_risk_medications", []),
                    "findings": safety_result.get("findings", []),
                },
            )

        billing_scan = None
        clinical_note_payload = scribe_output.get("clinical_note")
        if isinstance(clinical_note_payload, dict):
            try:
                generated_note = ClinicalNoteResponse.model_validate(clinical_note_payload)
                billing_scan = self._billing_agent.run(generated_note)
                scribe_output["revenue_optimization_flags"] = billing_scan.get("revenue_optimization_flags", [])
                scribe_output["estimated_recovered_usd_total"] = billing_scan.get("estimated_recovered_usd_total", 0.0)

                for item in billing_scan.get("revenue_optimization_flags", []):
                    note_flag = ReviewFlag(
                        issue=(
                            f"Revenue Optimization Flag: CPT {item.get('suggested_code', 'unknown')} "
                            "may be uncaptured"
                        ),
                        severity="warning",
                        recommendation=str(item.get("evidence_sentence", "Review billing evidence in SOAP note.")),
                    )
                    scribe_output.setdefault("review_flags", [])
                    scribe_output["review_flags"].append(note_flag.model_dump())

                self._audit_store.log_event(
                    run_id,
                    case_id,
                    self._billing_agent.id,
                    self._billing_agent.name,
                    "revenue_optimization_flagged",
                    {
                        "flag_count": len(billing_scan.get("revenue_optimization_flags", [])),
                        "estimated_recovered_usd_total": billing_scan.get("estimated_recovered_usd_total", 0.0),
                    },
                )
            except Exception:
                billing_scan = None

        return {
            "run_id": run_id,
            "case_id": case_id,
            "intake": {
                "symptoms": intake_output.get("symptoms", []),
                "vitals": intake_output.get("vitals", []),
            },
            "scribe": scribe_output,
            "billing_optimizer": billing_scan,
            "audit": {
                "database": settings.database_url,
                "table": "agent_run_logs",
                "events_logged": 2
                + (1 if scribe_output.get("safety_tool_invoked") else 0)
                + (1 if filtered_warnings else 0),
            },
        }

    def run_safety_only(self, request: AgentRunRequest) -> dict[str, Any]:
        case = self._resolve_case(request)
        run_id = uuid4().hex[:12]
        self._audit_store.log_event(
            run_id,
            case.case_id,
            self._safety_agent.id,
            self._safety_agent.name,
            "agent_started",
            {"step": "safety_only"},
        )
        result = self._safety_agent.run(case.transcript, case.note.model_dump())
        self._audit_store.log_event(
            run_id,
            case.case_id,
            self._safety_agent.id,
            self._safety_agent.name,
            "agent_completed",
            {
                "step": "safety_only",
                "finding_count": len(result.get("findings", [])),
            },
        )
        return {
            "case_id": case.case_id,
            "patient_label": case.patient_label,
            **result,
        }

    def run_billing_optimizer(self, request: AgentRunRequest) -> dict[str, Any]:
        case = self._resolve_case(request)
        run_id = uuid4().hex[:12]
        self._audit_store.log_event(
            run_id,
            case.case_id,
            self._billing_agent.id,
            self._billing_agent.name,
            "agent_started",
            {"step": "billing_optimizer"},
        )

        result = self._billing_agent.run(case.note)

        self._audit_store.log_event(
            run_id,
            case.case_id,
            self._billing_agent.id,
            self._billing_agent.name,
            "agent_completed",
            {
                "step": "billing_optimizer",
                "matched_billable_codes": len(result.get("matched_billable_codes", [])),
                "potential_revenue_leakage": len(result.get("potential_revenue_leakage", [])),
                "matched_icd10_codes": len(result.get("matched_icd10_codes", [])),
                "potential_icd10_leakage": len(result.get("potential_icd10_leakage", [])),
            },
        )
        return {
            "case_id": case.case_id,
            "patient_label": case.patient_label,
            **result,
        }

    def list_audit_events(
        self,
        *,
        run_id: str | None = None,
        case_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 500))
        return self._audit_store.list_events(run_id=run_id, case_id=case_id, limit=bounded_limit)

    def _load_scribe_config(self) -> dict[str, Any]:
        # Prefer a dedicated scribe agent config if present; fall back to queue orchestrator metadata.
        candidate_ids = ["scribe_agent", "review_queue_orchestrator", "clinical_intake_agent"]
        for candidate in candidate_ids:
            try:
                return self._registry.get_agent_payload(candidate)
            except KeyError:
                continue
        raise KeyError("Unable to load any config for scribe agent")

    def _resolve_case(self, request: AgentRunRequest) -> CaseRecord:
        case_id = request.case_id or request.context.get("case_id")
        if not case_id:
            raise ValueError("Agent request requires case_id")
        return self._service.get_case(case_id)


class ProjectAgentRunner:
    def __init__(self, registry: ProjectAgentRegistry, service: ClinicalDocumentationService) -> None:
        self._registry = registry
        self._service = service
        self._runtime = AgentRuntime(registry, service)

    def list_agents(self) -> list[AgentSummary]:
        return self._registry.list_agents()

    def run(self, agent_id: str, request: AgentRunRequest) -> AgentRunResponse:
        metadata = self._registry.get_agent_payload(agent_id)["agent"]

        if agent_id == "clinical_intake_agent":
            return AgentRunResponse(
                agent_id=agent_id,
                agent_name=metadata["name"],
                result=self._runtime.orchestrate(request),
            )
        if agent_id == "note_safety_reviewer":
            return AgentRunResponse(
                agent_id=agent_id,
                agent_name=metadata["name"],
                result=self._runtime.run_safety_only(request),
            )
        if agent_id == "review_queue_orchestrator":
            return AgentRunResponse(
                agent_id=agent_id,
                agent_name=metadata["name"],
                result=self._run_review_queue_orchestrator(),
            )
        if agent_id == "billing_optimizer_agent":
            return AgentRunResponse(
                agent_id=agent_id,
                agent_name=metadata["name"],
                result=self._runtime.run_billing_optimizer(request),
            )

        raise KeyError(agent_id)

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

    def list_runtime_audit_events(
        self,
        *,
        run_id: str | None = None,
        case_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self._runtime.list_audit_events(run_id=run_id, case_id=case_id, limit=limit)

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


def _extract_medications(transcript: str, note_payload: dict[str, Any] | None = None) -> set[str]:
    lowered = transcript.lower()
    medications = {name for name in HIGH_RISK_MEDICATIONS if name in lowered}

    if note_payload:
        entities = note_payload.get("entities", {})
        med_items = entities.get("medications", []) if isinstance(entities, dict) else []
        for item in med_items:
            if isinstance(item, dict):
                value = str(item.get("value", "")).lower().strip()
            elif isinstance(item, str):
                value = item.lower().strip()
            else:
                value = ""
            for medication in HIGH_RISK_MEDICATIONS:
                if medication in value:
                    medications.add(medication)

    # Also detect common medications that participate in interaction rules.
    for medication in ("ibuprofen", "diclofenac", "naproxen"):
        if medication in lowered:
            medications.add(medication)

    return medications


def _extract_allergies(note_payload: dict[str, Any] | None) -> list[str]:
    if not note_payload:
        return []
    entities = note_payload.get("entities", {})
    if not isinstance(entities, dict):
        return []

    results: list[str] = []
    for item in entities.get("allergies", []):
        if isinstance(item, dict):
            value = str(item.get("value", "")).strip().lower()
        elif isinstance(item, str):
            value = item.strip().lower()
        else:
            value = ""
        if value and value not in {"unknown", "none", "n/a", "na"}:
            results.append(value)
    return results


def _review_flags_from_findings(findings: list[dict[str, Any]]) -> list[ReviewFlag]:
    flags: list[ReviewFlag] = []
    for finding in findings:
        severity = str(finding.get("severity", "warning")).lower()
        if severity not in {"info", "warning", "critical"}:
            severity = "warning"
        flags.append(
            ReviewFlag(
                issue=str(finding.get("issue", "Safety review finding")),
                severity=severity,
                recommendation=str(finding.get("recommendation", "Clinician review required.")),
            )
        )
    return flags


def _dedupe_findings(findings: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for finding in findings:
        key = (finding.get("issue", ""), finding.get("severity", ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _soap_as_text(note: ClinicalNoteResponse) -> str:
    return " ".join(
        [
            note.soap_note.subjective.text,
            note.soap_note.objective.text,
            note.soap_note.assessment.text,
            note.soap_note.plan.text,
        ]
    )


def _phrase_in_text(phrase: str, normalized_text: str) -> bool:
    normalized_phrase = _normalize_text(phrase)
    if not normalized_phrase:
        return False
    return normalized_phrase in normalized_text


def _normalize_text(value: str) -> str:
    lowered = value.lower()
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lowered)
    return " ".join(cleaned.split())


def _find_evidence_sentence(sentences: list[str], phrases: list[str]) -> str:
    normalized_sentences = [(sentence, _normalize_text(sentence)) for sentence in sentences if sentence.strip()]
    for phrase in phrases:
        normalized_phrase = _normalize_text(phrase)
        if not normalized_phrase:
            continue
        for original_sentence, normalized_sentence in normalized_sentences:
            if normalized_phrase in normalized_sentence:
                return original_sentence
    return ""


def _split_text_into_sentences(text: str) -> list[str]:
    return [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", text) if segment and segment.strip()]


def _retrieve_supporting_evidence(claim: str, transcript_chunks: list[str], top_k: int = 4) -> list[str]:
    claim_tokens = set(_normalize_text(claim).split())
    if not claim_tokens:
        return transcript_chunks[:top_k]

    scored: list[tuple[int, int, str]] = []
    for chunk in transcript_chunks:
        tokens = set(_normalize_text(chunk).split())
        overlap = len(claim_tokens.intersection(tokens))
        scored.append((overlap, len(tokens), chunk))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in scored[: max(1, top_k)] if item[2].strip()]
