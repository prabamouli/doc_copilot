Map<String, dynamic> _asMap(Object? value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) return Map<String, dynamic>.from(value);
  return const {};
}

List<Map<String, dynamic>> _asMapList(Object? value) {
  if (value is! List) return const [];
  return value
      .whereType<Map>()
      .map((item) => Map<String, dynamic>.from(item))
      .toList();
}

DateTime _parseDateTime(Object? value) {
  if (value is String) {
    final parsed = DateTime.tryParse(value);
    if (parsed != null) return parsed;
  }
  return DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
}

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
      caseId: json['case_id']?.toString() ?? '',
      patientLabel: json['patient_label']?.toString() ?? 'Unknown patient',
      transcript: json['transcript']?.toString() ?? '',
      reviewStatus: json['review_status']?.toString() ?? 'pending_review',
      clinicianFeedback: json['clinician_feedback']?.toString() ?? '',
      createdAt: _parseDateTime(json['created_at']),
      updatedAt: _parseDateTime(json['updated_at']),
      note: ClinicalNote.fromJson(_asMap(json['note'])),
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
      summary: json['summary']?.toString() ?? '',
      entities: ClinicalEntities.fromJson(_asMap(json['entities'])),
      soapNote: SoapNote.fromJson(_asMap(json['soap_note'])),
      reviewFlags: _asMapList(
        json['review_flags'],
      ).map(ReviewFlag.fromJson).toList(),
      differentialDiagnosis: _asMapList(
        json['differential_diagnosis'],
      ).map(DifferentialDiagnosisItem.fromJson).toList(),
      disclaimer:
          json['disclaimer']?.toString() ?? 'Clinician review required.',
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
      return _asMapList(json[key]).map(ExtractedFact.fromJson).toList();
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
      value: json['value']?.toString() ?? '',
      status: json['status']?.toString() ?? 'unknown',
      confidence: json['confidence']?.toString() ?? 'medium',
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
      subjective: SoapSection.fromJson(_asMap(json['subjective'])),
      objective: SoapSection.fromJson(_asMap(json['objective'])),
      assessment: SoapSection.fromJson(_asMap(json['assessment'])),
      plan: SoapSection.fromJson(_asMap(json['plan'])),
    );
  }
}

class SoapSection {
  const SoapSection({required this.text});

  final String text;

  factory SoapSection.fromJson(Map<String, dynamic> json) {
    return SoapSection(text: json['text']?.toString() ?? '');
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
      issue: json['issue']?.toString() ?? 'Unknown issue',
      severity: json['severity']?.toString() ?? 'info',
      recommendation: json['recommendation']?.toString() ?? '',
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
      condition: json['condition']?.toString() ?? 'Unknown condition',
      rationale: json['rationale']?.toString() ?? '',
      confidence: json['confidence']?.toString() ?? 'low',
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
      id: (json['id'] as num?)?.toInt() ?? 0,
      caseId: json['case_id']?.toString() ?? '',
      eventType: json['event_type']?.toString() ?? 'unknown',
      actor: json['actor']?.toString() ?? 'system',
      details: json['details']?.toString() ?? '',
      createdAt: _parseDateTime(json['created_at']),
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
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? 'Unknown agent',
      description: json['description']?.toString() ?? '',
      version: json['version']?.toString() ?? 'unknown',
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
      agentId: json['agent_id']?.toString() ?? '',
      agentName: json['agent_name']?.toString() ?? 'Unknown agent',
      result: _asMap(json['result']),
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
      issue: json['issue']?.toString() ?? 'Unknown issue',
      severity: json['severity']?.toString() ?? 'info',
      recommendation: json['recommendation']?.toString() ?? '',
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
      caseId: json['case_id']?.toString() ?? '',
      patientLabel: json['patient_label']?.toString() ?? 'Unknown patient',
      reviewStatus: json['review_status']?.toString() ?? 'pending_review',
      topIssue: json['top_issue']?.toString() ?? '',
      recommendedAction: json['recommended_action']?.toString() ?? '',
    );
  }
}

class PatientHistoryDebugResponse {
  const PatientHistoryDebugResponse({
    required this.patientId,
    required this.currentComplaint,
    required this.historicalContext,
    required this.retrieved,
  });

  final String patientId;
  final String currentComplaint;
  final String historicalContext;
  final List<RetrievedHistoryItem> retrieved;

