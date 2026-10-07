import 'dart:async';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter_music_picker/flutter_music_picker.dart';
import 'package:flutter/material.dart';
import 'package:just_audio/just_audio.dart';
import 'package:media_store_plus/media_store_plus.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';

import 'app_theme.dart';
import 'camera_screen.dart';
import 'mcp_runtime.dart';
import 'reminder_runtime.dart';
import 'smart_home_scanner.dart';
import 'xiaozhi_controller.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.controller});

  final XiaozhiController controller;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  static Future<void>? _mediaStoreInitialization;

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
  bool _scanningSmartHome = false;
  bool _scanningMqttBrokers = false;
  final AudioPlayer _reminderPreviewPlayer = AudioPlayer();
  String _reminderSoundUri = ReminderRuntime.defaultAlarmSoundUri;
  String _reminderSoundName = 'Suara bawaan perangkat';
  bool _loadingReminderSound = false;
  bool _previewingReminderSound = false;

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
    _McpModule('volume', 'Volume', 'Kontrol volume sistem', supported: true),
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
    unawaited(_loadReminderSound());
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
    unawaited(_reminderPreviewPlayer.dispose());
    unawaited(FlutterMusicPicker.stopRingtone());
    super.dispose();
  }

  Future<void> _loadReminderSound() async {
    final (uri, name) = await ReminderRuntime.getAlarmSound();
    if (!mounted) return;
    setState(() {
      _reminderSoundUri = uri;
      _reminderSoundName = name;
    });
  }

  Future<bool> _requestReminderAudioPermission() async {
    var status = await Permission.audio.request();
    if (!status.isGranted) status = await Permission.storage.request();
    if (status.isGranted) return true;
    if (!mounted) return false;
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(content: Text('Izin audio diperlukan untuk membaca nada alarm di HP.')),
    );
    return false;
  }

  Future<void> _pickSystemReminderSound() async {
    if (!await _requestReminderAudioPermission()) return;
    setState(() => _loadingReminderSound = true);
    try {
      final sounds = await FlutterMusicPicker.getRingtones();
      if (!mounted) return;
      if (sounds.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Daftar nada alarm HP kosong.')),
        );
        return;
      }
      final selected = await showModalBottomSheet<MusicItem>(
        context: context,
        isScrollControlled: true,
        builder: (context) => SafeArea(
          child: SizedBox(
            height: MediaQuery.sizeOf(context).height * 0.72,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(20, 18, 20, 10),
                  child: Text(
                    'Nada alarm di HP',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                ),
                Expanded(
                  child: ListView.builder(
                    itemCount: sounds.length,
                    itemBuilder: (context, index) {
                      final sound = sounds[index];
                      return ListTile(
                        leading: Icon(sound.isRingtone ? Icons.alarm_rounded : Icons.notifications_active_outlined),
                        title: Text(sound.title, maxLines: 1, overflow: TextOverflow.ellipsis),
                        subtitle: Text(sound.album, maxLines: 1, overflow: TextOverflow.ellipsis),
                        onTap: () => Navigator.of(context).pop(sound),
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        ),
      );
      if (selected != null) {
        await _saveReminderSound(uri: selected.uri, name: selected.title);
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Nada alarm gagal dimuat: $error')),
        );
      }
    } finally {
      if (mounted) setState(() => _loadingReminderSound = false);
    }
  }

  Future<void> _pickCustomReminderSound() async {
    try {
      final result = await FilePicker.platform.pickFiles(
        type: FileType.audio,
        allowMultiple: false,
        withData: true,
      );
      if (result == null || result.files.isEmpty) return;
      final selected = result.files.single;
      final bytes = selected.bytes ??
          (selected.path == null ? null : await File(selected.path!).readAsBytes());
      if (bytes == null || bytes.isEmpty) throw const FormatException('File audio kosong.');
      if (bytes.length > 20 * 1024 * 1024) {
        throw const FormatException('Ukuran suara alarm maksimal 20 MB.');
      }
      final extension = p.extension(selected.name).toLowerCase();
      if (!{'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}.contains(extension)) {
        throw const FormatException('Pilih file MP3, WAV, M4A, AAC, OGG, atau FLAC.');
      }
      final directory = await getApplicationSupportDirectory();
      final target = File(p.join(
        directory.path,
        'reminder_alarm_${DateTime.now().microsecondsSinceEpoch}$extension',
      ));
      await target.writeAsBytes(bytes, flush: true);
      await (_mediaStoreInitialization ??= MediaStore.ensureInitialized());
      final saved = await MediaStore().saveFile(
        tempFilePath: target.path,
        dirType: DirType.audio,
        dirName: DirName.ringtones,
      );
      if (saved == null || saved.uri.toString().isEmpty) {
        throw const FileSystemException('File tidak dapat disimpan ke folder Ringtones HP.');
      }
      await _saveReminderSound(uri: saved.uri.toString(), name: selected.name);
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('File suara gagal dipakai: $error')),
      );
    }
  }

  Future<void> _saveReminderSound({required String uri, required String name}) async {
    setState(() => _loadingReminderSound = true);
    try {
      await _stopReminderSoundPreview();
      final storedUri = uri.isEmpty ? ReminderRuntime.defaultAlarmSoundUri : uri;
      await ReminderRuntime.setAlarmSound(uri: uri, name: name);
      if (!mounted) return;
      setState(() {
        _reminderSoundUri = storedUri;
        _reminderSoundName = name;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Suara disimpan. Jadwal reminder aktif diperbarui.')),
      );
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Suara reminder gagal disimpan: $error')),
      );
    } finally {
      if (mounted) setState(() => _loadingReminderSound = false);
    }
  }

  Future<void> _toggleReminderSoundPreview() async {
    if (_previewingReminderSound) {
      await _stopReminderSoundPreview();
      return;
    }
    if (_reminderSoundUri.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Pilih nada alarm terlebih dahulu.')),
      );
      return;
    }
    try {
      final uri = Uri.parse(_reminderSoundUri);
      if (uri.scheme == 'content') {
        if (mounted) setState(() => _previewingReminderSound = true);
        await FlutterMusicPicker.playRingtone(_reminderSoundUri);
      } else {
        await _reminderPreviewPlayer.setAudioSource(AudioSource.uri(uri));
        if (mounted) setState(() => _previewingReminderSound = true);
        unawaited(_reminderPreviewPlayer.play().whenComplete(() {
          if (mounted) setState(() => _previewingReminderSound = false);
        }));
      }
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Preview suara gagal diputar: $error')),
      );
    }
  }

  Future<void> _stopReminderSoundPreview() async {
    await _reminderPreviewPlayer.stop();
    await FlutterMusicPicker.stopRingtone();
    if (mounted) setState(() => _previewingReminderSound = false);
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

  Future<void> _scanSmartHomeDevices() async {
    final broker = _smartHomeBrokerController.text.trim();
    if (broker.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Masukkan broker MQTT sebelum scan perangkat.')),
      );
      return;
    }
    setState(() => _scanningSmartHome = true);
    try {
      final response = await McpRuntime.handle(
        {
          'jsonrpc': '2.0',
          'id': 1,
          'method': 'tools/call',
          'params': {'name': 'discover_devices', 'arguments': <String, Object?>{}},
        },
        disabledModules: const <String>{},
        smartHomeConfig: {
          'broker': broker,
          'port': int.tryParse(_smartHomePortController.text) ?? 1883,
          'username': _smartHomeUsernameController.text,
          'password': _smartHomePasswordController.text,
          'useTls': _smartHomeUseTls,
          'devices': _smartHomeDevicesController.text,
        },
      );
      final error = response?['error'];
      final result = response?['result'];
      final content = result is Map ? result['content'] : null;
      final first = content is List && content.isNotEmpty ? content.first : null;
      final message = error is Map
          ? '${error['message'] ?? 'Scan gagal.'}'
          : first is Map
              ? '${first['text'] ?? 'Scan selesai.'}'
              : 'Scan selesai.';
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Scan smart home gagal: $error')),
      );
    } finally {
      if (mounted) setState(() => _scanningSmartHome = false);
    }
  }

  Future<void> _scanMqttBrokers() async {
    final port = int.tryParse(_smartHomePortController.text) ?? 1883;
    setState(() => _scanningMqttBrokers = true);
    try {
      final hosts = await SmartHomeScanner.findMqttBrokers(port);
      if (!mounted) return;
      if (hosts.isNotEmpty) _smartHomeBrokerController.text = hosts.first;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            hosts.isEmpty
                ? 'Tidak ditemukan broker MQTT pada port $port.'
                : 'Broker ditemukan: ${hosts.join(', ')}',
          ),
        ),
      );
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Scan broker MQTT gagal: $error')),
      );
    } finally {
      if (mounted) setState(() => _scanningMqttBrokers = false);
    }
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
                        hintText: 'wss://api.tenclass.net/xiaozhi/v1/',
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
                        hintText: 'test-token',
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
                      'Alarm & Reminder',
                      style: TextStyle(color: AppColors.ink, fontSize: 17, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      'Pengingat dijadwalkan oleh Android dan tetap aktif setelah aplikasi ditutup atau HP dimulai ulang.',
                      style: TextStyle(color: Color(0xFF71817C), height: 1.45),
                    ),
                    const SizedBox(height: 8),
                    ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.notifications_active_outlined, color: AppColors.green),
                      title: const Text('Suara alarm'),
                      subtitle: Text(_reminderSoundName, maxLines: 2, overflow: TextOverflow.ellipsis),
                      trailing: IconButton.filledTonal(
                        tooltip: _previewingReminderSound ? 'Hentikan preview' : 'Putar preview',
                        onPressed: _loadingReminderSound ? null : _toggleReminderSoundPreview,
                        icon: Icon(_previewingReminderSound ? Icons.stop_rounded : Icons.play_arrow_rounded),
                      ),
                    ),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        OutlinedButton.icon(
                          onPressed: _loadingReminderSound ? null : _pickSystemReminderSound,
                          icon: const Icon(Icons.notifications_active_outlined, size: 18),
                          label: const Text('Nada HP'),
                        ),
                        OutlinedButton.icon(
                          onPressed: _loadingReminderSound ? null : _pickCustomReminderSound,
                          icon: const Icon(Icons.audio_file_outlined, size: 18),
                          label: const Text('File audio'),
                        ),
                        TextButton.icon(
                          onPressed: _loadingReminderSound
                              ? null
                              : () => _saveReminderSound(
                                    uri: ReminderRuntime.defaultAlarmSoundUri,
                                    name: 'Suara bawaan perangkat',
                                  ),
                          icon: const Icon(Icons.restart_alt_rounded, size: 18),
                          label: const Text('Bawaan'),
                        ),
                      ],
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
                        hintText: 'https://api.xiaozhi.me/vision/explain',
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
                        hintText: 'https://api.xiaozhi.me/vision/explain',
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
                    Align(
                      alignment: Alignment.centerLeft,
                      child: OutlinedButton.icon(
                        onPressed: _scanningMqttBrokers ? null : _scanMqttBrokers,
                        icon: _scanningMqttBrokers
                            ? const SizedBox.square(
                                dimension: 18,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.wifi_find_rounded, size: 18),
                        label: Text(_scanningMqttBrokers ? 'Memindai jaringan...' : 'Cari broker di Wi-Fi'),
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
                    const _FieldLabel(label: 'Devices (opsional, JSON)'),
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
                    Align(
                      alignment: Alignment.centerLeft,
                      child: OutlinedButton.icon(
                        onPressed: _scanningSmartHome ? null : _scanSmartHomeDevices,
                        icon: _scanningSmartHome
                            ? const SizedBox.square(
                                dimension: 18,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Icon(Icons.radar_rounded, size: 18),
                        label: Text(_scanningSmartHome ? 'Memindai...' : 'Scan perangkat Tasmota'),
                      ),
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      'Scan menyimpan perangkat yang ditemukan untuk kontrol otomatis. JSON hanya diperlukan jika discovery broker tidak tersedia; setiap entri manual memerlukan topic.',
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