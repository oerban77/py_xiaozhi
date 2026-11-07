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
/// same emotions to Cubism MouthForm/EyeOpen/Cheek).
///
/// The values are deliberately MODEST: this model's morphs are very
/// strong (the same reason the talking mouth needed caps of 0.5 and
/// the smile/funnel scaled 0.4x). The first version used 0.7 smiles
/// and produced a manic grin that ignored the user's mouth-strength
/// setting — frightening, not friendly. A happy smile is now 0.35
/// before the strength scaling, a sad frown 0.35, angry brows 0.35.
///
/// How the weights are applied: [Avatar3D] merges them into the
/// 30 Hz mouth ticker's weights before sending them to the page, so
/// the expression persists (the ticker rewrites mouth morphs every
/// 33 ms; sending the emotion separately would be clobbered on the
/// next tick). Critically, the emotion's MOUTH morphs (smile, frown,
/// jaw, lip shapes) are suppressed while the avatar is speaking: the
/// mouth belongs to the vowels then, and a held smile/frown on top of
/// the vowel shape is what made talking look like a wide grin (and a
/// held-open surprised jaw masked the articulation, so the mouth
/// looked frozen). While talking the emotion shows only in the upper
/// face (brows, eyes, cheeks); when NOT speaking the full emotion
/// (including the mouth) shows. ALL emotion weights are scaled by the
/// user's
/// "Kekuatan Mulut 3D" setting ([Avatar3D.mouthStrength], default
/// 0.5) before merging, so the expression intensity follows the same
/// setting as the mouth opening — the face never overpowers the
/// mouth setting the user chose.
class Avatar3DEmotionFace {
  const Avatar3DEmotionFace(this.weights);

  /// Morph-target weights for this emotion (ARKit names -> 0..1).
  /// These are the values BEFORE the mouth-strength scaling.
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
          'mouthSmile': 0.35,
          'mouthSmileLeft': 0.35,
          'mouthSmileRight': 0.35,
          'cheekSquintLeft': 0.2,
          'cheekSquintRight': 0.2,
          'eyeSquintLeft': 0.15,
          'eyeSquintRight': 0.15,
        });
      case 'loving':
      case 'kissy':
      case 'delicious':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.25,
          'mouthSmileLeft': 0.25,
          'mouthSmileRight': 0.25,
          'cheekSquintLeft': 0.15,
          'cheekSquintRight': 0.15,
          'eyeSquintLeft': 0.1,
          'eyeSquintRight': 0.1,
        });
      case 'winking':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.2,
          'mouthSmileLeft': 0.2,
          'mouthSmileRight': 0.2,
          'eyeSquintRight': 0.3,
          'cheekSquintRight': 0.15,
        });
      case 'sad':
      case 'crying':
        return const Avatar3DEmotionFace({
          'mouthFrown': 0.35,
          'mouthFrownLeft': 0.3,
          'mouthFrownRight': 0.3,
          'browInnerUp': 0.3,
          'mouthLowerDownLeft': 0.15,
          'mouthLowerDownRight': 0.15,
        });
      case 'angry':
        return const Avatar3DEmotionFace({
          'browDownLeft': 0.35,
          'browDownRight': 0.35,
          'mouthFrown': 0.25,
          'mouthFrownLeft': 0.25,
          'mouthFrownRight': 0.25,
          'jawForward': 0.15,
          'eyeSquintLeft': 0.15,
          'eyeSquintRight': 0.15,
        });
      case 'embarrassed':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.15,
          'mouthSmileLeft': 0.15,
          'mouthSmileRight': 0.15,
          'cheekSquintLeft': 0.25,
          'cheekSquintRight': 0.25,
          'browInnerUp': 0.15,
        });
      case 'shocked':
      case 'surprised':
        return const Avatar3DEmotionFace({
          'browInnerUp': 0.35,
          'browOuterUpLeft': 0.2,
          'browOuterUpRight': 0.2,
          'jawOpen': 0.15,
          'mouthOpen': 0.1,
          'eyeWideLeft': 0.25,
          'eyeWideRight': 0.25,
        });
      case 'thinking':
      case 'confused':
        return const Avatar3DEmotionFace({
          'browInnerUp': 0.2,
          'browOuterUpLeft': 0.1,
          'mouthFrown': 0.1,
          'mouthFrownLeft': 0.1,
          'mouthFrownRight': 0.1,
        });
      case 'sleepy':
        return const Avatar3DEmotionFace({
          'eyeSquintLeft': 0.25,
          'eyeSquintRight': 0.25,
          'browInnerUp': 0.1,
          'jawOpen': 0.05,
          'mouthOpen': 0.05,
        });
      case 'relaxed':
        return const Avatar3DEmotionFace({
          'mouthSmile': 0.15,
          'mouthSmileLeft': 0.15,
          'mouthSmileRight': 0.15,
          'eyeSquintLeft': 0.05,
          'eyeSquintRight': 0.05,
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
        'mouthSmile': 0.12,
        'mouthSmileLeft': 0.12,
        'mouthSmileRight': 0.12,
        'cheekSquintLeft': 0.05,
        'cheekSquintRight': 0.05,
      });
    }
    if (isListening) {
      return const Avatar3DEmotionFace({
        'mouthSmile': 0.08,
        'mouthSmileLeft': 0.08,
        'mouthSmileRight': 0.08,
      });
    }
    return const Avatar3DEmotionFace({});
  }
}
