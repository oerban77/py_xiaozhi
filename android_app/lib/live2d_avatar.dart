import 'dart:async';
import 'dart:io' show Platform;
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:live2d/live2d.dart';

import 'emotion_display.dart';
import 'live2d_emotion.dart';

/// Live2D avatar that replaces the old GIF/emoji [EmotionDisplay].
///
/// Why this is not a literal copy of hacxy/l2d-widget: that project is a
/// *web* widget (DOM + WebGL, MIT, Cubism 2 & 6). Its "lip-sync" only moves
/// `PARAM_MOUTH_OPEN_Y` while it types its own tip text character by
/// character — it knows nothing about the assistant's voice. This widget
/// uses the same Cubism parameter (`ParamMouthOpenY`, the standard
/// Live2D mouth parameter) through the Flutter `live2d` package (WebGL
/// inside a WebView under the hood, same Cubism runtime family), but drives
/// it from the *real* TTS audio: [XiaozhiController.audioLevelStream]
/// already emits the TTS RMS level while speaking, so the mouth opens with
/// the voice's loudness instead of with a typing animation.
///
/// Model choice: the default model is Haru (a standard Live2D sample
/// character with a full mouth/eye/brow parameter set, served from the
/// jsDelivr mirror of the pixi-live2d-display test assets, the same model
/// the `live2d` package documents). The l2d-widget demo model
/// (cat-black, https://model.hacxy.cn/cat-black/model.json) is kept as the
/// fallback: during the VM check that host failed to resolve, so Haru is
/// primary and the cat is only tried if Haru fails to load. If both fail,
/// the widget falls back to the old [EmotionDisplay] instead of showing a
/// blank area. Either URL can be swapped by changing the constants below;
/// the model itself is not ours — see the note in the delivery message
/// about the Live2D sample-material terms.
class Live2DAvatar extends StatefulWidget {
  const Live2DAvatar({
    super.key,
    required this.emotion,
    required this.isSpeaking,
    this.isListening = false,
    required this.audioLevelStream,
    this.size = 240.0,
  });

  /// Normalized emotion name from the server (same values as
  /// [EmotionService.bundled]: happy, sad, angry, ...).
  final String emotion;

  /// True only while TTS is playing ([XiaozhiController.isSpeaking]).
  /// The mouth is driven only in this state; while listening the avatar
  /// keeps its mouth closed and only blinks / idles.
  final bool isSpeaking;

  /// True while the mic is open ([XiaozhiController.deviceState] ==
  /// listening). Drives the attentive gaze drift in [_driveMotion].
  final bool isListening;

  /// Live audio level stream (TTS RMS while speaking, mic RMS while
  /// listening). True RMS is typically 0.03-0.3, so it is amplified the
  /// same way [AudioLevelVisualizer] does (x3, clamped to [0, 1]).
  final Stream<double> audioLevelStream;

  final double size;

  /// Default model: Haru, a standard Live2D sample character.
  static const String primaryModelUrl =
      'https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display/test/assets/haru/haru_greeter_t03.model3.json';

  /// Fallback model: the l2d-widget demo character.
  static const String fallbackModelUrl =
      'https://model.hacxy.cn/cat-black/model.json';

  @override
  State<Live2DAvatar> createState() => _Live2DAvatarState();
}

/// True when running under `flutter test`.
///
/// The Live2D viewer is a WebView with a local asset server: there is no
/// platform view in a widget test, so building it there can only throw or
/// hang `pumpAndSettle`. In that environment the avatar falls back to the
/// old [EmotionDisplay], exactly as it does when both models fail to load.
/// On a real device this is always false, so the Live2D avatar is used.
bool get _isFlutterTest =>
    Platform.environment['FLUTTER_TEST'] == 'true';

class _Live2DAvatarState extends State<Live2DAvatar> {
  late final Live2DController _controller;
  StreamSubscription<double>? _levelSubscription;

  /// Slow timer driving the state-based motion in [_driveMotion].
  Timer? _motionTimer;

  /// Smoothed mouth value in [0, 1] actually sent to the model.
  double _mouth = 0.0;

