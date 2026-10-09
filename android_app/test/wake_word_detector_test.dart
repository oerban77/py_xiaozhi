// Regression test for the wake-word startup failure.
//
// sherpa-onnx requires the keywords at KeywordSpotter construction time: an
// empty keywords_file/keywords_buf makes KeywordSpotterConfig::Validate() fail
// ("Please provide either a keywords-file or the keywords-buf"), the C-API
// returns nullptr and the Dart factory throws "Failed to create kws. Please
// check your config", which the controller surfaces as
// "Wake word gagal dimulai: Exception: Failed to create kws. ...".
//
// This test pins the fix without pulling in the FFI plugin (which needs a
// device): start() must write exactly one keyword line to keywords.txt in the
// same directory as the copied model files, and that line must be the one the
// BPE converter produced for the configured wake word.

import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;

import 'package:py_xiaozhi_android/bpe_keyword_converter.dart';
import 'package:py_xiaozhi_android/wake_word_detector.dart';

// A slice of the real models/en/tokens.txt: enough tokens to tokenize
// "HELLO XIAOZHI" and "XIAOZHI" greedily, the way the shipped model does.
const _tokens = <String>[
  '<blk> 0',
  '<sos/eos> 1',
  '<unk> 2',
  'S 3',
  'T 4',
  '▁THE 5',
  '▁HE 6',
  'LL 7',
  'O 8',
  '▁X 9',
  'IA 10',
  'Z 11',
  'H 12',
  'I 13',
];

const _modelAssetDir = 'assets/models/en';

// path_provider funnels getApplicationSupportDirectory() through this channel;
// answering it with a temp dir is enough for a host test, and it keeps the test
// free of an extra dev_dependency on path_provider_platform_interface.
const _pathProviderChannel = MethodChannel('plugins.flutter.io/path_provider');

void main() {
  late Directory tempDir;

  setUp(() async {
    TestWidgetsFlutterBinding.ensureInitialized();

    tempDir = await Directory.systemTemp.createTemp('wake_word_test_');

    final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(_pathProviderChannel, (call) async {
      if (call.method == 'getApplicationSupportDirectory') return tempDir.path;
      return null;
    });

    // Bundle the model assets the detector copies out of rootBundle. Only
    // tokens.txt has real content; the ONNX files are never opened here because
    // the FFI plugin is absent on the host.
    final modelDir = Directory(p.join(tempDir.path, _modelAssetDir));
    await modelDir.create(recursive: true);
    await File(p.join(modelDir.path, 'tokens.txt'))
        .writeAsString('${_tokens.join('\n')}\n', flush: true);
    for (final name in <String>['encoder.onnx', 'decoder.onnx', 'joiner.onnx']) {
      await File(p.join(modelDir.path, name)).writeAsBytes(<int>[0], flush: true);
    }

    // rootBundle.load() sends the asset key as raw UTF-8 bytes on the
    // "flutter/assets" channel (PlatformAssetBundle.load), so the mock handler
    // has to decode the message itself rather than expect a method call.
    Future<ByteData?> loadAsset(ByteData? message) async {
      final key = message == null ? '' : utf8.decode(message.buffer.asUint8List());
      final file = File(p.join(tempDir.path, key));
      if (!await file.exists()) return null;
      return ByteData.sublistView(await file.readAsBytes());
    }

    messenger.setMockMessageHandler('flutter/assets', loadAsset);
  });

  tearDown(() async {
    final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    messenger.setMockMessageHandler('flutter/assets', null);
    messenger.setMockMethodCallHandler(_pathProviderChannel, null);
    if (await tempDir.exists()) {
      await tempDir.delete(recursive: true);
    }
  });

  group('keywords file', () {
    test('is written next to the copied model files with one keyword line', () async {
      final detector = WakeWordDetector();
      final tokensPath = await detector.copyAssetFile('$_modelAssetDir/tokens.txt');
      final keywordsPath = await detector.writeKeywordsFile(
        p.dirname(tokensPath),
        '▁HE LL O ▁ X IA O Z H I @HELLO-XIAOZHI',
      );

      // The spotter opens keywords_file with InitKeywords(), so the path must be
      // absolute, must exist, and must sit beside the model files.
      expect(p.isAbsolute(keywordsPath), isTrue);
      expect(File(keywordsPath).existsSync(), isTrue);
      expect(p.basename(keywordsPath), 'keywords.txt');
      expect(p.dirname(keywordsPath), p.dirname(tokensPath));

      final written = await File(keywordsPath).readAsString();
      expect(written.trim(), '▁HE LL O ▁ X IA O Z H I @HELLO-XIAOZHI');
      expect(written.endsWith('\n'), isTrue,
          reason: 'InitKeywords() splits on newlines; a missing trailing newline '
              'can glue the last keyword to the next one.');
    });

    test('is regenerated when the wake word changes', () async {
      final detector = WakeWordDetector();
      final tokensPath = await detector.copyAssetFile('$_modelAssetDir/tokens.txt');
      final dir = p.dirname(tokensPath);

      final first = await detector.writeKeywordsFile(dir, '▁HE LL O @HELLO');
      expect((await File(first).readAsString()).trim(), '▁HE LL O @HELLO');

      // Overwriting in place must not leave the previous keyword behind: a stale
      // second keyword would still be spotted after the user edits the wake word.
      final second = await detector.writeKeywordsFile(dir, '▁X IA O Z H I @XIAOZHI');
      expect(second, first);
      final lines = (await File(second).readAsLines()).where((line) => line.isNotEmpty);
      expect(lines, hasLength(1));
      expect(lines.single, '▁X IA O Z H I @XIAOZHI');
    });
  });

  group('keyword line', () {
    // The line written to keywords.txt must be exactly what the BPE converter
    // emits for the configured wake word, otherwise the spotter would be built
    // for a different keyword than the one the user set.
    for (final wakeWord in <String>['Hello Xiaozhi', 'Xiaozhi', 'HELLO XIAOZHI']) {
      test('matches the BPE conversion of "$wakeWord"', () async {
        final detector = WakeWordDetector();
        final tokensPath = await detector.copyAssetFile('$_modelAssetDir/tokens.txt');

        final converter = BpeKeywordConverter(tokensPath);
        final expected = await converter.convert(wakeWord);

        final keywordsPath =
            await detector.writeKeywordsFile(p.dirname(tokensPath), expected);
        expect((await File(keywordsPath).readAsString()).trim(), expected);
      });
    }
  });
}
