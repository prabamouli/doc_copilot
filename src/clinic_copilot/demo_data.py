from clinic_copilot.schemas import (
    ClinicalEntities,
    ClinicalNoteRequest,
    ClinicalNoteResponse,
    DifferentialDiagnosisItem,
    EvidenceItem,
    ExtractedFact,
    ReviewFlag,
    SoapNote,
    SoapSection,
    VisitContext,
)


def demo_cases() -> list[tuple[str, ClinicalNoteRequest, ClinicalNoteResponse]]:
    return [
        (
            "Seeded Demo Patient",
            ClinicalNoteRequest(
                transcript="""Doctor: Hello, what brings you in today?
Patient: I have had a fever and sore throat for three days.
Doctor: Any cough or trouble breathing?
Patient: Mild cough, no trouble breathing.
Doctor: Any allergies?
Patient: No known drug allergies.
Doctor: Have you taken anything already?
Patient: Just paracetamol last night.""",
                visit_context=VisitContext(locale="en-IN", specialty="general_medicine", clinician_name="Dr. Rao"),
                include_differential_diagnosis=True,
            ),
            _upper_respiratory_note(),
        ),
        (
            "Nisha Patel",
            ClinicalNoteRequest(
                transcript="""Doctor: Good morning, what are you feeling today?
Patient: Burning while passing urine since yesterday and I am going more often.
Doctor: Any fever, back pain, or vomiting?
Patient: No fever and no vomiting, just lower abdominal discomfort.
Doctor: Are you allergic to any medicines?
Patient: I am not aware of any allergies.""",
                visit_context=VisitContext(locale="en-IN", specialty="family_medicine", clinician_name="Dr. Shah"),
                include_differential_diagnosis=True,
            ),
            _uti_note(),
        ),
        (
            "Arjun Menon",
            ClinicalNoteRequest(
                transcript="""Doctor: Tell me what happened.
Patient: I twisted my right ankle while playing badminton this morning.
Doctor: Were you able to walk after the injury?
Patient: I can walk slowly but it hurts and there is swelling.
Doctor: Any numbness or previous injury in that ankle?
Patient: No numbness and no previous injury.""",
                visit_context=VisitContext(locale="en-IN", specialty="orthopedics", clinician_name="Dr. Iyer"),
                include_differential_diagnosis=False,
            ),
            _ankle_note(),
        ),
    ]


def primary_demo_case() -> tuple[str, ClinicalNoteRequest, ClinicalNoteResponse]:
    return demo_cases()[0]


def _upper_respiratory_note() -> ClinicalNoteResponse:
    evidence = [
        EvidenceItem(quote="I have had a fever and sore throat for three days.", speaker="patient"),
        EvidenceItem(quote="Mild cough, no trouble breathing.", speaker="patient"),
    ]
    return ClinicalNoteResponse(
        summary="Patient reports fever, sore throat, and mild cough for three days without breathing difficulty. No known drug allergies were reported. Paracetamol was taken the previous night.",
        entities=ClinicalEntities(
            symptoms=[
                ExtractedFact(value="fever", confidence="high", evidence=[evidence[0]]),
                ExtractedFact(value="sore throat", confidence="high", evidence=[evidence[0]]),
                ExtractedFact(value="mild cough", confidence="medium", evidence=[evidence[1]]),
            ],
            duration=[ExtractedFact(value="three days", confidence="high", evidence=[evidence[0]])],
            medications=[
                ExtractedFact(
                    value="paracetamol",
                    confidence="medium",
                    evidence=[EvidenceItem(quote="Just paracetamol last night.", speaker="patient")],
                )
            ],
            allergies=[
                ExtractedFact(
                    value="no known drug allergies",
                    confidence="high",
                    evidence=[EvidenceItem(quote="No known drug allergies.", speaker="patient")],
                )
            ],
        ),
        soap_note=SoapNote(
            subjective=SoapSection(
                text="Patient reports fever, sore throat, and mild cough for three days. No breathing difficulty. Took paracetamol last night.",
                evidence=evidence,
            ),
            objective=SoapSection(
                text="No objective vitals documented in transcript. Clinician asked about respiratory distress; patient denied difficulty breathing.",
                evidence=[evidence[1]],
            ),
            assessment=SoapSection(
                text="Acute upper respiratory tract infection is possible based on fever, sore throat, and mild cough. More objective examination is needed.",
                evidence=evidence,
            ),
            plan=SoapSection(
                text="Confirm vitals, perform throat and respiratory exam, continue supportive care guidance, and follow up if symptoms worsen.",
            ),
        ),
        differential_diagnosis=[
            DifferentialDiagnosisItem(
                condition="Acute viral pharyngitis",
                rationale="Fever, sore throat, and mild cough over three days are compatible with a viral upper respiratory illness.",
                confidence="medium",
            ),
            DifferentialDiagnosisItem(
                condition="Tonsillopharyngitis",
                rationale="Sore throat and fever can be associated with tonsillar or pharyngeal inflammation, pending exam findings.",
                confidence="low",
            ),
        ],
        review_flags=[
            ReviewFlag(
                issue="Objective vitals missing from transcript",
                severity="warning",
                recommendation="Record temperature, pulse, respiratory rate, and oxygen saturation before final sign-off.",
            )
        ],
    )


