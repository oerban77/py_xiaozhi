import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:audio_session/audio_session.dart';
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
import 'avatar_gesture.dart';
import 'avatar_expression.dart';
import 'emotion_service.dart';
import 'lipsync_mfcc.dart';
import 'live2d_characters.dart';
import 'mcp_runtime.dart';
import 'mic_buffers.dart';
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

  /// Maximum chat messages kept in memory. The list only ever grew:
  /// a very long session with long TTS answers accumulates megabytes
  /// of text. The UI shows the recent conversation; older messages are
  /// dropped (the server keeps the full history).
  static const maxChatMessages = 200;

  /// Add a chat message, dropping the oldest ones past [maxChatMessages].
  void _addChatMessage(ChatMessage message) {
    messages.add(message);
    if (messages.length > maxChatMessages) {
      messages.removeRange(0, messages.length - maxChatMessages);
    }
  }

  /// Test-only entry point for [_addChatMessage].
  @visibleForTesting
  void addChatMessageForTest(ChatMessage message) => _addChatMessage(message);

  final MicFrameQueue _pendingMicBytes = MicFrameQueue();
  final WakeWordDetector wakeWordDetector = WakeWordDetector();

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _socketSubscription;
  StreamSubscription<Uint8List>? _micSubscription;

  /// When the last mic chunk arrived. The recorder stream can die
  /// SILENTLY (audio-focus loss from a notification, device error):
  /// the subscription stays non-null but delivers nothing, so
  /// _handleMicStreamDied's onError/onDone never fire and the app
  /// looks armed (flags up, waveform flat, wake word deaf) while the
  /// detector hears silence. The watchdog below reopens the stream
  /// when no data has arrived for a while.
  DateTime? _lastMicDataAt;

  /// Periodic watchdog for the mic stream (see [_checkMicStreamAlive]).
  Timer? _micWatchdogTimer;

  /// Audio-interruption subscription (notification/call/alarm) that
  /// restarts the TTS player when the interruption ends; see
  /// [_listenForAudioInterruptions].
  StreamSubscription<AudioInterruptionEvent>? _audioInterruptionSubscription;
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
  /// `.model3.json` URL of the avatar character, chosen in Settings
  /// (Karakter) and persisted as 'character_model_url'. The home screen
  /// passes it to the avatar, which reloads when it changes.
  String characterModelUrl = Live2dCharacters.defaultModelUrl;

  /// `.glb` URL of the 3D avatar character, chosen in Settings
  /// (Karakter 3D) and persisted as 'avatar_3d_model'. The 2D Live2D
  /// characters (Haru, ...) are Cubism models and have no .glb version,
  /// so the 3D choices are the bundled .glb models (see Avatar3D.models).
  /// Default mirrors Avatar3D.defaultModelUrl; the literal is kept here
  /// so this logic class does not import the 3D widget.
  String character3dModelUrl = 'models/halfBody.glb';

  /// How strongly the 3D avatar's mouth opens while speaking
  /// (0.0-1.0), chosen in Settings ("Kekuatan Mulut 3D") and
  /// persisted as 'mouth_strength'. Default 0.5: the model's morphs
  /// are very strong, so 1.0 still gaped wide open on the device
  /// videos; 0.5 keeps the mouth clearly moving but natural.
  double mouthStrength = 0.5;

  /// Filter suara luar (TV): when true, a wake-word hit only counts
  /// when the mic audio that triggered it is loud enough to be the
  /// user speaking close to the phone. TV audio is speech too, so the
  /// vowel gate alone cannot tell it apart — but it is usually much
  /// quieter at the mic than the user. Persisted as
  /// 'external_sound_filter'.
  bool externalSoundFilter = true;

  /// Minimum mic RMS (0.0-1.0) for a wake-word hit to count as the
  /// user speaking close by, used only when [externalSoundFilter] is
  /// on. Normal speech at arm's length is ~0.03-0.3 RMS, but at a
  /// table/desk distance (~1 m) it drops to ~0.01-0.03, so the old
  /// default 0.03 rejected legitimate wake words spoken from a
  /// distance (the "susah" in idle). TV at a distance is usually
  /// below 0.01, so 0.015 still filters it. Persisted as
  /// 'close_speech_threshold'.
  double closeSpeechThreshold = 0.015;

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
  /// Movie-style subtitle: the TTS segment currently being spoken. Unlike
  /// [liveText] (the whole accumulated answer), this holds one segment at
  /// a time and only appears once its audio starts playing, so the text
  /// on screen stays in sync with the voice instead of running ahead of
  /// it. Shown by the home screen while [subtitleVisible] is true.
  String subtitleText = '';
  /// Whether the subtitle line is currently shown. Set when a segment's
  /// audio starts playing, cleared a few seconds after speech ends (see
  /// [_scheduleSubtitleHide]) or when the user interrupts / starts talking.
  bool subtitleVisible = false;
  /// Latest TTS text segment received but not yet spoken: promoted to
  /// [subtitleText] by [_promoteSubtitle] when its audio is first fed to
  /// the player, so the line appears with the voice, not with the network
  /// message that arrived up to a buffer earlier.
  String? _pendingSubtitle;
  /// Hides the subtitle this long after speech ends, so the last line
  /// stays readable briefly and then the stage is clean again.
  static const Duration _subtitleHideDelay = Duration(seconds: 8);
  Timer? _subtitleHideTimer;
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

  /// Exposed for the debug screen to inspect pending attachment state.
  String? get pendingTextAttachment => _pendingTextAttachment;
  /// Exposed for the debug screen to inspect pending attachment state.
  PdfExtraction? get pendingPdf => _pendingPdf;
  /// Exposed for the debug screen to inspect pending attachment state.
  Uint8List? get pendingImageAttachment => _pendingImageAttachment;
  /// Exposed for the debug screen to inspect pending attachment state.
  String get pendingImageQuestion => _pendingImageQuestion;
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

  /// True while the mic stream is open for the wake word listener.
  /// The debug screen shows this: if the detector is running but the
  /// mic is not active, the spotter hears nothing and the wake word
  /// can never fire.
  bool get wakeWordMicActive => _wakeWordMicActive;

  /// RMS of the mic audio in the last ~1 s (0.0 when the mic is
  /// silent/dead). The debug screen shows this next to
  /// [closeSpeechThreshold]: a wake-word hit is rejected by the
  /// external-sound filter when this is below the threshold, so a
  /// user speaking from a distance can see the number and raise or
  /// lower the threshold in Settings accordingly.
  double get micRecentRms => _micRecentRms();
  // A wake word spoken while the socket is down (the usual aftermath of the
  // server taking the session on "bye bye"): connect() consumes this once the
  // socket is up and starts the conversation, so the user does not have to
  // repeat the wake word. Cleared on every connect failure and disconnect.
  bool _pendingWakeWordStart = false;
  StreamSubscription<bool>? _musicPlaybackSubscription;

  /// Live audio level (0.0–1.0) for the oscilloscope-style visualizer.
  /// Emits mic level while listening and TTS level while speaking, so the
  /// UI can show a waveform that reacts to the mic or the assistant's voice.
  final StreamController<double> _audioLevelController =
      StreamController<double>.broadcast();
  Stream<double> get audioLevelStream => _audioLevelController.stream;

  /// Lip-sync phoneme analysis (MFCC + vowel templates, see
  /// lipsync_mfcc.dart) for the Live2D mouth. The TTS PCM is analyzed
  /// in the feed path and the resulting vowel frame is emitted on
  /// [lipSyncStream], delayed by the same player-buffer latency as the
  /// RMS levels, so the mouth shape lands when its audio actually
  /// sounds. The avatar falls back to the RMS-only mouth when nothing
  /// listens to this stream.
  final LipSyncAnalyzer _lipSyncAnalyzer = LipSyncAnalyzer();
  final StreamController<LipSyncFrame> _lipSyncController =
      StreamController<LipSyncFrame>.broadcast();
  Stream<LipSyncFrame> get lipSyncStream => _lipSyncController.stream;

  /// Avatar gesture requests (MCP `avatar_gesture` tool): the LLM asks
  /// the avatar to perform a movement (wave, nod, happy, ...). Emitted
  /// as the canonical gesture name; both the 3D avatar and the Live2D
  /// avatar subscribe and play their own version of it. Broadcast so
  /// the tool can fire even when only one avatar is on screen.
  final StreamController<String> _avatarGestureController =
      StreamController<String>.broadcast();
  Stream<String> get avatarGestureStream => _avatarGestureController.stream;

  /// Request the avatar to perform [gesture] (canonical name or an
  /// alias; unknown names are ignored by the avatar). Called by the
  /// MCP `avatar_gesture` tool and by the UI.
  void requestAvatarGesture(String gesture) {
    final name = AvatarGesture.normalize(gesture);
    if (name == null) return;
    if (!_avatarGestureController.isClosed) {
      _avatarGestureController.add(name);
    }
  }

  /// Request the avatar to show [expression] (canonical name or an
  /// alias; unknown names are ignored). Called by the MCP
  /// `avatar_expression` tool. Sets [emotion] so the on-screen avatar
  /// (3D or Live2D) renders it, like a server `llm` emotion. When
  /// [durationMs] is positive the expression holds only that long and
  /// then returns to 'neutral' — but only if the emotion has not
  /// changed since (a newer server emotion is never clobbered). With
  /// 0 it holds until the next server emotion or device-state change,
  /// exactly like a normal server emotion.
  void requestAvatarExpression(String expression, [int durationMs = 0]) {
    final name = AvatarExpression.normalize(expression);
    if (name == null) return;
    emotion = name;
    notifyListeners();
    if (durationMs > 0) {
      Timer(Duration(milliseconds: durationMs), () {
        if (emotion == name) {
          emotion = 'neutral';
          notifyListeners();
        }
      });
    }
  }

  /// 16 kHz PCM accumulator for the lip-sync analyzer (64 ms windows).
  final List<double> _lipSyncPcm = <double>[];

  /// Latest emitted lip-sync frame, for the debug screen (no
  /// notifyListeners -- it is polled, so a 50 Hz frame rate does not
  /// rebuild the UI 50 times a second).
  LipSyncFrame? _lastLipSyncFrame;
  LipSyncFrame? get lastLipSyncFrame => _lastLipSyncFrame;

  /// Temporal smoothing for the vowel frames (see LipSyncSmoother):
  /// without it the winner-take-all vowel flips every frame and the
  /// mouth vibrates. Stateful across utterances on purpose -- the
  /// carry-over is one or two blended frames at most.
  final LipSyncSmoother _lipSyncSmoother = LipSyncSmoother();

  /// Ring buffer of recent mic audio (16 kHz mono floats, last 1 s)
  /// for the speech gate: barge-in and the wake word must only fire on
  /// human vowels, not on music or ambient noise (see
  /// [LipSyncFrame.isSpeechLike]). Filled in [_handleMicData] before
  /// the detector is fed, so the audio that triggered a detection is
  /// already in the buffer when the gate checks it.
  ///
  /// A fixed circular buffer ([MicRingBuffer]): the previous
  /// `List<double>` + removeRange(0, excess) shifted up to 16000 doubles
  /// on every mic chunk. Readers use [MicRingBuffer.snapshot] (a copy,
  /// but only on wake-word/barge-in checks) or [MicRingBuffer.recentRms].
  final MicRingBuffer _micRing = MicRingBuffer();
  /// Current audio level (0.0–1.0), reflecting the mic level while listening
  /// or the TTS level while speaking. Exposed for the debug screen.
  double get audioLevel =>
      _deviceState == DeviceState.listening ? _micLevel : _ttsLevel;
  double _micLevel = 0.0;
  double _ttsLevel = 0.0;
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

  /// One player buffer in bytes (see _startPlayerStream). The player sounds
  /// fed audio roughly one buffer after it is fed, so mouth/waveform levels
  /// are delayed by this long (see _emitTtsLevelsDelayed).
  static const int _playerBufferBytes = 8192;
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

  /// Consecutive milliseconds of user-speech-like mic audio during TTS,
  /// for the speech barge-in detector below.
  double _speechLoudMs = 0;

  /// RMS of the most recently played TTS burst, for the echo guard: the
  /// mic hears the speaker, so mic energy alone cannot tell user speech
  /// from the assistant's own voice. Only mic audio clearly louder than
  /// what was just played counts as an interruption.
  double _lastTtsOutputRms = 0;
  DateTime? _lastTtsOutputAt;
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
    // Wire the MCP avatar_gesture tool to the avatar: when the LLM
    // calls it, the gesture is emitted on avatarGestureStream and the
    // on-screen avatar (3D or Live2D) plays it.
    McpRuntime.onAvatarGesture = (gesture, durationMs) {
      requestAvatarGesture(gesture);
    };
    // Wire the MCP avatar_expression tool to the avatar: when the LLM
    // calls it, the expression is set on the controller and the
    // on-screen avatar (3D or Live2D) shows it.
    McpRuntime.onAvatarExpression = (expression, durationMs) {
      requestAvatarExpression(expression, durationMs);
    };
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
    final storedCharacter = _preferences?.getString('character_model_url');
    characterModelUrl = storedCharacter != null && storedCharacter.trim().isNotEmpty
        ? storedCharacter.trim()
        : Live2dCharacters.defaultModelUrl;
    final storedCharacter3d = _preferences?.getString('avatar_3d_model');
    character3dModelUrl = storedCharacter3d != null && storedCharacter3d.trim().isNotEmpty
        ? storedCharacter3d.trim()
        : 'models/halfBody.glb';
    mouthStrength = (_preferences?.getDouble('mouth_strength') ?? 0.5).clamp(0.0, 1.0);
    externalSoundFilter = _preferences?.getBool('external_sound_filter') ?? true;
    closeSpeechThreshold =
        (_preferences?.getDouble('close_speech_threshold') ?? 0.015).clamp(0.01, 0.2);
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
        // Music stopped: the audio session changes when just_audio
        // releases focus, and the mic stream can die in that transition
        // (or have been closed because wakeWordListening was briefly
        // false). Either way the mic must be re-armed here, or the app
        // sits idle with every flag saying it listens while the
        // detector hears nothing -- "mic tidak direspon" after stopping
        // a song. _ensureWakeWordArmed reopens the mic when it is dead
        // and is a no-op when it is already alive.
        if (wakeWordOptions.enabled) {
          unawaited(_ensureWakeWordArmed());
        } else if (_wakeWordMicActive) {
          unawaited(_closeMicStream());
        }
      }
    });

    // Listen for audio interruptions (phone notification, call, alarm).
    // A notification takes audio focus and can leave the flutter_sound
    // TTS player "started" but silent -- the app thinks it is speaking
    // while nothing comes out, and the mic stream can die the same way.
    // When the interruption ends, re-activate the session and restart
    // the TTS player if it was playing, so the voice comes back instead
    // of staying silent until the next utterance. The mic watchdog
    // (see _checkMicStreamAlive) reopens a dead mic stream the same
    // way; together they make the app recover from a notification
    // instead of going deaf-and-mute.
    unawaited(_listenForAudioInterruptions());

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
    String? newCharacterModelUrl,
    String? newCharacter3dModelUrl,
    double? newMouthStrength,
    bool? newExternalSoundFilter,
    double? newCloseSpeechThreshold,
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
    if (newCharacterModelUrl != null && newCharacterModelUrl.trim().isNotEmpty) {
      characterModelUrl = newCharacterModelUrl.trim();
      await _preferences?.setString('character_model_url', characterModelUrl);
    }
    if (newCharacter3dModelUrl != null && newCharacter3dModelUrl.trim().isNotEmpty) {
      character3dModelUrl = newCharacter3dModelUrl.trim();
      await _preferences?.setString('avatar_3d_model', character3dModelUrl);
    }
    if (newMouthStrength != null) {
      mouthStrength = newMouthStrength.clamp(0.0, 1.0);
      await _preferences?.setDouble('mouth_strength', mouthStrength);
    }
    if (newExternalSoundFilter != null) {
      externalSoundFilter = newExternalSoundFilter;
      await _preferences?.setBool('external_sound_filter', externalSoundFilter);
    }
    if (newCloseSpeechThreshold != null) {
      closeSpeechThreshold = newCloseSpeechThreshold.clamp(0.01, 0.2);
      await _preferences?.setDouble('close_speech_threshold', closeSpeechThreshold);
    }
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
    // Back to standby: the wake word must work again after goodbye,
    // even if the session ended while the detector's flags were down.
    await _ensureWakeWordArmed();
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
  /// here, and the detector subscribes to the audio stream. The detector is
  /// on-device, so [allowOffline] lets _ensureWakeWordArmed arm it while the
  /// socket is down (after the server took the session); the plain call keeps
  /// the old guard so a normal connect still loads the model only after the
  /// handshake, not competing with it for CPU.
  Future<void> _startWakeWordDetector({bool allowOffline = false}) async {
    if (!isConnected && !allowOffline) return;
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

  /// Re-arms wake word listening after a session ends. Without this, a
  /// session that ended while the detector's flags were down leaves the
  /// app deaf: connected and idle, but no longer accepting the wake word
  /// after goodbye. The detector itself is reloaded only when it is not
  /// running at all; when it is running but the flags were reset, only
  /// the mic stream and flags are restored, so the ONNX model is not
  /// reloaded on every session end. The detector is local, so arming does
  /// not need a socket: after the server takes the session on "bye bye"
  /// the detector and mic are armed here even while disconnected, and a
  /// wake word spoken then reconnects (see _handleWakeWordDetected). A
  /// listening flag alone is not trusted either: if the recorder stream
  /// died, the mic is reopened even when the flag still says listening.
  Future<void> _ensureWakeWordArmed() async {
    if (!wakeWordOptions.enabled) return;
    if (!wakeWordDetector.isRunning) {
      await _startWakeWordDetector(allowOffline: true);
      return;
    }
    // A listening flag alone does not prove the mic is alive: if the recorder
    // stream died (its onError/onDone nulled the subscription), the detector
    // hears nothing while every flag still says it is listening. Reopen the
    // mic in that case instead of returning early.
    if (wakeWordListening && _micSubscription != null) return;
    wakeWordDetector.onDetected = _handleWakeWordDetected;
    try {
      if (!isRecording && !_wakeWordMicActive) await _openMicStream();
      _wakeWordMicActive = !isRecording;
      wakeWordListening = true;
      wakeWordError = '';
      if (!isRecording) _setDeviceState(DeviceState.idle);
    } catch (error) {
      wakeWordListening = false;
      wakeWordError = error.toString();
      _reportError('Wake word gagal di-rearm: $wakeWordError');
    }
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
    // The keyword spotter can fire on music or noise that happens to
    // contain similar phonemes. Only act when the audio that triggered
    // it actually contains human vowels; otherwise ignore the hit
    // instead of answering the music. (The detector already paused
    // itself for 300 ms; that brief pause is harmless.)
    if (!_micIsSpeechLike()) return;
    // External-sound filter (TV): TV audio is speech too, so it passes
    // the vowel gate above. But it is usually much quieter at the mic
    // than the user speaking close to the phone. When the filter is on,
    // a hit only counts when the recent mic audio is loud enough to be
    // the user nearby; a distant TV is ignored instead of answered.
    // This also applies while TTS is playing: the wake word still
    // interrupts (like Gemini) when the user says it close by, but a
    // TV in the background cannot cut the answer off.
    if (!shouldAcceptWakeWordHit(
      filterEnabled: externalSoundFilter,
      hasEnoughAudio: _micRing.length >= LipSyncAnalyzer.windowSamples,
      recentRms: _micRecentRms(),
      closeThreshold: closeSpeechThreshold,
    )) {
      return;
    }
    if (!isConnected) {
      // The wake word fired while idle and disconnected -- the usual
      // "bye bye" aftermath when the server took the session and
      // auto-reconnect is off. Dropping the hit here is exactly the
      // "wake word does nothing after goodbye" symptom, so reconnect and
      // start the conversation once the socket is up instead.
      _pendingWakeWordStart = true;
      unawaited(connect());
      return;
    }
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
      // A wake word that fired while disconnected asked for a conversation:
      // the socket is up now, so start it instead of making the user repeat
      // the wake word.
      if (_pendingWakeWordStart) {
        _pendingWakeWordStart = false;
        if (wakeWordOptions.enabled) {
          autoSessionActive = true;
          if (!isRecording) {
            unawaited(startVoice());
          }
          _sendJson(
            ProtocolMessages.detectText(sessionId, wakeWordOptions.wakeWord),
          );
          _setDeviceState(DeviceState.listening);
        }
      }
    } catch (error) {
      await _closeSocket();
      isConnected = false;
      isConnecting = false;
      _pendingWakeWordStart = false;
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
    _pendingWakeWordStart = false;
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
    _subtitleHideTimer?.cancel();
    _subtitleHideTimer = null;
    _pendingSubtitle = null;
    subtitleText = '';
    subtitleVisible = false;
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
    _addChatMessage(ChatMessage(text: displayText, isUser: true));
    // The conversation is continuous, so a text send keeps the session alive and
    // the state stays listening while the answer is being prepared.
    autoSessionActive = true;
    _setDeviceState(DeviceState.listening);
    _sendJson(ProtocolMessages.listenStart(sessionId, 'realtime'));
    _sendJson(ProtocolMessages.detectText(sessionId, detectText));
    // Open the mic so the user's next sentence reaches the server after TTS
    // ends. Without this, the server is in listening mode but no audio flows:
    // _suppressMic gates the uplink during TTS and the post-TTS silence period,
    // and once both clear the recorder is still closed because startVoice() was
    // never called. The user then has to press the button again, but
    // toggleConversation() sees autoSessionActive=true and calls
    // stopConversation() instead -- exactly the "mic tidak direspon setelah TTS"
    // symptom. startVoice() is a no-op when already recording, so this is safe
    // to call unconditionally.
    if (!isRecording) {
      unawaited(startVoice());
    }
    return true;
  }

  Future<void> startVoice() async {
    if (!isConnected || isRecording) return;
    // The user is about to speak: the assistant's old subtitle must go.
    _subtitleHideTimer?.cancel();
    _subtitleHideTimer = null;
    _pendingSubtitle = null;
    subtitleVisible = false;
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
    _lastMicDataAt = DateTime.now();
    final stream = await _recorder.startStream(_micConfig);
    _micSubscription = stream.listen(
      _handleMicData,
      onError: (_) => _handleMicStreamDied(),
      onDone: () => _handleMicStreamDied(),
    );
    _startMicWatchdog();
  }

  /// Start the mic watchdog: every 2 s it checks whether the stream
  /// that should be open has actually delivered data recently. The
  /// recorder can die silently (a notification taking audio focus is
  /// the usual killer) without firing onError/onDone, so the
  /// subscription stays non-null, _openMicStream's guard blocks every
  /// reopen, and the app sits armed-but-deaf with a flat waveform.
  /// The watchdog is the only thing that notices.
  void _startMicWatchdog() {
    _micWatchdogTimer?.cancel();
    _micWatchdogTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      _checkMicStreamAlive();
    });
  }

  void _stopMicWatchdog() {
    _micWatchdogTimer?.cancel();
    _micWatchdogTimer = null;
  }

  void _checkMicStreamAlive() {
    if (_micSubscription == null) return;
    // Only the wake-word listener and the uplink need the mic; if
    // neither is active there is nothing to keep alive.
    if (!isRecording && !_wakeWordMicActive) return;
    final last = _lastMicDataAt;
    if (last == null) return;
    if (DateTime.now().difference(last) < const Duration(seconds: 3)) return;
    // No mic data for 3 s while the stream should be open: the stream
    // died silently. Drop the dead subscription (its non-null guard is
    // what blocked every reopen) and open a fresh one; _openMicStream
    // restarts the watchdog, and the wake word is re-armed if it was
    // the listener.
    _micSubscription = null;
    _wakeWordMicActive = false;
    unawaited(_openMicStream());
    if (!isRecording && wakeWordOptions.enabled) {
      unawaited(_ensureWakeWordArmed());
    }
  }

  /// Listen for audio interruptions (notification, call, alarm) and
  /// recover the TTS player when they end. A notification takes audio
  /// focus; the flutter_sound player can be left "started" but silent,
  /// so the assistant keeps "speaking" with no sound until the next
  /// utterance. Restarting the player stream here (the same serialized
  /// restart the wake-word interrupt uses) brings the voice back for
  /// the utterance that is still in flight. Best-effort: if the
  /// audio_session plugin is unavailable the app keeps its old
  /// behavior.
  Future<void> _listenForAudioInterruptions() async {
    try {
      final session = await AudioSession.instance;
      _audioInterruptionSubscription =
          session.interruptionEventStream.listen((event) {
        if (event.begin) return;
        // Interruption ended: re-activate the session so the TTS
        // player can reclaim focus, then restart it if it was playing.
        unawaited(() async {
          try {
            await session.setActive(true);
          } catch (_) {}
          if (_playerStarted && isConnected) {
            await _stopPlayerStream();
            if (isConnected) await _startPlayerStream();
          }
        }());
      });
    } catch (_) {
      // audio_session is best-effort; without it the app keeps the old
      // behavior (TTS may stay silent after a notification until the
      // next utterance).
    }
  }

  /// The recorder stream died underneath us (audio-focus loss, device error):
  /// the subscription object stays non-null but delivers nothing, so drop it
  /// and re-arm wake word if it was the listener. Without this the app looks
  /// armed (flags up, subscription non-null) while the detector hears silence,
  /// and _openMicStream's non-null guard blocks every reopen attempt.
  /// A normal _closeMicStream cancels the subscription, which does not fire
  /// onError/onDone, so this never runs on a deliberate close.
  void _handleMicStreamDied() {
    if (_micSubscription == null) return;
    _micSubscription = null;
    if (!isRecording && wakeWordOptions.enabled) {
      _wakeWordMicActive = false;
      unawaited(_ensureWakeWordArmed());
    }
  }

  Future<void> _closeMicStream() async {
    // Only close if the mic is not needed for wake word detection
    if (_wakeWordMicActive && wakeWordOptions.enabled && wakeWordListening) {
      return; // Keep mic open for wake word detector
    }
    _stopMicWatchdog();
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
              // DO NOT clear _pendingImageQuestion - preserve the user's question
              // for the vision model analysis
              _pendingAttachmentName = null;
            }
          }
          // Reset the speaking watchdog on MCP tool-call activity. During long
          // document reads the LLM makes many tools/call requests without
          // producing TTS text, so without this reset the 15s watchdog would
          // fire and tear down the speaking session mid-read.
          if (method == 'tools/call' && isSpeaking) {
            _speakingWatchdog?.cancel();
            _speakingWatchdog = Timer(_speakingTimeout, _handleLostTtsStop);
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
          _addChatMessage(ChatMessage(text: text, isUser: true));
        }
        // Mirrors UiPresenter.show_protocol_message: stt text also updates the
        // live chat panel line (mainModel.ttsText) in the desktop GUI.
        liveText = text;
      }
    } else if (type == 'tts') {
      final state = data['state'];
      if (state == 'start') {
        _discardTtsAudioUntilStart = false;
        _pendingSubtitle = null;
        _subtitleHideTimer?.cancel();
        _subtitleHideTimer = null;
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
        // Staged, not shown yet: _promoteSubtitle displays it when its
        // audio reaches the player, which is what keeps the subtitle in
        // sync with the voice.
        _pendingSubtitle = text;
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
      _addChatMessage(ChatMessage(text: _assistantText, isUser: false));
      _assistantMessageIndex = messages.length - 1;
    } else {
      _assistantText = appendTtsSegment(_assistantText, text);
      messages[_assistantMessageIndex!] =
          messages[_assistantMessageIndex!].copyWith(text: _assistantText);
    }
  }


  Future<void> _interruptTtsForTextInput() async {
    if (!isSpeaking && !_suppressMic) return;
    _hideSubtitle();
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
        bufferSize: _playerBufferBytes,
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

  /// True when the recent mic audio looks like human vowels (MFCC
  /// vowel match, see [LipSyncFrame.isSpeechLike]). The energy gates
  /// alone cannot tell speech from music or noise -- both are loud --
  /// so the barge-in trigger and the wake-word callback both ask this
  /// before responding. It scans the last second of the ring buffer,
  /// not just the latest window: the spotter fires after the phrase
  /// ends, so the latest 64 ms is already silence. Runs a few 64 ms
  /// analyses per check, only when a trigger is already pending, so it
  /// costs nothing in silence. Too little buffered audio fails open
  /// (the detector needs ~1 s to fire anyway, so the buffer is full by
  /// then in practice).
  bool _micIsSpeechLike() {
    if (_micRing.length < LipSyncAnalyzer.windowSamples) return true;
    return _lipSyncAnalyzer.containsSpeech(_micRing.snapshot());
  }

  /// Whether a wake-word hit counts as the user speaking close by,
  /// for the external-sound (TV) filter. TV audio is speech too, so
  /// it passes the vowel gate — but it is usually much quieter at the
  /// mic than the user. When the filter is off, or there is not enough
  /// mic audio buffered to judge, the hit is accepted (fail open, so
  /// a fresh start never eats the first wake word).
  static bool shouldAcceptWakeWordHit({
    required bool filterEnabled,
    required bool hasEnoughAudio,
    required double recentRms,
    required double closeThreshold,
  }) {
    if (!filterEnabled) return true;
    if (!hasEnoughAudio) return true;
    return recentRms >= closeThreshold;
  }

  /// RMS of the mic audio in the ring buffer (the last ~1 s). Used by
  /// the external-sound filter: a wake-word hit only counts as the
  /// user when this is loud enough to be speech close to the phone.
  /// TV audio is speech too (so it passes the vowel gate above), but
  /// it is usually much quieter at the mic than the user.
  double _micRecentRms() {
    return _micRing.recentRms();
  }

  /// Gemini/ChatGPT-style barge-in on user SPEECH (not just the wake
  /// word) while the assistant is speaking. A trigger needs sustained
  /// speech-level energy, not a click or a noise burst, and it must
  /// clear the echo guard: the mic level has to exceed twice the TTS
  /// level that was just played. On trigger it reuses [interrupt], so
  /// the session goes back to listening and the server hears the rest
  /// of the utterance and answers directly, with no extra tap or wake
  /// word needed.
  ///
  /// Honest limit, no AEC on Android: the guard compares levels, it does
  /// not cancel echo. Quiet speech over loud TTS can still be missed,
  /// and the wake word stays the reliable interrupt; this is an
  /// additional path, not a replacement.
  void _checkSpeechBargeIn(Uint8List data, DateTime? suppressedUntil) {
    if (!isSpeaking || !isConnected) {
      _speechLoudMs = 0;
      return;
    }
    if (suppressedUntil != null && DateTime.now().isBefore(suppressedUntil)) {
      _speechLoudMs = 0;
      return;
    }
    if (data.lengthInBytes < 2) return;
    final sampleCount = data.lengthInBytes ~/ 2;
    final samples = Int16List.view(
      data.buffer,
      data.offsetInBytes,
      sampleCount,
    );
    final micRms = _rmsLevel(samples);
    final lastTtsAt = _lastTtsOutputAt;
    final ttsRms = (lastTtsAt != null &&
            DateTime.now().difference(lastTtsAt) < const Duration(milliseconds: 500))
        ? _lastTtsOutputRms
        : 0.0;
    // Normal speech is ~0.03-0.3 RMS; the floor sits below that but far
    // above the detector's 0.001 near-silence gate, and the echo guard
    // demands the mic clearly beat the speaker output just played.
    final speechLike = micRms > 0.02 && micRms > 2.0 * ttsRms;
    if (speechLike) {
      _speechLoudMs += sampleCount / 16.0;
      if (_speechLoudMs >= 400) {
        _speechLoudMs = 0;
        // Energy alone cannot tell speech from music or noise -- both
        // are loud for 400 ms. Only interrupt when the sustained sound
        // actually contains human vowels; otherwise reset and wait for
        // the next 400 ms window instead of answering the music.
        if (!_micIsSpeechLike()) return;
        unawaited(interrupt());
      }
    } else {
      _speechLoudMs = 0;
    }
  }

  void _handleMicData(Uint8List data) {
    // The mic watchdog (see _checkMicStreamAlive) uses this to tell a
    // live stream from one that died silently: a subscription that
    // delivers nothing leaves this timestamp stale.
    _lastMicDataAt = DateTime.now();
    // Feed the speech-gate ring buffer first: the wake word and the
    // barge-in check below both read it, and the audio that triggers a
    // detection must already be in it when they do. The ring keeps
    // the last 1 s (16 kHz) and overwrites the oldest samples, so no
    // trimming is needed here.
    if (data.length >= 2) {
      final byteData = ByteData.sublistView(data);
      final count = data.length ~/ 2;
      for (var i = 0; i < count; i++) {
        _micRing.add(byteData.getInt16(i * 2, Endian.little) / 32768.0);
      }
    }
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
    // Speech barge-in runs on the same stream: the mic stays open
    // through TTS (only the uplink is suppressed), so user speech
    // during an answer can be caught here as well as by the keyword.
    _checkSpeechBargeIn(data, suppressedUntil);
    if (!isRecording && !isSpeaking && _wakeWordMicActive) {
      // Idle wake-word mic is live: show its level on the waveform even
      // though nothing is being recorded. The waveform is the mic-path
      // indicator -- flat in silence while the path is open, dancing with
      // any sound -- so a path that died after goodbye shows up as a dead
      // line instead of hiding behind "idle shows nothing". During TTS
      // this stays off: the mic hears the speaker (no AEC) and must not
      // pollute the TTS waveform.
      if (data.length >= 2 && data.offsetInBytes % 2 == 0) {
        final samples = Int16List.view(
          data.buffer,
          data.offsetInBytes,
          data.length ~/ 2,
        );
        if (!_audioLevelController.isClosed) {
          _audioLevelController.add(_rmsLevel(samples));
        }
      }
    }
    if (!isRecording) return;
    _pendingMicBytes.addAll(data);
    const frameBytes = 320 * 2;
    while (_pendingMicBytes.length >= frameBytes) {
      // takeFrame returns a view into the queue's backing store (no
      // copy): read it fully (RMS + Opus encode, both synchronous)
      // before consuming it, and never hold it past this loop body.
      final bytes = _pendingMicBytes.takeFrame(frameBytes)!;
      final samples = Int16List.view(bytes.buffer, bytes.offsetInBytes, 320);
      _micLevel = _rmsLevel(samples);
      if (!_audioLevelController.isClosed) {
        _audioLevelController.add(_micLevel);
      }
      final packet = _encoder?.encode(input: samples);
      // During the post-TTS silence period the speaker is still draining; sending mic
      // audio now makes the assistant hear and answer itself in realtime mode.
      if (packet != null && isConnected && !_suppressMic) _channel?.sink.add(packet);
      _pendingMicBytes.consume(frameBytes);
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
      // NOTE: the mouth/waveform level used to be emitted here, at decode
      // time, but the PCM only reaches the speaker later (after the 240 ms
      // prebuffer plus the player queue), so the mouth moved out of sync
      // with the audible speech. The level is now emitted in
      // _feedTtsFromBuffer/_flushTtsBuffer, at the moment the audio is
      // actually fed to the player.
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
          // What was just played, for the speech barge-in echo guard.
          _lastTtsOutputRms = _rmsLevel(merged);
          _lastTtsOutputAt = DateTime.now();
          // Mouth + waveform follow the sound that is actually playing:
          // one delayed level per frame (see _emitTtsLevelsDelayed),
          // not one immediate level per burst. _ttsLevel keeps the
          // immediate burst RMS for the debug-screen getter.
          _ttsLevel = _lastTtsOutputRms;
          _emitTtsLevelsDelayed(burst);
          _emitLipSyncDelayed(burst);
          // Its audio is playing now: show the staged segment with it.
          _promoteSubtitle();
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

  /// Emits one mouth/waveform level per TTS frame, delayed so each level
  /// lands when its audio actually sounds instead of when it was fed.
  /// The player sounds fed audio roughly one buffer after the feed
  /// (bufferSize, ~170 ms at 24 kHz), so emitting at feed time made the
  /// mouth move ahead of the audible speech; and one level per 6-frame
  /// burst made the mouth step every 120 ms instead of tracking syllables.
  /// Each frame (20 ms of audio) gets its own RMS, scheduled at buffer
  /// latency plus its offset inside the burst, reproducing the original
  /// 20 ms cadence at the speaker. A stale timer firing after an
  /// interrupt is harmless: the avatar closes the mouth when not speaking,
  /// and the waveform just shows a brief tail blip.
  void _emitTtsLevelsDelayed(List<Int16List> frames) {
    final rate = effectiveOutputSampleRate;
    if (rate <= 0 || frames.isEmpty) return;
    var offsetMs = _playerBufferBytes ~/ 2 * 1000 ~/ rate;
    for (final frame in frames) {
      if (frame.isEmpty) continue;
      final level = _rmsLevel(frame);
      Timer(Duration(milliseconds: offsetMs), () {
        if (!_audioLevelController.isClosed) {
          _audioLevelController.add(level);
        }
      });
      offsetMs += frame.length * 1000 ~/ rate;
    }
  }

  /// Analyzes the TTS burst's vowels and emits one [LipSyncFrame] per
  /// 20 ms TTS frame, delayed by the player-buffer latency like
  /// [_emitTtsLevelsDelayed]. Each analysis reads the last 64 ms window
  /// (one-frame hop: the oldest frame's samples are dropped after each
  /// analysis), so consecutive windows overlap by ~70% and the vowel
  /// changes smoothly instead of jumping.
  ///
  /// Cadence matters: the previous version throttled analyses to one
  /// per 33 ms of wall-clock time, but a burst's 6 frames are processed
  /// within ~1 ms, so only one analysis ran per 120 ms burst (~8 Hz).
  /// At 8 Hz the smoother's lag was ~300 ms (mouth behind the voice)
  /// and the target jumped every 120 ms (mouth stepping/vibrating) --
  /// exactly the device report on the previous build. One analysis per
  /// frame (~50 Hz) brings the smoother lag to ~50 ms and the target
  /// moves every 20 ms, smoother than the avatar's 30 Hz ticker.
  void _emitLipSyncDelayed(List<Int16List> frames) {
    final rate = effectiveOutputSampleRate;
    if (rate <= 0 || frames.isEmpty) return;
    var offsetMs = _playerBufferBytes ~/ 2 * 1000 ~/ rate;
    for (final frame in frames) {
      if (frame.isEmpty) continue;
      // Resample this 20 ms frame to the analyzer's 16 kHz and append.
      final floats = List<double>.generate(
        frame.length,
        (i) => frame[i] / 32768.0,
      );
      final resampled = LipSyncAnalyzer.resampleLinear(floats, rate, 16000);
      _lipSyncPcm.addAll(resampled);
      // Keep the accumulator bounded: the analyzer only ever reads the
      // last 64 ms, so anything older than two windows is dead weight.
      if (_lipSyncPcm.length > 4096) {
        _lipSyncPcm.removeRange(0, _lipSyncPcm.length - 4096);
      }
      if (_lipSyncPcm.length >= LipSyncAnalyzer.windowSamples) {
        final window = _lipSyncPcm.sublist(
          _lipSyncPcm.length - LipSyncAnalyzer.windowSamples,
        );
        // Smooth the vowel weights before emitting: the raw match is
        // winner-take-all and flips every frame, which made the mouth
        // vibrate (see LipSyncSmoother).
        final lipFrame =
            _lipSyncSmoother.smooth(_lipSyncAnalyzer.analyze(window));
        // The analyzed window is 64 ms long and ends with this frame;
        // its audible moment is its center, 32 ms earlier than the
        // frame end, so subtract half the window.
        const windowCenterMs =
            LipSyncAnalyzer.windowSamples * 1000 ~/ 16000 ~/ 2;
        final delayMs =
            offsetMs + frame.length * 1000 ~/ rate - windowCenterMs;
        Timer(Duration(milliseconds: delayMs < 0 ? 0 : delayMs), () {
          if (!_lipSyncController.isClosed) {
            _lipSyncController.add(lipFrame);
            _lastLipSyncFrame = lipFrame;
          }
        });
        // One-frame hop: drop the oldest frame's samples so the next
        // analysis slides the window by exactly 20 ms.
        _lipSyncPcm.removeRange(0, resampled.length);
      }
      offsetMs += frame.length * 1000 ~/ rate;
    }
  }

  /// Root-mean-square of a 16-bit PCM frame, normalized to 0.0–1.0.
  /// Uses a fast integer approximation: sum of squares / (count * 32768^2),
  /// then sqrt. This is cheap enough to run on every mic/TTS frame.
  static double _rmsLevel(Int16List samples) {
    if (samples.isEmpty) return 0.0;
    var sumSq = 0;
    for (final s in samples) {
      sumSq += s * s;
    }
    final mean = sumSq / samples.length;
    // NOTE: the previous version returned mean / 32768^2 WITHOUT the sqrt,
    // i.e. the SQUARE of the true RMS. Normal speech (true RMS ~0.03-0.3)
    // then came out as 0.001-0.09, so the avatar mouth and the waveform
    // barely moved. Take the sqrt to get the real RMS.
    final rms = mean > 0 ? sqrt(mean) / 32768.0 : 0.0;
    return rms > 1.0 ? 1.0 : rms;
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

  /// Shows the latest received TTS segment, if any, now that its audio
  /// is actually playing. Called after every player feed, so a segment
  /// appears with its voice rather than with the network message.
  void _promoteSubtitle() {
    final pending = _pendingSubtitle;
    if (pending == null || pending.isEmpty) return;
    _pendingSubtitle = null;
    subtitleText = pending;
    subtitleVisible = true;
    _subtitleHideTimer?.cancel();
    _subtitleHideTimer = null;
    notifyListeners();
  }

  /// Fades the subtitle out [_subtitleHideDelay] after speech ends.
  /// Re-scheduled on every stop, so a new utterance starting within the
  /// window keeps its own line (its first promotion cancels this timer).
  void _scheduleSubtitleHide() {
    _subtitleHideTimer?.cancel();
    _subtitleHideTimer = Timer(_subtitleHideDelay, () {
      _subtitleHideTimer = null;
      subtitleVisible = false;
      notifyListeners();
    });
  }

  /// Hides the subtitle immediately: the user interrupted or started
  /// talking, so the assistant's old line must not linger.
  void _hideSubtitle() {
    _subtitleHideTimer?.cancel();
    _subtitleHideTimer = null;
    _pendingSubtitle = null;
    subtitleVisible = false;
    notifyListeners();
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
    _scheduleSubtitleHide();
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
    _scheduleSubtitleHide();
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
      // One-shot answer finished: back to standby, so the wake word must
      // be armed again for the next hands-free start.
      unawaited(_ensureWakeWordArmed());
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
    final frames = List<Int16List>.from(_ttsBuffer);
    final remaining = _mergeInt16(frames);
    _ttsBuffer.clear();
    try {
      await _player.feedInt16FromStream([remaining]).timeout(_ttsFeedTimeout);
      _lastTtsOutputRms = _rmsLevel(remaining);
      _lastTtsOutputAt = DateTime.now();
      // Tail of the utterance: same delayed per-frame emission as the
      // burst feed, so the mouth follows the tail instead of freezing.
      _ttsLevel = _lastTtsOutputRms;
      _emitTtsLevelsDelayed(frames);
      _emitLipSyncDelayed(frames);
      _promoteSubtitle();
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
    // The socket is gone but the wake word is local: re-arm the detector and
    // mic here, so idle-after-goodbye listens even when auto-reconnect is
    // off and no reconnect ever runs. A later reconnect re-arms again
    // harmlessly. Fire-and-forget so the reconnect scheduling below does not
    // wait for a model load.
    unawaited(_ensureWakeWordArmed());
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
    _pendingWakeWordStart = false;
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
    // The socket died, but the wake word is local: re-arm the detector and
    // mic here too, not only on a clean close. Without this a server
    // time-out "bye bye" with auto-reconnect off leaves the mic path
    // closed (detector stopped above, mic closed by _closeSocket) and no
    // reconnect ever restores it -- the app sits idle, waveform flat,
    // wake word deaf. Fire-and-forget so the reconnect scheduling above
    // does not wait for a model load.
    unawaited(_ensureWakeWordArmed());
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
    _micWatchdogTimer?.cancel();
    _micWatchdogTimer = null;
    _audioInterruptionSubscription?.cancel();
    _audioInterruptionSubscription = null;
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
    _avatarGestureController.close();
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