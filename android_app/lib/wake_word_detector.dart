// Port of src/audio_processing/wake_word_detect.py (sherpa-onnx KeywordSpotter).
//
// The detector is fed from the same 16 kHz PCM16 microphone stream the controller
// already records for the uplink, so no second recorder is needed. Detection is
// gated by the controller: while the assistant is speaking the mic is suppressed
// (Android has no acoustic echo cancellation), so the spotter must be paused for
// the whole TTS window, otherwise the assistant's own voice out of the speaker
// triggers the wake word.

import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart' show rootBundle;
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa_onnx;

import 'bpe_keyword_converter.dart';

/// Mirrors WAKE_WORD_OPTIONS in src/utils/config_manager.py.
class WakeWordOptions {
  const WakeWordOptions({
    this.enabled = false,
    this.wakeWord = 'Hello Xiaozhi',
    this.numThreads = 2,
    this.maxActivePaths = 2,
    this.keywordsScore = 1.8,
    this.keywordsThreshold = 0.2,
    this.numTrailingBlanks = 1,
  });

  final bool enabled;
  final String wakeWord;
  final int numThreads;
  final int maxActivePaths;
  final double keywordsScore;
  final double keywordsThreshold;
  final int numTrailingBlanks;

  WakeWordOptions copyWith({
    bool? enabled,
    String? wakeWord,
    int? numThreads,
    int? maxActivePaths,
    double? keywordsScore,
    double? keywordsThreshold,
    int? numTrailingBlanks,
  }) =>
      WakeWordOptions(
        enabled: enabled ?? this.enabled,
        wakeWord: wakeWord ?? this.wakeWord,
        numThreads: numThreads ?? this.numThreads,
        maxActivePaths: maxActivePaths ?? this.maxActivePaths,
        keywordsScore: keywordsScore ?? this.keywordsScore,
        keywordsThreshold: keywordsThreshold ?? this.keywordsThreshold,
        numTrailingBlanks: numTrailingBlanks ?? this.numTrailingBlanks,
      );
}

/// Result of [WakeWordDetector.start].
enum WakeWordStartResult {
  /// The spotter is running.
  started,
  /// The feature is disabled in the options.
  disabled,
  /// The wake word could not be converted to a keyword line.
  invalidKeyword,
  /// A model file is missing from the asset bundle.
  modelMissing,
  /// sherpa-onnx failed to load the model or create the stream.
  initFailed,
}

/// Callback invoked with the detected keyword (the display name from the keyword
/// line, e.g. "HELLO-XIAOZHI").
typedef WakeWordCallback = void Function(String keyword);

/// Wraps sherpa_onnx.KeywordSpotter for Android.
///
/// Lifecycle mirrors the Python detector: [start] loads the model and creates the
/// stream, [feed] pushes mic audio, [stop] releases everything. [pause]/[resume]
/// mirror the Python `_paused` flag and are used to keep the spotter quiet while
/// the assistant speaks.
class WakeWordDetector {
  WakeWordDetector();

  static const String _modelAssetDir = 'assets/models/en';

  // 16 kHz mono, matching AudioConfig.INPUT_SAMPLE_RATE and the mic recorder.
  static const int sampleRate = 16000;
  // Mirrors _detection_cooldown in src/audio_processing/wake_word_detect.py.
  static const Duration _cooldown = Duration(milliseconds: 1500);

  sherpa_onnx.KeywordSpotter? _spotter;
  sherpa_onnx.OnlineStream? _stream;
  bool _running = false;
  bool _paused = false;
  DateTime? _lastDetectionAt;
  String _keywordLine = '';
  WakeWordCallback? onDetected;

  /// True when the model is loaded and the detection loop is accepting audio.
  bool get isRunning => _running && !_paused;