def _uti_note() -> ClinicalNoteResponse:
    evidence = [
        EvidenceItem(quote="Burning while passing urine since yesterday and I am going more often.", speaker="patient"),
        EvidenceItem(quote="No fever and no vomiting, just lower abdominal discomfort.", speaker="patient"),
    ]
    return ClinicalNoteResponse(
        summary="Patient reports dysuria and urinary frequency since yesterday with lower abdominal discomfort. No fever or vomiting reported, and no known medication allergies were identified.",
        entities=ClinicalEntities(
            symptoms=[
                ExtractedFact(value="dysuria", confidence="high", evidence=[evidence[0]]),
                ExtractedFact(value="urinary frequency", confidence="high", evidence=[evidence[0]]),
                ExtractedFact(value="lower abdominal discomfort", confidence="medium", evidence=[evidence[1]]),
            ],
            duration=[ExtractedFact(value="since yesterday", confidence="high", evidence=[evidence[0]])],
            allergies=[
                ExtractedFact(
                    value="no known allergies",
                    confidence="medium",
                    evidence=[EvidenceItem(quote="I am not aware of any allergies.", speaker="patient")],
                )
            ],
        ),
        soap_note=SoapNote(
            subjective=SoapSection(
                text="Patient reports burning micturition and urinary frequency since yesterday, with mild lower abdominal discomfort. Denies fever and vomiting.",
                evidence=evidence,
            ),
            objective=SoapSection(
                text="No urinalysis or vital signs documented in transcript.",
                evidence=[],
            ),
            assessment=SoapSection(
                text="Lower urinary tract infection is a consideration based on dysuria and urinary frequency without systemic symptoms.",
                evidence=evidence,
            ),
            plan=SoapSection(
                text="Obtain urine analysis, assess hydration status, review red flags, and confirm treatment after clinician examination.",
            ),
        ),
        differential_diagnosis=[
            DifferentialDiagnosisItem(
                condition="Uncomplicated cystitis",
                rationale="Dysuria and frequency with no fever are compatible with lower urinary tract infection.",
                confidence="medium",
            ),
        ],
        review_flags=[
            ReviewFlag(
                issue="Urinalysis not yet documented",
                severity="warning",
                recommendation="Document test results or rationale if treatment is started empirically.",
            )
        ],
    )


def _ankle_note() -> ClinicalNoteResponse:
    evidence = [
        EvidenceItem(quote="I twisted my right ankle while playing badminton this morning.", speaker="patient"),
        EvidenceItem(quote="I can walk slowly but it hurts and there is swelling.", speaker="patient"),
    ]
    return ClinicalNoteResponse(
        summary="Patient twisted the right ankle while playing badminton this morning and reports pain with swelling. Weight-bearing is possible but limited, with no numbness and no prior injury reported.",
        entities=ClinicalEntities(
            symptoms=[
                ExtractedFact(value="right ankle pain", confidence="high", evidence=[evidence[1]]),
                ExtractedFact(value="ankle swelling", confidence="high", evidence=[evidence[1]]),
            ],
            duration=[ExtractedFact(value="this morning", confidence="high", evidence=[evidence[0]])],
            medical_history=[
                ExtractedFact(
                    value="no prior injury in right ankle",
                    confidence="medium",
                    evidence=[EvidenceItem(quote="No numbness and no previous injury.", speaker="patient")],
                )
            ],
        ),
        soap_note=SoapNote(
            subjective=SoapSection(
                text="Patient twisted the right ankle while playing badminton this morning. Pain and swelling are present; able to walk slowly. Denies numbness and prior ankle injury.",
                evidence=evidence,
            ),
            objective=SoapSection(
                text="No physical exam findings or imaging documented yet.",
                evidence=[],
            ),
            assessment=SoapSection(
                text="Right ankle sprain is likely, though bony injury should be excluded based on exam and Ottawa ankle rule assessment.",
                evidence=evidence,
            ),
            plan=SoapSection(
                text="Perform focused ankle exam, consider X-ray if indicated, advise rest/ice/compression/elevation, and reassess weight-bearing ability.",
            ),
        ),
        review_flags=[
            ReviewFlag(
                issue="Severity grading not documented",
                severity="info",
                recommendation="Record ligament tenderness, range of motion, and need for imaging before finalizing the note.",
            )
        ],
    )
