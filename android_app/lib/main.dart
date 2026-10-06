import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_theme.dart';
import 'home_screen.dart';
import 'permission_gate_screen.dart';
import 'permission_manager.dart';
import 'xiaozhi_controller.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await SystemChrome.setPreferredOrientations([
    DeviceOrientation.landscapeLeft,
    DeviceOrientation.landscapeRight,
  ]);

  final controller = XiaozhiController();
  await controller.initialize();
  runApp(XiaozhiApp(controller: controller));
}

class XiaozhiApp extends StatefulWidget {
  const XiaozhiApp({
    super.key,
    required this.controller,
    this.skipPermissionGate = false,
  });

  final XiaozhiController controller;

  /// Allows widget tests and other controlled in-app bootstrap flows to skip the
  /// first-run permission gate without changing the default production behavior.
  final bool skipPermissionGate;

  @override
  State<XiaozhiApp> createState() => _XiaozhiAppState();
}

class _XiaozhiAppState extends State<XiaozhiApp> {
  // Null while the permission check is still in flight; true once every required
  // permission is granted (or the user skipped the gate).
  bool? _permissionsGranted;

  @override
  void initState() {
    super.initState();
    if (widget.skipPermissionGate) {
      _permissionsGranted = true;
      return;
    }
    unawaited(_checkPermissions());
  }

  Future<void> _checkPermissions() async {
    final manager = const PermissionManager();
    final granted = await manager.allRequiredGranted;
    if (!mounted) return;
    setState(() => _permissionsGranted = granted);
  }

  void _onPermissionsGranted() {
    if (!mounted) return;
    setState(() => _permissionsGranted = true);
  }

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
        // While any required runtime permission is missing, show the agreement
        // screen so the mic/camera/notification prompts are answered once, up
        // front, instead of interrupting the first conversation.
        home: _permissionsGranted == null
            ? const Scaffold(
                backgroundColor: AppColors.paper,
                body: Center(child: CircularProgressIndicator(color: AppColors.green)),
              )
            : _permissionsGranted!
                ? HomeScreen(controller: widget.controller)
                : PermissionGateScreen(onGranted: _onPermissionsGranted),
      );
}