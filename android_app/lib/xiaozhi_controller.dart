import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_sound/flutter_sound.dart';
import 'package:opus_dart/opus_dart.dart';
import 'package:opus_flutter/opus_flutter.dart' as opus_flutter;
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';
import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'chat_message.dart';
import 'emotion_service.dart';
import 'mcp_runtime.dart';
import 'protocol_messages.dart';
import 'wake_word_detector.dart';

class XiaozhiController extends ChangeNotifier {
  static const maxTextAttachmentChars = 24000;
  static const defaultEndpoint = 'wss://api.tenclass.net/xiaozhi/v1/';
  static const defaultAccessToken = 'test-token';
  static const defaultVisionUrl = 'https://api.xiaozhi.me/vision/explain';
  static const _secureStorage = FlutterSecureStorage();
  static const _uuid = Uuid();
  static const _micConfig = RecordConfig(
    encoder: AudioEncoder.pcm16bits,
    sampleRate: 16000,
    numChannels: 1,
    echoCancel: true,
    noiseSuppress: true,
    androidConfig: AndroidRecordConfig(
      audioSource: AndroidAudioSource.voiceCommunication,
    ),
  );

  final AudioRecorder _recorder = AudioRecorder();
  final FlutterSoundPlayer _player = FlutterSoundPlayer();
  final List<ChatMessage> messages = [];
  final List<int> _pendingMicBytes = [];
  final WakeWordDetector wakeWordDetector = WakeWordDetector();

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _socketSubscription;
  StreamSubscription<Uint8List>? _micSubscription;
  Future<void> _audioEventQueue = Future<void>.value();
  SimpleOpusEncoder? _encoder;
  SimpleOpusDecoder? _decoder;
  Completer<void>? _helloCompleter;
  SharedPreferences? _preferences;

  String endpoint = defaultEndpoint;
  String token = defaultAccessToken;
  String deviceId = '';
  String clientId = '';
  String cameraFacing = 'back';
  String localVlUrl = defaultVisionUrl;
  String vlApiKey = '';
  String visionUrl = defaultVisionUrl;
  String visionToken = '';
  String smartHomeBroker = '';
  int smartHomePort = 1883;
  String smartHomeUsername = '';
  String smartHomePassword = '';
  bool smartHomeUseTls = false;
  String smartHomeDevicesJson = '[]';
  List<String> disabledMcpModules = [];
  bool autoConversation = false;
  bool autoSessionActive = false;
  /// Mirrors WAKE_WORD_OPTIONS in src/utils/config_manager.py. The wake word
  /// starts a conversation and interrupts TTS, exactly like src/plugins/wake_word.py.
  WakeWordOptions wakeWordOptions = const WakeWordOptions();
  /// True while the wake word detector is armed and listening. Separate from
  /// WakeWordDetector.isRunning so the UI can show "status listening" even while
  /// the spotter is briefly paused for a cooldown or a TTS window.
  bool wakeWordListening = false;
  String wakeWordError = '';
  String status = 'Belum terhubung';
  /// Current emotion name, mirroring mainModel.emotionUrl in the desktop GUI.
  /// Driven by the `llm` message's `emotion` field (see UiPresenter
  /// show_protocol_message) and reset to "neutral" on device-state changes, like
  /// UiPresenter.show_device_state does. Empty while disconnected so the UI can
  /// show the dimmed placeholder from EmotionDisplay.qml.
  String emotion = '';
  /// Live assistant text, mirroring mainModel.ttsText in the desktop GUI: the
  /// streaming TTS text the chat panel shows under the emotion animation while
  /// the assistant is speaking.
  String liveText = '';
  String sessionId = '';
  String _assistantText = '';
  String? _pendingTextAttachment;
  Uint8List? _pendingImageAttachment;
  bool _pendingImageAsDocument = false;
  String _pendingImageQuestion = '';
  String? _pendingAttachmentName;
  int? _assistantMessageIndex;
  /// Opus decode sample rate. The server's encode rate is configurable
  /// (AUDIO_DEVICES.opus_output_sample_rate: 24000 official / 16000 third-party);
  /// the decoder must match it, otherwise frames decode at the wrong size and TTS
  /// sounds distorted. Mirrors src/ui/tui/settings_data.py.
  int _outputSampleRate = 24000;
  static const List<int> supportedOutputSampleRates = [24000, 16000];
  int get outputSampleRate => _outputSampleRate;
  bool isConnected = false;
  bool isConnecting = false;
  bool isRecording = false;
  bool _wakeWordMicActive = false;
  bool isSpeaking = false;
  bool _opusInitialized = false;
  bool _playerStarted = false;
  bool mcpInitialized = false;
  bool mcpToolsListed = false;

  /// Extracts the text payload of a tools/call result, mirroring the shape
  /// McpRuntime returns (`result.content[0].text`).
  static String? _extractResultText(Map result) {
    final content = result['content'];
    if (content is! List || content.isEmpty) return null;
    final first = content.first;
    if (first is Map && first['text'] is String) return first['text'] as String;
    return null;
  }

  /// Reads the persisted facing out of a self.camera.switch result so the UI and
  /// the next take_photo stay in sync with what the LLM selected.
  static String? _facingFromSwitchResult(String? text) {
    if (text == null || text.trim().isEmpty) return null;
    try {
      final decoded = jsonDecode(text);
      if (decoded is! Map) return null;
      final success = decoded['success'];
      final facing = decoded['facing'];
      if (success == true && facing is String && (facing == 'front' || facing == 'back')) {
        return facing;
      }
    } on FormatException {
      return null;
    }
    return null;
  }

