import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/avatar_gesture.dart';

void main() {
  group('AvatarGesture.normalize', () {
    test('accepts canonical names', () {
      for (final name in AvatarGesture.names) {
        expect(AvatarGesture.normalize(name), name);
      }
    });

    test('maps Indonesian names to canonical names', () {
      expect(AvatarGesture.normalize('lambaian'), 'wave');
      expect(AvatarGesture.normalize('angguk'), 'nod');
      expect(AvatarGesture.normalize('geleng'), 'shake');
      expect(AvatarGesture.normalize('membungkuk'), 'bow');
      expect(AvatarGesture.normalize('senang'), 'happy');
      expect(AvatarGesture.normalize('sedih'), 'sad');
      expect(AvatarGesture.normalize('terkejut'), 'surprised');
      expect(AvatarGesture.normalize('marah'), 'angry');
      expect(AvatarGesture.normalize('berpikir'), 'think');
      expect(AvatarGesture.normalize('lihat_kiri'), 'look_left');
      expect(AvatarGesture.normalize('lihat_kanan'), 'look_right');
      expect(AvatarGesture.normalize('lihat_atas'), 'look_up');
      expect(AvatarGesture.normalize('lihat_bawah'), 'look_down');
      expect(AvatarGesture.normalize('menari'), 'dance');
    });

    test('is case-insensitive and accepts spaces and dashes', () {
      expect(AvatarGesture.normalize('Lambaian'), 'wave');
      expect(AvatarGesture.normalize('WAVE'), 'wave');
      expect(AvatarGesture.normalize('look left'), 'look_left');
      expect(AvatarGesture.normalize('look-left'), 'look_left');
      expect(AvatarGesture.normalize('  angguk  '), 'nod');
    });

    test('returns null for unknown names and empty input', () {
      expect(AvatarGesture.normalize('terbang'), isNull);
      expect(AvatarGesture.normalize(''), isNull);
      expect(AvatarGesture.normalize('   '), isNull);
    });
  });

  group('AvatarGesture.names', () {
    test('covers the 14 gestures the 3D page implements', () {
      expect(AvatarGesture.names, hasLength(14));
      expect(AvatarGesture.names, containsAll(['wave', 'nod', 'dance']));
    });
  });
}
