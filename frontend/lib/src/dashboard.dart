import 'package:flutter/material.dart';

import 'api.dart';
import 'models.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  final ClinicApiClient _api = ClinicApiClient();
  final TextEditingController _reviewerController = TextEditingController(text: 'Dr. Maya');
  final TextEditingController _feedbackController = TextEditingController();
  final TextEditingController _amendReasonController = TextEditingController();
  final TextEditingController _summaryController = TextEditingController();
  final TextEditingController _subjectiveController = TextEditingController();
  final TextEditingController _objectiveController = TextEditingController();
  final TextEditingController _assessmentController = TextEditingController();
  final TextEditingController _planController = TextEditingController();
  final TextEditingController _symptomsController = TextEditingController();
  final TextEditingController _durationController = TextEditingController();
  final TextEditingController _severityController = TextEditingController();
  final TextEditingController _historyController = TextEditingController();
  final TextEditingController _medicationsController = TextEditingController();
  final TextEditingController _allergiesController = TextEditingController();
  final TextEditingController _vitalsController = TextEditingController();
  final TextEditingController _agentTranscriptController = TextEditingController(
    text: 'Doctor: What brings you in today? Patient: I have fever and body pain for 2 days. Doctor: Any allergies? Patient: No known allergies.',
  );

  bool _submitting = false;
  bool _savingDraft = false;
  bool _switchingCase = false;
  bool _runningAgent = false;
  String? _error;
  CaseRecord? _caseRecord;
  List<CaseRecord> _cases = const [];
  List<AuditLogEntry> _auditLogs = const [];
  List<AgentSummary> _agents = const [];
  AgentRunResponse? _latestAgentResponse;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _reviewerController.dispose();
    _feedbackController.dispose();
    _amendReasonController.dispose();
    _summaryController.dispose();
    _subjectiveController.dispose();
    _objectiveController.dispose();
    _assessmentController.dispose();
    _planController.dispose();
    _symptomsController.dispose();
    _durationController.dispose();
    _severityController.dispose();
    _historyController.dispose();
    _medicationsController.dispose();
    _allergiesController.dispose();
    _vitalsController.dispose();
    _agentTranscriptController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _error = null;
    });
    try {
      final cases = await _api.fetchCases();
      final agents = await _api.fetchAgents();
      final selectedCase = _pickSelectedCase(cases);
      final auditLogs = await _api.fetchAuditLogs(selectedCase.caseId);
      if (!mounted) return;
      setState(() {
        _cases = cases;
        _agents = agents;
        _caseRecord = selectedCase;
        _auditLogs = auditLogs;
      });
      _syncDraftControllers(selectedCase);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '$error';
      });
    }
  }

  CaseRecord _pickSelectedCase(List<CaseRecord> cases) {
    final currentId = _caseRecord?.caseId;
    if (currentId != null) {
      for (final caseRecord in cases) {
        if (caseRecord.caseId == currentId) {
          return caseRecord;
        }
      }
    }
    return cases.first;
  }

  void _syncDraftControllers(CaseRecord caseRecord) {
    _summaryController.text = caseRecord.note.summary;
    _subjectiveController.text = caseRecord.note.soapNote.subjective.text;
    _objectiveController.text = caseRecord.note.soapNote.objective.text;
    _assessmentController.text = caseRecord.note.soapNote.assessment.text;
    _planController.text = caseRecord.note.soapNote.plan.text;
    _symptomsController.text = _joinFacts(caseRecord.note.entities.symptoms);
    _durationController.text = _joinFacts(caseRecord.note.entities.duration);
    _severityController.text = _joinFacts(caseRecord.note.entities.severity);
    _historyController.text = _joinFacts(caseRecord.note.entities.medicalHistory);
    _medicationsController.text = _joinFacts(caseRecord.note.entities.medications);
    _allergiesController.text = _joinFacts(caseRecord.note.entities.allergies);
    _vitalsController.text = _joinFacts(caseRecord.note.entities.vitals);
    _feedbackController.text = caseRecord.clinicianFeedback;
    _amendReasonController.clear();
  }

  String _joinFacts(List<ExtractedFact> facts) => facts.map((fact) => fact.value).join('\n');

  List<String> _splitLines(String value) {
    return value
        .split(RegExp(r'[\n,]+'))
        .map((item) => item.trim())
        .where((item) => item.isNotEmpty)
        .toList();
  }

  Future<void> _selectCase(String caseId) async {
    if (_caseRecord?.caseId == caseId) return;

    setState(() {
      _switchingCase = true;
      _error = null;
    });
    try {
      final selectedCase = await _api.fetchCase(caseId);
      final auditLogs = await _api.fetchAuditLogs(caseId);
      if (!mounted) return;
      setState(() {
        _caseRecord = selectedCase;
        _auditLogs = auditLogs;
        _switchingCase = false;
      });
      _syncDraftControllers(selectedCase);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _switchingCase = false;
        _error = '$error';
      });
    }
  }

  Future<void> _submitReview(String status) async {
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;

    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      final updatedCase = await _api.submitReview(
        caseId: caseRecord.caseId,
        status: status,
        reviewedBy: _reviewerController.text.trim(),
        feedback: _feedbackController.text.trim(),
      );
      final logs = await _api.fetchAuditLogs(caseRecord.caseId);
      final refreshedCases = await _api.fetchCases();
      final refreshedSelected = refreshedCases.firstWhere((item) => item.caseId == updatedCase.caseId);
      if (!mounted) return;
      setState(() {
        _cases = refreshedCases;
        _caseRecord = refreshedSelected;
        _auditLogs = logs;
        _submitting = false;
      });
      _syncDraftControllers(refreshedSelected);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _error = '$error';
      });
    }
  }

  Future<void> _saveDraftAmendments() async {
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;

    setState(() {
      _savingDraft = true;
      _error = null;
    });
    try {
      final updatedCase = await _api.amendNote(
        caseId: caseRecord.caseId,
        editedBy: _reviewerController.text.trim(),
        reason: _amendReasonController.text.trim(),
        summary: _summaryController.text.trim(),
        subjective: _subjectiveController.text.trim(),
        objective: _objectiveController.text.trim(),
        assessment: _assessmentController.text.trim(),
        plan: _planController.text.trim(),
        symptoms: _splitLines(_symptomsController.text),
        duration: _splitLines(_durationController.text),
        severity: _splitLines(_severityController.text),
        medicalHistory: _splitLines(_historyController.text),
        medications: _splitLines(_medicationsController.text),
        allergies: _splitLines(_allergiesController.text),
        vitals: _splitLines(_vitalsController.text),
      );
      final logs = await _api.fetchAuditLogs(caseRecord.caseId);
      final refreshedCases = await _api.fetchCases();
      final refreshedSelected = refreshedCases.firstWhere((item) => item.caseId == updatedCase.caseId);
      if (!mounted) return;
      setState(() {
        _cases = refreshedCases;
        _caseRecord = refreshedSelected;
        _auditLogs = logs;
        _savingDraft = false;
      });
      _syncDraftControllers(refreshedSelected);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _savingDraft = false;
        _error = '$error';
      });
    }
  }

  Future<void> _runSafetyAgent() async {
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;
    await _runAgent(() => _api.runSafetyReviewer(caseRecord.caseId));
  }

  Future<void> _runQueueAgent() async {
    await _runAgent(_api.runQueueOrchestrator);
  }

  Future<void> _runIntakeAgent() async {
    await _runAgent(
      () => _api.runClinicalIntakeAgent(
        transcript: _agentTranscriptController.text.trim(),
        clinicianName: _reviewerController.text.trim(),
      ),
    );
    await _load();
  }

  Future<void> _runAgent(Future<AgentRunResponse> Function() action) async {
    setState(() {
      _runningAgent = true;
      _error = null;
    });
    try {
      final response = await action();
      if (!mounted) return;
      setState(() {
        _latestAgentResponse = response;
        _runningAgent = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _runningAgent = false;
        _error = '$error';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final caseRecord = _caseRecord;

    return Scaffold(
      body: DecoratedBox(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFFF6F0E8), Color(0xFFE5EDE6), Color(0xFFF6E4D7)],
          ),
        ),
        child: SafeArea(
          child: caseRecord == null
              ? _LoadingState(error: _error, onRetry: _load)
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(24),
                    children: [
                      _HeroBanner(
                        patientLabel: caseRecord.patientLabel,
                        caseId: caseRecord.caseId,
                        reviewStatus: caseRecord.reviewStatus,
                        updatedAt: caseRecord.updatedAt,
                      ),
                      const SizedBox(height: 20),
                      LayoutBuilder(
                        builder: (context, constraints) {
                          final stacked = constraints.maxWidth < 1100;
                          final caseRail = _CaseRail(
                            cases: _cases,
                            selectedCaseId: caseRecord.caseId,
                            switchingCase: _switchingCase,
                            onSelectCase: _selectCase,
                          );
                          final leftColumn = Column(
                            children: [
                              _Panel(
                                title: 'Visit transcript',
                                subtitle: 'Source-of-truth conversation used for note generation.',
                                child: SelectableText(caseRecord.transcript, style: theme.textTheme.bodyLarge),
                              ),
                              const SizedBox(height: 16),
                              _EditableNotePanel(
                                disclaimer: caseRecord.note.disclaimer,
                                summaryController: _summaryController,
                                subjectiveController: _subjectiveController,
                                objectiveController: _objectiveController,
                                assessmentController: _assessmentController,
                                planController: _planController,
                                symptomsController: _symptomsController,
                                durationController: _durationController,
                                severityController: _severityController,
                                historyController: _historyController,
                                medicationsController: _medicationsController,
                                allergiesController: _allergiesController,
                                vitalsController: _vitalsController,
                                amendReasonController: _amendReasonController,
                                savingDraft: _savingDraft,
                                onSaveDraft: _saveDraftAmendments,
                              ),
                              const SizedBox(height: 16),
                              _EntitiesPanel(entities: caseRecord.note.entities),
                            ],
                          );

                          final rightColumn = Column(
                            children: [
                              _FlagsPanel(
                                flags: caseRecord.note.reviewFlags,
                                differentialDiagnosis: caseRecord.note.differentialDiagnosis,
                              ),
                              const SizedBox(height: 16),
                              _ReviewPanel(
                                reviewerController: _reviewerController,
                                feedbackController: _feedbackController,
                                reviewStatus: caseRecord.reviewStatus,
                                clinicianFeedback: caseRecord.clinicianFeedback,
                                submitting: _submitting,
                                onApprove: () => _submitReview('approved'),
                                onRequestChanges: () => _submitReview('needs_changes'),
                              ),
                              const SizedBox(height: 16),
                              _AgentPanel(
                                agents: _agents,
                                agentTranscriptController: _agentTranscriptController,
                                runningAgent: _runningAgent,
                                latestResponse: _latestAgentResponse,
                                onRunSafety: _runSafetyAgent,
                                onRunQueue: _runQueueAgent,
                                onRunIntake: _runIntakeAgent,
                              ),
                              const SizedBox(height: 16),
                              _AuditPanel(auditLogs: _auditLogs),
                            ],
                          );

                          if (stacked) {
                            return Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                caseRail,
                                const SizedBox(height: 16),
                                leftColumn,
                                const SizedBox(height: 16),
                                rightColumn,
                              ],
                            );
                          }

                          return Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              SizedBox(width: 280, child: caseRail),
                              const SizedBox(width: 16),
                              Expanded(flex: 6, child: leftColumn),
                              const SizedBox(width: 16),
                              Expanded(flex: 4, child: rightColumn),
                            ],
                          );
                        },
                      ),
                      if (_error != null) ...[
                        const SizedBox(height: 16),
                        Text(_error!, style: theme.textTheme.bodyMedium?.copyWith(color: Colors.red.shade800)),
                      ],
                    ],
                  ),
                ),
        ),
      ),
    );
  }
}