  /// Jitter buffer for incoming TTS PCM frames. Network frames arrive at an uneven
  /// pace; feeding them straight to the player one-by-one makes playback stutter
  /// ("brebet"). Releasing them in fixed-size bursts smooths that out.
  ///
  /// Latency matters as much as smoothness: a large burst or a large prebuffer
  /// holds back the first syllables, so in auto mode the answer sounds like it only
  /// starts near the end of the sentence. Keep both small.
  static const int _ttsFeedFrames = 2;
  /// Mirrors _TTS_FIFO_MAX_S = 10.0 in src/audio_codecs/audio_codec.py. This is a
  /// safety valve for a stalled consumer, not a jitter target: it has to be large
  /// that a slow feed never drops the *head* of an utterance. Dropping the head is
  /// exactly what makes TTS sound like it begins mid-sentence.
  static const int _ttsBufferMaxFrames = 500;
  /// Frames to accumulate before the first feed of an utterance, mirroring
  /// _TTS_PREBUFFER_S in src/audio_codecs/audio_codec.py but kept smaller: the
  /// Android OS playback buffer already adds its own start-up latency on top, so a
  /// full 0.12s hold here delays the first syllable noticeably.
  static const int _ttsPrebufferFrames = 2;
  /// Wall-clock cap for a single player feed. feedInt16FromStream can exert
  /// backpressure; without a cap a future that never returns leaves _ttsFeeding true
  /// forever, every later frame is skipped, and only the tail of the sentence plays.
  static const Duration _ttsFeedTimeout = Duration(milliseconds: 500);
  final List<Int16List> _ttsBuffer = [];
  bool _ttsFeeding = false;
  bool _ttsNeedsPrebuffer = true;
  bool _discardTtsAudioUntilStart = false;

  /// Mirrors src/plugins/audio.py: while the speaker is still draining its TTS
  /// buffer the microphone must stay suppressed, otherwise in auto/realtime mode
  /// the assistant hears its own voice out of the speaker and answers itself.
  static const Duration _silencePeriodMax = Duration(seconds: 3);
  static const Duration _silencePeriodHold = Duration(milliseconds: 900);
  bool _suppressMic = false;
  /// True when the mic recorder was started by auto conversation rather than by
  /// the user pressing "Mulai bicara", so disabling auto conversation can stop it
  /// again without touching a manually started recording.
  bool _micAutoManaged = false;
  Timer? _silencePollTimer;
  DateTime? _lastTtsFrameAt;

  Future<void> initialize() async {
    await McpRuntime.initializeNotifications();
    _preferences = await SharedPreferences.getInstance();
    final storedEndpoint = _preferences?.getString('server_url')?.trim() ?? '';
    endpoint = storedEndpoint.isEmpty ? defaultEndpoint : storedEndpoint;
    cameraFacing = _preferences?.getString('camera_facing') ?? 'back';
    deviceId = _preferences?.getString('device_id') ?? '';
    clientId = _preferences?.getString('client_id') ?? '';
    final storedLocalVlUrl = _preferences?.getString('camera_local_vl_url')?.trim() ?? '';
    localVlUrl = storedLocalVlUrl.isEmpty ? defaultVisionUrl : storedLocalVlUrl;
    final storedVisionUrl = _preferences?.getString('camera_explain_url')?.trim() ?? '';
    visionUrl = storedVisionUrl.isEmpty ? defaultVisionUrl : storedVisionUrl;
    smartHomeBroker = _preferences?.getString('smart_home_broker') ?? '';
    smartHomePort = _preferences?.getInt('smart_home_port') ?? 1883;
    smartHomeUsername = _preferences?.getString('smart_home_username') ?? '';
    smartHomeUseTls = _preferences?.getBool('smart_home_use_tls') ?? false;
    smartHomeDevicesJson = _preferences?.getString('smart_home_devices') ?? '[]';
    disabledMcpModules = _preferences?.getStringList('mcp_disabled_modules') ?? [];
    autoConversation = _preferences?.getBool('auto_conversation') ?? false;
    final storedRate = _preferences?.getInt('opus_output_sample_rate') ?? 24000;
    _outputSampleRate = supportedOutputSampleRates.contains(storedRate) ? storedRate : 24000;
    wakeWordOptions = WakeWordOptions(
      enabled: _preferences?.getBool('wake_word_enabled') ?? true,
      wakeWord: _preferences?.getString('wake_word_text') ?? 'Hello Xiaozhi',
    );
    final storedToken = (await _secureStorage.read(key: 'access_token'))?.trim() ?? '';
    token = storedToken.isEmpty ? defaultAccessToken : storedToken;
    vlApiKey = await _secureStorage.read(key: 'camera_vl_api_key') ?? '';
    visionToken = await _secureStorage.read(key: 'camera_explain_token') ?? '';
    smartHomePassword = await _secureStorage.read(key: 'smart_home_password') ?? '';
    if (deviceId.isEmpty) {
      deviceId = _uuid.v4().replaceAll('-', '');
      await _preferences?.setString('device_id', deviceId);
    }
    if (clientId.isEmpty) {
      clientId = _uuid.v4();
      await _preferences?.setString('client_id', clientId);
    }
    notifyListeners();
  }

