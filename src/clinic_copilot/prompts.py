import json

from clinic_copilot.schemas import ClinicalEntities, ClinicalNoteRequest


def build_system_prompt() -> str:
    return (
        "You are a clinical AI assistant designed for structured medical documentation.\n\n"
        "STRICT RULES:\n"
        "- Only use information explicitly present in input\n"
        "- Do NOT hallucinate or invent facts\n"
        "- Clearly mark uncertainty\n"
        "- Follow clinical reasoning standards\n"
        "- Output MUST be valid JSON\n\n"
        "If information is missing -> return \"unknown\""
    )


def build_pipeline_context(request: ClinicalNoteRequest) -> str:
    return f"""
Visit context:
- Locale: {request.visit_context.locale}
- Specialty: {request.visit_context.specialty}
- Clinician name: {request.visit_context.clinician_name or "unknown"}

Conversation:
{request.transcript}
""".strip()


def build_entity_extraction_prompt(request: ClinicalNoteRequest) -> str:
    return f"""
text id="entity001"
Extract structured medical entities.

Return JSON only:
{{
  "symptoms": [],
  "duration": [],
  "severity": [],
  "history": [],
  "medications": [],
  "allergies": [],
  "vitals": []
}}

If not present -> empty array

{build_pipeline_context(request)}
""".strip()


def build_soap_prompt(request: ClinicalNoteRequest) -> str:
    return f"""
text id="soap001"
TASK: Convert conversation into SOAP format.

INPUT:
{build_pipeline_context(request)}

OUTPUT JSON:
{{
  "subjective": "...",
  "objective": "...",
  "assessment": [
    {{
      "condition": "",
      "confidence": "low|medium|high",
      "reason": ""
    }}
  ],
  "plan": {{
    "medications": [],
    "tests": [],
    "advice": [],
    "follow_up": ""
  }}
}}

RULES:
- No assumptions
- No hallucination
- Keep concise
""".strip()


def build_diagnosis_prompt(entities: ClinicalEntities) -> str:
    return f"""
text id="diag001"
Based ONLY on extracted data, suggest possible conditions.

Rules:
- Max 3 conditions
- Include reasoning
- Include confidence
- Do NOT claim certainty

Structured data:
{json.dumps(_entities_for_prompt(entities), indent=2)}

Output JSON:
{{
  "conditions": [
    {{
      "condition": "",
      "reason": "",
      "confidence": "low|medium|high"
    }}
  ]
}}
""".strip()


def build_treatment_prompt(entities: ClinicalEntities, include_differential: bool) -> str:
    prompt_tail = "Use only common first-line or supportive suggestions. Add 'Doctor validation required'."
    if not include_differential:
        prompt_tail += " If insufficient data exists, keep medications and tests empty."
    return f"""
text id="treat001"
Generate a conservative treatment plan.

Rules:
- Only common treatments
- Avoid risky advice
- Add "Doctor validation required"

Structured data:
{json.dumps(_entities_for_prompt(entities), indent=2)}

Output JSON:
{{
  "medications": [],
  "tests": [],
  "advice": [],
  "follow_up": ""
}}

{prompt_tail}
""".strip()


def build_validation_prompt(
    request: ClinicalNoteRequest,
    entities: ClinicalEntities,
    soap_json: dict,
    diagnosis_json: list[dict],
    treatment_json: dict,
) -> str:
    return f"""
text id="validate001"
Check the generated output.

Rules:
- Detect hallucination
- Check medical consistency
- Flag unsafe recommendations

Conversation:
{request.transcript}

Entities:
{json.dumps(_entities_for_prompt(entities), indent=2)}

SOAP:
{json.dumps(soap_json, indent=2)}

Diagnosis:
{json.dumps(diagnosis_json, indent=2)}

Treatment:
{json.dumps(treatment_json, indent=2)}

Output:
{{
  "valid": true,
  "issues": []
}}
""".strip()


def _entities_for_prompt(entities: ClinicalEntities) -> dict[str, list[str]]:
    return {
        "symptoms": [item.value for item in entities.symptoms],
        "duration": [item.value for item in entities.duration],
        "severity": [item.value for item in entities.severity],
        "history": [item.value for item in entities.medical_history],
        "medications": [item.value for item in entities.medications],
        "allergies": [item.value for item in entities.allergies],
        "vitals": [item.value for item in entities.vitals],
    }
