// Tests for the auto-connect / auto-reconnect state machine and for the
// wake-word -> auto-conversation handoff in XiaozhiController.
//
// The controller's constructor only builds the recorder/player/detector; it does
// not touch the network, so these tests construct it directly and drive the
// pieces that are pure state. connect() itself is not exercised here: it opens a
// real WebSocket, and there is no server to talk to in a host test.
//
// AudioRecorder() is not a plain Dart object: its constructor invokes "create"
// on the record plugin's method channel, and with no plugin registered on the
// host that Future fails with MissingPluginException. The call is
// fire-and-forget, so the error surfaces after the test body has already
// completed ("failed after test completion"). Answering the channel in setUp
// keeps that Future from ever seeing an error.

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/xiaozhi_controller.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  const recordChannel = MethodChannel('com.llfbandit.record/messages');

  setUp(() {
    TestWidgetsFlutterBinding.ensureInitialized();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(recordChannel, (MethodCall call) async {
      // AudioRecorder._create() only awaits the acknowledgement; the recorder is
      // never started in these tests.
      return null;
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(recordChannel, null);
  });

  group('reconnectDelaySeconds', () {
    test('grows exponentially and caps at 30s', () {
      expect(XiaozhiController.reconnectDelaySeconds(1), 2);
      expect(XiaozhiController.reconnectDelaySeconds(2), 4);
      expect(XiaozhiController.reconnectDelaySeconds(3), 6);
      expect(XiaozhiController.reconnectDelaySeconds(15), 30);
      // Never below the 2s floor, even for a bogus attempt number.
      expect(XiaozhiController.reconnectDelaySeconds(0), 2);
      expect(XiaozhiController.reconnectDelaySeconds(-1), 2);
    });
  });

  group('canAutoConnect', () {
    test('is false before the device/client ids exist', () {
      final controller = XiaozhiController();
      // The constructor leaves the identifiers empty; the defaults fill the
      // endpoint and token, so the missing device/client ids are what gates it.
      expect(controller.deviceId, isEmpty);
      expect(controller.clientId, isEmpty);
      expect(controller.canAutoConnect, isFalse);
    });

    test('is true once every field connect() needs is set', () {
      final controller = XiaozhiController()
        ..deviceId = 'device-1'
        ..clientId = 'client-1';
      expect(controller.canAutoConnect, isTrue);
    });

    test('is false again if a required field is cleared', () {
      final controller = XiaozhiController()
        ..deviceId = 'device-1'
        ..clientId = 'client-1';
      expect(controller.canAutoConnect, isTrue);
      controller.endpoint = '';
      expect(controller.canAutoConnect, isFalse);
    });
  });

  group('conversation state', () {
    test('is always continuous; manual mode no longer exists', () {
      final controller = XiaozhiController();
      // There is no manual mode anymore, so this is a constant: every
      // conversation is a continuous realtime listen/speak loop.
      expect(controller.autoConversation, isTrue);
    });

    test('starts idle and derives the status from the device state', () {
      final controller = XiaozhiController();
      expect(controller.deviceState, DeviceState.idle);
      // Disconnected idle is the "not connected yet" status, not a stale label.
      expect(controller.status, 'Belum terhubung');
      expect(controller.primaryActionLabel, 'Mulai bicara');
    });

    test('auto-connect defaults to enabled', () {
      final controller = XiaozhiController();
      expect(controller.autoConnect, isTrue);
    });

    test('isSpeaking tracks the device state, not a separate flag', () {
      // Regression: the tts "start" handler set deviceState to speaking but left
      // the old isSpeaking boolean false, so the player-stream re-arm gate in
      // _handleAudio never opened and no audio reached the speaker -- the live
      // text kept updating while the answer stayed silent. isSpeaking is now
      // derived from deviceState, the way StateManager.is_speaking() reads the
      // enum, so the two can never disagree again.
      final controller = XiaozhiController();
      expect(controller.isSpeaking, isFalse);

      controller.setDeviceStateForTest(DeviceState.speaking);
      expect(controller.isSpeaking, isTrue);

      controller.setDeviceStateForTest(DeviceState.idle);
      expect(controller.isSpeaking, isFalse);

      controller.setDeviceStateForTest(DeviceState.listening);
      expect(controller.isSpeaking, isFalse);
    });
  });

  group('camera facing', () {
    test('normalizes and stores a switch made from the camera screen', () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final controller = XiaozhiController();
      expect(controller.cameraFacing, 'back');

      await controller.setCameraFacing('front');

      expect(controller.cameraFacing, 'front');
    });

    test('ignores an unknown facing instead of storing it', () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final controller = XiaozhiController();

      await controller.setCameraFacing('side');

      expect(controller.cameraFacing, 'back');
    });

    test('skips a redundant update', () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final controller = XiaozhiController();
      var notifications = 0;
      controller.addListener(() => notifications++);

      await controller.setCameraFacing('back');

      expect(notifications, 0);
    });

    test('notifies listeners when the facing actually changes', () async {
      SharedPreferences.setMockInitialValues(<String, Object>{});
      final controller = XiaozhiController();
      var notifications = 0;
      controller.addListener(() => notifications++);

      await controller.setCameraFacing('front');

      expect(notifications, 1);
    });
  });

  group('tts segment joining', () {
    test('appends a segment on a new line unless one already ends a line', () {
      expect(XiaozhiController.appendTtsSegment('', 'Halo'), 'Halo');
      expect(XiaozhiController.appendTtsSegment('Halo', 'semua'), 'Halo\nsemua');
      // A segment that itself starts a newline is joined verbatim.
      expect(XiaozhiController.appendTtsSegment('Halo', '\nsemua'), 'Halo\nsemua');
      // A current text already ending in a newline does not gain a second one.
      expect(XiaozhiController.appendTtsSegment('Halo\n', 'semua'), 'Halo\nsemua');
    });
  });

  group('clean close classification', () {
    // The server taking back the session ("bye bye") must not be treated as a
    // network fault: a fault tears the wake word detector and the mic down and
    // reports an error, a clean close keeps them alive and just goes idle.
    test('1000/1001/1005 are a normal server-side close', () {
      expect(XiaozhiController.isCleanCloseCode(1000), isTrue);
      expect(XiaozhiController.isCleanCloseCode(1001), isTrue);
      expect(XiaozhiController.isCleanCloseCode(1005), isTrue);
    });

    test('an error code or an abrupt reset is a network fault', () {
      expect(XiaozhiController.isCleanCloseCode(1006), isFalse);
      expect(XiaozhiController.isCleanCloseCode(1011), isFalse);
      expect(XiaozhiController.isCleanCloseCode(4000), isFalse);
      // A null code is a TCP reset with no close frame, not a "bye bye".
      expect(XiaozhiController.isCleanCloseCode(null), isFalse);
    });
  });

  // The TOC byte of an Opus packet names the encoder's bandwidth (RFC 6716
  // §3.1), which is what makes "auto sample rate" possible. These values were
  // captured from the Python encoder (src/audio_codecs/opus_codec.py) so the Dart
  // port stays in lockstep with it: a 20ms frame at each rate encodes to exactly
  // these bytes.
  group('parseOpusToc', () {
    test('reads the bandwidth the encoder wrote at each sample rate', () {
      // 8000 Hz -> SILK/NB, 12000 Hz -> SILK/MB, 16000 Hz -> SILK/WB,
      // 24000 Hz -> Hybrid/SWB, 48000 Hz -> CELT/FB. All at 20ms.
      const cases = [
        (8, 'NB', 8000),
        (40, 'MB', 12000),
        (72, 'WB', 16000),
        (104, 'SWB', 24000),
        (248, 'FB', 48000),
      ];
      for (final (toc, bandwidth, rate) in cases) {
        final parsed = XiaozhiController.parseOpusToc(Uint8List.fromList([toc, 0x00]));
        expect(parsed, isNotNull, reason: 'TOC $toc should parse');
        expect(parsed!.bandwidth, bandwidth, reason: 'TOC $toc');
        expect(parsed.frameMs, 20, reason: 'TOC $toc');
        expect(parsed.mode, isNotEmpty, reason: 'TOC $toc');
        expect(XiaozhiController.sampleRateForBandwidth(bandwidth), rate, reason: 'TOC $toc');
      }
    });

    test('a code-3 packet reads the frame count from the second byte', () {
      // 107 == 104 | 0x03: same SWB/20ms config as the 24000 Hz case above, but
      // with code 3, which means the count is in the low 6 bits of byte 1.
      final parsed = XiaozhiController.parseOpusToc(Uint8List.fromList([107, 0x2A]));
      expect(parsed, isNotNull);
      // 0x2A & 0x3F == 42.
      expect(parsed!.frames, 42);
      // The config bits are untouched by the code bits.
      expect(parsed.bandwidth, 'SWB');
      expect(parsed.frameMs, 20);
    });

    test('an empty packet parses to nothing instead of throwing', () {
      expect(XiaozhiController.parseOpusToc(Uint8List(0)), isNull);
    });

    test('a code-3 packet with no length byte falls back to one frame', () {
      // 107 has code 3, so the count would come from byte 1, but that byte is
      // missing and cannot be read; one frame is the safe default rather than
      // crashing the audio path.
      final parsed = XiaozhiController.parseOpusToc(Uint8List.fromList([107]));
      expect(parsed, isNotNull);
      expect(parsed!.frames, 1);
    });

    test('an unknown bandwidth maps to no sample rate', () {
      expect(XiaozhiController.sampleRateForBandwidth('XX'), isNull);
    });
  });
}
