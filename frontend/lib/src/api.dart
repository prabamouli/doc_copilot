import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:web_socket_channel/web_socket_channel.dart';

import 'models.dart';

class ClinicalNudgeSocket {
  ClinicalNudgeSocket(this._channel);

  final WebSocketChannel _channel;

  Stream<ClinicalNudgeEvent> get events => _channel.stream
      .map((raw) => raw is String ? raw : raw.toString())
      .map((raw) => jsonDecode(raw) as Map<String, dynamic>)
      .where((payload) => payload['type'] == 'clinical_nudge')
      .map(ClinicalNudgeEvent.fromJson);

  void observe({
    required String caseId,
    required String transcript,
    required int elapsedSeconds,
  }) {
    _channel.sink.add(
      jsonEncode(
        {
          'case_id': caseId,
          'transcript': transcript,
          'elapsed_seconds': elapsedSeconds,
        },
      ),
    );
  }

  Future<void> close() async {
    await _channel.sink.close();
  }
}

class ClinicApiClient {
  ClinicApiClient({http.Client? client})
      : _client = client ?? http.Client(),
        baseUrl = const String.fromEnvironment(
          'API_BASE_URL',
          defaultValue: 'http://127.0.0.1:8000',
        );

  final http.Client _client;
  final String baseUrl;

  ClinicalNudgeSocket connectClinicalNudges() {
    final httpUri = Uri.parse(baseUrl);
    final wsUri = httpUri.replace(
      scheme: httpUri.scheme == 'https' ? 'wss' : 'ws',
      path: '/ws/clinical-nudges',
      query: null,
    );
    return ClinicalNudgeSocket(WebSocketChannel.connect(wsUri));
  }

  Future<CaseRecord> fetchDemoCase() async {
    final response = await _client.get(Uri.parse('$baseUrl/v1/demo-case'));
    _ensureSuccess(response);
    return CaseRecord.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<List<CaseRecord>> fetchCases() async {
    final response = await _client.get(Uri.parse('$baseUrl/v1/cases'));
    _ensureSuccess(response);
    return (jsonDecode(response.body) as List<dynamic>)
        .map((item) => CaseRecord.fromJson(item as Map<String, dynamic>))
        .toList();
  }

  Future<CaseRecord> fetchCase(String caseId) async {
    final response = await _client.get(Uri.parse('$baseUrl/v1/cases/$caseId'));
    _ensureSuccess(response);
    return CaseRecord.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<List<AuditLogEntry>> fetchAuditLogs(String caseId) async {
    final response = await _client.get(Uri.parse('$baseUrl/v1/audit-logs?case_id=$caseId'));
    _ensureSuccess(response);
    return (jsonDecode(response.body) as List<dynamic>)
        .map((item) => AuditLogEntry.fromJson(item as Map<String, dynamic>))
        .toList();
  }

  Future<List<AgentSummary>> fetchAgents() async {
    final response = await _client.get(Uri.parse('$baseUrl/v1/agents'));
    _ensureSuccess(response);
    return (jsonDecode(response.body) as List<dynamic>)
        .map((item) => AgentSummary.fromJson(item as Map<String, dynamic>))
        .toList();
  }

  Future<AgentRunResponse> runClinicalIntakeAgent({
    required String transcript,
    required String clinicianName,
  }) async {
    final response = await _client.post(
      Uri.parse('$baseUrl/v1/agents/clinical_intake_agent/run'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(
        {
          'transcript': transcript,
          'visit_context': {
            'locale': 'en-IN',
            'specialty': 'general_medicine',
            'clinician_name': clinicianName,
          },
        },
      ),
    );
    _ensureSuccess(response);
    return AgentRunResponse.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<AgentRunResponse> runSafetyReviewer(String caseId) async {
    final response = await _client.post(
      Uri.parse('$baseUrl/v1/agents/note_safety_reviewer/run'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'case_id': caseId}),
    );
    _ensureSuccess(response);
    return AgentRunResponse.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<AgentRunResponse> runQueueOrchestrator() async {
    final response = await _client.post(
      Uri.parse('$baseUrl/v1/agents/review_queue_orchestrator/run'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({}),
    );
    _ensureSuccess(response);
    return AgentRunResponse.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<AgentRunResponse> runBillingOptimizer(String caseId) async {
    final response = await _client.post(
      Uri.parse('$baseUrl/v1/agents/billing_optimizer_agent/run'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'case_id': caseId}),
    );
    _ensureSuccess(response);
    return AgentRunResponse.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<void> captureConversationSnapshot({
    required String caseId,
    required String transcript,
  }) async {
    final response = await _client.post(
      Uri.parse('$baseUrl/v1/cases/$caseId/conversation-capture'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'transcript': transcript}),
    );
    _ensureSuccess(response);
  }

  Future<CaseRecord> submitReview({
    required String caseId,
    required String status,
    required String reviewedBy,
    required String feedback,
  }) async {
    final response = await _client.post(
      Uri.parse('$baseUrl/v1/cases/$caseId/review'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(
        {
          'status': status,
          'reviewed_by': reviewedBy,
          'clinician_feedback': feedback,
        },
      ),
    );
    _ensureSuccess(response);
    return CaseRecord.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<CaseRecord> amendNote({
    required String caseId,
    required String editedBy,
    required String reason,
    required String summary,
    required String subjective,
    required String objective,
    required String assessment,
    required String plan,
    required List<String> symptoms,
    required List<String> duration,
    required List<String> severity,
    required List<String> medicalHistory,
    required List<String> medications,
    required List<String> allergies,
    required List<String> vitals,
  }) async {
    final response = await _client.post(
      Uri.parse('$baseUrl/v1/cases/$caseId/amend'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(
        {
          'edited_by': editedBy,
          'reason': reason,
          'note': {
            'summary': summary,
            'subjective': subjective,
            'objective': objective,
            'assessment': assessment,
            'plan': plan,
            'symptoms': symptoms,
            'duration': duration,
            'severity': severity,
            'medical_history': medicalHistory,
            'medications': medications,
            'allergies': allergies,
            'vitals': vitals,
          },
        },
      ),
    );
    _ensureSuccess(response);
    return CaseRecord.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<PatientHistoryDebugResponse> fetchPatientHistoryDebug({
    required String patientId,
    required String currentComplaint,
    int topK = 5,
  }) async {
    final response = await _client.post(
      Uri.parse('$baseUrl/v1/patient-history/retrieve'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(
        {
          'patient_id': patientId,
          'current_complaint': currentComplaint,
          'top_k': topK,
        },
      ),
    );
    _ensureSuccess(response);
    return PatientHistoryDebugResponse.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  Future<VisionObjectiveResponse> analyzeVisionMedia({
    required String mediaPath,
    required String mediaType,
  }) async {
    final request = http.MultipartRequest(
      'POST',
      Uri.parse('$baseUrl/v1/vision-agent/analyze'),
    )
      ..fields['media_type'] = mediaType
      ..files.add(await http.MultipartFile.fromPath('media_file', mediaPath));

    final streamed = await _client.send(request);
    final response = await http.Response.fromStream(streamed);
    _ensureSuccess(response);
    return VisionObjectiveResponse.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
  }

  void _ensureSuccess(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw Exception('Request failed (${response.statusCode}): ${response.body}');
    }
  }
}
