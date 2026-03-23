from __future__ import annotations

import logging
import re
from typing import Iterable


_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\d{3}[-.\s]?){2}\d{4}\b")
_PERSON_NAME_RE = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")
_HONORIFIC_NAME_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+\b")
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{2,4})\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


class PhiRedactionFilter(logging.Filter):
    """Best-effort PHI redaction for logger records before writing to handlers."""

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - runtime mutation
        message = record.getMessage()
        if not message:
            return True

        redacted = redact_phi(message)
        record.msg = redacted
        record.args = ()
        return True


def redact_phi(text: str) -> str:
    redacted = text
    redacted = _PHONE_RE.sub("[PHONE_REDACTED]", redacted)
    redacted = _EMAIL_RE.sub("[EMAIL_REDACTED]", redacted)
    redacted = _DATE_RE.sub("[DATE_REDACTED]", redacted)
    redacted = _HONORIFIC_NAME_RE.sub("[NAME_REDACTED]", redacted)
    redacted = _PERSON_NAME_RE.sub("[NAME_REDACTED]", redacted)
    return redacted


def install_phi_redaction_filter() -> None:
    filter_instance = PhiRedactionFilter()
    candidate_loggers: Iterable[logging.Logger] = [
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("fastapi"),
    ]

    for logger in candidate_loggers:
        _attach_filter_once(logger, filter_instance)
        for handler in logger.handlers:
            _attach_filter_once(handler, filter_instance)


def _attach_filter_once(target: logging.Filterer, filter_instance: logging.Filter) -> None:
    if any(isinstance(existing, PhiRedactionFilter) for existing in target.filters):
        return
    target.addFilter(filter_instance)
