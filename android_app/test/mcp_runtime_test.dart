import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/attachment_text_reader.dart';
import 'package:image/image.dart' as img;
import 'package:py_xiaozhi_android/mcp_runtime.dart';
import 'package:py_xiaozhi_android/reminder_runtime.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  test('lists prayer tools by their registered names', () async {
    final response = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
      disabledModules: <String>{},
    );

    final result = response!['result'] as Map<String, dynamic>;
    final tools = result['tools'] as List<dynamic>;
    final names = tools.map((tool) => (tool as Map)['name']).toList();

    expect(names, contains('prayer_times_today'));
    expect(names, contains('prayer_times_monthly'));
    expect(names, contains('get_weather'));
    expect(names, contains('self.indonesia_holiday_query'));
    expect(names, contains('get_news'));
    expect(names, contains('web_search'));
    expect(names, contains('read_article'));
    expect(names, contains('device_status'));
    expect(names, contains('device_control'));
    expect(names, contains('discover_devices'));
    expect(names, contains('take_photo'));
    expect(names, contains('qrcode_read_file'));
    expect(names, contains('add_reminder_in'));
    expect(names, contains('add_prayer_reminders'));
    expect(names, contains('list_reminders'));
    expect(names, contains('music_player.search_and_play'));
    expect(names, contains('music_player.pause'));
    expect(names, contains('music_player.get_status'));
    expect(names, contains('music_player.get_lyrics'));
    expect(names, contains('self.audio_speaker.set_volume'));
    expect(names, contains('self.audio_speaker.get_volume'));
    expect(names, contains('self.audio_speaker.get_volume_status'));
    expect(names, contains('self.audio_speaker.set_muted'));
    expect(names, contains('self.audio_speaker.toggle_mute'));
    expect(names, contains('self.audio_speaker.get_muted'));
    expect(names, contains('self.camera.switch'));
    expect(names, contains('self.camera.get_facing'));
  });

  test('hides disabled prayer module tools', () async {
    final response = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
      disabledModules: <String>{'prayer'},
    );

    final result = response!['result'] as Map<String, dynamic>;
    final names = (result['tools'] as List<dynamic>)
      .map((tool) => (tool as Map)['name'])
      .toList();
    expect(names, isNot(contains('prayer_times_today')));
    expect(names, contains('get_weather'));
  });

  test('hides all tools for a disabled module only', () async {
    final response = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'},
      disabledModules: <String>{'websearch'},
    );

    final result = response!['result'] as Map<String, dynamic>;
    final names = (result['tools'] as List<dynamic>)
        .map((tool) => (tool as Map)['name'])
        .toList();
    expect(names, isNot(contains('web_search')));
    expect(names, isNot(contains('read_article')));
    expect(names, contains('prayer_times_today'));
  });

  test('includes LWT-only devices and all reported relays in discovery', () {
    final devices = McpRuntime.completeSmartHomeDiscovery(
      discovered: [
        {
          'topic': 'configured-light',
          'powerCmd': 'POWER',
          'name': 'Configured light',
          'room': 'living room',
          'type': 'light',
        },
      ],
      online: {
        'configured-light': 'Online',
        'lwt-light': 'Online',
        'multi-switch': 'Online',
        'offline': 'Offline',
      },
      states: {
        'multi-switch/POWER1': 'ON',
        'multi-switch/POWER2': 'OFF',
        'multi-switch/POWER3': 'ON',
      },
    );

    expect(devices, hasLength(5));
    expect(
      devices.map((device) => '${device['topic']}/${device['powerCmd']}'),
      containsAll([
        'configured-light/POWER',
        'lwt-light/POWER',
        'multi-switch/POWER1',
        'multi-switch/POWER2',
        'multi-switch/POWER3',
      ]),
    );
    expect(devices.map((device) => device['topic']), isNot(contains('offline')));
  });

  test('keeps reminder data even when Android scheduling fails', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});
    // No clock app is available to handle SET_ALARM, so the launch fails. The
    // record is still stored so the reminder can be re-scheduled later.
    ReminderRuntime.launchAlarm = (extras) async {
      throw StateError('No Activity found to handle Intent '
          '{ act=android.intent.action.SET_ALARM }');
    };

    final createdAt = DateTime.now().add(const Duration(minutes: 5)).toIso8601String();
    final result = await ReminderRuntime.call('add_reminder', {
      'title': 'Tes reminder alarm',
      'message': 'Uji alarm Android',
      'mode': 'once',
      'datetime': createdAt,
    });

    expect(result, contains('saved locally'));

    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString('android_mcp_reminders');
    expect(raw, isNotNull);
    expect(raw, contains('Tes reminder alarm'));

    ReminderRuntime.launchAlarm = null;
  });

  test('maps reminder modes to AlarmClock.EXTRA_DAYS weekdays', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});

    final captured = <Map<String, dynamic>>[];
    ReminderRuntime.launchAlarm = (extras) async {
      captured.add(extras);
    };

    await ReminderRuntime.call('add_reminder', {
      'title': 'Alarm harian',
      'mode': 'daily',
      'time': '07:30',
    });
    await ReminderRuntime.call('add_reminder', {
      'title': 'Alarm hari kerja',
      'mode': 'workday',
      'time': '08:00',
    });
    await ReminderRuntime.call('add_reminder', {
      'title': 'Alarm mingguan',
      'mode': 'weekly',
      'time': '09:00',
      'weekday': 'jumat',
    });

    expect(captured, hasLength(3));
    // Calendar.SUNDAY=1 .. Calendar.SATURDAY=7, so daily is every day and
    // workday is Monday(2)..Friday(6).
    expect(captured[0]['android.intent.extra.alarm.DAYS'], [1, 2, 3, 4, 5, 6, 7]);
    expect(captured[1]['android.intent.extra.alarm.DAYS'], [2, 3, 4, 5, 6]);
    // 'jumat' is Indonesian for Friday, which is Calendar.FRIDAY=6.
    expect(captured[2]['android.intent.extra.alarm.DAYS'], [6]);
    expect(captured[2]['android.intent.extra.alarm.HOUR'], 9);
    expect(captured[2]['android.intent.extra.alarm.MINUTES'], 0);
    // "Langsung simpan": the clock app stores the alarm without opening a UI.
    expect(captured[2]['android.intent.extra.alarm.SKIP_UI'], isTrue);
    // The alarm sound configured in the settings screen is forwarded verbatim.
    expect(captured[2]['android.intent.extra.alarm.RINGTONE'],
        ReminderRuntime.defaultAlarmSoundUri);

    ReminderRuntime.launchAlarm = null;
  });

  test('saves a new alarm sound even when a reminder has expired', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    final expired = DateTime.now().subtract(const Duration(hours: 1)).toIso8601String();
    SharedPreferences.setMockInitialValues({
      'android_mcp_reminders': jsonEncode([
        {
          'id': 1,
          'title': 'Alarm kedaluwarsa',
          'message': 'Alarm kedaluwarsa',
          'mode': 'once',
          'datetime': expired,
          'enabled': true,
        },
        {
          'id': 2,
          'title': 'Alarm harian',
          'message': 'Alarm harian',
          'mode': 'daily',
          'time': '07:30',
          'enabled': true,
        },
      ]),
    });

    final captured = <Map<String, dynamic>>[];
    ReminderRuntime.launchAlarm = (extras) async {
      captured.add(extras);
    };

    // The expired one-shot has no future trigger, so re-scheduling it is
    // skipped rather than aborting the save; the daily reminder still picks up
    // the new sound.
    await ReminderRuntime.setAlarmSound(
      uri: 'content://media/external/audio/media/42',
      name: 'Nada dering kustom',
    );

    expect(captured, hasLength(1));
    expect(captured.single['android.intent.extra.alarm.MESSAGE'], 'Alarm harian');
    expect(
      captured.single['android.intent.extra.alarm.RINGTONE'],
      'content://media/external/audio/media/42',
    );

    final (uri, name) = await ReminderRuntime.getAlarmSound();
    expect(uri, 'content://media/external/audio/media/42');
    expect(name, 'Nada dering kustom');

    ReminderRuntime.launchAlarm = null;
  });

  test('saves a new alarm sound even when one launch fails', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({
      'android_mcp_reminders': jsonEncode([
        {
          'id': 1,
          'title': 'Alarm pertama',
          'message': 'Alarm pertama',
          'mode': 'daily',
          'time': '07:30',
          'enabled': true,
        },
        {
          'id': 2,
          'title': 'Alarm kedua',
          'message': 'Alarm kedua',
          'mode': 'daily',
          'time': '08:00',
          'enabled': true,
        },
      ]),
    });

    var launchCount = 0;
    final captured = <Map<String, dynamic>>[];
    ReminderRuntime.launchAlarm = (extras) async {
      launchCount++;
      // The first re-scheduled reminder hits an unavailable clock app; the
      // second must still be launched with the new sound.
      if (launchCount == 1) throw StateError('No Activity found to handle Intent');
      captured.add(extras);
    };

    await ReminderRuntime.setAlarmSound(
      uri: 'content://media/external/audio/media/43',
      name: 'Nada dering kustom',
    );

    expect(captured, hasLength(1));
    expect(captured.single['android.intent.extra.alarm.MESSAGE'], 'Alarm kedua');
    expect(
      captured.single['android.intent.extra.alarm.RINGTONE'],
      'content://media/external/audio/media/43',
    );

    // The preference is stored even though one reminder could not be re-scheduled.
    final (uri, name) = await ReminderRuntime.getAlarmSound();
    expect(uri, 'content://media/external/audio/media/43');
    expect(name, 'Nada dering kustom');

    ReminderRuntime.launchAlarm = null;
  });

  test('rejects reminder modes the clock app cannot express', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({});
    ReminderRuntime.launchAlarm = (extras) async {};

    await expectLater(
      ReminderRuntime.call('add_reminder', {
        'title': 'Alarm per jam',
        'mode': 'hourly',
        'time': '10:00',
      }),
      throwsA(isA<ArgumentError>()),
    );

    ReminderRuntime.launchAlarm = null;
  });

  test('offers and returns pending long-text attachment content', () async {
    const attachedText = 'Tolong jelaskan instruksi panjang ini dan ikuti permintaannya.';
    final listResponse = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/list'},
      disabledModules: <String>{'documents'},
      pendingTextAttachment: attachedText,
    );
    final listResult = listResponse!['result'] as Map<String, dynamic>;
    final listedTools = listResult['tools'] as List<dynamic>;
    expect(listedTools.map((tool) => (tool as Map)['name']), contains('manage_document'));
    expect(listedTools.map((tool) => (tool as Map)['name']), isNot(contains('take_photo')));

    final callResponse = await McpRuntime.handle(
      {
        'jsonrpc': '2.0',
        'id': 5,
        'method': 'tools/call',
        'params': {
          'name': 'manage_document',
          'arguments': {'action': 'read'},
        },
      },
      disabledModules: <String>{'documents'},
      pendingTextAttachment: attachedText,
      pendingTextQuestion: 'Jelaskan isinya.',
      pendingAttachmentName: 'catatan.txt',
    );
    final callResult = callResponse!['result'] as Map<String, dynamic>;
    final content = callResult['content'] as List<dynamic>;
    // The question hint is prepended like document_manage's question_hint in
    // src/mcp/tools/documents/service.py, then the chunked attachment content.
    // The text is short enough to be a single chunk, so the read also reports the
    // end of the document rather than ending with the raw content.
    expect((content.single as Map)['text'], contains('Attached file: catatan.txt'));
    expect((content.single as Map)['text'], contains('User question: Jelaskan isinya.'));
    expect((content.single as Map)['text'], contains(attachedText));
    expect((content.single as Map)['text'], endsWith('[Read chunk 1; end of document.]'));
  });

  test('offers the attachment reader before an attachment is sent', () async {
    final response = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 6, 'method': 'tools/list'},
      disabledModules: <String>{},
    );

    final result = response!['result'] as Map<String, dynamic>;
    final names = (result['tools'] as List<dynamic>)
        .map((tool) => (tool as Map)['name'])
        .toList();
    expect(names, contains('manage_document'));
  });

  test('promotes take_photo with the attached-image banner', () async {
    final response = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 7, 'method': 'tools/list'},
      disabledModules: <String>{},
      pendingImageAttachment: Uint8List.fromList(<int>[1, 2, 3]),
      pendingImageQuestion: 'analisa',
      pendingAttachmentName: 'struk.jpg',
    );

    final result = response!['result'] as Map<String, dynamic>;
    final tools = result['tools'] as List<dynamic>;
    final takePhoto = tools.firstWhere(
      (tool) => (tool as Map)['name'] == 'take_photo',
    ) as Map<String, dynamic>;
    final description = takePhoto['description'] as String;
    expect(description, contains('ATTACHED MESSAGE'));
    expect(description, contains('struk.jpg'));
    expect(description, contains('analisa gambar'));
  });

  test('routes OCR-targeted images through the attachment reader', () async {
    final response = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 8, 'method': 'tools/list'},
      disabledModules: <String>{},
      pendingImageAttachment: Uint8List.fromList(<int>[1, 2, 3]),
      pendingImageAsDocument: true,
      pendingImageQuestion: 'baca tulisan di gambar',
      pendingAttachmentName: 'nota.jpg',
    );

    final result = response!['result'] as Map<String, dynamic>;
    final tools = result['tools'] as List<dynamic>;
    final names = tools.map((tool) => (tool as Map)['name']).toList();
    expect(names, contains('manage_document'));
    expect(names, isNot(contains('take_photo')));
    final attachmentReader = tools.first as Map<String, dynamic>;
    expect(attachmentReader['description'], contains('OCR'));
    expect(attachmentReader['description'], contains('nota.jpg'));
  });

  test('rejects stale take_photo calls while a text attachment is pending', () async {
    final response = await McpRuntime.handle(
      {
        'jsonrpc': '2.0',
        'id': 9,
        'method': 'tools/call',
        'params': {
          'name': 'take_photo',
          'arguments': {'question': 'read the attachment'},
        },
      },
      disabledModules: <String>{},
      pendingTextAttachment: 'Isi PDF yang harus dibaca.',
    );

    final result = response!['result'] as Map<String, dynamic>;
    expect(result['isError'], isTrue);
    final content = result['content'] as List<dynamic>;
    expect((content.single as Map)['text'], contains('manage_document'));
  });

  test('serves a pending PDF in page chunks and lets the model continue', () async {
    final pdf = PdfExtraction(
      List<String>.generate(9, (index) => 'Isi halaman ${index + 1}'),
    );

    final first = await McpRuntime.handle(
      {
        'jsonrpc': '2.0',
        'id': 10,
        'method': 'tools/call',
        'params': {
          'name': 'manage_document',
          'arguments': {'action': 'read'},
        },
      },
      disabledModules: <String>{},
      pendingPdf: pdf,
      pendingTextQuestion: 'Baca semuanya.',
      pendingAttachmentName: 'dokumen.pdf',
    );
    var content = (first!['result'] as Map<String, dynamic>)['content'] as List<dynamic>;
    var text = (content.single as Map)['text'] as String;
    expect(text, contains('Isi halaman 1'));
    expect(text, contains('Isi halaman 4'));
    expect(text, isNot(contains('Isi halaman 5')));
    expect(text, contains('Read pages 1-4 of 9.'));
    expect(text, contains('Continue with page_start=5, page_end=8.'));

    // The attachment is still pending, so the model can continue without a
    // re-upload.
    final next = await McpRuntime.handle(
      {
        'jsonrpc': '2.0',
        'id': 11,
        'method': 'tools/call',
        'params': {
          'name': 'manage_document',
          'arguments': {'action': 'read', 'page_start': 5, 'page_end': 8},
        },
      },
      disabledModules: <String>{},
      pendingPdf: pdf,
      pendingAttachmentName: 'dokumen.pdf',
    );
    content = (next!['result'] as Map<String, dynamic>)['content'] as List<dynamic>;
    text = (content.single as Map)['text'] as String;
    expect(text, contains('Isi halaman 5'));
    expect(text, contains('Isi halaman 8'));
    expect(text, isNot(contains('Isi halaman 4')));
    expect(text, contains('Continue with page_start=9, page_end=9.'));
  });

  test('jumps to the page the model asks for in a pending PDF', () async {
    final pdf = PdfExtraction(
      List<String>.generate(12, (index) => 'Isi halaman ${index + 1}'),
    );

    final response = await McpRuntime.handle(
      {
        'jsonrpc': '2.0',
        'id': 12,
        'method': 'tools/call',
        'params': {
          'name': 'manage_document',
          'arguments': {'action': 'read', 'page_start': 10, 'page_end': 10},
        },
      },
      disabledModules: <String>{},
      pendingPdf: pdf,
      pendingAttachmentName: 'dokumen.pdf',
    );
    final content = (response!['result'] as Map<String, dynamic>)['content'] as List<dynamic>;
    final text = (content.single as Map)['text'] as String;
    expect(text, contains('Isi halaman 10'));
    expect(text, isNot(contains('Isi halaman 9')));
    expect(text, isNot(contains('Isi halaman 11')));
    expect(text, contains('Read pages 10-10 of 12.'));
  });

  test('lists manage_document and hides take_photo while a PDF is pending',
      () async {
    final response = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 13, 'method': 'tools/list'},
      disabledModules: <String>{},
      pendingPdf: const PdfExtraction(<String>['Isi halaman 1']),
      pendingAttachmentName: 'dokumen.pdf',
    );

    final result = response!['result'] as Map<String, dynamic>;
    final names = (result['tools'] as List<dynamic>)
        .map((tool) => (tool as Map)['name'])
        .toList();
    expect(names, contains('manage_document'));
    expect(names, isNot(contains('take_photo')));
  });


  test('generic MQTT discovery covers HA, Zigbee2MQTT, Shelly and generic topics', () {
    final devices = McpRuntime.buildSmartHomeDiscovery(
      tasmotaDiscovered: [
        {
          'topic': 'sonoff-tengah',
          'powerCmd': 'POWER',
          'name': 'Tengah',
          'room': 'unknown',
          'type': 'light',
        },
      ],
      online: {'sonoff-tengah': 'Online'},
      states: {},
      haConfigs: {
        'homeassistant/light/dapur/lampu/config': {
          'name': 'Lampu Dapur',
          'state_topic': 'ha/dapur/state',
          'command_topic': 'ha/dapur/set',
        },
      },
      z2mEntries: {
        'z-lamp': {
          'friendly_name': 'z-lamp',
          'definition': {
            'exposes': [
              {
                'type': 'light',
                'features': [
                  {'property': 'state'},
                ],
              },
            ],
          },
        },
      },
      shellyRelays: {'shelly1-abc/0': 'on'},
      shellyModels: {'shelly1-abc': 'Shelly1'},
      shellyGen2States: {'shellyplus1pm-abc/status/switch:0': '{"output": true}'},
      genericTopics: {
        'vacuum/status': 'Mengisi daya',
        'vacuum/battery': '100',
        'xiaomi/cam360_2k/status': 'Online - 192.168.0.10',
      },
    );
    final protocols = devices.map((d) => d['protocol']).toSet();
    expect(
      protocols,
      containsAll(['tasmota', 'homeassistant', 'zigbee2mqtt', 'shelly', 'generic']),
    );
    final vacuum = devices.firstWhere((d) => d['stateTopic'] == 'vacuum/status');
    expect(vacuum['type'], 'sensor');
    expect(vacuum['controllable'], isFalse);
    expect(vacuum['commandTopic'], '');
    final ha = devices.firstWhere((d) => d['protocol'] == 'homeassistant');
    expect(ha['controllable'], isTrue);
    expect(ha['commandTopic'], 'ha/dapur/set');
    final z2m = devices.firstWhere((d) => d['protocol'] == 'zigbee2mqtt');
    expect(z2m['commandTopic'], 'zigbee2mqtt/z-lamp/set');
    final gen2 = devices.firstWhere(
      (d) => d['stateTopic'] == 'shellyplus1pm-abc/status/switch:0',
    );
    expect(gen2['commandTopic'], 'shellyplus1pm-abc/rpc');
  });

  test('configured generic devices keep their command topic in discovery merge', () {
    final configured = McpRuntime.parseSmartHomeDevicesForTest(
      '[{"state_topic": "vacuum/status", "command_topic": "vacuum/command/clean", "name": "Vacuum Clean", "type": "switch", "payload_on": "START", "payload_off": "STOP"}]',
    );
    expect(configured, hasLength(1));
    expect(configured.first['protocol'], 'generic');
    expect(configured.first['controllable'], isTrue);
    expect(configured.first['payloadOn'], 'START');
    expect(configured.first['payloadOff'], 'STOP');
  });

  group('downscaleImageForVision', () {
    // Build a large photo-like JPEG with the image package itself, then
    // verify the helper shrinks it to the 1280 px cap and stays a JPEG.
    Uint8List makePhoto(int width, int height) {
      final image = img.Image(width: width, height: height);
      // A gradient so the JPEG is not trivially compressible.
      for (var y = 0; y < height; y += 7) {
        for (var x = 0; x < width; x += 7) {
          image.setPixelRgb(x, y, x % 256, y % 256, (x + y) % 256);
        }
      }
      return Uint8List.fromList(img.encodeJpg(image, quality: 95));
    }

    test('shrinks a large landscape photo to the 2560 px cap', () {
      final original = makePhoto(4000, 3000);
      final shrunk = McpRuntime.downscaleImageForVision(original);
      final decoded = img.decodeImage(shrunk)!;
      expect(decoded.width, 2560);
      expect(decoded.height, 1920);
      expect(shrunk.length, lessThan(original.length));
    });

    test('shrinks a large portrait photo by its height', () {
      final original = makePhoto(3000, 4000);
      final shrunk = McpRuntime.downscaleImageForVision(original);
      final decoded = img.decodeImage(shrunk)!;
      expect(decoded.width, 1920);
      expect(decoded.height, 2560);
    });

    test('keeps full resolution when the question asks to read text', () {
      final original = makePhoto(4000, 3000);
      final result = McpRuntime.downscaleImageForVision(
        original,
        keepFullRes: true,
      );
      expect(result, original);
    });

    test('returns a small image unchanged', () {
      final original = makePhoto(800, 600);
      final result = McpRuntime.downscaleImageForVision(original);
      expect(result, original);
    });

    test('returns undecodable bytes unchanged', () {
      final garbage = Uint8List.fromList([0, 1, 2, 3, 4, 5]);
      expect(McpRuntime.downscaleImageForVision(garbage), garbage);
    });
  });

  group('isTextReadingQuestion', () {
    test('detects Indonesian read-text phrasings', () {
      expect(McpRuntime.isTextReadingQuestion('Bacakan tulisan di gambar ini'), isTrue);
      expect(McpRuntime.isTextReadingQuestion('Apa yang tertulis di struk ini?'), isTrue);
      expect(McpRuntime.isTextReadingQuestion('Tolong baca dokumen ini'), isTrue);
      expect(McpRuntime.isTextReadingQuestion('Transkrip nota ini'), isTrue);
    });

    test('detects English read-text phrasings', () {
      expect(McpRuntime.isTextReadingQuestion('Read the text in this image'), isTrue);
      expect(McpRuntime.isTextReadingQuestion('OCR this receipt'), isTrue);
    });

    test('detects QR and barcode questions', () {
      expect(McpRuntime.isTextReadingQuestion('Scan QR code ini'), isTrue);
      expect(McpRuntime.isTextReadingQuestion('Apa isi kode QR ini?'), isTrue);
      expect(McpRuntime.isTextReadingQuestion('Baca barcode ini'), isTrue);
    });

    test('does not flag a plain description request', () {
      expect(McpRuntime.isTextReadingQuestion('Jelaskan isi gambar ini'), isFalse);
      expect(McpRuntime.isTextReadingQuestion('Ada apa di foto ini?'), isFalse);
      expect(McpRuntime.isTextReadingQuestion('Describe this photo'), isFalse);
    });
  });
}
