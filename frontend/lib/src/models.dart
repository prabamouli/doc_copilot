class CaseRecord {
  const CaseRecord({
    required this.caseId,
    required this.patientLabel,
    required this.transcript,
    required this.reviewStatus,
    required this.clinicianFeedback,
    required this.createdAt,
    required this.updatedAt,
    required this.note,
  });

  final String caseId;
  final String patientLabel;
  final String transcript;
  final String reviewStatus;
  final String clinicianFeedback;
  final DateTime createdAt;
  final DateTime updatedAt;
  final ClinicalNote note;

  factory CaseRecord.fromJson(Map<String, dynamic> json) {
    return CaseRecord(
      caseId: json['case_id'] as String,
      patientLabel: json['patient_label'] as String,
      transcript: json['transcript'] as String,
      reviewStatus: json['review_status'] as String,
      clinicianFeedback: json['clinician_feedback'] as String? ?? '',
      createdAt: DateTime.parse(json['created_at'] as String),
      updatedAt: DateTime.parse(json['updated_at'] as String),
      note: ClinicalNote.fromJson(json['note'] as Map<String, dynamic>),
    );
  }
}

class ClinicalNote {
  const ClinicalNote({
    required this.summary,
    required this.entities,
    required this.soapNote,
    required this.reviewFlags,
    required this.differentialDiagnosis,
    required this.disclaimer,
  });

  final String summary;
  final ClinicalEntities entities;
  final SoapNote soapNote;
  final List<ReviewFlag> reviewFlags;
  final List<DifferentialDiagnosisItem> differentialDiagnosis;
  final String disclaimer;

  factory ClinicalNote.fromJson(Map<String, dynamic> json) {
    return ClinicalNote(
      summary: json['summary'] as String,
      entities: ClinicalEntities.fromJson(json['entities'] as Map<String, dynamic>),
      soapNote: SoapNote.fromJson(json['soap_note'] as Map<String, dynamic>),
      reviewFlags: (json['review_flags'] as List<dynamic>)
          .map((item) => ReviewFlag.fromJson(item as Map<String, dynamic>))
          .toList(),
      differentialDiagnosis: (json['differential_diagnosis'] as List<dynamic>)
          .map((item) => DifferentialDiagnosisItem.fromJson(item as Map<String, dynamic>))
          .toList(),
      disclaimer: json['disclaimer'] as String,
    );
  }
}

class ClinicalEntities {
  const ClinicalEntities({
    required this.symptoms,
    required this.duration,
    required this.severity,
    required this.medicalHistory,
    required this.medications,
    required this.allergies,
    required this.vitals,
  });

  final List<ExtractedFact> symptoms;
  final List<ExtractedFact> duration;
  final List<ExtractedFact> severity;
  final List<ExtractedFact> medicalHistory;
  final List<ExtractedFact> medications;
  final List<ExtractedFact> allergies;
  final List<ExtractedFact> vitals;

  factory ClinicalEntities.fromJson(Map<String, dynamic> json) {
    List<ExtractedFact> parseList(String key) {
      return (json[key] as List<dynamic>)
          .map((item) => ExtractedFact.fromJson(item as Map<String, dynamic>))
          .toList();
    }

    return ClinicalEntities(
      symptoms: parseList('symptoms'),
      duration: parseList('duration'),
      severity: parseList('severity'),
      medicalHistory: parseList('medical_history'),
      medications: parseList('medications'),
      allergies: parseList('allergies'),
      vitals: parseList('vitals'),
    );
  }
}

class ExtractedFact {
  const ExtractedFact({
    required this.value,
    required this.status,
    required this.confidence,
  });

  final String value;
  final String status;
  final String confidence;

  factory ExtractedFact.fromJson(Map<String, dynamic> json) {
    return ExtractedFact(
      value: json['value'] as String,
      status: json['status'] as String,
      confidence: json['confidence'] as String,
    );
  }
}

class SoapNote {
  const SoapNote({
    required this.subjective,
    required this.objective,
    required this.assessment,
    required this.plan,
  });

  final SoapSection subjective;
  final SoapSection objective;
  final SoapSection assessment;
  final SoapSection plan;

