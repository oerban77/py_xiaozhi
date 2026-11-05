// Real-time lip-sync for the Live2D avatar: MFCC + phoneme-template
// matching, ported from pylipsync 0.2.1 (MIT; a Python port of
// hecomi/uLipSync, the Unity MFCC lip-sync asset).
//
// Pipeline (identical to pylipsync's compute_mfcc + PhonemeAnalyzer):
// 64 ms windows of 16 kHz mono PCM -> low-pass FIR -> pre-emphasis
// (0.97) -> Hamming -> normalize -> zero-pad to 2048 -> FFT magnitude
// -> 26-channel mel filter bank -> dB -> DCT (12 coefficients, c0
// skipped) -> z-score by the template stats -> cosine similarity
// (clamped at 0, raised to ^100) against one template vector per
// phoneme -> normalize to sum 1 -> silence gate (silence share >= 0.3
// closes the mouth) -> volume weighting (log10 RMS mapped from
// [-2.5, -1.5] to [0, 1]).
//
// Why this instead of the RMS-only mouth: RMS only says how loud the
// speech is, so the mouth just opens and closes. The phoneme weights
// say WHICH vowel is being spoken (aa/ee/ih/oh/ou), so the opening
// follows the vowel (aa opens widest, ou least) and the mouth form
// shifts with it (ee spreads, oh/ou round) -- that variation is what
// reads as "talking" instead of "opening and closing".
//
// Pure Dart (dart:math + dart:typed_data only): no Flutter import, so
// this file is unit-testable on the VM, and its MFCC output is checked
// against the Python reference in lipsync_mfcc_test.dart.
//
// The templates are generic vowel MFCCs (one vector per phoneme) from
// pylipsync's phonemes/template.json, embedded as consts so no asset
// load or pubspec change is needed. They are speaker-independent
// enough for a TTS voice; if the device test shows the mouth shape
// barely varies, the next step is calibrating templates from the TTS
// voice itself (feed it isolated vowels and record the MFCCs).

import 'dart:math' as math;
import 'dart:typed_data';

// Phoneme templates (MFCC vectors, 12 coefficients each) +
// z-score normalization stats, from pylipsync 0.2.1
// (MIT; a Python port of hecomi/uLipSync),
// phonemes/template.json. Embedded as a const so the analyzer
// needs no asset load (and no pubspec change).
const Map<String, List<double>> kLipSyncTemplates = {
  'aa': [-2.938578, -4.487621, 18.513788, -50.914841, -37.050888, -10.892639, 6.909369, -14.842397, -4.697351, 2.621092, -30.083838, -7.397129],
  'ee': [-15.349740, 48.892708, 64.855064, -32.343060, -10.650424, -1.668973, -30.692984, -37.440311, -2.678161, -13.636274, -24.949532, 5.783706],
  'ih': [6.849985, -60.075703, -8.524347, -2.932256, 3.683343, -13.482163, -3.203255, -14.422897, -8.323977, -18.071774, -15.280038, 3.250446],
  'oh': [46.794621, -13.406741, -26.565050, -64.000786, 25.737410, -2.581289, -11.435253, -15.993087, 2.756296, 3.447110, -18.369555, -6.255181],
  'ou': [13.191476, -28.421341, 39.217289, -20.528776, -24.618015, -34.899971, -3.832758, -19.581863, -13.471390, 2.044885, -5.368793, -3.614950],
  'silence': [-0.000000, 0.000000, -0.000000, 0.000000, -0.000000, 0.000000, -0.000000, 0.000000, -0.000000, 0.000000, 0.000000, 0.000000],
};
const List<double> kLipSyncMeans = [8.091294, -9.583116, 14.582791, -28.453286, -7.149762, -10.587506, -7.042480, -17.046759, -4.402431, -3.932493, -15.675292, -1.372185];
const List<double> kLipSyncStdDevs = [19.416957, 32.803004, 30.521455, 23.476892, 20.254683, 11.939452, 11.887916, 10.998256, 5.343429, 8.589569, 10.432095, 4.824106];

/// Vowels the analyzer distinguishes (pylipsync template names).
const List<String> kLipSyncVowels = ['aa', 'ee', 'ih', 'oh', 'ou'];

