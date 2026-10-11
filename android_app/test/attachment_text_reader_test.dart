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
}