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
import 'protocol_messages.dart';

class XiaozhiController extends ChangeNotifier {
  static const _secureStorage = FlutterSecureStorage();
  static const _uuid = Uuid();

  final AudioRecorder _recorder = AudioRecorder();
  final FlutterSoundPlayer _player = FlutterSoundPlayer();
  final List<ChatMessage> messages = [];
  final List<int> _pendingMicBytes = [];

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _socketSubscription;
  StreamSubscription<Uint8List>? _micSubscription;
  SimpleOpusEncoder? _encoder;
  SimpleOpusDecoder? _decoder;
  Completer<void>? _helloCompleter;
  SharedPreferences? _preferences;

  String endpoint = '';
  String token = '';
  String deviceId = '';
  String clientId = '';
  String cameraFacing = 'back';
  String status = 'Belum terhubung';
  String sessionId = '';
  String _assistantText = '';
  int? _assistantMessageIndex;
  int _outputSampleRate = 24000;
  bool isConnected = false;
  bool isConnecting = false;
  bool isRecording = false;
  bool isSpeaking = false;
  bool _opusInitialized = false;
  bool _playerStarted = false;

  Future<void> initialize() async {
    _preferences = await SharedPreferences.getInstance();
    endpoint = _preferences?.getString('server_url') ?? '';
    cameraFacing = _preferences?.getString('camera_facing') ?? 'back';
    deviceId = _preferences?.getString('device_id') ?? '';
    clientId = _preferences?.getString('client_id') ?? '';
    token = await _secureStorage.read(key: 'access_token') ?? '';
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
  }) async {
    await disconnect();
    endpoint = newEndpoint.trim();
    token = newToken.trim();
    deviceId = newDeviceId.trim().isEmpty
      ? _uuid.v4().replaceAll('-', '')
      : newDeviceId.trim();
    clientId = newClientId.trim().isEmpty ? _uuid.v4() : newClientId.trim();
    cameraFacing = newCameraFacing == 'front' ? 'front' : 'back';
    await _preferences?.setString('server_url', endpoint);
    await _preferences?.setString('device_id', deviceId);
    await _preferences?.setString('client_id', clientId);
    await _preferences?.setString('camera_facing', cameraFacing);
    await _secureStorage.write(key: 'access_token', value: token);
    status = 'Pengaturan tersimpan';
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
    } catch (error) {
      await _closeSocket();
      isConnected = false;
      isConnecting = false;
      status = 'Gagal terhubung: ${error.toString()}';
    }
    notifyListeners();
  }

  Future<void> disconnect() async {
    if (isRecording) await stopVoice();
    isConnected = false;
    isConnecting = false;
    await _closeSocket();
    if (_playerStarted) {
      await _player.stopPlayer();
      _playerStarted = false;
    }
    sessionId = '';
    status = 'Terputus';
    notifyListeners();
  }

  Future<void> sendText(String value) async {
    final text = value.trim();
    if (!isConnected || text.isEmpty) return;
    messages.add(ChatMessage(text: text, isUser: true));
    status = 'Menunggu jawaban...';
    notifyListeners();
    _sendJson(ProtocolMessages.listenStart(sessionId));
    _sendJson(ProtocolMessages.detectText(sessionId, text));
  }

  Future<void> startVoice() async {
    if (!isConnected || isRecording) return;
    try {
      if (!await _recorder.hasPermission()) {
        status = 'Izin mikrofon diperlukan';
        notifyListeners();
        return;
      }
      _pendingMicBytes.clear();
      _sendJson(ProtocolMessages.listenStart(sessionId));
      final stream = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: 16000,
          numChannels: 1,
        ),
      );
      _micSubscription = stream.listen(_handleMicData);
      isRecording = true;
      status = 'Mendengarkan...';
    } catch (error) {
      status = 'Mikrofon gagal dimulai: ${error.toString()}';
    }
    notifyListeners();
  }

  Future<void> stopVoice() async {
    if (!isRecording) return;
    isRecording = false;
    await _micSubscription?.cancel();
    _micSubscription = null;
    await _recorder.stop();
    _pendingMicBytes.clear();
    _sendJson(ProtocolMessages.listenStop(sessionId));
    status = 'Menunggu jawaban...';
    notifyListeners();
  }

  Future<void> interrupt() async {
    if (!isConnected) return;
    if (isRecording) await stopVoice();
    _sendJson(ProtocolMessages.abort(sessionId));
    if (_playerStarted) {
      await _player.stopPlayer();
      _playerStarted = false;
      await _startPlayerStream();
    }
    isSpeaking = false;
    status = 'Percakapan dihentikan';
    notifyListeners();
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
      _handleAudio(Uint8List.fromList(message));
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

    if (type == 'stt') {
      final text = data['text'];
      if (text is String && text.trim().isNotEmpty) {
        if (messages.isEmpty || !messages.last.isUser || messages.last.text != text) {
          messages.add(ChatMessage(text: text, isUser: true));
        }
      }
    } else if (type == 'tts') {
      final state = data['state'];
      if (state == 'start') {
        isSpeaking = true;
        _assistantText = '';
        _assistantMessageIndex = null;
        status = 'Xiaozhi sedang berbicara';
      }
      final text = data['text'];
      if (text is String && text.isNotEmpty) _appendAssistantText(text);
      if (state == 'stop') {
        isSpeaking = false;
        status = 'Terhubung';
        _assistantMessageIndex = null;
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
      _assistantText += text;
      messages[_assistantMessageIndex!] =
          messages[_assistantMessageIndex!].copyWith(text: _assistantText);
    }
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
    if (!_player.isOpen) await _player.openPlayer();
    await _startPlayerStream();
  }

  Future<void> _startPlayerStream() async {
    await _player.startPlayerFromStream(
      codec: Codec.pcm16,
      numChannels: 1,
      sampleRate: _outputSampleRate,
      interleaved: false,
      bufferSize: 8192,
    );
    _playerStarted = true;
  }

  void _handleMicData(Uint8List data) {
    _pendingMicBytes.addAll(data);
    const frameBytes = 320 * 2;
    while (_pendingMicBytes.length >= frameBytes) {
      final bytes = Uint8List.fromList(_pendingMicBytes.take(frameBytes).toList());
      _pendingMicBytes.removeRange(0, frameBytes);
      final samples = Int16List.view(bytes.buffer, bytes.offsetInBytes, 320);
      final packet = _encoder?.encode(input: samples);
      if (packet != null && isConnected) _channel?.sink.add(packet);
    }
  }

  Future<void> _handleAudio(Uint8List packet) async {
    final decoder = _decoder;
    if (decoder == null || !_playerStarted || packet.isEmpty) return;
    try {
      final pcm = decoder.decode(input: packet);
      if (pcm.isNotEmpty) await _player.feedInt16FromStream([pcm]);
    } catch (_) {
      status = 'Paket audio tidak dapat diputar';
      notifyListeners();
    }
  }

  void _sendJson(Map<String, Object?> message) {
    final channel = _channel;
    if (channel != null && isConnected) channel.sink.add(jsonEncode(message));
  }

  void _handleConnectionFailure(String reason) {
    isConnected = false;
    isConnecting = false;
    isRecording = false;
    status = reason;
    if (!(_helloCompleter?.isCompleted ?? true)) {
      _helloCompleter!.completeError(StateError(reason));
    }
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
    unawaited(_socketSubscription?.cancel());
    unawaited(_micSubscription?.cancel());
    unawaited(_channel?.sink.close());
    _recorder.dispose();
    unawaited(_player.closePlayer());
    _encoder?.destroy();
    _decoder?.destroy();
    super.dispose();
  }
}