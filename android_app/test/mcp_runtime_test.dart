import 'package:flutter_test/flutter_test.dart';
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
    // src/mcp/tools/documents/service.py, then the raw attachment content.
    expect((content.single as Map)['text'], contains('Attached file: catatan.txt'));
    expect((content.single as Map)['text'], contains('User question: Jelaskan isinya.'));
    expect((content.single as Map)['text'], endsWith(attachedText));
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
}