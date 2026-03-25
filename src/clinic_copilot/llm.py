from __future__ import annotations

import json
import re
import base64
from pathlib import Path
from typing import Any

from litellm import completion

from clinic_copilot.config import settings
from clinic_copilot.prompts import (
    build_critic_review_prompt,
    build_diagnosis_confidence_prompt,
    build_patient_friendly_summary_prompt,
    build_patient_timeline_summary_prompt,
    build_prescription_generator_prompt,
    build_rag_medical_validation_prompt,
    build_full_output_validation_prompt,
    build_diagnosis_prompt,
    build_entity_extraction_prompt,
    build_soap_prompt,
    build_system_prompt,
    build_treatment_prompt,
    build_validation_prompt,
)
from clinic_copilot.regulatory_vault import regulatory_vault
from clinic_copilot.schemas import (
    ClinicalEntities,
    ClinicalNoteRequest,
    ClinicalNoteResponse,
    DifferentialDiagnosisItem,
    ExtractedFact,
    ReviewFlag,
    SoapDraftOutput,
    SoapAssessmentItem,
    SoapNote,
    SoapSection,
    TreatmentPlanDraft,
    ValidationResult,
)


class LLMClient:
    def __init__(self) -> None:
        self._gateway_enabled = bool(settings.openai_api_key or settings.openai_base_url)
        self._standard_model = settings.llm_standard_model or settings.openai_model or settings.ollama_model
        self._clinical_reasoning_model = (
            settings.llm_clinical_reasoning_model or settings.openai_model or self._standard_model
        )
        self._vision_model = settings.llm_vision_model or self._standard_model

    def generate_clinical_note(self, request: ClinicalNoteRequest) -> ClinicalNoteResponse:
        if not self._gateway_enabled:
            return self._fallback_note(request)

        entities_payload = self._call_json(build_entity_extraction_prompt(request), priority="Standard")
        entities = self._parse_entities(entities_payload)

        soap_payload = self._call_json(build_soap_prompt(request), priority="Standard")
        soap_draft = SoapDraftOutput.model_validate(soap_payload)

        diagnosis_payload = self._call_json(build_diagnosis_prompt(entities), priority="Clinical_Reasoning")
        diagnosis_items = self._normalize_diagnosis_items(diagnosis_payload)
        differential = [
            DifferentialDiagnosisItem(
                condition=item["condition"],
                rationale=item["reason"],
                confidence=item["confidence"],
            )
            for item in diagnosis_items
        ]

        treatment_payload = self._call_json(
            build_treatment_prompt(entities, request.include_differential_diagnosis)
            ,
            priority="Clinical_Reasoning",
        )
        treatment = TreatmentPlanDraft.model_validate(treatment_payload)

        validation_payload = self._call_json(
            build_validation_prompt(
                request,
                entities,
                soap_payload,
                diagnosis_items,
                treatment_payload,
            )
            ,
            priority="Clinical_Reasoning",
        )
        validation = ValidationResult.model_validate(validation_payload)

        soap_note = SoapNote(
            subjective=SoapSection(text=soap_draft.subjective),
            objective=SoapSection(text=soap_draft.objective),
            assessment=SoapSection(text=self._format_assessment_text(soap_draft, differential)),
            plan=SoapSection(text=self._format_plan_text(treatment)),
        )

        review_flags = [
            ReviewFlag(
                issue=issue,
                severity="warning",
                recommendation="Clinician review required before finalizing the note.",
            )
            for issue in validation.issues
        ]
        if not validation.valid and not review_flags:
            review_flags.append(
                ReviewFlag(
                    issue="Validation agent marked the draft as invalid",
                    severity="warning",
                    recommendation="Review all sections for unsupported or inconsistent content.",
                )
            )

        return ClinicalNoteResponse(
            summary=self._build_summary(entities, request.transcript),
            entities=entities,
            soap_note=soap_note,
            differential_diagnosis=differential if request.include_differential_diagnosis else [],
            review_flags=review_flags,
        )

    def _fallback_note(self, request: ClinicalNoteRequest) -> ClinicalNoteResponse:
        entities = self._fallback_entities(request.transcript)
        soap_draft = SoapDraftOutput(
            subjective=self._extract_patient_statement(request.transcript),
            objective="unknown",
            assessment=[
                SoapAssessmentItem(
                    condition="unknown",
                    confidence="low",
                    reason="Insufficient structured evidence for a reliable local diagnosis draft.",
                )
            ],
            plan=TreatmentPlanDraft(
                medications=[],
                tests=[],
                advice=["Doctor validation required"],
                follow_up="unknown",
                warning="Doctor validation required",
            ),
        )
        return ClinicalNoteResponse(
            summary=self._build_summary(entities, request.transcript),
            entities=entities,
            soap_note=SoapNote(
                subjective=SoapSection(text=soap_draft.subjective),
                objective=SoapSection(text=soap_draft.objective),
                assessment=SoapSection(text=self._format_assessment_text(soap_draft, [])),
                plan=SoapSection(text=self._format_plan_text(soap_draft.plan)),
            ),
            differential_diagnosis=[],
            review_flags=[
                ReviewFlag(
                    issue="Running in local fallback mode without a configured model backend",
                    severity="info",
                    recommendation="Configure an OpenAI-compatible model to enable the full multi-step pipeline.",
                )
            ],
        )

    def _dispatch_model(self, priority: str) -> str:
        if priority == "Clinical_Reasoning":
            return self._clinical_reasoning_model
        return self._standard_model

    def _call_json(self, prompt: str, priority: str = "Standard") -> Any:
        masked_payload = regulatory_vault.deidentify(
            text=prompt,
            route="litellm.completion",
            metadata={"priority": priority, "model": self._dispatch_model(priority)},
        )
        response_text = self._safe_completion_json(
            messages=[
                {"role": "system", "content": build_system_prompt()},
                {"role": "user", "content": str(masked_payload["deidentified_text"])},
            ],
            priority=priority,
        )
        reidentified = regulatory_vault.reidentify(response_text, mapping_id=str(masked_payload["mapping_id"]))
        return json.loads(reidentified)

    def _safe_completion_json(self, messages: list[dict[str, str]], priority: str) -> str:
        model = self._dispatch_model(priority)
        try:
            response = completion(
                model=model,
                api_base=settings.openai_base_url,
                api_key=settings.openai_api_key,
                messages=messages,
                response_format={"type": "json_object"},
                timeout=max(10, int(settings.llm_timeout_seconds)),
            )
            return str(response.choices[0].message.content)
        except Exception as exc:
            if priority != "Clinical_Reasoning":
                raise RuntimeError(f"LiteLLM standard inference failed: {exc}") from exc

            if not self._is_fallback_eligible(exc):
                raise RuntimeError(f"LiteLLM clinical reasoning inference failed: {exc}") from exc

            try:
                fallback_response = completion(
                    model=self._standard_model,
                    api_base=settings.openai_base_url,
                    api_key=settings.openai_api_key,
                    messages=messages,
                    response_format={"type": "json_object"},
                    timeout=max(10, int(settings.llm_timeout_seconds)),
                )
                return str(fallback_response.choices[0].message.content)
            except Exception as fallback_exc:
                raise RuntimeError(
                    "LiteLLM clinical reasoning fallback failed after local gateway error: "
                    f"primary={exc}; fallback={fallback_exc}"
                ) from fallback_exc

    def _is_fallback_eligible(self, exc: Exception) -> bool:
        message = str(exc).lower()
        trigger_phrases = (
            "connection refused",
            "failed to establish a new connection",
            "gpu out of memory",
            "cuda out of memory",
            "out of memory",
            "oom",
        )
        return any(token in message for token in trigger_phrases)

    def _parse_entities(self, payload: dict[str, list[str]]) -> ClinicalEntities:
        def facts(key: str) -> list[ExtractedFact]:
            return [
                ExtractedFact(value=value, status="supported", confidence="medium")
                for value in payload.get(key, [])
                if isinstance(value, str) and value.strip()
            ]

        history_values = [
            value
            for value in payload.get("history", [])
            if isinstance(value, str) and value.strip()
        ]
        medical_history_values = [
            value
            for value in payload.get("medical_history", [])
            if isinstance(value, str) and value.strip()
        ]
        lifestyle_values = [
            value
            for value in payload.get("lifestyle", [])
            if isinstance(value, str) and value.strip()
        ]
        merged_history = []
        seen: set[str] = set()
        for value in [*history_values, *medical_history_values, *lifestyle_values]:
            normalized = value.strip()
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            merged_history.append(normalized)

        return ClinicalEntities(
            symptoms=facts("symptoms"),
            duration=facts("duration"),
            severity=facts("severity"),
            medical_history=[
                ExtractedFact(value=value, status="supported", confidence="medium")
                for value in merged_history
            ],
            medications=facts("medications"),
            allergies=facts("allergies"),
            vitals=facts("vitals"),
        )

    def _normalize_diagnosis_items(self, payload: Any) -> list[dict[str, str]]:
        # Accept either the new array format or legacy {"conditions": [...]} format.
        raw_items: list[Any]
        if isinstance(payload, list):
            raw_items = payload
        elif isinstance(payload, dict):
            maybe_items = payload.get("conditions", [])
            raw_items = maybe_items if isinstance(maybe_items, list) else []
        else:
            raw_items = []

        normalized: list[dict[str, str]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            condition = str(raw.get("condition", "")).strip()
            reason = str(raw.get("reason", "")).strip()
            confidence = str(raw.get("confidence", "low")).strip().lower()
            if not condition or not reason:
                continue
            if confidence not in {"low", "medium", "high"}:
                confidence = "low"

            # Conservative guardrail: demote overconfident wording in uncertain drafts.
            lowered_reason = reason.lower()
            if any(token in lowered_reason for token in ("definite", "certain", "confirmed", "diagnosed")):
                confidence = "low"

            normalized.append(
                {
                    "condition": condition,
                    "reason": reason,
                    "confidence": confidence,
                }
            )
            if len(normalized) >= 3:
                break

        return normalized

    def _build_summary(self, entities: ClinicalEntities, transcript: str) -> str:
        parts: list[str] = []
        if entities.symptoms:
            parts.append(
                "Patient reports " + ", ".join(item.value for item in entities.symptoms[:3]) + "."
            )
        if entities.duration:
            parts.append("Duration noted: " + ", ".join(item.value for item in entities.duration[:2]) + ".")
        if entities.allergies:
            parts.append("Allergy status: " + ", ".join(item.value for item in entities.allergies[:2]) + ".")
        if not parts:
            parts.append(transcript[:120].strip() + ("..." if len(transcript) > 120 else ""))
        return " ".join(parts)

    def _fallback_entities(self, transcript: str) -> ClinicalEntities:
        lower = transcript.lower()

        def maybe(term: str, label: str | None = None) -> list[ExtractedFact]:
            if term in lower:
                return [ExtractedFact(value=label or term, status="supported", confidence="medium")]
            return []

        duration_matches = re.findall(r"\b(\d+\s+(?:day|days|week|weeks|month|months))\b", lower)
        allergies = []
        if "no known allergies" in lower or "no known drug allergies" in lower:
            allergies = [ExtractedFact(value="no known allergies", status="supported", confidence="high")]

        return ClinicalEntities(
            symptoms=(
                maybe("fever")
                + maybe("cough")
                + maybe("sore throat", "sore throat")
                + maybe("body pain", "body pain")
            ),
            duration=[
                ExtractedFact(value=value, status="supported", confidence="medium")
                for value in duration_matches
            ],
            severity=[],
            medical_history=[],
            medications=[],
            allergies=allergies,
            vitals=[],
        )

    def _extract_patient_statement(self, transcript: str) -> str:
        match = re.search(r"Patient:\s*(.+)", transcript, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return transcript[:180].strip() + ("..." if len(transcript) > 180 else "")

    def _format_assessment_text(
        self,
        soap_draft: SoapDraftOutput,
        differential: list[DifferentialDiagnosisItem],
    ) -> str:
        if differential:
            return " ".join(
                f"{item.condition} ({item.confidence} confidence): {item.rationale}"
                for item in differential
            )
        if soap_draft.assessment:
            return " ".join(
                f"{item.condition} ({item.confidence} confidence): {item.reason}"
                for item in soap_draft.assessment
            )
        return "unknown"

    def _format_plan_text(self, treatment: TreatmentPlanDraft) -> str:
        sections: list[str] = []
        if treatment.medications:
            sections.append("Medications: " + ", ".join(treatment.medications))
        if treatment.tests:
            sections.append("Tests: " + ", ".join(treatment.tests))
        if treatment.advice:
            sections.append("Advice: " + ", ".join(treatment.advice))
        if treatment.follow_up:
            sections.append("Follow-up: " + treatment.follow_up)
        if treatment.warning:
            sections.append("Warning: " + treatment.warning)
        return " ".join(sections) if sections else "unknown"

    def analyze_visual_objective(self, media_path: str, media_type: str) -> dict[str, Any]:
        path = Path(media_path)
        if not path.exists() or not path.is_file():
            raise ValueError(f"Media file not found: {media_path}")

        mime = _infer_media_mime(path, media_type)
        if not self._gateway_enabled:
            return {
                "media_type": media_type,
                "objective_text": _fallback_visual_objective(media_type),
                "model": "fallback-local",
                "confidence": "low",
            }

        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
        prompt = (
            "You are VisionAgent for clinical objective observations. Return valid JSON only: "
            '{"objective_text":"...", "confidence":"low|medium|high"}. '
            "Describe only observable findings in clinical language and avoid diagnosis."
        )

        try:
            response = completion(
                model=self._vision_model,
                api_base=settings.openai_base_url,
                api_key=settings.openai_api_key,
                timeout=max(15, int(settings.llm_timeout_seconds)),
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Return JSON only."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{encoded}"},
                            },
                        ],
                    },
                ],
            )
            raw = str(response.choices[0].message.content or "{}").strip()
            payload = json.loads(raw)
            objective = str(payload.get("objective_text", "")).strip() or _fallback_visual_objective(media_type)
            confidence = str(payload.get("confidence", "medium")).lower()
            if confidence not in {"low", "medium", "high"}:
                confidence = "medium"
            return {
                "media_type": media_type,
                "objective_text": objective,
                "model": self._vision_model,
                "confidence": confidence,
            }
        except Exception:
            return {
                "media_type": media_type,
                "objective_text": _fallback_visual_objective(media_type),
                "model": "fallback-local",
                "confidence": "low",
            }

    def generate_patient_after_visit_summary(self, soap_note: SoapNote) -> dict[str, Any]:
        if not self._gateway_enabled:
            return _fallback_patient_after_visit_summary(soap_note)

        prompt = (
            "You are PatientCommunicatorAgent. Convert this clinician SOAP note into patient-friendly "
            "English at a 5th-grade reading level. Return valid JSON only with keys: "
            '{"what_we_found":[],"what_you_need_to_do_next":[],"when_to_get_help":[]}. '
            "Use short bullet-style sentences. Do not include diagnosis certainty unless clearly stated.\n\n"
            f"Subjective:\n{soap_note.subjective.text}\n\n"
            f"Objective:\n{soap_note.objective.text}\n\n"
            f"Assessment:\n{soap_note.assessment.text}\n\n"
            f"Plan:\n{soap_note.plan.text}\n"
        )

        try:
            payload = self._call_json(prompt, priority="Standard")
            found = _normalize_plain_list(payload.get("what_we_found"))
            next_steps = _normalize_plain_list(payload.get("what_you_need_to_do_next"))
            help_when = _normalize_plain_list(payload.get("when_to_get_help"))
            if not found and not next_steps:
                return _fallback_patient_after_visit_summary(soap_note)
            return {
                "what_we_found": found,
                "what_you_need_to_do_next": next_steps,
                "when_to_get_help": help_when,
            }
        except Exception:
            return _fallback_patient_after_visit_summary(soap_note)

    def summarize_patient_timeline(self, past_records: Any) -> dict[str, Any]:
        if not self._gateway_enabled:
            return {
                "chronic_conditions": [],
                "recurring_symptoms": [],
                "medication_history": [],
                "trend_summary": "Insufficient model backend to summarize timeline.",
            }

        try:
            payload = self._call_json(build_patient_timeline_summary_prompt(past_records), priority="Clinical_Reasoning")
        except Exception:
            return {
                "chronic_conditions": [],
                "recurring_symptoms": [],
                "medication_history": [],
                "trend_summary": "Unable to summarize timeline from current records.",
            }

        def list_field(key: str) -> list[str]:
            raw = payload.get(key, []) if isinstance(payload, dict) else []
            if not isinstance(raw, list):
                return []
            return [str(item).strip() for item in raw if str(item).strip()]

        trend_summary = ""
        if isinstance(payload, dict):
            trend_summary = str(payload.get("trend_summary", "")).strip()

        return {
            "chronic_conditions": list_field("chronic_conditions"),
            "recurring_symptoms": list_field("recurring_symptoms"),
            "medication_history": list_field("medication_history"),
            "trend_summary": trend_summary,
        }

    def rag_validate_diagnosis(self, diagnosis: Any, context: Any) -> dict[str, Any]:
        if not self._gateway_enabled:
            return {
                "supported": False,
                "evidence": "",
                "confidence": "low",
            }

        try:
            payload = self._call_json(
                build_rag_medical_validation_prompt(diagnosis=diagnosis, context=context),
                priority="Clinical_Reasoning",
            )
        except Exception:
            return {
                "supported": False,
                "evidence": "",
                "confidence": "low",
            }

        supported = bool(payload.get("supported", False)) if isinstance(payload, dict) else False
        evidence = str(payload.get("evidence", "")).strip() if isinstance(payload, dict) else ""
        confidence = str(payload.get("confidence", "low")).strip().lower() if isinstance(payload, dict) else "low"
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"

        # Hard rule: if no evidence, force unsupported.
        if not evidence:
            supported = False

        return {
            "supported": supported,
            "evidence": evidence,
            "confidence": confidence,
        }

    def validate_full_clinical_output(self, full_output: Any) -> dict[str, Any]:
        if not self._gateway_enabled:
            return {
                "valid": False,
                "issues": ["Model backend unavailable for validation."],
                "severity": "medium",
            }

        try:
            payload = self._call_json(
                build_full_output_validation_prompt(full_output),
                priority="Clinical_Reasoning",
            )
        except Exception:
            return {
                "valid": False,
                "issues": ["Unable to validate full clinical output."],
                "severity": "medium",
            }

        valid = bool(payload.get("valid", False)) if isinstance(payload, dict) else False
        issues_raw = payload.get("issues", []) if isinstance(payload, dict) else []
        issues = [str(item).strip() for item in issues_raw if str(item).strip()] if isinstance(issues_raw, list) else []
        severity = str(payload.get("severity", "low")).strip().lower() if isinstance(payload, dict) else "low"
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        if not valid and not issues:
            issues = ["Validation flagged concerns requiring clinician review."]

        return {
            "valid": valid,
            "issues": issues,
            "severity": severity,
        }

    def critic_review_output(self, output: Any) -> dict[str, Any]:
        if not self._gateway_enabled:
            return {
                "errors": ["Model backend unavailable for critic review."],
                "improvements": [],
                "final_verdict": "needs_revision",
            }

        try:
            payload = self._call_json(
                build_critic_review_prompt(output),
                priority="Clinical_Reasoning",
            )
        except Exception:
            return {
                "errors": ["Unable to run critic review on current output."],
                "improvements": [],
                "final_verdict": "needs_revision",
            }

        errors_raw = payload.get("errors", []) if isinstance(payload, dict) else []
        improvements_raw = payload.get("improvements", []) if isinstance(payload, dict) else []
        verdict = str(payload.get("final_verdict", "needs_revision")).strip().lower() if isinstance(payload, dict) else "needs_revision"
        if verdict not in {"acceptable", "needs_revision"}:
            verdict = "needs_revision"

        return {
            "errors": [str(item).strip() for item in errors_raw if str(item).strip()] if isinstance(errors_raw, list) else [],
            "improvements": [str(item).strip() for item in improvements_raw if str(item).strip()]
            if isinstance(improvements_raw, list)
            else [],
            "final_verdict": verdict,
        }

    def score_diagnosis_confidence(self, diagnosis: Any) -> dict[str, Any]:
        if not self._gateway_enabled:
            return {
                "score": 25,
                "reason": "Model backend unavailable; default conservative confidence score used.",
            }

        try:
            payload = self._call_json(
                build_diagnosis_confidence_prompt(diagnosis),
                priority="Clinical_Reasoning",
            )
        except Exception:
            return {
                "score": 25,
                "reason": "Unable to score diagnosis confidence from current input.",
            }

        raw_score = payload.get("score", 0) if isinstance(payload, dict) else 0
        try:
            score = int(raw_score)
        except Exception:
            score = 0
        score = max(0, min(100, score))
        reason = str(payload.get("reason", "")).strip() if isinstance(payload, dict) else ""
        if not reason:
            reason = "Confidence based on available diagnostic evidence only."

        return {
            "score": score,
            "reason": reason,
        }

    def generate_patient_friendly_summary(self, soap_note: Any) -> dict[str, Any]:
        if not self._gateway_enabled:
            return {
                "summary": "Your doctor reviewed your symptoms, exam findings, and treatment plan. Please follow the care plan and contact your clinic if symptoms worsen.",
            }

        try:
            payload = self._call_json(
                build_patient_friendly_summary_prompt(soap_note),
                priority="Standard",
            )
        except Exception:
            return {
                "summary": "Your doctor reviewed your symptoms, exam findings, and treatment plan. Please follow the care plan and contact your clinic if symptoms worsen.",
            }

        summary = str(payload.get("summary", "")).strip() if isinstance(payload, dict) else ""
        if not summary:
            summary = "Your doctor reviewed your symptoms, exam findings, and treatment plan. Please follow the care plan and contact your clinic if symptoms worsen."

        # Enforce max 150 words.
        words = summary.split()
        if len(words) > 150:
            summary = " ".join(words[:150]).strip()

        return {"summary": summary}

    def generate_prescription_draft(self, treatment: Any) -> dict[str, Any]:
        if not self._gateway_enabled:
            return {
                "medications": [],
                "dosage": [],
                "instructions": [],
                "notes": "Doctor must verify",
            }

        try:
            payload = self._call_json(
                build_prescription_generator_prompt(treatment),
                priority="Clinical_Reasoning",
            )
        except Exception:
            return {
                "medications": [],
                "dosage": [],
                "instructions": [],
                "notes": "Doctor must verify",
            }

        def normalize_list(key: str) -> list[str]:
            raw = payload.get(key, []) if isinstance(payload, dict) else []
            if not isinstance(raw, list):
                return []
            return [str(item).strip() for item in raw if str(item).strip()]

        return {
            "medications": normalize_list("medications"),
            "dosage": normalize_list("dosage"),
            "instructions": normalize_list("instructions"),
            "notes": "Doctor must verify",
        }


