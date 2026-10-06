// Requests every runtime permission the app needs, up front, so the mic, camera
// and notifications never prompt mid-conversation.
//
// Android grants dangerous permissions at runtime, not at install time. Without
// this gate the system dialog appears the first time the recorder starts, the
// first time the camera opens and the first time a reminder is scheduled — each
// one interrupting whatever the user was doing. Requesting them once on the first
// launch means the later flows just work.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:permission_handler/permission_handler.dart';

/// One row of the permission list shown on the gate screen.
@immutable
class AppPermission {
  const AppPermission({
    required this.permission,
    required this.title,
    required this.reason,
    this.required = true,
  });

  final Permission permission;
  final String title;
  final String reason;
  final bool required;

  /// Human readable status for the UI.
  String statusText(PermissionStatus status) {
    if (status.isGranted) return 'Diberikan';
    if (status.isPermanentlyDenied) return 'Ditolak permanen';
    if (status.isDenied) return 'Belum diberikan';
    if (status.isRestricted) return 'Dibatasi sistem';
    if (status.isLimited) return 'Terbatas';
    return 'Tidak diketahui';
  }
}

/// The permissions this app uses, in the order they are requested. The list
/// mirrors the <uses-permission> entries the CI workflow writes into
/// AndroidManifest.xml (see .github/workflows/android.yml).
///
/// Note on media access: attachments are opened through the system file picker
/// (Storage Access Framework), which needs no runtime permission, so there is no
/// storage/photos entry here. Requesting Permission.photos would wrongly report
/// "denied" on Android 12 and below (it maps to READ_MEDIA_IMAGES, API 33+).
final List<AppPermission> appPermissions = [
  const AppPermission(
    permission: Permission.microphone,
    title: 'Mikrofon',
    reason: 'Mengirim suara Anda ke asisten dan mendeteksi wake word.',
  ),
  const AppPermission(
    permission: Permission.camera,
    title: 'Kamera',
    reason: 'Mengambil foto dan pratinjau kamera untuk fitur vision.',
  ),
  const AppPermission(
    permission: Permission.notification,
    title: 'Notifikasi',
    reason: 'Menampilkan pengingat dan pemutar musik di panel notifikasi.',
    // Permission.notification shows no system dialog — it opens the notification
    // settings page instead, which would trap the first-run flow. It is also
    // requested on demand by reminder_runtime when a reminder is scheduled, so
    // it must not gate the rest of the app.
    required: false,
  ),
];

/// Result of [PermissionManager.requestAll].
enum PermissionRequestResult {
  /// Every required permission was granted.
  allGranted,
  /// The user declined at least one required permission this session.
  denied,
  /// At least one required permission is permanently denied; only the system
  /// settings screen can restore it.
  permanentlyDenied,
}

class PermissionManager {
  const PermissionManager();

  /// True when every required permission is already granted, i.e. nothing to ask.
  Future<bool> get allRequiredGranted async {
    for (final entry in appPermissions.where((entry) => entry.required)) {
      final status = await entry.permission.status;
      if (!status.isGranted) return false;
    }
    return true;
  }

  /// Requests every permission in [appPermissions] that is not already granted.
  ///
  /// Returns [PermissionRequestResult.permanentlyDenied] when any required
  /// permission ends up permanently denied, so the caller can send the user to
  /// the system settings page — the dialog can no longer be shown.
  Future<PermissionRequestResult> requestAll() async {
    var denied = false;
    var permanentlyDenied = false;

    for (final entry in appPermissions) {
      // request() is a no-op when the permission is already granted, and on
      // Android it is also the only way to distinguish "denied" from
      // "permanently denied" — status alone reports denied for both.
      final status = await entry.permission.request();
      if (status.isGranted) continue;
      if (status.isPermanentlyDenied) {
        if (entry.required) permanentlyDenied = true;
      } else if (entry.required) {
        denied = true;
      }
    }

    if (permanentlyDenied) return PermissionRequestResult.permanentlyDenied;
    if (denied) return PermissionRequestResult.denied;
    return PermissionRequestResult.allGranted;
  }

  /// Opens the system app settings page, the only way to recover a permanently
  /// denied permission.
  Future<bool> openSettings() => openAppSettings();

  /// Current status of every entry, for the gate screen's list view.
  Future<List<(AppPermission, PermissionStatus)>> statuses() async {
    final result = <(AppPermission, PermissionStatus)>[];
    for (final entry in appPermissions) {
      result.add((entry, await entry.permission.status));
    }
    return result;
  }
}
