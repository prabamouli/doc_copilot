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

_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\d{3}[-.\s]?){2}\d{4}\b")
_PERSON_NAME_RE = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")
_HONORIFIC_NAME_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+\b")


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
        self._ensure_table()

    def sanitize_for_llm(self, text: str, route: str, metadata: dict[str, Any] | None = None) -> str:
        pii_scan = self.scan_pii(text)
        masked_text = self.mask_text(text, pii_scan)

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

    def scan_pii(self, text: str) -> dict[str, list[str]]:
        phones = sorted({item.strip() for item in _PHONE_RE.findall(text) if item.strip()})
        names = sorted({item.strip() for item in _PERSON_NAME_RE.findall(text) if item.strip()})
        names.extend(item.strip() for item in _HONORIFIC_NAME_RE.findall(text) if item.strip())
        names = sorted(set(names))
        return {"phone_numbers": phones, "names": names}

    def mask_text(self, text: str, pii_scan: dict[str, list[str]]) -> str:
        masked = text

        for phone in sorted(set(pii_scan.get("phone_numbers", [])), key=len, reverse=True):
            masked = masked.replace(phone, "[PHONE_REDACTED]")

        for name in sorted(set(pii_scan.get("names", [])), key=len, reverse=True):
            masked = re.sub(re.escape(name), "[NAME_REDACTED]", masked)

        return masked

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
                connection.commit()
            return

        raise ValueError(f"Unsupported database URL for regulatory vault: {self._database_url}")


def _fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _now() -> str:
    return datetime.now(UTC).isoformat()


regulatory_vault = RegulatoryVault(
    database_url=settings.database_url,
    encryption_secret=settings.vault_encryption_secret or settings.litellm_master_key,
)
