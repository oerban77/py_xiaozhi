import 'dart:async';
import 'dart:convert';
import 'dart:io' show Platform;
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

import 'avatar_3d_server.dart';
import 'emotion_display.dart';
import 'lipsync_mfcc.dart';

/// 3D avatar (PoC): a .glb character rendered with three.js inside a
/// WebView, its face driven by the same [LipSyncFrame] vowel stream as
/// the Live2D avatar. Same architecture as [Live2DAvatar] (WebView + JS
/// bridge + a steady 30 Hz mouth ticker), different renderer.
///
/// Why a custom three.js page instead of flutter_3d_controller /
/// model-viewer: Google's model-viewer public API exposes only materials
/// and variants -- no morph targets -- and its runtime `Model` keeps the
/// three.js scene behind Symbol-keyed privates (model.ts), so there is no
/// stable way to reach the meshes from Dart. This page
/// (assets/threejs/avatar3d.html) loads the model with three.js directly,
/// so morph targets are first-class: `mesh.morphTargetDictionary` by
/// name, plus skeletal clips (including separate Mixamo clip files
/// applied to the avatar skeleton by bone name).
///
/// Everything the page needs is bundled as Flutter assets (three.js
/// r160 + addons + the default model), so the avatar opens instantly
/// with no download and works offline. The APK grows by ~6.8 MB for
/// it (5.9 MB model + ~0.8 MB three.js); a different model needs an
/// app update instead of just a URL change. The files are served to the
/// WebView by a tiny loopback HTTP server ([Avatar3dServer]): the page
/// cannot be loaded with `loadFlutterAsset`, because that serves it as
/// a `file://` URL and Chrome/WebView refuses to load ES modules (and
/// `fetch()` a .glb) from `file://` -- module fetches are
/// CORS-checked and a `file://` page has an opaque (`null`) origin, so
/// the import fails and the page never reports ready.
///
/// Default model: the Ready Player Me half-body sample from the MIT
/// `readyplayerme/visage` repo (5.9 MB, 72 morph targets incl. 15 Oculus
/// visemes + 52 ARKit shapes, embedded `idle_eyes` clip). It is bundled
/// with the app (see [Avatar3D.defaultModelUrl]). The full-body
/// `female.glb` (10.4 MB, same morph set, no clips) is the obvious next
/// model; a custom .glb URL works the same way as long as it carries
/// morph targets with these names.
///
/// What the PoC does NOT do yet (Phase 2): map the server emotion to
/// ARKit morphs (mouthSmile/mouthFrown/browInnerUp) -- the face stays
/// neutral except for the talking mouth -- and dance (needs a Mixamo
/// clip merged in or loaded via `loadAnimationClip`).
class Avatar3D extends StatefulWidget {
  const Avatar3D({
    super.key,
    required this.emotion,
    required this.isSpeaking,
    this.lipSyncStream,
    this.size = 240.0,
    this.height,
    this.modelUrl = defaultModelUrl,
    this.onLoaded,
    this.onError,
  });

  /// Normalized emotion name from the server (same values as
  /// [EmotionService.bundled]). Only used for the [EmotionDisplay]
  /// fallback when the model fails to load; the 3D face itself does
  /// not map emotions to morphs yet (Phase 2).
  final String emotion;

  /// True only while TTS is playing ([XiaozhiController.isSpeaking]).
  /// The mouth is driven only in this state; otherwise it stays closed.
  final bool isSpeaking;

  /// Lip-sync vowel frames from [XiaozhiController.lipSyncStream]
  /// (MFCC + vowel templates, see lipsync_mfcc.dart). When null the
  /// mouth stays closed (the PoC has no RMS-only fallback: the point
  /// is the vowel-driven mouth).
  final Stream<LipSyncFrame>? lipSyncStream;

  final double size;

  /// Stage height. When null the stage is square (size x size).
  final double? height;

