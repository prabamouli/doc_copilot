import 'dart:async';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import 'api.dart';
import 'models.dart';

enum WorkspaceView { overview, noteStudio, agents, audit }

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key, ClinicApiClient? apiClient}) : _apiClient = apiClient;

  final ClinicApiClient? _apiClient;

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  final ScrollController _mainScrollController = ScrollController();
  late final ClinicApiClient _api;

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
  final TextEditingController _searchController = TextEditingController();

  final GlobalKey _transcriptKey = GlobalKey();
  final GlobalKey _flagsKey = GlobalKey();
  final GlobalKey _noteEditorKey = GlobalKey();
  final GlobalKey _entitiesKey = GlobalKey();
  final GlobalKey _reviewPanelKey = GlobalKey();
  final GlobalKey _agentsKey = GlobalKey();
  final GlobalKey _auditKey = GlobalKey();

  bool _submitting = false;
  bool _savingDraft = false;
  bool _switchingCase = false;
  bool _runningAgent = false;
  bool _runningBillingAgent = false;
  bool _loading = true;
  String _queueFilter = 'all';
  String? _error;
  String? _historyError;
  CaseRecord? _caseRecord;
  PatientHistoryDebugResponse? _historyDebug;
  bool _loadingHistory = false;
  List<CaseRecord> _cases = const [];
  List<AuditLogEntry> _auditLogs = const [];
  List<AgentSummary> _agents = const [];
  AgentRunResponse? _latestAgentResponse;
  AgentRunResponse? _latestBillingResponse;
  VisionObjectiveResponse? _latestVisionObjective;
  Timer? _conversationCaptureTimer;
  Timer? _clinicalNudgeTimer;
  ClinicalNudgeSocket? _clinicalNudgeSocket;
  StreamSubscription<ClinicalNudgeEvent>? _clinicalNudgeSubscription;
  final ImagePicker _imagePicker = ImagePicker();
  bool _ambientNudgesEnabled = true;
  bool _analyzingVision = false;
  int _observerElapsedSeconds = 0;
  String? _lastNudgeId;
  WorkspaceView _workspaceView = WorkspaceView.overview;

  @override
  void initState() {
    super.initState();
    _api = widget._apiClient ?? ClinicApiClient();
    _load();
    _startConversationCaptureLoop();
    _startClinicalNudgeLoop();
  }

  @override
  void dispose() {
    _conversationCaptureTimer?.cancel();
    _clinicalNudgeTimer?.cancel();
    _clinicalNudgeSubscription?.cancel();
    unawaited(_clinicalNudgeSocket?.close());
    _mainScrollController.dispose();
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
    _searchController.dispose();
    super.dispose();
  }

  void _startConversationCaptureLoop() {
    _conversationCaptureTimer?.cancel();
    _conversationCaptureTimer = Timer.periodic(const Duration(seconds: 3), (_) {
      _captureConversationTick();
    });
  }

  void _startClinicalNudgeLoop() {
    _clinicalNudgeSubscription?.cancel();
    _clinicalNudgeTimer?.cancel();
    unawaited(_clinicalNudgeSocket?.close());
    _clinicalNudgeSocket = null;

    if (!_ambientNudgesEnabled) {
      return;
    }

    _clinicalNudgeSocket = _api.connectClinicalNudges();
    _clinicalNudgeSubscription = _clinicalNudgeSocket!.events.listen(_handleClinicalNudgeEvent);

    _clinicalNudgeTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      _sendClinicalNudgeObservation();
    });
  }

  void _sendClinicalNudgeObservation() {
    if (!_ambientNudgesEnabled) return;
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;

    final transcript = caseRecord.transcript.trim();
    if (transcript.isEmpty) return;

    _observerElapsedSeconds += 30;
    _clinicalNudgeSocket?.observe(
      caseId: caseRecord.caseId,
      transcript: transcript,
      elapsedSeconds: _observerElapsedSeconds,
    );
  }

  void _handleClinicalNudgeEvent(ClinicalNudgeEvent event) {
    if (!_ambientNudgesEnabled) return;
    if (!mounted || event.type != 'clinical_nudge') return;
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;
    if (event.caseId != caseRecord.caseId) return;

    final eventId = event.id;
    if (eventId != null && eventId == _lastNudgeId) return;
    _lastNudgeId = eventId;

    final text = event.evidence.isNotEmpty
        ? '${event.message}\nEvidence: ${event.evidence}'
        : event.message;

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('${event.title}: $text'),
        backgroundColor: Colors.red.shade700,
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 6),
      ),
    );
  }

  Future<void> _captureConversationTick() async {
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;
    if (caseRecord.transcript.trim().isEmpty) return;
    try {
      await _api.captureConversationSnapshot(
        caseId: caseRecord.caseId,
        transcript: caseRecord.transcript,
      );
    } catch (_) {
      // Background capture should never disrupt clinician workflow.
    }
  }

  Future<void> _load() async {
    setState(() {
      _error = null;
      _loading = true;
    });
    try {
      final cases = await _api.fetchCases();
      final agents = await _api.fetchAgents();
      if (cases.isEmpty) {
        throw Exception('No cases available');
      }
      final selectedCase = _pickSelectedCase(cases);
      final auditLogs = await _api.fetchAuditLogs(selectedCase.caseId);
      if (!mounted) return;
      setState(() {
        _cases = cases;
        _agents = agents;
        _caseRecord = selectedCase;
        _auditLogs = auditLogs;
        _loading = false;
      });
      _syncDraftControllers(selectedCase);
      unawaited(_loadHistoryCitations(selectedCase));
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '$error';
        _loading = false;
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
        _workspaceView = WorkspaceView.overview;
      });
      _observerElapsedSeconds = 0;
      _lastNudgeId = null;
      _syncDraftControllers(selectedCase);
      unawaited(_loadHistoryCitations(selectedCase));
      _showMessage('Opened ${selectedCase.patientLabel}');
      _scrollToTop();
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
      _showMessage(status == 'approved' ? 'Case approved' : 'Case sent back for changes');
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
      _showMessage('Draft amendments saved');
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
    final newCaseId = _latestAgentResponse?.result['case_id'] as String?;
    if (newCaseId != null) {
      await _selectCase(newCaseId);
      setState(() {
        _workspaceView = WorkspaceView.agents;
      });
    }
  }

  Future<void> _runBillingAgent() async {
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;

    setState(() {
      _runningBillingAgent = true;
      _error = null;
    });
    try {
      final response = await _api.runBillingOptimizer(caseRecord.caseId);
      if (!mounted) return;
      setState(() {
        _latestBillingResponse = response;
        _runningBillingAgent = false;
      });
      _showMessage('Billing optimizer finished');
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _runningBillingAgent = false;
        _error = '$error';
      });
    }
  }

  Future<void> _runVisionAgent(String mediaType) async {
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;

    XFile? selected;
    if (mediaType == 'video') {
      selected = await _imagePicker.pickVideo(source: ImageSource.camera, maxDuration: const Duration(seconds: 20));
    } else {
      selected = await _imagePicker.pickImage(source: ImageSource.camera, imageQuality: 85);
    }
    if (selected == null) return;

    setState(() {
      _analyzingVision = true;
      _error = null;
    });
    try {
      final response = await _api.analyzeVisionMedia(mediaPath: selected.path, mediaType: mediaType);
      if (!mounted) return;
      setState(() {
        _latestVisionObjective = response;
        _analyzingVision = false;
      });

      final injected = response.objectiveText.trim();
      final currentObjective = _objectiveController.text.trim();
      if (injected.isNotEmpty && !currentObjective.contains(injected)) {
        _objectiveController.text = currentObjective.isEmpty ? injected : '$currentObjective\n$injected';
      }
      _showMessage('Objective auto-injected from ${mediaType == 'video' ? 'gait video' : 'wound image'}.');
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _analyzingVision = false;
        _error = '$error';
      });
    }
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
        _workspaceView = WorkspaceView.agents;
      });
      _showMessage('${response.agentName} finished');
      _scrollToKey(_agentsKey);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _runningAgent = false;
        _error = '$error';
      });
    }
  }

  void _showMessage(String message) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _showCaseQueueSheet() async {
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (context) => DraggableScrollableSheet(
        initialChildSize: 0.82,
        minChildSize: 0.55,
        maxChildSize: 0.94,
        builder: (context, scrollController) => Container(
          decoration: const BoxDecoration(
            color: Color(0xFFFFFBF6),
            borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
          ),
          child: ListView(
            controller: scrollController,
            padding: const EdgeInsets.all(20),
            children: [
              Center(
                child: Container(
                  width: 48,
                  height: 5,
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(999),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              _QueueRail(
                cases: _visibleCases,
                selectedCaseId: caseRecord.caseId,
                switchingCase: _switchingCase,
                searchController: _searchController,
                queueFilter: _queueFilter,
                onSearchChanged: (_) => setState(() {}),
                onFilterChanged: (value) => setState(() {
                  _queueFilter = value;
                }),
                onSelectCase: (caseId) async {
                  Navigator.of(context).pop();
                  await _selectCase(caseId);
                },
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _scrollToTop() {
    if (!_mainScrollController.hasClients) return;
    _mainScrollController.animateTo(
      0,
      duration: const Duration(milliseconds: 260),
      curve: Curves.easeOutCubic,
    );
  }

  void _scrollToKey(GlobalKey key) {
    final currentContext = key.currentContext;
    if (currentContext == null) return;
    Scrollable.ensureVisible(
      currentContext,
      duration: const Duration(milliseconds: 280),
      curve: Curves.easeOutCubic,
      alignment: 0.08,
    );
  }

  void _openWorkspace(WorkspaceView workspaceView, {GlobalKey? focusKey}) {
    setState(() {
      _workspaceView = workspaceView;
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (focusKey != null) {
        _scrollToKey(focusKey);
      } else {
        _scrollToTop();
      }
    });
  }

  void _handleSafetyIssueTap(SafetyIssue issue) {
    final normalized = issue.issue.toLowerCase();
    if (normalized.contains('vital') || normalized.contains('allerg') || normalized.contains('medication')) {
      _openWorkspace(WorkspaceView.noteStudio, focusKey: _entitiesKey);
      return;
    }
    if (normalized.contains('objective') || normalized.contains('assessment') || normalized.contains('plan')) {
      _openWorkspace(WorkspaceView.noteStudio, focusKey: _noteEditorKey);
      return;
    }
    _openWorkspace(WorkspaceView.overview, focusKey: _flagsKey);
  }

  Future<void> _handleQueueCaseTap(String caseId) async {
    await _selectCase(caseId);
    _openWorkspace(WorkspaceView.overview, focusKey: _reviewPanelKey);
  }

  int _countCasesByStatus(String status) {
    return _cases.where((caseRecord) => caseRecord.reviewStatus == status).length;
  }

  int _countCriticalCases() {
    return _cases.where((caseRecord) {
      return caseRecord.note.reviewFlags.any((flag) => flag.severity == 'critical');
    }).length;
  }

  List<CaseRecord> get _visibleCases {
    final query = _searchController.text.trim().toLowerCase();
    return _cases.where((caseRecord) {
      if (_queueFilter != 'all' && caseRecord.reviewStatus != _queueFilter) {
        return false;
      }
      if (query.isEmpty) return true;
      return caseRecord.patientLabel.toLowerCase().contains(query) ||
          caseRecord.caseId.toLowerCase().contains(query) ||
          caseRecord.note.summary.toLowerCase().contains(query);
    }).toList()
      ..sort((left, right) => right.updatedAt.compareTo(left.updatedAt));
  }

  int _completionScore(CaseRecord caseRecord) {
    var score = 30;
    if (caseRecord.note.summary.isNotEmpty) score += 10;
    if (caseRecord.note.soapNote.subjective.text.isNotEmpty) score += 10;
    if (caseRecord.note.soapNote.objective.text.isNotEmpty) score += 10;
    if (caseRecord.note.soapNote.assessment.text.isNotEmpty) score += 10;
    if (caseRecord.note.soapNote.plan.text.isNotEmpty) score += 10;
    if (caseRecord.note.entities.symptoms.isNotEmpty) score += 10;
    if (caseRecord.note.entities.allergies.isNotEmpty) score += 5;
    if (caseRecord.note.entities.vitals.isNotEmpty) score += 5;
    return score.clamp(0, 100);
  }

  String _patientIdForCase(CaseRecord caseRecord) {
    final normalized = caseRecord.patientLabel
        .toLowerCase()
        .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
        .replaceAll(RegExp(r'^-+|-+$'), '');
    return normalized.isEmpty ? 'unknown-patient' : normalized;
  }

  String _retrievalQueryForCase(CaseRecord caseRecord) {
    final summary = caseRecord.note.summary.trim();
    if (summary.isNotEmpty && summary.toLowerCase() != 'unknown') {
      return summary;
    }

    final subjective = caseRecord.note.soapNote.subjective.text.trim();
    if (subjective.isNotEmpty && subjective.toLowerCase() != 'unknown') {
      return subjective;
    }

    return caseRecord.transcript.trim();
  }

  Future<void> _loadHistoryCitations(CaseRecord caseRecord) async {
    final complaint = _retrievalQueryForCase(caseRecord);
    if (complaint.isEmpty) {
      if (!mounted) return;
      setState(() {
        _historyDebug = null;
        _historyError = 'No complaint text available for retrieval.';
      });
      return;
    }

    setState(() {
      _loadingHistory = true;
      _historyError = null;
    });

    try {
      final payload = await _api.fetchPatientHistoryDebug(
        patientId: _patientIdForCase(caseRecord),
        currentComplaint: complaint,
        topK: 5,
      );
      if (!mounted) return;
      setState(() {
        _historyDebug = payload;
        _loadingHistory = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _loadingHistory = false;
        _historyError = '$error';
      });
    }
  }

  Future<void> _refreshHistoryCitations() async {
    final caseRecord = _caseRecord;
    if (caseRecord == null) return;
    await _loadHistoryCitations(caseRecord);
  }

  @override
  Widget build(BuildContext context) {
    final caseRecord = _caseRecord;
    final isMobile = MediaQuery.sizeOf(context).width < 720;

    return Scaffold(
      bottomNavigationBar: _loading || caseRecord == null || !isMobile
          ? null
          : _MobileBottomNav(
              currentView: _workspaceView,
              onChanged: (view) => _openWorkspace(view),
            ),
      body: DecoratedBox(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [Color(0xFFF4E9DB), Color(0xFFE6EFE5), Color(0xFFF9ECDD)],
          ),
        ),
        child: SafeArea(
          child: _loading || caseRecord == null
              ? _LoadingState(error: _error, onRetry: _load)
              : Column(
                  children: [
                    Padding(
                      padding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
                      child: Column(
                        children: [
                          _CompactHeader(
                            patientLabel: caseRecord.patientLabel,
                            caseId: caseRecord.caseId,
                            reviewStatus: caseRecord.reviewStatus,
                            updatedAt: caseRecord.updatedAt,
                          ),
                          const SizedBox(height: 12),
                          _CaseSelectorBar(
                            cases: _cases,
                            selectedCaseId: caseRecord.caseId,
                            onSelectCase: _selectCase,
                            onRefresh: _load,
                            switchingCase: _switchingCase,
                            onOpenQueue: isMobile ? _showCaseQueueSheet : null,
                          ),
                          const SizedBox(height: 12),
                          if (!isMobile) ...[
                            _WorkspaceSwitcher(
                              currentView: _workspaceView,
                              onChanged: (view) => _openWorkspace(view),
                            ),
                            const SizedBox(height: 12),
                          ],
                          _WorkspaceBanner(view: _workspaceView),
                          const SizedBox(height: 10),
                          _AmbientNudgeToggle(
                            enabled: _ambientNudgesEnabled,
                            onChanged: (value) {
                              setState(() {
                                _ambientNudgesEnabled = value;
                              });
                              _startClinicalNudgeLoop();
                            },
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),
                    Expanded(
                      child: AnimatedSwitcher(
                        duration: const Duration(milliseconds: 220),
                        switchInCurve: Curves.easeOutCubic,
                        switchOutCurve: Curves.easeInCubic,
                        child: KeyedSubtree(
                          key: ValueKey('workspace-pane-${_workspaceView.name}'),
                          child: _buildWorkspace(context, caseRecord),
                        ),
                      ),
                    ),
                  ],
                ),
        ),
      ),
    );
  }

  Widget _buildWorkspace(BuildContext context, CaseRecord caseRecord) {
    final bodyPadding = const EdgeInsets.fromLTRB(20, 0, 20, 24);
    final isMobile = MediaQuery.sizeOf(context).width < 720;
    switch (_workspaceView) {
      case WorkspaceView.overview:
        return RefreshIndicator(
          onRefresh: _load,
          child: ListView(
            controller: _mainScrollController,
            padding: bodyPadding,
            children: [
              _TopHeader(
                patientLabel: caseRecord.patientLabel,
                caseId: caseRecord.caseId,
                reviewStatus: caseRecord.reviewStatus,
                updatedAt: caseRecord.updatedAt,
                totalCases: _cases.length,
                pendingCases: _countCasesByStatus('pending_review'),
                needsChangesCases: _countCasesByStatus('needs_changes'),
                criticalCases: _countCriticalCases(),
                onRefresh: _load,
              ),
              const SizedBox(height: 18),
              _CasePulseRow(caseRecord: caseRecord),
              const SizedBox(height: 16),
              if (!isMobile) ...[
                _QueueRail(
                  cases: _visibleCases,
                  selectedCaseId: caseRecord.caseId,
                  switchingCase: _switchingCase,
                  searchController: _searchController,
                  queueFilter: _queueFilter,
                  onSearchChanged: (_) => setState(() {}),
                  onFilterChanged: (value) => setState(() {
                    _queueFilter = value;
                  }),
                  onSelectCase: _selectCase,
                ),
                const SizedBox(height: 16),
              ],
              _Panel(
                key: _transcriptKey,
                title: 'Visit transcript',
                subtitle: 'Clinician conversation remains the source of truth for every generated field.',
                child: SelectableText(caseRecord.transcript, style: Theme.of(context).textTheme.bodyLarge),
              ),
              const SizedBox(height: 16),
              _Panel(
                title: 'Clinical summary',
                subtitle: 'Fast read before you move into note editing.',
                accent: const Color(0xFF1D6A72),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(caseRecord.note.summary, style: Theme.of(context).textTheme.bodyLarge),
                    const SizedBox(height: 16),
                    Wrap(
                      spacing: 10,
                      runSpacing: 10,
                      children: [
                        _QuickFact(label: 'Symptoms', value: '${caseRecord.note.entities.symptoms.length} captured'),
                        _QuickFact(label: 'Allergies', value: '${caseRecord.note.entities.allergies.length} documented'),
                        _QuickFact(label: 'Vitals', value: '${caseRecord.note.entities.vitals.length} documented'),
                        _QuickFact(label: 'Audit events', value: '${_auditLogs.length} recorded'),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              _Panel(
                title: 'Retrieval citations',
                subtitle: 'Hybrid retrieval evidence used for longitudinal context with visit/date/source/score traceability.',
                accent: const Color(0xFF3A5F4D),
                child: _RetrievalCitationsPanel(
                  loading: _loadingHistory,
                  error: _historyError,
                  payload: _historyDebug,
                  onRefresh: _refreshHistoryCitations,
                ),
              ),
              const SizedBox(height: 16),
              _Panel(
                title: 'Revenue leakage detector',
                subtitle: 'Find billable CPT and ICD-10 opportunities missing from summary capture.',
                accent: const Color(0xFF6E4E2E),
                child: _BillingLeakageCard(
                  running: _runningBillingAgent,
                  latestResponse: _latestBillingResponse,
                  onRun: _runBillingAgent,
                ),
              ),
              const SizedBox(height: 16),
              _Panel(
                key: _flagsKey,
                title: 'Safety and reasoning',
                subtitle: 'Use agent findings and generated differentials to decide what needs review attention.',
                accent: const Color(0xFFC96F4A),
                child: _FlagsAndDiagnosisPanel(
                  flags: caseRecord.note.reviewFlags,
                  differentialDiagnosis: caseRecord.note.differentialDiagnosis,
                ),
              ),
              const SizedBox(height: 16),
              _SideRail(
                reviewPanelKey: _reviewPanelKey,
                caseRecord: caseRecord,
                reviewerController: _reviewerController,
                feedbackController: _feedbackController,
                reviewScore: _completionScore(caseRecord),
                submitting: _submitting,
                runningAgent: _runningAgent,
                onApprove: () => _submitReview('approved'),
                onRequestChanges: () => _submitReview('needs_changes'),
                onOpenTranscript: () => _openWorkspace(WorkspaceView.overview, focusKey: _transcriptKey),
                onOpenFlags: () => _openWorkspace(WorkspaceView.overview, focusKey: _flagsKey),
                onOpenEditor: () => _openWorkspace(WorkspaceView.noteStudio),
                onOpenAudit: () => _openWorkspace(WorkspaceView.audit),
                onRunSafety: _runSafetyAgent,
                onRunQueue: _runQueueAgent,
              ),
              if (_error != null) ...[
                const SizedBox(height: 16),
                _ErrorBanner(message: _error!),
              ],
            ],
          ),
        );
      case WorkspaceView.noteStudio:
        return ListView(
          padding: bodyPadding,
          children: [
            _Panel(
              key: _noteEditorKey,
              title: 'Note studio',
              subtitle: 'Edit the clinician-facing summary and SOAP sections before sign-off.',
              accent: const Color(0xFF244553),
              child: _EditableNotePanel(
                disclaimer: caseRecord.note.disclaimer,
                summaryController: _summaryController,
                subjectiveController: _subjectiveController,
                objectiveController: _objectiveController,
                assessmentController: _assessmentController,
                planController: _planController,
                amendReasonController: _amendReasonController,
                savingDraft: _savingDraft,
                onSaveDraft: _saveDraftAmendments,
              ),
            ),
            const SizedBox(height: 16),
            _Panel(
              title: 'Multi-modal Objective Assist',
              subtitle: 'Capture wound photos or gait videos and auto-inject objective findings.',
              accent: const Color(0xFF2F5D50),
              child: _VisionAssistPanel(
                running: _analyzingVision,
                latest: _latestVisionObjective,
                onCaptureImage: () => _runVisionAgent('image'),
                onCaptureVideo: () => _runVisionAgent('video'),
              ),
            ),
            const SizedBox(height: 16),
            _Panel(
              key: _entitiesKey,
              title: 'Structured entities',
              subtitle: 'Correct extracted symptoms, history, medications, allergies, and vitals.',
              accent: const Color(0xFF1D6A72),
              child: _EntityEditorPanel(
                symptomsController: _symptomsController,
                durationController: _durationController,
                severityController: _severityController,
                historyController: _historyController,
                medicationsController: _medicationsController,
                allergiesController: _allergiesController,
                vitalsController: _vitalsController,
              ),
            ),
            const SizedBox(height: 16),
            _Panel(
              key: _reviewPanelKey,
              title: 'Clinician sign-off',
              subtitle: 'Finish the workflow here after making note edits.',
              accent: const Color(0xFFC96F4A),
              child: Column(
                children: [
                  TextField(
                    controller: _reviewerController,
                    decoration: const InputDecoration(labelText: 'Reviewer name'),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _feedbackController,
                    maxLines: 4,
                    decoration: const InputDecoration(labelText: 'Review feedback'),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      Expanded(
                        child: FilledButton(
                          onPressed: _submitting ? null : () => _submitReview('approved'),
                          child: Text(_submitting ? 'Saving...' : 'Approve note'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton(
                          onPressed: _submitting ? null : () => _submitReview('needs_changes'),
                          child: const Text('Request changes'),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        );
      case WorkspaceView.agents:
        return ListView(
          padding: bodyPadding,
          children: [
            _Panel(
              key: _agentsKey,
              title: 'Agent command center',
              subtitle: 'Run intake, safety, and queue agents, then turn their output into actions.',
              accent: const Color(0xFF12212B),
              child: _AgentPanel(
                agents: _agents,
                agentTranscriptController: _agentTranscriptController,
                runningAgent: _runningAgent,
                latestResponse: _latestAgentResponse,
                onRunSafety: _runSafetyAgent,
                onRunQueue: _runQueueAgent,
                onRunIntake: _runIntakeAgent,
                onSelectQueueCase: _handleQueueCaseTap,
                onOpenSafetyIssue: _handleSafetyIssueTap,
                onOpenCreatedCase: _handleQueueCaseTap,
              ),
            ),
            const SizedBox(height: 16),
            _Panel(
              title: 'Current chart context',
              subtitle: 'Keep the selected chart visible while you run background agents.',
              accent: const Color(0xFF1D6A72),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(caseRecord.patientLabel, style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 8),
                  Text(caseRecord.note.summary),
                ],
              ),
            ),
          ],
        );
      case WorkspaceView.audit:
        return ListView(
          padding: bodyPadding,
          children: [
            _Panel(
              key: _auditKey,
              title: 'Audit timeline',
              subtitle: 'Every note amendment, review decision, and agent-assisted handoff stays visible.',
              accent: const Color(0xFF75513B),
              child: _AuditPanel(auditLogs: _auditLogs),
            ),
          ],
        );
    }
  }
}

class _BillingLeakageCard extends StatelessWidget {
  const _BillingLeakageCard({
    required this.running,
    required this.latestResponse,
    required this.onRun,
  });

  final bool running;
  final AgentRunResponse? latestResponse;
  final Future<void> Function() onRun;

  List<Map<String, dynamic>> _listFromResult(String key) {
    final result = latestResponse?.result;
    if (result == null) return const [];
    final dynamic values = result[key];
    if (values is! List) return const [];
    return values.whereType<Map>().map((item) => Map<String, dynamic>.from(item)).toList();
  }

  @override
  Widget build(BuildContext context) {
    final cptLeaks = _listFromResult('potential_revenue_leakage');
    final icdLeaks = _listFromResult('potential_icd10_leakage');
    final matchedCpt = _listFromResult('matched_billable_codes');
    final matchedIcd = _listFromResult('matched_icd10_codes');

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'CPT matches: ${matchedCpt.length} • ICD-10 matches: ${matchedIcd.length}',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
            ),
            FilledButton.tonalIcon(
              onPressed: running ? null : onRun,
              icon: running
                  ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.attach_money_outlined),
              label: Text(running ? 'Scanning...' : 'Run detector'),
            ),
          ],
        ),
        const SizedBox(height: 12),
        if (cptLeaks.isEmpty && icdLeaks.isEmpty)
          Text(
            'No potential leakage flagged yet. Run the detector to evaluate summary vs SOAP capture.',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        for (final leak in cptLeaks) ...[
          _LeakageItem(
            code: leak['cpt_code']?.toString() ?? 'unknown',
            title: leak['procedure']?.toString() ?? 'Procedure',
            reason: leak['reason']?.toString() ?? '',
            suggestion: leak['suggestion']?.toString() ?? '',
          ),
          const SizedBox(height: 10),
        ],
        for (final leak in icdLeaks) ...[
          _LeakageItem(
            code: leak['icd10_code']?.toString() ?? 'unknown',
            title: leak['condition']?.toString() ?? 'Condition',
            reason: leak['reason']?.toString() ?? '',
            suggestion: leak['suggestion']?.toString() ?? '',
          ),
          const SizedBox(height: 10),
        ],
      ],
    );
  }
}

class _LeakageItem extends StatelessWidget {
  const _LeakageItem({
    required this.code,
    required this.title,
    required this.reason,
    required this.suggestion,
  });

  final String code;
  final String title;
  final String reason;
  final String suggestion;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF7EE),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFFE8D0B1)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('$code • $title', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 4),
          Text(reason, style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 4),
          Text(suggestion, style: Theme.of(context).textTheme.bodySmall?.copyWith(fontWeight: FontWeight.w600)),
        ],
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
        constraints: const BoxConstraints(maxWidth: 460),
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

class _CompactHeader extends StatelessWidget {
  const _CompactHeader({
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
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFF12212B), Color(0xFF244553)],
        ),
        borderRadius: BorderRadius.circular(24),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Clinic Copilot', style: Theme.of(context).textTheme.headlineSmall?.copyWith(color: Colors.white)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              _StatusChip(label: reviewStatus.replaceAll('_', ' ').toUpperCase()),
              Text(patientLabel, style: Theme.of(context).textTheme.titleLarge?.copyWith(color: Colors.white)),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'Case $caseId • Updated ${_formatDateTime(updatedAt)}',
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: Colors.white70),
          ),
        ],
      ),
    );
  }
}

class _CaseSelectorBar extends StatelessWidget {
  const _CaseSelectorBar({
    required this.cases,
    required this.selectedCaseId,
    required this.onSelectCase,
    required this.onRefresh,
    required this.switchingCase,
    this.onOpenQueue,
  });

  final List<CaseRecord> cases;
  final String selectedCaseId;
  final ValueChanged<String> onSelectCase;
  final Future<void> Function() onRefresh;
  final bool switchingCase;
  final VoidCallback? onOpenQueue;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.78),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
      ),
      child: Row(
        children: [
          if (onOpenQueue != null) ...[
            Expanded(
              child: OutlinedButton.icon(
                onPressed: onOpenQueue,
                icon: const Icon(Icons.folder_open_outlined),
                label: const Text('Open queue'),
              ),
            ),
          ] else ...[
            Expanded(
              child: DropdownButtonFormField<String>(
                initialValue: selectedCaseId,
                decoration: const InputDecoration(labelText: 'Selected chart'),
                items: cases
                    .map(
                      (caseRecord) => DropdownMenuItem<String>(
                        value: caseRecord.caseId,
                        child: Text(caseRecord.patientLabel, overflow: TextOverflow.ellipsis),
                      ),
                    )
                    .toList(),
                onChanged: switchingCase
                    ? null
                    : (value) {
                        if (value != null) {
                          onSelectCase(value);
                        }
                      },
              ),
            ),
          ],
          const SizedBox(width: 12),
          IconButton.filledTonal(
            onPressed: onRefresh,
            icon: switchingCase ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)) : const Icon(Icons.sync),
          ),
        ],
      ),
    );
  }
}

