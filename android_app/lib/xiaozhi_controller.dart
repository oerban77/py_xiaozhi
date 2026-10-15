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
import 'package:connectivity_plus/connectivity_plus.dart';

import 'chat_message.dart';
import 'attachment_text_reader.dart';
import 'emotion_service.dart';
import 'mcp_runtime.dart';
import 'music_runtime.dart';
import 'protocol_messages.dart';
import 'wake_word_detector.dart';

/// Coarse device state, mirroring DeviceState in src/constants/constants.py.
/// The Python client derives its status line and its button label from this
/// single value (see SessionActions.on_device_state_changed), and the Android
/// UI does the same: the status bar and the main button are driven by
/// [XiaozhiController.deviceState] rather than by a loose String, so idle /
/// listening / speaking can never disagree with each other.
enum DeviceState { idle, listening, speaking }

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

  /// Persists a camera-facing change made outside the settings page, so the
  /// next take_photo and the settings SegmentedButton stay in sync. Mirrors the
  /// persistence done for the self.camera.switch MCP tool.
  Future<void> setCameraFacing(String facing) async {
    final normalized = facing == 'front' ? 'front' : 'back';
    if (normalized == cameraFacing) return;
    cameraFacing = normalized;
    await _preferences?.setString('camera_facing', cameraFacing);
    notifyListeners();
  }

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
  /// The app is always in auto conversation, mirroring the Python client's
  /// SessionActions which only ever sends ListeningMode.MANUAL from an explicit
  /// "Hold to Talk" press. That press does not exist here, so there is no manual
  /// mode at all: every conversation is a continuous listen/speak loop and the
  /// mode can never be flipped to a state the UI no longer exposes.
  bool get autoConversation => true;
  /// Mirrors SessionActions._auto_session_active: true while a continuous
  /// conversation is running, so the main button shows "Akhiri percakapan" and
  /// tapping it stops the loop instead of starting a second one.
  bool autoSessionActive = false;
  /// Mirrors the desktop GUI's auto-connect: connect as soon as the app starts
  /// when the server settings are already complete, and reconnect after the
  /// socket drops. Toggled in Pengaturan; persisted as 'auto_connect'.
  bool autoConnect = true;
  bool _autoConnectEnabled = true;
  bool _isReconnecting = false;
  int _reconnectAttempt = 0;
  Timer? _reconnectTimer;
  StreamSubscription<List<ConnectivityResult>>? _connectivitySubscription;
  /// True once the current connect() attempt got past the handshake. Only a
  /// connection that was actually up is worth reconnecting: a failed first
  /// connect (bad endpoint, handshake timeout) would otherwise spin forever.
  /// Reset at the top of every connect() and in disconnect().
  bool _wasConnected = false;
  /// Mirrors WAKE_WORD_OPTIONS in src/utils/config_manager.py. The wake word
  /// starts a conversation and interrupts TTS, exactly like src/plugins/wake_word.py.
  WakeWordOptions wakeWordOptions = const WakeWordOptions();
  /// True while the wake word detector is armed and listening. Separate from
  /// WakeWordDetector.isRunning so the UI can show "status listening" even while
  /// the spotter is briefly paused for a cooldown or a TTS window.
  bool wakeWordListening = false;
  String wakeWordError = '';
  /// Suppress wake word detection for 2 seconds after the app resumes from
  /// background. This prevents false triggers from button clicks, volume
  /// adjustments, or other noise when switching back to the app.
  DateTime? _wakeWordSuppressedUntil;

  /// The single source of truth for idle / listening / speaking, mirroring
  /// StateManager._device_state in the Python client. Every transition goes
  /// through [_setDeviceState], which is the only place that clears the flags
  /// the old state implied, so the UI can never show a status that contradicts
  /// isRecording / isSpeaking.
  DeviceState _deviceState = DeviceState.idle;
  DeviceState get deviceState => _deviceState;

  /// Test-only seam so a host test can drive a state transition without a live
  /// socket. Production code goes through [_setDeviceState].
  @visibleForTesting
  void setDeviceStateForTest(DeviceState next) => _setDeviceState(next);

  /// Human-readable rendering of [deviceState], mirroring the status line the
  /// desktop GUI builds from the device state. Kept as a getter so a stale
  /// String can never survive a state change.
  String get status {
    if (!isConnected) {
      if (isConnecting) return 'Menghubungkan...';
      return _reconnectTimer != null
          ? 'Mencoba menghubungkan kembali dalam ${reconnectDelaySeconds(_reconnectAttempt)}dtk...'
          : 'Belum terhubung';
    }
    switch (_deviceState) {
      case DeviceState.listening:
        return wakeWordListening && !isRecording ? 'Wake word aktif' : 'Mendengarkan...';
      case DeviceState.speaking:
        return mcpToolsListed ? 'Xiaozhi sedang berbicara' : 'MCP belum siap';
      case DeviceState.idle:
        return autoSessionActive ? 'Menunggu jawaban...' : 'Terhubung';
    }
  }

  /// Translates [deviceState] into the main button's label, mirroring
  /// SessionActions.on_device_state_changed which flips the button between
  /// "Mulai/Selesai bicara" and "Hentikan jawaban" from the state alone.
  String get primaryActionLabel {
    switch (_deviceState) {
      case DeviceState.listening:
        return 'Selesai bicara';
      case DeviceState.speaking:
        return 'Hentikan jawaban';
      case DeviceState.idle:
        return autoSessionActive ? 'Selesai bicara' : 'Mulai bicara';
    }
  }

  /// The last error the user needs to see (a failed handshake, a rejected
  /// attachment, a player that would not start). Only set by [_reportError] and
  /// cleared by [_setDeviceState], so a transient error cannot linger after the
  /// app has moved on -- which is what made the old loose `status` String show
  /// "Mikrofon gagal dimulai" long after the mic was working again.
  String _error = '';
  String get error => _error;
  bool get hasError => _error.isNotEmpty;

  /// Mirrors StateManager.set_device_state: record the new state, reset the
  /// flags that belonged to the old one, and notify once at the end. The Python
  /// client also emits DEVICE_STATE_CHANGED here, which the audio plugin uses
  /// to re-open the mic after TTS; the equivalent Android work is done inline
  /// by the callers (the silence-period timers) before they call this.
  void _setDeviceState(DeviceState next) {
    if (_deviceState == next && !hasError) return;
    _deviceState = next;
    _error = '';
    notifyListeners();
  }

  void _reportError(String message) {
    _error = message;
    notifyListeners();
  }

  void clearError() {
    if (_error.isEmpty) return;
    _error = '';
    notifyListeners();
  }
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
  /// Mirrors the question passed to set_pending_document in
  /// src/mcp/mcp_server.py: surfaced to the LLM as
  /// "[Attached file: ... | User question: ...]" so it knows what to answer
  /// even though only the short prompt travelled over the detect channel.
  String? _pendingTextQuestion;
  /// Page-by-page PDF text of the pending attachment, kept across reads so the
  /// LLM can continue and jump pages without re-uploading. Mirrors
  /// _LAST_READ_PDF_PATH/_LAST_READ_PDF_AT in src/mcp/tools/documents/service.py.
  PdfExtraction? _pendingPdf;
  DateTime _pendingAttachmentAt = DateTime.fromMillisecondsSinceEpoch(0);
  static const _attachmentTtl = Duration(minutes: 30);
  Uint8List? _pendingImageAttachment;
  bool _pendingImageAsDocument = false;
  String _pendingImageQuestion = '';
  String? _pendingAttachmentName;
  int? _assistantMessageIndex;
  /// Opus decode sample rate. The server's encode rate is configurable
  /// (AUDIO_DEVICES.opus_output_sample_rate: 24000 official / 16000 third-party);
  /// the decoder must match it, otherwise frames decode at the wrong size and TTS
  /// sounds distorted. Mirrors src/ui/tui/settings_data.py.
  ///
  /// A value of [autoOutputSampleRate] means "detect it from the stream": every
  /// Opus packet's TOC byte names the encoder's bandwidth (RFC 6716 §3.1), so the
  /// first frame tells us the rate instead of forcing the user to guess. This is
  /// the default, because guessing wrong is exactly what makes TTS sound broken.
  static const int autoOutputSampleRate = 0;
  static const List<int> supportedOutputSampleRates = [autoOutputSampleRate, 24000, 16000];
  int _outputSampleRate = autoOutputSampleRate;
  int get outputSampleRate => _outputSampleRate;
  /// The rate actually in use, after auto-detection resolved the stream's
  /// bandwidth. Until the first packet arrives this is the manual default.
  int get effectiveOutputSampleRate =>
      _outputSampleRate == autoOutputSampleRate ? _detectedOutputSampleRate : _outputSampleRate;
  int _detectedOutputSampleRate = 24000;
  /// True while the decoder/player are still bound to the pre-first-frame default
  /// and an auto-detection is pending. The first audio packet resolves it.
  bool get isAutoDetectingOutputSampleRate =>
      _outputSampleRate == autoOutputSampleRate && !_autoRateDetected;
  bool _autoRateDetected = false;
  bool isConnected = false;
  bool isConnecting = false;
  bool isRecording = false;
  bool _wakeWordMicActive = false;
  StreamSubscription<bool>? _musicPlaybackSubscription;
  /// Derived from [deviceState], exactly like StateManager.is_speaking() reads
  /// the state enum instead of keeping a second boolean. Keeping a separate
  /// writable flag is how TTS went silent: the tts "start" handler set the state
  /// to speaking but the flag stayed false, so the player-stream re-arm gate in
  /// _handleAudio never opened and no audio ever reached the speaker.
  bool get isSpeaking => _deviceState == DeviceState.speaking;
  bool _opusInitialized = false;
  bool _playerStarted = false;
  /// Monotonic token bumped on every player teardown. A stop/start is inherently
  /// racy here: _handleWakeWordDetected restarts the stream in a .then() while
  /// _interruptTtsForTextInput and setOutputSampleRate do their own stop/start,
  /// and an audio frame arriving in the middle of that window used to see
  /// _playerStarted == false and call startPlayerFromStream() on a player whose
  /// stopPlayer() had not completed yet. That call throws, the exception escaped
  /// _handleAudio, and the whole utterance was dropped ("TTS tidak ada suaranya").
  /// Every start captures the generation it was launched under and every pending
  /// restart verifies it is still current before touching the player.
  int _playerGeneration = 0;
  /// Serializes player stop/start. Without this, two concurrent restarts race:
  /// the loser's startPlayerFromStream() hits an already-open stream and throws,
  /// which is exactly how an utterance goes silent.
  Future<void> _playerStreamOp = Future<void>.value();
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
  /// Increased from 4 to 6 frames (80ms -> 120ms) to reduce feed frequency and
  /// improve resilience to network jitter.
  static const int _ttsFeedFrames = 6;
  /// Mirrors _TTS_FIFO_MAX_S = 10.0 in src/audio_codecs/audio_codec.py. This is a
  /// safety valve for a stalled consumer, not a jitter target: it has to be large
  /// that a slow feed never drops the *head* of an utterance. Dropping the head is
  /// exactly what makes TTS sound like it begins mid-sentence.
  static const int _ttsBufferMaxFrames = 500;
  /// Frames to accumulate before the first feed of an utterance, mirroring
  /// _TTS_PREBUFFER_S in src/audio_codecs/audio_codec.py. The Python client holds
  /// 0.12s; increased from 8 to 12 frames (160ms -> 240ms) to better absorb
  /// network jitter at speech onset and prevent "brebet" / choppy starts.
  static const int _ttsPrebufferFrames = 12;
  /// Wall-clock cap for a single player feed. feedInt16FromStream can exert
  /// backpressure; increased from 500ms to 1000ms to avoid dropping bursts under
  /// load, which caused "suara tidak keluar" on slower devices.
  static const Duration _ttsFeedTimeout = Duration(milliseconds: 1000);
  final List<Int16List> _ttsBuffer = [];
  bool _ttsFeeding = false;
  bool _ttsNeedsPrebuffer = true;
  bool _discardTtsAudioUntilStart = false;

  /// Mirrors src/plugins/audio.py: while the speaker is still draining its TTS
  /// buffer the microphone must stay suppressed, otherwise in auto/realtime mode
  /// the assistant hears its own voice out of the speaker and answers itself.
  static const Duration _silencePeriodMax = Duration(seconds: 3);
  /// Increased from 900ms to 1500ms to ensure the larger player buffer (8192 bytes)
  /// fully drains before the mic is re-enabled. This prevents the assistant from
  /// hearing its own tail audio and interrupting itself ("mic tidak direspon" or
  /// false wake word triggers right after TTS).
  static const Duration _silencePeriodHold = Duration(milliseconds: 1500);
  bool _suppressMic = false;
  Timer? _silencePollTimer;
  DateTime? _lastTtsFrameAt;
  /// Wall-clock cap for the whole speaking window. The server's tts "stop" can be
  /// lost (the socket drops mid-utterance, or the server is killed), and without a
  /// cap _suppressMic would stay true forever and the mic would never answer again
  /// ("mic tidak direspon"). Mirrors the _SILENCE_PERIOD_MAX_S deadline in
  /// src/plugins/audio.py, applied to the playback window itself rather than only
  /// to the post-stop drain.
  static const Duration _speakingTimeout = Duration(seconds: 15);
  Timer? _speakingWatchdog;

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
    autoConnect = _preferences?.getBool('auto_connect') ?? true;
    _autoConnectEnabled = autoConnect;
    final storedRate = _preferences?.getInt('opus_output_sample_rate') ?? autoOutputSampleRate;
    _outputSampleRate =
        supportedOutputSampleRates.contains(storedRate) ? storedRate : autoOutputSampleRate;
    wakeWordOptions = WakeWordOptions(
      enabled: _preferences?.getBool('wake_word_enabled') ?? true,
      wakeWord: _preferences?.getString('wake_word_text') ?? 'Hello Xiaozhi',
      numThreads: _preferences?.getInt('wake_word_num_threads') ?? 2,
      maxActivePaths: _preferences?.getInt('wake_word_max_active_paths') ?? 2,
      keywordsScore: _preferences?.getDouble('wake_word_keywords_score') ?? 1.8,
      keywordsThreshold: _preferences?.getDouble('wake_word_keywords_threshold') ?? 0.35,
      numTrailingBlanks: _preferences?.getInt('wake_word_num_trailing_blanks') ?? 1,
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

    // Listen for music playback state changes to keep mic open for wake word detection
    await MusicRuntime.initialize();
    _musicPlaybackSubscription = MusicRuntime.playbackStateStream.listen((playing) {
      if (playing) {
        // Music started - ensure mic is open for wake word detector
        if (!_wakeWordMicActive && wakeWordOptions.enabled && wakeWordListening) {
          _wakeWordMicActive = true;
          unawaited(_openMicStream());
        }
      } else {
        // Music stopped - close mic if not needed for wake word
        if (_wakeWordMicActive && (!wakeWordOptions.enabled || !wakeWordListening)) {
          unawaited(_closeMicStream());
        }
      }
    });

    // Listen for network connectivity changes to trigger reconnect when network
    // comes back (e.g., after switching WiFi, mobile data, or airplane mode).
    _connectivitySubscription = Connectivity().onConnectivityChanged.listen((
      List<ConnectivityResult> results,
    ) {
      // If we have any non-none connectivity and auto-connect is enabled but
      // we're not currently connected/connecting, try to reconnect.
      final hasConnectivity = results.any((r) => r != ConnectivityResult.none);
      if (hasConnectivity) {
        // Network is available - try to connect if we should be connected
        // Also check _isReconnecting to avoid race with _scheduleReconnect timer
        if (_autoConnectEnabled &&
            !isConnected &&
            !isConnecting &&
            !_isReconnecting &&
            _canAutoConnect) {
          unawaited(connect());
        }
      } else {
        // Network is lost - cancel any pending reconnect timer since there's
        // no point trying to reconnect without network. The timer will be
        // rescheduled when connectivity returns.
        _cancelReconnect();
      }
    });
  }

  /// True when every field connect() needs is already saved, so auto-connect can
  /// proceed without the user opening Pengaturan first. Mirrors the guard at the
  /// top of connect().
  bool get _canAutoConnect =>
      endpoint.trim().isNotEmpty &&
      token.trim().isNotEmpty &&
      deviceId.trim().isNotEmpty &&
      clientId.trim().isNotEmpty;

  /// Connects on startup when the feature is on and the settings are complete.
  /// Called from main.dart once the permission gate has passed; a missing mic
  /// permission would make the wake word detector fail to arm, but connect()
  /// itself only needs the WebSocket, so this does not block on permissions.
  Future<void> maybeAutoConnect() async {
    if (!autoConnect || !_canAutoConnect) return;
    await connect();
  }

  /// Reconnects after the socket drops, with an exponential backoff capped at
  /// 30s. Called from _handleConnectionFailure, which already ran the teardown,
  /// so this only schedules the next attempt. Any user action (disconnect, or a
  /// successful connect) cancels the timer via _cancelReconnect().
  void _scheduleReconnect() {
    if (!_autoConnectEnabled || isConnecting || isConnected) return;
    if (_isReconnecting) return;
    _isReconnecting = true;
    _reconnectAttempt += 1;
    final delay = reconnectDelaySeconds(_reconnectAttempt);
    // status is derived from _reconnectTimer now, so this only needs the notify.
    notifyListeners();
    _reconnectTimer = Timer(Duration(seconds: delay), () {
      _reconnectTimer = null;
      _isReconnecting = false;
      if (!_autoConnectEnabled || isConnected || isConnecting) return;
      unawaited(connect());
    });
  }

  void _cancelReconnect() {
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _isReconnecting = false;
    _reconnectAttempt = 0;
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
    required bool newAutoConnect,
    required int newOutputSampleRate,
    required bool newWakeWordEnabled,
    required String newWakeWordText,
    required int newWakeWordNumThreads,
    required int newWakeWordMaxActivePaths,
    required double newWakeWordKeywordsScore,
    required double newWakeWordKeywordsThreshold,
    required int newWakeWordNumTrailingBlanks,
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
    // Saving settings tears down any open conversation (disconnect() ran above),
    // so the session is no longer active.
    autoSessionActive = false;
    autoConnect = newAutoConnect;
    _autoConnectEnabled = newAutoConnect;
    _cancelReconnect();
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
    await _preferences?.setBool('auto_connect', autoConnect);
    await setOutputSampleRate(newOutputSampleRate);
    // disconnect() above stopped the detector and cleared isConnected, so arm it
    // directly here; setWakeWordOptions would no-op because it sees no connection.
    await setWakeWordOptions(
      enabled: newWakeWordEnabled,
      wakeWord: newWakeWordText,
      numThreads: newWakeWordNumThreads,
      maxActivePaths: newWakeWordMaxActivePaths,
      keywordsScore: newWakeWordKeywordsScore,
      keywordsThreshold: newWakeWordKeywordsThreshold,
      numTrailingBlanks: newWakeWordNumTrailingBlanks,
    );
    if (wakeWordOptions.enabled) {
      await _startWakeWordDetector();
    }
    await _secureStorage.write(key: 'access_token', value: token);
    await _secureStorage.write(key: 'camera_vl_api_key', value: vlApiKey);
    await _secureStorage.write(key: 'camera_explain_token', value: visionToken);
    await _secureStorage.write(key: 'smart_home_password', value: smartHomePassword);
    _setDeviceState(DeviceState.idle);
    // The user just saved connection settings, so the intent is clearly to be
    // connected to the new server. disconnect() above disabled auto-reconnect,
    // and without this the app would sit idle showing "Pengaturan tersimpan"
    // until the user manually taps Hubungkan (issue: "tidak bisa autoconnect
    // setelah save setting"). Reconnect immediately when the feature is on and
    // the new settings are complete.
    if (newAutoConnect && _canAutoConnect) {
      unawaited(connect());
    }
  }

  /// Starts a continuous conversation, mirroring
  /// ConversationSession.start_auto_conversation: send listen in realtime mode
  /// (Android has no AEC, so the mic must stay open for the whole session) and
  /// mark the session active so the main button offers "Akhiri percakapan".
  ///
  /// This replaces the old setAutoConversation(true). Manual mode is gone, so
  /// there is no mode to toggle -- the only choice is whether a conversation is
  /// running, and that is what the main button already controls.
  Future<void> startConversation() async {
    if (!isConnected) {
      _reportError('Hubungkan perangkat terlebih dahulu');
      return;
    }
    if (autoSessionActive) return;
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
    }
    _setDeviceState(DeviceState.listening);
  }

  /// Ends the continuous conversation, mirroring ConversationSession.stop_listening:
  /// stop listening, drop keep_listening, and return to idle.
  Future<void> stopConversation() async {
    if (!autoSessionActive) return;
    autoSessionActive = false;
    if (isRecording) {
      // stopVoice sends the listen-stop; sending it here too would duplicate it.
      await stopVoice();
    } else {
      // The mic is in wake-word mode, so stopVoice is a no-op, but the server is
      // still in a realtime listen and needs the stop.
      _sendJson(ProtocolMessages.listenStop(sessionId));
    }
    _setDeviceState(DeviceState.idle);
  }

  /// The main button: start or end a conversation, mirroring
  /// SessionActions.auto_session_toggle. While the assistant is speaking the same
  /// tap interrupts the answer (SessionActions._stop_auto_session stops first,
  /// then aborts, so the interruption is not resumed by the listen continuation).
  Future<void> toggleConversation() async {
    if (!isConnected) return;
    if (isSpeaking) {
      await interrupt();
      return;
    }
    if (autoSessionActive || isRecording) {
      await stopConversation();
      return;
    }
    await startConversation();
  }

  /// Applies new wake word settings and persists them, mirroring the
  /// CONFIG_CHANGED handler in src/plugins/wake_word.py which hot-reloads the model.
  /// Passing [enabled] or [wakeWord] as null leaves the current value untouched.
  Future<void> setWakeWordOptions({
    bool? enabled,
    String? wakeWord,
    int? numThreads,
    int? maxActivePaths,
    double? keywordsScore,
    double? keywordsThreshold,
    int? numTrailingBlanks,
  }) async {
    final wasEnabled = wakeWordOptions.enabled;
    wakeWordOptions = wakeWordOptions.copyWith(
      enabled: enabled ?? wakeWordOptions.enabled,
      wakeWord: wakeWord?.trim().isNotEmpty == true ? wakeWord!.trim() : wakeWordOptions.wakeWord,
      numThreads: numThreads ?? wakeWordOptions.numThreads,
      maxActivePaths: maxActivePaths ?? wakeWordOptions.maxActivePaths,
      keywordsScore: keywordsScore ?? wakeWordOptions.keywordsScore,
      keywordsThreshold: keywordsThreshold ?? wakeWordOptions.keywordsThreshold,
      numTrailingBlanks: numTrailingBlanks ?? wakeWordOptions.numTrailingBlanks,
    );
    await _preferences?.setBool('wake_word_enabled', wakeWordOptions.enabled);
    await _preferences?.setString('wake_word_text', wakeWordOptions.wakeWord);
    await _preferences?.setInt('wake_word_num_threads', wakeWordOptions.numThreads);
    await _preferences?.setInt('wake_word_max_active_paths', wakeWordOptions.maxActivePaths);
    await _preferences?.setDouble('wake_word_keywords_score', wakeWordOptions.keywordsScore);
    await _preferences?.setDouble('wake_word_keywords_threshold', wakeWordOptions.keywordsThreshold);
    await _preferences?.setInt('wake_word_num_trailing_blanks', wakeWordOptions.numTrailingBlanks);
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
          // The status bar derives from deviceState; the spotter running on its
          // own (no conversation yet) is idle, not "listening".
          if (!isRecording) _setDeviceState(DeviceState.idle);
          // If music is playing, ensure mic stays open for wake word detection
          if (wakeWordListening && MusicRuntime.isPlaying) {
            if (!_wakeWordMicActive) {
              _wakeWordMicActive = true;
              unawaited(_openMicStream());
            }
          }
        } catch (error) {
          await wakeWordDetector.stop();
          wakeWordListening = false;
          wakeWordError = error.toString();
          _reportError('Wake word gagal dimulai: $wakeWordError');
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
        _reportError('Wake word gagal dimulai: $wakeWordError');
        break;
    }
  }

  Future<void> _stopWakeWordDetector() async {
    await wakeWordDetector.stop();
    wakeWordListening = false;
    if (_wakeWordMicActive) await _closeMicStream();
  }

  /// Suppress wake word detection for 2 seconds after the app resumes from
  /// background. This prevents false triggers from button clicks, volume
  /// adjustments, or other noise when switching back to the app.
  void suppressWakeWordAfterResume() {
    _wakeWordSuppressedUntil = DateTime.now().add(const Duration(seconds: 2));
  }

  /// Mirrors WakeWordPlugin._on_detected: interrupt TTS, or start a voice session
  /// so the speech following the wake word reaches the server.
  ///
  /// The session the wake word opens always runs in realtime mode, exactly as
  /// ConversationSession.start_auto_conversation picks REALTIME when AEC is on.
  /// There is no manual mode to fall back to, so the server never waits for a
  /// listen-stop that no tap will ever send.
  void _handleWakeWordDetected(String keyword) {
    if (!isConnected) return;
    if (isSpeaking) {
      // AbortReason.WAKE_WORD_DETECTED + clear_audio_queue in Python.
      _sendJson(ProtocolMessages.abort(sessionId, 'wake_word_detected'));
      _silencePollTimer?.cancel();
      _speakingWatchdog?.cancel();
      _speakingWatchdog = null;
      _suppressMic = false;
      // Mirrors clear_audio_queue in WakeWordPlugin._on_detected: frames of the
      // interrupted utterance are still in flight on the socket and would otherwise
      // land in _ttsBuffer after the clear below, then play out of the restarted
      // stream -- the old answer would keep talking over the user's new question.
      _discardTtsAudioUntilStart = true;
      _ttsBuffer.clear();
      _ttsFeeding = false;
      _ttsNeedsPrebuffer = true;
      // The detector pauses itself for 300ms after a hit (see
      // WakeWordDetector._onDetected) so the interrupt can complete before the
      // buffered audio that contains the wake word is re-processed. Resuming it
      // here is not needed -- the detector's own timer re-enables it -- but the
      // restart below must not be skipped when the player was already stopped
      // by a previous interrupt, otherwise the next utterance stays silent.
      if (_playerStarted || _player.isOpen()) {
        // Serialize the restart: the old code set _playerStarted = false here and
        // restarted inside a .then(), so an audio frame arriving in that window saw
        // a stopped stream and re-armed it twice (startPlayerFromStream on an
        // already-open stream throws), or the restart raced with the interrupt's
        // own restart. Both left the next utterance silent.
        unawaited(
          _stopPlayerStream().then((_) {
            if (isConnected) _startPlayerStream();
          }),
        );
      }
      // The conversation is continuous now, so the session stays active and the
      // state goes back to listening for the user's next sentence.
      autoSessionActive = true;
      _setDeviceState(DeviceState.listening);
      // The abort flow needs the mic live so the user's next sentence is heard.
      // startVoice is a no-op when already recording.
      startVoice();
      return;
    }
    // Not speaking: tell the server the wake word fired and open the mic, so the
    // conversation starts without a tap (send_wake_word_detected + start_listening
    // with AUTO_STOP, since Android has no AEC).
    autoSessionActive = true;
    if (!isRecording) {
      unawaited(startVoice());
    }
    _sendJson(ProtocolMessages.detectText(sessionId, wakeWordOptions.wakeWord));
    _setDeviceState(DeviceState.listening);
  }

  Future<void> connect() async {
    if (isConnected || isConnecting) return;
    _cancelReconnect();
    _wasConnected = false;
    clearError(); // Clear any previous error when user manually connects
    // A manual connect re-enables auto-reconnect: disconnect() turns it off so
    // an intentional disconnect is not immediately undone, but the user tapping
    // "Hubungkan" clearly wants a live connection again.
    _autoConnectEnabled = autoConnect;
    if (endpoint.isEmpty || token.isEmpty || deviceId.isEmpty || clientId.isEmpty) {
      _reportError('Lengkapi koneksi di Pengaturan');
      return;
    }

    final uri = Uri.tryParse(endpoint);
    if (uri == null || !{'ws', 'wss'}.contains(uri.scheme) || uri.host.isEmpty) {
      _reportError('Alamat server harus dimulai dengan ws:// atau wss://');
      return;
    }

    isConnecting = true;
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
          if (!(isConnected || isConnecting)) return;
          // closeCode is populated before onDone fires: the channel adapter sets
          // it on CloseReceived and only then closes the stream. 1000/1001/1005
          // are a normal server-side close -- the server taking back the session
          // ("bye bye") -- which mirrors the clean=True branch of
          // _handle_connection_loss in src/protocols/protocol.py: the device just
          // returns to IDLE. Anything else, including null (an abrupt TCP reset),
          // is a network fault. A close during the handshake is always a failure,
          // never a "bye bye".
          final code = channel.closeCode;
          if (isConnected && isCleanCloseCode(code)) {
            unawaited(_handleCleanClose());
          } else {
            _handleConnectionFailure('Koneksi server ditutup');
          }
        },
      );
      await channel.ready.timeout(const Duration(seconds: 15));
      channel.sink.add(ProtocolMessages.hello(sessionId));
      await _helloCompleter!.future.timeout(const Duration(seconds: 15));
      isConnected = true;
      _wasConnected = true;
      isConnecting = false;
      clearError(); // Clear any previous connection error on successful connect
      // Mirrors _on_audio_channel_opened: the channel is up but nothing is
      // listening yet, so the device is idle until the user starts a conversation.
      _setDeviceState(DeviceState.idle);
      // Mirrors UiPresenter.show_device_state, which resets the emotion to
      // "neutral" whenever the device state changes.
      emotion = EmotionService.hasAnimation(emotion) ? emotion : 'neutral';
      liveText = '';
      // Arm the wake word detector after the handshake so the model load does not
      // compete with the connection for CPU (WakeWordPlugin.start runs after the
      // protocol is up for the same reason). Skip the reload when the spotter is
      // already armed: a normal server-side close keeps it running (see
      // _handleCleanClose), and reloading the ONNX model on every reconnect would
      // burn CPU and briefly drop the mic stream the detector is fed from.
      if (wakeWordOptions.enabled) {
        if (!wakeWordDetector.isRunning) {
          await _startWakeWordDetector();
        } else if (!wakeWordListening) {
          // Detector is running (kept alive across clean close) but mic stream
          // and flags were reset in _handleCleanClose. Re-arm without reloading
          // the model so wake word works immediately after reconnect.
          try {
            wakeWordDetector.onDetected = _handleWakeWordDetected;
            if (!isRecording && !_wakeWordMicActive) await _openMicStream();
            _wakeWordMicActive = !isRecording;
            wakeWordListening = true;
            wakeWordError = '';
            if (!isRecording) _setDeviceState(DeviceState.idle);
            // If music is playing, ensure mic stays open for wake word detection
            if (wakeWordListening && MusicRuntime.isPlaying) {
              if (!_wakeWordMicActive) {
                _wakeWordMicActive = true;
                unawaited(_openMicStream());
              }
            }
          } catch (error) {
            wakeWordListening = false;
            wakeWordError = error.toString();
            _reportError('Wake word gagal di-rearm: $wakeWordError');
          }
        }
      }
    } catch (error) {
      await _closeSocket();
      isConnected = false;
      isConnecting = false;
      _reportError('Gagal terhubung: ${error.toString()}');
      emotion = '';
      liveText = '';
      // Schedule reconnect for initial connection failures too (network down,
      // server unreachable, etc.), but only if auto-connect is enabled and
      // settings are valid. This mirrors the Python client's behavior of
      // retrying indefinitely with backoff until the user disables auto-connect.
      if (_autoConnectEnabled && _canAutoConnect) {
        _scheduleReconnect();
      }
    }
    notifyListeners();
  }

  Future<void> disconnect() async {
    _autoConnectEnabled = false;
    _wasConnected = false;
    _cancelReconnect();
    _connectivitySubscription?.cancel();
    _connectivitySubscription = null;
    if (isRecording) await stopVoice();
    await _stopWakeWordDetector();
    autoSessionActive = false;
    isConnected = false;
    isConnecting = false;
    mcpInitialized = false;
    mcpToolsListed = false;
    _pendingTextAttachment = null;
    _pendingTextQuestion = null;
    _pendingPdf = null;
    _pendingAttachmentName = null;
    _silencePollTimer?.cancel();
    _speakingWatchdog?.cancel();
    _speakingWatchdog = null;
    _suppressMic = false;
    _ttsBuffer.clear();
    await _closeSocket();
    await _stopPlayerStream();
    sessionId = '';
    _setDeviceState(DeviceState.idle);
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
      // Mirrors ui_session.send_text: a long chat message is routed through the
      // document queue as a temporary "pasted-*.txt" attachment, with a question
      // that tells the LLM to treat the content as the user's request.
      attachmentName: isLongText ? 'pesan-pengguna.txt' : '',
      pendingQuestion: isLongText
          ? 'Ini pesan/perintah pengguna. Baca isinya dan jalankan permintaan yang tertulis.'
          : '',
    );
  }

  Future<bool> sendTextAttachment({
    required String fileName,
    String content = '',
    required String question,
    PdfExtraction? pdf,
  }) {
    final request = question.trim().isEmpty
        ? 'Baca dan jawab isi lampiran ini.'
        : question.trim();
    return _sendChatRequest(
      displayText: 'Lampiran teks: $fileName\n$request',
      detectText: 'baca lampiran',
      pendingText: content,
      pendingPdf: pdf,
      attachmentName: fileName,
      pendingQuestion: request,
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
    PdfExtraction? pendingPdf,
    Uint8List? imageBytes,
    String imageQuestion = '',
    String attachmentName = '',
    String pendingQuestion = '',
    bool pendingImageAsDocument = false,
  }) async {
    if (!isConnected) return false;
    await _interruptTtsForTextInput();
    // A PDF is served page by page (see McpRuntime.manage_document), so its
    // full text is never inlined here; only non-PDF attachments are capped.
    _pendingTextAttachment = pendingPdf != null
        ? null
        : pendingText == null
            ? null
            : pendingText.length > maxTextAttachmentChars
                ? '${pendingText.substring(0, maxTextAttachmentChars)}\n[Attachment content truncated]'
                : pendingText;
    _pendingPdf = pendingPdf;
    _pendingAttachmentAt = DateTime.now();
    _pendingTextQuestion = (pendingText != null || pendingPdf != null)
        ? pendingQuestion
        : null;
    _pendingImageAttachment = imageBytes;
    _pendingImageAsDocument = pendingImageAsDocument;
    _pendingImageQuestion = imageQuestion;
    final hasAttachment =
        pendingText != null || pendingPdf != null || imageBytes != null;
    _pendingAttachmentName = hasAttachment ? attachmentName : null;
    messages.add(ChatMessage(text: displayText, isUser: true));
    // The conversation is continuous, so a text send keeps the session alive and
    // the state stays listening while the answer is being prepared.
    autoSessionActive = true;
    _setDeviceState(DeviceState.listening);
    _sendJson(ProtocolMessages.listenStart(sessionId, 'realtime'));
    _sendJson(ProtocolMessages.detectText(sessionId, detectText));
    return true;
  }

  Future<void> startVoice() async {
    if (!isConnected || isRecording) return;
    // The user explicitly asked to speak, so any residual post-TTS silence period
    // is irrelevant: without this the recorder opens but _handleMicData still sees
    // _suppressMic true and sends nothing to the server, which is exactly the
    // "mic tidak direspon setelah TTS" symptom. The period's own timers clear the
    // flag eventually, but not before the user has already started talking.
    _silencePollTimer?.cancel();
    _silencePollTimer = null;
    _suppressMic = false;
    if (_wakeWordMicActive) {
      _wakeWordMicActive = false;
      isRecording = true;
      _sendJson(ProtocolMessages.listenStart(sessionId, 'realtime'));
      emotion = 'neutral';
      liveText = '';
      _setDeviceState(DeviceState.listening);
      return;
    }
    try {
      await _openMicStream();
      _sendJson(ProtocolMessages.listenStart(sessionId, 'realtime'));
      isRecording = true;
      // DeviceState.LISTENING in the desktop GUI resets the emotion to neutral.
      emotion = 'neutral';
      liveText = '';
      _setDeviceState(DeviceState.listening);
    } catch (error) {
      _reportError('Mikrofon gagal dimulai: ${error.toString()}');
    }
  }

  Future<void> stopVoice() async {
    if (!isRecording) return;
    isRecording = false;
    _sendJson(ProtocolMessages.listenStop(sessionId));
    if (isConnected && wakeWordOptions.enabled && wakeWordListening) {
      _wakeWordMicActive = true;
    } else {
      await _closeMicStream();
    }
    // A continuous conversation goes back to idle and waits for the next wake
    // word or tap; the status bar derives the label from this.
    _setDeviceState(DeviceState.idle);
  }

  Future<void> _openMicStream() async {
    // Don't open if already open
    if (_micSubscription != null) return;
    if (!await _recorder.hasPermission()) {
      throw StateError('Izin mikrofon diperlukan');
    }
    _pendingMicBytes.clear();
    final stream = await _recorder.startStream(_micConfig);
    _micSubscription = stream.listen(_handleMicData);
  }

  Future<void> _closeMicStream() async {
    // Only close if the mic is not needed for wake word detection
    if (_wakeWordMicActive && wakeWordOptions.enabled && wakeWordListening) {
      return; // Keep mic open for wake word detector
    }
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
      _setDeviceState(DeviceState.listening);
    } else {
      await startVoice();
    }
  }

  void _handleSocketMessage(dynamic message) {
    if (message is String) {
      try {
        _handleJson(jsonDecode(message) as Map<String, dynamic>);
      } on FormatException {
        _reportError('Pesan server tidak valid');
      }
    } else if (message is List<int>) {
      final packet = Uint8List.fromList(message);
      _audioEventQueue = _audioEventQueue.then((_) async {
        try {
          await _handleAudio(packet);
        } catch (_) {
          _reportError('Paket audio tidak dapat diputar');
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
          _reportError('Payload MCP tidak valid');
          return;
        }
      }

      if (request != null) {
        // Mirrors _PDF_CONTEXT_TTL_SECONDS in src/mcp/tools/documents/service.py:
        // an unread attachment older than the TTL is dropped rather than served.
        if (_pendingAttachmentAt.difference(DateTime.now()).abs() > _attachmentTtl) {
          _pendingTextAttachment = null;
          _pendingTextQuestion = null;
          _pendingPdf = null;
          _pendingAttachmentName = null;
        }
        final response = await McpRuntime.handle(
          request,
          disabledModules: disabledMcpModules.toSet(),
          pendingTextAttachment: _pendingTextAttachment,
          pendingTextQuestion: _pendingTextQuestion ?? '',
          pendingPdf: _pendingPdf,
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
            // Mirrors _LAST_READ_PDF_AT in src/mcp/tools/documents/service.py:
            // a read refreshes the TTL instead of dropping the attachment, so
            // "continue" / "read page N" still work on the next turn. Only an
            // image attachment is consumed here, since take_photo is one-shot.
            _pendingAttachmentAt = DateTime.now();
            if (params['name'] == 'take_photo') {
              _pendingTextAttachment = null;
              _pendingTextQuestion = null;
              _pendingPdf = null;
              _pendingImageAttachment = null;
              _pendingImageAsDocument = false;
              _pendingImageQuestion = '';
              _pendingAttachmentName = null;
            }
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
        _discardTtsAudioUntilStart = false;
        _assistantText = '';
        _assistantMessageIndex = null;
        // Mirrors ConversationSession._handle_tts_start: the device is speaking
        // unless a continuous realtime session keeps the mic open. Android has no
        // AEC, so the mic is always gated during playback and the state is always
        // SPEAKING here.
        _beginTtsPlayback();
        _ttsNeedsPrebuffer = true;
        // A stale timestamp from the previous utterance would make the silence
        // period's hold math run against an unrelated clock, so the drain timer
        // starts fresh here and _handleTtsStop/_flushTtsBuffer refresh it as the
        // real frames flow.
        _lastTtsFrameAt = DateTime.now();
        // The server's tts "stop" can be lost (socket drops mid-utterance, the
        // server is killed). Without a cap isSpeaking/_suppressMic would stay true
        // forever and the mic would never answer again. Cleared by the real stop
        // and by every path that tears playback down.
        _speakingWatchdog?.cancel();
        _speakingWatchdog = Timer(_speakingTimeout, _handleLostTtsStop);
        _setDeviceState(DeviceState.speaking);
      }
      final text = data['text'];
      if (text is String && text.isNotEmpty) {
        _appendAssistantText(text);
        liveText = _assistantText;
        // Reset the watchdog on TTS text: for long documents (PDF, articles)
        // the server may pause audio synthesis while processing the next
        // chunk. Without this reset, a gap > 15s between audio frames would
        // fire _handleLostTtsStop and kill the TTS session mid-utterance.
        // Text messages prove the server is still actively generating.
        if (isSpeaking) {
          _speakingWatchdog?.cancel();
          _speakingWatchdog = Timer(_speakingTimeout, _handleLostTtsStop);
        }
        notifyListeners();
      }
      if (state == 'stop') {
        _assistantMessageIndex = null;
        _speakingWatchdog?.cancel();
        _speakingWatchdog = null;
        unawaited(_handleTtsStop());
      }
    } else if (type == 'llm') {
      // Mirrors UiPresenter.show_protocol_message's llm branch: the LLM's emotion
      // updates the animation shown next to the chat text.
      final value = data['emotion'];
      if (value is String && value.trim().isNotEmpty) {
        emotion = EmotionService.normalize(value);
        // Reset the watchdog on LLM emotion: for very long responses the server
        // may have gaps between TTS text chunks. LLM messages prove the server
        // is still actively generating the response.
        if (isSpeaking) {
          _speakingWatchdog?.cancel();
          _speakingWatchdog = Timer(_speakingTimeout, _handleLostTtsStop);
        }
        notifyListeners();
      }
    } else if (type == 'error') {
      _reportError((data['message'] ?? data['error'] ?? 'Kesalahan server').toString());
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
    _speakingWatchdog?.cancel();
    _speakingWatchdog = null;
    _suppressMic = false;
    _discardTtsAudioUntilStart = true;
    _ttsBuffer.clear();
    _ttsFeeding = false;
    _ttsNeedsPrebuffer = true;
    // The caller decides the next state (interrupt goes to listening, a lost
    // connection goes to idle); isSpeaking is derived from that state, so it must
    // not be touched here or it would race with the caller's transition.
    // Stop and restart as one serialized unit. The old code set _playerStarted =
    // false first and re-armed in a separate await, so a frame arriving in between
    // hit _handleAudio's re-arm path on a half-stopped player and threw, dropping
    // the whole following utterance.
    //
    // The isOpen() half mirrors _handleWakeWordDetected: an interrupt that arrives
    // after the stream was already stopped (a previous interrupt, a sample-rate
    // change) still needs the restart, otherwise the next utterance finds no live
    // stream and stays silent.
    if (_playerStarted || _player.isOpen()) {
      await _stopPlayerStream();
      if (isConnected) {
        try {
          await _startPlayerStream();
        } catch (_) {}
      }
    }
  }
  @visibleForTesting
  static String appendTtsSegment(String current, String segment) {
    if (current.isEmpty || current.endsWith('\n') || segment.startsWith('\n')) {
      return '$current$segment';
    }
    return '$current\n$segment';
  }

  /// Seconds to wait before the [attempt]-th reconnect, exponential and capped
  /// at 30s so a long outage does not hammer the server.
  @visibleForTesting
  static int reconnectDelaySeconds(int attempt) {
    final attempt1 = attempt < 1 ? 1 : attempt;
    return (2 * attempt1).clamp(2, 30);
  }

  /// True when every field connect() needs is already saved. Exposed for tests
  /// so they can assert the auto-connect guard without a live socket.
  @visibleForTesting
  bool get canAutoConnect => _canAutoConnect;

  /// Normal server-side close codes (RFC 6455): the server is taking back the
  /// session ("bye bye") rather than reporting a network fault. Exposed for
  /// tests so the clean-close classification can be asserted without a socket.
  @visibleForTesting
  static bool isCleanCloseCode(int? code) =>
      code == 1000 || code == 1001 || code == 1005;

  /// Parses the encoding parameters out of the TOC byte of an Opus packet,
  /// mirroring parse_opus_toc in src/audio_codecs/opus_codec.py (RFC 6716 §3.1):
  /// the high 5 bits are the config (mode + bandwidth + frame duration) and the
  /// low 2 bits are the code (frame count).
  ///
  /// This is what makes "auto sample rate" possible: the encoder's bandwidth is
  /// written into every packet, so the client never has to guess the server's
  /// [AUDIO_DEVICES.opus_output_sample_rate].
  @visibleForTesting
  static OpusToc? parseOpusToc(Uint8List packet) {
    if (packet.isEmpty) return null;
    final toc = packet[0];
    final config = (toc >> 3) & 0x1F;
    final code = toc & 0x03;

    final String mode;
    final String bandwidth;
    final int frameMs;
    if (config < 12) {
      mode = 'SILK';
      bandwidth = const ['NB', 'MB', 'WB'][config ~/ 4];
      frameMs = const [10, 20, 40, 60][config % 4];
    } else if (config < 16) {
      mode = 'Hybrid';
      bandwidth = const ['SWB', 'FB'][(config - 12) ~/ 2];
      frameMs = const [10, 20][config % 2];
    } else {
      mode = 'CELT';
      bandwidth = const ['NB', 'WB', 'SWB', 'FB'][(config - 16) ~/ 4];
      frameMs = const [2, 5, 10, 20][config % 4];
    }

    final int frames;
    if (code == 0) {
      frames = 1;
    } else if (code <= 2) {
      frames = 2;
    } else {
      frames = (packet.length >= 2) ? (packet[1] & 0x3F) : 1;
    }

    return OpusToc(
      bandwidth: bandwidth,
      frameMs: frameMs,
      frames: frames,
      mode: mode,
    );
  }

  /// Maps an Opus bandwidth to the sample rate the decoder must be configured
  /// with. libopus resamples internally, so decoding a 16kHz-bandwidth stream at
  /// 24kHz still plays at the right *speed* -- the rate only sets the output
  /// bandwidth -- but decoding below the encoded bandwidth throws away the top of
  /// the spectrum and sounds muffled, and the player stream must be opened at the
  /// same rate the decoder produces. Returns null for a bandwidth this client does
  /// not support.
  @visibleForTesting
  static int? sampleRateForBandwidth(String bandwidth) {
    switch (bandwidth) {
      case 'NB':
        return 8000;
      case 'MB':
        return 12000;
      case 'WB':
        return 16000;
      case 'SWB':
        return 24000;
      case 'FB':
        return 48000;
      default:
        return null;
    }
  }

  /// Rebuilds the decoder and the player stream around [effectiveOutputSampleRate].
  /// Called when auto-detection resolves a different rate than the stream was
  /// started with; the two are bound together, so both must move together.
  Future<void> _applyOutputSampleRate() async {
    final rate = effectiveOutputSampleRate;
    if (_opusInitialized) {
      _decoder?.destroy();
      _decoder = SimpleOpusDecoder(sampleRate: rate, channels: 1);
    }
    if (_playerStarted) {
      await _stopPlayerStream();
      await _startPlayerStream();
    }
    notifyListeners();
  }

  Future<void> _initializeAudio() async {
    if (!_opusInitialized) {
      initOpus(await opus_flutter.load());
      _encoder = SimpleOpusEncoder(
        sampleRate: 16000,
        channels: 1,
        application: Application.voip,
      );
      _decoder = SimpleOpusDecoder(sampleRate: effectiveOutputSampleRate, channels: 1);
      _opusInitialized = true;
    }
    // A new connection means a new stream whose bandwidth is unknown until the
    // first packet, so re-arm auto-detection. Without this, a switch from a 24kHz
    // server to a 16kHz one would keep the stale detected rate from the previous
    // session.
    if (_outputSampleRate == autoOutputSampleRate) {
      _autoRateDetected = false;
    }
    // The player is opened and started lazily by _handleAudio when the first TTS
    // frame actually arrives. Doing it here on every handshake requests Android
    // audio focus and stops a song playing in the background even when no TTS is
    // coming -- exactly the "server time out -> bye bye -> lagu putus" symptom,
    // because every auto-reconnect ran this. _handleAudio's re-arm path opens and
    // starts the stream on demand, so an answer still starts promptly.
  }

  /// Changes the Opus decode sample rate. The decoder and the player stream are
  /// bound to this rate, so both must be rebuilt when it changes. Passing
  /// [autoOutputSampleRate] switches to detection from the stream.
  Future<void> setOutputSampleRate(int rate) async {
    if (!supportedOutputSampleRates.contains(rate)) return;
    final wasAuto = _outputSampleRate == autoOutputSampleRate;
    final wasEffective = effectiveOutputSampleRate;
    _outputSampleRate = rate;
    _autoRateDetected = rate != autoOutputSampleRate;
    await _preferences?.setInt('opus_output_sample_rate', rate);
    // Rebuild only when the *effective* rate actually moves. Switching between
    // "auto" and a manual value that happens to match what auto already detected
    // would otherwise tear a working stream down for nothing.
    if (wasAuto != (_outputSampleRate == autoOutputSampleRate) ||
        wasEffective != effectiveOutputSampleRate) {
      await _applyOutputSampleRate();
    }
    notifyListeners();
  }

  Future<void> _startPlayerStream() async {
    // Everything is funneled through _withPlayerStream so concurrent stop/start
    // attempts cannot interleave; see _playerStreamOp.
    await _withPlayerStream(() async {
      if (_playerStarted) return;
      await _player.startPlayerFromStream(
        codec: Codec.pcm16,
        numChannels: 1,
        sampleRate: effectiveOutputSampleRate,
        interleaved: false,
        // Increased from 2048 to 8192 bytes (~43ms -> ~170ms at 24kHz) to give
        // the player a larger internal buffer. This absorbs network jitter and
        // prevents "brebet" / choppy playback when frames arrive unevenly. The
        // prebuffer (_ttsPrebufferFrames = 12 frames = 240ms) ensures the buffer
        // is well-filled before playback starts, so the added latency is minimal.
        bufferSize: 8192,
      );
      _playerStarted = true;
    });
  }

  /// Runs [op] with the player-stream lock and a generation check. Returns null if
  /// a newer stop/start superseded this one while it was waiting, which is the
  /// signal for a pending restart to give up rather than resurrect a torn-down
  /// stream.
  Future<void> _withPlayerStream(Future<void> Function() op) async {
    final generation = ++_playerGeneration;
    // Chain instead of a simple mutex: the chain itself is what guarantees the
    // second caller sees the first caller's result.
    final completer = Completer<void>();
    final previous = _playerStreamOp;
    _playerStreamOp = completer.future;
    try {
      await previous;
      // A newer operation already took over; let it win.
      if (generation != _playerGeneration) return;
      await op();
    } catch (error) {
      // startPlayerFromStream throws when the player is not open or the stream is
      // already started. Swallowing keeps a failed restart from breaking the
      // connection; the next frame re-arms the stream via _handleAudio.
      _reportError('Audio tidak dapat dimulai: $error');
    } finally {
      completer.complete();
    }
  }

  /// Tears the stream down and bumps the generation so any restart that was queued
  /// behind it cannot resurrect a stream the caller meant to close.
  Future<void> _stopPlayerStream() async {
    await _withPlayerStream(() async {
      if (!_playerStarted) return;
      await _player.stopPlayer();
      _playerStarted = false;
    });
  }

  void _handleMicData(Uint8List data) {
    // The wake word detector taps the same 16 kHz PCM16 stream the uplink uses,
    // so no second recorder is opened. It must see the audio even while the uplink
    // is suppressed: the whole point of the wake word is to interrupt TTS, and
    // _suppressMic is true for that entire window.
    //
    // After the app resumes from background, button clicks and volume-key noise
    // can reach the mic and be misread as the wake word (the assistant then
    // answers "Sama-sama" to a phantom "terima kasih"). Skip feeding the
    // detector for the suppression window set by suppressWakeWordAfterResume().
    final suppressedUntil = _wakeWordSuppressedUntil;
    if (wakeWordDetector.isRunning &&
        (suppressedUntil == null || DateTime.now().isAfter(suppressedUntil))) {
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
    // Auto sample rate: the TOC byte of every packet names the encoder's
    // bandwidth (RFC 6716 §3.1), so the first frame of the session resolves the
    // server's AUDIO_DEVICES.opus_output_sample_rate without the user having to
    // guess it. The decoder and the player stream must both be rebuilt when it
    // differs from what they were started with, otherwise the player consumes the
    // PCM at the wrong rate.
    //
    // Done before the player re-arm below so the stream opens at the detected rate
    // the first time and there is nothing to tear down. The packet is not decoded
    // here; the decode below uses the freshly rebuilt decoder.
    if (isAutoDetectingOutputSampleRate) {
      final toc = parseOpusToc(packet);
      final detected = toc == null ? null : sampleRateForBandwidth(toc.bandwidth);
      if (detected != null && detected != _detectedOutputSampleRate) {
        _detectedOutputSampleRate = detected;
        // Drop whatever was buffered at the old rate: it was decoded for a
        // different bandwidth and would play as a brief glitch at the start of the
        // utterance. Only the first frames of the first utterance are ever affected.
        _ttsBuffer.clear();
        await _applyOutputSampleRate();
      }
      _autoRateDetected = true;
    }
    if (!_playerStarted && isSpeaking) {
      // The stream is torn down by an interrupt or a sample-rate change while the
      // server keeps sending frames; re-arm it instead of dropping the utterance.
      // Gated on isSpeaking so an abort (which leaves the idle/listening state)
      // cannot resurrect playback of frames that are still in flight. The
      // serialized start means a concurrent stop/interrupt wins instead of
      // throwing here.
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
      // Re-arm on every frame: the watchdog is a stall detector, not a hard cap on
      // utterance length. A long answer (a story, a long explanation) used to hit
      // the fixed 15s deadline mid-sentence, and _handleLostTtsStop then cleared
      // the buffer and flipped isSpeaking, so the rest of the answer arrived into
      // a "not speaking" state and played with a gap or not at all ("TTS sering
      // tidak keluar suaranya"). Only a genuine 15s gap between frames is a stall.
      _speakingWatchdog?.cancel();
      _speakingWatchdog = Timer(_speakingTimeout, _handleLostTtsStop);
      _ttsBuffer.add(pcm);
      // Guard the buffer: if the consumer stalls, never grow unbounded. The 10s cap
      // means this only trips on a genuine stall, and dropping the oldest then
      // matches PcmFifo.push in src/audio_codecs/audio_buffer.py.
      if (_ttsBuffer.length > _ttsBufferMaxFrames) {
        _ttsBuffer.removeRange(0, _ttsBuffer.length - _ttsBufferMaxFrames);
      }
      unawaited(_feedTtsFromBuffer());
    } catch (_) {
      _reportError('Paket audio tidak dapat diputar');
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
        // An interrupt (wake word, text send, abort) clears the buffer and the
        // flags between bursts; stop pushing the interrupted utterance's audio into
        // a stream that may already have been restarted for the next one.
        if (_discardTtsAudioUntilStart || !isSpeaking) break;
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
  ///
  /// Mirrors ConversationSession._handle_tts_stop: a continuous conversation goes
  /// back to LISTENING (re-arming the uplink once the speaker has drained), and a
  /// one-shot conversation goes back to IDLE. Getting this wrong is exactly what
  /// made the state machine look "ngaco": the app finished speaking but the status
  /// bar still said "sedang berbicara", or it went idle in the middle of a
  /// continuous chat and the mic never opened again.
  Future<void> _handleTtsStop() async {
    await _flushTtsBuffer();
    _beginSilencePeriod();
    _resumeAfterTts();
    // Resume music after TTS ends (like Python's AudioPlugin._resume_music_after_tts)
    unawaited(MusicRuntime.resumeAfterTts());
  }

  /// Safety net for a tts "stop" that never arrives (the socket dies mid-utterance
  /// or the server is killed). Without it isSpeaking stays true, the mic stays
  /// suppressed, and the app looks alive but never listens again. This mirrors the
  /// _SILENCE_PERIOD_MAX_S deadline in src/plugins/audio.py, applied to the whole
  /// speaking window because the Android client owns both flags.
  void _handleLostTtsStop() {
    if (!isSpeaking) return;
    _assistantMessageIndex = null;
    _ttsBuffer.clear();
    _ttsFeeding = false;
    _ttsNeedsPrebuffer = true;
    _beginSilencePeriod();
    _resumeAfterTts();
    // Resume music after TTS ends (like Python's AudioPlugin._resume_music_after_tts)
    unawaited(MusicRuntime.resumeAfterTts());
  }

  /// The state transition both tts-stop paths share. isSpeaking is derived from
  /// [deviceState], so there is exactly one place that decides what follows a
  /// finished answer: this method's choice of the next state.
  void _resumeAfterTts() {
    if (autoSessionActive) {
      // Continuous conversation: keep_listening is still true, so re-arm the
      // uplink and go back to listening. The silence period gates the mic until
      // the speaker has actually drained.
      _setDeviceState(DeviceState.listening);
    } else {
      _setDeviceState(DeviceState.idle);
    }
  }

  /// Called on tts "start". The mic must stay suppressed for the entire playback
  /// window (not just the post-stop tail), because Android has no acoustic echo
  /// cancellation: any audio sent now is the assistant's own voice coming out of
  /// the speaker, which the server reads as user speech and interrupts.
  ///
  /// The wake word spotter is *not* paused here. Unlike the uplink, it must keep
  /// seeing the audio during TTS so the user can interrupt the assistant, exactly
  /// as src/plugins/wake_word.py does (the Python detector runs through the
  /// SPEAKING window and its callback aborts the speech). The detector's own
  /// 300ms post-hit pause plus the cooldown keeps the speaker output from
  /// re-triggering it.
  void _beginTtsPlayback() {
    _suppressMic = true;
    _silencePollTimer?.cancel();
    _silencePollTimer = null;
    _lastTtsFrameAt = DateTime.now();
    // Reset prebuffer flag for the new utterance so _feedTtsFromBuffer holds
    // the first frames until the prebuffer threshold is reached. Without this,
    /// a new TTS after a previous one would skip prebuffering and could underrun
    /// immediately ("brebet" at the start of answers).
    _ttsNeedsPrebuffer = true;
    // Pause music so wake word can be heard during TTS (like Python's AudioPlugin._pause_music_for_tts)
    unawaited(MusicRuntime.pauseForTts());
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
    // The poll above only fires every 40ms and only while _playerStarted is true.
    // If the player was already torn down (an interrupt, a sample-rate change, or
    // the connection dropping right at the end of the utterance) the timer never
    // runs and _suppressMic stays true forever -- the mic then never answers again
    // even though the UI shows "Terhubung". Clearing it after the hold window,
    // unconditionally, is the floor that keeps the state machine from sticking.
    // The identity check keeps this from clobbering a *newer* silence period: a
    // wake word right at the tail of one answer starts another TTS window whose
    // own _beginTtsPlayback re-arms _suppressMic, and this timer must not clear
    // that one.
    final pollTimer = _silencePollTimer;
    Timer(_silencePeriodHold + const Duration(milliseconds: 250), () {
      if (!identical(_silencePollTimer, pollTimer)) return;
      pollTimer?.cancel();
      _silencePollTimer = null;
      _suppressMic = false;
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

  /// The server closed the connection normally (close code 1000/1001/1005): it
  /// is taking back the session ("bye bye"), not a network fault. Mirrors the
  /// clean=True branch of _handle_connection_loss in src/protocols/protocol.py,
  /// which only resets the device state to IDLE and neither reports a network
  /// error nor tears local playback down.
  ///
  /// Music keeps playing. just_audio is independent of the socket, and this path
  /// no longer stops and re-arms the TTS player stream on reconnect (see
  /// _initializeAudio), so the reconnect cannot steal Android audio focus and
  /// pause the song. The wake word detector and its microphone also stay armed,
  /// so the user can start the next conversation hands-free while the reconnect
  /// runs in the background.
  Future<void> _handleCleanClose() async {
    isConnected = false;
    isConnecting = false;
    // The server's tool list is per-session, so the next hello has to re-list;
    // leaving these true would make the UI claim "MCP siap" for a dead session.
    mcpInitialized = false;
    mcpToolsListed = false;
    // The uplink recording has nowhere to go, but the microphone itself stays
    // open for the wake word detector, exactly as stopVoice hands the stream
    // over with _wakeWordMicActive. When the detector is not armed there is
    // nothing to hand the stream to, so it must be closed here; otherwise the
    // recorder keeps capturing while every flag says it is idle, and the next
    // startVoice/startStream would fail with "already recording".
    if (isRecording && wakeWordOptions.enabled && wakeWordListening) {
      _wakeWordMicActive = true;
    } else if (isRecording) {
      await _closeMicStream();
    }
    isRecording = false;
    // The continuous conversation died with the socket; without this the next
    // tap would assume a session is still running and stop instead of starting.
    autoSessionActive = false;
    _suppressMic = false;
    _silencePollTimer?.cancel();
    _silencePollTimer = null;
    _speakingWatchdog?.cancel();
    _speakingWatchdog = null;
    // A dropped socket mid-utterance leaves the player holding the last TTS
    // frames; ending in idle is what makes this recover instead of sticking in a
    // "Xiaozhi sedang berbicara" state that never listens again.
    _assistantMessageIndex = null;
    _ttsBuffer.clear();
    _ttsFeeding = false;
    _ttsNeedsPrebuffer = true;
    _setDeviceState(DeviceState.idle);
    if (!(_helloCompleter?.isCompleted ?? true)) {
      _helloCompleter!.completeError(StateError('Koneksi server ditutup'));
    }
    // Only the socket is closed; the mic stays open for the detector above.
    unawaited(_closeSocketOnly());
    if (_playerStarted) unawaited(_stopPlayer());
    // Music is local (just_audio), so it must survive a server disconnect: the
    // Python client's MusicPlayer is likewise independent of the protocol layer
    // and keeps playing across a goodbye. Do not stop it here.
    if (_wasConnected) {
      _scheduleReconnect();
    }
    notifyListeners();
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
    _silencePollTimer = null;
    _speakingWatchdog?.cancel();
    _speakingWatchdog = null;
    _suppressMic = false;
    _ttsBuffer.clear();
    _ttsFeeding = false;
    _ttsNeedsPrebuffer = true;
    // A dropped socket mid-utterance leaves the player holding the last TTS
    // frames. Ending in idle is what makes the "server time out -> bye bye" path
    // recover instead of sticking in a "Xiaozhi sedang berbicara" state that never
    // listens again. Mirrors _on_audio_channel_closed in src/bootstrap/session.py,
    // which only resets the device state to IDLE and does not touch local
    // playback.
    _assistantMessageIndex = null;
    _setDeviceState(DeviceState.idle);
    if (!(_helloCompleter?.isCompleted ?? true)) {
      _helloCompleter!.completeError(StateError(reason));
    }
    unawaited(wakeWordDetector.stop());
    unawaited(_closeSocket());
    if (_playerStarted) unawaited(_stopPlayer());
    // Music is local (just_audio), so it must survive a server disconnect: the
    // Python client's MusicPlayer is likewise independent of the protocol layer
    // and keeps playing across a goodbye. Do not stop it here.
    // Auto-reconnect only after a connection that was actually up; a failed
    // first connect() (bad endpoint, handshake timeout) must not spin forever.
    if (_wasConnected) {
      _scheduleReconnect();
    }
    notifyListeners();
  }

  Future<void> _stopPlayer() async {
    await _stopPlayerStream();
  }

  Future<void> _closeSocket() async {
    await _closeMicStream();
    await _closeSocketOnly();
  }

  /// Cancels the socket subscription and closes the channel without touching the
  /// microphone, so a normal server-side close can keep feeding the wake word
  /// detector while the reconnect runs.
  Future<void> _closeSocketOnly() async {
    await _socketSubscription?.cancel();
    _socketSubscription = null;
    await _channel?.sink.close();
    _channel = null;
  }

  @override
  void dispose() {
    _silencePollTimer?.cancel();
    _speakingWatchdog?.cancel();
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    _connectivitySubscription?.cancel();
    _connectivitySubscription = null;
    _musicPlaybackSubscription?.cancel();
    _musicPlaybackSubscription = null;
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

/// The encoding parameters read from the TOC byte of an Opus packet, mirroring
/// the dict returned by parse_opus_toc in src/audio_codecs/opus_codec.py.
/// See RFC 6716 §3.1.
@immutable
class OpusToc {
  const OpusToc({
    required this.bandwidth,
    required this.frameMs,
    required this.frames,
    required this.mode,
  });

  /// 'NB' | 'MB' | 'WB' | 'SWB' | 'FB'. This is what names the encoder's sample
  /// rate, and therefore what the decoder must be configured with.
  final String bandwidth;
  final int frameMs;
  final int frames;
  final String mode;

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is OpusToc &&
          bandwidth == other.bandwidth &&
          frameMs == other.frameMs &&
          frames == other.frames &&
          mode == other.mode;

  @override
  int get hashCode => Object.hash(bandwidth, frameMs, frames, mode);

  @override
  String toString() => 'OpusToc($mode/$bandwidth ${frameMs}ms x$frames)';
}