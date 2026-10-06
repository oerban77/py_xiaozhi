// First-run permission agreement screen. Shown by main.dart while any required
// runtime permission is missing, so the mic, camera and notification prompts are
// answered once, up front, instead of firing mid-conversation.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:permission_handler/permission_handler.dart';

import 'app_theme.dart';
import 'permission_manager.dart';

class PermissionGateScreen extends StatefulWidget {
  const PermissionGateScreen({super.key, required this.onGranted});

  /// Called once every required permission is granted; the app then continues.
  final VoidCallback onGranted;

  @override
  State<PermissionGateScreen> createState() => _PermissionGateScreenState();
}

class _PermissionGateScreenState extends State<PermissionGateScreen> {
  final PermissionManager _manager = const PermissionManager();
  List<(AppPermission, PermissionStatus)> _statuses = const [];
  bool _requesting = true;
  bool _permanentlyDenied = false;

  @override
  void initState() {
    super.initState();
    // Ask immediately on first launch; the list below reflects the answers.
    unawaited(_request());
  }

  Future<void> _request() async {
    setState(() {
      _requesting = true;
      _permanentlyDenied = false;
    });
    final result = await _manager.requestAll();
    final statuses = await _manager.statuses();
    if (!mounted) return;
    setState(() {
      _statuses = statuses;
      _requesting = false;
      _permanentlyDenied = result == PermissionRequestResult.permanentlyDenied;
    });
    if (result == PermissionRequestResult.allGranted) {
      widget.onGranted();
    }
  }

  Future<void> _openSettings() async {
    await _manager.openSettings();
    // The settings page is a separate app task; re-check when the user comes back.
    if (!mounted) return;
    final allGranted = await _manager.allRequiredGranted;
    if (allGranted) {
      widget.onGranted();
      return;
    }
    await _request();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: AppColors.paper,
        body: SafeArea(
          child: _requesting
              ? const Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      CircularProgressIndicator(color: AppColors.green),
                      SizedBox(height: 20),
                      Text('Meminta izin...'),
                    ],
                  ),
                )
              : _buildAgreement(),
        ),
      );

  Widget _buildAgreement() {
    final missing = _statuses.where((row) => !row.$2.isGranted).toList();
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 32, 24, 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Icon(Icons.shield_outlined, size: 56, color: AppColors.green),
          const SizedBox(height: 20),
          const Text(
            'Izin yang dibutuhkan',
            style: TextStyle(
              color: AppColors.ink,
              fontSize: 24,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 10),
          const Text(
            'Aplikasi ini butuh izin berikut agar bisa mendengarkan suara, '
            'menggunakan kamera, dan menampilkan notifikasi. Izin hanya diminta '
            'sekali; setelah disetujui, aplikasi tidak akan menanyanya lagi saat '
            'digunakan.',
            style: TextStyle(color: Color(0xFF71817C), height: 1.5),
          ),
          const SizedBox(height: 28),
          Expanded(child: _buildList()),
          if (_permanentlyDenied) ...[
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: const Color(0xFFFDECEA),
                borderRadius: BorderRadius.circular(14),
              ),
              child: const Text(
                'Salah satu izin ditolak permanen. Aktifkan secara manual di '
                'pengaturan aplikasi, lalu kembali ke sini.',
                style: TextStyle(color: Color(0xFFB3261E), height: 1.45),
              ),
            ),
            const SizedBox(height: 16),
          ],
          if (missing.isEmpty)
            FilledButton(
              onPressed: widget.onGranted,
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.green,
                padding: const EdgeInsets.symmetric(vertical: 16),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(16),
                ),
              ),
              child: const Text('Lanjutkan'),
            )
          else ...[
            FilledButton(
              onPressed: _permanentlyDenied ? _openSettings : _request,
              style: FilledButton.styleFrom(
                backgroundColor: AppColors.green,
                padding: const EdgeInsets.symmetric(vertical: 16),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(16),
                ),
              ),
              child: Text(
                _permanentlyDenied ? 'Buka pengaturan aplikasi' : 'Berikan izin',
              ),
            ),
            const SizedBox(height: 12),
            TextButton(
              onPressed: widget.onGranted,
              child: const Text(
                'Lewati untuk sekarang',
                style: TextStyle(color: Color(0xFF71817C)),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildList() => ListView.separated(
        itemCount: _statuses.length,
        separatorBuilder: (_, __) => const Divider(height: 1),
        itemBuilder: (context, index) {
          final (permission, status) = _statuses[index];
          final granted = status.isGranted;
          return ListTile(
            contentPadding: const EdgeInsets.symmetric(vertical: 6),
            leading: Icon(
              granted ? Icons.check_circle : Icons.error_outline,
              color: granted ? AppColors.green : const Color(0xFFB3261E),
            ),
            title: Text(
              permission.title,
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            subtitle: Text(
              '${permission.reason}\n${permission.statusText(status)}',
              style: const TextStyle(color: Color(0xFF71817C), height: 1.4),
            ),
            isThreeLine: true,
          );
        },
      );
}