  factory PatientHistoryDebugResponse.fromJson(Map<String, dynamic> json) {
    return PatientHistoryDebugResponse(
      patientId: json['patient_id'] as String? ?? '',
      currentComplaint: json['current_complaint'] as String? ?? '',
      historicalContext: json['historical_context'] as String? ?? '',
      retrieved: (json['retrieved'] as List<dynamic>? ?? const [])
          .map(
            (item) =>
                RetrievedHistoryItem.fromJson(item as Map<String, dynamic>),
          )
          .toList(),
    );
  }
}

class RetrievedHistoryItem {
  const RetrievedHistoryItem({
    required this.visitId,
    required this.date,
    required this.score,
    required this.source,
    required this.textChunk,
  });

  final String visitId;
  final String date;
  final double score;
  final String source;
  final String textChunk;

  factory RetrievedHistoryItem.fromJson(Map<String, dynamic> json) {
    return RetrievedHistoryItem(
      visitId: json['visit_id'] as String? ?? 'unknown-visit',
      date: json['date'] as String? ?? 'unknown-date',
      score: (json['score'] as num?)?.toDouble() ?? 0,
      source: json['source'] as String? ?? 'unknown-source',
      textChunk: json['text_chunk'] as String? ?? '',
    );
  }
}

class ClinicalNudgeEvent {
  const ClinicalNudgeEvent({
    required this.type,
    required this.caseId,
    required this.payload,
  });

  final String type;
  final String caseId;
  final Map<String, dynamic> payload;

  String? get id => payload['id'] as String?;
  String get title => payload['title'] as String? ?? 'Clinical Nudge';
  String get message => payload['message'] as String? ?? '';
  String get evidence => payload['evidence'] as String? ?? '';
  List<String> get symptoms => _readStringList('symptoms');
  List<String> get riskSignals => _readStringList('risk_signals');
  List<String> get missingQuestions => _readStringList('missing_questions');
  List<String> get nextQuestionSuggestions =>
      _readStringList('next_question_suggestions');
  List<String> get clinicalAssistantTasks =>
      _readStringList('clinical_assistant_tasks');

  List<String> _readStringList(String key) {
    final raw = payload[key];
    if (raw is! List) return const [];
    return raw
        .whereType<Object>()
        .map((item) => item.toString().trim())
        .where((item) => item.isNotEmpty)
        .toList();
  }

  factory ClinicalNudgeEvent.fromJson(Map<String, dynamic> json) {
    return ClinicalNudgeEvent(
      type: json['type'] as String? ?? 'unknown',
      caseId: json['case_id'] as String? ?? '',
      payload: Map<String, dynamic>.from(json['payload'] as Map? ?? const {}),
    );
  }
}

class VisionObjectiveResponse {
  const VisionObjectiveResponse({
    required this.mediaType,
    required this.objectiveText,
    required this.model,
    required this.confidence,
  });

  final String mediaType;
  final String objectiveText;
  final String model;
  final String confidence;

  factory VisionObjectiveResponse.fromJson(Map<String, dynamic> json) {
    return VisionObjectiveResponse(
      mediaType: json['media_type'] as String? ?? 'image',
      objectiveText: json['objective_text'] as String? ?? '',
      model: json['model'] as String? ?? 'unknown',
      confidence: json['confidence'] as String? ?? 'medium',
    );
  }
}

class PatientAfterVisitSummary {
  const PatientAfterVisitSummary({
    required this.caseId,
    required this.audience,
    required this.readingLevel,
    required this.whatWeFound,
    required this.whatYouNeedToDoNext,
    required this.whenToGetHelp,
    required this.disclaimer,
  });

  final String caseId;
  final String audience;
  final String readingLevel;
  final List<String> whatWeFound;
  final List<String> whatYouNeedToDoNext;
  final List<String> whenToGetHelp;
  final String disclaimer;

  factory PatientAfterVisitSummary.fromJson(Map<String, dynamic> json) {
    List<String> parseList(String key) {
      final dynamic raw = json[key];
      if (raw is! List) return const [];
      return raw
          .whereType<Object>()
          .map((item) => item.toString())
          .where((item) => item.trim().isNotEmpty)
          .toList();
    }

    return PatientAfterVisitSummary(
      caseId: json['case_id'] as String? ?? '',
      audience: json['audience'] as String? ?? 'patient',
      readingLevel: json['reading_level'] as String? ?? '5th_grade',
      whatWeFound: parseList('what_we_found'),
      whatYouNeedToDoNext: parseList('what_you_need_to_do_next'),
      whenToGetHelp: parseList('when_to_get_help'),
      disclaimer: json['disclaimer'] as String? ?? '',
    );
  }
}

