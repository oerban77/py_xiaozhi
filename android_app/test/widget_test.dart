import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/chat_message.dart';
import 'package:py_xiaozhi_android/emotion_display.dart';
import 'package:py_xiaozhi_android/main.dart';
import 'package:py_xiaozhi_android/xiaozhi_controller.dart';

void main() {
  test('separates consecutive TTS text segments with a newline', () {
    expect(
      XiaozhiController.appendTtsSegment('Kalimat pertama.', 'Kalimat kedua.'),
      'Kalimat pertama.\nKalimat kedua.',
    );
  });

  testWidgets('shows the Xiaozhi conversation screen', (tester) async {
    final controller = XiaozhiController();
    await tester.pumpWidget(XiaozhiApp(
      controller: controller,
      skipPermissionGate: true,
    ));

    expect(find.text('Xiaozhi'), findsOneWidget);
    expect(find.byType(EmotionDisplay), findsOneWidget);
    expect(find.text('Ada yang bisa aku bantu hari ini?'), findsOneWidget);
  });

  testWidgets('keeps the server emotion visible while TTS text is displayed', (tester) async {
    final controller = XiaozhiController()
      ..emotion = 'happy'
      ..liveText = 'Jawaban sedang dibacakan.'
      ..messages.add(ChatMessage(text: 'Jawaban sedang dibacakan.', isUser: false));

    await tester.pumpWidget(XiaozhiApp(
      controller: controller,
      skipPermissionGate: true,
    ));

    expect(find.byType(EmotionDisplay), findsOneWidget);
    expect(find.text('Jawaban sedang dibacakan.'), findsOneWidget);
    // The emotion size is derived from the available conversation area
    // (min(240, min(width * 0.78, height * 0.9))), so on the 800x600 test
    // surface it lands near 174. Assert it stays a large, visible widget
    // instead of pinning it to the old fixed 176 cap.
    expect(tester.getSize(find.byType(EmotionDisplay)).width, greaterThan(160));
  });

  testWidgets('keeps the live text area compact for long responses', (tester) async {
    final controller = XiaozhiController()..liveText = List.filled(40, 'Teks panjang').join('\n');
    await tester.pumpWidget(XiaozhiApp(
      controller: controller,
      skipPermissionGate: true,
    ));

    expect(tester.getSize(find.byKey(const ValueKey('live-text-area'))).height, lessThanOrEqualTo(160));
    expect(find.text(controller.liveText), findsOneWidget);
  });
}