class _TopHeader extends StatelessWidget {
  const _TopHeader({
    required this.patientLabel,
    required this.caseId,
    required this.reviewStatus,
    required this.updatedAt,
    required this.totalCases,
    required this.pendingCases,
    required this.needsChangesCases,
    required this.criticalCases,
    required this.onRefresh,
  });

  final String patientLabel;
  final String caseId;
  final String reviewStatus;
  final DateTime updatedAt;
  final int totalCases;
  final int pendingCases;
  final int needsChangesCases;
  final int criticalCases;
  final Future<void> Function() onRefresh;

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
        spacing: 20,
        runSpacing: 20,
        alignment: WrapAlignment.spaceBetween,
        crossAxisAlignment: WrapCrossAlignment.start,
        children: [
          SizedBox(
            width: 520,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Clinic Copilot', style: theme.textTheme.displaySmall?.copyWith(color: Colors.white)),
                const SizedBox(height: 10),
                Text(
                  'Production review workspace for chart validation, agent-assisted triage, note correction, and auditable clinician sign-off.',
                  style: theme.textTheme.bodyLarge?.copyWith(color: Colors.white.withValues(alpha: 0.86)),
                ),
                const SizedBox(height: 18),
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    _StatusChip(label: reviewStatus.replaceAll('_', ' ').toUpperCase()),
                    Text(patientLabel, style: theme.textTheme.titleLarge?.copyWith(color: Colors.white)),
                  ],
                ),
                const SizedBox(height: 6),
                Text('Case $caseId • Updated ${_formatDateTime(updatedAt)}',
                    style: theme.textTheme.bodyMedium?.copyWith(color: Colors.white70)),
              ],
            ),
          ),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 520),
            child: Column(
              children: [
                Row(
                  children: [
                    Expanded(child: _MetricTile(label: 'Queue', value: '$totalCases', tone: const Color(0xFFFFE7C6))),
                    const SizedBox(width: 12),
                    Expanded(child: _MetricTile(label: 'Pending', value: '$pendingCases', tone: const Color(0xFFD6F5EB))),
                  ],
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: _MetricTile(label: 'Needs changes', value: '$needsChangesCases', tone: const Color(0xFFFFE1D6)),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: _MetricTile(label: 'Critical flags', value: '$criticalCases', tone: const Color(0xFFFFD7D1)),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                Align(
                  alignment: Alignment.centerRight,
                  child: OutlinedButton.icon(
                    onPressed: onRefresh,
                    icon: const Icon(Icons.sync),
                    label: const Text('Refresh data'),
                    style: OutlinedButton.styleFrom(foregroundColor: Colors.white, side: const BorderSide(color: Colors.white54)),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _WorkspaceSwitcher extends StatelessWidget {
  const _WorkspaceSwitcher({required this.currentView, required this.onChanged});

  final WorkspaceView currentView;
  final ValueChanged<WorkspaceView> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(8),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.66),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
      ),
      child: Wrap(
        spacing: 10,
        runSpacing: 10,
        children: [
          for (final option in WorkspaceView.values)
            _WorkspaceButton(
              buttonKey: ValueKey('workspace-${option.name}'),
              label: _workspaceLabel(option),
              icon: _workspaceIcon(option),
              selected: currentView == option,
              onTap: () => onChanged(option),
            ),
        ],
      ),
    );
  }

  String _workspaceLabel(WorkspaceView view) {
    switch (view) {
      case WorkspaceView.overview:
        return 'Overview';
      case WorkspaceView.noteStudio:
        return 'Note Studio';
      case WorkspaceView.agents:
        return 'Agents';
      case WorkspaceView.audit:
        return 'Audit';
    }
  }

  IconData _workspaceIcon(WorkspaceView view) {
    switch (view) {
      case WorkspaceView.overview:
        return Icons.dashboard_outlined;
      case WorkspaceView.noteStudio:
        return Icons.edit_note;
      case WorkspaceView.agents:
        return Icons.smart_toy_outlined;
      case WorkspaceView.audit:
        return Icons.timeline;
    }
  }
}

class _WorkspaceButton extends StatelessWidget {
  const _WorkspaceButton({
    this.buttonKey,
    required this.label,
    required this.icon,
    required this.selected,
    required this.onTap,
  });

  final Key? buttonKey;
  final String label;
  final IconData icon;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final style = selected
        ? FilledButton.styleFrom(
            backgroundColor: const Color(0xFF12212B),
            foregroundColor: Colors.white,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
          )
        : OutlinedButton.styleFrom(
            foregroundColor: const Color(0xFF12212B),
            side: BorderSide(color: Colors.black.withValues(alpha: 0.08)),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
          );

    if (selected) {
      return FilledButton.icon(
        key: buttonKey,
        onPressed: onTap,
        style: style,
        icon: Icon(icon, size: 18),
        label: Text(label),
      );
    }

    return OutlinedButton.icon(
      key: buttonKey,
      onPressed: onTap,
      style: style,
      icon: Icon(icon, size: 18),
      label: Text(label),
    );
  }
}

