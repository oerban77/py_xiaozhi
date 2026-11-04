/// Pure emotion -> Live2D face-parameter mapping, kept separate from
/// [Live2DAvatar] so it can be unit-tested without a WebView.
///
/// Values are standard Cubism parameters: MouthForm in [-1, 1]
/// (-1 frown, +1 smile), EyeOpen in [0, 1], Cheek (blush) in [0, 1].
/// They exist on practically every Cubism model, unlike expression ids
/// which are model-specific.
/// Convenience for tests: the emotion -> face-parameter mapping, kept
/// pure so it can be checked without a WebView.
class Live2DEmotionFace {
  const Live2DEmotionFace({
    required this.mouthForm,
    required this.eyeOpen,
    required this.cheek,
  });

  final double mouthForm;
  final double eyeOpen;
  final double cheek;

  static Live2DEmotionFace forEmotion(String emotion) {
    switch (emotion.toLowerCase()) {
      case 'happy':
      case 'laughing':
      case 'confident':
      case 'cool':
      case 'funny':
      case 'silly':
        return const Live2DEmotionFace(mouthForm: 0.7, eyeOpen: 1.0, cheek: 0.0);
      case 'loving':
      case 'kissy':
      case 'delicious':
        return const Live2DEmotionFace(mouthForm: 0.6, eyeOpen: 1.0, cheek: 0.5);
      case 'winking':
        return const Live2DEmotionFace(mouthForm: 0.5, eyeOpen: 0.85, cheek: 0.0);
      case 'sad':
      case 'crying':
        return const Live2DEmotionFace(mouthForm: -0.6, eyeOpen: 0.9, cheek: 0.0);
      case 'angry':
        return const Live2DEmotionFace(mouthForm: -0.5, eyeOpen: 0.75, cheek: 0.0);
      case 'embarrassed':
        return const Live2DEmotionFace(mouthForm: -0.3, eyeOpen: 1.0, cheek: 0.4);
      case 'shocked':
      case 'surprised':
        return const Live2DEmotionFace(mouthForm: 0.2, eyeOpen: 1.0, cheek: 0.0);
      case 'thinking':
      case 'confused':
        return const Live2DEmotionFace(mouthForm: 0.1, eyeOpen: 0.95, cheek: 0.0);
      case 'sleepy':
        return const Live2DEmotionFace(mouthForm: 0.0, eyeOpen: 0.35, cheek: 0.0);
      default:
        return const Live2DEmotionFace(mouthForm: 0.1, eyeOpen: 1.0, cheek: 0.0);
    }
  }
}
