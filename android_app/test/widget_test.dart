import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/chat_message.dart';
import 'package:py_xiaozhi_android/emotion_display.dart';
import 'package:py_xiaozhi_android/main.dart';
import 'package:py_xiaozhi_android/xiaozhi_controller.dart';

void main() {
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
  });
}