class _MobileBottomNav extends StatelessWidget {
  const _MobileBottomNav({
    required this.currentView,
    required this.onChanged,
  });

  final WorkspaceView currentView;
  final ValueChanged<WorkspaceView> onChanged;

  @override
  Widget build(BuildContext context) {
    return NavigationBar(
      selectedIndex: WorkspaceView.values.indexOf(currentView),
      onDestinationSelected: (index) => onChanged(WorkspaceView.values[index]),
      destinations: const [
        NavigationDestination(icon: Icon(Icons.dashboard_outlined), label: 'Overview'),
        NavigationDestination(icon: Icon(Icons.edit_note), label: 'Note'),
        NavigationDestination(icon: Icon(Icons.smart_toy_outlined), label: 'Agents'),
        NavigationDestination(icon: Icon(Icons.timeline), label: 'Audit'),
      ],
    );
  }
}

class _WorkspaceBanner extends StatelessWidget {
  const _WorkspaceBanner({required this.view});

  final WorkspaceView view;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.78),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
      ),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: const Color(0xFF12212B).withValues(alpha: 0.92),
              borderRadius: BorderRadius.circular(14),
            ),
            child: Icon(_iconFor(view), color: Colors.white),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_titleFor(view), style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 4),
                Text(_subtitleFor(view), style: Theme.of(context).textTheme.bodyMedium),
              ],
            ),
          ),
        ],
      ),
    );
  }

  String _titleFor(WorkspaceView view) {
    switch (view) {
      case WorkspaceView.overview:
        return 'Overview workspace';
      case WorkspaceView.noteStudio:
        return 'Note studio workspace';
      case WorkspaceView.agents:
        return 'Agent workspace';
      case WorkspaceView.audit:
        return 'Audit workspace';
    }
  }

  String _subtitleFor(WorkspaceView view) {
    switch (view) {
      case WorkspaceView.overview:
        return 'Transcript, summary, and risk context for the current chart.';
      case WorkspaceView.noteStudio:
        return 'Edit SOAP sections and structured entities before sign-off.';
      case WorkspaceView.agents:
        return 'Run intake, safety, and triage agents and act on the results.';
      case WorkspaceView.audit:
        return 'Inspect the clinician and system activity timeline.';
    }
  }

  IconData _iconFor(WorkspaceView view) {
    switch (view) {
      case WorkspaceView.overview:
        return Icons.dashboard_outlined;
      case WorkspaceView.noteStudio:
        return Icons.edit_note;
      case WorkspaceView.agents:
        return Icons.smart_toy_outlined;
      case WorkspaceView.audit:
        return Icons.timeline;
    }
  }
}

