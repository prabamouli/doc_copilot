from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from clinic_copilot.config import settings


_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="system")
_vault_mapping_ctx: ContextVar[str | None] = ContextVar("vault_mapping_id", default=None)

_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\d{3}[-.\s]?){2}\d{4}\b")
_PERSON_NAME_RE = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")
_HONORIFIC_NAME_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+\b")
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{2,4})\b",
    re.IGNORECASE,
)
_CLINIC_RE = re.compile(
    r"\b(?:at\s+)?([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,4}\s+(?:Clinic|Hospital|Medical\s+Center|Health\s+Center))\b"
)


class RegulatoryVaultMiddleware(BaseHTTPMiddleware):
    """Request middleware that stamps a request id used by the regulatory vault logs."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id", uuid.uuid4().hex)
        token = _request_id_ctx.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers["x-request-id"] = request_id
        return response


class RegulatoryVault:
    """PII scan + masking + encrypted tamper-evident audit logging for LLM calls."""

    def __init__(self, database_url: str, encryption_secret: str) -> None:
        self._database_url = database_url
        self._is_sqlite = database_url.startswith("sqlite:///")
        self._is_postgres = database_url.startswith("postgresql://") or database_url.startswith("postgres://")
        self._fernet = Fernet(_fernet_key(encryption_secret))
        self._spacy_nlp = None
        self._spacy_checked = False
        self._ensure_table()

    def sanitize_for_llm(self, text: str, route: str, metadata: dict[str, Any] | None = None) -> str:
        payload = self.deidentify(text=text, route=route, metadata=metadata)
        pii_scan = payload["pii_scan"]
        masked_text = payload["deidentified_text"]

        transaction = {
            "request_id": _request_id_ctx.get(),
            "route": route,
            "pii": pii_scan,
            "metadata": metadata or {},
            "original_length": len(text),
            "masked_length": len(masked_text),
            "masked_preview": masked_text[:250],
        }
        self._log_transaction(route=route, pii=pii_scan, transaction=transaction)
        return masked_text

    def deidentify(self, text: str, route: str = "local", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Replace sensitive entities with deterministic placeholders and persist encrypted mapping."""
        pii_scan = self.scan_pii(text)
        mapping, deidentified_text = self._build_placeholder_mapping(text)
        mapping_id = self._persist_mapping(route=route, mapping=mapping, metadata=metadata or {})
        _vault_mapping_ctx.set(mapping_id)
        return {
            "mapping_id": mapping_id,
            "deidentified_text": deidentified_text,
            "pii_scan": pii_scan,
            "placeholder_count": len(mapping),
        }

    def reidentify(self, generated_text: str, mapping_id: str | None = None) -> str:
        """Restore placeholders to original values before clinician display."""
        selected_mapping_id = mapping_id or _vault_mapping_ctx.get()
        if not selected_mapping_id:
            return generated_text

        mapping = self._load_mapping(selected_mapping_id)
        if not mapping:
            return generated_text

        restored = generated_text
        for placeholder, original in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
            restored = restored.replace(placeholder, original)
        return restored

    def scan_pii(self, text: str) -> dict[str, list[str]]:
        phones = sorted({item.strip() for item in _PHONE_RE.findall(text) if item.strip()})
        names = sorted({item.strip() for item in _PERSON_NAME_RE.findall(text) if item.strip()})
        names.extend(item.strip() for item in _HONORIFIC_NAME_RE.findall(text) if item.strip())
        names = sorted(set(names))
        dates = sorted({item.strip() for item in _DATE_RE.findall(text) if item.strip()})
        clinics = sorted(
            {
                item.group(1).strip()
                for item in _CLINIC_RE.finditer(text)
                if item.group(1) and item.group(1).strip()
            }
        )

        for entity in self._extract_entities_with_local_ner(text):
            value = entity["text"].strip()
            if not value:
                continue
            label = entity["label"]
            if label == "PERSON":
                names.append(value)
            elif label == "DATE":
                dates.append(value)
            elif label in {"LOCATION", "CLINIC"}:
                clinics.append(value)

        return {
            "phone_numbers": sorted(set(phones)),
            "names": sorted(set(names)),
            "dates": sorted(set(dates)),
            "locations": sorted(set(clinics)),
        }

    def mask_text(self, text: str, pii_scan: dict[str, list[str]]) -> str:
        masked = text

        for phone in sorted(set(pii_scan.get("phone_numbers", [])), key=len, reverse=True):
            masked = masked.replace(phone, "[PHONE_REDACTED]")

        for name in sorted(set(pii_scan.get("names", [])), key=len, reverse=True):
            masked = re.sub(re.escape(name), "[NAME_REDACTED]", masked)

        for date_value in sorted(set(pii_scan.get("dates", [])), key=len, reverse=True):
            masked = re.sub(re.escape(date_value), "[DATE_REDACTED]", masked)

        for location in sorted(set(pii_scan.get("locations", [])), key=len, reverse=True):
            masked = re.sub(re.escape(location), "[LOCATION_REDACTED]", masked)

        return masked

    def _extract_entities_with_local_ner(self, text: str) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        nlp = self._load_spacy_model()
        if nlp is None:
            return entities

        doc = nlp(text)
        for ent in doc.ents:
            label = str(ent.label_).upper()
            mapped_label: str | None = None
            if label == "PERSON":
                mapped_label = "PERSON"
            elif label in {"DATE", "TIME"}:
                mapped_label = "DATE"
            elif label in {"GPE", "LOC", "FAC", "ORG"}:
                mapped_label = "LOCATION"

            if mapped_label is None:
                continue

            value = ent.text.strip()
            if not value:
                continue
            entities.append(
                {
                    "text": value,
                    "label": mapped_label,
                    "start": int(ent.start_char),
                    "end": int(ent.end_char),
                }
            )
        return entities

    def _load_spacy_model(self):
        if self._spacy_checked:
            return self._spacy_nlp
        self._spacy_checked = True

        try:
            import spacy

            self._spacy_nlp = spacy.load("en_core_web_sm")
        except Exception:
            self._spacy_nlp = None
        return self._spacy_nlp

    def _build_placeholder_mapping(self, text: str) -> tuple[dict[str, str], str]:
        spans: list[dict[str, Any]] = []

        for match in _PHONE_RE.finditer(text):
            spans.append({"text": match.group(0), "label": "PHONE", "start": match.start(), "end": match.end()})
        for match in _DATE_RE.finditer(text):
            spans.append({"text": match.group(0), "label": "DATE", "start": match.start(), "end": match.end()})
        for match in _PERSON_NAME_RE.finditer(text):
            spans.append({"text": match.group(0), "label": "PERSON", "start": match.start(), "end": match.end()})
        for match in _HONORIFIC_NAME_RE.finditer(text):
            spans.append({"text": match.group(0), "label": "PERSON", "start": match.start(), "end": match.end()})
        for match in _CLINIC_RE.finditer(text):
            value = match.group(1)
            if value:
                spans.append({"text": value, "label": "CLINIC", "start": match.start(1), "end": match.end(1)})

        spans.extend(self._extract_entities_with_local_ner(text))
        spans = _filter_non_overlapping_spans(spans)

        placeholder_by_key: dict[tuple[str, str], str] = {}
        placeholder_to_original: dict[str, str] = {}
        type_counters: dict[str, int] = {}

        for span in sorted(spans, key=lambda item: (int(item["start"]), str(item["text"]))):
            entity_type = _normalized_label(str(span["label"]))
            original = str(span["text"]).strip()
            if not original:
                continue
            key = (entity_type, original.lower())
            if key in placeholder_by_key:
                continue

            type_counters[entity_type] = type_counters.get(entity_type, 0) + 1
            placeholder = _placeholder_for(entity_type, type_counters[entity_type])
            placeholder_by_key[key] = placeholder
            placeholder_to_original[placeholder] = original

        deidentified = text
        replacements: list[tuple[int, int, str]] = []
        for span in spans:
            entity_type = _normalized_label(str(span["label"]))
            original = str(span["text"]).strip()
            key = (entity_type, original.lower())
            placeholder = placeholder_by_key.get(key)
            if not placeholder:
                continue
            replacements.append((int(span["start"]), int(span["end"]), placeholder))

        for start, end, placeholder in sorted(replacements, key=lambda item: item[0], reverse=True):
            deidentified = deidentified[:start] + placeholder + deidentified[end:]

        return placeholder_to_original, deidentified

    def _persist_mapping(self, route: str, mapping: dict[str, str], metadata: dict[str, Any]) -> str:
        mapping_id = uuid.uuid4().hex
        payload = {
            "mapping": mapping,
            "metadata": metadata,
        }
        encrypted_payload = self._fernet.encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
        created_at = _now()
        request_id = _request_id_ctx.get()

        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            db_file.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_file) as connection:
                connection.execute(
                    """
                    INSERT INTO vault_token_maps (mapping_id, request_id, route, mapping_encrypted, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (mapping_id, request_id, route, encrypted_payload, created_at),
                )
            return mapping_id

        if self._is_postgres:
            psycopg = __import__("psycopg")
            with psycopg.connect(self._database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO vault_token_maps (mapping_id, request_id, route, mapping_encrypted, created_at)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (mapping_id, request_id, route, encrypted_payload, created_at),
                    )
                connection.commit()
            return mapping_id

        raise ValueError(f"Unsupported database URL for regulatory vault: {self._database_url}")

    def _load_mapping(self, mapping_id: str) -> dict[str, str]:
        encrypted_payload: str | None = None

        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            with sqlite3.connect(db_file) as connection:
                row = connection.execute(
                    "SELECT mapping_encrypted FROM vault_token_maps WHERE mapping_id = ?",
                    (mapping_id,),
                ).fetchone()
            encrypted_payload = row[0] if row else None

        elif self._is_postgres:
            psycopg = __import__("psycopg")
            with psycopg.connect(self._database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT mapping_encrypted FROM vault_token_maps WHERE mapping_id = %s",
                        (mapping_id,),
                    )
                    row = cursor.fetchone()
            encrypted_payload = row[0] if row else None
        else:
            raise ValueError(f"Unsupported database URL for regulatory vault: {self._database_url}")

        if not encrypted_payload:
            return {}

        try:
            decrypted = self._fernet.decrypt(encrypted_payload.encode("utf-8")).decode("utf-8")
            payload = json.loads(decrypted)
            mapping = payload.get("mapping", {})
            if not isinstance(mapping, dict):
                return {}
            return {str(key): str(value) for key, value in mapping.items()}
        except Exception:
            return {}

    def _log_transaction(self, route: str, pii: dict[str, list[str]], transaction: dict[str, Any]) -> None:
        plaintext = json.dumps(transaction, separators=(",", ":"), sort_keys=True)
        payload_hash = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        encrypted_payload = self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

        request_id = _request_id_ctx.get()
        created_at = _now()
        pii_json = json.dumps(pii, separators=(",", ":"))

        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            db_file.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_file) as connection:
                connection.execute(
                    """
                    INSERT INTO encrypted_audit_log (
                        request_id, route, pii_json, payload_encrypted, payload_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (request_id, route, pii_json, encrypted_payload, payload_hash, created_at),
                )
            return

        if self._is_postgres:
            psycopg = __import__("psycopg")
            with psycopg.connect(self._database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO encrypted_audit_log (
                            request_id, route, pii_json, payload_encrypted, payload_sha256, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (request_id, route, pii_json, encrypted_payload, payload_hash, created_at),
                    )
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for regulatory vault: {self._database_url}")

    def _ensure_table(self) -> None:
        if self._is_sqlite:
            db_file = Path(self._database_url.removeprefix("sqlite:///"))
            db_file.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_file) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS encrypted_audit_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT NOT NULL,
                        route TEXT NOT NULL,
                        pii_json TEXT NOT NULL,
                        payload_encrypted TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS vault_token_maps (
                        mapping_id TEXT PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        route TEXT NOT NULL,
                        mapping_encrypted TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
            return

        if self._is_postgres:
            psycopg = __import__("psycopg")
            with psycopg.connect(self._database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS encrypted_audit_log (
                            id BIGSERIAL PRIMARY KEY,
                            request_id TEXT NOT NULL,
                            route TEXT NOT NULL,
                            pii_json JSONB NOT NULL,
                            payload_encrypted TEXT NOT NULL,
                            payload_sha256 TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        )
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS vault_token_maps (
                            mapping_id TEXT PRIMARY KEY,
                            request_id TEXT NOT NULL,
                            route TEXT NOT NULL,
                            mapping_encrypted TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        )
                        """
                    )
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for regulatory vault: {self._database_url}")


def _fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_label(label: str) -> str:
    lowered = label.lower()
    if lowered in {"person", "name"}:
        return "PATIENT"
    if lowered in {"date", "time"}:
        return "DATE"
    if lowered in {"clinic", "location", "gpe", "loc", "fac", "org"}:
        return "CLINIC"
    if lowered in {"phone", "phone_number"}:
        return "PHONE"
    return "ENTITY"


def _placeholder_for(entity_type: str, index: int) -> str:
    if entity_type == "CLINIC":
        return "{{CLINIC_%s}}" % _alpha_token(index)
    return "{{%s_%d}}" % (entity_type, index)


def _alpha_token(index: int) -> str:
    value = max(1, index)
    token = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        token = chr(ord("A") + remainder) + token
    return token


def _filter_non_overlapping_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = [
        span
        for span in spans
        if int(span.get("end", 0)) > int(span.get("start", 0)) and str(span.get("text", "")).strip()
    ]
    prepared.sort(key=lambda item: (int(item["start"]), -(int(item["end"]) - int(item["start"]))))

    accepted: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for span in prepared:
        start = int(span["start"])
        end = int(span["end"])
        if any(not (end <= taken_start or start >= taken_end) for taken_start, taken_end in occupied):
            continue
        accepted.append(span)
        occupied.append((start, end))
    return accepted


regulatory_vault = RegulatoryVault(
    database_url=settings.database_url,
    encryption_secret=settings.vault_encryption_secret or settings.litellm_master_key,
)
