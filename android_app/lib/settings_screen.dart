import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'camera_screen.dart';
import 'xiaozhi_controller.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.controller});

  final XiaozhiController controller;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _urlController;
  late final TextEditingController _tokenController;
  late final TextEditingController _deviceController;
  late final TextEditingController _clientController;
  late String _cameraFacing;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    _urlController = TextEditingController(text: widget.controller.endpoint);
    _tokenController = TextEditingController(text: widget.controller.token);
    _deviceController = TextEditingController(text: widget.controller.deviceId);
    _clientController = TextEditingController(text: widget.controller.clientId);
    _cameraFacing = widget.controller.cameraFacing;
  }

  @override
  void dispose() {
    _urlController.dispose();
    _tokenController.dispose();
    _deviceController.dispose();
    _clientController.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_urlController.text.trim().isEmpty || _tokenController.text.trim().isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Alamat server dan access token wajib diisi.')),
      );
      return;
    }
    setState(() => _saving = true);
    await widget.controller.saveSettings(
      newEndpoint: _urlController.text,
      newToken: _tokenController.text,
      newDeviceId: _deviceController.text,
      newClientId: _clientController.text,
      newCameraFacing: _cameraFacing,
    );
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: const Text('Pengaturan'),
          leading: IconButton(
            tooltip: 'Kembali',
            onPressed: () => Navigator.of(context).pop(),
            icon: const Icon(Icons.arrow_back_rounded),
          ),
        ),
        body: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 8, 20, 28),
            children: [
              const Text(
                'Koneksi server',
                style: TextStyle(color: AppColors.ink, fontSize: 21, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 6),
              const Text(
                'Masukkan detail WebSocket Xiaozhi yang diberikan oleh server Anda.',
                style: TextStyle(color: Color(0xFF71817C), height: 1.45),
              ),
              const SizedBox(height: 22),
              const _FieldLabel(label: 'Alamat WebSocket'),
              TextField(
                controller: _urlController,
                keyboardType: TextInputType.url,
                autocorrect: false,
                decoration: const InputDecoration(
                  hintText: 'wss://server.example/v1/',
                  prefixIcon: Icon(Icons.dns_outlined),
                ),
              ),
              const SizedBox(height: 18),
              const _FieldLabel(label: 'Access token'),
              TextField(
                controller: _tokenController,
                obscureText: true,
                autocorrect: false,
                decoration: const InputDecoration(
                  hintText: 'Bearer token',
                  prefixIcon: Icon(Icons.key_outlined),
                ),
              ),
              const SizedBox(height: 26),
              const Text(
                'Identitas perangkat',
                style: TextStyle(color: AppColors.ink, fontSize: 17, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 14),
              const _FieldLabel(label: 'Device ID'),
              TextField(
                controller: _deviceController,
                autocorrect: false,
                decoration: const InputDecoration(prefixIcon: Icon(Icons.phone_android_rounded)),
              ),
              const SizedBox(height: 16),
              const _FieldLabel(label: 'Client ID'),
              TextField(
                controller: _clientController,
                autocorrect: false,
                decoration: const InputDecoration(prefixIcon: Icon(Icons.fingerprint_rounded)),
              ),
              const SizedBox(height: 12),
              const Text(
                'Token disimpan di penyimpanan aman Android. ID perangkat dibuat otomatis dan dapat diubah bila server meminta identitas tertentu.',
                style: TextStyle(color: Color(0xFF71817C), fontSize: 12, height: 1.5),
              ),
              const SizedBox(height: 28),
              const Text(
                'Kamera',
                style: TextStyle(color: AppColors.ink, fontSize: 17, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 8),
              const Text(
                'Pilih kamera yang digunakan saat membuka preview.',
                style: TextStyle(color: Color(0xFF71817C), height: 1.4),
              ),
              const SizedBox(height: 14),
              SegmentedButton<String>(
                segments: const [
                  ButtonSegment(
                    value: 'back',
                    icon: Icon(Icons.camera_rear_outlined),
                    label: Text('Belakang'),
                  ),
                  ButtonSegment(
                    value: 'front',
                    icon: Icon(Icons.camera_front_outlined),
                    label: Text('Depan'),
                  ),
                ],
                selected: {_cameraFacing},
                onSelectionChanged: (selection) {
                  setState(() => _cameraFacing = selection.first);
                },
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: () => Navigator.of(context).push<void>(
                  MaterialPageRoute<void>(
                    builder: (_) => CameraScreen(lensDirection: _cameraFacing),
                  ),
                ),
                icon: const Icon(Icons.visibility_outlined),
                label: const Text('Uji preview kamera'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: AppColors.green,
                  minimumSize: const Size.fromHeight(48),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                ),
              ),
              const SizedBox(height: 28),
              FilledButton.icon(
                onPressed: _saving ? null : _save,
                icon: _saving
                    ? const SizedBox.square(
                        dimension: 18,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                      )
                    : const Icon(Icons.check_rounded),
                label: Text(_saving ? 'Menyimpan...' : 'Simpan pengaturan'),
                style: FilledButton.styleFrom(
                  backgroundColor: AppColors.green,
                  foregroundColor: Colors.white,
                  minimumSize: const Size.fromHeight(52),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                ),
              ),
            ],
          ),
        ),
      );
}

class _FieldLabel extends StatelessWidget {
  const _FieldLabel({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 7),
        child: Text(
          label,
          style: const TextStyle(color: AppColors.ink, fontSize: 13, fontWeight: FontWeight.w600),
        ),
      );
}