class _AmbientNudgeToggle extends StatelessWidget {
  const _AmbientNudgeToggle({required this.enabled, required this.onChanged});

  final bool enabled;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.78),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
      ),
      child: Row(
        children: [
          const Icon(Icons.notifications_active_outlined, size: 18),
          const SizedBox(width: 10),
          const Expanded(
            child: Text(
              'Ambient clinical nudges (session)',
              style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
            ),
          ),
          Switch.adaptive(value: enabled, onChanged: onChanged),
        ],
      ),
    );
  }
}

class _VisionAssistPanel extends StatelessWidget {
  const _VisionAssistPanel({
    required this.running,
    required this.latest,
    required this.onCaptureImage,
    required this.onCaptureVideo,
  });

  final bool running;
  final VisionObjectiveResponse? latest;
  final VoidCallback onCaptureImage;
  final VoidCallback onCaptureVideo;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            FilledButton.tonalIcon(
              onPressed: running ? null : onCaptureImage,
              icon: const Icon(Icons.camera_alt_outlined),
              label: Text(running ? 'Analyzing...' : 'Capture wound photo'),
            ),
            FilledButton.tonalIcon(
              onPressed: running ? null : onCaptureVideo,
              icon: const Icon(Icons.videocam_outlined),
              label: const Text('Capture gait video'),
            ),
          ],
        ),
        const SizedBox(height: 12),
        if (latest != null)
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: const Color(0xFFF4FAF3),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: const Color(0xFFCFE2CD)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Objective injected (${latest!.mediaType}) • ${latest!.model} • ${latest!.confidence}',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
                const SizedBox(height: 6),
                Text(latest!.objectiveText, style: Theme.of(context).textTheme.bodyMedium),
              ],
            ),
          ),
      ],
    );
  }
}

