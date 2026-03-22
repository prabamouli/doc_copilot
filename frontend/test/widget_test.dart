import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:frontend/src/api.dart';
import 'package:frontend/src/app.dart';
import 'package:frontend/src/dashboard.dart';
import 'package:frontend/src/models.dart';

class _FakeClinicApiClient extends ClinicApiClient {
  _FakeClinicApiClient();

  final CaseRecord _caseRecord = CaseRecord.fromJson({
    'case_id': 'case-123',
    'patient_label': 'Seeded Demo Patient',
    'transcript': 'Doctor: What brings you in today? Patient: Fever for 2 days.',
    'review_status': 'pending_review',
    'clinician_feedback': '',
    'created_at': '2026-03-22T10:00:00Z',
    'updated_at': '2026-03-22T11:00:00Z',
    'note': {
      'summary': 'Patient reports fever for 2 days.',
      'entities': {
        'symptoms': [
          {'value': 'fever', 'status': 'supported', 'confidence': 'high'}
        ],
        'duration': [
          {'value': '2 days', 'status': 'supported', 'confidence': 'high'}
        ],
        'severity': [],
        'medical_history': [],
        'medications': [],
        'allergies': [],
        'vitals': [],
      },
      'soap_note': {
        'subjective': {'text': 'Fever for 2 days.'},
        'objective': {'text': 'No vitals recorded.'},
        'assessment': {'text': 'Viral syndrome considered.'},
        'plan': {'text': 'Supportive care and follow-up.'},
      },
      'review_flags': [
        {
          'issue': 'Objective vitals missing from transcript',
          'severity': 'warning',
          'recommendation': 'Record temperature and pulse before sign-off.',
        }
      ],
      'differential_diagnosis': [
        {
          'condition': 'Viral upper respiratory infection',
          'rationale': 'Fever without severe red flags in transcript.',
          'confidence': 'low',
        }
      ],
      'disclaimer': 'Doctor validation required.',
    },
  });

  @override
  Future<List<CaseRecord>> fetchCases() async => [_caseRecord];

  @override
  Future<CaseRecord> fetchCase(String caseId) async => _caseRecord;

  @override
  Future<List<AuditLogEntry>> fetchAuditLogs(String caseId) async => [
        AuditLogEntry.fromJson({
          'id': 1,
          'case_id': caseId,
          'event_type': 'note_generated',
          'actor': 'system',
          'details': 'Initial note created.',
          'created_at': '2026-03-22T11:05:00Z',
        }),
      ];

  @override
  Future<List<AgentSummary>> fetchAgents() async => [
        AgentSummary.fromJson({
          'id': 'clinical_intake_agent',
          'name': 'Clinical Intake Agent',
          'description': 'Creates a note from transcript.',
          'version': '1.0.0',
        }),
        AgentSummary.fromJson({
          'id': 'note_safety_reviewer',
          'name': 'Note Safety Reviewer',
          'description': 'Checks chart risks.',
          'version': '1.0.0',
        }),
      ];
}

void main() {
  testWidgets('dashboard renders loading state', (WidgetTester tester) async {
    await tester.pumpWidget(const ClinicCopilotApp());

    expect(find.text('Loading clinician dashboard...'), findsOneWidget);
  });

  testWidgets('workspace buttons switch the visible dashboard section', (WidgetTester tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: DashboardScreen(apiClient: _FakeClinicApiClient()),
      ),
    );

    await tester.pumpAndSettle();

    expect(find.text('Retry'), findsNothing);
    expect(find.text('Clinic Copilot'), findsWidgets);
    final noteStudioButton = find.byKey(const ValueKey('workspace-noteStudio'));
    final agentsButton = find.byKey(const ValueKey('workspace-agents'));
    final auditButton = find.byKey(const ValueKey('workspace-audit'));

    expect(noteStudioButton, findsOneWidget);
    expect(agentsButton, findsOneWidget);
    expect(auditButton, findsOneWidget);

    await tester.ensureVisible(noteStudioButton);
    await tester.tapAt(tester.getCenter(noteStudioButton));
    await tester.pumpAndSettle();
    expect(find.text('Note studio workspace'), findsOneWidget);

    await tester.ensureVisible(agentsButton);
    await tester.tapAt(tester.getCenter(agentsButton));
    await tester.pumpAndSettle();
    expect(find.text('Agent workspace'), findsOneWidget);

    await tester.ensureVisible(auditButton);
    await tester.tapAt(tester.getCenter(auditButton));
    await tester.pumpAndSettle();
    expect(find.text('Audit workspace'), findsOneWidget);
  });
}