  /// Default model: Ready Player Me half-body sample (visage repo, MIT).
  /// 72 morph targets, `idle_eyes` clip, 5.9 MB. It is BUNDLED with
  /// the app (assets/threejs/models/halfBody.glb), so the 3D avatar
  /// opens instantly with no download; the page resolves this relative
  /// path against its own asset URL. A custom http(s) .glb URL still
  /// loads on demand.
  static const String defaultModelUrl = 'models/halfBody.glb';

  /// Second bundled model: the Ready Player Me full-body woman from
  /// the same visage repo (10.4 MB, same 72 morph targets, no clips).
  /// She is framed head-to-waist by the viewer (see avatar3d.html),
  /// so her face is as large on screen as the half-body man's.
  static const String femaleModelUrl = 'models/female.glb';

  /// The bundled 3D characters the user can pick in Settings
  /// (Karakter 3D). The 2D Live2D characters (Haru, Hiyori, ...) are
  /// Cubism models and have no .glb version, so they cannot be used
  /// here; these are the closest 3D equivalents.
  static const List<({String label, String url})> models = [
    (label: 'Pria (helm)', url: defaultModelUrl),
    (label: 'Wanita', url: femaleModelUrl),
  ];

  /// `.glb` URL of the character to display. Changing it reloads the
  /// viewer (see didUpdateWidget).
  final String modelUrl;

  /// Called with the model's embedded animation names and morph target
  /// names once it loads (useful for the debug screen / dance list).
  final void Function(List<String> animations, List<String> morphs)?
      onLoaded;

  /// Called with the load error message when the model (or the page)
  /// fails; the widget then falls back to [EmotionDisplay].
  final void Function(String message)? onError;

  @override
  State<Avatar3D> createState() => _Avatar3DState();
}

/// Lip-sync frame -> three.js morph weights, by target name.
///
/// The five vowels map 1:1 onto the Oculus viseme set the Ready Player
/// Me models carry (viseme_aa/E/I/O/U), each scaled by the frame's
/// openness; `jawOpen` (and a smaller `mouthOpen`) carry the opening
/// itself; the form shift becomes a smile for ee/ih and a
/// funnel+pucker for oh/ou. All values are clamped to [0, 1]. A silent
/// frame yields all zeros, so the mouth closes when speech ends.
Map<String, double> lipSyncFrameToMorphWeights(LipSyncFrame frame) {
  return morphWeightsFor(
    openness: frame.openness,
    formDelta: frame.formDelta,
    vowelWeights: frame.weights,
  );
}

