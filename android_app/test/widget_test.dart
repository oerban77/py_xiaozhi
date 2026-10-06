import 'package:flutter_test/flutter_test.dart';
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
    expect(find.text('Mulai percakapan'), findsOneWidget);
  });
}