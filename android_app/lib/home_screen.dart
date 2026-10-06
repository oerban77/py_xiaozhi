import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'camera_screen.dart';
import 'chat_message.dart';
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
  PlatformFile? _attachment;
  Uint8List? _attachmentBytes;
  bool _sendingAttachment = false;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_scrollToLatest);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_scrollToLatest);
    _textController.dispose();
    _scrollController.dispose();
    super.dispose();
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
    await Navigator.of(context).push<void>(
      MaterialPageRoute<void>(
        builder: (_) => CameraScreen(lensDirection: widget.controller.cameraFacing),
      ),
    );
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
          'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp',
          'txt', 'md', 'json', 'csv', 'log', 'ini', 'yaml', 'yml', 'xml',
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
      final isImage = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'}.contains(extension);
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
      final isImage = {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'}.contains(extension);
      final question = _textController.text.trim();
      final sent = isImage
          ? await widget.controller.sendImageAttachment(
              fileName: file.name,
              imageBytes: bytes,
              question: question,
            )
          : await widget.controller.sendTextAttachment(
              fileName: file.name,
              content: utf8.decode(bytes, allowMalformed: true),
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
          body: SafeArea(
            child: Column(
              children: [
                _buildHeader(),
                _buildEmotionPanel(),
                Expanded(child: _buildConversation()),
                _buildComposer(),
              ],
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

  /// The status + emotion animation + live TTS text block, mirroring the desktop
  /// GUI's status card area in windows/MainWindow.qml (status label, then the
  /// emotion AnimatedImage, then the scrolling ttsText line) plus the connection
  /// dot from StatusBadge.qml.
  Widget _buildEmotionPanel() {
    final controller = widget.controller;
    final connected = controller.isConnected;
    final connecting = controller.isConnecting;
    final recording = controller.isRecording;
    final hasEmotion = connected && controller.emotion.isNotEmpty;
    final hasLiveText = controller.liveText.isNotEmpty;
    final dotColor = recording
        ? const Color(0xFFCC634F)
        : connected
            ? AppColors.green
            : const Color(0xFF899691);

    return Container(
      margin: const EdgeInsets.fromLTRB(20, 2, 20, 6),
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
      decoration: BoxDecoration(
        color: AppColors.mint,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          // Emotion animation / emoji / placeholder.
          hasEmotion
              ? EmotionDisplay(emotion: controller.emotion, size: 84)
              : const SizedBox(width: 84, height: 84, child: Center(child: EmotionPlaceholder(size: 84))),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                // Status label, like the rounded status Rectangle in
                // MainWindow.qml with the StatusBadge.qml dot in front of it.
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(color: dotColor, shape: BoxShape.circle),
                      ),
                      const SizedBox(width: 8),
                      Flexible(
                        child: Text(
                          controller.status,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: dotColor,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 8),
                // Live TTS text, mirroring mainModel.ttsText in ChatPanel.qml.
                // While idle this is the placeholder the desktop GUI shows
                // ("Waiting for conversation...").
                Text(
                  hasLiveText
                      ? controller.liveText
                      : (connected ? 'Menunggu percakapan...' : 'Belum terhubung'),
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: hasLiveText ? AppColors.ink : const Color(0xFF71817C),
                    fontSize: 13,
                    height: 1.4,
                  ),
                ),
                const SizedBox(height: 8),
                // Connect / disconnect action, previously the standalone status row.
                _buildConnectionAction(connected, connecting),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildConnectionAction(bool connected, bool connecting) {
    if (connecting) {
      return const Align(
        alignment: Alignment.centerLeft,
        child: SizedBox.square(
          dimension: 18,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
      );
    }
    if (!connected) {
      return Align(
        alignment: Alignment.centerLeft,
        child: TextButton.icon(
          onPressed: widget.controller.connect,
          icon: const Icon(Icons.link_rounded, size: 16),
          label: const Text('Hubungkan'),
          style: TextButton.styleFrom(
            foregroundColor: AppColors.green,
            padding: const EdgeInsets.symmetric(horizontal: 10),
          ),
        ),
      );
    }
    return Align(
      alignment: Alignment.centerLeft,
      child: IconButton(
        tooltip: 'Putuskan koneksi',
        onPressed: widget.controller.disconnect,
        visualDensity: VisualDensity.compact,
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints(),
        icon: const Icon(Icons.link_off_rounded, size: 19),
      ),
    );
  }

  Widget _buildConversation() {
    final messages = widget.controller.messages;
    if (messages.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 42),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 76,
                height: 76,
                decoration: const BoxDecoration(color: AppColors.mint, shape: BoxShape.circle),
                child: const Icon(Icons.waves_rounded, color: AppColors.green, size: 37),
              ),
              const SizedBox(height: 22),
              const Text(
                'Mulai percakapan',
                textAlign: TextAlign.center,
                style: TextStyle(color: AppColors.ink, fontSize: 22, fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 8),
              const Text(
                'Kirim pesan atau tekan tombol mikrofon untuk berbicara.',
                textAlign: TextAlign.center,
                style: TextStyle(color: Color(0xFF71817C), height: 1.45),
              ),
            ],
          ),
        ),
      );
    }

    return ListView.builder(
      controller: _scrollController,
      padding: const EdgeInsets.fromLTRB(18, 10, 18, 18),
      itemCount: messages.length,
      itemBuilder: (context, index) => _MessageBubble(message: messages[index]),
    );
  }

  Widget _buildComposer() => Container(
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
                    enabled: widget.controller.isConnected && !_sendingAttachment,
                    textInputAction: TextInputAction.send,
                    maxLength: XiaozhiController.maxTextAttachmentChars,
                    buildCounter: (_, {required currentLength, required isFocused, maxLength}) => null,
                    onSubmitted: (_) => _sendText(),
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
            SegmentedButton<bool>(
              segments: const [
                ButtonSegment<bool>(
                  value: false,
                  icon: Icon(Icons.keyboard_voice_outlined),
                  label: Text('Manual'),
                ),
                ButtonSegment<bool>(
                  value: true,
                  icon: Icon(Icons.auto_awesome_outlined),
                  label: Text('Auto'),
                ),
              ],
              selected: {widget.controller.autoConversation},
              onSelectionChanged: (selection) async {
                if (selection.isEmpty) return;
                await widget.controller.setAutoConversation(selection.first);
              },
              style: const ButtonStyle(
                minimumSize: WidgetStatePropertyAll(Size.fromHeight(42)),
              ),
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: FilledButton.icon(
                    onPressed: !widget.controller.isConnected
                        ? null
                        : widget.controller.isRecording
                            ? widget.controller.stopVoice
                            : widget.controller.startVoice,
                    icon: Icon(widget.controller.isRecording ? Icons.stop_rounded : Icons.mic_none_rounded),
                    label: Text(widget.controller.isRecording ? 'Selesai bicara' : 'Mulai bicara'),
                    style: FilledButton.styleFrom(
                      backgroundColor: widget.controller.isRecording ? const Color(0xFFB94E3C) : AppColors.green,
                      foregroundColor: Colors.white,
                      minimumSize: const Size.fromHeight(48),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                    ),
                  ),
                ),
                if (widget.controller.isSpeaking) ...[
                  const SizedBox(width: 8),
                  IconButton.filledTonal(
                    tooltip: 'Hentikan jawaban',
                    onPressed: widget.controller.interrupt,
                    style: IconButton.styleFrom(
                      foregroundColor: const Color(0xFFB94E3C),
                      fixedSize: const Size(48, 48),
                    ),
                    icon: const Icon(Icons.stop_circle_outlined),
                  ),
                ],
              ],
            ),
          ],
        ),
      );
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final user = message.isUser;
    return Align(
      alignment: user ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: BoxConstraints(maxWidth: MediaQuery.sizeOf(context).width * 0.82),
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 12),
        decoration: BoxDecoration(
          color: user ? AppColors.green : Colors.white,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(18),
            topRight: const Radius.circular(18),
            bottomLeft: Radius.circular(user ? 18 : 5),
            bottomRight: Radius.circular(user ? 5 : 18),
          ),
          boxShadow: user
              ? null
              : const [BoxShadow(color: Color(0x0D172B28), blurRadius: 12, offset: Offset(0, 3))],
        ),
        child: Text(
          message.text,
          style: TextStyle(
            color: user ? Colors.white : AppColors.ink,
            fontSize: 15,
            height: 1.4,
          ),
        ),
      ),
    );
  }
}