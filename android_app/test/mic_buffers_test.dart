// Tests for MicRingBuffer and MicFrameQueue (lib/mic_buffers.dart):
// the circular replacements for the mic ring buffer and the uplink
// framing queue in XiaozhiController.
//
// The behavior contract they pin down:
// - the ring keeps exactly the last 1 s (capacity samples) in time
//   order, even after many wrap-arounds;
// - recentRms equals the RMS of those same samples;
// - the frame queue produces byte-identical 20 ms frames to the old
//   List<int> + take/removeRange algorithm, for arbitrary mic chunk
//   sizes, including after the backing store compacts or grows.

import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/mic_buffers.dart';

void main() {
  group('MicRingBuffer', () {
    test('snapshot returns written samples in time order before full', () {
      final ring = MicRingBuffer(capacity: 8);
      ring.add(0.1);
      ring.add(0.2);
      ring.add(0.3);
      expect(ring.length, 3);
      expect(ring.snapshot(), [0.1, 0.2, 0.3]);
    });

    test('snapshot is empty and RMS is 0 before any write', () {
      final ring = MicRingBuffer(capacity: 8);
      expect(ring.isEmpty, isTrue);
      expect(ring.snapshot(), isEmpty);
      expect(ring.recentRms(), 0.0);
    });

    test('keeps exactly the last capacity samples in time order', () {
      final ring = MicRingBuffer(capacity: 5);
      for (var i = 0; i < 13; i++) {
        ring.add(i.toDouble());
      }
      expect(ring.length, 5);
      // Last 5 of 0..12, oldest first.
      expect(ring.snapshot(), [8.0, 9.0, 10.0, 11.0, 12.0]);
    });

    test('survives many wrap-arounds with exact time order', () {
      final ring = MicRingBuffer(capacity: 4);
      for (var i = 0; i < 1000; i++) {
        ring.add(i.toDouble());
      }
      expect(ring.snapshot(), [996.0, 997.0, 998.0, 999.0]);
    });

    test('snapshot does not alias the ring (later writes do not change it)', () {
      final ring = MicRingBuffer(capacity: 3);
      ring.add(1.0);
      ring.add(2.0);
      final snap = ring.snapshot();
      ring.add(3.0);
      ring.add(4.0);
      expect(snap, [1.0, 2.0]);
      expect(ring.snapshot(), [2.0, 3.0, 4.0]);
    });

    test('recentRms matches the RMS of the last second, before and after wrap', () {
      double manualRms(List<double> values) {
        var sumSq = 0.0;
        for (final v in values) {
          sumSq += v * v;
        }
        return math.sqrt(sumSq / values.length);
      }

      final ring = MicRingBuffer(capacity: 6);
      final written = <double>[];
      for (var i = 0; i < 10; i++) {
        final v = (i % 3 == 0 ? 0.5 : -0.25) * (i + 1) / 10;
        ring.add(v);
        written.add(v);
        final expected = manualRms(written.sublist(written.length - ring.length));
        expect(ring.recentRms(), closeTo(expected, 1e-12));
      }
    });

    test('default capacity is 16000 (last 1 s at 16 kHz)', () {
      expect(MicRingBuffer().capacity, 16000);
    });
  });

  group('MicFrameQueue', () {
    const frameBytes = 640;

    // The old algorithm, kept here as the reference: accumulate in a
    // List<int>, take 640-byte frames with a double copy, shift the
    // rest with removeRange.
    List<Uint8List> oldAlgorithmFrames(List<Uint8List> chunks) {
      final pending = <int>[];
      final frames = <Uint8List>[];
      for (final chunk in chunks) {
        pending.addAll(chunk);
        while (pending.length >= frameBytes) {
          frames.add(Uint8List.fromList(pending.take(frameBytes).toList()));
          pending.removeRange(0, frameBytes);
        }
      }
      return frames;
    }

    int oldAlgorithmLeftover(List<Uint8List> chunks) {
      final pending = <int>[];
      for (final chunk in chunks) {
        pending.addAll(chunk);
      }
      return pending.length % frameBytes;
    }

    List<Uint8List> queueFrames(MicFrameQueue queue, List<Uint8List> chunks) {
      final frames = <Uint8List>[];
      for (final chunk in chunks) {
        queue.addAll(chunk);
        while (queue.length >= frameBytes) {
          final frame = queue.takeFrame(frameBytes)!;
          // Copy out immediately: the view is only valid until the
          // next addAll, exactly like the controller's RMS/encode use.
          frames.add(Uint8List.fromList(frame));
          queue.consume(frameBytes);
        }
      }
      return frames;
    }

    // Deterministic pseudo-random chunk bytes (content matters: the
    // frames must be byte-identical, not just the right length).
    Uint8List chunk(int seed, int length) {
      var state = seed;
      final bytes = Uint8List(length);
      for (var i = 0; i < length; i++) {
        state = (state * 1103515245 + 12345) & 0x7fffffff;
        bytes[i] = state % 256;
      }
      return bytes;
    }

    test('produces byte-identical frames to the old algorithm', () {
      // Odd chunk sizes: smaller than a frame, larger than a frame,
      // and not multiples of a frame, so frames span chunk boundaries.
      final chunks = [
        chunk(1, 100),
        chunk(2, 1000),
        chunk(3, 640),
        chunk(4, 37),
        chunk(5, 4096),
        chunk(6, 1),
        chunk(7, 639),
        chunk(8, 1280),
      ];
      final queue = MicFrameQueue();
      final got = queueFrames(queue, chunks);
      final want = oldAlgorithmFrames(chunks);
      expect(got.length, want.length);
      for (var i = 0; i < want.length; i++) {
        expect(got[i], want[i]);
      }
      expect(queue.length, oldAlgorithmLeftover(chunks));
    });

    test('takeFrame returns null when fewer than a frame is buffered', () {
      final queue = MicFrameQueue();
      expect(queue.takeFrame(frameBytes), isNull);
      queue.addAll(chunk(9, 639));
      expect(queue.takeFrame(frameBytes), isNull);
      queue.addAll(chunk(10, 1));
      expect(queue.takeFrame(frameBytes), isNotNull);
      expect(queue.length, frameBytes);
    });

    test('clear empties the queue and a fresh frame starts clean', () {
      final queue = MicFrameQueue();
      queue.addAll(chunk(11, 1000));
      queue.clear();
      expect(queue.isEmpty, isTrue);
      expect(queue.takeFrame(frameBytes), isNull);
      queue.addAll(chunk(12, 640));
      expect(queue.takeFrame(frameBytes), isNotNull);
    });

    test('stays correct when the backing store compacts and grows', () {
      // Tiny initial capacity forces compaction (tail full, head
      // consumed) and growth (a chunk bigger than the buffer) many
      // times over a long stream.
      final queue = MicFrameQueue(64);
      final chunks = List<Uint8List>.generate(
        50,
        (i) => chunk(100 + i, 37 + (i * 53) % 1500),
      );
      final got = queueFrames(queue, chunks);
      final want = oldAlgorithmFrames(chunks);
      expect(got.length, want.length);
      for (var i = 0; i < want.length; i++) {
        expect(got[i], want[i]);
      }
      expect(queue.length, oldAlgorithmLeftover(chunks));
    });

    test('frame views are 2-byte aligned for Int16List (640-byte consumes)', () {
      final queue = MicFrameQueue();
      queue.addAll(chunk(13, 4096));
      while (queue.length >= frameBytes) {
        final frame = queue.takeFrame(frameBytes)!;
        // Must not throw: Int16List.view requires an even offset.
        final samples = Int16List.view(frame.buffer, frame.offsetInBytes, 320);
        expect(samples.length, 320);
        queue.consume(frameBytes);
      }
    });
  });
}