  Future<void> saveSettings({
    required String newEndpoint,
    required String newToken,
    required String newDeviceId,
    required String newClientId,
    required String newCameraFacing,
    required String newLocalVlUrl,
    required String newVlApiKey,
    required String newVisionUrl,
    required String newVisionToken,
    required String newSmartHomeBroker,
    required int newSmartHomePort,
    required String newSmartHomeUsername,
    required String newSmartHomePassword,
    required bool newSmartHomeUseTls,
    required String newSmartHomeDevicesJson,
    required List<String> newDisabledMcpModules,
    required bool newAutoConversation,
    required int newOutputSampleRate,
    required bool newWakeWordEnabled,
    required String newWakeWordText,
  }) async {
    await disconnect();
    endpoint = newEndpoint.trim().isEmpty ? defaultEndpoint : newEndpoint.trim();
    token = newToken.trim().isEmpty ? defaultAccessToken : newToken.trim();
    deviceId = newDeviceId.trim().isEmpty
      ? _uuid.v4().replaceAll('-', '')
      : newDeviceId.trim();
    clientId = newClientId.trim().isEmpty ? _uuid.v4() : newClientId.trim();
    cameraFacing = newCameraFacing == 'front' ? 'front' : 'back';
    localVlUrl = newLocalVlUrl.trim().isEmpty ? defaultVisionUrl : newLocalVlUrl.trim();
    vlApiKey = newVlApiKey.trim();
    visionUrl = newVisionUrl.trim().isEmpty ? defaultVisionUrl : newVisionUrl.trim();
    visionToken = newVisionToken.trim();
    smartHomeBroker = newSmartHomeBroker.trim();
    smartHomePort = newSmartHomePort.clamp(1, 65535).toInt();
    smartHomeUsername = newSmartHomeUsername.trim();
    smartHomePassword = newSmartHomePassword;
    smartHomeUseTls = newSmartHomeUseTls || smartHomePort == 8883;
    smartHomeDevicesJson = newSmartHomeDevicesJson.trim().isEmpty
      ? '[]'
      : newSmartHomeDevicesJson.trim();
    disabledMcpModules = List.of(newDisabledMcpModules);
    autoConversation = newAutoConversation;
    autoSessionActive = false;
    await _preferences?.setString('server_url', endpoint);
    await _preferences?.setString('device_id', deviceId);
    await _preferences?.setString('client_id', clientId);
    await _preferences?.setString('camera_facing', cameraFacing);
    await _preferences?.setString('camera_local_vl_url', localVlUrl);
    await _preferences?.setString('camera_explain_url', visionUrl);
    await _preferences?.setString('smart_home_broker', smartHomeBroker);
    await _preferences?.setInt('smart_home_port', smartHomePort);
    await _preferences?.setString('smart_home_username', smartHomeUsername);
    await _preferences?.setBool('smart_home_use_tls', smartHomeUseTls);
    await _preferences?.setString('smart_home_devices', smartHomeDevicesJson);
    await _preferences?.setStringList('mcp_disabled_modules', disabledMcpModules);
    await _preferences?.setBool('auto_conversation', autoConversation);
    await setOutputSampleRate(newOutputSampleRate);
    // disconnect() above stopped the detector and cleared isConnected, so arm it
    // directly here; setWakeWordOptions would no-op because it sees no connection.
    await setWakeWordOptions(enabled: newWakeWordEnabled, wakeWord: newWakeWordText);
    if (wakeWordOptions.enabled) {
      await _startWakeWordDetector();
    }
    await _secureStorage.write(key: 'access_token', value: token);
    await _secureStorage.write(key: 'camera_vl_api_key', value: vlApiKey);
    await _secureStorage.write(key: 'camera_explain_token', value: visionToken);
    await _secureStorage.write(key: 'smart_home_password', value: smartHomePassword);
    status = 'Pengaturan tersimpan';
    notifyListeners();
  }

  Future<void> setAutoConversation(bool enabled) async {
    if (!isConnected) {
      autoConversation = false;
      autoSessionActive = false;
      status = 'Hubungkan perangkat terlebih dahulu untuk auto conversation';
      notifyListeners();
      return;
    }
    autoConversation = enabled;
    await _preferences?.setBool('auto_conversation', autoConversation);
    if (enabled) {
      autoSessionActive = true;
      _sendJson(ProtocolMessages.listenStart(sessionId, 'realtime'));
      // Realtime mode needs a live microphone for the whole session. The uplink is
      // gated by _suppressMic while the assistant speaks (see _beginTtsPlayback), so
      // the state machine ends up exactly as expected: speaking -> the answer plays
      // out of the speaker, listening -> the mic reaches the server. Without starting
      // the recorder here the server waits for user audio that never arrives once
      // the assistant stops talking.
      if (!isRecording) {
        await startVoice();
        _micAutoManaged = isRecording;
      }
      status = 'Auto conversation aktif';
    } else {
      autoSessionActive = false;
      _sendJson(ProtocolMessages.listenStop(sessionId));
      if (_micAutoManaged && isRecording) {
        await stopVoice();
      }
      _micAutoManaged = false;
      status = 'Auto conversation dimatikan';
    }
    notifyListeners();
  }

  Future<void> toggleAutoConversation() async {
    await setAutoConversation(!autoConversation);
  }

  /// Applies new wake word settings and persists them, mirroring the
  /// CONFIG_CHANGED handler in src/plugins/wake_word.py which hot-reloads the model.
  /// Passing [enabled] or [wakeWord] as null leaves the current value untouched.
  Future<void> setWakeWordOptions({bool? enabled, String? wakeWord}) async {
    final wasEnabled = wakeWordOptions.enabled;
    wakeWordOptions = wakeWordOptions.copyWith(
      enabled: enabled ?? wakeWordOptions.enabled,
      wakeWord: wakeWord?.trim().isNotEmpty == true ? wakeWord!.trim() : wakeWordOptions.wakeWord,
    );
    await _preferences?.setBool('wake_word_enabled', wakeWordOptions.enabled);
    await _preferences?.setString('wake_word_text', wakeWordOptions.wakeWord);
    wakeWordError = '';
    // Only reload when something that affects the loaded model changed; flipping
    // the text while disabled just waits for the next connect.
    if (wakeWordOptions.enabled) {
      if (isConnected) {
        await _startWakeWordDetector();
      }
    } else if (wasEnabled) {
      await _stopWakeWordDetector();
    }
    notifyListeners();
  }