/// How wide the mouth opens per vowel (fraction of full open).
/// aa is the widest vowel, ou the most closed-rounded one.
const Map<String, double> kLipSyncVowelOpenness = {
  'aa': 1.0,
  'ee': 0.75,
  'ih': 0.55,
  'oh': 0.85,
  'ou': 0.5,
};

/// Mouth-form shift per vowel, added to the emotion's mouth form
/// (Haru: ParamMouthForm is smile (+) / frown (-), see
/// live2d_emotion.dart). ee spreads the lips (like a smile), oh/ou
/// round them (corners in). Kept small (+/-0.3) so the emotion face
/// stays dominant -- a sad face stays sad while talking.
const Map<String, double> kLipSyncVowelForm = {
  'aa': 0.0,
  'ee': 0.3,
  'ih': 0.1,
  'oh': -0.2,
  'ou': -0.3,
};

/// One analyzed 64 ms window: the phoneme weights (volume-weighted)
/// plus the mouth shape derived from them. The avatar smooths
/// [openness] / [formDelta] on its 30 Hz ticker.
class LipSyncFrame {
  const LipSyncFrame({
    required this.weights,
    required this.openness,
    required this.formDelta,
    required this.silence,
  });

  /// Volume-weighted phoneme weights: aa/ee/ih/oh/ou/silence.
  final Map<String, double> weights;

  /// Mouth opening, 0..1: sum of the vowel weights times each vowel's
  /// openness, already scaled by loudness (0 in silence).
  final double openness;

  /// Mouth-form shift, -0.3..0.3: weighted average of the vowels'
  /// form values; 0 when nothing is spoken.
  final double formDelta;

  /// Silence weight, 0..1 (1.0 when the window is quiet).
  final double silence;

  /// The frame for a quiet window: mouth closed, no form shift.
  static const LipSyncFrame silent = LipSyncFrame(
    weights: {
      'aa': 0.0,
      'ee': 0.0,
      'ih': 0.0,
      'oh': 0.0,
      'ou': 0.0,
      'silence': 1.0,
    },
    openness: 0.0,
    formDelta: 0.0,
    silence: 1.0,
  );

  /// Dominant vowel, or 'silence' when the window is quiet.
  String get dominant {
    var best = 'silence';
    var bestValue = silence;
    for (final vowel in kLipSyncVowels) {
      final value = weights[vowel] ?? 0.0;
      if (value > bestValue) {
        bestValue = value;
        best = vowel;
      }
    }
    return best;
  }

  /// True when this window looks like human vowels rather than noise,
  /// a tone, or silence: a clear vowel wins and the silence share is
  /// low. Used as a speech gate for the mic (barge-in and wake word),
  /// so music and ambient noise do not trigger a response. Measured
  /// separation (see the gate test): a vowel scores 0.7-1.0, white
  /// noise and a pure tone score 0.0 (silence gate), a quiet vowel
  /// 0.72. Known limit: harmonic music (a chord) scores like a vowel
  /// (its harmonics resemble formants) and still passes -- the gate
  /// rejects noise and tones, not sung or chordal music.
  bool get isSpeechLike {
    if (silence >= 0.5 || dominant == 'silence') return false;
    return (weights[dominant] ?? 0.0) >= 0.4;
  }

  /// Builds a frame from volume-weighted phoneme weights (speech
  /// weights already multiplied by the normalized volume).
  factory LipSyncFrame.fromWeights(Map<String, double> weights) {
    var speechTotal = 0.0;
    var openness = 0.0;
    var formSum = 0.0;
    for (final vowel in kLipSyncVowels) {
      final w = weights[vowel] ?? 0.0;
      speechTotal += w;
      openness += w * (kLipSyncVowelOpenness[vowel] ?? 0.0);
      formSum += w * (kLipSyncVowelForm[vowel] ?? 0.0);
    }
    return LipSyncFrame(
      weights: Map<String, double>.unmodifiable(weights),
      openness: openness.clamp(0.0, 1.0),
      formDelta: speechTotal > 1e-6
          ? (formSum / speechTotal).clamp(-0.3, 0.3)
          : 0.0,
      silence: weights['silence'] ?? 0.0,
    );
  }
}

