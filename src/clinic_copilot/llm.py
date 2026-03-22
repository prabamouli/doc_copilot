from __future__ import annotations

import json
import re
from typing import Any

from litellm import completion

from clinic_copilot.config import settings
from clinic_copilot.prompts import (
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

    def generate_clinical_note(self, request: ClinicalNoteRequest) -> ClinicalNoteResponse:
        if not self._gateway_enabled:
            return self._fallback_note(request)

        entities_payload = self._call_json(build_entity_extraction_prompt(request), priority="Standard")
        entities = self._parse_entities(entities_payload)

        soap_payload = self._call_json(build_soap_prompt(request), priority="Standard")
        soap_draft = SoapDraftOutput.model_validate(soap_payload)

        diagnosis_payload = self._call_json(build_diagnosis_prompt(entities), priority="Clinical_Reasoning")
        diagnosis_items = diagnosis_payload.get("conditions", [])
        differential = [
            DifferentialDiagnosisItem(
                condition=item.get("condition", "unknown"),
                rationale=item.get("reason", "unknown"),
                confidence=item.get("confidence", "low"),
            )
            for item in diagnosis_items[:3]
            if item.get("condition")
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

        return ClinicalEntities(
            symptoms=facts("symptoms"),
            duration=facts("duration"),
            severity=facts("severity"),
            medical_history=facts("history"),
            medications=facts("medications"),
            allergies=facts("allergies"),
            vitals=facts("vitals"),
        )

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
        return " ".join(sections) if sections else "unknown"
