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
}