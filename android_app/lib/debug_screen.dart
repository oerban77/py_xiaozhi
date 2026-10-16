import 'dart:io';

import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'xiaozhi_controller.dart';

class DebugScreen extends StatelessWidget {
  const DebugScreen({super.key, required this.controller});

  final XiaozhiController controller;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.paper,
      body: GestureDetector(
        onHorizontalDragEnd: (details) {
          if (details.primaryDelta > 50) {
            // Right swipe - go back to main screen
            Navigator.of(context).pop();
          }
        },
        child: SafeArea(
          child: Column(
            children: [
              _buildHeader(),
              Expanded(child: _buildDebugContent()),
              _buildFooter(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader() => Padding(
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
              'Connected: ${controller.isConnected}',
              'Connecting: ${controller.isConnecting}',
              'Session ID: ${controller.sessionId ?? 'N/A'}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('Device State', [
              'Device State: ${controller.deviceState.name}',
              'Emotion: ${controller.emotion}',
              'Live Text: ${controller.liveText.isEmpty ? '(empty)' : controller.liveText}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('Audio Level', [
              'Audio Level: ${controller.audioLevel}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('Pending Attachments', [
              'Text Attachment: ${controller.pendingTextAttachment != null ? 'YES' : 'NO'}',
              'PDF Attachment: ${controller.pendingPdf != null ? 'YES' : 'NO'}',
              'Image Attachment: ${controller.pendingImageAttachment != null ? 'YES' : 'NO'}',
              'Image Question: ${controller.pendingImageQuestion}',
            ]),
            const SizedBox(height: 20),
            _buildDebugSection('System Info', [
              'Platform: ${Platform.operatingSystem}',
              'Flutter Version: ${Platform.flutterVersion}',
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
          border: Border.all(color: Color(0xFFE4EAE7)),
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
            ...items
                .map(
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
                )
                .toList(),
          ],
        ),
      );

  Widget _buildFooter() => Container(
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