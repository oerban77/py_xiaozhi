import 'dart:async';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'audio_level_visualizer.dart';
import 'app_theme.dart';
import 'attachment_text_reader.dart';
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
  double _dragExtent = 0;

  /// App chrome (header, status bar, composer) visibility. Hidden chrome
  /// leaves the avatar stage full screen; tap the stage or swipe down to
  /// bring it back, swipe up (or wait for the timeout) to hide it again.
  bool _chromeVisible = true;
  Timer? _chromeTimer;
  double _verticalDragExtent = 0;
  

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_scrollToLatest);
    WidgetsBinding.instance.addPostFrameCallback((_) => _armChromeTimer());
    // Prevent force close on Android 16 when typing in chat box:
    // ensure focus node is properly managed and keyboard is dismissed
    // before the widget is disposed.
    _textFocusNode.addListener(_onFocusChange);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_scrollToLatest);
    _chromeTimer?.cancel();
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
      if (mounted) setState(() => _sendingAttachment = false);
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
          body: GestureDetector(
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
                // Soft teal glow behind the avatar.
                Align(
                  alignment: const Alignment(0, -0.1),
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
                        // Live audio waveform (oscilloscope-style) shown
                        // while the mic is active (listening) or the
                        // assistant is speaking (TTS).
                        if (widget.controller.deviceState != DeviceState.idle)
                          _buildAudioVisualizer(),
                        _buildLiveTextArea(),
                        _chromeFade(child: _buildComposer()),
                      ],
                    ),
                  ),
                ),
              ],
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

  /// Avatar stage, enlarged and transparent: the model fills most of the
  /// screen (up from the old 340 cap) so the mouth is clearly visible,
  /// floating over the dark stage instead of a panel.
  Widget _buildAvatarStage() {
    final controller = widget.controller;
    return LayoutBuilder(
      builder: (context, constraints) {
        final emotion = controller.emotion.isEmpty ? 'neutral' : controller.emotion;
        final size = math.min(
          640.0,
          math.min(
            constraints.maxWidth * 0.94,
            constraints.maxHeight * 0.72,
          ),
        );
        return Align(
          alignment: const Alignment(0, -0.12),
          child: Live2DAvatar(
            emotion: emotion,
            isSpeaking: controller.deviceState == DeviceState.speaking,
            isListening: controller.deviceState == DeviceState.listening,
            audioLevelStream: controller.audioLevelStream,
            size: size,
          ),
        );
      },
    );
  }

  /// Live transcript area. Compact by design (widget tests assert this):
  /// at most 160 high. This is content, not chrome, so it stays visible
  /// even when the header and composer are hidden.
  Widget _buildLiveTextArea() {
    final controller = widget.controller;
    final textMaxWidth =
        math.min(MediaQuery.sizeOf(context).width * 0.88, 560.0);
    final textAreaHeight =
        math.min(160.0, MediaQuery.sizeOf(context).height * 0.22);
    return SizedBox(
      key: const ValueKey('live-text-area'),
      height: textAreaHeight,
      child: Container(
        margin: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
        decoration: BoxDecoration(
          color: Colors.white.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: Colors.white.withValues(alpha: 0.12)),
        ),
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
                    controller.liveText.isEmpty
                        ? 'Ada yang bisa aku bantu hari ini?'
                        : controller.liveText,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: Color(0xFFEAF3F1),
                      fontSize: 14,
                      height: 1.5,
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
                        _attachment!.name,
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