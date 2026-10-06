import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:pdfrx/pdfrx.dart';
import 'package:xml/xml.dart';

class AttachmentTextReader {
  static const maxPdfPages = 4;

  static Future<String> extract({
    required String fileName,
    required Uint8List bytes,
  }) async {
    final extension = fileName.split('.').last.toLowerCase();
    if ({'txt', 'md', 'json', 'csv', 'log', 'ini', 'yaml', 'yml', 'xml', 'html', 'htm'}
        .contains(extension)) {
      return utf8.decode(bytes, allowMalformed: true).trim();
    }
    if (extension == 'docx') return _extractDocx(bytes);
    if (extension == 'pdf') return _extractPdf(fileName, bytes);
    if (extension == 'doc') {
      throw const FormatException(
        'Format .doc lama belum didukung. Simpan sebagai .docx atau .txt lalu lampirkan kembali.',
      );
    }
    throw FormatException('Format .$extension belum didukung sebagai lampiran teks.');
  }

  static String _extractDocx(Uint8List bytes) {
    final archive = ZipDecoder().decodeBytes(bytes);
    final documentFile = archive.findFile('word/document.xml');
    if (documentFile == null) {
      throw const FormatException('Isi dokumen Word tidak ditemukan.');
    }
    final documentBytes = documentFile.readBytes();
    if (documentBytes == null) {
      throw const FormatException('Isi dokumen Word tidak dapat dibaca.');
    }
    final document = XmlDocument.parse(utf8.decode(documentBytes));
    final paragraphs = document
        .findAllElements('w:p')
        .map((paragraph) => paragraph.findAllElements('w:t').map((node) => node.innerText).join())
        .where((paragraph) => paragraph.trim().isNotEmpty)
        .toList();
    if (paragraphs.isEmpty) {
      throw const FormatException('Tidak ada teks yang dapat dibaca di dokumen Word.');
    }
    return paragraphs.join('\n');
  }

  static Future<String> _extractPdf(String fileName, Uint8List bytes) async {
    await pdfrxFlutterInitialize();
    final document = await PdfDocument.openData(bytes, sourceName: fileName);
    try {
      final pages = document.pages.take(maxPdfPages).toList();
      final pageTexts = await Future.wait(
        pages.map((page) async => (await page.loadText())?.fullText ?? ''),
      );
      final text = pageTexts.where((page) => page.trim().isNotEmpty).join('\n\n').trim();
      if (text.isEmpty) {
        throw const FormatException(
          'PDF tidak memiliki teks yang dapat diekstrak. PDF hasil scan memerlukan OCR.',
        );
      }
      if (document.pages.length > pages.length) {
        return '$text\n\n[Hanya halaman 1-${pages.length} dari ${document.pages.length} yang dibaca.]';
      }
      return text;
    } finally {
      await document.dispose();
    }
  }
}