  /// Arms the wake word detector. Mirrors WakeWordPlugin.start: the model loads
  /// here, and the detector subscribes to the audio stream.
  Future<void> _startWakeWordDetector() async {
    if (!isConnected) return;
    wakeWordDetector.onDetected = _handleWakeWordDetected;
    final result = await wakeWordDetector.start(wakeWordOptions);
    switch (result) {
      case WakeWordStartResult.started:
        try {
          if (!isRecording && !_wakeWordMicActive) await _openMicStream();
          _wakeWordMicActive = !isRecording;
          wakeWordListening = true;
          wakeWordError = '';
          if (!isRecording) status = 'Wake word aktif';
        } catch (error) {
          await wakeWordDetector.stop();
          wakeWordListening = false;
          wakeWordError = error.toString();
          status = 'Wake word gagal dimulai: $wakeWordError';
        }
        break;
      case WakeWordStartResult.disabled:
        wakeWordListening = false;
        break;
      case WakeWordStartResult.invalidKeyword:
      case WakeWordStartResult.modelMissing:
      case WakeWordStartResult.initFailed:
        wakeWordListening = false;
        wakeWordError = wakeWordDetector.lastError;
        status = 'Wake word gagal dimulai: $wakeWordError';
        break;
    }
  }

  Future<void> _stopWakeWordDetector() async {
    await wakeWordDetector.stop();
    wakeWordListening = false;
    if (_wakeWordMicActive) await _closeMicStream();
  }

  /// Mirrors WakeWordPlugin._on_detected: interrupt TTS, or start a voice session
  /// so the speech following the wake word reaches the server.
  void _handleWakeWordDetected(String keyword) {
    if (!isConnected) return;
    if (isSpeaking) {
      // AbortReason.WAKE_WORD_DETECTED + clear_audio_queue in Python.
      _sendJson(ProtocolMessages.abort(sessionId, 'wake_word_detected'));
      _silencePollTimer?.cancel();
      _suppressMic = false;
      _ttsBuffer.clear();
      _ttsFeeding = false;
      _ttsNeedsPrebuffer = true;
      if (_playerStarted) {
        _player.stopPlayer().then((_) {
          if (isConnected) _startPlayerStream();
        });
        _playerStarted = false;
      }
      isSpeaking = false;
      status = 'Wake word terdeteksi, TTS dihentikan';
      notifyListeners();
      // The abort flow needs the mic live so the user's next sentence is heard.
      // startVoice is a no-op when already recording.
      startVoice();
      return;
    }
    // Not speaking: tell the server the wake word fired and open the mic, so the
    // conversation starts without a tap (send_wake_word_detected + start_listening
    // with AUTO_STOP, since Android has no AEC).
    if (!isRecording) {
      unawaited(startVoice());
    }
    _sendJson(ProtocolMessages.detectText(sessionId, wakeWordOptions.wakeWord));
    status = 'Wake word terdeteksi: ${wakeWordOptions.wakeWord}';
    notifyListeners();
  }

  Future<void> connect() async {
    if (isConnected || isConnecting) return;
    if (endpoint.isEmpty || token.isEmpty || deviceId.isEmpty || clientId.isEmpty) {
      status = 'Lengkapi koneksi di Pengaturan';
      notifyListeners();
      return;
    }

    final uri = Uri.tryParse(endpoint);
    if (uri == null || !{'ws', 'wss'}.contains(uri.scheme) || uri.host.isEmpty) {
      status = 'Alamat server harus dimulai dengan ws:// atau wss://';
      notifyListeners();
      return;
    }

    isConnecting = true;
    status = 'Menghubungkan...';
    _helloCompleter = Completer<void>();
    notifyListeners();

    try {
      final channel = IOWebSocketChannel.connect(
        uri,
        headers: {
          'Authorization': 'Bearer $token',
          'Protocol-Version': '1',
          'Device-Id': deviceId,
          'Client-Id': clientId,
        },
        pingInterval: const Duration(seconds: 20),
        connectTimeout: const Duration(seconds: 12),
      );
      _channel = channel;
      _socketSubscription = channel.stream.listen(
        _handleSocketMessage,
        onError: (Object error) => _handleConnectionFailure(error.toString()),
        onDone: () {
          if (isConnected || isConnecting) {
            _handleConnectionFailure('Koneksi server ditutup');
          }
        },
      );
      await channel.ready.timeout(const Duration(seconds: 15));
      channel.sink.add(ProtocolMessages.hello());
      await _helloCompleter!.future.timeout(const Duration(seconds: 15));
      isConnected = true;
      isConnecting = false;
      status = 'Terhubung';
      // Mirrors UiPresenter.show_device_state, which resets the emotion to
      // "neutral" whenever the device state changes.
      emotion = EmotionService.hasAnimation(emotion) ? emotion : 'neutral';
      liveText = '';
      // Arm the wake word detector after the handshake so the model load does not
      // compete with the connection for CPU (WakeWordPlugin.start runs after the
      // protocol is up for the same reason).
      if (wakeWordOptions.enabled) {
        await _startWakeWordDetector();
      }
    } catch (error) {
      await _closeSocket();
      isConnected = false;
      isConnecting = false;
      status = 'Gagal terhubung: ${error.toString()}';
      emotion = '';
      liveText = '';
    }
    notifyListeners();
  }

  Future<void> disconnect() async {
    if (isRecording) await stopVoice();
    await _stopWakeWordDetector();
    autoConversation = false;
    autoSessionActive = false;
    _micAutoManaged = false;
    isConnected = false;
    isConnecting = false;
    mcpInitialized = false;
    mcpToolsListed = false;
    _silencePollTimer?.cancel();
    _suppressMic = false;
    _ttsBuffer.clear();
    await _closeSocket();
    if (_playerStarted) {
      await _player.stopPlayer();
      _playerStarted = false;
    }
    sessionId = '';
    status = 'Terputus';
    // Back to the dimmed placeholder, like the disconnected desktop GUI.
    emotion = '';
    liveText = '';
    notifyListeners();
  }

  Future<bool> sendText(String value) async {
    final text = value.trim();
    if (!isConnected || text.isEmpty) return false;
    final isLongText = text.length > 31;
    return _sendChatRequest(
      displayText: text,
      detectText: isLongText ? 'baca lampiran' : text,
      pendingText: isLongText ? text : null,
    );
  }

