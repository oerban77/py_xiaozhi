import 'dart:async';
import 'dart:io' show Platform;
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:live2d/live2d.dart';

import 'emotion_display.dart';
import 'lipsync_mfcc.dart';
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
    this.lipSyncStream,
    this.gestureStream,
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

  /// Lip-sync vowel frames from [XiaozhiController.lipSyncStream]
  /// (MFCC + vowel templates, see lipsync_mfcc.dart). When provided,
  /// the mouth opening follows the vowel being spoken and the mouth
  /// form shifts with it, instead of the RMS-only open/close. When
  /// null (widget tests, older callers) the RMS mouth is used.
  final Stream<LipSyncFrame>? lipSyncStream;

  /// Gesture requests from [XiaozhiController.avatarGestureStream]
  /// (MCP `avatar_gesture` tool). Each event is a canonical gesture
  /// name; the widget plays Haru's built-in motion for it, or animates
  /// the head/expression parameters directly.
  final Stream<String>? gestureStream;

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
  StreamSubscription<LipSyncFrame>? _lipSyncSubscription;
  StreamSubscription<String>? _gestureSubscription;

  /// 30 Hz ticker that animates a gesture's head/expression parameters
  /// (see [_playGesture]). Null when no gesture is running.
  Timer? _gestureTimer;

  /// Latest lip-sync frame from [LipSyncFrame]; null until the first
  /// analyzed window arrives. While speaking the mouth opening and
  /// form come from this frame, not from the RMS level.
  LipSyncFrame? _lipSyncFrame;

  /// Smoothed mouth form in [-1, 1] actually sent to the model:
  /// the emotion's mouth form plus the current vowel's shift.
  double _mouthForm = 0.0;

  /// Last mouth form sent to the model; the ticker only sends when
  /// the value moved enough to be visible.
  double _lastSentMouthForm = -2.0;

  /// The emotion face's mouth form (see _applyEmotion): the baseline
  /// the vowel shift is added to, so a smile stays a smile while
  /// talking and the face returns to it when speech ends.
  double _emotionMouthForm = 0.1;

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
    final lipSyncStream = widget.lipSyncStream;
    if (lipSyncStream != null) {
      _lipSyncSubscription = lipSyncStream.listen((frame) {
        if (mounted) _lipSyncFrame = frame;
      });
    }
    final gestureStream = widget.gestureStream;
    if (gestureStream != null) {
      _gestureSubscription = gestureStream.listen(_playGesture);
    }
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
      // for the ticker's release smoothing to decay, and return the
      // mouth form to the emotion face.
      _mouthTarget = 0.0;
      _mouth = 0.0;
      _lipSyncFrame = null;
      _mouthForm = _emotionMouthForm;
      _sendMouth(forceOpen: true, forceForm: true);
    }
  }

  @override
  void dispose() {
    _mouthTimer?.cancel();
    _gestureTimer?.cancel();
    _levelSubscription?.cancel();
    _lipSyncSubscription?.cancel();
    _gestureSubscription?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onLevel(double rawLevel) {
    if (!mounted) return;
    // Lip-sync owns the mouth when it is connected: the vowel frames
    // carry their own opening, so the RMS level must not overwrite it.
    if (_lipSyncSubscription != null) return;
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

  /// Advances the mouth toward its target on the ticker clock and sends
  /// it when it moved visibly. Time-based smoothing (attack ~45 ms,
  /// release ~180 ms) replaces the old per-event smoothing: with a steady
  /// tick the mouth opens on the first syllable, holds through short gaps,
  /// and closes smoothly, instead of jumping with every audio burst.
  /// The opening target comes from the latest lip-sync frame when one is
  /// connected (vowel-driven opening), otherwise from the RMS level.
  /// The form target is the emotion's mouth form plus the current vowel's
  /// shift, so the shape follows the vowel while the emotion face stays
  /// dominant; it returns to the emotion form when speech ends.
  void _tickMouth() {
    if (!mounted || _failed || _isFlutterTest) return;
    final now = DateTime.now();
    final dt = now.difference(_lastMouthTick).inMilliseconds / 1000.0;
    _lastMouthTick = now;
    if (dt <= 0) return;
    final openTarget = widget.isSpeaking
        ? (_lipSyncFrame?.openness ?? _mouthTarget)
        : 0.0;
    final formTarget = (widget.isSpeaking
            ? _emotionMouthForm + (_lipSyncFrame?.formDelta ?? 0.0)
            : _emotionMouthForm)
        .clamp(-1.0, 1.0);
    final tauOpen = openTarget > _mouth ? 0.045 : 0.18;
    final kOpen = 1.0 - math.exp(-dt / tauOpen);
    _mouth += (openTarget - _mouth) * kOpen;
    if (_mouth < 0.02) _mouth = 0.0;
    // The form changes a little slower than the opening, so a vowel
    // change reads as a shape shift rather than a flicker.
    final kForm = 1.0 - math.exp(-dt / 0.09);
    _mouthForm += (formTarget - _mouthForm) * kForm;
    _sendMouth();
  }

  /// Sends the mouth parameters that actually moved. Each parameter is
  /// sent only when it moved visibly (>= 0.01) -- the previous version
  /// sent both whenever either moved, so a jittering form re-sent the
  /// opening on every tick too, doubling the WebView bridge traffic
  /// and contributing to the slowness. The speech-end snap in
  /// didUpdateWidget forces both.
  void _sendMouth({bool forceOpen = false, bool forceForm = false}) {
    if (_failed || _isFlutterTest) return;
    // Opening follows the vowel (or the RMS level when no lip-sync
    // stream is connected); the form is the emotion face plus the
    // vowel's shift. setParameter with an id the model does not have
    // is ignored by the Cubism core, and the package catches JS errors,
    // so a cat model without a given parameter simply does not move
    // that part.
    if (forceOpen || (_mouth - _lastSentMouth).abs() >= 0.01) {
      unawaited(
        _controller.setParameter(
          DefaultParameterID.paramMouthOpenY,
          _mouth,
        ),
      );
      _lastSentMouth = _mouth;
    }
    if (forceForm || (_mouthForm - _lastSentMouthForm).abs() >= 0.01) {
      unawaited(
        _controller.setParameter(
          DefaultParameterID.paramMouthForm,
          _mouthForm,
        ),
      );
      _lastSentMouthForm = _mouthForm;
    }
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
    // The emotion form is the baseline the lip-sync vowel shift is
    // added to (see _tickMouth); it is sent immediately here and the
    // ticker keeps sending the combined value while speaking.
    _emotionMouthForm = face.mouthForm;
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

  /// Play a gesture (MCP `avatar_gesture`). Haru's built-in motions
  /// cover the wave (`Tap` group = greeting wave) and the idle dance;
  /// the rest are parameter animations driven by a 30 Hz ticker with
  /// the same envelope as the 3D avatar (rise, hold, fall). The
  /// ticker restores the emotion face when the gesture ends, so a
  /// gesture never leaves the face stuck. Parameter ranges follow the
  /// Cubism defaults: angles in degrees (-30..30), smiles/brows in
  /// 0..1, mouth form in -1..1.
  void _playGesture(String gesture) {
    if (_failed || _isFlutterTest) return;
    _gestureTimer?.cancel();
    // Built-in motions first: Haru's Tap is a greeting wave.
    if (gesture == 'wave') {
      unawaited(_controller.startMotion('Tap', index: 0, priority: 3));
      return;
    }
    if (gesture == 'dance') {
      unawaited(_controller.startMotion('Idle', index: 1, priority: 3));
    }
    final start = DateTime.now();
    const duration = Duration(milliseconds: 2000);
    _gestureTimer = Timer.periodic(
      const Duration(milliseconds: 33),
      (timer) {
        final elapsed = DateTime.now().difference(start);
        final p = elapsed.inMilliseconds / duration.inMilliseconds;
        if (p >= 1) {
          timer.cancel();
          _gestureTimer = null;
          _applyEmotion();
          return;
        }
        // Envelope: smooth rise (0-20%), hold, smooth fall (80-100%).
        double envelope(double t) {
          double smooth(double x) => x * x * (3 - 2 * x);
          if (t < 0.2) return smooth(t / 0.2);
          if (t > 0.8) return smooth((1 - t) / 0.2);
          return 1;
        }
        double osc(double t, int cycles) =>
            math.sin(t * cycles * math.pi * 2) * envelope(t);
        final e = envelope(p);
        // Head angles and expression params for this gesture.
        double angleX = 0, angleY = 0, angleZ = 0;
        double eyeSmile = 0, browUp = 0, browDown = 0, mouthForm = 0;
        switch (gesture) {
          case 'nod':
            angleY = 15 * osc(p, 2);
            break;
          case 'shake':
            angleX = 20 * osc(p, 2);
            break;
          case 'bow':
            angleY = 18 * e;
            break;
          case 'happy':
            eyeSmile = 0.9 * e;
            mouthForm = 0.8 * e;
            angleZ = 5 * e;
            break;
          case 'sad':
            browUp = 0.7 * e;
            mouthForm = -0.7 * e;
            angleY = 6 * e;
            break;
          case 'surprised':
            browUp = 0.8 * e;
            mouthForm = 0.4 * e;
            angleY = -5 * e;
            break;
          case 'angry':
            browDown = 0.8 * e;
            mouthForm = -0.5 * e;
            break;
          case 'think':
            browUp = 0.5 * e;
            angleZ = 10 * e;
            break;
          case 'look_left':
            angleX = 25 * e;
            break;
          case 'look_right':
            angleX = -25 * e;
            break;
          case 'look_up':
            angleY = -15 * e;
            break;
          case 'look_down':
            angleY = 15 * e;
            break;
          case 'dance':
            angleZ = 8 * osc(p, 2);
            angleX = 10 * osc(p, 2);
            break;
        }
        unawaited(
          _controller.setParameter(DefaultParameterID.paramAngleX, angleX),
        );
        unawaited(
          _controller.setParameter(DefaultParameterID.paramAngleY, angleY),
        );
        unawaited(
          _controller.setParameter(DefaultParameterID.paramAngleZ, angleZ),
        );
        if (eyeSmile > 0) {
          unawaited(
            _controller.setParameter(
                DefaultParameterID.paramEyeLSmile, eyeSmile),
          );
          unawaited(
            _controller.setParameter(
                DefaultParameterID.paramEyeRSmile, eyeSmile),
          );
        }
        if (browUp > 0) {
          unawaited(
            _controller.setParameter(DefaultParameterID.paramBrowLY, browUp),
          );
          unawaited(
            _controller.setParameter(DefaultParameterID.paramBrowRY, browUp),
          );
        }
        if (browDown > 0) {
          unawaited(
            _controller.setParameter(
                DefaultParameterID.paramBrowLAngle, -browDown),
          );
          unawaited(
            _controller.setParameter(
                DefaultParameterID.paramBrowRAngle, -browDown),
          );
        }
        if (mouthForm != 0) {
          unawaited(
            _controller.setParameter(
                DefaultParameterID.paramMouthForm, mouthForm),
          );
        }
      },
    );
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
