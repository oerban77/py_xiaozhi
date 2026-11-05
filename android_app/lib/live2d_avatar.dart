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
    this.height,
    this.modelUrl = primaryModelUrl,
  });

  /// Normalized emotion name from the server (same values as
  /// [EmotionService.bundled]: happy, sad, angry, ...).
  final String emotion;

  /// True only while TTS is playing ([XiaozhiController.isSpeaking]).
  /// The mouth is driven only in this state; while listening the avatar
  /// keeps its mouth closed and only blinks / idles.
  final bool isSpeaking;

  /// True while the mic is open ([XiaozhiController.deviceState] ==
  /// listening). The avatar keeps its mouth closed in this state; gaze
  /// and head motion come from the model's own idle motion.
  final bool isListening;

  /// Live audio level stream (TTS RMS while speaking, mic RMS while
  /// listening). True RMS is typically 0.03-0.3, so it is amplified the
  /// same way [AudioLevelVisualizer] does (x3, clamped to [0, 1]).
  final Stream<double> audioLevelStream;

  final double size;

  /// Stage height. When null the stage is square (size x size). The home
  /// screen passes a taller height so the full-body model renders bigger
  /// on narrow phones: the model scales to the taller box instead of
  /// being capped by the screen width.
  final double? height;

  /// Default model: Haru, a standard Live2D sample character. Also the
  /// fallback: when a chosen character fails to load, the avatar retries
  /// with Haru once instead of the old demo-cat host, which proved
  /// unreachable, before giving up to the emoji avatar.
  static const String primaryModelUrl =
      'https://cdn.jsdelivr.net/gh/guansss/pixi-live2d-display/test/assets/haru/haru_greeter_t03.model3.json';

  /// `.model3.json` URL of the character to display. The home screen
  /// passes the character saved in Settings (Karakter); changing it
  /// reloads the viewer, see didUpdateWidget.
  final String modelUrl;

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

  /// Regular ticker driving the mouth toward [_mouthTarget] (see _tickMouth):
  /// the original live2d updates the mouth in the render loop, not on
  /// irregular audio events, so the mouth moves on a steady clock.
  Timer? _mouthTimer;
  DateTime _lastMouthTick = DateTime.fromMillisecondsSinceEpoch(0);

  /// Latest mouth target in [0, 1] from the audio level stream; the ticker
  /// smooths [_mouth] toward this, so bursty level events never reach the
  /// model directly.
  double _mouthTarget = 0.0;

  /// Smoothed mouth value in [0, 1] actually sent to the model.
  double _mouth = 0.0;

  /// Last mouth value sent to the model; the ticker only sends when the
  /// value moved enough to be visible.
  double _lastSentMouth = -1.0;

  bool _triedFallback = false;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _controller = Live2DController();
    _controller.onModelLoaded = (_) => _applyEmotion();
    _controller.onError = (_) => _onModelError();
    _levelSubscription = widget.audioLevelStream.listen(_onLevel);
    // Mouth clock: a steady 30 Hz tick advances the mouth toward the
    // latest audio level (see _tickMouth). Skipped in tests, like every
    // other parameter send.
    if (!_isFlutterTest) {
      _lastMouthTick = DateTime.now();
      _mouthTimer = Timer.periodic(
        const Duration(milliseconds: 33),
        (_) => _tickMouth(),
      );
    }
  }

  @override
  void didUpdateWidget(Live2DAvatar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.modelUrl != widget.modelUrl) {
      // Character changed in Settings: reload the viewer with the new
      // model and a fresh fallback attempt. A parent rebuild is already
      // scheduled (the controller notified), so assigning _failed here
      // without setState is safe: build() runs right after this with the
      // new modelUrl.
      _failed = false;
      _triedFallback = false;
      unawaited(_controller.loadModel(widget.modelUrl));
    }
    if (oldWidget.emotion != widget.emotion ||
        oldWidget.isSpeaking != widget.isSpeaking ||
        oldWidget.isListening != widget.isListening) {
      // Emotion changes apply a new face, and so do state changes: when
      // no server emotion is active the face comes from the device
      // state, so speaking/listening/idle transitions must re-apply it.
      _applyEmotion();
    }
    if (oldWidget.isSpeaking != widget.isSpeaking && !widget.isSpeaking) {
      // Speech just ended: close the mouth promptly instead of waiting
      // for the ticker's release smoothing to decay.
      _mouthTarget = 0.0;
      _mouth = 0.0;
      _sendMouth();
    }
  }

  @override
  void dispose() {
    _mouthTimer?.cancel();
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
    // Only the target is stored here: the mouth ticker (_tickMouth)
    // advances the model value toward it on a steady clock, the way the
    // original live2d drives the mouth from its render loop. Smoothing
    // per audio event made the mouth jump whenever network jitter
    // bunched the level events.
    final amplified = (rawLevel.clamp(0.0, 1.0) * 3.0).clamp(0.0, 1.0);
    _mouthTarget = widget.isSpeaking ? amplified : 0.0;
  }

  /// Advances the mouth toward [_mouthTarget] on the ticker clock and sends
  /// it when it moved visibly. Time-based smoothing (attack ~45 ms, release
  /// ~180 ms) replaces the old per-event smoothing: with a steady tick the
  /// mouth opens on the first syllable, holds through short gaps, and
  /// closes smoothly, instead of jumping with every audio burst.
  void _tickMouth() {
    if (!mounted || _failed || _isFlutterTest) return;
    final now = DateTime.now();
    final dt = now.difference(_lastMouthTick).inMilliseconds / 1000.0;
    _lastMouthTick = now;
    if (dt <= 0) return;
    final tau = _mouthTarget > _mouth ? 0.045 : 0.18;
    final k = 1.0 - math.exp(-dt / tau);
    _mouth += (_mouthTarget - _mouth) * k;
    if (_mouth < 0.02) _mouth = 0.0;
    if ((_mouth - _lastSentMouth).abs() < 0.01) return;
    _sendMouth();
  }

  void _sendMouth() {
    if (_failed || _isFlutterTest) return;
    // Mouth shape follows the emotion (see _applyEmotion); here only the
    // opening amount is driven, so a smile stays a smile while talking.
    // setParameter with an id the model does not have is ignored by the
    // Cubism core, and the package catches JS errors, so a cat model
    // without a given parameter simply does not move that part.
    unawaited(
      _controller.setParameter(DefaultParameterID.paramMouthOpenY, _mouth),
    );
    _lastSentMouth = _mouth;
  }

  // No Dart-driven head/body/gaze motion here, on purpose: the model's own
  // idle motion already animates those parameters, and sending them from
  // Dart every 120 ms fought the motion curves -- the rendered value
  // flickered between the two and the whole character vibrated. The
  // original live2d example likewise lets the model own its motion and
  // only drives the mouth (plus discrete expression changes) from code.

  /// Maps the current expression to standard Cubism face parameters.
  /// Only MouthForm, eye opening and cheek (blush) are used: they exist on
  /// practically every Cubism model, whereas expression ids are
  /// model-specific (Haru's are f01/f02/..., a cat model's are different),
  /// so mapping by expression id would only work for one model.
  /// The server emoticon is authoritative: the `llm` message's `emotion`
  /// field (same names as EmotionService.bundled, normalised by the
  /// controller) always wins, including an explicit 'neutral'. The
  /// device-state face is only a fallback for when the server has sent
  /// nothing yet (empty emotion, e.g. before the first response), so the
  /// character still smiles gently while talking and looks attentive
  /// while listening instead of sitting on a flat face.
  void _applyEmotion() {
    if (_failed || _isFlutterTest) return;
    final emotion = widget.emotion.trim();
    final face = emotion.isEmpty
        ? Live2DEmotionFace.forDeviceState(
            isSpeaking: widget.isSpeaking,
            isListening: widget.isListening,
          )
        : Live2DEmotionFace.forEmotion(widget.emotion);
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
    if (!_triedFallback && widget.modelUrl != Live2DAvatar.primaryModelUrl) {
      // A chosen character failed: retry once with Haru before giving up.
      // When Haru itself fails there is nothing left to try.
      _triedFallback = true;
      unawaited(_controller.loadModel(Live2DAvatar.primaryModelUrl));
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
      height: widget.height ?? widget.size,
      child: Live2DViewer(
            controller: _controller,
            initialModelUrl: widget.modelUrl,
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
