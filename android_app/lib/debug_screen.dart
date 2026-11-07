import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'mcp_runtime.dart';
import 'xiaozhi_controller.dart';

class DebugScreen extends StatefulWidget {
  const DebugScreen({super.key, required this.controller});

  final XiaozhiController controller;

  @override
  State<DebugScreen> createState() => _DebugScreenState();
}

class _DebugScreenState extends State<DebugScreen> {
  double _dragExtent = 0;

  /// Refreshes the live values (audio level, lip-sync vowel) four
  /// times a second; the screen otherwise only rebuilds on navigation,
  /// so a snapshot would go stale mid-utterance.
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _refreshTimer = Timer.periodic(
      const Duration(milliseconds: 250),
      (_) {
        if (mounted) setState(() {});
      },
    );
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  /// Latest lip-sync frame as one line: which vowel the analyzer
  /// thinks it hears, how wide the mouth is open, and the form shift.
  /// Compare it with the spoken word while testing: if the vowel shown
  /// is wrong (e.g. 'ou' while the voice says 'a'), the generic
  /// templates misread this TTS voice and need calibration.
  /// Tools actually served to the server in the most recent
  /// `tools/list` response. The avatar_gesture line is the diagnostic
  /// for the "gesture not available" reports: if it says NO here, the
  /// server never received the tool (forwarding/caching gap); if YES
  /// but the LLM still says unavailable, the tool was listed but the
  /// LLM never called it. The full name list shows exactly what the
  /// server got, so a missing or filtered tool is visible at a glance.
  List<String> _toolsListText() {
    final names = McpRuntime.lastListedToolNames;
    if (names.isEmpty) {
      return const ['(no tools/list served yet -- connect first)'];
    }
    final at = McpRuntime.lastToolsListAt;
    final time = at == null
        ? ''
        : ' (${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}:${at.second.toString().padLeft(2, '0')})';
    return [
      'avatar_gesture listed: ${names.contains('avatar_gesture') ? 'YES' : 'NO'}',
      'Total tools: ${names.length}$time',
      'Tools: ${names.join(', ')}',
    ];
  }
  /// Wake-word listener state. The diagnostic for "wake word susah"
  /// in idle: if the detector is not running or the mic is not
  /// active, the spotter hears nothing and no keyword can fire. If
  /// both are YES but hits still do nothing, compare 'Mic RMS' with
  /// 'Ambang dekat': a hit is rejected by the external-sound filter
  /// when RMS is below the threshold (user speaking from a distance).
  /// Lower the threshold in Settings, or move closer to the phone.
  List<String> _wakeWordText() {
    final c = widget.controller;
    return [
      'Detector running: ${c.wakeWordDetector.isRunning ? 'YES' : 'NO'}',
      'Listening flag: ${c.wakeWordListening ? 'YES' : 'NO'}',
      'Mic active: ${c.wakeWordMicActive ? 'YES' : 'NO'}',
      'Mic RMS (last 1s): ${c.micRecentRms.toStringAsFixed(4)}',
      'Ambang dekat: ${c.closeSpeechThreshold.toStringAsFixed(3)} (filter ${c.externalSoundFilter ? 'ON' : 'OFF'})',
      'Error: ${c.wakeWordError.isEmpty ? '(none)' : c.wakeWordError}',
    ];
  }
  String _lipSyncText() {
    final frame = widget.controller.lastLipSyncFrame;
    if (frame == null) return '(no frame yet)';
    return '${frame.dominant} open=${frame.openness.toStringAsFixed(2)} '
        'form=${frame.formDelta.toStringAsFixed(2)} '
        'sil=${frame.silence.toStringAsFixed(2)}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.paper,
      body:GestureDetector(
        // Allow text selection by using HitTestBehavior.deferToChild
        onHorizontalDragUpdate: (details) {
          setState(() {
            _dragExtent += details.delta.dx;
          });
        },
        onHorizontalDragEnd: (details) {
          // Right swipe - go back to main screen
          if (_dragExtent > 100) {
            Navigator.of(context).pop();
          }
          _dragExtent = 0;
        },
        child: SafeArea(
          child: Column(
            children: [
              _buildHeader(context),
              Expanded(child: _buildDebugContent()),
              _buildFooter(context),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 14, 14, 10),
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: AppColors.green,
                borderRadius: BorderRadius.circular(15),
              ),
              child: const Icon(Icons.bug_report_rounded, color: Colors.white, size: 25),
            ),
            const SizedBox(width: 12),
            const Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'DEBUG SCREEN',
                    style: TextStyle(
                      color: AppColors.ink,
                      fontSize: 20,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  SizedBox(height: 2),
                  Text(
                    'DEBUG MODE',
                    style: TextStyle(
                      color: Color(0xFF71817C),
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 1.1,
                    ),
                  ),
                ],
              ),
            ),
            IconButton.filledTonal(
              tooltip: 'Kembali ke layar utama',
              onPressed: () => Navigator.of(context).pop(),
              style: IconButton.styleFrom(
                backgroundColor: Colors.white,
                foregroundColor: AppColors.ink,
                fixedSize: const Size(44, 44),
              ),
              icon: const Icon(Icons.arrow_back_rounded),
            ),
          ],
        ),
      );

  Widget _buildDebugContent() => SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildDebugSection('Connection Status', [
              'Connected: ${widget.controller.isConnected}',
              'Connecting: ${widget.controller.isConnecting}',
              'Session ID: ${widget.controller.sessionId}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('Device State', [
              'Device State: ${widget.controller.deviceState.name}',
              'Emotion: ${widget.controller.emotion}',
              'Live Text: ${widget.controller.liveText.isEmpty ? '(empty)' : widget.controller.liveText}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('Audio Level', [
              'Audio Level: ${widget.controller.audioLevel}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('Lip-sync', [
              'Vowel: ${_lipSyncText()}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('MCP Tools (tools/list)', _toolsListText()),
            const SizedBox(height: 20),
            _buildDebugSection('Wake Word', _wakeWordText()),
            const SizedBox(height: 20),
            _buildDebugSection('Pending Attachments', [
              'Text Attachment: ${widget.controller.pendingTextAttachment != null ? 'YES' : 'NO'}',
              'PDF Attachment: ${widget.controller.pendingPdf != null ? 'YES' : 'NO'}',
              'Image Attachment: ${widget.controller.pendingImageAttachment != null ? 'YES' : 'NO'}',
              'Image Question: ${widget.controller.pendingImageQuestion}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('System Info', [
              'Platform: ${Platform.operatingSystem}',
              'Flutter: ${Platform.version}',
]),
          ],
        ),
      );

  Widget _buildDebugSection(String title, List<String> items) => Container(
        width: double.infinity,
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: const Color(0xFFE4EAE7)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              title,
              style: const TextStyle(
                color: AppColors.green,
                fontSize: 16,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 12),
            ...items.map(
              (item) => Padding(
                padding: const EdgeInsets.symmetric(vertical: 4),
                child: Text(
                  item,
                  style: const TextStyle(
                    color: AppColors.ink,
                    fontSize: 14,
                  ),
                ),
              ),
            ),
          ],
        ),
      );

  Widget _buildFooter(BuildContext context) => Container(
        padding: const EdgeInsets.all(20),
        child: Row(
          children: [
            Expanded(
              child: FilledButton.icon(
                onPressed: () => Navigator.of(context).pop(),
                icon: const Icon(Icons.arrow_back_rounded),
                label: const Text('Kembali ke Layar Utama'),
                style: FilledButton.styleFrom(
                  backgroundColor: AppColors.green,
                  foregroundColor: Colors.white,
                  minimumSize: const Size.fromHeight(48),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                ),
              ),
            ),
          ],
        ),
      );
}