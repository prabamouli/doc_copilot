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

## Run the Flutter dashboard

In a second terminal:

```bash
cd /Users/prabhamini/Documents/clinic_copilot_mvp/frontend
flutter run -d macos --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

The dashboard loads a seeded case queue automatically, shows a case rail for switching between patients, and keeps the transcript, amendment workspace, review controls, and audit timeline in sync with the selected chart.

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

## Next steps

- Add auth and role-based access
- Add transcript chunking for long visits
- Add offline-first sync for the Flutter desktop app
- Replace SQLite with Postgres when multi-user deployment begins
