import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:pdfrx/pdfrx.dart';
import 'package:xml/xml.dart';

/// Extracted PDF text kept page by page, mirroring how
/// src/mcp/tools/documents/service.py reads PDFs: the whole document is parsed
/// once and then served to the LLM in slices of [AttachmentTextReader.pdfPagesPerRead]
/// pages, so "continue" and "jump to page N" work without re-uploading.
class PdfExtraction {
  const PdfExtraction(this.pages);

  /// One entry per PDF page, in document order. Blank pages stay empty so the
  /// 1-based index always matches the real page number.
  final List<String> pages;

  int get pageCount => pages.length;

  String get text =>
      pages.where((page) => page.trim().isNotEmpty).join('\n\n').trim();
}

class AttachmentTextReader {
  /// Mirrors _PDF_MAX_PAGES_PER_READ in src/mcp/tools/documents/service.py:
  /// one manage_document read returns at most this many PDF pages.
  static const pdfPagesPerRead = 4;

  /// Mirrors _PDF_MAX_CHARS_PER_READ: a single read returns at most this many
  /// characters, so one huge page cannot swallow the whole reply budget.
  static const pdfCharsPerRead = 6000;

  /// Mirrors _MAX_DOCUMENT_CHARS_PER_READ: non-PDF documents are paged in
  /// chunks of this many characters.
  static const documentCharsPerRead = 24000;

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
    if (extension == 'pptx') return _extractPptx(bytes);
    if (extension == 'xlsx') return _extractXlsx(bytes);
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

  static String _extractPptx(Uint8List bytes) {
    final archive = ZipDecoder().decodeBytes(bytes);
    final slides = archive.files
        .where((file) => RegExp(r'^ppt/slides/slide\d+\.xml$').hasMatch(file.name))
        .toList()
      ..sort((a, b) => _fileNumber(a.name).compareTo(_fileNumber(b.name)));
    final slideTexts = <String>[];
    for (final slide in slides) {
      final slideBytes = slide.readBytes();
      if (slideBytes == null) continue;
      final document = XmlDocument.parse(utf8.decode(slideBytes));
      final text = document
          .findAllElements('a:t')
          .map((node) => node.innerText)
          .where((value) => value.trim().isNotEmpty)
          .join(' ');
      if (text.isNotEmpty) slideTexts.add(text);
    }
    if (slideTexts.isEmpty) {
      throw const FormatException('Tidak ada teks yang dapat dibaca di presentasi PowerPoint.');
    }
    return slideTexts.join('\n\n');
  }

  static String _extractXlsx(Uint8List bytes) {
    final archive = ZipDecoder().decodeBytes(bytes);
    final sharedStrings = <String>[];
    final sharedStringsFile = archive.findFile('xl/sharedStrings.xml');
    final sharedStringsBytes = sharedStringsFile?.readBytes();
    if (sharedStringsBytes != null) {
      final document = XmlDocument.parse(utf8.decode(sharedStringsBytes));
      for (final item in document.findAllElements('si')) {
        sharedStrings.add(item.findAllElements('t').map((node) => node.innerText).join());
      }
    }

    final sheets = archive.files
        .where((file) => RegExp(r'^xl/worksheets/sheet\d+\.xml$').hasMatch(file.name))
        .toList()
      ..sort((a, b) => _fileNumber(a.name).compareTo(_fileNumber(b.name)));
    final rows = <String>[];
    for (final sheet in sheets) {
      final sheetBytes = sheet.readBytes();
      if (sheetBytes == null) continue;
      final document = XmlDocument.parse(utf8.decode(sheetBytes));
      for (final row in document.findAllElements('row')) {
        final values = <String>[];
        for (final cell in row.findAllElements('c')) {
          final type = cell.getAttribute('t');
          final valueNode = cell.findElements('v').firstOrNull;
          var value = valueNode?.innerText ?? '';
          if (type == 's') {
            final index = int.tryParse(value);
            value = index != null && index >= 0 && index < sharedStrings.length
                ? sharedStrings[index]
                : '';
          } else if (type == 'inlineStr') {
            value = cell.findAllElements('t').map((node) => node.innerText).join();
          }
          if (value.isNotEmpty) values.add(value);
        }
        if (values.isNotEmpty) rows.add(values.join('\t'));
      }
    }
    if (rows.isEmpty) {
      throw const FormatException('Tidak ada teks yang dapat dibaca di lembar Excel.');
    }
    return rows.join('\n');
  }

  static int _fileNumber(String path) {
    final match = RegExp(r'(\d+)\.xml$').firstMatch(path);
    return int.tryParse(match?.group(1) ?? '') ?? 0;
  }

  static Future<String> _extractPdf(String fileName, Uint8List bytes) async {
    final extraction = await extractPdf(fileName: fileName, bytes: bytes);
    return extraction.text;
  }