  Future<bool> sendTextAttachment({
    required String fileName,
    required String content,
    required String question,
  }) {
    final request = question.trim().isEmpty
        ? 'Baca dan jawab isi lampiran ini.'
        : question.trim();
    return _sendChatRequest(
      displayText: 'Lampiran teks: $fileName\n$request',
      detectText: 'baca lampiran',
      pendingText: 'Nama file: $fileName\nPermintaan pengguna: $request\n\n$content',
      attachmentName: fileName,
    );
  }

  Future<bool> sendImageAttachment({
    required String fileName,
    required Uint8List imageBytes,
    required String question,
  }) {
    if (imageBytes.isEmpty) return Future.value(false);
    final userQuestion = question.trim();
    final request = userQuestion.isEmpty ? 'analisa' : userQuestion;
    final shouldUseDocumentTool =
      userQuestion.isEmpty ||
      userQuestion.length > 31 ||
      _isTextHeavyImageRequest(userQuestion);
    if (shouldUseDocumentTool) {
      return _sendChatRequest(
        displayText: 'Lampiran gambar: $fileName\n$request',
        detectText: 'baca lampiran',
        imageBytes: imageBytes,
        imageQuestion: userQuestion,
        attachmentName: fileName,
        pendingImageAsDocument: true,
      );
    }
    return _sendChatRequest(
      displayText: 'Lampiran gambar: $fileName\n$request',
      detectText: 'analisa gambar',
      imageBytes: imageBytes,
      imageQuestion: request,
      attachmentName: fileName,
    );
  }

  /// True when an image request is really about reading text (OCR), so it must be
  /// routed through the document reader instead of the vision model. Mirrors
  /// src/plugins/ui_session.py `_should_use_document_tool_for_image`.
  static const List<String> _textHeavyImageKeywords = [
    'ocr', 'read text', 'baca teks', 'baca tulisan', 'baca kuitansi', 'baca struk',
    'baca slip', 'baca nota', 'cek struk', 'cek kuitansi', 'cek nota', 'extract text',
    'what is written', 'what does it say', 'struk', 'receipt', 'bank note', 'invoice',
    'nota', 'kuitansi', 'slip', 'transfer', 'nomor rekening', 'rekening', 'kode',
    'read the text', 'text on the image', 'text in this image', 'tulisan', 'nomor',
    'transaksi', 'dokumen', 'total bayar', 'jumlah pembayaran', 'jumlah transfer',
    'faktur', 'bukti pembayaran', 'bukti transfer', 'saldo', 'nominal', 'norek',
    'no rekening', 'no. rekening',
  ];

  static bool _isTextHeavyImageRequest(String question) {
    final q = question.trim().toLowerCase();
    if (q.isEmpty) return false;
    return _textHeavyImageKeywords.any((keyword) => q.contains(keyword));
  }

  Future<bool> _sendChatRequest({
    required String displayText,
    required String detectText,
    String? pendingText,
    Uint8List? imageBytes,
    String imageQuestion = '',
    String attachmentName = '',
    bool pendingImageAsDocument = false,
  }) async {
    if (!isConnected) return false;
    await _interruptTtsForTextInput();
    _pendingTextAttachment = pendingText == null
        ? null
        : pendingText.length > maxTextAttachmentChars
            ? '${pendingText.substring(0, maxTextAttachmentChars)}\n[Attachment content truncated]'
            : pendingText;
    _pendingImageAttachment = imageBytes;
    _pendingImageAsDocument = pendingImageAsDocument;
    _pendingImageQuestion = imageQuestion;
    final hasAttachment = pendingText != null || imageBytes != null;
    _pendingAttachmentName = hasAttachment ? attachmentName : null;
    messages.add(ChatMessage(text: displayText, isUser: true));
    status = hasAttachment ? 'Mengirim attachment...' : 'Menunggu jawaban...';
    notifyListeners();
    _sendJson(ProtocolMessages.listenStart(sessionId, autoConversation ? 'realtime' : 'manual'));
    _sendJson(ProtocolMessages.detectText(sessionId, detectText));
    return true;
  }

  Future<void> startVoice() async {
    if (!isConnected || isRecording) return;
    if (_wakeWordMicActive) {
      _wakeWordMicActive = false;
      isRecording = true;
      _sendJson(ProtocolMessages.listenStart(sessionId, autoConversation ? 'realtime' : 'manual'));
      status = 'Mendengarkan...';
      emotion = 'neutral';
      liveText = '';
      notifyListeners();
      return;
    }
    try {
      await _openMicStream();
      _sendJson(ProtocolMessages.listenStart(sessionId, autoConversation ? 'realtime' : 'manual'));
      isRecording = true;
      status = 'Mendengarkan...';
      // DeviceState.LISTENING in the desktop GUI resets the emotion to neutral.
      emotion = 'neutral';
      liveText = '';
    } catch (error) {
      status = 'Mikrofon gagal dimulai: ${error.toString()}';
    }
    notifyListeners();
  }

  Future<void> stopVoice() async {
    if (!isRecording) return;
    isRecording = false;
    _micAutoManaged = false;
    _sendJson(ProtocolMessages.listenStop(sessionId));
    if (isConnected && wakeWordOptions.enabled && wakeWordListening) {
      _wakeWordMicActive = true;
      status = 'Wake word aktif';
    } else {
      await _closeMicStream();
      status = 'Menunggu jawaban...';
    }
    notifyListeners();
  }

  Future<void> _openMicStream() async {
    if (!await _recorder.hasPermission()) {
      throw StateError('Izin mikrofon diperlukan');
    }
    _pendingMicBytes.clear();
    final stream = await _recorder.startStream(_micConfig);
    _micSubscription = stream.listen(_handleMicData);
  }

  Future<void> _closeMicStream() async {
    _wakeWordMicActive = false;
    await _micSubscription?.cancel();
    _micSubscription = null;
    try {
      if (await _recorder.isRecording()) await _recorder.stop();
    } catch (_) {}
    _pendingMicBytes.clear();
  }