/// Same mapping, from already-smoothed openness/form values and the
/// latest frame's vowel weights (what the mouth ticker sends).
///
/// The jaw and the visemes follow the VOWEL, not just the loudness:
/// 'aa' drops the jaw wide, 'ih'/'ee' stay narrow with a smile,
/// 'oh'/'ou' sit in between with rounded lips. Without this every
/// vowel looked like the same generic open mouth (the jaw opened the
/// same amount for all of them and the visemes were diluted by the
/// openness factor, so the mouth never visibly matched the TTS).
///
/// But the opening is deliberately CAPPED: a real mouth never opens
/// to its full range while talking, and at full jaw + full viseme
/// the face gapes wide open (a big dark hole) and looks frightening
/// rather than like speech. The caps keep the shapes distinct
/// (aa still opens widest, ih narrowest) while the mouth stays in
/// the moderate range a native speaker uses. The smile/funnel are
/// scaled down further (0.4): the ARKit lip morphs are very strong
/// on this model, and at full strength they turn 'ee' into a manic
/// grin and 'uu' into a pucker that pushes the tongue out.
Map<String, double> morphWeightsFor({
  required double openness,
  required double formDelta,
  required Map<String, double> vowelWeights,
}) {
  double clamp01(double v) => v.clamp(0.0, 1.0);
  // Per-vowel jaw drop (fraction of the overall openness): 'aa' widest,
  // 'ih' narrowest. Weighted by the current vowel mix, so the jaw
  // follows the vowel instead of opening the same for all. Capped so
  // the jaw never drops to its full range while talking.
  const jawFor = {'aa': 1.0, 'ee': 0.55, 'ih': 0.45, 'oh': 0.8, 'ou': 0.7};
  var jawMix = 0.0;
  var weightSum = 0.0;
  for (final entry in jawFor.entries) {
    final w = vowelWeights[entry.key] ?? 0.0;
    jawMix += w * entry.value;
    weightSum += w;
  }
  final jawFactor = weightSum > 0 ? jawMix / weightSum : 1.0;
  final jaw = clamp01(openness * jawFactor).clamp(0.0, 0.6);
  // Sharpen the vowel weights so the dominant vowel's viseme stands
  // out instead of blending into a generic open mouth: the MFCC
  // matcher returns a soft blend (the dominant vowel is often only
  // ~0.6 of the total), so a power curve restores the contrast. No
  // extra boost on top: the sharpened share times the openness is
  // enough, and boosting it to 1.0 made the mouth gape. Capped so
  // no single viseme ever maxes out.
  final sharpened = <String, double>{};
  var sharpSum = 0.0;
  for (final entry in jawFor.entries) {
    final s = math.pow(vowelWeights[entry.key] ?? 0.0, 1.5).toDouble();
    sharpened[entry.key] = s;
    sharpSum += s;
  }
  double viseme(String vowel) => sharpSum > 0
      ? clamp01((sharpened[vowel]! / sharpSum) * openness).clamp(0.0, 0.6)
      : 0.0;
  // formDelta lives in [-0.3, 0.3]: positive = spread/smile (ee, ih),
  // negative = rounded (oh, ou). Scaled to [0, 1] and by openness, so
  // the shape shift only shows while the mouth is actually open, then
  // scaled down 0.4x: the lip morphs are very strong on this model.
  final smile = clamp01(formDelta / 0.3) * openness * 0.4;
  final funnel = clamp01(-formDelta / 0.3) * openness * 0.4;
  return {
    'viseme_aa': viseme('aa'),
    'viseme_E': viseme('ee'),
    'viseme_I': viseme('ih'),
    'viseme_O': viseme('oh'),
    'viseme_U': viseme('ou'),
    'jawOpen': jaw,
    'mouthOpen': clamp01(jaw * 0.6),
    'mouthSmile': smile,
    'mouthSmileLeft': smile,
    'mouthSmileRight': smile,
    'mouthFunnel': funnel,
    'mouthPucker': funnel * 0.6,
  };
}

/// True when running under `flutter test`: no platform WebView exists
/// there, so the widget falls back to [EmotionDisplay] (same guard as
/// the Live2D avatar).
bool get _isFlutterTest => Platform.environment['FLUTTER_TEST'] == 'true';

class _Avatar3DState extends State<Avatar3D> {
  late final WebViewController _webViewController;
  StreamSubscription<LipSyncFrame>? _lipSyncSubscription;

  /// Latest lip-sync frame; null until the first analyzed window.
  LipSyncFrame? _lipSyncFrame;

  /// Smoothed mouth values actually sent (same clock as the Live2D
  /// avatar: 30 Hz ticker, attack ~70 ms, release ~180 ms for the
  /// opening, ~90 ms for the form).
  double _mouth = 0.0;
  double _mouthForm = 0.0;

  /// Smoothed vowel weights (aa/ee/ih/oh/ou): the MFCC matcher can
  /// jump between vowels frame to frame, and sending those raw jumps
  /// makes the mouth shape snap instead of gliding like speech. An
  /// exponential average (tau ~80 ms) keeps the vowel transitions
  /// smooth while still following the TTS.
  Map<String, double> _smoothedVowelWeights = {};
  Timer? _mouthTimer;
  DateTime _lastMouthTick = DateTime.fromMillisecondsSinceEpoch(0);

  /// Last morph weights sent to the page; the ticker only sends when a
  /// value moved visibly (>= 0.01), so a still mouth costs no bridge
  /// traffic.
  Map<String, double> _lastSentMorphs = {};

  /// True once the page reported the model loaded (morphs may be sent).
  bool _pageReady = false;
  bool _modelReady = false;
  bool _failed = false;

  /// Fires if the page never reports ready (three.js failed to load):
  /// without it a dead page would leave the spinner up forever instead
  /// of falling back to the emoji avatar with an error.
  Timer? _pageReadyTimer;

