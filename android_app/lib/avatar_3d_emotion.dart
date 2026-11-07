/// Pure emotion -> 3D face-morph mapping, kept separate from [Avatar3D]
/// so it can be unit-tested without a WebView.
///
/// Maps the server emotion (the `llm` message's `emotion` field, same
/// 21 names as EmotionService.bundled) to ARKit morph-target weights.
/// The Ready Player Me models carry the same 72-morph set (15 Oculus
/// visemes + 52 ARKit), so these names exist on both bundled models
/// (halfBody.glb and female.glb).
///
/// This is the 3D counterpart of [Live2DEmotionFace] (which maps the
/// same emotions to Cubism MouthForm/EyeOpen/Cheek). The values are
/// deliberately strong enough to read at a glance on a phone screen:
/// a smile goes to 0.7, a frown to 0.7, sad brows to 0.6.
///
/// How the weights are applied: [Avatar3D] merges them into the
/// 30 Hz mouth ticker's weights before sending them to the page, so
/// the expression persists (the ticker rewrites mouth morphs every
/// 33 ms; sending the emotion separately would be clobbered on the
/// next tick). Morphs the mouth ticker already writes (mouthSmile,
/// jawOpen, mouthOpen) are ADDED to the ticker's value and clamped
/// to 1.0, so a happy avatar talking smiles wider than a neutral one
/// talking, instead of the two fighting.
class Avatar3DEmotionFace {
  const Avatar3DEmotionFace(this.weights);

  /// Morph-target weights for this emotion (ARKit names -> 0..1).
  final Map<String, double> weights;

  static Avatar3DEmotionFace forEmotion(String emotion) {
    switch (emotion.toLowerCase()) {
      case 'happy':
      case 'laughing':
      case 'confident':
      case 'cool':
      case 'funny':
      case 'silly':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.7,
          'mouthSmileLeft': 0.7,
          'mouthSmileRight': 0.7,
          'cheekSquintLeft': 0.4,
          'cheekSquintRight': 0.4,
          'eyeSquintLeft': 0.3,
          'eyeSquintRight': 0.3,
        });
      case 'loving':
      case 'kissy':
      case 'delicious':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.5,
          'mouthSmileLeft': 0.5,
          'mouthSmileRight': 0.5,
          'cheekSquintLeft': 0.3,
          'cheekSquintRight': 0.3,
          'eyeSquintLeft': 0.2,
          'eyeSquintRight': 0.2,
        });
      case 'winking':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.4,
          'mouthSmileLeft': 0.4,
          'mouthSmileRight': 0.4,
          'eyeSquintRight': 0.6,
          'cheekSquintRight': 0.3,
        });
      case 'sad':
      case 'crying':
        return const Avatar3DEmotionFace({
          'mouthFrown': 0.7,
          'mouthFrownLeft': 0.6,
          'mouthFrownRight': 0.6,
          'browInnerUp': 0.6,
          'mouthLowerDownLeft': 0.3,
          'mouthLowerDownRight': 0.3,
        });
      case 'angry':
        return const Avatar3DEmotionFace({
          'browDownLeft': 0.7,
          'browDownRight': 0.7,
          'mouthFrown': 0.5,
          'mouthFrownLeft': 0.5,
          'mouthFrownRight': 0.5,
          'jawForward': 0.3,
          'eyeSquintLeft': 0.3,
          'eyeSquintRight': 0.3,
        });
      case 'embarrassed':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.3,
          'mouthSmileLeft': 0.3,
          'mouthSmileRight': 0.3,
          'cheekSquintLeft': 0.5,
          'cheekSquintRight': 0.5,
          'browInnerUp': 0.3,
        });
      case 'shocked':
      case 'surprised':
        return const Avatar3DEmotionFace({
          'browInnerUp': 0.7,
          'browOuterUpLeft': 0.4,
          'browOuterUpRight': 0.4,
          'jawOpen': 0.3,
          'mouthOpen': 0.2,
          'eyeWideLeft': 0.5,
          'eyeWideRight': 0.5,
        });
      case 'thinking':
      case 'confused':
        return const Avatar3DEmotionFace({
          'browInnerUp': 0.4,
          'browOuterUpLeft': 0.2,
          'mouthFrown': 0.2,
          'mouthFrownLeft': 0.2,
          'mouthFrownRight': 0.2,
        });
      case 'sleepy':
        return const Avatar3DEmotionFace({
          'eyeSquintLeft': 0.5,
          'eyeSquintRight': 0.5,
          'browInnerUp': 0.2,
          'jawOpen': 0.1,
          'mouthOpen': 0.1,
        });
      case 'relaxed':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.3,
          'mouthSmileLeft': 0.3,
          'mouthSmileRight': 0.3,
          'eyeSquintLeft': 0.1,
          'eyeSquintRight': 0.1,
        });
      case 'neutral':
      default:
        return const Avatar3DEmotionFace({});
    }
  }

  /// Face for a device state, used when no server emotion is active
  /// (empty, or 'neutral'). Without this, the face sits flat between
  /// server emotions: talking gets a gentle smile, listening an
  /// attentive slight smile, idle the calm default.
  static Avatar3DEmotionFace forDeviceState({
    required bool isSpeaking,
    required bool isListening,
  }) {
    if (isSpeaking) {
      return const Avatar3DEmotionFace({
        'mouthSmile': 0.25,
        'mouthSmileLeft': 0.25,
        'mouthSmileRight': 0.25,
        'cheekSquintLeft': 0.1,
        'cheekSquintRight': 0.1,
      });
    }
    if (isListening) {
      return const Avatar3DEmotionFace({
        'mouthSmile': 0.15,
        'mouthSmileLeft': 0.15,
        'mouthSmileRight': 0.15,
      });
    }
    return const Avatar3DEmotionFace({});
  }
}