  Future<void> interrupt() async {
    if (!isConnected) return;
    await _interruptTtsForTextInput();
    if (isRecording) {
      if (!autoConversation) {
        _sendJson(ProtocolMessages.listenStart(sessionId, 'manual'));
      }
      status = 'Mendengarkan...';
      notifyListeners();
    } else {
      await startVoice();
    }
    if (autoConversation) _micAutoManaged = isRecording;
  }

  void _handleSocketMessage(dynamic message) {
    if (message is String) {
      try {
        _handleJson(jsonDecode(message) as Map<String, dynamic>);
      } on FormatException {
        status = 'Pesan server tidak valid';
        notifyListeners();
      }
    } else if (message is List<int>) {
      final packet = Uint8List.fromList(message);
      _audioEventQueue = _audioEventQueue.then((_) async {
        try {
          await _handleAudio(packet);
        } catch (_) {
          status = 'Paket audio tidak dapat diputar';
          notifyListeners();
        }
      });
    }
  }

  Future<void> _handleJson(Map<String, dynamic> data) async {
    final receivedSessionId = data['session_id'];
    if (receivedSessionId is String && receivedSessionId.isNotEmpty) {
      sessionId = receivedSessionId;
    }

    final type = data['type'];
    if (type == 'hello') {
      if (data['transport'] != 'websocket') {
        _handleConnectionFailure('Server tidak mendukung WebSocket');
        return;
      }
      try {
        await _initializeAudio();
        if (!(_helloCompleter?.isCompleted ?? true)) {
          _helloCompleter!.complete();
        }
      } catch (error) {
        _handleConnectionFailure('Audio tidak dapat disiapkan: $error');
      }
      return;
    }

    if (type == 'mcp') {
      final payload = data['payload'];
      Map<String, dynamic>? request;

      if (payload is Map) {
        request = Map<String, dynamic>.from(payload);
      } else if (payload is String && payload.trim().isNotEmpty) {
        try {
          final decoded = jsonDecode(payload);
          if (decoded is Map) {
            request = Map<String, dynamic>.from(decoded);
          }
        } on FormatException {
          status = 'Payload MCP tidak valid';
          notifyListeners();
          return;
        }
      }

      if (request != null) {
        final response = await McpRuntime.handle(
          request,
          disabledModules: disabledMcpModules.toSet(),
          pendingTextAttachment: _pendingTextAttachment,
          pendingImageAttachment: _pendingImageAttachment,
          pendingImageAsDocument: _pendingImageAsDocument,
          pendingImageQuestion: _pendingImageQuestion,
          pendingAttachmentName: _pendingAttachmentName ?? '',
          smartHomeConfig: {
            'broker': smartHomeBroker,
            'port': smartHomePort,
            'username': smartHomeUsername,
            'password': smartHomePassword,
            'useTls': smartHomeUseTls,
            'devices': smartHomeDevicesJson,
          },
          visionConfig: {
            'cameraFacing': cameraFacing,
            'localVlUrl': localVlUrl,
            'vlApiKey': vlApiKey,
            'visionUrl': visionUrl,
            'visionToken': visionToken,
            'deviceId': deviceId,
            'clientId': clientId,
          },
        );
        if (response != null) {
          final params = request['params'];
          final method = request['method'];
          if (method == 'initialize') {
            mcpInitialized = true;
          } else if (method == 'tools/list') {
            mcpToolsListed = true;
          }
          if (method == 'tools/call' &&
              params is Map &&
              response['result'] is Map &&
              params['name'] == 'self.camera.switch') {
            final text = _extractResultText(response['result'] as Map);
            final facing = _facingFromSwitchResult(text);
            if (facing != null) {
              cameraFacing = facing;
              await _preferences?.setString('camera_facing', cameraFacing);
              notifyListeners();
            }
          }
          if (method == 'tools/call' &&
              params is Map &&
              (params['name'] == 'manage_document' || params['name'] == 'take_photo') &&
              response['result'] is Map) {
            _pendingTextAttachment = null;
            _pendingImageAttachment = null;
            _pendingImageAsDocument = false;
            _pendingImageQuestion = '';
            _pendingAttachmentName = null;
          }
          _sendJson({
            'type': 'mcp',
            'session_id': sessionId,
            'payload': response,
          });
        }
      }
      return;
    }

    if (type == 'stt') {
      final text = data['text'];
      if (text is String && text.trim().isNotEmpty) {
        if (messages.isEmpty || !messages.last.isUser || messages.last.text != text) {
          messages.add(ChatMessage(text: text, isUser: true));
        }
        // Mirrors UiPresenter.show_protocol_message: stt text also updates the
        // live chat panel line (mainModel.ttsText) in the desktop GUI.
        liveText = text;
      }
    } else if (type == 'tts') {
      final state = data['state'];
      if (state == 'start') {
        isSpeaking = true;
        _discardTtsAudioUntilStart = false;
        _assistantText = '';
        _assistantMessageIndex = null;
        status = mcpToolsListed ? 'Xiaozhi sedang berbicara' : 'MCP belum siap';
        // Android has no AEC, so unlike the Python client (which keeps the mic open
        // during SPEAKING only when AEC is enabled) we must gate the mic for the
        // whole playback window. Otherwise the speaker output leaks into the mic and
        // the server's VAD interrupts the assistant mid-sentence in realtime mode.
        _beginTtsPlayback();
        _ttsNeedsPrebuffer = true;
        notifyListeners();
      }
      final text = data['text'];
      if (text is String && text.isNotEmpty) {
        _appendAssistantText(text);
        liveText = _assistantText;
        notifyListeners();
      }
      if (state == 'stop') {
        isSpeaking = false;
        status = 'Terhubung';
        _assistantMessageIndex = null;
        unawaited(_handleTtsStop());
      }
    } else if (type == 'llm') {
      // Mirrors UiPresenter.show_protocol_message's llm branch: the LLM's emotion
      // updates the animation shown next to the chat text.
      final value = data['emotion'];
      if (value is String && value.trim().isNotEmpty) {
        emotion = EmotionService.normalize(value);
        notifyListeners();
      }
    } else if (type == 'error') {
      status = (data['message'] ?? data['error'] ?? 'Kesalahan server').toString();
    }
    notifyListeners();
  }

