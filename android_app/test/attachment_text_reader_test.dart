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