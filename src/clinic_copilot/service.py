from __future__ import annotations

from clinic_copilot.demo_data import demo_cases
from clinic_copilot.llm import LLMClient
from clinic_copilot.schemas import (
    CaseRecord,
    ClinicalNoteRequest,
    NoteAmendmentRequest,
    ReviewFlag,
    ReviewDecisionRequest,
)
from clinic_copilot.storage import ClinicRepository


class ClinicalDocumentationService:
    def __init__(self, llm_client: LLMClient, repository: ClinicRepository) -> None:
        self._llm_client = llm_client
        self._repository = repository

    def generate_note(self, request: ClinicalNoteRequest) -> CaseRecord:
        response = self._llm_client.generate_clinical_note(request)
        response.review_flags.extend(self._local_review_flags(request))
        return self._repository.create_case(request, response)

    def seed_demo_case(self) -> CaseRecord:
        seeded_cases = self.seed_demo_cases()
        return seeded_cases[0]

    def seed_demo_cases(self) -> list[CaseRecord]:
        return self._repository.seed_demo_cases(demo_cases())

    def list_cases(self) -> list[CaseRecord]:
        cases = self._repository.list_cases()
        if cases:
            return cases
        return self.seed_demo_cases()

    def get_case(self, case_id: str) -> CaseRecord:
        return self._repository.get_case(case_id)

    def review_case(self, case_id: str, review: ReviewDecisionRequest) -> CaseRecord:
        return self._repository.review_case(case_id, review)

    def amend_case(self, case_id: str, amendment: NoteAmendmentRequest) -> CaseRecord:
        return self._repository.amend_case(case_id, amendment)

    def audit_logs(self, case_id: str | None = None) -> list:
        return self._repository.list_audit_logs(case_id=case_id)

    def _local_review_flags(self, request: ClinicalNoteRequest) -> list[ReviewFlag]:
        lowered = request.transcript.lower()
        flags: list[ReviewFlag] = []

        if "allerg" not in lowered:
            flags.append(
                ReviewFlag(
                    issue="Allergy status not clearly documented",
                    severity="warning",
                    recommendation="Confirm allergies before finalizing the visit note.",
                )
            )

        if "dose" in lowered and "mg" not in lowered:
            flags.append(
                ReviewFlag(
                    issue="Medication mentioned without clear dosage",
                    severity="warning",
                    recommendation="Verify medication strength and schedule with the clinician.",
                )
            )

        return flags
