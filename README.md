# Clinic Copilot MVP

Open-source clinician review workspace built with Flutter and FastAPI. The product focuses on documentation support, review workflow, and auditability instead of autonomous medical decision-making.

## What it does

- Accepts a doctor-patient transcript
- Extracts structured clinical entities
- Produces a SOAP note with evidence
- Generates a patient-friendly summary
- Flags uncertainty and missing data
- Stores review state in SQLite
- Tracks audit events for generation and clinician review
- Supports clinician amendments to the summary and SOAP sections
- Supports clinician edits to structured entities like symptoms, allergies, meds, and vitals
- Serves multiple seeded demo cases so the UI works out of the box
- Exposes integrated agent actions in both the backend API and the Flutter dashboard
- Includes a production-style Flutter review console with queue filters, workspace switching, readiness scoring, and actionable agent results

## Safety choices

- Unsupported facts are returned as `unknown`
- Each extracted item includes evidence from the transcript
- Diagnoses are optional and clearly labeled as clinician-assist output
- Medication dosages are never invented

## Architecture

- `FastAPI` for backend APIs
- `Flutter` for the clinician review dashboard
- `SQLite` for local open-source persistence
- Optional `OpenAI-compatible` endpoint for note generation
- Deterministic fallback mode when no API key is set
- Multi-step clinical pipeline: entity extraction -> SOAP draft -> diagnosis -> treatment -> validation
- Local audit logging today, with Langfuse-style observability as the next integration point

## Run backend locally

```bash
cd /Users/prabhamini/Documents/clinic_copilot_mvp
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
uvicorn clinic_copilot.main:app --reload
```

## Real local model deployment

The backend can run against a fully local stack:

```text
Ollama -> LiteLLM -> FastAPI/Haystack
```

1. Install and start Ollama:

```bash
brew install ollama
ollama serve
ollama pull llama3.2:1b
```

2. Start the LiteLLM proxy:

```bash
cd /Users/prabhamini/Documents/clinic_copilot_mvp
source .venv/bin/activate
cp .env.example .env
litellm --config litellm.config.yaml --host 127.0.0.1 --port 4000
```

3. Start the backend:

```bash
cd /Users/prabhamini/Documents/clinic_copilot_mvp
source .venv/bin/activate
uvicorn clinic_copilot.main:app --reload
```

4. Test the Haystack route:

```bash
curl -X POST http://127.0.0.1:8000/v1/haystack-pipeline \
  -H "Content-Type: application/json" \
  -d '{
    "transcript": "Doctor: What brings you in today? Patient: I have fever and body pain for 2 days. Doctor: Any allergies? Patient: No known allergies."
  }'
```

## Run the Flutter dashboard

In a second terminal:

```bash
cd /Users/prabhamini/Documents/clinic_copilot_mvp/frontend
flutter run -d macos --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

The dashboard loads a seeded case queue automatically and provides:

- searchable queue management
- overview, note-studio, agents, and audit workspaces
- editable SOAP and structured-entity review
- readiness scoring and clinician sign-off controls
- agent results that can open cases or route reviewers to the relevant section

## API

### `GET /v1/demo-case`

Returns the seeded review case used by the Flutter dashboard.

### `GET /v1/cases`

Lists saved cases.

### `POST /v1/clinical-note`

Request body:

```json
{
  "transcript": "Doctor: What brings you in today?\nPatient: I have had a fever for three days.",
  "visit_context": {
    "locale": "en-IN",
    "specialty": "general_medicine"
  }
}
```

Creates and persists a new case record.

### `POST /v1/cases/{case_id}/review`

Stores a clinician review decision and feedback.

### `POST /v1/cases/{case_id}/amend`

Persists clinician-edited summary and SOAP text, resets the case to `pending_review`, and writes an audit log entry for the amendment.

### `GET /v1/audit-logs`

Returns recent audit entries, optionally filtered by `case_id`.

### `POST /v1/orchestrator/pre-visit`

Builds a pre-visit briefing from longitudinal retrieval context.

### `POST /v1/orchestrator/during-visit`

Appends transcript chunks to a live buffer and returns any triggered clinical nudge.

### `POST /v1/orchestrator/post-visit/{case_id}`

Runs post-visit parallel agents (scribe, billing, patient summary) and returns pre-sign consistency validation.

## Offline readiness command

Use this before clinic opening to verify sovereign mode prerequisites:

```bash
cd /Users/prabhamini/Documents/clinic_copilot_mvp
source .venv/bin/activate
python scripts/offline_readiness.py --json
```

Optional model pre-pull:

```bash
python scripts/offline_readiness.py --prepull
```

## Next steps

- Add auth and role-based access
- Add transcript chunking for long visits
- Add offline-first sync for the Flutter desktop app
- Replace SQLite with Postgres when multi-user deployment begins