/// MFCC + phoneme-template analyzer. One instance is cheap (the FIR
/// taps are built once); [analyze] is synchronous and fast enough
/// (~1-2 ms for a 1024-sample window) to run in the TTS feed path
/// at ~30 Hz.
class LipSyncAnalyzer {
  LipSyncAnalyzer({this.sampleRate = 16000})
      : _firTaps = _buildFirTaps(sampleRate);

  /// Analysis sample rate; the templates were built at 16 kHz.
  final int sampleRate;

  /// Analysis window: 64 ms at 16 kHz.
  static const int windowSamples = 1024;

  /// Silence gate: when the silence phoneme's share of the normalized
  /// targets reaches this, the window counts as quiet.
  static const double silenceThreshold = 0.3;

  /// Volume mapping: log10 RMS from [minVolumeLog] to [maxVolumeLog]
  /// maps to 0..1.
  static const double minVolumeLog = -2.5;
  static const double maxVolumeLog = -1.5;

  final List<double> _firTaps;

  /// Analyzes the last [windowSamples] of 16 kHz mono PCM in [-1, 1].
  /// Shorter input returns the silent frame (not enough audio yet).
  LipSyncFrame analyze(List<double> pcm16k) {
    if (pcm16k.length < windowSamples) return LipSyncFrame.silent;
    final window = pcm16k.sublist(pcm16k.length - windowSamples);
    final volume = _rms(window);
    if (volume < 1e-6) return LipSyncFrame.silent;
    final mfcc = computeMfcc(window, sampleRate, _firTaps);
    final normalized = List<double>.generate(
      mfcc.length,
      (i) => (mfcc[i] - kLipSyncMeans[i]) / kLipSyncStdDevs[i],
    );
    final raw = <String, double>{};
    for (final entry in kLipSyncTemplates.entries) {
      final template = entry.value;
      final templateNorm = List<double>.generate(
        template.length,
        (i) => (template[i] - kLipSyncMeans[i]) / kLipSyncStdDevs[i],
      );
      raw[entry.key] = _cosineSimilarityPow100(normalized, templateNorm);
    }
    var total = 0.0;
    for (final value in raw.values) {
      total += value;
    }
    final targets = <String, double>{};
    for (final entry in raw.entries) {
      targets[entry.key] = total > 0 ? entry.value / total : 0.0;
    }
    final silenceShare = targets['silence'] ?? 0.0;
    if (silenceShare >= silenceThreshold) {
      // Quiet window: mouth closed. (Python keeps the silence share
      // and zeroes the speech weights; the frame's silence is the share.)
      return LipSyncFrame(
        weights: {
          'aa': 0.0,
          'ee': 0.0,
          'ih': 0.0,
          'oh': 0.0,
          'ou': 0.0,
          'silence': silenceShare,
        },
        openness: 0.0,
        formDelta: 0.0,
        silence: silenceShare,
      );
    }
    final logVolume = math.log(volume) / math.ln10;
    final volumeNorm = ((logVolume - minVolumeLog) /
            (maxVolumeLog - minVolumeLog))
        .clamp(0.0, 1.0);
    final weighted = <String, double>{};
    for (final entry in targets.entries) {
      weighted[entry.key] = entry.value * volumeNorm;
    }
    return LipSyncFrame.fromWeights(weighted);
  }

  /// True when any 64 ms window in the last [lookbackSamples] of PCM
  /// looks like human vowels. A single latest window is the wrong
  /// thing to check for a keyword: the spotter fires after the phrase
  /// ends (trailing blanks + decode latency), so the latest 64 ms is
  /// already silence even when the user just spoke -- checking only it
  /// made the wake word silently ignored (device report). Scanning the
  /// last second catches the phrase itself. Short input returns false;
  /// callers that must not block on too little data fail open before
  /// calling this.
  bool containsSpeech(
    List<double> pcm16k, {
    int lookbackSamples = 16000,
  }) {
    if (pcm16k.length < windowSamples) return false;
    final start = pcm16k.length - lookbackSamples < 0
        ? 0
        : pcm16k.length - lookbackSamples;
    // 64 ms windows, 32 ms hop. Stops at the first vowel-like window.
    var offset = start;
    while (offset + windowSamples <= pcm16k.length) {
      if (analyze(pcm16k.sublist(offset, offset + windowSamples))
          .isSpeechLike) {
        return true;
      }
      offset += windowSamples ~/ 2;
    }
    return false;
  }