class _QueueRail extends StatelessWidget {
  const _QueueRail({
    required this.cases,
    required this.selectedCaseId,
    required this.switchingCase,
    required this.searchController,
    required this.queueFilter,
    required this.onSearchChanged,
    required this.onFilterChanged,
    required this.onSelectCase,
  });

  final List<CaseRecord> cases;
  final String selectedCaseId;
  final bool switchingCase;
  final TextEditingController searchController;
  final String queueFilter;
  final ValueChanged<String> onSearchChanged;
  final ValueChanged<String> onFilterChanged;
  final ValueChanged<String> onSelectCase;

  @override
  Widget build(BuildContext context) {
    return _Panel(
      title: 'Live queue',
      subtitle: switchingCase ? 'Opening chart...' : 'Filter by status, search patients, and jump between cases.',
      accent: const Color(0xFF244553),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          TextField(
            controller: searchController,
            onChanged: onSearchChanged,
            decoration: const InputDecoration(
              prefixIcon: Icon(Icons.search),
              hintText: 'Search patient, case, or summary',
            ),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final filter in const ['all', 'pending_review', 'needs_changes', 'approved'])
                ChoiceChip(
                  label: Text(filter == 'all' ? 'All' : filter.replaceAll('_', ' ')),
                  selected: queueFilter == filter,
                  onSelected: (_) => onFilterChanged(filter),
                ),
            ],
          ),
          const SizedBox(height: 16),
          if (cases.isEmpty)
            const _EmptyState(message: 'No cases match the current filters.')
          else
            Column(
              children: cases.map((caseRecord) {
                final selected = caseRecord.caseId == selectedCaseId;
                final hasWarning = caseRecord.note.reviewFlags.any((flag) => flag.severity != 'info');
                return Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: InkWell(
                    borderRadius: BorderRadius.circular(20),
                    onTap: switchingCase ? null : () => onSelectCase(caseRecord.caseId),
                    child: AnimatedContainer(
                      duration: const Duration(milliseconds: 180),
                      padding: const EdgeInsets.all(16),
                      decoration: BoxDecoration(
                        color: selected ? const Color(0xFF12212B) : Colors.white.withValues(alpha: 0.82),
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(
                          color: hasWarning
                              ? const Color(0xFFC96F4A).withValues(alpha: selected ? 0.32 : 0.62)
                              : Colors.black.withValues(alpha: 0.06),
                        ),
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
                          const SizedBox(height: 10),
                          Row(
                            children: [
                              Expanded(
                                child: Text(
                                  _formatDateTime(caseRecord.updatedAt),
                                  style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                                        color: selected ? Colors.white54 : Colors.black54,
                                      ),
                                ),
                              ),
                              if (hasWarning)
                                Icon(
                                  Icons.priority_high_rounded,
                                  color: selected ? Colors.white70 : const Color(0xFFC96F4A),
                                  size: 18,
                                ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                );
              }).toList(),
            ),
        ],
      ),
    );
  }
}