  /// Copies the bundled model files out of the asset bundle. Android apps are
  /// sandboxed, so sherpa-onnx cannot open "assets/..." directly; the canonical
  /// sherpa-onnx Flutter examples use the same copy-to-app-support approach.
  Future<String> _copyAssetFile(String src, [String? dst]) async {
    final directory = await getApplicationSupportDirectory();
    dst ??= p.basename(src);
    final target = p.join(directory.path, dst);
    final data = await rootBundle.load(src);
    final exists = await File(target).exists();
    if (!exists || File(target).lengthSync() != data.lengthInBytes) {
      final bytes = data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes);
      await (await File(target).create(recursive: true)).writeAsBytes(bytes);
    }
    return target;
  }

  /// Loads the model and starts detecting. Safe to call repeatedly; a running
  /// detector is stopped first (mirrors WakeWordDetector.initialize in Python,
  /// which stops the old loop and releases the old model before loading).
  Future<WakeWordStartResult> start(WakeWordOptions options) async {
    if (!options.enabled) return WakeWordStartResult.disabled;

    await stop();

    try {
      await sherpa_onnx.initBindingsAsync();
    } catch (error) {
      _lastError = error.toString();
      return WakeWordStartResult.initFailed;
    }

    try {
      final encoderPath = await _copyAssetFile('$_modelAssetDir/encoder.onnx');
      final decoderPath = await _copyAssetFile('$_modelAssetDir/decoder.onnx');
      final joinerPath = await _copyAssetFile('$_modelAssetDir/joiner.onnx');
      final tokensPath = await _copyAssetFile('$_modelAssetDir/tokens.txt');

      // createStream(keywords:) takes an inline keyword line, so a user-defined
      // wake word needs no keywords.txt on disk.
      final converter = BpeKeywordConverter(tokensPath);
      _keywordLine = await converter.convert(options.wakeWord);

      final config = sherpa_onnx.KeywordSpotterConfig(
        feat: const sherpa_onnx.FeatureConfig(sampleRate: sampleRate, featureDim: 80),
        model: sherpa_onnx.OnlineModelConfig(
          transducer: sherpa_onnx.OnlineTransducerModelConfig(
            encoder: encoderPath,
            decoder: decoderPath,
            joiner: joinerPath,
          ),
          tokens: tokensPath,
          numThreads: options.numThreads,
          provider: 'cpu',
        ),
        maxActivePaths: options.maxActivePaths,
        numTrailingBlanks: options.numTrailingBlanks,
        keywordsScore: options.keywordsScore,
        keywordsThreshold: options.keywordsThreshold,
      );

      _spotter = sherpa_onnx.KeywordSpotter(config);
      _stream = _spotter!.createStream(keywords: _keywordLine);
      _running = true;
      _paused = false;
      _lastDetectionAt = null;
      return WakeWordStartResult.started;
    } catch (error) {
      _lastError = error.toString();
      await _release();
      if (error is BpeConversionError) return WakeWordStartResult.invalidKeyword;
      return WakeWordStartResult.initFailed;
    }
  }

  /// Last initialization error, surfaced in the settings UI when detection fails.
  String _lastError = '';
  String get lastError => _lastError;

  /// Stops detecting and releases the model. Mirrors WakeWordDetector.stop +
  /// _release_model: the spotter is freed before the stream.
  Future<void> stop() async {
    _running = false;
    _paused = false;
    await _release();
  }

  Future<void> _release() async {
    final spotter = _spotter;
    final stream = _stream;
    _spotter = null;
    _stream = null;
    try {
      spotter?.free();
    } catch (_) {}
    // The spotter owns the stream at the C++ level; freeing the spotter first
    // avoids a double free (the Python client deletes them in the same order).
    try {
      stream?.free();
    } catch (_) {}
  }

  /// Pauses detection without unloading the model, mirroring WakeWordDetector.pause.
  void pause() => _paused = true;

  /// Resumes detection, mirroring WakeWordDetector.resume.
  void resume() => _paused = false;

  /// Feeds 16 kHz mono PCM16 mic audio. Returns the detected keyword, or null.
  ///
  /// This runs on the mic stream callback, so it must stay cheap: acceptWaveform
  /// copies the samples and the decode loop only runs while the spotter is ready.
  String? feed(Uint8List pcm16) {
    if (!_running || _paused) return null;
    final spotter = _spotter;
    final stream = _stream;
    if (spotter == null || stream == null) return null;
    if (pcm16.length < 2) return null;

    final sampleCount = pcm16.length ~/ 2;
    final samples = Float32List(sampleCount);
    final byteData = ByteData.sublistView(pcm16);
    for (var i = 0; i < sampleCount; i++) {
      // PCM16 -> float in [-1, 1], as sherpa-onnx expects.
      samples[i] = byteData.getInt16(i * 2, Endian.little) / 32768.0;
    }

    try {
      stream.acceptWaveform(samples: samples, sampleRate: sampleRate);
      while (spotter.isReady(stream)) {
        spotter.decode(stream);
        final result = spotter.getResult(stream);
        final keyword = result.keyword;
        if (keyword.isNotEmpty) {
          // reset() right after a hit, as every sherpa-onnx example does;
          // without it the spotter keeps reporting the same keyword.
          spotter.reset(stream);
          return _onDetected(keyword);
        }
      }
    } catch (_) {
      // A transient decode error must not kill the mic stream; the next chunk
      // retries. The Python loop logs and continues the same way.
    }
    return null;
  }

  /// Cooldown + callback dispatch, mirroring _handle_detection in Python.
  String? _onDetected(String keyword) {
    final now = DateTime.now();
    final last = _lastDetectionAt;
    if (last != null && now.difference(last) < _cooldown) return null;
    _lastDetectionAt = now;
    // Briefly pause so the interrupt flow can complete before stale buffered
    // audio triggers a duplicate detection (Python pauses for 0.3s and drains).
    _paused = true;
    Future<void>.delayed(const Duration(milliseconds: 300)).then((_) {
      if (_running) _paused = false;
    });
    onDetected?.call(keyword);
    return keyword;
  }

  /// Validates a wake word without loading the ONNX models, for the settings UI.
  /// Returns null when the keyword line is valid, otherwise an error message.
  Future<String?> validateKeyword(String wakeWord) async {
    try {
      final tokensPath = await _copyAssetFile('$_modelAssetDir/tokens.txt');
      final converter = BpeKeywordConverter(tokensPath);
      await converter.convert(wakeWord);
      return null;
    } on BpeConversionError catch (error) {
      return error.message;
    } catch (error) {
      return 'Gagal memvalidasi kata wake word: ${error.toString()}';
    }
  }
}