  /// Linear-interpolation resampling (used to bring 24 kHz TTS PCM
  /// down to the analyzer's 16 kHz). Same rate returns a copy.
  static List<double> resampleLinear(
    List<double> pcm,
    int fromRate,
    int toRate,
  ) {
    if (pcm.isEmpty || fromRate == toRate) return List<double>.from(pcm);
    final ratio = fromRate / toRate;
    final outLength = (pcm.length / ratio).floor();
    final out = List<double>.filled(outLength, 0.0);
    for (var i = 0; i < outLength; i++) {
      final pos = i * ratio;
      final index = pos.floor();
      final frac = pos - index;
      final a = pcm[index];
      final b = index + 1 < pcm.length ? pcm[index + 1] : a;
      out[i] = a + (b - a) * frac;
    }
    return out;
  }

  /// MFCC feature extraction, mirroring pylipsync's compute_mfcc
  /// exactly (same FIR, pre-emphasis, Hamming, zero-padding, mel bank,
  /// dB scale and DCT). Public so the parity test can check it against
  /// the Python reference sample for sample.
  static List<double> computeMfcc(
    List<double> audio,
    int sampleRate, [
    List<double>? firTaps,
  ]) {
    const melChannels = 26;
    const mfccNum = 12;
    final taps = firTaps ?? _buildFirTaps(sampleRate);
    final filtered = _lowPassFilter(audio, taps);
    final emphasized = _preEmphasis(filtered);
    final windowed = _hammingWindow(emphasized);
    final normalized = _normalize(windowed);
    final padded = _zeroPad(normalized);
    final spectrum = _fftMagnitude(padded);
    final melSpectrum = _melFilterBank(spectrum, sampleRate, melChannels);
    final melDb = _powerToDb(melSpectrum);
    final cepstrum = _dct(melDb);
    return cepstrum.sublist(1, mfccNum + 1);
  }

  static double _rms(List<double> samples) {
    var sumSq = 0.0;
    for (final s in samples) {
      sumSq += s * s;
    }
    return math.sqrt(sumSq / samples.length);
  }

  static double _cosineSimilarityPow100(
    List<double> a,
    List<double> b,
  ) {
    var dot = 0.0;
    var normA = 0.0;
    var normB = 0.0;
    for (var i = 0; i < a.length; i++) {
      dot += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }
    if (normA == 0.0 || normB == 0.0) return 0.0;
    final similarity = dot / (math.sqrt(normA) * math.sqrt(normB));
    if (similarity <= 0.0) return 0.0;
    // ^100 makes the match winner-take-all: only a near-perfect
    // template match survives, exactly like the Python reference.
    return math.pow(similarity, 100).toDouble();
  }

  static List<double> _buildFirTaps(int sampleRate) {
    final cutoff = sampleRate / 2;
    const rangeHz = 500.0;
    final cutoffNorm = (cutoff - rangeHz) / sampleRate;
    final rangeNorm = rangeHz / sampleRate;
    var n = (3.1 / rangeNorm).round();
    if ((n + 1) % 2 == 0) n += 1;
    final taps = List<double>.filled(n, 0.0);
    for (var i = 0; i < n; i++) {
      final x = i - (n - 1) / 2.0;
      final ang = 2.0 * math.pi * cutoffNorm * x;
      taps[i] = ang.abs() < 1e-10
          ? 2.0 * cutoffNorm
          : 2.0 * cutoffNorm * math.sin(ang) / ang;
    }
    return taps;
  }

