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
  late final TextEditingController _localVlUrlController;
  late final TextEditingController _vlApiKeyController;
  late final TextEditingController _visionUrlController;
  late final TextEditingController _visionTokenController;
  late final TextEditingController _smartHomeBrokerController;
  late final TextEditingController _smartHomePortController;
  late final TextEditingController _smartHomeUsernameController;
  late final TextEditingController _smartHomePasswordController;
  late final TextEditingController _smartHomeDevicesController;
  late String _cameraFacing;
  late bool _autoConversation;
  late bool _smartHomeUseTls;
  late int _outputSampleRate;
  late bool _wakeWordEnabled;
  late final TextEditingController _wakeWordController;
  // Live validation result for the typed wake word; empty string means valid.
  String _wakeWordHint = '';
  bool _wakeWordValid = false;
  late final Set<String> _disabledMcpModules;
  bool _saving = false;

  static const _mcpModules = <_McpModule>[
    _McpModule('app', 'App', 'Pengelolaan aplikasi'),
    _McpModule('blender', 'Blender', 'Kontrol Blender 3D', desktopOnly: true),
    _McpModule('camera', 'Camera', 'Kamera', supported: true),
    _McpModule('coding', 'Coding', 'Operasi workspace dan kode', desktopOnly: true),
    _McpModule('documents', 'Documents', 'Dokumen'),
    _McpModule('hardware', 'Hardware', 'Akses perangkat dan sistem', desktopOnly: true),
    _McpModule('indonesia_holiday', 'Indonesia Holiday', 'Hari libur Indonesia', supported: true),
    _McpModule('kali', 'Kali', 'Tool keamanan dan jaringan', desktopOnly: true),
    _McpModule('music', 'Music', 'Musik', supported: true),
    _McpModule('news', 'News', 'Berita', supported: true),
    _McpModule('prayer', 'Prayer', 'Jadwal sholat', supported: true),
    _McpModule('qrcode', 'QRCode', 'Kode QR', supported: true),
    _McpModule('reminder', 'Reminder', 'Pengingat', supported: true),
    _McpModule('screenshot', 'Screenshot', 'Tangkapan layar desktop', desktopOnly: true),
    _McpModule('smarthome', 'Smart Home', 'Otomasi rumah', supported: true),
    _McpModule('volume', 'Volume', 'Kontrol volume sistem', desktopOnly: true),
    _McpModule('weather', 'Weather', 'Cuaca', supported: true),
    _McpModule('websearch', 'Web Search', 'Pencarian web', supported: true),
  ];

  @override
  void initState() {
    super.initState();
    _urlController = TextEditingController(text: widget.controller.endpoint);
    _tokenController = TextEditingController(text: widget.controller.token);
    _deviceController = TextEditingController(text: widget.controller.deviceId);
    _clientController = TextEditingController(text: widget.controller.clientId);
    _localVlUrlController = TextEditingController(text: widget.controller.localVlUrl);
    _vlApiKeyController = TextEditingController(text: widget.controller.vlApiKey);
    _visionUrlController = TextEditingController(text: widget.controller.visionUrl);
    _visionTokenController = TextEditingController(text: widget.controller.visionToken);
    _smartHomeBrokerController = TextEditingController(text: widget.controller.smartHomeBroker);
    _smartHomePortController = TextEditingController(text: '${widget.controller.smartHomePort}');
    _smartHomeUsernameController = TextEditingController(text: widget.controller.smartHomeUsername);
    _smartHomePasswordController = TextEditingController(text: widget.controller.smartHomePassword);
    _smartHomeDevicesController = TextEditingController(text: widget.controller.smartHomeDevicesJson);
    _smartHomeUseTls = widget.controller.smartHomeUseTls;
    _disabledMcpModules = {
      ...widget.controller.disabledMcpModules,
      ..._mcpModules.where((module) => !module.supported).map((module) => module.id),
    };
    _cameraFacing = widget.controller.cameraFacing;
    _autoConversation = widget.controller.autoConversation;
    _outputSampleRate = widget.controller.outputSampleRate;
    _wakeWordEnabled = widget.controller.wakeWordOptions.enabled;
    _wakeWordController = TextEditingController(text: widget.controller.wakeWordOptions.wakeWord);
    // Validate the initial value once so the helper text explains what the
    // detector will actually load.
    WidgetsBinding.instance.addPostFrameCallback((_) => _validateWakeWord());
  }

  @override
  void dispose() {
    _urlController.dispose();
    _tokenController.dispose();
    _deviceController.dispose();
    _clientController.dispose();
    _localVlUrlController.dispose();
    _vlApiKeyController.dispose();
    _visionUrlController.dispose();
    _visionTokenController.dispose();
    _smartHomeBrokerController.dispose();
    _smartHomePortController.dispose();
    _smartHomeUsernameController.dispose();
    _smartHomePasswordController.dispose();
    _smartHomeDevicesController.dispose();
    _wakeWordController.dispose();
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
      newLocalVlUrl: _localVlUrlController.text,
      newVlApiKey: _vlApiKeyController.text,
      newVisionUrl: _visionUrlController.text,
      newVisionToken: _visionTokenController.text,
      newSmartHomeBroker: _smartHomeBrokerController.text,
      newSmartHomePort: int.tryParse(_smartHomePortController.text) ?? 1883,
      newSmartHomeUsername: _smartHomeUsernameController.text,
      newSmartHomePassword: _smartHomePasswordController.text,
      newSmartHomeUseTls: _smartHomeUseTls,
      newSmartHomeDevicesJson: _smartHomeDevicesController.text,
      newDisabledMcpModules: _disabledMcpModules.toList()..sort(),
      newAutoConversation: _autoConversation,
      newOutputSampleRate: _outputSampleRate,
      newWakeWordEnabled: _wakeWordEnabled,
      newWakeWordText: _wakeWordController.text,
    );
    if (mounted) Navigator.of(context).pop();
  }

  /// Validates the typed wake word against the bundled BPE tokens without loading
  /// the ONNX models, so the user sees a bad keyword before saving.
  Future<void> _validateWakeWord() async {
    final text = _wakeWordController.text.trim();
    if (text.isEmpty) {
      setState(() {
        _wakeWordValid = false;
        _wakeWordHint = 'Kata wake word tidak boleh kosong.';
      });
      return;
    }
    final error = await widget.controller.wakeWordDetector.validateKeyword(text);
    if (!mounted) return;
    setState(() {
      _wakeWordValid = error == null;
      _wakeWordHint = error ?? '';
    });
  }

  @override
  Widget build(BuildContext context) => DefaultTabController(
        length: 2,
        child: Scaffold(
          appBar: AppBar(
            title: const Text('Pengaturan'),
            leading: IconButton(
              tooltip: 'Kembali',
              onPressed: () => Navigator.of(context).pop(),
              icon: const Icon(Icons.arrow_back_rounded),
            ),
            bottom: const TabBar(
              tabs: [
                Tab(text: 'Umum'),
                Tab(text: 'MCP Modules'),
              ],
            ),
          ),
          body: SafeArea(
            child: TabBarView(
              children: [
                ListView(
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
                      'Auto Conversation',
                      style: TextStyle(color: AppColors.ink, fontSize: 17, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 8),
                    SwitchListTile.adaptive(
                      contentPadding: EdgeInsets.zero,
                      value: _autoConversation,
                      title: const Text('Aktifkan auto conversation'),
                      subtitle: const Text('Mulai listening realtime saat mode otomatis aktif.'),
                      onChanged: (value) => setState(() => _autoConversation = value),
                      activeThumbColor: AppColors.green,
                      activeTrackColor: AppColors.green.withValues(alpha: 0.35),
                    ),
                    const SizedBox(height: 28),
                    const Text(
                      'Wake Word',
                      style: TextStyle(color: AppColors.ink, fontSize: 17, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 8),
                    SwitchListTile.adaptive(
                      contentPadding: EdgeInsets.zero,
                      value: _wakeWordEnabled,
                      title: const Text('Aktifkan wake word'),
                      subtitle: const Text(
                        'Ucapkan kata kunci untuk memulai percakapan atau menghentikan TTS '
                        'tanpa menekan tombol mikrofon.',
                      ),
                      onChanged: (value) => setState(() => _wakeWordEnabled = value),
                      activeThumbColor: AppColors.green,
                      activeTrackColor: AppColors.green.withValues(alpha: 0.35),
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'Hanya bahasa Inggris yang didukung (model bahasa Mandarin butuh '
                      'pinyin yang tidak tersedia di Android). Contoh: "Hello Xiaozhi".',
                      style: TextStyle(color: Color(0xFF71817C), height: 1.45),
                    ),
                    const SizedBox(height: 14),
                    TextField(
                      controller: _wakeWordController,
                      enabled: _wakeWordEnabled,
                      textCapitalization: TextCapitalization.words,
                      decoration: InputDecoration(
                        labelText: 'Kata wake word',
                        prefixIcon: const Icon(Icons.mic),
                        helperText: _wakeWordEnabled
                            ? (_wakeWordHint.isEmpty
                                ? 'Kata akan dideteksi saat terhubung ke server.'
                                : _wakeWordHint)
                            : 'Aktifkan dulu untuk mengatur kata wake word.',
                        helperStyle: TextStyle(
                          color: _wakeWordHint.isEmpty ? const Color(0xFF71817C) : Colors.red,
                        ),
                        errorText: _wakeWordEnabled && !_wakeWordValid ? '' : null,
                      ),
                      onChanged: (value) {
                        _validateWakeWord();
                        setState(() {});
                      },
                    ),
                    const SizedBox(height: 28),
                    const Text(
                      'Audio',
                      style: TextStyle(color: AppColors.ink, fontSize: 17, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'Sample rate decode Opus harus cocok dengan encode rate server. '
                      'Server resmi 24000 Hz; server pihak ketiga biasanya 16000 Hz. '
                      'Jika TTS terdengar robotic/terganggu, coba ganti nilai ini.',
                      style: TextStyle(color: Color(0xFF71817C), height: 1.45),
                    ),
                    const SizedBox(height: 14),
                    SegmentedButton<int>(
                      segments: const [
                        ButtonSegment<int>(
                          value: 24000,
                          label: Text('24000 Hz'),
                        ),
                        ButtonSegment<int>(
                          value: 16000,
                          label: Text('16000 Hz'),
                        ),
                      ],
                      selected: {_outputSampleRate},
                      onSelectionChanged: (selection) {
                        if (selection.isEmpty) return;
                        setState(() => _outputSampleRate = selection.first);
                      },
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
                    const Text(
                      'Vision / Kamera AI',
                      style: TextStyle(color: AppColors.ink, fontSize: 17, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 8),
                    const _FieldLabel(label: 'Local VL URL'),
                    TextField(
                      controller: _localVlUrlController,
                      keyboardType: TextInputType.url,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        hintText: 'https://your-vl.example/api',
                        prefixIcon: Icon(Icons.link_rounded),
                      ),
                    ),
                    const SizedBox(height: 16),
                    const _FieldLabel(label: 'VL API Key'),
                    TextField(
                      controller: _vlApiKeyController,
                      obscureText: true,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        hintText: 'Token model vision',
                        prefixIcon: Icon(Icons.key_outlined),
                      ),
                    ),
                    const SizedBox(height: 16),
                    const _FieldLabel(label: 'Vision Service URL'),
                    TextField(
                      controller: _visionUrlController,
                      keyboardType: TextInputType.url,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        hintText: 'https://api.xiaozhi.me/vision',
                        prefixIcon: Icon(Icons.image_search_rounded),
                      ),
                    ),
                    const SizedBox(height: 16),
                    const _FieldLabel(label: 'Vision Service Token'),
                    TextField(
                      controller: _visionTokenController,
                      obscureText: true,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        hintText: 'Bearer token untuk layanan vision',
                        prefixIcon: Icon(Icons.shield_outlined),
                      ),
                    ),
                  ],
                ),
                ListView(
                  padding: const EdgeInsets.fromLTRB(20, 18, 20, 28),
                  children: [
                    const Text(
                      'Modul MCP',
                      style: TextStyle(color: AppColors.ink, fontSize: 21, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      'Preferensi disimpan di perangkat. Runtime MCP Android masih perlu diport; modul desktop-only belum tersedia di Android.',
                      style: TextStyle(color: Color(0xFF71817C), height: 1.45),
                    ),
                    const SizedBox(height: 12),
                    for (final module in _mcpModules)
                      SwitchListTile.adaptive(
                        contentPadding: EdgeInsets.zero,
                        value: !_disabledMcpModules.contains(module.id),
                        title: Text(module.title),
                        subtitle: Text(
                          module.desktopOnly
                            ? '${module.description} · Desktop-only'
                            : module.supported
                              ? module.description
                              : '${module.description} · Belum tersedia di Android',
                        ),
                        onChanged: !module.supported ? null : (enabled) {
                          setState(() {
                            if (enabled) {
                              _disabledMcpModules.remove(module.id);
                            } else {
                              _disabledMcpModules.add(module.id);
                            }
                          });
                        },
                        activeThumbColor: AppColors.green,
                        activeTrackColor: AppColors.green.withValues(alpha: 0.35),
                      ),
                    const SizedBox(height: 20),
                    const Text(
                      'Smart Home MQTT',
                      style: TextStyle(color: AppColors.ink, fontSize: 17, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'Kredensial disimpan di perangkat. TLS otomatis digunakan untuk port 8883.',
                      style: TextStyle(color: Color(0xFF71817C), height: 1.45),
                    ),
                    const SizedBox(height: 14),
                    const _FieldLabel(label: 'Broker IP / host'),
                    TextField(
                      controller: _smartHomeBrokerController,
                      keyboardType: TextInputType.url,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        hintText: '192.168.1.10 atau mqtt.example.com',
                        prefixIcon: Icon(Icons.router_outlined),
                      ),
                    ),
                    const SizedBox(height: 14),
                    const _FieldLabel(label: 'Port'),
                    TextField(
                      controller: _smartHomePortController,
                      keyboardType: TextInputType.number,
                      decoration: const InputDecoration(
                        hintText: '1883',
                        prefixIcon: Icon(Icons.numbers_rounded),
                      ),
                    ),
                    const SizedBox(height: 14),
                    const _FieldLabel(label: 'Username'),
                    TextField(
                      controller: _smartHomeUsernameController,
                      autocorrect: false,
                      decoration: const InputDecoration(prefixIcon: Icon(Icons.person_outline_rounded)),
                    ),
                    const SizedBox(height: 14),
                    const _FieldLabel(label: 'Password'),
                    TextField(
                      controller: _smartHomePasswordController,
                      obscureText: true,
                      autocorrect: false,
                      decoration: const InputDecoration(prefixIcon: Icon(Icons.key_outlined)),
                    ),
                    SwitchListTile.adaptive(
                      contentPadding: EdgeInsets.zero,
                      value: _smartHomeUseTls,
                      title: const Text('Gunakan TLS'),
                      subtitle: const Text('Aktifkan untuk broker TLS seperti port 8883.'),
                      onChanged: (value) => setState(() => _smartHomeUseTls = value),
                      activeThumbColor: AppColors.green,
                      activeTrackColor: AppColors.green.withValues(alpha: 0.35),
                    ),
                    const SizedBox(height: 14),
                    const _FieldLabel(label: 'Devices (JSON)'),
                    TextField(
                      controller: _smartHomeDevicesController,
                      minLines: 3,
                      maxLines: 8,
                      autocorrect: false,
                      decoration: const InputDecoration(
                        hintText: '[{"topic":"sonoff-1000","name":"Living Room Light","room":"living room","type":"light"}]',
                        alignLabelWithHint: true,
                      ),
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      'Setiap perangkat memerlukan topic; name, room, type, dan power_cmd opsional.',
                      style: TextStyle(color: Color(0xFF71817C), fontSize: 12, height: 1.4),
                    ),
                  ],
                ),
              ],
            ),
          ),
          bottomNavigationBar: SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
              child: FilledButton.icon(
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
            ),
          ),
        ),
      );
}

class _McpModule {
  const _McpModule(
    this.id,
    this.title,
    this.description, {
    this.desktopOnly = false,
    this.supported = false,
  });

  final String id;
  final String title;
  final String description;
  final bool desktopOnly;
  final bool supported;
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