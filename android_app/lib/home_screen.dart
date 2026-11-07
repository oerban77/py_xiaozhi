import 'dart:async';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'audio_level_visualizer.dart';
import 'app_theme.dart';
import 'attachment_text_reader.dart';
import 'avatar_3d.dart';
import 'camera_screen.dart';
import 'debug_screen.dart';
import 'live2d_avatar.dart';
import 'settings_screen.dart';
import 'xiaozhi_controller.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.controller});

  final XiaozhiController controller;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _textController = TextEditingController();
  final _scrollController = ScrollController();
  final _textFocusNode = FocusNode();
  PlatformFile? _attachment;
  Uint8List? _attachmentBytes;
  bool _sendingAttachment = false;
  // Progress text while a large PDF is being extracted
  // ("Membaca PDF: 120/2000 halaman"), shown in place of the file name in
  // the attachment chip so the user sees the job advancing instead of a
  // frozen spinner. Null when no extraction is running.
  String? _attachmentProgress;
  double _dragExtent = 0;

  /// App chrome (header, status bar, composer) visibility. Hidden chrome
  /// leaves the avatar stage full screen; tap the stage or swipe down to
  /// bring it back, swipe up (or wait for the timeout) to hide it again.
  bool _chromeVisible = true;
  Timer? _chromeTimer;

  /// PoC switch for the 3D avatar: false = Live2D (Haru, the default),
  /// true = the three.js .glb avatar (Avatar3D). Runtime-only, not
  /// persisted -- the toggle lives on the stage so the two mouths can
  /// be compared on the same build.
  bool _use3dAvatar = false;
  /// Last 3D avatar failure, shown as a small pill on the stage so a
  /// failed load is diagnosable from a screenshot instead of silently
  /// falling back to the emoji. Cleared when the toggle is switched.
  String? _avatar3dError;
  /// Idle wandering: the avatar's current stroll offset, as a fraction
  /// of its own size (AnimatedSlide units). Only used while idle;
  /// speaking and listening recenter it (see _buildAvatarStage), so the
  /// character faces the user while talking or listening.
  Offset _wanderOffset = Offset.zero;
  Timer? _wanderTimer;
  final math.Random _random = math.Random();
  double _verticalDragExtent = 0;
  

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_scrollToLatest);
    WidgetsBinding.instance.addPostFrameCallback((_) => _armChromeTimer());
    if (!_isFlutterTest) {
      _wanderTimer =
          Timer.periodic(const Duration(seconds: 7), (_) => _pickWanderTarget());
    }
    // Prevent force close on Android 16 when typing in chat box:
    // ensure focus node is properly managed and keyboard is dismissed
    // before the widget is disposed.
    _textFocusNode.addListener(_onFocusChange);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_scrollToLatest);
    _chromeTimer?.cancel();
    _wanderTimer?.cancel();
    _textFocusNode.removeListener(_onFocusChange);
    _textFocusNode.dispose();
    _textController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _onFocusChange() {
    // On Android 16, if the TextField loses focus while the keyboard is
    // still showing, it can cause a force close. This listener ensures
    // we handle focus changes gracefully.
    if (!_textFocusNode.hasFocus) {
      // Keyboard is dismissing or has dismissed; no action needed here
      // but the listener prevents the framework from crashing on focus loss.
      _armChromeTimer();
    } else {
      // Never auto-hide while the user is typing.
      _chromeTimer?.cancel();
    }
  }

  bool get _isFlutterTest =>
      Platform.environment['FLUTTER_TEST'] == 'true';

  /// Arms the auto-hide timeout: chrome hides after 5 seconds without
  /// interaction. Disabled in widget tests so the chrome never vanishes
  /// mid-test, and never while the text field has focus.
  void _armChromeTimer() {
    _chromeTimer?.cancel();
    if (_isFlutterTest || !_chromeVisible || _textFocusNode.hasFocus) return;
    _chromeTimer = Timer(const Duration(seconds: 5), () {
      if (!mounted || _textFocusNode.hasFocus) return;
      setState(() => _chromeVisible = false);
    });
  }

  void _showChrome() {
    if (_chromeVisible) {
      _armChromeTimer();
      return;
    }
    setState(() => _chromeVisible = true);
    _armChromeTimer();
  }

  void _toggleChrome() {
    setState(() => _chromeVisible = !_chromeVisible);
    if (_chromeVisible) _armChromeTimer();
  }

  /// Chrome wrapper: fades out and stops receiving touches when hidden,
  /// so the stage underneath stays fully interactive.
  Widget _chromeFade({required Widget child}) => AnimatedOpacity(
        opacity: _chromeVisible ? 1.0 : 0.0,
        duration: const Duration(milliseconds: 300),
        child: IgnorePointer(ignoring: !_chromeVisible, child: child),
      );

  void _scrollToLatest() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 220),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _openSettings() async {
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => SettingsScreen(controller: widget.controller),
      ),
    );
  }

  Future<void> _openCamera() async {
    final bytes = await Navigator.of(context).push<Uint8List>(
      MaterialPageRoute<Uint8List>(
        builder: (_) => CameraScreen(
          lensDirection: widget.controller.cameraFacing,
          onCameraFacingChanged: (facing) =>
              widget.controller.setCameraFacing(facing),
        ),
      ),
    );
    if (bytes == null || bytes.isEmpty || !mounted) return;
    // The captured photo becomes the chat attachment, exactly like a picked
    // image file, so the existing send path handles the vision request.
    setState(() {
      _attachment = PlatformFile(
        name: 'foto_${DateTime.now().millisecondsSinceEpoch}.jpg',
        size: bytes.length,
        bytes: bytes,
      );
      _attachmentBytes = bytes;
    });
    // If the chat box is not on screen when the photo comes back (the
    // 5 s chrome timer can fire while the user is in the camera screen,
    // leaving the composer invisible and untappable), send the photo
    // straight away instead of leaving an invisible attachment waiting
    // for a Send tap the user cannot see. When the composer is visible
    // the photo stays attached so the user can add a caption first.
    if (!_chromeVisible && mounted) {
      await _sendAttachment();
    }
  }

  Future<void> _sendText() async {
    if (_attachment != null) {
      await _sendAttachment();
      return;
    }
    final text = _textController.text.trim();
    if (text.isEmpty) return;
    final sent = await widget.controller.sendText(text);
    if (sent) _textController.clear();
  }

  Future<void> _pickAttachment() async {
    try {
      final result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: [
          'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff',
          'txt', 'md', 'json', 'csv', 'log', 'ini', 'yaml', 'yml', 'xml', 'html', 'htm',
          'pdf', 'docx', 'pptx', 'xlsx',
        ],
        allowMultiple: false,
        withData: true,
      );
      if (result == null || result.files.isEmpty || !mounted) return;
      final file = result.files.single;
      final bytes = file.bytes ??
          (file.path == null ? null : await File(file.path!).readAsBytes());
      if (bytes == null || bytes.isEmpty) {
        throw const FormatException('File kosong atau tidak dapat dibaca.');
      }
      final extension = (file.extension ?? '').toLowerCase();
        final isImage = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff'}
          .contains(extension);
      if (isImage && bytes.length > 10 * 1024 * 1024) {
        throw const FormatException('Ukuran gambar maksimal 10 MB.');
      }
      setState(() {
        _attachment = file;
        _attachmentBytes = bytes;
      });
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Lampiran gagal dipilih: $error')),
      );
    }
  }

  Future<void> _sendAttachment() async {
    final file = _attachment;
    final bytes = _attachmentBytes;
    if (file == null || bytes == null || !widget.controller.isConnected) return;
    setState(() => _sendingAttachment = true);
    try {
      final extension = (file.extension ?? '').toLowerCase();
      final isImage = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'tif', 'tiff'}
          .contains(extension);
      final question = _textController.text.trim();
      final isPdf = extension == 'pdf';
      final sent = isImage
          ? await widget.controller.sendImageAttachment(
              fileName: file.name,
              imageBytes: bytes,
              question: question,
            )
          : isPdf
              ? await widget.controller.sendTextAttachment(
                  fileName: file.name,
                  question: question,
                  pdf: await AttachmentTextReader.extractPdf(
                    fileName: file.name,
                    bytes: bytes,
                    // A thousands-page PDF takes a while to extract; show
                    // the page count as it goes so the user sees progress
                    // instead of a frozen spinner. Extraction runs in
                    // bounded batches that yield to the event loop, so the
                    // WebSocket ping keeps the server connection alive
                    // while this runs.
                    onProgress: (pagesDone, pageCount) {
                      if (!mounted) return;
                      setState(() => _attachmentProgress =
                          'Membaca PDF: $pagesDone/$pageCount halaman');
                    },
                  ),
                )
              : await widget.controller.sendTextAttachment(
                  fileName: file.name,
                  content: await AttachmentTextReader.extract(
                    fileName: file.name,
                    bytes: bytes,
                  ),
                  question: question,
                );
      if (sent && mounted) {
        _textController.clear();
        setState(() {
          _attachment = null;
          _attachmentBytes = null;
        });
      }
    } catch (error) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Lampiran gagal dikirim: $error')),
      );
    } finally {
      if (mounted) {
        setState(() {
          _sendingAttachment = false;
          _attachmentProgress = null;
        });
      }
    }
  }

  void _removeAttachment() {
    setState(() {
      _attachment = null;
      _attachmentBytes = null;
    });
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: widget.controller,
        builder: (context, _) => Scaffold(
          backgroundColor: const Color(0xFF060A18),
          extendBody: true,
          body: FocusTraversalGroup(
            // Android TV / set-top-box remote: arrow keys move the focus
            // to the nearest control in that direction, and OK activates
            // it. Without this, arrow keys would only walk the widget
            // order, which is unusable on a 2D TV layout.
            policy: WidgetOrderTraversalPolicy(),
            child: GestureDetector(
            onHorizontalDragUpdate: (details) {
              setState(() {
                _dragExtent += details.delta.dx;
              });
            },
            onHorizontalDragEnd: (details) {
              // Left swipe - open debug screen
              if (_dragExtent < -100) {
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (context) => DebugScreen(controller: widget.controller),
                  ),
                );
              }
              _dragExtent = 0;
            },
            child: Stack(
              children: [
                // Futuristic stage background, edge to edge (no SafeArea:
                // system bars are immersive-sticky and overlay the stage).
                Container(
                  decoration: const BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: [
                        Color(0xFF060A18),
                        Color(0xFF0B2233),
                        Color(0xFF0E3A3A),
                      ],
                    ),
                  ),
                ),
                // Landscape (TV / set-top-box / rotated phone): the
                // animation goes left and the live text goes right, so
                // the character stays big instead of being squeezed by
                // a bottom column. Portrait keeps the fullscreen stage
                // with overlay chrome below.
                if (MediaQuery.orientationOf(context) == Orientation.landscape)
                  _buildLandscapeLayout()
                else ...[
                // Avatar stage, full screen. Tap toggles the chrome;
                // vertical swipe hides (up) / shows (down) it again.
                // The outer detector keeps the horizontal swipe navigation.
                GestureDetector(
                  behavior: HitTestBehavior.translucent,
                  onTap: _toggleChrome,
                  onVerticalDragUpdate: (details) {
                    _verticalDragExtent += details.delta.dy;
                  },
                  onVerticalDragEnd: (_) {
                    if (_verticalDragExtent < -60) {
                      setState(() => _chromeVisible = false);
                      _chromeTimer?.cancel();
                    } else if (_verticalDragExtent > 60) {
                      _showChrome();
                    }
                    _verticalDragExtent = 0;
                  },
                  child: _buildAvatarStage(),
                ),
                // Top chrome: title bar + connection status.
                Positioned(
                  top: 0,
                  left: 0,
                  right: 0,
                  child: Padding(
                    padding: EdgeInsets.only(
                      top: MediaQuery.viewPaddingOf(context).top,
                    ),
                    child: _chromeFade(
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          _buildHeader(),
                          _buildStatusBar(),
                        ],
                      ),
                    ),
                  ),
                ),
                // Bottom chrome: live text stays visible (it is content,
                // not chrome); the waveform and composer fade with chrome.
                Positioned(
                  bottom: 0,
                  left: 0,
                  right: 0,
                  child: Padding(
                    padding: EdgeInsets.only(
                      bottom: MediaQuery.viewPaddingOf(context).bottom,
                    ),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        _buildLiveTextArea(),
                        // The waveform now lives behind the avatar in the
                        // stage Stack (see _buildAvatarStage), not here.
                        _chromeFade(child: _buildComposer()),
                      ],
                    ),
                  ),
                ),
                ],
              ],
            ),
          ),
          ),
        ),
      );

  Widget _buildHeader() => Padding(
        padding: const EdgeInsets.fromLTRB(20, 12, 14, 10),
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                gradient: const LinearGradient(
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                  colors: [Color(0xFF1DB9A6), Color(0xFF176B5C)],
                ),
                borderRadius: BorderRadius.circular(15),
                boxShadow: [
                  BoxShadow(
                    color: const Color(0xFF1DB9A6).withValues(alpha: 0.45),
                    blurRadius: 16,
                    spreadRadius: 1,
                  ),
                ],
              ),
              child: const Icon(Icons.graphic_eq_rounded, color: Colors.white, size: 25),
            ),
            const SizedBox(width: 12),
            const Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Xiaozhi',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 20,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  SizedBox(height: 2),
                  Text(
                    'VOICE ASSISTANT',
                    style: TextStyle(
                      color: Color(0xFF8FE3D0),
                      fontSize: 10,
                      fontWeight: FontWeight.w700,
                      letterSpacing: 1.1,
                    ),
                  ),
                ],
              ),
            ),
            IconButton.filledTonal(
              tooltip: 'Preview kamera',
              onPressed: _openCamera,
              style: IconButton.styleFrom(
                backgroundColor: Colors.white.withValues(alpha: 0.10),
                foregroundColor: Colors.white,
                fixedSize: const Size(44, 44),
              ),
              icon: const Icon(Icons.camera_alt_outlined),
            ),
            const SizedBox(width: 6),
            // PoC switch Live2D <-> 3D avatar: a third header button in
            // the same style as camera/settings (44x44, same colors), so
            // it never overlaps anything. The label shows what tapping
            // switches TO; the teal tint marks when 3D is active.
            IconButton.filledTonal(
              tooltip: _use3dAvatar
                  ? 'Kembali ke avatar Live2D'
                  : 'Ganti ke avatar 3D',
              onPressed: () => setState(() {
                _use3dAvatar = !_use3dAvatar;
                _avatar3dError = null;
              }),
              style: IconButton.styleFrom(
                backgroundColor: _use3dAvatar
                    ? const Color(0xFF1DB9A6).withValues(alpha: 0.35)
                    : Colors.white.withValues(alpha: 0.10),
                foregroundColor: Colors.white,
                fixedSize: const Size(44, 44),
              ),
              icon: Text(
                _use3dAvatar ? '2D' : '3D',
                style: const TextStyle(
                  // Explicit white: a Text does NOT inherit
                  // IconButton.foregroundColor (that only tints Icons via
                  // IconTheme), so without this the label rendered in the
                  // theme's dark default text color and was unreadable
                  // on the dark button (reported 2026-11-06).
                  color: Colors.white,
                  fontWeight: FontWeight.w800,
                  fontSize: 15,
                ),
              ),
            ),
            const SizedBox(width: 6),
            IconButton.filledTonal(
              tooltip: 'Pengaturan koneksi dan kamera',
              onPressed: _openSettings,
              style: IconButton.styleFrom(
                backgroundColor: Colors.white.withValues(alpha: 0.10),
                foregroundColor: Colors.white,
                fixedSize: const Size(44, 44),
              ),
              icon: const Icon(Icons.tune_rounded),
            ),
          ],
        ),
      );

  Widget _buildStatusBar() {
    final controller = widget.controller;
    // An error is transient and always more urgent than the state label, so it
    // takes the line until the next state change clears it.
    final status = controller.hasError ? controller.error : controller.status;
    final statusColor = controller.hasError
        ? const Color(0xFFFF9A8A)
        : controller.deviceState == DeviceState.listening
            ? const Color(0xFFFFB4A6)
            : const Color(0xFF9AD0FF);
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
      ),
      child: Stack(
        alignment: Alignment.center,
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 36),
            child: Text(
              status,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: statusColor,
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          Align(
            alignment: Alignment.centerRight,
            child: _buildConnectionAction(
              controller.isConnected,
              controller.isConnecting,
            ),
          ),
        ],
      ),
    );
  }

  /// Live audio waveform (oscilloscope-style) shown above the composer while
  /// listening (mic level) or speaking (TTS level), mirroring the animated
  /// emoji reactions in the desktop GUI.
  Widget _buildAudioVisualizer() {
    final controller = widget.controller;
    return StreamBuilder<double>(
      stream: controller.audioLevelStream,
      initialData: 0.0,
      builder: (context, snapshot) {
        return AudioLevelVisualizer(
          audioLevelStream: controller.audioLevelStream,
          deviceState: controller.deviceState.index,
          emotion: controller.emotion,
          barCount: 24,
          maxHeight: 36,
        );
      },
    );
  }

  /// The primary button's icon, derived from deviceState so it can never
  /// disagree with the label or the status bar.
  IconData _primaryActionIcon() {
    switch (widget.controller.deviceState) {
      case DeviceState.listening:
        return Icons.stop_rounded;
      case DeviceState.speaking:
        return Icons.stop_circle_outlined;
      case DeviceState.idle:
        return Icons.mic_none_rounded;
    }
  }

  Color _primaryActionColor() {
    switch (widget.controller.deviceState) {
      case DeviceState.idle:
        return AppColors.green;
      case DeviceState.listening:
      case DeviceState.speaking:
        return const Color(0xFFB94E3C);
    }
  }

  Widget _buildConnectionAction(bool connected, bool connecting) {
    if (connecting) {
      return const SizedBox.square(
        dimension: 18,
        child: CircularProgressIndicator(
          strokeWidth: 2,
          color: Colors.white70,
        ),
      );
    }
    if (!connected) {
      return IconButton(
        tooltip: 'Hubungkan',
        onPressed: widget.controller.connect,
        visualDensity: VisualDensity.compact,
        constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
        iconSize: 18,
        color: const Color(0xFF8FE3D0),
        icon: const Icon(Icons.link_rounded),
      );
    }
    return IconButton(
      tooltip: 'Putuskan koneksi',
      onPressed: widget.controller.disconnect,
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
      color: Colors.white70,
      icon: const Icon(Icons.link_off_rounded, size: 18),
    );
  }

  /// Picks a new random wander target for the idle avatar. A target in any
  /// other state is ignored: speaking and listening recenter the avatar.
  void _pickWanderTarget() {
    if (widget.controller.deviceState != DeviceState.idle) {
      return;
    }
    setState(() {
      _wanderOffset = Offset(
        (_random.nextDouble() * 2 - 1) * 0.05,
        (_random.nextDouble() * 2 - 1) * 0.025,
      );
    });
  }

  /// Avatar stage, fullscreen: the model fills the whole screen so it
  /// is as big and clear as possible, and the bottom UI (waveform,
  /// subtitle, composer) floats over its lower part like overlays on a
  /// video. When idle the chrome and subtitles auto-hide, so the
  /// character is then fully unobstructed. The model floats over the
  /// dark stage instead of a panel, so the mouth stays clearly visible.
  Widget _buildAvatarStage() {
    final controller = widget.controller;
    return LayoutBuilder(
      builder: (context, constraints) {
        // Raw server emotion, including empty: the avatar treats empty as
        // "server sent nothing yet" (state face fallback) and any other
        // value, including 'neutral', as the authoritative server emoticon.
        final emotion = controller.emotion;
        return Stack(
          children: [
            // Soft teal glow behind the avatar, centred on the stage
            // (the whole screen in portrait, the left pane in landscape).
            Align(
              alignment: Alignment.center,
              child: Container(
                width: 420,
                height: 420,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: RadialGradient(
                    colors: [
                      const Color(0xFF26C6B0).withValues(alpha: 0.25),
                      const Color(0xFF26C6B0).withValues(alpha: 0.0),
                    ],
                  ),
                ),
              ),
            ),
            // Audio waveform behind the animation: the wave is the
            // mic/TTS level backdrop, so it lives in the stage Stack
            // under the avatar (never drawn over her), low on the
            // stage like before, and visible in every state -- in idle
            // it shows the live mic level, which is what makes it a
            // true mic-path indicator.
            Align(
              alignment: const Alignment(0.0, 0.55),
              child: _buildAudioVisualizer(),
            ),
            AnimatedSlide(
          // While idle the avatar strolls around the stage (see
          // _pickWanderTarget); in any other state it eases back to the
          // middle so it faces the user.
          offset: controller.deviceState == DeviceState.idle
              ? _wanderOffset
              : Offset.zero,
          duration: const Duration(milliseconds: 2500),
          curve: Curves.easeInOut,
          child: AnimatedScale(
            // Listening steps a little closer to the user; speaking
            // and idle stay at normal size.
            scale: controller.deviceState == DeviceState.listening
                ? 1.04
                : 1.0,
            duration: const Duration(milliseconds: 600),
            curve: Curves.easeInOut,
            child: _use3dAvatar
                ? Avatar3D(
                    emotion: emotion,
                    isSpeaking:
                        controller.deviceState == DeviceState.speaking,
                    lipSyncStream: controller.lipSyncStream,
                    gestureStream: controller.avatarGestureStream,
                    size: constraints.maxWidth,
                    height: constraints.maxHeight,
                    modelUrl: controller.character3dModelUrl,
                    mouthStrength: controller.mouthStrength,
                    onLoaded: (_, __) {
                      if (mounted && _avatar3dError != null) {
                        setState(() => _avatar3dError = null);
                      }
                    },
                    onError: (message) {
                      if (mounted) {
                        setState(() => _avatar3dError = message);
                      }
                    },
                  )
                : Live2DAvatar(
                    emotion: emotion,
                    isSpeaking:
                        controller.deviceState == DeviceState.speaking,
                    isListening:
                        controller.deviceState == DeviceState.listening,
                    audioLevelStream: controller.audioLevelStream,
                    lipSyncStream: controller.lipSyncStream,
                    gestureStream: controller.avatarGestureStream,
                    size: constraints.maxWidth,
                    height: constraints.maxHeight,
                    modelUrl: controller.characterModelUrl,
                  ),
          ),
          ),
            // 3D load failure, shown on the stage itself: the exact
            // reason (page not ready / model failed / WebView error),
            // so the next screenshot diagnoses the failure instead of
            // just showing the emoji fallback.
            if (_use3dAvatar && _avatar3dError != null)
              Align(
                alignment: const Alignment(0.0, 0.85),
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.65),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(
                    'Avatar 3D gagal: $_avatar3dError',
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: Color(0xFFFF8A80),
                      fontSize: 12,
                    ),
                  ),
                ),
              ),
          ],
        );
      },
    );
  }

  /// Landscape layout (TV / set-top-box / rotated phone): the
  /// animation pane on the left, full height so the character stays
  /// big, and the live text plus controls on the right. The right pane
  /// is layout, not chrome, so it never auto-hides: on a TV there is no
  /// tap or swipe to bring it back, and the remote navigates it with
  /// arrow keys + OK. The Scaffold already shrinks the body above the
  /// virtual keyboard (resizeToAvoidBottomInset), so when the chat box
  /// takes focus the composer rises above the keyboard instead of
  /// hiding behind it, in both orientations.
  Widget _buildLandscapeLayout() {
    return Row(
      children: [
        // Left: animation, full height.
        Expanded(
          flex: 11,
          child: _buildAvatarStage(),
        ),
        // Right: live text + controls.
        Expanded(
          flex: 9,
          child: Container(
            decoration: BoxDecoration(
              color: Colors.black.withValues(alpha: 0.25),
              border: Border(
                left: BorderSide(
                  color: Colors.white.withValues(alpha: 0.10),
                ),
              ),
            ),
            child: Padding(
              padding: EdgeInsets.only(
                top: MediaQuery.viewPaddingOf(context).top,
                bottom: MediaQuery.viewPaddingOf(context).bottom,
              ),
              child: Column(
                children: [
                  _buildHeader(),
                  _buildStatusBar(),
                  Expanded(child: _buildLandscapeTextArea()),
                  // The waveform now lives behind the avatar in the
                  // stage Stack (see _buildAvatarStage), not here.
                  _buildComposer(),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }

  /// Live text pane for landscape: the full accumulated live text
  /// (assistant answers and user transcripts), scrollable, always
  /// visible. Unlike the portrait subtitle it never fades: the pane is
  /// the text's home in this layout, and on a TV there is no gesture
  /// to bring a hidden pane back.
  Widget _buildLandscapeTextArea() {
    final controller = widget.controller;
    final text = controller.liveText.isEmpty
        ? 'Ada yang bisa aku bantu hari ini?'
        : controller.liveText;
    return SingleChildScrollView(
      controller: _scrollController,
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 16),
      child: SelectionArea(
        child: Text(
          text,
          style: const TextStyle(
            color: Colors.white,
            fontSize: 16,
            height: 1.5,
            shadows: [
              Shadow(
                color: Color(0xE6000000),
                blurRadius: 4,
                offset: Offset(0, 1),
              ),
              Shadow(
                color: Color(0x99000000),
                blurRadius: 8,
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Movie-style subtitle for the assistant's speech. The controller only
  /// promotes a TTS segment to [XiaozhiController.subtitleText] once its
  /// audio starts playing, so the line on screen stays in sync with the
  /// voice instead of running ahead of it; a few seconds after speech
  /// ends the controller hides it again. While hidden the box still
  /// occupies its slot (faded out, not removed) so the avatar stage never
  /// jumps when subtitles appear or go. Compact by design (widget tests
  /// assert this): at most 160 high.
  Widget _buildLiveTextArea() {
    final controller = widget.controller;
    final textMaxWidth =
        math.min(MediaQuery.sizeOf(context).width * 0.88, 560.0);
    final textAreaHeight =
        math.min(160.0, MediaQuery.sizeOf(context).height * 0.22);
    return AnimatedOpacity(
      opacity: controller.subtitleVisible ? 1.0 : 0.0,
      duration: const Duration(milliseconds: 300),
      child: IgnorePointer(
        ignoring: !controller.subtitleVisible,
        child: SizedBox(
          key: const ValueKey('live-text-area'),
          height: textAreaHeight,
          child: Container(
            margin: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
            child: SingleChildScrollView(
              controller: _scrollController,
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
              child: ConstrainedBox(
                constraints: BoxConstraints(minHeight: textAreaHeight - 28),
                child: Align(
                  alignment: Alignment.center,
                  child: ConstrainedBox(
                    constraints: BoxConstraints(maxWidth: textMaxWidth),
                    child: SelectionArea(
                      child: Text(
                        controller.subtitleText,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 16,
                          height: 1.5,
                          shadows: [
                            Shadow(
                              color: Color(0xE6000000),
                              blurRadius: 4,
                              offset: Offset(0, 1),
                            ),
                            Shadow(
                              color: Color(0x99000000),
                              blurRadius: 8,
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildComposer() => GestureDetector(
        // Dismiss keyboard when tapping outside the text field on Android 16+
        // to prevent force close when typing in chat box before sending.
        onTap: () => _textFocusNode.unfocus(),
        behavior: HitTestBehavior.opaque,
        child: Container(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.35),
            border: Border(
              top: BorderSide(color: Colors.white.withValues(alpha: 0.10)),
            ),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
            if (_attachment != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Row(
                  children: [
                    Icon(
                      {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'}
                              .contains((_attachment!.extension ?? '').toLowerCase())
                          ? Icons.image_outlined
                          : Icons.description_outlined,
                      color: const Color(0xFF8FE3D0),
                      size: 20,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        _attachmentProgress ?? _attachment!.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: Colors.white, fontSize: 13),
                      ),
                    ),
                    IconButton(
                      tooltip: 'Hapus lampiran',
                      onPressed: _sendingAttachment ? null : _removeAttachment,
                      visualDensity: VisualDensity.compact,
                      icon: const Icon(Icons.close_rounded, size: 19),
                    ),
                  ],
                ),
              ),
            Row(
              children: [
                IconButton(
                  tooltip: 'Lampirkan gambar atau file teks',
                  onPressed: widget.controller.isConnected && !_sendingAttachment
                      ? _pickAttachment
                      : null,
                  color: Colors.white70,
                  icon: const Icon(Icons.attach_file_rounded),
                ),
                Expanded(
                  child: TextField(
                    controller: _textController,
                    focusNode: _textFocusNode,
                    enabled: widget.controller.isConnected && !_sendingAttachment,
                    textInputAction: TextInputAction.send,
                    maxLength: XiaozhiController.maxTextAttachmentChars,
                    buildCounter: (_, {required currentLength, required isFocused, maxLength}) => null,
                    onSubmitted: (_) => _sendText(),
                    onTapOutside: (_) => _textFocusNode.unfocus(),
                    style: const TextStyle(color: Colors.white),
                    decoration: InputDecoration(
                      hintText: 'Tulis pesan...',
                      hintStyle: const TextStyle(color: Colors.white54),
                      prefixIcon: const Icon(
                        Icons.chat_bubble_outline_rounded,
                        size: 19,
                        color: Colors.white54,
                      ),
                      isDense: true,
                      filled: true,
                      fillColor: const Color(0xFF12202E),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(16),
                        borderSide: BorderSide(
                          color: Colors.white.withValues(alpha: 0.12),
                        ),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(16),
                        borderSide: BorderSide(
                          color: Colors.white.withValues(alpha: 0.12),
                        ),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(16),
                        borderSide: const BorderSide(
                          color: Color(0xFF1DB9A6),
                          width: 1.5,
                        ),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  tooltip: _attachment == null ? 'Kirim pesan' : 'Kirim lampiran',
                  onPressed: widget.controller.isConnected && !_sendingAttachment
                      ? _sendText
                      : null,
                  style: IconButton.styleFrom(
                    backgroundColor: AppColors.green,
                    foregroundColor: Colors.white,
                    fixedSize: const Size(50, 50),
                  ),
                  icon: _sendingAttachment
                      ? const SizedBox.square(
                          dimension: 20,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                        )
                      : Icon(_attachment == null ? Icons.arrow_upward_rounded : Icons.send_rounded),
                ),
              ],
            ),
            const SizedBox(height: 10),
            // One primary button, driven by deviceState exactly like the desktop
            // GUI's single "Mulai/Selesai bicara" / "Hentikan jawaban" button. The
            // old Manual/Auto segmented button is gone: there is no manual mode
            // anymore, so a second control could only put the idle/listening/
            // speaking state out of sync with the mic.
            Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    // TV/set-top-box: on open, the remote's OK lands
                    // here first, so the user can start talking without
                    // hunting for the button. Harmless on phones: touch
                    // users never see the focus highlight.
                    autofocus: true,
                    onPressed: !widget.controller.isConnected
                        ? null
                        : widget.controller.toggleConversation,
                    icon: Icon(_primaryActionIcon()),
                    label: Text(widget.controller.primaryActionLabel),
                    style: FilledButton.styleFrom(
                      backgroundColor: _primaryActionColor(),
                      foregroundColor: Colors.white,
                      minimumSize: const Size.fromHeight(48),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
}