  factory SoapNote.fromJson(Map<String, dynamic> json) {
    return SoapNote(
      subjective: SoapSection.fromJson(json['subjective'] as Map<String, dynamic>),
      objective: SoapSection.fromJson(json['objective'] as Map<String, dynamic>),
      assessment: SoapSection.fromJson(json['assessment'] as Map<String, dynamic>),
      plan: SoapSection.fromJson(json['plan'] as Map<String, dynamic>),
    );
  }
}

class SoapSection {
  const SoapSection({required this.text});

  final String text;

  factory SoapSection.fromJson(Map<String, dynamic> json) {
    return SoapSection(text: json['text'] as String);
  }
}

class ReviewFlag {
  const ReviewFlag({
    required this.issue,
    required this.severity,
    required this.recommendation,
  });

  final String issue;
  final String severity;
  final String recommendation;

  factory ReviewFlag.fromJson(Map<String, dynamic> json) {
    return ReviewFlag(
      issue: json['issue'] as String,
      severity: json['severity'] as String,
      recommendation: json['recommendation'] as String,
    );
  }
}

class DifferentialDiagnosisItem {
  const DifferentialDiagnosisItem({
    required this.condition,
    required this.rationale,
    required this.confidence,
  });

  final String condition;
  final String rationale;
  final String confidence;

  factory DifferentialDiagnosisItem.fromJson(Map<String, dynamic> json) {
    return DifferentialDiagnosisItem(
      condition: json['condition'] as String,
      rationale: json['rationale'] as String,
      confidence: json['confidence'] as String,
    );
  }
}

class AuditLogEntry {
  const AuditLogEntry({
    required this.id,
    required this.caseId,
    required this.eventType,
    required this.actor,
    required this.details,
    required this.createdAt,
  });

  final int id;
  final String caseId;
  final String eventType;
  final String actor;
  final String details;
  final DateTime createdAt;

  factory AuditLogEntry.fromJson(Map<String, dynamic> json) {
    return AuditLogEntry(
      id: json['id'] as int,
      caseId: json['case_id'] as String,
      eventType: json['event_type'] as String,
      actor: json['actor'] as String,
      details: json['details'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }
}

class AgentSummary {
  const AgentSummary({
    required this.id,
    required this.name,
    required this.description,
    required this.version,
  });

  final String id;
  final String name;
  final String description;
  final String version;

  factory AgentSummary.fromJson(Map<String, dynamic> json) {
    return AgentSummary(
      id: json['id'] as String,
      name: json['name'] as String,
      description: json['description'] as String,
      version: json['version'] as String,
    );
  }
}

class AgentRunResponse {
  const AgentRunResponse({
    required this.agentId,
    required this.agentName,
    required this.result,
  });

  final String agentId;
  final String agentName;
  final Map<String, dynamic> result;

  factory AgentRunResponse.fromJson(Map<String, dynamic> json) {
    return AgentRunResponse(
      agentId: json['agent_id'] as String,
      agentName: json['agent_name'] as String,
      result: Map<String, dynamic>.from(json['result'] as Map),
    );
  }
}

class SafetyIssue {
  const SafetyIssue({
    required this.issue,
    required this.severity,
    required this.recommendation,
  });

  final String issue;
  final String severity;
  final String recommendation;

  factory SafetyIssue.fromJson(Map<String, dynamic> json) {
    return SafetyIssue(
      issue: json['issue'] as String,
      severity: json['severity'] as String,
      recommendation: json['recommendation'] as String,
    );
  }
}

class QueueRankedCase {
  const QueueRankedCase({
    required this.caseId,
    required this.patientLabel,
    required this.reviewStatus,
    required this.topIssue,
    required this.recommendedAction,
  });

  final String caseId;
  final String patientLabel;
  final String reviewStatus;
  final String topIssue;
  final String recommendedAction;

  factory QueueRankedCase.fromJson(Map<String, dynamic> json) {
    return QueueRankedCase(
      caseId: json['case_id'] as String,
      patientLabel: json['patient_label'] as String,
      reviewStatus: json['review_status'] as String,
      topIssue: json['top_issue'] as String,
      recommendedAction: json['recommended_action'] as String,
    );
  }
}