class _LoadingState extends StatelessWidget {
  const _LoadingState({required this.error, required this.onRetry});

  final String? error;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (error == null) ...[
                  const CircularProgressIndicator(),
                  const SizedBox(height: 18),
                  Text('Loading clinician dashboard...', style: Theme.of(context).textTheme.titleMedium),
                ] else ...[
                  Text(error!, textAlign: TextAlign.center),
                  const SizedBox(height: 18),
                  FilledButton(onPressed: onRetry, child: const Text('Retry')),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CaseRail extends StatelessWidget {
  const _CaseRail({
    required this.cases,
    required this.selectedCaseId,
    required this.switchingCase,
    required this.onSelectCase,
  });

  final List<CaseRecord> cases;
  final String selectedCaseId;
  final bool switchingCase;
  final ValueChanged<String> onSelectCase;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Today\'s cases',
      subtitle: switchingCase ? 'Switching chart...' : 'Select a chart to review, amend, or approve.',
      accent: const Color(0xFF244553),
      child: Column(
        children: cases.map((caseRecord) {
          final selected = caseRecord.caseId == selectedCaseId;
          return Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: InkWell(
              borderRadius: BorderRadius.circular(20),
              onTap: switchingCase ? null : () => onSelectCase(caseRecord.caseId),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 180),
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: selected ? const Color(0xFF12212B) : const Color(0xFFF7F3EE),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            caseRecord.patientLabel,
                            style: Theme.of(context).textTheme.titleMedium?.copyWith(
                                  color: selected ? Colors.white : const Color(0xFF12212B),
                                ),
                          ),
                        ),
                        _MiniBadge(label: caseRecord.reviewStatus),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      caseRecord.note.summary,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color: selected ? Colors.white70 : Colors.black87,
                          ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      caseRecord.updatedAt.toLocal().toString().substring(0, 16),
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                            color: selected ? Colors.white54 : Colors.black54,
                          ),
                    ),
                  ],
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}

