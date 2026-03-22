# Clinic Copilot Agents

These agent definitions are project-relevant Hive exports for the current Clinic Copilot MVP.

## Included agents

- `clinical_intake_agent`
  Turns a transcript or visit summary into structured clinical documentation artifacts.

- `note_safety_reviewer`
  Reviews a generated note for missing information, unsupported claims, and clinician-review risks.

- `scribe_agent`
  Produces clinician-ready structured notes and escalates to safety tooling when high-risk medications are detected.

- `review_queue_orchestrator`
  Helps a clinician or operations lead triage a queue of pending charts and decide what needs urgent attention first.

- `billing_optimizer_agent`
  Detects potential revenue leakage by finding billable procedures in SOAP sections that are missing from summary-level capture, then suggests CPT codes.

## Why these agents

These map cleanly to the product we have already built:

- transcript intake and note generation
- documentation QA and safety checks
- multi-case dashboard review workflow

## Suggested usage order

1. `clinical_intake_agent`
2. `scribe_agent`
3. `note_safety_reviewer`
4. `review_queue_orchestrator`

## RAG note

None of these agents require RAG for the current MVP.
If you later add clinic-specific SOPs, prior patient history, or citation-backed guideline lookup, a retrieval agent would be the next one to add.