class OrchestratorDuringVisitResult {
  const OrchestratorDuringVisitResult({
    required this.caseId,
    required this.bufferLength,
    required this.elapsedSeconds,
    required this.nudge,
  });

  final String caseId;
  final int bufferLength;
  final int elapsedSeconds;
  final ClinicalNudgeEvent? nudge;

  factory OrchestratorDuringVisitResult.fromJson(Map<String, dynamic> json) {
    final nudgePayload = json['nudge'];
    return OrchestratorDuringVisitResult(
      caseId: json['case_id'] as String? ?? '',
      bufferLength: (json['buffer_length'] as num?)?.toInt() ?? 0,
      elapsedSeconds: (json['elapsed_seconds'] as num?)?.toInt() ?? 0,
      nudge: nudgePayload is Map<String, dynamic>
          ? ClinicalNudgeEvent.fromJson({
              'type': 'clinical_nudge',
              'case_id': json['case_id'] as String? ?? '',
              'payload': nudgePayload,
            })
          : null,
    );
  }
}

class OrchestratorPostVisitResult {
  const OrchestratorPostVisitResult({
    required this.caseId,
    required this.signAllowed,
    required this.preSignValidation,
    required this.outputs,
  });

  final String caseId;
  final bool signAllowed;
  final Map<String, dynamic> preSignValidation;
  final Map<String, dynamic> outputs;

  factory OrchestratorPostVisitResult.fromJson(Map<String, dynamic> json) {
    return OrchestratorPostVisitResult(
      caseId: json['case_id'] as String? ?? '',
      signAllowed: json['sign_allowed'] as bool? ?? false,
      preSignValidation: Map<String, dynamic>.from(
        json['pre_sign_validation'] as Map? ?? const {},
      ),
      outputs: Map<String, dynamic>.from(json['outputs'] as Map? ?? const {}),
    );
  }
}

class OfflineReadinessCheck {
  const OfflineReadinessCheck({
    required this.name,
    required this.ok,
    required this.detail,
  });

  final String name;
  final bool ok;
  final String detail;

  factory OfflineReadinessCheck.fromJson(Map<String, dynamic> json) {
    return OfflineReadinessCheck(
      name: json['name'] as String? ?? 'unknown',
      ok: json['ok'] as bool? ?? false,
      detail: json['detail'] as String? ?? '',
    );
  }
}

class OfflineReadinessStatus {
  const OfflineReadinessStatus({
    required this.workspace,
    required this.requestedModels,
    required this.databaseMode,
    required this.checks,
    required this.ready,
  });

  final String workspace;
  final List<String> requestedModels;
  final String databaseMode;
  final List<OfflineReadinessCheck> checks;
  final bool ready;

  factory OfflineReadinessStatus.fromJson(Map<String, dynamic> json) {
    return OfflineReadinessStatus(
      workspace: json['workspace'] as String? ?? '',
      requestedModels: (json['requested_models'] as List<dynamic>? ?? const [])
          .map((item) => item.toString())
          .toList(),
      databaseMode: json['database_mode'] as String? ?? 'unknown',
      checks: (json['checks'] as List<dynamic>? ?? const [])
          .map(
            (item) =>
                OfflineReadinessCheck.fromJson(item as Map<String, dynamic>),
          )
          .toList(),
      ready: json['ready'] as bool? ?? false,
    );
  }
}

class VoiceCommandResponse {
  const VoiceCommandResponse({
    required this.intent,
    required this.responseText,
    required this.actionCode,
    required this.data,
  });

  final String intent;
  final String responseText;
  final String actionCode;
  final Map<String, dynamic> data;

  factory VoiceCommandResponse.fromJson(Map<String, dynamic> json) {
    return VoiceCommandResponse(
      intent: json['intent'] as String? ?? 'unknown',
      responseText: json['response_text'] as String? ?? '',
      actionCode: json['action_code'] as String? ?? 'none',
      data: _asMap(json['data']),
    );
  }
}

class PatientTimelineSummary {
  const PatientTimelineSummary({
    required this.chronicConditions,
    required this.recurringSymptoms,
    required this.medicationHistory,
    required this.trendSummary,
  });