  static List<double> _lowPassFilter(
    List<double> data,
    List<double> taps,
  ) {
    // The sinc FIR is symmetric around its center (taps[j] ==
    // taps[n-1-j], because sin(ang)/ang is even in i - (n-1)/2), so
    // each pair of taps shares one multiply: y[i] = sum_j taps[j] *
    // (x[i-j] + x[i-(n-1-j)]). Same filter, half the multiplies --
    // the MFCC parity test pins the output against the Python
    // reference, so a mistake here fails loudly.
    final out = List<double>.filled(data.length, 0.0);
    final n = taps.length;
    final half = n ~/ 2;
    for (var i = 0; i < data.length; i++) {
      var sum = 0.0;
      for (var j = 0; j < half; j++) {
        final a = i - j;
        final b = i - (n - 1 - j);
        final xa = a >= 0 ? data[a] : 0.0;
        final xb = b >= 0 ? data[b] : 0.0;
        sum += taps[j] * (xa + xb);
      }
      if (n % 2 == 1) {
        final c = i - half;
        if (c >= 0) sum += taps[half] * data[c];
      }
      out[i] = sum;
    }
    return out;
  }

  static List<double> _preEmphasis(List<double> data) {
    final out = List<double>.filled(data.length, 0.0);
    if (data.isEmpty) return out;
    out[0] = data[0];
    for (var i = 1; i < data.length; i++) {
      out[i] = data[i] - 0.97 * data[i - 1];
    }
    return out;
  }

  static List<double> _hammingWindow(List<double> data) {
    final n = data.length;
    final out = List<double>.filled(n, 0.0);
    for (var i = 0; i < n; i++) {
      // numpy.hamming: 0.54 - 0.46 * cos(2*pi*i/(N-1)).
      out[i] = data[i] * (0.54 - 0.46 * math.cos(2.0 * math.pi * i / (n - 1)));
    }
    return out;
  }

  static List<double> _normalize(List<double> data) {
    var maxAbs = 0.0;
    for (final value in data) {
      final absValue = value.abs();
      if (absValue > maxAbs) maxAbs = absValue;
    }
    if (maxAbs < 2.22e-16) return List<double>.from(data);
    return data.map((value) => value / maxAbs).toList();
  }

  static List<double> _zeroPad(List<double> data) {
    final n = data.length;
    final padded = List<double>.filled(n * 2, 0.0);
    padded.setRange(n ~/ 2, n ~/ 2 + n, data);
    return padded;
  }

  /// Iterative radix-2 FFT magnitude (unscaled, like numpy.fft.fft).
  /// [data] must have a power-of-two length (2048 after zero-padding).
  static List<double> _fftMagnitude(List<double> data) {
    final n = data.length;
    final re = Float64List.fromList(data);
    final im = Float64List(n);
    var j = 0;
    for (var i = 1; i < n; i++) {
      var bit = n >> 1;
      while ((j & bit) != 0) {
        j ^= bit;
        bit >>= 1;
      }
      j ^= bit;
      if (i < j) {
        final tempRe = re[i];
        re[i] = re[j];
        re[j] = tempRe;
        final tempIm = im[i];
        im[i] = im[j];
        im[j] = tempIm;
      }
    }
    var len = 2;
    while (len <= n) {
      final ang = -2.0 * math.pi / len;
      final wr = math.cos(ang);
      final wi = math.sin(ang);
      final half = len ~/ 2;
      for (var i = 0; i < n; i += len) {
        var curR = 1.0;
        var curI = 0.0;
        for (var k = 0; k < half; k++) {
          final ur = re[i + k];
          final ui = im[i + k];
          final vr = re[i + k + half] * curR - im[i + k + half] * curI;
          final vi = re[i + k + half] * curI + im[i + k + half] * curR;
          re[i + k] = ur + vr;
          im[i + k] = ui + vi;
          re[i + k + half] = ur - vr;
          im[i + k + half] = ui - vi;
          final nextR = curR * wr - curI * wi;
          curI = curR * wi + curI * wr;
          curR = nextR;
        }
      }
      len <<= 1;
    }
    return List<double>.generate(
      n,
      (i) => math.sqrt(re[i] * re[i] + im[i] * im[i]),
    );
  }

  static double _toMel(double hz) => 1127.0 * math.log(hz / 700.0 + 1.0);

  static double _toHz(double mel) => 700.0 * (math.exp(mel / 1127.0) - 1.0);

