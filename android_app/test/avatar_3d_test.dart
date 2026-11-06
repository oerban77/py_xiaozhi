import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/avatar_3d.dart';
import 'package:py_xiaozhi_android/lipsync_mfcc.dart';

/// Unit tests for the 3D avatar's lip-sync -> morph-target mapping
/// (avatar_3d.dart). The mapping is pure Dart, so it is pinned here;
/// the WebView/three.js side only applies the weights by name.

LipSyncFrame frame({
  required Map<String, double> weights,
  required double openness,
  required double formDelta,
}) {
  return LipSyncFrame(
    weights: weights,
    openness: openness,
    formDelta: formDelta,
    silence: weights['silence'] ?? 0.0,
  );
}

void main() {
  test('aa frame opens the jaw and the aa viseme only', () {
    final weights = lipSyncFrameToMorphWeights(
      frame(
        weights: {'aa': 1.0, 'silence': 0.0},
        openness: 1.0,
        formDelta: 0.0,
      ),
    );
    expect(weights['viseme_aa'], 1.0);
    expect(weights['viseme_E'], 0.0);
    expect(weights['viseme_I'], 0.0);
    expect(weights['viseme_O'], 0.0);
    expect(weights['viseme_U'], 0.0);
    expect(weights['jawOpen'], 1.0);
    expect(weights['mouthOpen'], closeTo(0.7, 1e-9));
    expect(weights['mouthSmile'], 0.0);
    expect(weights['mouthFunnel'], 0.0);
  });

  test('ee frame smiles in proportion to openness and form', () {
    final weights = lipSyncFrameToMorphWeights(
      frame(
        weights: {'ee': 1.0, 'silence': 0.0},
        openness: 0.75,
        formDelta: 0.3,
      ),
    );
    // Pure vowel, so the sharpened viseme is boosted to full strength
    // (1.0 x 0.75 x 1.6 = 1.2, clamped to 1.0).
    expect(weights['viseme_E'], 1.0);
    // 'ee' keeps the jaw narrower than 'aa' at the same openness.
    expect(weights['jawOpen'], closeTo(0.4125, 1e-9));
    expect(weights['mouthSmile'], 0.75);
    expect(weights['mouthSmileLeft'], 0.75);
    expect(weights['mouthSmileRight'], 0.75);
    expect(weights['mouthFunnel'], 0.0);
    expect(weights['mouthPucker'], 0.0);
  });

  test('ou frame funnels and puckers in proportion to openness', () {
    final weights = lipSyncFrameToMorphWeights(
      frame(
        weights: {'ou': 1.0, 'silence': 0.0},
        openness: 0.5,
        formDelta: -0.3,
      ),
    );
    // Pure vowel, boosted: 1.0 x 0.5 x 1.6 = 0.8.
    expect(weights['viseme_U'], closeTo(0.8, 1e-9));
    expect(weights['jawOpen'], closeTo(0.35, 1e-9));
    expect(weights['mouthFunnel'], 0.5);
    expect(weights['mouthPucker'], closeTo(0.3, 1e-9));
    expect(weights['mouthSmile'], 0.0);
  });

  test('mixed vowel weights split across visemes by openness', () {
    final weights = lipSyncFrameToMorphWeights(
      frame(
        weights: {'aa': 0.6, 'oh': 0.4, 'silence': 0.0},
        openness: 0.8,
        formDelta: -0.12,
      ),
    );
    // Sharpened (w^1.5, renormalized): aa share 0.6475, oh 0.3525;
    // each boosted by openness x 1.6 = 1.28.
    expect(weights['viseme_aa'], closeTo(0.8289, 1e-3));
    expect(weights['viseme_O'], closeTo(0.4511, 1e-3));
    // Jaw follows the vowel mix: (0.6 x 1.0 + 0.4 x 0.8) x 0.8 = 0.736.
    expect(weights['jawOpen'], closeTo(0.736, 1e-9));
    // form -0.12 / -0.3 = 0.4 funnel share, times openness 0.8.
    expect(weights['mouthFunnel'], closeTo(0.32, 1e-9));
  });

  test('jaw drop follows the vowel: aa widest, oh middle, ih narrowest', () {
    final aa = lipSyncFrameToMorphWeights(
      frame(weights: {'aa': 1.0, 'silence': 0.0}, openness: 1.0, formDelta: 0.0),
    );
    final oh = lipSyncFrameToMorphWeights(
      frame(weights: {'oh': 1.0, 'silence': 0.0}, openness: 1.0, formDelta: 0.0),
    );
    final ih = lipSyncFrameToMorphWeights(
      frame(weights: {'ih': 1.0, 'silence': 0.0}, openness: 1.0, formDelta: 0.0),
    );
    expect(aa['jawOpen'], 1.0);
    expect(oh['jawOpen'], closeTo(0.8, 1e-9));
    expect(ih['jawOpen'], closeTo(0.45, 1e-9));
    // And each pure vowel drives its own viseme to full strength.
    expect(aa['viseme_aa'], 1.0);
    expect(oh['viseme_O'], 1.0);
    expect(ih['viseme_I'], 1.0);
  });

  test('silent frame closes every morph', () {
    final weights = lipSyncFrameToMorphWeights(LipSyncFrame.silent);
    expect(weights.values.every((v) => v == 0.0), isTrue);
  });

  test('weights never exceed 1 even for a loud frame', () {
    final weights = lipSyncFrameToMorphWeights(
      frame(
        weights: {'aa': 1.0, 'silence': 0.0},
        openness: 1.0,
        formDelta: 0.3,
      ),
    );
    expect(weights.values.every((v) => v >= 0.0 && v <= 1.0), isTrue);
  });
}
