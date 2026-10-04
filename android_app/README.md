# Xiaozhi Android

Flutter Android client for Xiaozhi WebSocket servers. It provides a mobile chat screen, manual voice input, Opus audio playback, text messages, interruption, and a separate connection settings screen.

## Build locally

Install Flutter stable and Android Studio with Android SDK 35 or newer. This app targets Android 7 (API 24) and newer. From this directory run:

```powershell
flutter create --platforms=android --project-name=py_xiaozhi_android --org=com.pyxiaozhi .
flutter pub get
flutter test
flutter build apk --release
```

The release APK is written to `build/app/outputs/flutter-apk/app-release.apk`.

## Connect

Open Settings and enter the server WebSocket URL, access token, Device ID, and Client ID. The token is stored using Android secure storage. Device and client IDs are generated on first launch when not supplied. Choose the front or rear camera in Settings; the selection is used by the camera preview shortcut. This client currently connects through the WebSocket transport; it does not yet perform desktop-style OTA activation or MQTT/UDP setup.

## GitHub Actions

The separate `Android APK` workflow is manual-dispatch only. It creates the Flutter Android platform scaffold, runs analysis and tests, builds a release APK, and uploads it as an Actions artifact.