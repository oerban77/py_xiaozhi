import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/lipsync_mfcc.dart';

/// The same deterministic signal the Python reference used
/// (180 Hz + 540 Hz + 900 Hz + 1500 Hz sines, 64 ms at 16 kHz).
List<double> testSignal() {
  const sampleRate = 16000;
  return List<double>.generate(1024, (i) {
    final t = i / sampleRate;
    return 0.5 * math.sin(2 * math.pi * 180 * t) +
        0.3 * math.sin(2 * math.pi * 540 * t) +
        0.2 * math.sin(2 * math.pi * 900 * t) +
        0.1 * math.sin(2 * math.pi * 1500 * t);
  });
}

void main() {
  test('MFCC matches the pylipsync reference sample for sample', () {
    // Expected values computed by pylipsync 0.2.1 compute_mfcc on the
    // same signal (see /tmp/lipsync_parity.json). Tolerance is loose
    // (0.5 on values of order 100) because the Python pipeline runs in
    // float32 in places while Dart is float64 throughout.
    const expected = [
      117.50858306884766,
      -22.10659408569336,
      -32.03118133544922,
      -4.4708452224731445,
      9.590982437133789,
      -2.088998556137085,
      6.723913192749023,
      22.602693557739258,
      12.38733959197998,
      -25.70802116394043,
      -37.039955139160156,
      -16.102338790893555,
    ];
    final mfcc = LipSyncAnalyzer.computeMfcc(testSignal(), 16000);
    expect(mfcc.length, 12);
    for (var i = 0; i < expected.length; i++) {
      expect(mfcc[i], closeTo(expected[i], 0.5), reason: 'mfcc[$i]');
    }
  });

  test('MFCC of silence is (near) zero', () {
    final mfcc = LipSyncAnalyzer.computeMfcc(
      List<double>.filled(1024, 0.0),
      16000,
    );
    for (final value in mfcc) {
      expect(value.abs(), lessThan(1e-6));
    }
  });

  test('analyzer picks the same dominant vowel as the Python reference', () {
    // pylipsync's PhonemeAnalyzer on this signal: oh = 1.0, everything
    // else ~0 (cosine^100 is winner-take-all), volume normalized to 1.0.
    final frame = LipSyncAnalyzer().analyze(testSignal());
    expect(frame.dominant, 'oh');
    expect(frame.weights['oh'] ?? 0.0, greaterThan(0.9));
    expect(frame.silence, lessThan(0.1));
    // oh opens 0.85 wide and rounds the mouth (form -0.2).
    expect(frame.openness, closeTo(0.85, 0.05));
    expect(frame.formDelta, closeTo(-0.2, 0.05));
  });

  test('silence analyzes to the silent frame', () {
    final frame = LipSyncAnalyzer().analyze(List<double>.filled(1024, 0.0));
    expect(frame.openness, 0.0);
    expect(frame.formDelta, 0.0);
    expect(frame.silence, 1.0);
    expect(frame.dominant, 'silence');
  });

  test('short input returns the silent frame instead of crashing', () {
    expect(
      LipSyncAnalyzer().analyze(const [0.1, -0.1, 0.05]).openness,
      0.0,
    );
    expect(LipSyncAnalyzer().analyze(const <double>[]).dominant, 'silence');
  });

  test('vowel weights map to opening and form as documented', () {
    final aa = LipSyncFrame.fromWeights({
      'aa': 1.0,
      'ee': 0.0,
      'ih': 0.0,
      'oh': 0.0,
      'ou': 0.0,
      'silence': 0.0,
    });
    expect(aa.openness, 1.0);
    expect(aa.formDelta, 0.0);
    expect(aa.dominant, 'aa');

    final ou = LipSyncFrame.fromWeights({
      'aa': 0.0,
      'ee': 0.0,
      'ih': 0.0,
      'oh': 0.0,
      'ou': 1.0,
      'silence': 0.0,
    });
    expect(ou.openness, 0.5);
    expect(ou.formDelta, -0.3);
    expect(ou.dominant, 'ou');

    final ee = LipSyncFrame.fromWeights({
      'aa': 0.0,
      'ee': 1.0,
      'ih': 0.0,
      'oh': 0.0,
      'ou': 0.0,
      'silence': 0.0,
    });
    expect(ee.openness, 0.75);
    expect(ee.formDelta, 0.3);
  });

  test('resampling 24 kHz to 16 kHz keeps a constant signal constant', () {
    final resampled = LipSyncAnalyzer.resampleLinear(
      List<double>.filled(480, 1.0),
      24000,
      16000,
    );
    expect(resampled.length, 320);
    for (final value in resampled) {
      expect(value, 1.0);
    }
  });

  test('resampling at the same rate returns a copy', () {
    final input = [0.1, -0.2, 0.3];
    final resampled = LipSyncAnalyzer.resampleLinear(input, 16000, 16000);
    expect(resampled, input);
    expect(identical(resampled, input), isFalse);
  });

  test('smoother averages one-frame vowel flips away', () {
    final smoother = LipSyncSmoother();
    Map<String, double> weights(String vowel) => {
          'aa': vowel == 'aa' ? 1.0 : 0.0,
          'ee': 0.0,
          'ih': 0.0,
          'oh': 0.0,
          'ou': vowel == 'ou' ? 1.0 : 0.0,
          'silence': 0.0,
        };
    // First frame passes through unblended.
    final first = smoother.smooth(LipSyncFrame.fromWeights(weights('aa')));
    expect(first.openness, 1.0);
    // A one-frame flip to ou blends 0.4 new / 0.6 old: openness
    // 0.6*1.0 + 0.4*0.5 = 0.8, form 0.6*0.0 + 0.4*(-0.3) = -0.12 --
    // the mouth moves toward ou instead of jumping to it.
    final flipped = smoother.smooth(LipSyncFrame.fromWeights(weights('ou')));
    expect(flipped.openness, closeTo(0.8, 0.01));
    expect(flipped.formDelta, closeTo(-0.12, 0.01));
    // Back to aa: 0.76 aa / 0.24 ou -> openness 0.88.
    final back = smoother.smooth(LipSyncFrame.fromWeights(weights('aa')));
    expect(back.openness, closeTo(0.88, 0.01));
  });

  test('smoother damps the opening when no vowel clearly wins', () {
    final smoother = LipSyncSmoother();
    // Spread weights: every vowel 0.2, dominant 0.2 < 0.4 threshold,
    // so confidence is 0.5 and the raw openness 0.73 is halved.
    final spread = smoother.smooth(
      LipSyncFrame.fromWeights({
        'aa': 0.2,
        'ee': 0.2,
        'ih': 0.2,
        'oh': 0.2,
        'ou': 0.2,
        'silence': 0.0,
      }),
    );
    expect(spread.openness, closeTo(0.365, 0.01));
  });

  test('smoother reset passes the next frame through unblended', () {
    final smoother = LipSyncSmoother();
    Map<String, double> weights(String vowel) => {
          'aa': vowel == 'aa' ? 1.0 : 0.0,
          'ee': 0.0,
          'ih': 0.0,
          'oh': 0.0,
          'ou': vowel == 'ou' ? 1.0 : 0.0,
          'silence': 0.0,
        };
    smoother.smooth(LipSyncFrame.fromWeights(weights('aa')));
    smoother.smooth(LipSyncFrame.fromWeights(weights('ou')));
    smoother.reset();
    final afterReset =
        smoother.smooth(LipSyncFrame.fromWeights(weights('ou')));
    expect(afterReset.openness, 0.5);
    expect(afterReset.formDelta, -0.3);
  });

  test('isSpeechLike separates vowels from noise and tones', () {
    final analyzer = LipSyncAnalyzer();
    // Vowel (and a quiet one) pass.
    expect(analyzer.analyze(testSignal()).isSpeechLike, isTrue);
    final quietVowel = List<double>.generate(1024, (i) {
      final t = i / 16000;
      return 0.02 * math.sin(2 * math.pi * 180 * t) +
          0.012 * math.sin(2 * math.pi * 540 * t);
    });
    expect(analyzer.analyze(quietVowel).isSpeechLike, isTrue);
    // Silence, white noise and a pure tone fail.
    expect(
      analyzer.analyze(List<double>.filled(1024, 0.0)).isSpeechLike,
      isFalse,
    );
    final rng = math.Random(42);
    final noise = List<double>.generate(
      1024,
      (_) => (rng.nextDouble() * 2 - 1) * 0.18,
    );
    expect(analyzer.analyze(noise).isSpeechLike, isFalse);
    final tone = List<double>.generate(
      1024,
      (i) => 0.4 * math.sin(2 * math.pi * 440 * i / 16000),
    );
    expect(analyzer.analyze(tone).isSpeechLike, isFalse);
  });

  test('harmonic music still passes the vowel gate (known limit)', () {
    // A C-major chord's harmonics resemble vowel formants closely
    // enough to match the ou template -- the gate rejects noise and
    // tones, not chordal or sung music. This test pins that behavior
    // so a future change cannot silently claim otherwise.
    final chord = List<double>.generate(1024, (i) {
      final t = i / 16000;
      return 0.25 * math.sin(2 * math.pi * 262 * t) +
          0.25 * math.sin(2 * math.pi * 330 * t) +
          0.25 * math.sin(2 * math.pi * 392 * t);
    });
    final frame = LipSyncAnalyzer().analyze(chord);
    expect(frame.dominant, 'ou');
    expect(frame.isSpeechLike, isTrue);
  });
}
