import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/live2d_emotion.dart';

void main() {
  test('happy maps to a full smile with open eyes', () {
    final face = Live2DEmotionFace.forEmotion('happy');
    expect(face.mouthForm, 1.0);
    expect(face.eyeOpen, 1.0);
    expect(face.cheek, 0.3);
  });

  test('sad maps to a frown', () {
    final face = Live2DEmotionFace.forEmotion('sad');
    expect(face.mouthForm, lessThan(0));
    expect(face.eyeOpen, lessThan(1.0));
  });

  test('sleepy narrows the eyes', () {
    final face = Live2DEmotionFace.forEmotion('sleepy');
    expect(face.eyeOpen, lessThan(0.5));
  });

  test('unknown emotion falls back to a calm face', () {
    final face = Live2DEmotionFace.forEmotion('not-an-emotion');
    expect(face.mouthForm, 0.1);
    expect(face.eyeOpen, 1.0);
    expect(face.cheek, 0.0);
  });

  test('speaking without a server emotion smiles gently', () {
    final face = Live2DEmotionFace.forDeviceState(
      isSpeaking: true,
      isListening: false,
    );
    expect(face.mouthForm, 0.45);
    expect(face.eyeOpen, 1.0);
  });

  test('listening without a server emotion looks attentive', () {
    final face = Live2DEmotionFace.forDeviceState(
      isSpeaking: false,
      isListening: true,
    );
    expect(face.mouthForm, 0.25);
    expect(face.eyeOpen, 1.0);
  });

  test('idle without a server emotion stays neutral', () {
    final face = Live2DEmotionFace.forDeviceState(
      isSpeaking: false,
      isListening: false,
    );
    expect(face.mouthForm, 0.1);
    expect(face.eyeOpen, 1.0);
  });

  test('relaxed maps to a soft smile', () {
    final face = Live2DEmotionFace.forEmotion('relaxed');
    expect(face.mouthForm, 0.3);
    expect(face.eyeOpen, 1.0);
  });

  test('neutral maps to the calm face', () {
    final face = Live2DEmotionFace.forEmotion('neutral');
    expect(face.mouthForm, 0.1);
    expect(face.eyeOpen, 1.0);
    expect(face.cheek, 0.0);
  });

  test('emotion names are case-insensitive', () {
    expect(
      Live2DEmotionFace.forEmotion('HAPPY').mouthForm,
      Live2DEmotionFace.forEmotion('happy').mouthForm,
    );
  });
}
