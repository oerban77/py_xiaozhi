import 'dart:math' as math;
import 'dart:typed_data';

/// Fixed-size circular buffer of recent mic audio (16 kHz mono floats,
/// last 1 s = [capacity] samples) for the speech gate.
///
/// Replaces a growing `List<double>` that was trimmed with
/// removeRange(0, excess) on every mic chunk — an O(n) shift of up to
/// 16000 doubles ~50 times a second (~800k element moves/sec) on the
/// hot mic path. Writes here are O(1) per sample and never shift.
///
/// Readers that need the samples in time order use [snapshot], which
/// copies at most [capacity] doubles — O(n), but only when actually
/// read (wake-word hits, barge-in checks), not on every mic chunk.
/// [recentRms] reads the ring in place without a copy.
class MicRingBuffer {
  MicRingBuffer({this.capacity = 16000})
      : assert(capacity > 0),
        _data = Float64List(capacity);

  /// Samples held, at most [capacity] (last 1 s at 16 kHz).
  final int capacity;

  final Float64List _data;

  /// Index of the next sample to overwrite (= the oldest sample once full).
  int _write = 0;

  /// Samples currently held, <= [capacity].
  int _length = 0;

  int get length => _length;

  bool get isEmpty => _length == 0;

  void add(double sample) {
    _data[_write] = sample;
    _write = (_write + 1) % capacity;
    if (_length < capacity) _length++;
  }

  /// The buffered samples in time order (oldest first), as a
  /// contiguous list. Empty when nothing was written yet.
  List<double> snapshot() {
    if (_length == 0) return <double>[];
    if (_length < capacity) return _data.sublist(0, _length);
    // Full: the oldest sample sits at _write (next to be overwritten).
    final out = List<double>.filled(capacity, 0.0);
    final tail = capacity - _write;
    out.setRange(0, tail, _data, _write);
    out.setRange(tail, capacity, _data, 0);
    return out;
  }

  /// RMS of the buffered samples, read in place (no copy).
  double recentRms() {
    if (_length == 0) return 0.0;
    var sumSq = 0.0;
    if (_length < capacity) {
      for (var i = 0; i < _length; i++) {
        final v = _data[i];
        sumSq += v * v;
      }
    } else {
      // Full: walk the ring from the oldest sample (_write) around.
      // Order is irrelevant for RMS, but the two-segment walk keeps
      // the access pattern simple and cache-friendly.
      for (var i = _write; i < capacity; i++) {
        final v = _data[i];
        sumSq += v * v;
      }
      for (var i = 0; i < _write; i++) {
        final v = _data[i];
        sumSq += v * v;
      }
    }
    return math.sqrt(sumSq / _length);
  }
}

/// Byte queue for the mic uplink framing: mic chunks arrive in
/// arbitrary sizes and are sent as fixed 20 ms frames (640 bytes).
///
/// Replaces a `List<int>` that framed with
/// `Uint8List.fromList(list.take(n).toList())` (double allocation per
/// frame) + `removeRange(0, n)` (O(n) shift per frame). This keeps one
/// backing store and a read offset: [addAll] appends at the tail,
/// [takeFrame] returns a view of the next frame without copying, and
/// [consume] advances the read offset. The unconsumed bytes are
/// compacted to the front only when the tail runs out of room (rare),
/// and the buffer grows (doubling) if a single chunk exceeds it.
///
/// The frame view returned by [takeFrame] is valid only until the
/// next [addAll]/[clear] — callers must read it (RMS, Opus encode,
/// both synchronous) and then [consume] it before appending more.
/// That is safe: SimpleOpusEncoder.encode copies the samples into
/// native memory synchronously and never retains the input.
///
/// Consumes are always the full frame size, so the read offset stays
/// a multiple of the frame size (640, even) and Int16List.view over
/// the frame is always 2-byte aligned.
class MicFrameQueue {
  MicFrameQueue([int initialCapacity = 8192])
      : assert(initialCapacity > 0),
        _buf = Uint8List(initialCapacity);

  Uint8List _buf;

  /// Offset of the first unconsumed byte.
  int _start = 0;

  /// Unconsumed bytes.
  int _length = 0;

  int get length => _length;

  bool get isEmpty => _length == 0;

  void addAll(Uint8List data) {
    if (data.isEmpty) return;
    _ensureCapacity(_length + data.length);
    _buf.setRange(_start + _length, _start + _length + data.length, data);
    _length += data.length;
  }

  /// A view of the next [count] bytes, or null when fewer than [count]
  /// bytes are buffered. See the class doc: the view is only valid
  /// until the next [addAll]/[clear]; consume it first.
  Uint8List? takeFrame(int count) {
    if (_length < count) return null;
    return Uint8List.view(_buf.buffer, _buf.offsetInBytes + _start, count);
  }

  /// Drops the first [count] buffered bytes. Call after the frame view
  /// has been fully read.
  void consume(int count) {
    assert(count <= _length);
    _start += count;
    _length -= count;
    if (_length == 0) _start = 0;
  }

  void clear() {
    _start = 0;
    _length = 0;
  }

  void _ensureCapacity(int needed) {
    // Fits at the tail without moving anything.
    if (_start + needed <= _buf.length) return;
    // Compact the unconsumed bytes to the front when that frees
    // enough room; otherwise grow (doubling).
    if (needed <= _buf.length && _start > 0) {
      _buf.setRange(0, _length, _buf, _start);
      _start = 0;
      return;
    }
    var size = _buf.length;
    while (size < needed) {
      size *= 2;
    }
    final grown = Uint8List(size);
    grown.setRange(0, _length, _buf, _start);
    _buf = grown;
    _start = 0;
  }
}