class _HeroBanner extends StatelessWidget {
  const _HeroBanner({
    required this.patientLabel,
    required this.caseId,
    required this.reviewStatus,
    required this.updatedAt,
  });

  final String patientLabel;
  final String caseId;
  final String reviewStatus;
  final DateTime updatedAt;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.all(28),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(32),
        gradient: const LinearGradient(
          colors: [Color(0xFF12212B), Color(0xFF244553), Color(0xFF1D6A72)],
        ),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.12),
            blurRadius: 24,
            offset: const Offset(0, 12),
          ),
        ],
      ),
      child: Wrap(
        runSpacing: 16,
        spacing: 24,
        alignment: WrapAlignment.spaceBetween,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 560),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Clinician Review Dashboard', style: theme.textTheme.displaySmall?.copyWith(color: Colors.white)),
                const SizedBox(height: 10),
                Text(
                  'Review AI-generated documentation, verify risk flags, and sign off with an audit trail.',
                  style: theme.textTheme.bodyLarge?.copyWith(color: Colors.white.withValues(alpha: 0.84)),
                ),
              ],
            ),
          ),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _StatusChip(label: reviewStatus.replaceAll('_', ' ').toUpperCase()),
              const SizedBox(height: 12),
              Text(patientLabel, style: theme.textTheme.titleLarge?.copyWith(color: Colors.white)),
              Text('Case $caseId', style: theme.textTheme.bodyMedium?.copyWith(color: Colors.white70)),
              Text(
                'Updated ${updatedAt.toLocal().toString().substring(0, 16)}',
                style: theme.textTheme.bodyMedium?.copyWith(color: Colors.white70),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({
    required this.title,
    required this.subtitle,
    required this.child,
    this.accent = const Color(0xFFC96F4A),
  });

  final String title;
  final String subtitle;
  final Widget child;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(22),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 12,
                  height: 12,
                  decoration: BoxDecoration(color: accent, borderRadius: BorderRadius.circular(999)),
                ),
                const SizedBox(width: 10),
                Expanded(child: Text(title, style: theme.textTheme.titleLarge)),
              ],
            ),
            const SizedBox(height: 8),
            Text(subtitle, style: theme.textTheme.bodyMedium?.copyWith(color: Colors.black54)),
            const SizedBox(height: 18),
            child,
          ],
        ),
      ),
    );
  }
}