  /// Parses every page of the PDF and keeps the per-page text, so later reads
  /// can slice it by page (mirrors _extract_pdf_text in
  /// src/mcp/tools/documents/service.py, which re-parses the file on disk).
  static Future<PdfExtraction> extractPdf({
    required String fileName,
    required Uint8List bytes,
  }) async {
    await pdfrxFlutterInitialize();
    final document = await PdfDocument.openData(bytes, sourceName: fileName);
    try {
      final pageTexts = await Future.wait(
        document.pages.map((page) async => (await page.loadText())?.fullText ?? ''),
      );
      if (pageTexts.every((page) => page.trim().isEmpty)) {
        throw const FormatException(
          'PDF tidak memiliki teks yang dapat diekstrak. PDF hasil scan memerlukan OCR.',
        );
      }
      return PdfExtraction(pageTexts);
    } finally {
      await document.dispose();
    }
  }

  /// Slices a PDF read the same way _extract_pdf_text does: at most
  /// [pdfPagesPerRead] pages starting at [pageStart] (1-based), stopping once
  /// [pdfCharsPerRead] characters have been spent, and ending with a hint the
  /// LLM can follow to read the rest.
  static String slicePdf({
    required PdfExtraction extraction,
    required int pageStart,
    int pageEnd = 0,
  }) {
    final pageCount = extraction.pageCount;
    final start = pageStart < 1 ? 1 : pageStart;
    if (pageCount == 0) return 'The PDF file has no pages.';
    if (start > pageCount) {
      return 'PDF page_start $start exceeds the document\'s $pageCount pages.';
    }
    if (pageEnd != 0 && pageEnd < start) {
      return 'PDF page_end must be greater than or equal to page_start.';
    }
    final lastPage = pageCount < pageEnd ? pageCount : pageEnd;
    final unclampedEnd = lastPage == 0
        ? start + pdfPagesPerRead - 1
        : (lastPage < start + pdfPagesPerRead - 1
            ? lastPage
            : start + pdfPagesPerRead - 1);
    // Never read past the last page when page_end was left at its default.
    final end = pageCount < unclampedEnd ? pageCount : unclampedEnd;
    final parts = <String>[];
    var charsUsed = 0;
    var textTruncated = false;
    var lastPageRead = start - 1;
    for (var pageNumber = start; pageNumber <= end; pageNumber++) {
      if (charsUsed >= pdfCharsPerRead) {
        textTruncated = true;
        break;
      }
      final text = extraction.pages[pageNumber - 1].trim();
      lastPageRead = pageNumber;
      if (text.isEmpty) continue;
      final separator = parts.isEmpty ? '' : '\n\n';
      final remaining = pdfCharsPerRead - charsUsed - separator.length;
      if (text.length > remaining) {
        if (remaining > 0) {
          parts.add('$separator${text.substring(0, remaining)}');
          charsUsed += separator.length + remaining;
        }
        textTruncated = true;
        break;
      }
      parts.add('$separator$text');
      charsUsed += separator.length + text.length;
    }
    if (parts.isEmpty) {
      // The requested pages are blank. Still report progress so the LLM can
      // continue instead of concluding the document has no text at all.
      final nextStart = end + 1;
      return pageCount > end
          ? 'Pages $start-$end of $pageCount contain no extractable text. '
              'Continue with page_start=$nextStart, '
              'page_end=${nextStart + pdfPagesPerRead - 1}.'
          : 'Pages $start-$end of $pageCount contain no extractable text.';
    }
    final notices = <String>[];
    if (textTruncated) {
      notices.add('Text truncated at $pdfCharsPerRead characters.');
    }
    if (lastPageRead < pageCount) {
      final nextStart = lastPageRead + 1;
      final nextEnd = pageCount < nextStart + pdfPagesPerRead - 1
          ? pageCount
          : nextStart + pdfPagesPerRead - 1;
      notices.add('Read pages $start-$lastPageRead of $pageCount. '
          'Continue with page_start=$nextStart, page_end=$nextEnd.');
    } else {
      notices.add('Read pages $start-$lastPageRead of $pageCount.');
    }
    return '${parts.join()}\n\n[${notices.join(' ')}]';
  }

  /// Pages a non-PDF document in [documentCharsPerRead] chunks, mirroring
  /// _chunk_extracted_text / _format_document_chunk.
  static String sliceDocument({required String text, required int chunkIndex}) {
    if (chunkIndex < 1) return 'chunk_index must be a positive integer.';
    final totalChunks =
        (text.length + documentCharsPerRead - 1) ~/ documentCharsPerRead;
    if (totalChunks == 0) return '[Read chunk $chunkIndex; end of document.]';
    if (chunkIndex > totalChunks) {
      return 'Chunk $chunkIndex exceeds the document\'s $totalChunks chunks.';
    }
    final start = (chunkIndex - 1) * documentCharsPerRead;
    final end = start + documentCharsPerRead;
    final chunk = end < text.length ? text.substring(start, end) : text.substring(start);
    final notice = chunkIndex < totalChunks
        ? 'Read chunk $chunkIndex. Continue with chunk_index=${chunkIndex + 1}.'
        : 'Read chunk $chunkIndex; end of document.';
    return '$chunk\n\n[$notice]';
  }
}