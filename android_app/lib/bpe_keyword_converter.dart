// Port of src/audio_processing/keyword_converters/bpe_converter.py.
//
// sherpa-onnx keyword spotting expects a "keyword line": the BPE tokens of the
// phrase, space separated, followed by " @DISPLAY-NAME". The display name is what
// KeywordResult.keyword returns; sherpa-onnx splits each line on whitespace, so it
// must not contain spaces (the Python client comments this too: words after the
// space are parsed as BPE tokens and the process aborts via std::exit()).
//
// Example: "Hello Xiaozhi" -> "▁HE LL O ▁ X IA O Z H I @HELLO-XIAOZHI"

import 'dart:convert';
import 'dart:io';

/// Converts a user-typed English wake word into a sherpa-onnx keyword line.
class BpeKeywordConverter {
  BpeKeywordConverter(this.tokensPath);

  /// Absolute path to the copied tokens.txt (assets cannot be read directly).
  final String tokensPath;

  // The Python client caches the token table; do the same so validating a wake
  // word in the settings UI does not re-read the file on every keystroke.
  Map<String, int>? _tokenToId;

  /// Detected language code, matching KeywordConverter.language in Python.
  String get language => 'en';

  /// Loads tokens.txt. Each line is `<token> <id>`; the English model has no
  /// tokens containing spaces (verified), so splitting on whitespace is safe.
  Future<void> _ensureTokensLoaded() async {
    if (_tokenToId != null) return;
    final file = File(tokensPath);
    final lines = await file.readAsLines(encoding: utf8);
    final table = <String, int>{};
    for (final line in lines) {
      final trimmed = line.trim();
      if (trimmed.isEmpty) continue;
      final parts = trimmed.split(RegExp(r'\s+'));
      if (parts.length < 2) continue;
      final id = int.tryParse(parts.last);
      if (id != null) table[parts.first] = id;
    }
    _tokenToId = table;
  }

  /// True for Latin text without CJK characters, matching BpeConverter.can_convert.
  bool canConvert(String text) {
    final hasChinese = RegExp(r'[\u4e00-\u9fff]').hasMatch(text);
    final hasLetters = RegExp(r'[a-zA-Z]').hasMatch(text);
    return !hasChinese && hasLetters;
  }

  /// Greedy longest-match tokenization, mirroring BpeConverter._greedy_tokenize.
  /// The longest English token is 11 characters, so the Python cap of 20 is never
  /// the binding constraint; we keep a generous cap for safety.
  List<String> _greedyTokenize(String text, Map<String, int> tokens) {
    const maxLen = 20;
    final result = <String>[];
    var i = 0;
    while (i < text.length) {
      var matched = false;
      final remaining = text.length - i;
      final limit = remaining < maxLen ? remaining : maxLen;
      for (var length = limit; length > 0; length--) {
        final substr = text.substring(i, i + length);
        if (tokens.containsKey(substr)) {
          result.add(substr);
          i += length;
          matched = true;
          break;
        }
      }
      if (!matched) {
        final char = text[i];
        result.add(tokens.containsKey(char) ? char : '<unk>');
        i += 1;
      }
    }
    return result;
  }

  /// Converts [text] into a keyword line, or throws a [BpeConversionError] when
  /// the wake word is empty or contains syllables the model cannot represent
  /// (same error cases as BpeConverter.convert in Python).
  Future<String> convert(String text) async {
    await _ensureTokensLoaded();
    final tokens = _tokenToId!;

    // Keep only letters, collapse runs of separators to a single space, uppercase.
    final cleaned = (text).replaceAll(RegExp(r'[^A-Za-z]+'), ' ');
    final normalized = cleaned.replaceAll(RegExp(r'\s+'), ' ').trim().toUpperCase();
    if (normalized.isEmpty) {
      throw const BpeConversionError(
        'Kata wake word bahasa Inggris kosong setelah menghapus tanda baca dan spasi.',
      );
    }

    final words = normalized.split(' ');
    final allTokens = <String>[];
    for (final word in words) {
      final wordTokens = _greedyTokenize('▁$word', tokens);
      final unknown = wordTokens.where((token) => token == '<unk>').toSet().toList()
        ..sort();
      if (unknown.isNotEmpty) {
        throw BpeConversionError(
          'Kata wake word mengandung suku kata yang tidak dikenali model: '
          '"$word" (token tidak dikenal: $unknown). '
          'Gunakan kata bahasa Inggris umum, mis. "Hello Xiaozhi".',
        );
      }
      allTokens.addAll(wordTokens);
    }

    final bpeStr = allTokens.join(' ');
    // Non-alphanumerics become "-" so the display name stays a single whitespace
    // free word; sherpa-onnx would otherwise treat the tail as BPE tokens.
    final displayName = normalized.replaceAll(RegExp(r'[^A-Za-z0-9]+'), '-').replaceAll(RegExp(r'^-+|-+$'), '');
    return '$bpeStr @$displayName';
  }
}

/// Mirrors the ValueError raised by BpeConverter.convert.
class BpeConversionError implements Exception {
  const BpeConversionError(this.message);

  final String message;

  @override
  String toString() => message;
}
