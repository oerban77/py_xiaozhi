import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'app_theme.dart';
import 'home_screen.dart';
import 'permission_gate_screen.dart';
import 'permission_manager.dart';
import 'reminder_runtime.dart';
import 'xiaozhi_controller.dart';

Future<void> main() async {
  // A global error handler so an uncaught async error never kills the
  // app silently (and never shows a red screen in release): log it and
  // keep running. Without this, a single fire-and-forget failure (the
  // app has dozens of unawaited() calls) can leave a feature dead with
  // no message — the exact shape of the "mic tidak direspon" /
  // "TTS tidak keluar" reports.
  FlutterError.onError = (details) {
    FlutterError.presentError(details);
  };
  await runZonedGuarded(
    () async {
      WidgetsFlutterBinding.ensureInitialized();
      await SystemChrome.setPreferredOrientations([]);
      // Full-screen stage: system bars (status + navigation) hide
      // automatically (immersive sticky) and reappear on an edge swipe,
      // then hide again after a timeout. Transparent bars so they overlay
      // the stage instead of pushing the content when they show.
      await SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
      SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
        statusBarColor: Colors.transparent,
        systemNavigationBarColor: Colors.transparent,
        statusBarIconBrightness: Brightness.light,
        systemNavigationBarIconBrightness: Brightness.light,
      ));

      final controller = XiaozhiController();
      try {
        await controller.initialize();
      } catch (e) {
        // Init failed (corrupt preferences, audio session unavailable,
        // storage full): show an error screen instead of crashing at
        // launch with no UI at all.
        runApp(_InitErrorApp(error: e));
        return;
      }
      runApp(XiaozhiApp(controller: controller));
    },
    (error, stack) {
      // Uncaught async error outside the Flutter framework: log it.
      // In release this keeps the app alive; the failing feature is
      // reported through the controller's own error channel when it
      // has one.
      debugPrint('Uncaught async error: $error\n$stack');
    },
  );
}

/// Shown when [XiaozhiController.initialize] throws at startup, so the
/// user sees what went wrong instead of a crash with no UI.
class _InitErrorApp extends StatelessWidget {
  const _InitErrorApp({required this.error});

  final Object error;

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Xiaozhi',
        debugShowCheckedModeBanner: false,
        home: Scaffold(
          backgroundColor: AppColors.paper,
          body: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.error_outline, size: 48, color: Colors.red),
                  const SizedBox(height: 16),
                  const Text(
                    'Xiaozhi gagal dimulai',
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '$error',
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 8),
                  const Text(
                    'Coba tutup aplikasi lalu buka lagi. Kalau terus gagal, '
                    'hapus data aplikasi dan masukkan lagi pengaturan server.',
                    textAlign: TextAlign.center,
                  ),
                ],
              ),
            ),
          ),
        ),
      );
}

class XiaozhiApp extends StatefulWidget {
  const XiaozhiApp({
    super.key,
    required this.controller,
    this.skipPermissionGate = false,
    this.skipAutoConnect = false,
  });

  final XiaozhiController controller;

  /// Allows widget tests and other controlled in-app bootstrap flows to skip the
  /// first-run permission gate without changing the default production behavior.
  final bool skipPermissionGate;

  /// Skips the startup auto-connect. Widget tests have no server to talk to, and
  /// XiaozhiController.connect() would otherwise try to reach the default
  /// endpoint the moment the test pumps the app.
  final bool skipAutoConnect;

  @override
  State<XiaozhiApp> createState() => _XiaozhiAppState();
}

class _XiaozhiAppState extends State<XiaozhiApp> with WidgetsBindingObserver {
  // Null while the permission check is still in flight; true once every required
  // permission is granted (or the user skipped the gate).
  bool? _permissionsGranted;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (widget.skipPermissionGate) {
      _permissionsGranted = true;
      unawaited(_maybeAutoConnect());
      return;
    }
    unawaited(_checkPermissions());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      unawaited(ReminderRuntime.initialize());
      // Suppress wake word detection for 2 seconds after resume to prevent
      // false triggers from button clicks, volume adjustments, or other
      // noise when switching back to the app.
      widget.controller.suppressWakeWordAfterResume();
    }
  }

  Future<void> _checkPermissions() async {
    final manager = const PermissionManager();
    final granted = await manager.allRequiredGranted;
    if (!mounted) return;
    setState(() => _permissionsGranted = granted);
    if (granted) unawaited(_maybeAutoConnect());
  }

  Future<void> _maybeAutoConnect() async {
    if (widget.skipAutoConnect) return;
    await widget.controller.maybeAutoConnect();
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
          // TV/set-top-box remote navigation: arrow keys move the focus
          // and the focused control must be clearly visible on a dark
          // screen, so the focus overlay is teal instead of the default
          // faint black.
          focusColor: const Color(0xFF26C6B0).withValues(alpha: 0.30),
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