  final List<String> chronicConditions;
  final List<String> recurringSymptoms;
  final List<String> medicationHistory;
  final String trendSummary;

  factory PatientTimelineSummary.fromJson(Map<String, dynamic> json) {
    List<String> parseList(String key) {
      final raw = json[key];
      if (raw is! List) return const [];
      return raw
          .whereType<Object>()
          .map((item) => item.toString().trim())
          .where((item) => item.isNotEmpty)
          .toList();
    }

    return PatientTimelineSummary(
      chronicConditions: parseList('chronic_conditions'),
      recurringSymptoms: parseList('recurring_symptoms'),
      medicationHistory: parseList('medication_history'),
      trendSummary: json['trend_summary']?.toString() ?? '',
    );
  }
}

class RagMedicalValidationResult {
  const RagMedicalValidationResult({
    required this.supported,
    required this.evidence,
    required this.confidence,
  });

  final bool supported;
  final String evidence;
  final String confidence;

  factory RagMedicalValidationResult.fromJson(Map<String, dynamic> json) {
    return RagMedicalValidationResult(
      supported: json['supported'] as bool? ?? false,
      evidence: json['evidence']?.toString() ?? '',
      confidence: json['confidence']?.toString() ?? 'low',
    );
  }
}

class FullOutputValidationResult {
  const FullOutputValidationResult({
    required this.valid,
    required this.issues,
    required this.severity,
  });

  final bool valid;
  final List<String> issues;
  final String severity;

  factory FullOutputValidationResult.fromJson(Map<String, dynamic> json) {
    final rawIssues = json['issues'];
    return FullOutputValidationResult(
      valid: json['valid'] as bool? ?? false,
      issues: rawIssues is List
          ? rawIssues
                .whereType<Object>()
                .map((item) => item.toString().trim())
                .where((item) => item.isNotEmpty)
                .toList()
          : const [],
      severity: json['severity']?.toString() ?? 'low',
    );
  }
}

class CriticReviewResult {
  const CriticReviewResult({
    required this.errors,
    required this.improvements,
    required this.finalVerdict,
  });

  final List<String> errors;
  final List<String> improvements;
  final String finalVerdict;

  factory CriticReviewResult.fromJson(Map<String, dynamic> json) {
    List<String> parseList(String key) {
      final raw = json[key];
      if (raw is! List) return const [];
      return raw
          .whereType<Object>()
          .map((item) => item.toString().trim())
          .where((item) => item.isNotEmpty)
          .toList();
    }

    return CriticReviewResult(
      errors: parseList('errors'),
      improvements: parseList('improvements'),
      finalVerdict: json['final_verdict']?.toString() ?? 'needs_revision',
    );
  }
}

class DiagnosisConfidenceScoreResult {
  const DiagnosisConfidenceScoreResult({
    required this.score,
    required this.reason,
  });

  final int score;
  final String reason;

  factory DiagnosisConfidenceScoreResult.fromJson(Map<String, dynamic> json) {
    final rawScore = json['score'];
    final score = rawScore is int
        ? rawScore
        : int.tryParse(rawScore?.toString() ?? '') ?? 0;
    return DiagnosisConfidenceScoreResult(
      score: score.clamp(0, 100),
      reason: json['reason']?.toString() ?? '',
    );
  }
}

class PatientFriendlySummaryResult {
  const PatientFriendlySummaryResult({required this.summary});

  final String summary;

  factory PatientFriendlySummaryResult.fromJson(Map<String, dynamic> json) {
    return PatientFriendlySummaryResult(
      summary: json['summary']?.toString() ?? '',
    );
  }
}

class PrescriptionDraftResult {
  const PrescriptionDraftResult({
    required this.medications,
    required this.dosage,
    required this.instructions,
    required this.notes,
  });

  final List<String> medications;
  final List<String> dosage;
  final List<String> instructions;
  final String notes;

  factory PrescriptionDraftResult.fromJson(Map<String, dynamic> json) {
    List<String> parseList(String key) {
      final raw = json[key];
      if (raw is! List) return const [];
      return raw
          .whereType<Object>()
          .map((item) => item.toString().trim())
          .where((item) => item.isNotEmpty)
          .toList();
    }

    return PrescriptionDraftResult(
      medications: parseList('medications'),
      dosage: parseList('dosage'),
      instructions: parseList('instructions'),
      notes: json['notes']?.toString() ?? 'Doctor must verify',
    );
  }
}