class _EditableNotePanel extends StatelessWidget {
  const _EditableNotePanel({
    required this.disclaimer,
    required this.summaryController,
    required this.subjectiveController,
    required this.objectiveController,
    required this.assessmentController,
    required this.planController,
    required this.symptomsController,
    required this.durationController,
    required this.severityController,
    required this.historyController,
    required this.medicationsController,
    required this.allergiesController,
    required this.vitalsController,
    required this.amendReasonController,
    required this.savingDraft,
    required this.onSaveDraft,
  });

  final String disclaimer;
  final TextEditingController summaryController;
  final TextEditingController subjectiveController;
  final TextEditingController objectiveController;
  final TextEditingController assessmentController;
  final TextEditingController planController;
  final TextEditingController symptomsController;
  final TextEditingController durationController;
  final TextEditingController severityController;
  final TextEditingController historyController;
  final TextEditingController medicationsController;
  final TextEditingController allergiesController;
  final TextEditingController vitalsController;
  final TextEditingController amendReasonController;
  final bool savingDraft;
  final VoidCallback onSaveDraft;

  @override
  Widget build(BuildContext context) {
    final sections = [
      ('Subjective', subjectiveController, const Color(0xFFC96F4A)),
      ('Objective', objectiveController, const Color(0xFF3B7A57)),
      ('Assessment', assessmentController, const Color(0xFF2B5C8A)),
      ('Plan', planController, const Color(0xFF8A5A2E)),
    ];

    return _Panel(
      title: 'Clinician amendment workspace',
      subtitle: disclaimer,
      accent: const Color(0xFF1D6A72),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: summaryController,
            minLines: 3,
            maxLines: 5,
            decoration: const InputDecoration(labelText: 'Summary'),
          ),
          const SizedBox(height: 16),
          LayoutBuilder(
            builder: (context, constraints) {
              final stacked = constraints.maxWidth < 760;
              return Wrap(
                runSpacing: 16,
                spacing: 16,
                children: sections.map((section) {
                  return SizedBox(
                    width: stacked ? double.infinity : (constraints.maxWidth - 16) / 2,
                    child: DecoratedBox(
                      decoration: BoxDecoration(
                        color: const Color(0xFFF7F3EE),
                        borderRadius: BorderRadius.circular(24),
                      ),
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Container(
                                  width: 12,
                                  height: 12,
                                  decoration: BoxDecoration(
                                    color: section.$3,
                                    borderRadius: BorderRadius.circular(999),
                                  ),
                                ),
                                const SizedBox(width: 10),
                                Text(section.$1, style: Theme.of(context).textTheme.titleMedium),
                              ],
                            ),
                            const SizedBox(height: 12),
                            TextField(
                              controller: section.$2,
                              minLines: 5,
                              maxLines: 8,
                              decoration: InputDecoration(labelText: '${section.$1} note'),
                            ),
                          ],
                        ),
                      ),
                    ),
                  );
                }).toList(),
              );
            },
          ),
          const SizedBox(height: 16),
          LayoutBuilder(
            builder: (context, constraints) {
              final stacked = constraints.maxWidth < 760;
              final entityFields = [
                ('Symptoms', symptomsController),
                ('Duration', durationController),
                ('Severity', severityController),
                ('History', historyController),
                ('Medications', medicationsController),
                ('Allergies', allergiesController),
                ('Vitals', vitalsController),
              ];
              return Wrap(
                runSpacing: 16,
                spacing: 16,
                children: entityFields.map((field) {
                  return SizedBox(
                    width: stacked ? double.infinity : (constraints.maxWidth - 16) / 2,
                    child: TextField(
                      controller: field.$2,
                      minLines: 3,
                      maxLines: 5,
                      decoration: InputDecoration(
                        labelText: field.$1,
                        hintText: 'One item per line or comma separated',
                      ),
                    ),
                  );
                }).toList(),
              );
            },
          ),
          const SizedBox(height: 16),
          TextField(
            controller: amendReasonController,
            minLines: 2,
            maxLines: 3,
            decoration: const InputDecoration(
              labelText: 'Amendment reason',
              hintText: 'Explain what was changed before saving the draft',
            ),
          ),
          const SizedBox(height: 16),
          Align(
            alignment: Alignment.centerLeft,
            child: FilledButton.icon(
              onPressed: savingDraft ? null : onSaveDraft,
              icon: const Icon(Icons.save_as_rounded),
              label: Text(savingDraft ? 'Saving draft...' : 'Save amended draft'),
            ),
          ),
        ],
      ),
    );
  }
}

