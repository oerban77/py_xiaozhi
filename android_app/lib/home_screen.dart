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
import 'emotion_display.dart';
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

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_scrollToLatest);
    // Prevent force close on Android 16 when typing in chat box:
    // ensure focus node is properly managed and keyboard is dismissed
    // before the widget is disposed.
    _textFocusNode.addListener(_onFocusChange);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_scrollToLatest);
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
    }
  }

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
          backgroundColor: AppColors.paper,
          body: GestureDetector(
            onHorizontalDragEnd: (details) {
              if (details.delta.dx < -50) {
                // Left swipe - open debug screen
                Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (context) => DebugScreen(controller: widget.controller),
                  ),
                );
              }
            },
            child: SafeArea(
              child: Column(
                children: [
                  _buildHeader(),
                  _buildStatusBar(),
                  Expanded(child: _buildConversation()),
                  // Live audio waveform (oscilloscope-style) shown while the mic
                  // is active (listening) or the assistant is speaking (TTS).
                  // Mirrors the animated emoji reactions in the desktop GUI.
                  if (widget.controller.deviceState != DeviceState.idle)
                    _buildAudioVisualizer(),
                  _buildComposer(),
                ],
              ),
            ),
          ),
        ),
      );

  Widget _buildHeader() => Padding(
        padding: const EdgeInsets.fromLTRB(20, 14, 14, 10),
        child: Row(
          children: [
            Container(
              width: 44,
              height: 44,
              decoration: BoxDecoration(
                color: AppColors.green,
                borderRadius: BorderRadius.circular(15),
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
                      color: AppColors.ink,
                      fontSize: 20,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  SizedBox(height: 2),
                  Text(
                    'VOICE ASSISTANT',
                    style: TextStyle(
                      color: Color(0xFF71817C),
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
                backgroundColor: Colors.white,
                foregroundColor: AppColors.ink,
                fixedSize: const Size(44, 44),
              ),
              icon: const Icon(Icons.camera_alt_outlined),
            ),
            const SizedBox(width: 6),
            IconButton.filledTonal(
              tooltip: 'Pengaturan koneksi dan kamera',
              onPressed: _openSettings,
              style: IconButton.styleFrom(
                backgroundColor: Colors.white,
                foregroundColor: AppColors.ink,
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
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
      color: const Color(0xFFE8F2FF),
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
                color: controller.deviceState == DeviceState.listening
                    ? const Color(0xFFCC634F)
                    : const Color(0xFF2583E8),
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
        child: CircularProgressIndicator(strokeWidth: 2),
      );
    }
    if (!connected) {
      return IconButton(
        tooltip: 'Hubungkan',
        onPressed: widget.controller.connect,
        visualDensity: VisualDensity.compact,
        constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
        iconSize: 18,
        color: AppColors.green,
        icon: const Icon(Icons.link_rounded),
      );
    }
    return IconButton(
      tooltip: 'Putuskan koneksi',
      onPressed: widget.controller.disconnect,
      visualDensity: VisualDensity.compact,
      padding: EdgeInsets.zero,
      constraints: const BoxConstraints(minWidth: 32, minHeight: 32),
      icon: const Icon(Icons.link_off_rounded, size: 18),
    );
  }

  Widget _buildConversation() {
    final controller = widget.controller;
    return LayoutBuilder(
      builder: (context, constraints) {
        final emotion = controller.emotion.isEmpty ? 'neutral' : controller.emotion;
        final textMaxWidth = math.min(constraints.maxWidth * 0.88, 560.0);
        final textAreaHeight = math.min(160.0, constraints.maxHeight * 0.3);

        return Column(
          children: [
            Expanded(
              child: Container(
                color: AppColors.paper,
                child: LayoutBuilder(
                  builder: (context, emotionConstraints) {
                    final emotionSize = math.min(
                      240.0,
                      math.min(
                        emotionConstraints.maxWidth * 0.78,
                        emotionConstraints.maxHeight * 0.9,
                      ),
                    );
                    return Align(
                      alignment: const Alignment(0, -0.08),
                      child: EmotionDisplay(emotion: emotion, size: emotionSize),
                    );
                  },
                ),
              ),
            ),
            SizedBox(
              key: const ValueKey('live-text-area'),
              height: textAreaHeight,
              child: Container(
                margin: const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
                decoration: BoxDecoration(
                  color: AppColors.paper,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: LayoutBuilder(
                  builder: (context, textConstraints) => SingleChildScrollView(
                    controller: _scrollController,
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
                    child: ConstrainedBox(
                      constraints: BoxConstraints(minHeight: textConstraints.maxHeight),
                      child: Align(
                        alignment: Alignment.center,
                        child: ConstrainedBox(
                          constraints: BoxConstraints(maxWidth: textMaxWidth),
                          child: Text(
                            controller.liveText.isEmpty
                                ? 'Ada yang bisa aku bantu hari ini?'
                                : controller.liveText,
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                              color: Color(0xFF394B60),
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
            ),
          ],
        );
      },
    );
  }

  Widget _buildComposer() => GestureDetector(
        // Dismiss keyboard when tapping outside the text field on Android 16+
        // to prevent force close when typing in chat box before sending.
        onTap: () => _textFocusNode.unfocus(),
        behavior: HitTestBehavior.opaque,
        child: Container(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
          decoration: const BoxDecoration(
            color: Colors.white,
            border: Border(top: BorderSide(color: Color(0xFFE4EAE7))),
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
                      color: AppColors.green,
                      size: 20,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        _attachment!.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(color: AppColors.ink, fontSize: 13),
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
                    decoration: const InputDecoration(
                      hintText: 'Tulis pesan...',
                      prefixIcon: Icon(Icons.chat_bubble_outline_rounded, size: 19),
                      isDense: true,
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