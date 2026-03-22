from __future__ import annotations

import json
import re

from fastapi import FastAPI
from pydantic import BaseModel


app = FastAPI(title="Mock OpenAI-Compatible Server")


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[dict]


def _extract_transcript(messages: list[dict]) -> str:
    for message in reversed(messages):
        content = message.get("content", "")
        if isinstance(content, str) and "Transcript:" in content:
            return content.split("Transcript:", 1)[1].strip()
    return ""


def _has_phrase(text: str, phrase: str) -> bool:
    return phrase.lower() in text.lower()


def _duration(text: str) -> str:
    match = re.search(r"\b(\d+\s+(?:day|days|week|weeks|month|months))\b", text.lower())
    return match.group(1) if match else "unknown"


@app.get("/v1/models")
def models() -> dict:
    return {"data": [{"id": "llama3.1", "object": "model"}], "object": "list"}


@app.post("/v1/chat/completions")
def chat_completions(request: ChatCompletionRequest) -> dict:
    transcript = _extract_transcript(request.messages)
    symptoms: list[dict] = []
    if _has_phrase(transcript, "fever"):
        symptoms.append({"value": "fever", "status": "supported", "confidence": "high"})
    if _has_phrase(transcript, "body pain"):
        symptoms.append({"value": "body pain", "status": "supported", "confidence": "medium"})
    if _has_phrase(transcript, "cough"):
        symptoms.append({"value": "cough", "status": "supported", "confidence": "medium"})

    allergies: list[dict] = []
    if _has_phrase(transcript, "no known allergies"):
        allergies.append({"value": "no known allergies", "status": "supported", "confidence": "high"})

    duration = _duration(transcript)
    payload = {
        "summary": "Patient reports " + ", ".join(item["value"] for item in symptoms) + "."
        if symptoms
        else "unknown",
        "entities": {
            "symptoms": symptoms,
            "duration": [] if duration == "unknown" else [{"value": duration, "status": "supported", "confidence": "medium"}],
            "severity": [],
            "medical_history": [],
            "medications": [],
            "allergies": allergies,
            "vitals": [],
        },
        "soap_note": {
            "subjective": {"text": "Patient reports " + ", ".join(item["value"] for item in symptoms) + "." if symptoms else "unknown"},
            "objective": {"text": "No vitals documented in transcript."},
            "assessment": {"text": "Findings remain transcript-limited; clinician review required."},
            "plan": {"text": "Supportive care discussion documented. Doctor validation required."},
        },
        "differential_diagnosis": [],
        "review_flags": [],
        "disclaimer": "Clinician review required.",
    }

    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": 1740000000,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 120,
            "total_tokens": 220,
        },
    }