class _EntitiesPanel extends StatelessWidget {
  const _EntitiesPanel({required this.entities});

  final ClinicalEntities entities;

  @override
  Widget build(BuildContext context) {
    final groups = <String, List<ExtractedFact>>{
      'Symptoms': entities.symptoms,
      'Duration': entities.duration,
      'Medications': entities.medications,
      'Allergies': entities.allergies,
      'Vitals': entities.vitals,
      'History': entities.medicalHistory,
    };

    return _Panel(
      title: 'Structured entities',
      subtitle: 'Fast scan of extracted charting facts with confidence labels.',
      accent: const Color(0xFF244553),
      child: Wrap(
        spacing: 12,
        runSpacing: 12,
        children: groups.entries.map((entry) {
          return SizedBox(
            width: 220,
            child: DecoratedBox(
              decoration: BoxDecoration(
                color: const Color(0xFFF7F3EE),
                borderRadius: BorderRadius.circular(20),
              ),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(entry.key, style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 8),
                    if (entry.value.isEmpty)
                      Text('No items captured', style: Theme.of(context).textTheme.bodyMedium)
                    else
                      ...entry.value.map(
                        (fact) => Padding(
                          padding: const EdgeInsets.only(bottom: 8),
                          child: Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Expanded(child: Text(fact.value)),
                              const SizedBox(width: 8),
                              _MiniBadge(label: fact.confidence),
                            ],
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}

class _FlagsPanel extends StatelessWidget {
  const _FlagsPanel({
    required this.flags,
    required this.differentialDiagnosis,
  });

  final List<ReviewFlag> flags;
  final List<DifferentialDiagnosisItem> differentialDiagnosis;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Risk and reasoning',
      subtitle: 'Items that need clinician attention before the chart is finalized.',
      accent: const Color(0xFFB24B36),
      child: Column(
        children: [
          ...flags.map(
            (flag) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: const Color(0xFFFFF1EB),
                  borderRadius: BorderRadius.circular(18),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        _MiniBadge(label: flag.severity),
                        const SizedBox(width: 8),
                        Expanded(child: Text(flag.issue, style: Theme.of(context).textTheme.titleMedium)),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(flag.recommendation),
                  ],
                ),
              ),
            ),
          ),
          if (differentialDiagnosis.isNotEmpty) ...[
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerLeft,
              child: Text('Suggested differential', style: Theme.of(context).textTheme.titleMedium),
            ),
            const SizedBox(height: 10),
            ...differentialDiagnosis.map(
              (item) => ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(item.condition),
                subtitle: Text(item.rationale),
                trailing: _MiniBadge(label: item.confidence),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _ReviewPanel extends StatelessWidget {
  const _ReviewPanel({
    required this.reviewerController,
    required this.feedbackController,
    required this.reviewStatus,
    required this.clinicianFeedback,
    required this.submitting,
    required this.onApprove,
    required this.onRequestChanges,
  });

  final TextEditingController reviewerController;
  final TextEditingController feedbackController;
  final String reviewStatus;
  final String clinicianFeedback;
  final bool submitting;
  final VoidCallback onApprove;
  final VoidCallback onRequestChanges;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Clinician sign-off',
      subtitle: 'Approve the draft or request edits. Every action is written to the audit trail.',
      accent: const Color(0xFF3B7A57),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: reviewerController,
            decoration: const InputDecoration(labelText: 'Reviewed by'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: feedbackController,
            maxLines: 5,
            decoration: InputDecoration(
              labelText: 'Feedback',
              hintText: clinicianFeedback.isEmpty ? 'Add review notes or requested edits' : clinicianFeedback,
            ),
          ),
          const SizedBox(height: 14),
          Text('Current status: ${reviewStatus.replaceAll('_', ' ')}'),
          const SizedBox(height: 16),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              FilledButton.icon(
                onPressed: submitting ? null : onApprove,
                icon: const Icon(Icons.verified_rounded),
                label: Text(submitting ? 'Saving...' : 'Approve note'),
              ),
              OutlinedButton.icon(
                onPressed: submitting ? null : onRequestChanges,
                icon: const Icon(Icons.edit_note_rounded),
                label: const Text('Request changes'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _AgentPanel extends StatelessWidget {
  const _AgentPanel({
    required this.agents,
    required this.agentTranscriptController,
    required this.runningAgent,
    required this.latestResponse,
    required this.onRunSafety,
    required this.onRunQueue,
    required this.onRunIntake,
  });

  final List<AgentSummary> agents;
  final TextEditingController agentTranscriptController;
  final bool runningAgent;
  final AgentRunResponse? latestResponse;
  final VoidCallback onRunSafety;
  final VoidCallback onRunQueue;
  final VoidCallback onRunIntake;

  @override
  Widget build(BuildContext context) {
    final resultView = latestResponse == null ? null : _buildResultView(context, latestResponse!);
    return _Panel(
      title: 'Agent workspace',
      subtitle: 'Run integrated project agents directly from the dashboard.',
      accent: const Color(0xFF8A5A2E),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${agents.length} agents available', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: agents.map((agent) => _MiniBadge(label: agent.name)).toList(),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: agentTranscriptController,
            minLines: 3,
            maxLines: 5,
            decoration: const InputDecoration(
              labelText: 'Intake agent transcript',
              hintText: 'Paste a short transcript to create a new case through the agent',
            ),
          ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              FilledButton.icon(
                onPressed: runningAgent ? null : onRunIntake,
                icon: const Icon(Icons.playlist_add_check_circle_rounded),
                label: const Text('Run intake'),
              ),
              OutlinedButton.icon(
                onPressed: runningAgent ? null : onRunSafety,
                icon: const Icon(Icons.health_and_safety_rounded),
                label: const Text('Run safety review'),
              ),
              OutlinedButton.icon(
                onPressed: runningAgent ? null : onRunQueue,
                icon: const Icon(Icons.low_priority_rounded),
                label: const Text('Run queue triage'),
              ),
            ],
          ),
          if (resultView != null) ...[
            const SizedBox(height: 16),
            resultView,
          ],
        ],
      ),
    );
  }

  Widget _buildResultView(BuildContext context, AgentRunResponse response) {
    switch (response.agentId) {
      case 'clinical_intake_agent':
        return _ClinicalIntakeResultCard(result: response.result);
      case 'note_safety_reviewer':
        return _SafetyReviewResultCard(result: response.result);
      case 'review_queue_orchestrator':
        return _QueueTriageResultCard(result: response.result);
      default:
        return _FallbackAgentResultCard(response: response);
    }
  }
}

class _ClinicalIntakeResultCard extends StatelessWidget {
  const _ClinicalIntakeResultCard({required this.result});

  final Map<String, dynamic> result;

  @override
  Widget build(BuildContext context) {
    final entities = ClinicalEntities.fromJson(result['entities'] as Map<String, dynamic>);
    final flags = (result['review_flags'] as List<dynamic>)
        .map((item) => ReviewFlag.fromJson(item as Map<String, dynamic>))
        .toList();

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFFF7F3EE),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Clinical Intake Agent', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(result['summary'] as String),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _MiniBadge(label: result['review_status'] as String),
              _MiniBadge(label: result['patient_label'] as String),
            ],
          ),
          const SizedBox(height: 12),
          Text('Captured symptoms', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: entities.symptoms.isEmpty
                ? [const Text('No symptom entities captured')]
                : entities.symptoms.map((item) => _MiniBadge(label: item.value)).toList(),
          ),
          if (flags.isNotEmpty) ...[
            const SizedBox(height: 12),
            Text('Review flags', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 6),
            ...flags.map((flag) => Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text('${flag.severity.toUpperCase()}: ${flag.issue}'),
                )),
          ],
        ],
      ),
    );
  }
}

