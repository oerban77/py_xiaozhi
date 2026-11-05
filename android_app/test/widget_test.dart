import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/chat_message.dart';
import 'package:py_xiaozhi_android/emotion_display.dart';
import 'package:py_xiaozhi_android/main.dart';
import 'package:py_xiaozhi_android/xiaozhi_controller.dart';

/// Widget tests run on an 800x600 surface by default, which is
/// landscape — but these tests cover the portrait phone UI (subtitle
/// area, greeting rules, compact live-text area). Pin a portrait
/// phone surface so HomeScreen builds its portrait branch.
void setPortraitSurface(WidgetTester tester) {
  tester.view.physicalSize = const Size(1080, 2400);
  tester.view.devicePixelRatio = 3.0;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

void main() {
  test('separates consecutive TTS text segments with a newline', () {
    expect(
      XiaozhiController.appendTtsSegment('Kalimat pertama.', 'Kalimat kedua.'),
      'Kalimat pertama.\nKalimat kedua.',
    );
  });

  testWidgets('shows the Xiaozhi conversation screen', (tester) async {
    setPortraitSurface(tester);
    final controller = XiaozhiController();
    await tester.pumpWidget(XiaozhiApp(
      controller: controller,
      skipPermissionGate: true,
      skipAutoConnect: true,
    ));

    expect(find.text('Xiaozhi'), findsOneWidget);
    expect(find.byType(EmotionDisplay), findsOneWidget);
    // Subtitles hide when idle: no greeting placeholder is shown until
    // the assistant actually speaks.
    expect(find.text('Ada yang bisa aku bantu hari ini?'), findsNothing);
  });

  testWidgets('keeps the server emotion visible while TTS text is displayed', (tester) async {
    setPortraitSurface(tester);
    final controller = XiaozhiController()
      ..emotion = 'happy'
      ..subtitleText = 'Jawaban sedang dibacakan.'
      ..subtitleVisible = true
      ..messages.add(ChatMessage(text: 'Jawaban sedang dibacakan.', isUser: false));

    await tester.pumpWidget(XiaozhiApp(
      controller: controller,
      skipPermissionGate: true,
      skipAutoConnect: true,
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
    setPortraitSurface(tester);
    final controller = XiaozhiController()
      ..subtitleText = List.filled(40, 'Teks panjang').join('\n')
      ..subtitleVisible = true;
    await tester.pumpWidget(XiaozhiApp(
      controller: controller,
      skipPermissionGate: true,
      skipAutoConnect: true,
    ));

    expect(tester.getSize(find.byKey(const ValueKey('live-text-area'))).height, lessThanOrEqualTo(160));
    expect(find.text(controller.subtitleText), findsOneWidget);
  });

  testWidgets('left swipe on main screen navigates to debug screen', (tester) async {
    setPortraitSurface(tester);
    final controller = XiaozhiController();
    await tester.pumpWidget(XiaozhiApp(
      controller: controller,
      skipPermissionGate: true,
      skipAutoConnect: true,
    ));

    // Verify we're on the main screen
    expect(find.text('Xiaozhi'), findsOneWidget);

    // Simulate left swipe
    await tester.drag(find.text('Xiaozhi'), const Offset(-300, 0));
    await tester.pumpAndSettle();

    // Verify debug screen is now visible
    expect(find.text('DEBUG SCREEN'), findsOneWidget);
  });

  testWidgets('right swipe on debug screen navigates back to main screen', (tester) async {
    setPortraitSurface(tester);
    final controller = XiaozhiController();
    await tester.pumpWidget(XiaozhiApp(
      controller: controller,
      skipPermissionGate: true,
      skipAutoConnect: true,
    ));

    // First navigate to debug screen via left swipe
    await tester.drag(find.text('Xiaozhi'), const Offset(-300, 0));
    await tester.pumpAndSettle();

    // Verify debug screen is visible
    expect(find.text('DEBUG SCREEN'), findsOneWidget);

    // Simulate right swipe to go back
    await tester.drag(find.text('DEBUG SCREEN'), const Offset(300, 0));
    await tester.pumpAndSettle();

    // Verify we're back on the main screen
    expect(find.text('Xiaozhi'), findsOneWidget);
  });
}