  void _appendAssistantText(String text) {
    if (_assistantMessageIndex == null || _assistantMessageIndex! >= messages.length) {
      _assistantText = text;
      messages.add(ChatMessage(text: _assistantText, isUser: false));
      _assistantMessageIndex = messages.length - 1;
    } else {
      _assistantText = appendTtsSegment(_assistantText, text);
      messages[_assistantMessageIndex!] =
          messages[_assistantMessageIndex!].copyWith(text: _assistantText);
    }
  }


  Future<void> _interruptTtsForTextInput() async {
    if (!isSpeaking && !_suppressMic) return;
    _sendJson(ProtocolMessages.abort(sessionId));
    _silencePollTimer?.cancel();
    _suppressMic = false;
    _discardTtsAudioUntilStart = true;
    _ttsBuffer.clear();
    _ttsNeedsPrebuffer = true;
    isSpeaking = false;
    if (_playerStarted) {
      try {
        await _player.stopPlayer();
      } catch (_) {}
      _playerStarted = false;
      try {
        if (isConnected) await _startPlayerStream();
      } catch (_) {}
    }
  }
  @visibleForTesting
  static String appendTtsSegment(String current, String segment) {
    if (current.isEmpty || current.endsWith('\n') || segment.startsWith('\n')) {
      return '$current$segment';
    }
    return '$current\n$segment';
  }

  Future<void> _initializeAudio() async {
    if (!_opusInitialized) {
      initOpus(await opus_flutter.load());
      _encoder = SimpleOpusEncoder(
        sampleRate: 16000,
        channels: 1,
        application: Application.voip,
      );
      _decoder = SimpleOpusDecoder(sampleRate: _outputSampleRate, channels: 1);
      _opusInitialized = true;
    }
    if (!_player.isOpen()) await _player.openPlayer();
    await _startPlayerStream();
  }

  /// Changes the Opus decode sample rate. The decoder and the player stream are
  /// bound to this rate, so both must be rebuilt when it changes.
  Future<void> setOutputSampleRate(int rate) async {
    if (!supportedOutputSampleRates.contains(rate) || rate == _outputSampleRate) return;
    _outputSampleRate = rate;
    await _preferences?.setInt('opus_output_sample_rate', rate);
    if (_opusInitialized) {
      _decoder?.destroy();
      _decoder = SimpleOpusDecoder(sampleRate: rate, channels: 1);
    }
    if (_playerStarted) {
      await _player.stopPlayer();
      _playerStarted = false;
      await _startPlayerStream();
    }
    notifyListeners();
  }

  Future<void> _startPlayerStream() async {
    await _player.startPlayerFromStream(
      codec: Codec.pcm16,
      numChannels: 1,
      sampleRate: _outputSampleRate,
      interleaved: false,
      // 8192 bytes is ~170ms of PCM16 at 24kHz; the OS will not emit a sound until
      // this much is queued, which alone delays the start of every answer. 2048
      // bytes (~43ms) is small enough to start promptly while the 2-frame prebuffer
      // keeps it from underrunning.
      bufferSize: 2048,
    );
    _playerStarted = true;
  }

  void _handleMicData(Uint8List data) {
    // The wake word detector taps the same 16 kHz PCM16 stream the uplink uses,
    // so no second recorder is opened. It must see the audio even while the uplink
    // is suppressed: the whole point of the wake word is to interrupt TTS, and
    // _suppressMic is true for that entire window.
    if (wakeWordDetector.isRunning) {
      wakeWordDetector.feed(data);
    }
    if (!isRecording) return;
    _pendingMicBytes.addAll(data);
    const frameBytes = 320 * 2;
    while (_pendingMicBytes.length >= frameBytes) {
      final bytes = Uint8List.fromList(_pendingMicBytes.take(frameBytes).toList());
      _pendingMicBytes.removeRange(0, frameBytes);
      final samples = Int16List.view(bytes.buffer, bytes.offsetInBytes, 320);
      final packet = _encoder?.encode(input: samples);
      // During the post-TTS silence period the speaker is still draining; sending mic
      // audio now makes the assistant hear and answer itself in realtime mode.
      if (packet != null && isConnected && !_suppressMic) _channel?.sink.add(packet);
    }
  }

  Future<void> _handleAudio(Uint8List packet) async {
    if (_discardTtsAudioUntilStart) return;
    final decoder = _decoder;
    if (decoder == null || packet.isEmpty) return;
    if (!_playerStarted && isSpeaking) {
      // The stream is torn down by an interrupt or a sample-rate change while the
      // server keeps sending frames; re-arm it instead of dropping the utterance.
      // Gated on isSpeaking so an abort (which clears that flag) cannot resurrect
      // playback of frames that are still in flight.
      try {
        if (!_player.isOpen()) await _player.openPlayer();
        await _startPlayerStream();
      } catch (_) {
        return;
      }
    }
    try {
      final pcm = decoder.decode(input: packet);
      if (pcm.isEmpty) return;
      _lastTtsFrameAt = DateTime.now();
      _ttsBuffer.add(pcm);
      // Guard the buffer: if the consumer stalls, never grow unbounded. The 10s cap
      // means this only trips on a genuine stall, and dropping the oldest then
      // matches PcmFifo.push in src/audio_codecs/audio_buffer.py.
      if (_ttsBuffer.length > _ttsBufferMaxFrames) {
        _ttsBuffer.removeRange(0, _ttsBuffer.length - _ttsBufferMaxFrames);
      }
      unawaited(_feedTtsFromBuffer());
    } catch (_) {
      status = 'Paket audio tidak dapat diputar';
      notifyListeners();
    }
  }