class _SafetyReviewResultCard extends StatelessWidget {
  const _SafetyReviewResultCard({required this.result});

  final Map<String, dynamic> result;

  @override
  Widget build(BuildContext context) {
    final issues = (result['issues'] as List<dynamic>)
        .map((item) => SafetyIssue.fromJson(item as Map<String, dynamic>))
        .toList();

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF1EB),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Safety Review Result', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text('Case ${result['patient_label']} • valid=${result['valid']}'),
          const SizedBox(height: 12),
          ...issues.map(
            (issue) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      _MiniBadge(label: issue.severity),
                      const SizedBox(width: 8),
                      Expanded(child: Text(issue.issue, style: Theme.of(context).textTheme.titleMedium)),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Text(issue.recommendation),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _QueueTriageResultCard extends StatelessWidget {
  const _QueueTriageResultCard({required this.result});

  final Map<String, dynamic> result;

  @override
  Widget build(BuildContext context) {
    final rankedCases = (result['ranked_cases'] as List<dynamic>)
        .map((item) => QueueRankedCase.fromJson(item as Map<String, dynamic>))
        .toList();

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFFEAF2F3),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Queue Triage Result', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text('Queue size: ${result['queue_size']}'),
          const SizedBox(height: 12),
          ...rankedCases.take(4).toList().asMap().entries.map(
                (entry) => Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${entry.key + 1}. ${entry.value.patientLabel}',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      const SizedBox(height: 4),
                      Text('Status: ${entry.value.reviewStatus}'),
                      Text('Top issue: ${entry.value.topIssue}'),
                      Text('Next action: ${entry.value.recommendedAction}'),
                    ],
                  ),
                ),
              ),
        ],
      ),
    );
  }
}

