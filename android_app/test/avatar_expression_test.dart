import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/avatar_expression.dart';

void main() {
  group('AvatarExpression.normalize', () {
    test('accepts canonical names', () {
      for (final name in AvatarExpression.names) {
        expect(AvatarExpression.normalize(name), name);
      }
    });

    test('maps Indonesian names to canonical names', () {
      expect(AvatarExpression.normalize('tertawa'), 'laughing');
      expect(AvatarExpression.normalize('ketawa'), 'laughing');
      expect(AvatarExpression.normalize('ngakak'), 'laughing');
      expect(AvatarExpression.normalize('lucu'), 'funny');
      expect(AvatarExpression.normalize('senang'), 'happy');
      expect(AvatarExpression.normalize('gembira'), 'happy');
      expect(AvatarExpression.normalize('sedih'), 'sad');
      expect(AvatarExpression.normalize('menangis'), 'crying');
      expect(AvatarExpression.normalize('marah'), 'angry');
      expect(AvatarExpression.normalize('terkejut'), 'surprised');
      expect(AvatarExpression.normalize('kaget'), 'surprised');
      expect(AvatarExpression.normalize('berpikir'), 'thinking');
      expect(AvatarExpression.normalize('bingung'), 'confused');
      expect(AvatarExpression.normalize('sayang'), 'loving');
      expect(AvatarExpression.normalize('cium'), 'kissy');
      expect(AvatarExpression.normalize('keren'), 'cool');
      expect(AvatarExpression.normalize('santai'), 'relaxed');
      expect(AvatarExpression.normalize('ngantuk'), 'sleepy');
      expect(AvatarExpression.normalize('konyol'), 'silly');
      expect(AvatarExpression.normalize('malu'), 'embarrassed');
      expect(AvatarExpression.normalize('enak'), 'delicious');
      expect(AvatarExpression.normalize('netral'), 'neutral');
      expect(AvatarExpression.normalize('kedip'), 'winking');
    });

    test('is case-insensitive and accepts spaces and underscores', () {
      expect(AvatarExpression.normalize('Tertawa'), 'laughing');
      expect(AvatarExpression.normalize('LAUGHING'), 'laughing');
      expect(AvatarExpression.normalize('tertawa keras'), 'laughing');
      expect(AvatarExpression.normalize('  senang  '), 'happy');
    });

    test('returns null for unknown names and empty input', () {
      expect(AvatarExpression.normalize('terbang'), isNull);
      expect(AvatarExpression.normalize(''), isNull);
      expect(AvatarExpression.normalize('   '), isNull);
    });
  });

  group('AvatarExpression.names', () {
    test('covers the 21 bundled emotions', () {
      expect(AvatarExpression.names, hasLength(21));
      expect(AvatarExpression.names,
          containsAll(['laughing', 'happy', 'sad', 'angry', 'surprised']));
    });
  });
}