def _infer_media_mime(path: Path, media_type: str) -> str:
    suffix = path.suffix.lower()
    if media_type == "video":
        if suffix in {".mp4", ".m4v"}:
            return "video/mp4"
        if suffix == ".mov":
            return "video/quicktime"
        return "video/mp4"

    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"


def _fallback_visual_objective(media_type: str) -> str:
    if media_type == "video":
        return (
            "Gait video reviewed. Observation suggests asymmetry in stride and reduced stance stability. "
            "Recommend focused musculoskeletal and neurologic exam correlation."
        )
    return (
        "Skin image reviewed. Visible lesion with erythematous surface changes and localized border irregularity. "
        "No definitive diagnosis assigned from image alone."
    )


def _normalize_plain_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        lines = [item.strip(" -\t") for item in re.split(r"[\n;]+", value) if item.strip()]
        return [item for item in lines if item]
    return []


def _fallback_patient_after_visit_summary(soap_note: SoapNote) -> dict[str, Any]:
    found = []
    next_steps = []

    if soap_note.assessment.text.strip() and soap_note.assessment.text.strip().lower() != "unknown":
        found.append(f"Your doctor is watching for: {soap_note.assessment.text.strip()}")
    if soap_note.objective.text.strip() and soap_note.objective.text.strip().lower() != "unknown":
        found.append(f"Exam notes: {soap_note.objective.text.strip()}")
    if not found:
        found.append("Your doctor reviewed your symptoms and exam today.")

    if soap_note.plan.text.strip() and soap_note.plan.text.strip().lower() != "unknown":
        next_steps.extend(_normalize_plain_list(soap_note.plan.text))
    if not next_steps:
        next_steps.append("Follow your doctor instructions and take medicines as prescribed.")

    return {
        "what_we_found": found[:4],
        "what_you_need_to_do_next": next_steps[:6],
        "when_to_get_help": [
            "Get help right away if pain, breathing trouble, or fever gets worse.",
            "Call the clinic if you are not improving or you have new symptoms.",
        ],
    }
