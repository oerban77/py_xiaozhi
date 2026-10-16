import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/attachment_text_reader.dart';

void main() {
  test('reads a UTF-8 text attachment', () async {
    final text = await AttachmentTextReader.extract(
      fileName: 'notes.txt',
      bytes: Uint8List.fromList(utf8.encode('Baris pertama\nBaris kedua')),
    );

    expect(text, 'Baris pertama\nBaris kedua');
  });

  test('extracts paragraphs from a DOCX attachment', () async {
    final archive = Archive()
      ..addFile(ArchiveFile.string(
        'word/document.xml',
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Halo</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Dunia</w:t></w:r></w:p></w:body></w:document>',
      ));
    final zipped = ZipEncoder().encode(archive);

    final text = await AttachmentTextReader.extract(
      fileName: 'notes.docx',
      bytes: Uint8List.fromList(zipped),
    );

    expect(text, 'Halo\nDunia');
  });

  test('extracts slide text from a PPTX attachment', () async {
    final archive = Archive()
      ..addFile(ArchiveFile.string(
        'ppt/slides/slide1.xml',
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:t>Presentasi</a:t><a:t>pertama</a:t></p:sld>',
      ));
    final zipped = ZipEncoder().encode(archive);

    final text = await AttachmentTextReader.extract(
      fileName: 'slides.pptx',
      bytes: Uint8List.fromList(zipped),
    );

    expect(text, 'Presentasi pertama');
  });

  test('extracts shared strings from an XLSX attachment', () async {
    final archive = Archive()
      ..addFile(ArchiveFile.string(
        'xl/sharedStrings.xml',
        '<sst><si><t>Nama</t></si><si><t>Rina</t></si></sst>',
      ))
      ..addFile(ArchiveFile.string(
        'xl/worksheets/sheet1.xml',
        '<worksheet><sheetData><row><c t="s"><v>0</v></c>'
            '<c t="s"><v>1</v></c></row></sheetData></worksheet>',
      ));
    final zipped = ZipEncoder().encode(archive);

    final text = await AttachmentTextReader.extract(
      fileName: 'table.xlsx',
      bytes: Uint8List.fromList(zipped),
    );

    expect(text, 'Nama\tRina');
  });

  test('explains that legacy DOC files must be converted', () async {
    await expectLater(
      AttachmentTextReader.extract(
        fileName: 'legacy.doc',
        bytes: Uint8List.fromList(<int>[0, 1, 2]),
      ),
      throwsA(isA<FormatException>()),
    );
  });

  test('slices a multi-page PDF into page chunks with a continuation hint', () {
    final extraction = PdfExtraction(List<String>.generate(10, (index) => 'Halaman ${index + 1}'));

    final first = AttachmentTextReader.slicePdf(extraction: extraction, pageStart: 1);
    expect(first, contains('Halaman 1'));
    expect(first, contains('Halaman 4'));
    expect(first, isNot(contains('Halaman 5')));
    expect(first, contains('Read pages 1-4 of 10.'));
    expect(first, contains('Continue with page_start=5, page_end=8.'));
  });

  test('jumps to a requested PDF page', () {
    final extraction = PdfExtraction(List<String>.generate(10, (index) => 'Halaman ${index + 1}'));

    final jump = AttachmentTextReader.slicePdf(
      extraction: extraction,
      pageStart: 7,
      pageEnd: 7,
    );
    expect(jump, contains('Halaman 7'));
    expect(jump, isNot(contains('Halaman 6')));
    expect(jump, isNot(contains('Halaman 8')));
    expect(jump, contains('Read pages 7-7 of 10.'));
    expect(jump, contains('Continue with page_start=8, page_end=10.'));
  });

  test('reaches the last PDF page and reports the end', () {
    final extraction = PdfExtraction(List<String>.generate(6, (index) => 'Halaman ${index + 1}'));

    final last = AttachmentTextReader.slicePdf(extraction: extraction, pageStart: 5);
    expect(last, contains('Halaman 5'));
    expect(last, contains('Halaman 6'));
    expect(last, contains('Read pages 5-6 of 6.'));
    expect(last, isNot(contains('Continue with')));
  });

  test('rejects a PDF page_start beyond the last page', () {
    const extraction = PdfExtraction(<String>['Halaman 1']);

    final error = AttachmentTextReader.slicePdf(extraction: extraction, pageStart: 9);
    expect(error, contains("exceeds the document's 1 pages"));
  });

  test('never reads past the last page when page_end is omitted', () {
    // 5 pages: a default read from page 4 must stop at 5, not at 7.
    final extraction =
        PdfExtraction(List<String>.generate(5, (index) => 'Halaman ${index + 1}'));

    final tail = AttachmentTextReader.slicePdf(extraction: extraction, pageStart: 4);
    expect(tail, contains('Halaman 4'));
    expect(tail, contains('Halaman 5'));
    expect(tail, contains('Read pages 4-5 of 5.'));
    expect(tail, isNot(contains('Continue with')));
  });

  test('skips blank pages and still reports progress', () {
    const extraction = PdfExtraction(<String>['', '', 'Halaman 3']);

    final blank = AttachmentTextReader.slicePdf(extraction: extraction, pageStart: 1);
    // The blank pages are skipped and the one page that has text is returned, so
    // the read covers the whole document and reports the end rather than claiming
    // there is nothing to read. Mirrors _extract_pdf_text, which only emits its
    // "no extractable text" branch when every requested page is blank.
    expect(blank, contains('Halaman 3'));
    expect(blank, contains('Read pages 1-3 of 3.'));
    expect(blank, isNot(contains('Continue with')));
  });

  test('reports no extractable text only when every requested page is blank', () {
    const extraction = PdfExtraction(<String>['', '', '']);

    final blank = AttachmentTextReader.slicePdf(extraction: extraction, pageStart: 1);
    expect(blank, contains('Pages 1-3 of 3 contain no extractable text.'));
    expect(blank, isNot(contains('Continue with')));
  });

  test('chunks a non-PDF document with a continuation hint', () {
    final text = List<String>.generate(
      3000,
      (index) => 'baris ${index + 1}',
    ).join('\n');

    final first = AttachmentTextReader.sliceDocument(text: text, chunkIndex: 1);
    expect(first, contains('baris 1'));
    expect(first, contains('Read chunk 1. Continue with chunk_index=2.'));

    // 3000 lines of ~10 chars is ~32k chars, and documentCharsPerRead is 24000,
    // so the document is exactly two chunks: the second read reaches the end.
    // Mirrors _chunk_extracted_text, whose has_more is chunk_index < total_chunks.
    final second = AttachmentTextReader.sliceDocument(text: text, chunkIndex: 2);
    expect(second, contains('baris 2284'));
    expect(second, contains('Read chunk 2; end of document.'));
    expect(second, isNot(contains('Continue with')));
  });

  group('parseEntryNumberQuery', () {
    test('parses "tokoh 7" from Indonesian', () {
      expect(AttachmentTextReader.parseEntryNumberQuery('baca tokoh 7'), 7);
    });

    test('parses "entry 12" and "nomor 3"', () {
      expect(AttachmentTextReader.parseEntryNumberQuery('show entry 12'), 12);
      expect(AttachmentTextReader.parseEntryNumberQuery('nomor 3'), 3);
    });

    test('parses "no. 5" and "no 5"', () {
      expect(AttachmentTextReader.parseEntryNumberQuery('no. 5'), 5);
      expect(AttachmentTextReader.parseEntryNumberQuery('no 5'), 5);
    });

    test('returns null when no entry number is mentioned', () {
      expect(AttachmentTextReader.parseEntryNumberQuery('hello world'), isNull);
      expect(AttachmentTextReader.parseEntryNumberQuery(''), isNull);
      expect(AttachmentTextReader.parseEntryNumberQuery(null), isNull);
    });
  });

  group('searchPdf', () {
    test('finds keyword matches across pages with excerpts', () {
      const extraction = PdfExtraction(<String>[
        'Halaman 1 membahas tentang sejarah Indonesia.',
        'Halaman 2 tentang ekonomi.',
        'Halaman 3 juga membahas sejarah Indonesia secara mendalam.',
      ]);

      final result = AttachmentTextReader.searchPdf(
        extraction: extraction,
        query: 'sejarah',
      );

      expect(result, contains('Page 1'));
      expect(result, contains('Page 3'));
      expect(result, contains('sejarah'));
      expect(result, contains('Found 2 match'));
    });

    test('returns a helpful message when nothing matches', () {
      const extraction = PdfExtraction(<String>['Halaman 1', 'Halaman 2']);

      final result = AttachmentTextReader.searchPdf(
        extraction: extraction,
        query: 'tidak-ada',
      );

      expect(result, contains('No matches'));
    });

    test('rejects an empty query', () {
      const extraction = PdfExtraction(<String>['Halaman 1']);

      final result = AttachmentTextReader.searchPdf(
        extraction: extraction,
        query: '   ',
      );

      expect(result, contains('cannot be empty'));
    });
  });

  group('searchPdfInPages', () {
    test('filters lines within the requested page range', () {
      const extraction = PdfExtraction(<String>[
        'Halaman 1 tentang ekonomi.',
        'Halaman 2 tentang sejarah Indonesia.',
        'Halaman 3 tentang sejarah dunia.',
        'Halaman 4 tentang ekonomi lagi.',
      ]);

      final result = AttachmentTextReader.searchPdfInPages(
        extraction: extraction,
        query: 'sejarah',
        pageStart: 2,
        pageEnd: 3,
      );

      expect(result, contains('Page 2:'));
      expect(result, contains('Page 3:'));
      expect(result, contains('sejarah Indonesia'));
      expect(result, contains('sejarah dunia'));
      expect(result, isNot(contains('Page 1')));
      expect(result, isNot(contains('Page 4')));
      expect(result, contains('Found 2 line'));
    });

    test('defaults page_end to pdfPagesPerRead pages from page_start', () {
      const extraction = PdfExtraction(<String>[
        'Halaman 1 tentang X.',
        'Halaman 2 tentang X.',
        'Halaman 3 tentang X.',
        'Halaman 4 tentang X.',
        'Halaman 5 tentang X.',
        'Halaman 6 tentang X.',
      ]);

      final result = AttachmentTextReader.searchPdfInPages(
        extraction: extraction,
        query: 'X',
        pageStart: 2,
      );

      // pdfPagesPerRead is 4, so pages 2-5 should be searched, not page 6.
      expect(result, contains('Page 2:'));
      expect(result, contains('Page 5:'));
      expect(result, isNot(contains('Page 6:')));
    });

    test('reports when no line matches in the requested range', () {
      const extraction = PdfExtraction(<String>[
        'Halaman 1 tentang ekonomi.',
        'Halaman 2 tentang ekonomi.',
      ]);

      final result = AttachmentTextReader.searchPdfInPages(
        extraction: extraction,
        query: 'sejarah',
        pageStart: 1,
        pageEnd: 2,
      );

      expect(result, contains('No line in pages 1-2 contains'));
    });

    test('rejects page_start beyond the document', () {
      const extraction = PdfExtraction(<String>['Halaman 1']);

      final result = AttachmentTextReader.searchPdfInPages(
        extraction: extraction,
        query: 'apa saja',
        pageStart: 99,
      );

      expect(result, contains('exceeds the document'));
    });

    test('caps the number of returned matching lines', () {
      const extraction = PdfExtraction(<String>[
        'baris satu tentang X\nbaris dua tentang X\n'
            'baris tiga tentang X\nbaris empat tentang X\n'
            'baris lima tentang X',
      ]);

      final result = AttachmentTextReader.searchPdfInPages(
        extraction: extraction,
        query: 'X',
        pageStart: 1,
        maxResults: 2,
      );

      // 5 lines match but only 2 are returned.
      expect(result, contains('showing 2 of 5'));
    });
  });

  group('extractPdfEntry', () {
    test('extracts a numbered entry up to the next heading', () {
      const extraction = PdfExtraction(<String>[
        '1. First Entry\nIsi entry pertama.',
        '2. Second Entry\nIsi entry kedua.',
        '3. Third Entry\nIsi entry ketiga.',
      ]);

      final result = AttachmentTextReader.extractPdfEntry(
        extraction: extraction,
        entryNumber: 2,
      );

      expect(result, contains('Second Entry'));
      expect(result, contains('Isi entry kedua.'));
      expect(result, isNot(contains('Third Entry')));
      expect(result, contains('Verbatim source text for entry 2'));
    });

    test('reports when the entry is not found in the search window', () {
      const extraction = PdfExtraction(<String>[
        '1. First Entry\nIsi entry pertama.',
        '2. Second Entry\nIsi entry kedua.',
      ]);

      final result = AttachmentTextReader.extractPdfEntry(
        extraction: extraction,
        entryNumber: 99,
      );

      expect(result, contains('was not found'));
    });

    test('rejects entry_number below 1', () {
      const extraction = PdfExtraction(<String>['1. First']);

      final result = AttachmentTextReader.extractPdfEntry(
        extraction: extraction,
        entryNumber: 0,
      );

      expect(result, contains('must be a positive integer'));
    });

    test('continues reading an entry across pages', () {
      const extraction = PdfExtraction(<String>[
        '5. Fifth Entry\nMulai di sini.',
        'Lanjutan entry kelima di halaman dua.',
        '6. Sixth Entry\nEntry keenam.',
      ]);

      final first = AttachmentTextReader.extractPdfEntry(
        extraction: extraction,
        entryNumber: 5,
      );

      expect(first, contains('Mulai di sini.'));
      expect(first, contains('Lanjutan entry kelima'));
      // Entry 5 ends before entry 6 (which starts on page 3), so the notice
      // indicates the entry boundary rather than offering a continuation.
      expect(first, contains('ends before the next numbered entry'));
    });
  });
}