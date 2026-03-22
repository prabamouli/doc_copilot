import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

class ClinicApiClient {
  ClinicApiClient({http.Client? client})
      : _client = client ?? http.Client(),
        baseUrl = const String.fromEnvironment(
          'API_BASE_URL',
          defaultValue: 'http://127.0.0.1:8000',
        );

  final http.Client _client;
  final String baseUrl;

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

  void _ensureSuccess(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw Exception('Request failed (${response.statusCode}): ${response.body}');
    }
  }
}