  /// Last value sent to the model, for throttling.
  double _lastSentMouth = -1.0;
  DateTime _lastSentAt = DateTime.fromMillisecondsSinceEpoch(0);

  bool _triedFallback = false;
  bool _failed = false;

  /// Mouth updates are throttled to at most one WebView call per 50 ms;
  /// faster than that the JS bridge cost outweighs any visible difference.
  static const Duration _minSendInterval = Duration(milliseconds: 50);

  /// A change smaller than this is invisible, so it is not sent.
  static const double _minSendDelta = 0.03;

  @override
  void initState() {
    super.initState();
    _controller = Live2DController();
    _controller.onModelLoaded = (_) => _applyEmotion();
    _controller.onError = (_) => _onModelError();
    _levelSubscription = widget.audioLevelStream.listen(_onLevel);
    // Lifelike motion: a slow timer drives small head/gaze parameters
    // from the conversation state (see _driveMotion). Skipped in tests
    // and once the model has failed, like every other parameter send.
    if (!_isFlutterTest) {
      _motionTimer = Timer.periodic(
        const Duration(milliseconds: 120),
        (_) => _driveMotion(),
      );
    }
  }

  @override
  void didUpdateWidget(Live2DAvatar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.emotion != widget.emotion) {
      _applyEmotion();
    }
    if (oldWidget.isSpeaking != widget.isSpeaking && !widget.isSpeaking) {
      // Speech just ended: close the mouth promptly instead of waiting
      // for the release smoothing to decay.
      _mouth = 0.0;
      _sendMouth(force: true);
    }
    if ((oldWidget.isListening != widget.isListening ||
            oldWidget.isSpeaking != widget.isSpeaking) &&
        !widget.isSpeaking &&
        !widget.isListening) {
      // Back to idle: return head and gaze to centre instead of leaving
      // the last listening drift frozen on the face.
      _sendParam(DefaultParameterID.paramAngleX, 0.0);
      _sendParam(DefaultParameterID.paramBodyAngleX, 0.0);
      _sendParam(DefaultParameterID.paramEyeBallX, 0.0);
      _sendParam(DefaultParameterID.paramEyeBallY, 0.0);
    }
  }

  @override
  void dispose() {
    _motionTimer?.cancel();
    _levelSubscription?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onLevel(double rawLevel) {
    if (!mounted) return;
    // Same gain as AudioLevelVisualizer: true 16-bit PCM RMS is ~0.03-0.3
    // for normal speech, so x3 maps it to a visible 0.1-0.9 mouth range
    // without saturating wide open (x10 was calibrated for the old,
    // squared RMS value and would now pin the mouth open).
    final amplified = (rawLevel.clamp(0.0, 1.0) * 3.0).clamp(0.0, 1.0);
    final target = widget.isSpeaking ? amplified : 0.0;
    // Fast attack so the mouth opens on the first syllable, slow release
    // so it does not snap shut in the gaps between words.
    if (target > _mouth) {
      _mouth = _mouth * 0.3 + target * 0.7;
    } else {
      _mouth = _mouth * 0.85 + target * 0.15;
    }
    if (_mouth < 0.02) _mouth = 0.0;
    _sendMouth();
  }

  void _sendMouth({bool force = false}) {
    if (_failed || _isFlutterTest) return;
    final now = DateTime.now();
    if (!force &&
        now.difference(_lastSentAt) < _minSendInterval &&
        (_mouth - _lastSentMouth).abs() < _minSendDelta) {
      return;
    }
    // Mouth shape follows the emotion (see _applyEmotion); here only the
    // opening amount is driven, so a smile stays a smile while talking.
    // setParameter with an id the model does not have is ignored by the
    // Cubism core, and the package catches JS errors, so a cat model
    // without a given parameter simply does not move that part.
    unawaited(
      _controller.setParameter(DefaultParameterID.paramMouthOpenY, _mouth),
    );
    _lastSentMouth = _mouth;
    _lastSentAt = now;
  }

  void _sendParam(String id, double value) {
    if (_failed || _isFlutterTest) return;
    unawaited(_controller.setParameter(id, value));
  }

  /// Small state-driven motion so the character feels alive instead of a
  /// static picture with a moving mouth:
  /// - speaking: gentle head bob and a slight lean that follows the mouth,
  ///   gaze forward at the user;
  /// - listening: slow gaze drift side to side with a slight head tilt,
  ///   as if paying attention;
  /// - idle: nothing is sent here; the model's own idle motion (breathing,
  ///   blinking) runs, and the transition back to centre is handled in
  ///   didUpdateWidget.
  void _driveMotion() {
    if (!mounted || _failed || _isFlutterTest) return;
    final phase = DateTime.now().millisecondsSinceEpoch / 1000.0;
    if (widget.isSpeaking) {
      _sendParam(
        DefaultParameterID.paramAngleX,
        2.0 * math.sin(phase * 4.0) * (0.3 + _mouth),
      );
      _sendParam(DefaultParameterID.paramBodyAngleX, _mouth * 4.0);
      _sendParam(DefaultParameterID.paramEyeBallX, 0.1 * math.sin(phase * 1.3));
      _sendParam(DefaultParameterID.paramEyeBallY, 0.0);
    } else if (widget.isListening) {
      _sendParam(DefaultParameterID.paramEyeBallX, 0.35 * math.sin(phase * 0.9));
      _sendParam(DefaultParameterID.paramEyeBallY, 0.15 * math.cos(phase * 0.7));
      _sendParam(DefaultParameterID.paramAngleX, 1.5 * math.sin(phase * 0.5));
    }
  }

  /// Maps a server emotion to standard Cubism face parameters.
  /// Only MouthForm, eye opening and cheek (blush) are used: they exist on
  /// practically every Cubism model, whereas expression ids are
  /// model-specific (Haru's are f01/f02/..., a cat model's are different),
  /// so mapping by expression id would only work for one model.
  void _applyEmotion() {
    if (_failed || _isFlutterTest) return;
    final face = Live2DEmotionFace.forEmotion(widget.emotion);
    unawaited(
      _controller.setParameter(DefaultParameterID.paramMouthForm, face.mouthForm),
    );
    unawaited(
      _controller.setParameter(DefaultParameterID.paramEyeLOpen, face.eyeOpen),
    );
    unawaited(
      _controller.setParameter(DefaultParameterID.paramEyeROpen, face.eyeOpen),
    );
    if (face.cheek > 0) {
      unawaited(
        _controller.setParameter(DefaultParameterID.paramCheek, face.cheek),
      );
    }
  }

  void _onModelError() {
    if (!mounted) return;
    if (!_triedFallback) {
      _triedFallback = true;
      unawaited(_controller.loadModel(Live2DAvatar.fallbackModelUrl));
      return;
    }
    setState(() => _failed = true);
  }

  @override
  Widget build(BuildContext context) {
    if (_isFlutterTest) {
      // Widget tests (widget_test.dart) assert on EmotionDisplay and on
      // swipe navigation, neither of which a WebView can provide in a test.
      return EmotionDisplay(emotion: widget.emotion, size: widget.size);
    }
    if (_failed) {
      // Both models failed (no network, host down, WebGL unavailable):
      // keep the old GIF/emoji avatar instead of a blank stage.
      return EmotionDisplay(emotion: widget.emotion, size: widget.size);
    }
    // Transparent stage: no disc or panel behind the model, so it
    // floats directly over the screen background and the mouth stays
    // visible against the dark stage. Live2DViewer already renders with
    // a transparent background.
    return SizedBox(
      width: widget.size,
      height: widget.size,
      child: Live2DViewer(
            controller: _controller,
            initialModelUrl: Live2DAvatar.primaryModelUrl,
            fit: Live2DFit.contain,
            scale: 1.0,
            autoInteract: true,
            backgroundColor: Colors.transparent,
            loadingBuilder: (context) => const Center(
              child: CircularProgressIndicator(),
            ),
            errorBuilder: (context, error) => EmotionDisplay(
              emotion: widget.emotion,
              size: widget.size,
            ),
          ),
    );
  }
}
