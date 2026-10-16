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

  /// Maximum number of search matches returned by [searchPdf]. Mirrors the
  /// practical cap used by pdf-mcp's pdf_search (default 10, max 100).
  static const pdfSearchMaxResults = 10;

  /// Default context window (chars) around each search hit. Mirrors
  /// pdf-mcp's pdf_search context_chars default.
  static const pdfSearchContextChars = 200;

  /// Regex matching a numbered PDF entry heading like "7. Some Title" or
  /// "007. Some Title". Mirrors _PDF_ENTRY_HEADING_RE in
  /// src/mcp/tools/documents/service.py.
  static final _pdfEntryHeadingRe = RegExp(
    r'^\s*0*(\d{1,3})\.\s+[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ0-9 ,.\'()\-]*',
    multiLine: true,
  );

  /// Regex matching a natural-language entry-number query like "tokoh 7",
  /// "entry 12", "nomor 3", "no. 5". Mirrors _parse_entry_number_query in
  /// src/mcp/tools/documents/service.py.
  static final _entryNumberQueryRe = RegExp(
    r'\b(?:tokoh|entry|nomor|no\.?)\s*(?:(?:ke|nomor|no\.?)\s*[.-]?\s*)?(\d{1,3})\b',
    caseSensitive: false,
  );

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

  /// Parses a natural-language query for a numbered PDF entry. Returns the
  /// entry number when the query mentions "tokoh 7", "entry 12", "nomor 3",
  /// "no. 5", etc.; otherwise null. Mirrors _parse_entry_number_query in
  /// src/mcp/tools/documents/service.py.
  static int? parseEntryNumberQuery(Object? query) {
    final text = query?.toString() ?? '';
    final match = _entryNumberQueryRe.firstMatch(text);
    if (match == null) return null;
    return int.tryParse(match.group(1) ?? '');
  }

  /// Searches the PDF for [query] (case-insensitive substring match) and
  /// returns up to [maxResults] matches with page numbers and excerpts.
  ///
  /// Mirrors pdf-mcp's pdf_search keyword mode: each match carries the page
  /// number, a context window of [contextChars] characters around the hit,
  /// and the position of the hit within the page. The response ends with a
  /// continuation hint so the LLM can call [slicePdf] to read the matching
  /// pages in full.
  static String searchPdf({
    required PdfExtraction extraction,
    required String query,
    int maxResults = pdfSearchMaxResults,
    int contextChars = pdfSearchContextChars,
  }) {
    final pageCount = extraction.pageCount;
    if (pageCount == 0) return 'The PDF file has no pages.';
    final trimmed = query.trim();
    if (trimmed.isEmpty) return 'Search query cannot be empty.';
    final limit = maxResults < 1 ? 1 : maxResults;
    final window = contextChars < 10 ? 10 : contextChars;
    final needle = trimmed.toLowerCase();

    final matches = <String>[];
    var totalHits = 0;
    final pageCounts = <int, int>{};
    for (var pageNumber = 1; pageNumber <= pageCount; pageNumber++) {
      final pageText = extraction.pages[pageNumber - 1];
      if (pageText.isEmpty) continue;
      final lower = pageText.toLowerCase();
      var position = 0;
      while (true) {
        final hit = lower.indexOf(needle, position);
        if (hit < 0) break;
        totalHits++;
        pageCounts[pageNumber] = (pageCounts[pageNumber] ?? 0) + 1;
        if (matches.length < limit) {
          final start = hit < window ? 0 : hit - window;
          final end = (hit + needle.length + window) > pageText.length
              ? pageText.length
              : hit + needle.length + window;
          final excerpt = pageText.substring(start, end).trim();
          matches.add(
            'Page $pageNumber (hit ${pageCounts[pageNumber]}): '
            '${_truncateExcerpt(excerpt, window * 4)}',
          );
        }
        position = hit + needle.length;
      }
    }

    if (matches.isEmpty) {
      return 'No matches for "$trimmed" in $pageCount pages. '
          'Try a shorter or more specific query, or use page_start/page_end '
          'to read a known range.';
    }

    final pagesWithHits = pageCounts.keys.toList()..sort();
    final firstPage = pagesWithHits.first;
    final lastPage = pagesWithHits.last;
    final notices = <String>[
      'Found $totalHits match(es) for "$trimmed" across '
      '${pageCounts.length} page(s).',
      'Showing ${matches.length} of $totalHits. '
      'Read pages $firstPage-$lastPage with page_start=$firstPage, '
      'page_end=$lastPage to see the full content.',
    ];
    return '${matches.join('\n\n')}\n\n[${notices.join(' ')}]';
  }

  /// Extracts a numbered PDF entry (e.g. "7. Some Title") starting at
  /// [pageStart] (1-based). The entry runs from its heading up to the next
  /// numbered heading or until [pdfPagesPerRead] pages have been read.
  ///
  /// Mirrors _extract_pdf_entry in src/mcp/tools/documents/service.py:
  /// - [entryContinue] skips the heading search and resumes from [pageStart]
  ///   at [entryCharOffset] characters into the previously extracted section.
  /// - The response ends with a continuation hint so the LLM can keep reading
  ///   the entry across more pages or pick up where the char window left off.
  static String extractPdfEntry({
    required PdfExtraction extraction,
    required int entryNumber,
    int pageStart = 1,
    bool entryContinue = false,
    int entryCharOffset = 0,
  }) {
    final pageCount = extraction.pageCount;
    if (pageCount == 0) return 'The PDF file has no pages.';
    if (entryNumber < 1) return 'entry_number must be a positive integer.';
    final start = pageStart < 1 ? 1 : pageStart;
    if (start > pageCount) {
      return 'page_start $start exceeds the document\'s $pageCount pages.';
    }

    var entryPage = 0;
    var targetStart = 0;
    if (entryContinue) {
      entryPage = start;
    } else {
      final searchEnd =
          pageCount < start + pdfPagesPerRead - 1 ? pageCount : start + pdfPagesPerRead - 1;
      for (var pageNumber = start; pageNumber <= searchEnd; pageNumber++) {
        final pageText = extraction.pages[pageNumber - 1];
        for (final match in _pdfEntryHeadingRe.allMatches(pageText)) {
          final number = int.tryParse(match.group(1) ?? '');
          if (number == entryNumber) {
            entryPage = pageNumber;
            targetStart = match.start;
            break;
          }
        }
        if (entryPage != 0) break;
      }
      if (entryPage == 0) {
        if (searchEnd < pageCount) {
          final nextEnd = pageCount < searchEnd + pdfPagesPerRead
              ? pageCount
              : searchEnd + pdfPagesPerRead;
          return 'Entry $entryNumber was not found on pages $start-$searchEnd. '
              'Continue searching with page_start=${searchEnd + 1}, '
              'page_end=$nextEnd, entry_number=$entryNumber.';
        }
        return 'Entry $entryNumber was not found in pages $start-$searchEnd. '
            'No other section text was returned.';
      }
    }

    final sectionParts = <String>[];
    var sectionChars = 0;
    var nextEntryFound = false;
    var lastPageRead = entryPage - 1;
    final lastPage = pageCount < entryPage + pdfPagesPerRead - 1
        ? pageCount
        : entryPage + pdfPagesPerRead - 1;
    for (var pageNumber = entryPage; pageNumber <= lastPage; pageNumber++) {
      final pageText = extraction.pages[pageNumber - 1];
      final startAt = pageNumber == entryPage ? targetStart : 0;
      var endAt = pageText.length;
      for (final match in _pdfEntryHeadingRe.allMatches(pageText, startAt)) {
        final number = int.tryParse(match.group(1) ?? '');
        if (number != null && number > entryNumber) {
          endAt = match.start;
          nextEntryFound = true;
          break;
        }
      }
      final pagePart = pageText.substring(startAt, endAt).trim();
      if (pagePart.isNotEmpty) {
        final separator = sectionParts.isEmpty ? 0 : 2;
        sectionParts.add(pagePart);
        sectionChars += pagePart.length + separator;
      }
      lastPageRead = pageNumber;
      if (nextEntryFound) break;
    }

    final section = sectionParts.join('\n\n');
    if (entryCharOffset > 0 && entryCharOffset >= section.length) {
      return 'entry_char_offset $entryCharOffset exceeds the '
          '${section.length} characters extracted for entry $entryNumber.';
    }
    final text = section.substring(
      entryCharOffset,
      entryCharOffset + pdfCharsPerRead > section.length
          ? section.length
          : entryCharOffset + pdfCharsPerRead,
    );

    final notices = <String>[
      'Verbatim source text for entry $entryNumber, '
          'pages $entryPage-$lastPageRead.',
    ];
    final charMore = entryCharOffset + text.length < section.length;
    final pageMore = !nextEntryFound && lastPageRead < pageCount;
    if (charMore) {
      notices.add(
        'Continue this entry with entry_number=$entryNumber, '
        'page_start=$entryPage, entry_continue=true, '
        'entry_char_offset=${entryCharOffset + text.length}.',
      );
    } else if (pageMore) {
      final nextStart = lastPageRead + 1;
      final nextEnd = pageCount < nextStart + pdfPagesPerRead - 1
          ? pageCount
          : nextStart + pdfPagesPerRead - 1;
      notices.add(
        'Entry $entryNumber continues. Continue with '
        'entry_number=$entryNumber, page_start=$nextStart, '
        'page_end=$nextEnd, entry_continue=true.',
      );
    } else if (nextEntryFound) {
      notices.add('Entry $entryNumber ends before the next numbered entry.');
    } else {
      notices.add('End of document.');
    }
    return '$text\n\n[${notices.join(' ')}]';
  }

  /// Trims an excerpt to at most [maxChars] characters, breaking on a word
  /// boundary when possible and adding an ellipsis when truncated.
  static String _truncateExcerpt(String text, int maxChars) {
    if (text.length <= maxChars) return text;
    var cut = maxChars;
    while (cut > 0 && text[cut] != ' ') {
      cut--;
    }
    if (cut < maxChars ~/ 2) cut = maxChars;
    return '${text.substring(0, cut).trimRight()}…';
  }
}