class _SideRail extends StatelessWidget {
  const _SideRail({
    required this.reviewPanelKey,
    required this.caseRecord,
    required this.reviewerController,
    required this.feedbackController,
    required this.reviewScore,
    required this.submitting,
    required this.runningAgent,
    required this.onApprove,
    required this.onRequestChanges,
    required this.onOpenTranscript,
    required this.onOpenFlags,
    required this.onOpenEditor,
    required this.onOpenAudit,
    required this.onRunSafety,
    required this.onRunQueue,
  });

  final GlobalKey reviewPanelKey;
  final CaseRecord caseRecord;
  final TextEditingController reviewerController;
  final TextEditingController feedbackController;
  final int reviewScore;
  final bool submitting;
  final bool runningAgent;
  final VoidCallback onApprove;
  final VoidCallback onRequestChanges;
  final VoidCallback onOpenTranscript;
  final VoidCallback onOpenFlags;
  final VoidCallback onOpenEditor;
  final VoidCallback onOpenAudit;
  final VoidCallback onRunSafety;
  final VoidCallback onRunQueue;

  @override
  Widget build(BuildContext context) {
    final missingVitals = caseRecord.note.entities.vitals.isEmpty;
    final missingAllergies = caseRecord.note.entities.allergies.isEmpty;
    final warningCount = caseRecord.note.reviewFlags.length;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _Panel(
          key: reviewPanelKey,
          title: 'Review controls',
          subtitle: 'Capture clinician feedback and update the chart status.',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              TextField(
                controller: reviewerController,
                decoration: const InputDecoration(labelText: 'Reviewer name'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: feedbackController,
                maxLines: 4,
                decoration: const InputDecoration(labelText: 'Feedback for the team'),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  Expanded(
                    child: FilledButton(
                      onPressed: submitting ? null : onApprove,
                      child: Text(submitting ? 'Saving...' : 'Approve note'),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      onPressed: submitting ? null : onRequestChanges,
                      child: const Text('Request changes'),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        _Panel(
          title: 'Readiness score',
          subtitle: 'A quick heuristic so the reviewer knows whether this chart is close to sign-off.',
          accent: const Color(0xFF1D6A72),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(999),
                      child: LinearProgressIndicator(
                        value: reviewScore / 100,
                        minHeight: 12,
                        color: const Color(0xFF1D6A72),
                        backgroundColor: const Color(0xFFE7ECE5),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Text('$reviewScore%', style: Theme.of(context).textTheme.titleLarge),
                ],
              ),
              const SizedBox(height: 16),
              _ChecklistRow(
                title: 'Review flags',
                trailing: '$warningCount open',
                done: warningCount == 0,
              ),
              _ChecklistRow(
                title: 'Vitals documented',
                trailing: missingVitals ? 'Missing' : 'Present',
                done: !missingVitals,
              ),
              _ChecklistRow(
                title: 'Allergy status documented',
                trailing: missingAllergies ? 'Missing' : 'Present',
                done: !missingAllergies,
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
        _Panel(
          title: 'Quick actions',
          subtitle: 'Production workflows should route reviewers, not just display data.',
          accent: const Color(0xFFC96F4A),
          child: Column(
            children: [
              _ActionButton(label: 'Open transcript', icon: Icons.forum_outlined, onTap: onOpenTranscript),
              _ActionButton(label: 'Jump to flags', icon: Icons.warning_amber_rounded, onTap: onOpenFlags),
              _ActionButton(label: 'Edit note', icon: Icons.edit_note, onTap: onOpenEditor),
              _ActionButton(label: 'View audit trail', icon: Icons.timeline, onTap: onOpenAudit),
              _ActionButton(
                label: runningAgent ? 'Running safety agent...' : 'Run safety reviewer',
                icon: Icons.verified_user_outlined,
                onTap: runningAgent ? null : onRunSafety,
              ),
              _ActionButton(
                label: runningAgent ? 'Running queue agent...' : 'Run queue orchestrator',
                icon: Icons.hub_outlined,
                onTap: runningAgent ? null : onRunQueue,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _CasePulseRow extends StatelessWidget {
  const _CasePulseRow({required this.caseRecord});

  final CaseRecord caseRecord;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 12,
      runSpacing: 12,
      children: [
        _PulseCard(
          label: 'Summary status',
          value: caseRecord.reviewStatus.replaceAll('_', ' '),
          accent: const Color(0xFF244553),
        ),
        _PulseCard(
          label: 'Flags',
          value: '${caseRecord.note.reviewFlags.length}',
          accent: const Color(0xFFC96F4A),
        ),
        _PulseCard(
          label: 'Differentials',
          value: '${caseRecord.note.differentialDiagnosis.length}',
          accent: const Color(0xFF1D6A72),
        ),
        _PulseCard(
          label: 'Latest note update',
          value: _formatDateTime(caseRecord.updatedAt),
          accent: const Color(0xFF75513B),
        ),
      ],
    );
  }
}

class _FlagsAndDiagnosisPanel extends StatelessWidget {
  const _FlagsAndDiagnosisPanel({
    required this.flags,
    required this.differentialDiagnosis,
  });

  final List<ReviewFlag> flags;
  final List<DifferentialDiagnosisItem> differentialDiagnosis;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (flags.isEmpty)
          const _EmptyState(message: 'No review flags were generated for this note.')
        else
          ...flags.map(
            (flag) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: _SeverityCard(
                issue: flag.issue,
                severity: flag.severity,
                recommendation: flag.recommendation,
              ),
            ),
          ),
        const SizedBox(height: 8),
        Text('Differential diagnosis', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 10),
        if (differentialDiagnosis.isEmpty)
          const _EmptyState(message: 'Differential diagnosis was not included for this case.')
        else
          ...differentialDiagnosis.map(
            (item) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.75),
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(child: Text(item.condition, style: Theme.of(context).textTheme.titleMedium)),
                        _MiniBadge(label: item.confidence),
                      ],
                    ),
                    const SizedBox(height: 6),
                    Text(item.rationale),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }
}

class _RetrievalCitationsPanel extends StatelessWidget {
  const _RetrievalCitationsPanel({
    required this.loading,
    required this.error,
    required this.payload,
    required this.onRefresh,
  });

  final bool loading;
  final String? error;
  final PatientHistoryDebugResponse? payload;
  final Future<void> Function() onRefresh;

  String _displayDate(String raw) {
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) return raw;
    return _formatDateTime(parsed);
  }

  @override
  Widget build(BuildContext context) {
    final matches = payload?.retrieved ?? const <RetrievedHistoryItem>[];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                payload == null
                    ? 'No retrieval context loaded yet.'
                    : 'Query: ${payload!.currentComplaint}',
                style: Theme.of(context).textTheme.bodyMedium,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            FilledButton.tonalIcon(
              onPressed: loading ? null : onRefresh,
              icon: loading
                  ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.refresh),
              label: Text(loading ? 'Loading...' : 'Refresh citations'),
            ),
          ],
        ),
        const SizedBox(height: 12),
        if (error != null)
          _ErrorBanner(message: error!)
        else if (loading && payload == null)
          const _EmptyState(message: 'Fetching retrieval evidence...')
        else if (matches.isEmpty)
          const _EmptyState(message: 'No citations were returned for this complaint.')
        else
          Column(
            children: matches
                .map(
                  (item) => Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(12),
                      decoration: BoxDecoration(
                        color: const Color(0xFFF4FAF3),
                        borderRadius: BorderRadius.circular(14),
                        border: Border.all(color: const Color(0xFFCFE2CD)),
                      ),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Wrap(
                            spacing: 8,
                            runSpacing: 8,
                            children: [
                              _MiniBadge(label: 'Visit ${item.visitId}'),
                              _MiniBadge(label: 'Date ${_displayDate(item.date)}'),
                              _MiniBadge(label: 'Source ${item.source}'),
                              _MiniBadge(label: 'Score ${item.score.toStringAsFixed(3)}'),
                            ],
                          ),
                          const SizedBox(height: 8),
                          Text(item.textChunk, style: Theme.of(context).textTheme.bodyMedium),
                        ],
                      ),
                    ),
                  ),
                )
                .toList(),
          ),
      ],
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
  final TextEditingController amendReasonController;
  final bool savingDraft;
  final VoidCallback onSaveDraft;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _BannerNote(text: disclaimer),
        const SizedBox(height: 14),
        TextField(
          controller: summaryController,
          maxLines: 4,
          decoration: const InputDecoration(labelText: 'Summary'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: subjectiveController,
          maxLines: 4,
          decoration: const InputDecoration(labelText: 'SOAP: Subjective'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: objectiveController,
          maxLines: 4,
          decoration: const InputDecoration(labelText: 'SOAP: Objective'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: assessmentController,
          maxLines: 4,
          decoration: const InputDecoration(labelText: 'SOAP: Assessment'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: planController,
          maxLines: 4,
          decoration: const InputDecoration(labelText: 'SOAP: Plan'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: amendReasonController,
          maxLines: 3,
          decoration: const InputDecoration(labelText: 'Amendment reason'),
        ),
        const SizedBox(height: 16),
        FilledButton.icon(
          onPressed: savingDraft ? null : onSaveDraft,
          icon: const Icon(Icons.save_outlined),
          label: Text(savingDraft ? 'Saving...' : 'Save amendments'),
        ),
      ],
    );
  }
}

class _EntityEditorPanel extends StatelessWidget {
  const _EntityEditorPanel({
    required this.symptomsController,
    required this.durationController,
    required this.severityController,
    required this.historyController,
    required this.medicationsController,
    required this.allergiesController,
    required this.vitalsController,
  });

  final TextEditingController symptomsController;
  final TextEditingController durationController;
  final TextEditingController severityController;
  final TextEditingController historyController;
  final TextEditingController medicationsController;
  final TextEditingController allergiesController;
  final TextEditingController vitalsController;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _EntityField(label: 'Symptoms', controller: symptomsController),
        _EntityField(label: 'Duration', controller: durationController),
        _EntityField(label: 'Severity', controller: severityController),
        _EntityField(label: 'Medical history', controller: historyController),
        _EntityField(label: 'Medications', controller: medicationsController),
        _EntityField(label: 'Allergies', controller: allergiesController),
        _EntityField(label: 'Vitals', controller: vitalsController),
      ],
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
    required this.onSelectQueueCase,
    required this.onOpenSafetyIssue,
    required this.onOpenCreatedCase,
  });

  final List<AgentSummary> agents;
  final TextEditingController agentTranscriptController;
  final bool runningAgent;
  final AgentRunResponse? latestResponse;
  final VoidCallback onRunSafety;
  final VoidCallback onRunQueue;
  final VoidCallback onRunIntake;
  final ValueChanged<String> onSelectQueueCase;
  final ValueChanged<SafetyIssue> onOpenSafetyIssue;
  final ValueChanged<String> onOpenCreatedCase;

  @override
  Widget build(BuildContext context) {
    final response = latestResponse;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: agents
              .map(
                (agent) => Chip(
                  avatar: const Icon(Icons.smart_toy_outlined, size: 18),
                  label: Text('${agent.name} • ${agent.version}'),
                ),
              )
              .toList(),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: agentTranscriptController,
          maxLines: 5,
          decoration: const InputDecoration(
            labelText: 'New intake transcript',
            hintText: 'Paste a new doctor-patient conversation here',
          ),
        ),
        const SizedBox(height: 14),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            FilledButton.icon(
              onPressed: runningAgent ? null : onRunIntake,
              icon: const Icon(Icons.play_arrow_rounded),
              label: Text(runningAgent ? 'Running...' : 'Run intake agent'),
            ),
            OutlinedButton.icon(
              onPressed: runningAgent ? null : onRunSafety,
              icon: const Icon(Icons.health_and_safety_outlined),
              label: const Text('Run safety reviewer'),
            ),
            OutlinedButton.icon(
              onPressed: runningAgent ? null : onRunQueue,
              icon: const Icon(Icons.queue_play_next),
              label: const Text('Run queue orchestrator'),
            ),
          ],
        ),
        const SizedBox(height: 18),
        if (response == null)
          const _EmptyState(message: 'Run an agent to populate this command center.')
        else ...[
          Text('Latest result: ${response.agentName}', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
          if (response.agentId == 'clinical_intake_agent')
            _IntakeAgentResult(
              response: response,
              onOpenCase: onOpenCreatedCase,
            )
          else if (response.agentId == 'note_safety_reviewer')
            _SafetyAgentResult(
              response: response,
              onOpenSafetyIssue: onOpenSafetyIssue,
            )
          else if (response.agentId == 'review_queue_orchestrator')
            _QueueAgentResult(
              response: response,
              onSelectQueueCase: onSelectQueueCase,
            )
          else
            Text(response.result.toString()),
        ],
      ],
    );
  }
}