  /// Loopback server that serves the bundled page/three.js/model to
  /// the WebView over http://127.0.0.1 (see [Avatar3dServer]).
  final Avatar3dServer _server = Avatar3dServer();

  @override
  void initState() {
    super.initState();
    if (_isFlutterTest) return;
    _webViewController = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(Colors.transparent)
      ..addJavaScriptChannel(
        'Avatar3d',
        onMessageReceived: _onJsMessage,
      );
    // The page is NOT loaded with loadFlutterAsset: that serves it as
    // a file:// URL, and Chrome/WebView refuses ES module imports from
    // file:// (CORS: a file:// page has an opaque origin). The loopback
    // server serves the same bundled files over http://127.0.0.1, where
    // modules and fetch() work normally.
    unawaited(_startAndLoad());
    _pageReadyTimer = Timer(
      const Duration(seconds: 20),
      _onPageReadyTimeout,
    );
    final lipSyncStream = widget.lipSyncStream;
    if (lipSyncStream != null) {
      _lipSyncSubscription = lipSyncStream.listen((frame) {
        if (mounted) _lipSyncFrame = frame;
      });
    }
    _lastMouthTick = DateTime.now();
    _mouthTimer = Timer.periodic(
      const Duration(milliseconds: 33),
      (_) => _tickMouth(),
    );
  }

