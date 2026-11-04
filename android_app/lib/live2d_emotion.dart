/// Pure emotion -> Live2D face-parameter mapping, kept separate from
/// [Live2DAvatar] so it can be unit-tested without a WebView.
///
/// Values are standard Cubism parameters: MouthForm in [-1, 1]
/// (-1 frown, +1 smile), EyeOpen in [0, 1], Cheek (blush) in [0, 1].
/// They exist on practically every Cubism model, unlike expression ids
/// which are model-specific.
///
/// The values are deliberately strong: the earlier, subtler values
/// (happy 0.7, sad eyeOpen 0.9) were barely readable at a glance on a
/// phone screen, so the character looked frozen even when the emotion
/// changed. A smile now goes all the way to 1.0, a frown to -1.0, and
/// sad/angry eyes narrow visibly.
///
/// Every name the server can send (the `llm` message's `emotion` field,
/// same 21 names as EmotionService.bundled) maps explicitly here, so no
/// server emoticon ever falls through to the generic default.
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
        return const Live2DEmotionFace(mouthForm: 1.0, eyeOpen: 1.0, cheek: 0.3);
      case 'loving':
      case 'kissy':
      case 'delicious':
        return const Live2DEmotionFace(mouthForm: 0.6, eyeOpen: 1.0, cheek: 0.5);
      case 'winking':
        return const Live2DEmotionFace(mouthForm: 0.5, eyeOpen: 0.85, cheek: 0.0);
      case 'sad':
      case 'crying':
        return const Live2DEmotionFace(mouthForm: -1.0, eyeOpen: 0.7, cheek: 0.0);
      case 'angry':
        return const Live2DEmotionFace(mouthForm: -0.8, eyeOpen: 0.6, cheek: 0.0);
      case 'embarrassed':
        return const Live2DEmotionFace(mouthForm: -0.4, eyeOpen: 1.0, cheek: 0.6);
      case 'shocked':
      case 'surprised':
        return const Live2DEmotionFace(mouthForm: 0.35, eyeOpen: 1.0, cheek: 0.0);
      case 'thinking':
      case 'confused':
        return const Live2DEmotionFace(mouthForm: 0.1, eyeOpen: 0.95, cheek: 0.0);
      case 'sleepy':
        return const Live2DEmotionFace(mouthForm: 0.0, eyeOpen: 0.35, cheek: 0.0);
      case 'relaxed':
        return const Live2DEmotionFace(mouthForm: 0.3, eyeOpen: 1.0, cheek: 0.0);
      case 'neutral':
        return const Live2DEmotionFace(mouthForm: 0.1, eyeOpen: 1.0, cheek: 0.0);
      default:
        return const Live2DEmotionFace(mouthForm: 0.1, eyeOpen: 1.0, cheek: 0.0);
    }
  }

  /// Face for a device state, used when no server emotion is active
  /// (empty, or 'neutral' — which is also what the controller resets to
  /// on listening/idle). Without this, the face sits on the flat default
  /// between server emotions and the character looks frozen even though
  /// the conversation state keeps changing: talking gets a gentle
  /// smile, listening an attentive slight smile, idle the calm default.
  static Live2DEmotionFace forDeviceState({
    required bool isSpeaking,
    required bool isListening,
  }) {
    if (isSpeaking) {
      return const Live2DEmotionFace(mouthForm: 0.45, eyeOpen: 1.0, cheek: 0.1);
    }
    if (isListening) {
      return const Live2DEmotionFace(mouthForm: 0.25, eyeOpen: 1.0, cheek: 0.0);
    }
    return const Live2DEmotionFace(mouthForm: 0.1, eyeOpen: 1.0, cheek: 0.0);
  }
}