class _IntakeAgentResult extends StatelessWidget {
  const _IntakeAgentResult({required this.response, required this.onOpenCase});

  final AgentRunResponse response;
  final ValueChanged<String> onOpenCase;

  @override
  Widget build(BuildContext context) {
    final result = response.result;
    final caseId = result['case_id'] as String? ?? '';
    final entities = ClinicalEntities.fromJson(Map<String, dynamic>.from(result['entities'] as Map));
    final reviewFlags = (result['review_flags'] as List<dynamic>)
        .map((item) => ReviewFlag.fromJson(Map<String, dynamic>.from(item as Map)))
        .toList();

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.78),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(result['patient_label'] as String? ?? 'Generated case')),
              FilledButton.tonal(
                onPressed: caseId.isEmpty ? null : () => onOpenCase(caseId),
                child: const Text('Open case'),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(result['summary'] as String? ?? ''),
          const SizedBox(height: 14),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _QuickFact(label: 'Symptoms', value: '${entities.symptoms.length}'),
              _QuickFact(label: 'Allergies', value: '${entities.allergies.length}'),
              _QuickFact(label: 'Flags', value: '${reviewFlags.length}'),
              _QuickFact(label: 'Status', value: result['review_status'] as String? ?? 'unknown'),
            ],
          ),
        ],
      ),
    );
  }
}

class _SafetyAgentResult extends StatelessWidget {
  const _SafetyAgentResult({required this.response, required this.onOpenSafetyIssue});

