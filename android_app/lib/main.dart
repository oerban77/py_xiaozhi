import 'package:flutter/material.dart';

import 'app_theme.dart';
import 'home_screen.dart';
import 'xiaozhi_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final controller = XiaozhiController();
  await controller.initialize();
  runApp(XiaozhiApp(controller: controller));
}

class XiaozhiApp extends StatelessWidget {
  const XiaozhiApp({super.key, required this.controller});

  final XiaozhiController controller;

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Xiaozhi',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
          useMaterial3: true,
          scaffoldBackgroundColor: AppColors.paper,
          colorScheme: ColorScheme.fromSeed(
            seedColor: AppColors.green,
            primary: AppColors.green,
            surface: Colors.white,
            onSurface: AppColors.ink,
          ),
          appBarTheme: const AppBarTheme(
            backgroundColor: AppColors.paper,
            foregroundColor: AppColors.ink,
            elevation: 0,
            centerTitle: false,
          ),
          inputDecorationTheme: InputDecorationTheme(
            filled: true,
            fillColor: Colors.white,
            contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(16),
              borderSide: const BorderSide(color: Color(0xFFDCE5E1)),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(16),
              borderSide: const BorderSide(color: Color(0xFFDCE5E1)),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(16),
              borderSide: const BorderSide(color: AppColors.green, width: 1.5),
            ),
          ),
        ),
        home: HomeScreen(controller: controller),
      );
}