  static List<double> _melFilterBank(
    List<double> spectrum,
    int sampleRate,
    int melDiv,
  ) {
    final fMax = sampleRate / 2;
    final melMax = _toMel(fMax);
    final nMax = spectrum.length ~/ 2;
    final df = fMax / nMax;
    final dMel = melMax / (melDiv + 1);
    final fPoints = List<double>.generate(
      melDiv + 2,
      (i) => _toHz(i * dMel),
    );
    final melSpectrum = List<double>.filled(melDiv, 0.0);
    for (var n = 0; n < melDiv; n++) {
      final iBegin = (fPoints[n] / df).ceil();
      final iCenter = (fPoints[n + 1] / df).round();
      final iEnd = (fPoints[n + 2] / df).floor();
      if (iEnd > iBegin) {
        var sum = 0.0;
        for (var index = iBegin + 1; index <= iEnd; index++) {
          final freq = df * index;
          double weight;
          if (index < iCenter) {
            weight = (freq - fPoints[n]) / (fPoints[n + 1] - fPoints[n]);
          } else {
            weight = (fPoints[n + 2] - freq) / (fPoints[n + 2] - fPoints[n + 1]);
          }
          weight /= (fPoints[n + 2] - fPoints[n]) * 0.5;
          sum += weight * spectrum[index];
        }
        melSpectrum[n] = sum;
      }
    }
    return melSpectrum;
  }

  static List<double> _powerToDb(List<double> array) {
    return array
        .map((value) => 10.0 * (math.log(value > 1e-10 ? value : 1e-10) / math.ln10))
        .toList();
  }

  static List<double> _dct(List<double> spectrum) {
    final n = spectrum.length;
    final a = math.pi / n;
    final out = List<double>.filled(n, 0.0);
    for (var i = 0; i < n; i++) {
      var sum = 0.0;
      for (var j = 0; j < n; j++) {
        sum += spectrum[j] * math.cos((j + 0.5) * i * a);
      }
      out[i] = sum;
    }
    return out;
  }
}

/// Temporal smoothing for the vowel weights (the original uLipSync
/// smooths the blendshape weights on the consumer side too -- the job
/// itself is winner-take-all, see LipSyncJob.CalcCosineSimilarityScore
/// with its ^100 exponent, so a single frame can flip aa -> ou on a
/// breath. Without smoothing, the mouth form flips every 33 ms and the
/// avatar vibrates (and the WebView bridge is hammered with a form send
/// on every tick). A one-pole blend (alpha 0.4 at ~30 Hz, ~80 ms time
/// constant) averages one-frame flips away while tracking real vowel
/// changes within ~3 frames. A confidence gate damps the opening when
/// no vowel clearly wins (uncertain windows: breaths, transitions,
/// noise) instead of opening on a guess.
class LipSyncSmoother {
  LipSyncSmoother({this.alpha = 0.4, this.minConfidentWeight = 0.4});

  /// Weight of the newest frame (0..1); higher tracks faster.
  final double alpha;

  /// Smoothed dominant weight below this damps the opening toward 0.
  final double minConfidentWeight;

  Map<String, double>? _smoothed;

  LipSyncFrame smooth(LipSyncFrame frame) {
    final previous = _smoothed;
    final blended = <String, double>{};
    for (final key in frame.weights.keys) {
      final current = frame.weights[key] ?? 0.0;
      final old = previous?[key] ?? 0.0;
      blended[key] =
          previous == null ? current : alpha * current + (1.0 - alpha) * old;
    }
    _smoothed = blended;
    final smoothedFrame = LipSyncFrame.fromWeights(blended);
    // Confidence gate: a spread-out weight distribution (no vowel
    // clearly winning) is a breath, a transition, or noise -- damp the
    // opening instead of opening on a guess.
    final dominantWeight = blended[smoothedFrame.dominant] ?? 0.0;
    final confidence =
        (dominantWeight / minConfidentWeight).clamp(0.0, 1.0);
    if (confidence >= 1.0) return smoothedFrame;
    return LipSyncFrame(
      weights: smoothedFrame.weights,
      openness: smoothedFrame.openness * confidence,
      formDelta: smoothedFrame.formDelta * confidence,
      silence: smoothedFrame.silence,
    );
  }

  void reset() => _smoothed = null;
}