  Future<void> _feedTtsFromBuffer() async {
    if (_ttsFeeding) return;
    _ttsFeeding = true;
    try {
      // Hold the first frames of an utterance until the prebuffer is filled; the
      // player drains faster than the network delivers at speech onset. After that
      // the threshold is the burst size, so steady-state frames are batched and the
      // tts-stop flush handles whatever tail is left over.
      final threshold = _ttsNeedsPrebuffer ? _ttsPrebufferFrames : _ttsFeedFrames;
      while (_ttsBuffer.length >= threshold) {
        if (_ttsNeedsPrebuffer) _ttsNeedsPrebuffer = false;
        final burst = _ttsBuffer.sublist(0, _ttsFeedFrames);
        _ttsBuffer.removeRange(0, _ttsFeedFrames);
        final merged = _mergeInt16(burst);
        try {
          // The timeout is the important part: a feed that never returns would keep
          // _ttsFeeding true, so every later frame would be skipped and the
          // utterance would be reduced to what the tts-stop flush managed to push.
          await _player.feedInt16FromStream([merged]).timeout(_ttsFeedTimeout);
        } catch (_) {
          // The stream was closed or the feed stalled. Drop this burst and let the
          // next network frame retry instead of deadlocking the whole utterance.
          break;
        }
      }
    } finally {
      _ttsFeeding = false;
    }
  }

  Int16List _mergeInt16(List<Int16List> chunks) {
    final total = chunks.fold<int>(0, (sum, chunk) => sum + chunk.length);
    final merged = Int16List(total);
    var offset = 0;
    for (final chunk in chunks) {
      merged.setAll(offset, chunk);
      offset += chunk.length;
    }
    return merged;
  }

  /// Called when the server signals the end of a TTS utterance. The last frames may
  /// still be sitting in the jitter buffer or the OS playback buffer, so flush what
  /// we have and hold the mic off until playback has actually drained.
  Future<void> _handleTtsStop() async {
    await _flushTtsBuffer();
    _beginSilencePeriod();
  }

  /// Called on tts "start". The mic must stay suppressed for the entire playback
  /// window (not just the post-stop tail), because Android has no acoustic echo
  /// cancellation: any audio sent now is the assistant's own voice coming out of
  /// the speaker, which the server reads as user speech and interrupts.
  void _beginTtsPlayback() {
    _suppressMic = true;
    _silencePollTimer?.cancel();
    _lastTtsFrameAt = DateTime.now();
  }

  Future<void> _flushTtsBuffer() async {
    if (_ttsBuffer.isEmpty) return;
    final remaining = _mergeInt16(List<Int16List>.from(_ttsBuffer));
    _ttsBuffer.clear();
    try {
      await _player.feedInt16FromStream([remaining]).timeout(_ttsFeedTimeout);
      // The drain clock must start from the moment the final PCM actually reaches
      // the player, not from the last network frame, otherwise the hold can expire
      // while buffered audio is still playing out of the speaker.
      _lastTtsFrameAt = DateTime.now();
    } catch (_) {}
  }

  void _beginSilencePeriod() {
    _suppressMic = true;
    _silencePollTimer?.cancel();
    final startedAt = _lastTtsFrameAt ?? DateTime.now();
    final deadline = startedAt.add(_silencePeriodMax);
    _silencePollTimer = Timer.periodic(const Duration(milliseconds: 40), (timer) {
      final now = DateTime.now();
      // Safety cap, identical in spirit to _SILENCE_PERIOD_MAX_S in the Python client.
      if (now.isAfter(deadline) || !_playerStarted) {
        timer.cancel();
        _suppressMic = false;
        return;
      }
      // FlutterSound exposes no "buffer empty" query, so approximate "drained" by
      // holding the mic off for a fixed window after the final frame was fed.
      final lastFrame = _lastTtsFrameAt;
      if (lastFrame != null && now.difference(lastFrame) >= _silencePeriodHold) {
        timer.cancel();
        _suppressMic = false;
      }
    });
  }

  void _sendJson(Map<String, Object?> message) {
    // Send whenever the socket exists. The server sends its MCP "initialize" request
    // immediately after the hello handshake, before isConnected becomes true; gating on
    // it silently dropped that response, so the server never asked for tools/list and only
    // its own built-in tools stayed registered.
    final channel = _channel;
    if (channel != null) channel.sink.add(jsonEncode(message));
  }

  void _handleConnectionFailure(String reason) {
    isConnected = false;
    isConnecting = false;
    isRecording = false;
    wakeWordListening = false;
    _wakeWordMicActive = false;
    mcpInitialized = false;
    mcpToolsListed = false;
    _silencePollTimer?.cancel();
    _suppressMic = false;
    _ttsBuffer.clear();
    status = reason;
    if (!(_helloCompleter?.isCompleted ?? true)) {
      _helloCompleter!.completeError(StateError(reason));
    }
    unawaited(wakeWordDetector.stop());
    unawaited(_closeSocket());
    if (_playerStarted) unawaited(_stopPlayer());
    notifyListeners();
  }

  Future<void> _stopPlayer() async {
    await _player.stopPlayer();
    _playerStarted = false;
  }

  Future<void> _closeSocket() async {
    await _micSubscription?.cancel();
    _micSubscription = null;
    try {
      if (await _recorder.isRecording()) await _recorder.stop();
    } catch (_) {}
    await _socketSubscription?.cancel();
    _socketSubscription = null;
    await _channel?.sink.close();
    _channel = null;
  }

  @override
  void dispose() {
    _silencePollTimer?.cancel();
    unawaited(_socketSubscription?.cancel());
    unawaited(_micSubscription?.cancel());
    unawaited(_channel?.sink.close());
    unawaited(McpRuntime.dispose());
    _recorder.dispose();
    unawaited(_player.closePlayer());
    _encoder?.destroy();
    _decoder?.destroy();
    super.dispose();
  }
}