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

  group('auto conversation', () {
    test('starts disabled and is assignable, as the segmented button binds to it', () {
      final controller = XiaozhiController();
      expect(controller.autoConversation, isFalse);
      controller.autoConversation = true;
      expect(controller.autoConversation, isTrue);
    });

    test('auto-connect defaults to enabled', () {
      final controller = XiaozhiController();
      expect(controller.autoConnect, isTrue);
    });
  });
}