  @override
  void didUpdateWidget(Avatar3D oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.modelUrl != widget.modelUrl) {
      _modelReady = false;
      _failed = false;
      _loadModel();
    }
    if (oldWidget.isSpeaking != widget.isSpeaking && !widget.isSpeaking) {
      // Speech just ended: close the mouth promptly instead of waiting
      // for the ticker's release smoothing to decay.
      _lipSyncFrame = null;
      _mouth = 0.0;
      _mouthForm = 0.0;
      _smoothedVowelWeights = {};
      _sendMorphs(force: true);
    }
  }

  @override
  void dispose() {
    _mouthTimer?.cancel();
    _pageReadyTimer?.cancel();
    _lipSyncSubscription?.cancel();
    unawaited(_server.stop());
    super.dispose();
  }

  /// Starts the loopback server and loads the viewer page from it.
  /// No onPageFinished model load: the page's `<script type="module">`
  /// executes after the page finishes, so `window.__avatar3d` is
  /// usually undefined at that point. The page posts {type:'ready'}
  /// once its API exists (see _onJsMessage) and the model loads only
  /// then.
  Future<void> _startAndLoad() async {
    try {
      await _server.start();
      final baseUrl = _server.baseUrl;
      if (baseUrl == null || !mounted) return;
      await _webViewController.loadRequest(
        Uri.parse('${baseUrl}avatar3d.html'),
      );
    } catch (e) {
      if (!mounted) return;
      widget.onError?.call('Server avatar 3D gagal start: $e');
      setState(() => _failed = true);
    }
  }

  void _loadModel() {
    if (!_pageReady || _isFlutterTest) return;
    unawaited(
      _webViewController.runJavaScript(
        'window.__avatar3d.loadModel(${jsonEncode(widget.modelUrl)})',
      ),
    );
  }

  void _onPageReadyTimeout() {
    if (!mounted || _pageReady || _failed) return;
    widget.onError?.call('Halaman avatar 3D tidak siap (three.js gagal dimuat)');
    setState(() => _failed = true);
  }

  void _onJsMessage(JavaScriptMessage message) {
    if (!mounted) return;
    try {
      final data = jsonDecode(message.message) as Map<String, dynamic>;
      switch (data['type']) {
        case 'ready':
          // The page API exists now (module script executed): cancel
          // the readiness timeout and load the model.
          _pageReadyTimer?.cancel();
          _pageReady = true;
          _loadModel();
        case 'loaded':
          final animations = (data['animations'] as List<dynamic>? ?? [])
              .map((e) => e.toString())
              .toList();
          final morphs = (data['morphs'] as List<dynamic>? ?? [])
              .map((e) => e.toString())
              .toList();
          setState(() => _modelReady = true);
          // Prefer the idle clip when the model has one (halfBody:
          // idle_eyes keeps the eyes alive and the render loop busy).
          final idle = animations.firstWhere(
            (name) => name.toLowerCase().contains('idle'),
            orElse: () => animations.isNotEmpty ? animations.first : '',
          );
          if (idle.isNotEmpty) {
            unawaited(
              _webViewController.runJavaScript(
                'window.__avatar3d.playAnimation(${jsonEncode(idle)})',
              ),
            );
          }
          widget.onLoaded?.call(animations, morphs);
        case 'error':
          final error = data['message']?.toString() ?? 'unknown error';
          widget.onError?.call(error);
          setState(() => _failed = true);
      }
    } catch (_) {
      // A malformed channel message must not take the avatar down.
    }
  }

  /// Advances the smoothed mouth toward the latest lip-sync frame and
  /// sends the morph weights when they moved visibly. Same timing as
  /// the Live2D avatar's ticker, so the two avatars stay comparable.
  /// The attack is gentler than the Live2D avatar's (70 ms vs 45 ms):
  /// the 3D mouth snapping open looked startling, so it eases open
  /// instead.
  void _tickMouth() {
    if (!mounted || _failed || _isFlutterTest || !_modelReady) return;
    final now = DateTime.now();
    final dt = now.difference(_lastMouthTick).inMilliseconds / 1000.0;
    _lastMouthTick = now;
    if (dt <= 0) return;
    final openTarget =
        widget.isSpeaking ? (_lipSyncFrame?.openness ?? 0.0) : 0.0;
    final formTarget = (widget.isSpeaking
            ? (_lipSyncFrame?.formDelta ?? 0.0)
            : 0.0)
        .clamp(-1.0, 1.0);
    final tauOpen = openTarget > _mouth ? 0.07 : 0.18;
    final kOpen = 1.0 - math.exp(-dt / tauOpen);
    _mouth += (openTarget - _mouth) * kOpen;
    if (_mouth < 0.02) _mouth = 0.0;
    final kForm = 1.0 - math.exp(-dt / 0.09);
    _mouthForm += (formTarget - _mouthForm) * kForm;
    // Smooth the vowel weights too: the raw frame weights jump between
    // vowels, and without this the mouth shape snaps from one vowel
    // to the next instead of gliding.
    final rawWeights = _lipSyncFrame?.weights ?? const <String, double>{};
    final kVowel = 1.0 - math.exp(-dt / 0.08);
    final smoothed = <String, double>{};
    for (final key in const ['aa', 'ee', 'ih', 'oh', 'ou']) {
      final prev = _smoothedVowelWeights[key] ?? 0.0;
      final target = widget.isSpeaking ? (rawWeights[key] ?? 0.0) : 0.0;
      smoothed[key] = prev + (target - prev) * kVowel;
    }
    _smoothedVowelWeights = smoothed;
    _sendMorphs();
  }

  void _sendMorphs({bool force = false}) {
    if (_failed || _isFlutterTest || !_modelReady) return;
    final weights = morphWeightsFor(
      openness: _mouth,
      formDelta: _mouthForm,
      vowelWeights: _smoothedVowelWeights,
    );
    var changed = force;
    if (!changed) {
      for (final entry in weights.entries) {
        if (((_lastSentMorphs[entry.key] ?? -1.0) - entry.value).abs() >=
            0.01) {
          changed = true;
          break;
        }
      }
    }
    if (!changed) return;
    _lastSentMorphs = weights;
    unawaited(
      _webViewController.runJavaScript(
        'window.__avatar3d.setMorphs(${jsonEncode(weights)})',
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_isFlutterTest || _failed) {
      // No WebView in a widget test, or the model/page failed to load:
      // keep the old emoji avatar instead of a blank stage.
      return EmotionDisplay(emotion: widget.emotion, size: widget.size);
    }
    return SizedBox(
      width: widget.size,
      height: widget.height ?? widget.size,
      child: Stack(
        children: [
          WebViewWidget(controller: _webViewController),
          if (!_modelReady)
            const Center(child: CircularProgressIndicator()),
        ],
      ),
    );
  }
}
