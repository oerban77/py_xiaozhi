import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/mcp_runtime.dart';

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
    );
    final callResult = callResponse!['result'] as Map<String, dynamic>;
    final content = callResult['content'] as List<dynamic>;
    expect((content.single as Map)['text'], attachedText);
  });

  test('does not offer the attachment reader without a pending attachment', () async {
    final response = await McpRuntime.handle(
      {'jsonrpc': '2.0', 'id': 6, 'method': 'tools/list'},
      disabledModules: <String>{},
    );

    final result = response!['result'] as Map<String, dynamic>;
    final names = (result['tools'] as List<dynamic>)
        .map((tool) => (tool as Map)['name'])
        .toList();
    expect(names, isNot(contains('manage_document')));
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
}