  final AgentRunResponse response;
  final ValueChanged<SafetyIssue> onOpenSafetyIssue;

  @override
  Widget build(BuildContext context) {
    final result = response.result;
    final issues = (result['issues'] as List<dynamic>)
        .map((item) => SafetyIssue.fromJson(Map<String, dynamic>.from(item as Map)))
        .toList();

    return Column(
      children: issues
          .map(
            (issue) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: InkWell(
                borderRadius: BorderRadius.circular(18),
                onTap: () => onOpenSafetyIssue(issue),
                child: _SeverityCard(
                  issue: issue.issue,
                  severity: issue.severity,
                  recommendation: '${issue.recommendation} Tap to route to the relevant review section.',
                ),
              ),
            ),
          )
          .toList(),
    );
  }
}

class _QueueAgentResult extends StatelessWidget {
  const _QueueAgentResult({required this.response, required this.onSelectQueueCase});

  final AgentRunResponse response;
  final ValueChanged<String> onSelectQueueCase;

  @override
  Widget build(BuildContext context) {
    final result = response.result;
    final rankedCases = (result['ranked_cases'] as List<dynamic>)
        .map((item) => QueueRankedCase.fromJson(Map<String, dynamic>.from(item as Map)))
        .toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Queue size: ${result['queue_size']}'),
        const SizedBox(height: 12),
        ...rankedCases.map(
          (item) => Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: InkWell(
              borderRadius: BorderRadius.circular(18),
              onTap: () => onSelectQueueCase(item.caseId),
              child: Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.8),
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(child: Text(item.patientLabel, style: Theme.of(context).textTheme.titleMedium)),
                        _MiniBadge(label: item.reviewStatus),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(item.topIssue),
                    const SizedBox(height: 6),
                    Text(item.recommendedAction, style: Theme.of(context).textTheme.bodyMedium),
                    const SizedBox(height: 10),
                    Text('Tap to open case', style: Theme.of(context).textTheme.bodyMedium),
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _AuditPanel extends StatelessWidget {
  const _AuditPanel({required this.auditLogs});

  final List<AuditLogEntry> auditLogs;

  @override
  Widget build(BuildContext context) {
    if (auditLogs.isEmpty) {
      return const _EmptyState(message: 'No audit events recorded yet.');
    }

    return Column(
      children: auditLogs
          .map(
            (entry) => Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.78),
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      width: 42,
                      height: 42,
                      decoration: const BoxDecoration(color: Color(0xFF12212B), shape: BoxShape.circle),
                      child: const Icon(Icons.history, color: Colors.white, size: 20),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Expanded(child: Text(entry.eventType.replaceAll('_', ' '), style: Theme.of(context).textTheme.titleMedium)),
                              Text(_formatDateTime(entry.createdAt), style: Theme.of(context).textTheme.bodyMedium),
                            ],
                          ),
                          const SizedBox(height: 6),
                          Text('Actor: ${entry.actor}'),
                          const SizedBox(height: 4),
                          Text(entry.details),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          )
          .toList(),
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({
    super.key,
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
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 56,
              height: 4,
              decoration: BoxDecoration(color: accent, borderRadius: BorderRadius.circular(999)),
            ),
            const SizedBox(height: 16),
            Text(title, style: theme.textTheme.headlineSmall),
            const SizedBox(height: 6),
            Text(subtitle, style: theme.textTheme.bodyMedium),
            const SizedBox(height: 18),
            child,
          ],
        ),
      ),
    );
  }
}

class _MetricTile extends StatelessWidget {
  const _MetricTile({required this.label, required this.value, required this.tone});

  final String label;
  final String value;
  final Color tone;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: tone.withValues(alpha: 0.18),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: Colors.white24),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: Colors.white70)),
          const SizedBox(height: 8),
          Text(value, style: Theme.of(context).textTheme.headlineSmall?.copyWith(color: Colors.white)),
        ],
      ),
    );
  }
}

class _PulseCard extends StatelessWidget {
  const _PulseCard({required this.label, required this.value, required this.accent});

  final String label;
  final String value;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 210,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.74),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: accent.withValues(alpha: 0.22)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: Theme.of(context).textTheme.bodyMedium),
          const SizedBox(height: 8),
          Text(value, style: Theme.of(context).textTheme.titleLarge?.copyWith(color: accent)),
        ],
      ),
    );
  }
}

class _QuickFact extends StatelessWidget {
  const _QuickFact({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.78),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: Colors.black.withValues(alpha: 0.06)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: Theme.of(context).textTheme.bodyMedium),
          const SizedBox(height: 4),
          Text(value, style: Theme.of(context).textTheme.titleMedium),
        ],
      ),
    );
  }
}

class _SeverityCard extends StatelessWidget {
  const _SeverityCard({
    required this.issue,
    required this.severity,
    required this.recommendation,
  });

  final String issue;
  final String severity;
  final String recommendation;

  @override
  Widget build(BuildContext context) {
    Color tone;
    switch (severity) {
      case 'critical':
        tone = const Color(0xFFB9473E);
        break;
      case 'warning':
        tone = const Color(0xFFC96F4A);
        break;
      default:
        tone = const Color(0xFF244553);
        break;
    }
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: tone.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: tone.withValues(alpha: 0.28)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(issue, style: Theme.of(context).textTheme.titleMedium?.copyWith(color: tone))),
              _MiniBadge(label: severity),
            ],
          ),
          const SizedBox(height: 8),
          Text(recommendation),
        ],
      ),
    );
  }
}

class _EntityField extends StatelessWidget {
  const _EntityField({required this.label, required this.controller});

  final String label;
  final TextEditingController controller;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: controller,
        maxLines: 3,
        decoration: InputDecoration(labelText: '$label (one per line)'),
      ),
    );
  }
}

class _ChecklistRow extends StatelessWidget {
  const _ChecklistRow({required this.title, required this.trailing, required this.done});

  final String title;
  final String trailing;
  final bool done;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: Row(
        children: [
          Icon(done ? Icons.check_circle : Icons.radio_button_unchecked, color: done ? const Color(0xFF1D6A72) : const Color(0xFFC96F4A)),
          const SizedBox(width: 10),
          Expanded(child: Text(title)),
          Text(trailing, style: Theme.of(context).textTheme.bodyMedium),
        ],
      ),
    );
  }
}

class _ActionButton extends StatelessWidget {
  const _ActionButton({
    required this.label,
    required this.icon,
    required this.onTap,
  });

  final String label;
  final IconData icon;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: SizedBox(
        width: double.infinity,
        child: OutlinedButton.icon(
          onPressed: onTap,
          icon: Icon(icon),
          label: Align(
            alignment: Alignment.centerLeft,
            child: Text(label),
          ),
        ),
      ),
    );
  }
}

class _ErrorBanner extends StatelessWidget {
  const _ErrorBanner({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFFFFE0D9),
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: const Color(0xFFB9473E).withValues(alpha: 0.3)),
      ),
      child: Row(
        children: [
          const Icon(Icons.error_outline, color: Color(0xFFB9473E)),
          const SizedBox(width: 10),
          Expanded(child: Text(message)),
        ],
      ),
    );
  }
}

class _BannerNote extends StatelessWidget {
  const _BannerNote({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFFEAF5F3),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.info_outline, color: Color(0xFF1D6A72)),
          const SizedBox(width: 10),
          Expanded(child: Text(text)),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.72),
        borderRadius: BorderRadius.circular(18),
      ),
      child: Text(message),
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
        border: Border.all(color: Colors.white24),
      ),
      child: Text(
        label,
        style: Theme.of(context).textTheme.labelLarge?.copyWith(color: Colors.white),
      ),
    );
  }
}

class _MiniBadge extends StatelessWidget {
  const _MiniBadge({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.06),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label.replaceAll('_', ' '),
        style: Theme.of(context).textTheme.bodyMedium,
      ),
    );
  }
}

String _formatDateTime(DateTime value) {
  final local = value.toLocal();
  final month = local.month.toString().padLeft(2, '0');
  final day = local.day.toString().padLeft(2, '0');
  final hour = local.hour.toString().padLeft(2, '0');
  final minute = local.minute.toString().padLeft(2, '0');
  return '${local.year}-$month-$day $hour:$minute';
}