class _FallbackAgentResultCard extends StatelessWidget {
  const _FallbackAgentResultCard({required this.response});

  final AgentRunResponse response;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFFF7F3EE),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(response.agentName, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(response.result.toString()),
        ],
      ),
    );
  }
}

class _AuditPanel extends StatelessWidget {
  const _AuditPanel({required this.auditLogs});

  final List<AuditLogEntry> auditLogs;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Audit timeline',
      subtitle: 'Open-source logging for generation and review actions.',
      accent: const Color(0xFF2B5C8A),
      child: Column(
        children: auditLogs.map((log) {
          return Padding(
            padding: const EdgeInsets.only(bottom: 14),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 12,
                  height: 12,
                  margin: const EdgeInsets.only(top: 4),
                  decoration: BoxDecoration(
                    color: const Color(0xFF2B5C8A),
                    borderRadius: BorderRadius.circular(999),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('${log.eventType} • ${log.actor}', style: Theme.of(context).textTheme.titleMedium),
                      const SizedBox(height: 4),
                      Text(log.details),
                      const SizedBox(height: 2),
                      Text(
                        log.createdAt.toLocal().toString().substring(0, 19),
                        style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: Colors.black54),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: Colors.white.withValues(alpha: 0.22)),
      ),
      child: Text(label, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w700)),
    );
  }
}

class _MiniBadge extends StatelessWidget {
  const _MiniBadge({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: const Color(0xFF12212B),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label.toUpperCase(),
        style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.w700),
      ),
    );
  }
}
