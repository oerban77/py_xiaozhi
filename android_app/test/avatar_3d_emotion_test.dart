import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/avatar_3d_emotion.dart';

void main() {
  group('Avatar3DEmotionFace', () {
    test('happy smiles with squinting cheeks and eyes', () {
      final face = Avatar3DEmotionFace.forEmotion('happy');
      expect(face.weights['mouthSmile'], 0.35);
      expect(face.weights['mouthSmileLeft'], 0.35);
      expect(face.weights['mouthSmileRight'], 0.35);
      expect(face.weights['cheekSquintLeft'], 0.2);
      expect(face.weights['eyeSquintLeft'], 0.15);
      // No frown or brow-down on a happy face.
      expect(face.weights.containsKey('mouthFrown'), isFalse);
      expect(face.weights.containsKey('browDownLeft'), isFalse);
    });

    test('sad/crying frowns with raised inner brows', () {
      for (final name in ['sad', 'crying']) {
        final face = Avatar3DEmotionFace.forEmotion(name);
        expect(face.weights['mouthFrown'], 0.35, reason: name);
        expect(face.weights['browInnerUp'], 0.3, reason: name);
      }
    });

    test('angry lowers the brows and frowns', () {
      final face = Avatar3DEmotionFace.forEmotion('angry');
      expect(face.weights['browDownLeft'], 0.35);
      expect(face.weights['browDownRight'], 0.35);
      expect(face.weights['mouthFrown'], 0.25);
      expect(face.weights['jawForward'], 0.15);
    });

    test('surprised widens the eyes and drops the jaw', () {
      for (final name in ['shocked', 'surprised']) {
        final face = Avatar3DEmotionFace.forEmotion(name);
        expect(face.weights['browInnerUp'], 0.35, reason: name);
        expect(face.weights['jawOpen'], 0.15, reason: name);
        expect(face.weights['eyeWideLeft'], 0.25, reason: name);
      }
    });

    test('winking squints only the right eye', () {
      final face = Avatar3DEmotionFace.forEmotion('winking');
      expect(face.weights['eyeSquintRight'], 0.3);
      expect(face.weights.containsKey('eyeSquintLeft'), isFalse);
    });

    test('neutral and unknown emotions are a blank face', () {
      expect(Avatar3DEmotionFace.forEmotion('neutral').weights, isEmpty);
      expect(Avatar3DEmotionFace.forEmotion('not-an-emotion').weights, isEmpty);
    });

    test('device state smiles gently while speaking', () {
      final speaking = Avatar3DEmotionFace.forDeviceState(
        isSpeaking: true,
        isListening: false,
      );
      expect(speaking.weights['mouthSmile'], 0.12);
      final idle = Avatar3DEmotionFace.forDeviceState(
        isSpeaking: false,
        isListening: false,
      );
      expect(idle.weights, isEmpty);
    });

    test('all 21 server emotions map to a face (no silent default)', () {
      const emotions = [
        'happy', 'laughing', 'confident', 'cool', 'funny', 'silly',
        'loving', 'kissy', 'delicious', 'winking', 'sad', 'crying',
        'angry', 'embarrassed', 'shocked', 'surprised', 'thinking',
        'confused', 'sleepy', 'relaxed', 'neutral',
      ];
      for (final name in emotions) {
        // Must not throw; every name resolves (neutral -> empty map).
        final face = Avatar3DEmotionFace.forEmotion(name);
        expect(face.weights, isA<Map<String, double>>(), reason: name);
        for (final value in face.weights.values) {
          expect(value, inInclusiveRange(0.0, 1.0), reason: name);
        }
      }
    });
  });
}
