import json

from clinic_copilot.schemas import ClinicalEntities, ClinicalNoteRequest


SCRIBE_SYSTEM_PROMPT = """
You are ScribeAgent for longitudinal clinical documentation in a RAG-heavy workflow.

Non-negotiable rules:
- You MUST use the provided Retrieved Context for historical facts.
- You MUST NOT invent historical facts that are not explicitly present in Retrieved Context.
- If required historical information is absent, write exactly: Information not found in historical records.

Citation rules:
- Every statement about patient history MUST include a citation in square brackets.
- Citation format: [Source: Visit <visit_id_or_date>]
- If multiple records support one statement, include multiple citations.

Conflict handling:
- Current Transcript has priority for current-visit facts.
- If Retrieved Context contradicts Current Transcript, add an explicit entry in an Audit section.
- Audit entries must include field, current_transcript_value, historical_value, and cited source.

Output rules:
- Return valid JSON only.
- Keep statements concise and clinically grounded.
- Do not guess.
""".strip()


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


def build_scribe_system_prompt(retrieved_context: str) -> str:
    return (
        f"{SCRIBE_SYSTEM_PROMPT}\n\n"
        "Retrieved Context:\n"
        f"{retrieved_context.strip() or 'No retrieved historical context provided.'}"
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
  "vitals": [],
  "lifestyle": []
}}

If not present -> empty array
Do not infer.

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
Suggest possible diagnoses based ONLY on input structured data.

Rules:
- Max 3 conditions
- Include reasoning for each diagnosis
- Include confidence as low|medium|high
- No definitive statements
- Do not infer beyond available data

Structured data:
{json.dumps(_entities_for_prompt(entities), indent=2)}

Output JSON (array only):
[
  {{
    "condition": "",
    "reason": "",
    "confidence": "low|medium|high"
  }}
]
""".strip()


def build_treatment_prompt(entities: ClinicalEntities, include_differential: bool) -> str:
    prompt_tail = "Use only common first-line or supportive suggestions. Add 'Doctor validation required'."
    if not include_differential:
        prompt_tail += " If insufficient data exists, keep medications and tests empty."
    return f"""
text id="treat001"
Generate a conservative treatment plan.

Rules:
- Only common first-line treatments
- Avoid risky interventions
- Keep recommendations conservative and non-definitive
- warning must be exactly "Doctor validation required"

Structured data:
{json.dumps(_entities_for_prompt(entities), indent=2)}

Output JSON:
{{
  "medications": [],
  "tests": [],
  "advice": [],
  "follow_up": "",
  "warning": "Doctor validation required"
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


def build_patient_timeline_summary_prompt(past_records: object) -> str:
    return f"""
text id="timeline001"
Summarize patient history across visits.

INPUT:
{json.dumps(past_records, indent=2, default=str)}

OUTPUT JSON:
{{
  "chronic_conditions": [],
  "recurring_symptoms": [],
  "medication_history": [],
  "trend_summary": ""
}}

RULES:
- Identify patterns
- Do not assume causation
- Use only evidence present in input
""".strip()


def build_rag_medical_validation_prompt(diagnosis: object, context: object) -> str:
    return f"""
text id="ragval001"
Validate diagnosis using retrieved medical knowledge.

INPUT:
{{
  "diagnosis": {json.dumps(diagnosis, default=str)},
  "context": {json.dumps(context, default=str)}
}}

OUTPUT JSON:
{{
  "supported": true,
  "evidence": "",
  "confidence": ""
}}

RULES:
- If no evidence, supported must be false
- Do not infer facts not present in retrieved context
- Keep evidence concise and directly relevant
""".strip()


def build_full_output_validation_prompt(full_output: object) -> str:
    return f"""
text id="fullval001"
Validate full clinical output.

INPUT:
{json.dumps(full_output, indent=2, default=str)}

CHECK:
- Hallucinations
- Logical inconsistencies
- Unsafe recommendations

OUTPUT JSON:
{{
  "valid": true,
  "issues": [],
  "severity": "low|medium|high"
}}
""".strip()


def build_critic_review_prompt(output: object) -> str:
    return f"""
text id="critic001"
Critically review the diagnosis and treatment.

INPUT:
{json.dumps(output, indent=2, default=str)}

OUTPUT JSON:
{{
  "errors": [],
  "improvements": [],
  "final_verdict": "acceptable|needs_revision"
}}
""".strip()


def build_diagnosis_confidence_prompt(diagnosis: object) -> str:
    return f"""
text id="confscore001"
Assign confidence score.

INPUT:
{json.dumps(diagnosis, indent=2, default=str)}

OUTPUT JSON:
{{
  "score": 0,
  "reason": ""
}}

RULES:
- score must be an integer from 0 to 100
- be conservative when evidence is weak
- reason must be concise and evidence-grounded
""".strip()


def build_patient_friendly_summary_prompt(soap_note: object) -> str:
    return f"""
text id="pfsummary001"
Convert clinical data into simple explanation.

INPUT:
{json.dumps(soap_note, indent=2, default=str)}

OUTPUT JSON:
{{
  "summary": ""
}}

RULES:
- Simple English
- No jargon
- Max 150 words
""".strip()


def build_prescription_generator_prompt(treatment: object) -> str:
    return f"""
text id="rxgen001"
Generate prescription draft.

INPUT:
{json.dumps(treatment, indent=2, default=str)}

OUTPUT JSON:
{{
  "medications": [],
  "dosage": [],
  "instructions": [],
  "notes": "Doctor must verify"
}}

RULES:
- Use only treatment data provided in input
- Keep items concise and practical
- notes must be exactly "Doctor must verify"
""".strip()
