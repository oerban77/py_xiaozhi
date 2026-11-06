import 'dart:async';
import 'dart:io';

import 'package:flutter/services.dart';

/// Serves the bundled 3D avatar files (the viewer page, three.js and
/// the .glb model) over a loopback HTTP server, so the WebView can load
/// them as ES modules.
///
/// Why a server at all: the avatar page lives in the app bundle, and
/// `WebViewController.loadFlutterAsset` serves it as a
/// `file:///android_asset/...` URL. Chrome/WebView refuses to load ES
/// modules (and `fetch()` a .glb) from `file://` URLs: module fetches
/// are CORS-checked and a `file://` page has an opaque (`null`)
/// origin, so the import fails and the page never reports ready -- the
/// widget then falls back to the emoji avatar after its 20 s timeout
/// (that is exactly what happened on the first bundled build). The
/// Live2D avatar is unaffected: it uses classic `<script>` tags, which
/// load fine from `file://`.
///
/// A loopback server fixes it with no download and no network access:
/// the page, three.js and the model all come from the app bundle,
/// served over `http://127.0.0.1` with correct MIME types (module
/// scripts need `text/javascript`). The server binds an ephemeral
/// port, serves only the `assets/threejs/` directory, and stops when
/// the avatar widget is disposed. Cleartext to loopback is allowed by
/// Android without any manifest change.
class Avatar3dServer {
  Avatar3dServer({AssetBundle? bundle}) : _bundle = bundle ?? rootBundle;

  static const _assetRoot = 'assets/threejs';

  static const _mimeTypes = <String, String>{
    'html': 'text/html; charset=utf-8',
    'js': 'text/javascript; charset=utf-8',
    'mjs': 'text/javascript; charset=utf-8',
    'glb': 'model/gltf-binary',
    'gltf': 'model/gltf+json',
    'json': 'application/json',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'bin': 'application/octet-stream',
  };

  final AssetBundle _bundle;
  HttpServer? _server;

  /// Base URL of the running server, e.g. `http://127.0.0.1:54321/`.
  /// Null until [start] succeeds.
  String? get baseUrl =>
      _server == null ? null : 'http://127.0.0.1:${_server!.port}/';

  bool get isRunning => _server != null;

  Future<void> start() async {
    if (_server != null) return;
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server = server;
    unawaited(_serve(server));
  }

  Future<void> _serve(HttpServer server) async {
    await for (final request in server) {
      unawaited(_handle(request));
    }
  }

  Future<void> _handle(HttpRequest request) async {
    try {
      // Only the avatar files, nothing else: strip the leading slash
      // and reject any path that would escape the asset directory.
      final path = request.uri.path.replaceFirst(RegExp(r'^/+'), '');
      if (path.isEmpty || path.contains('..')) {
        request.response
          ..statusCode = HttpStatus.notFound
          ..close();
        return;
      }
      final data = await _bundle.load('$_assetRoot/$path');
      final ext = path.split('.').last.toLowerCase();
      request.response
        ..statusCode = HttpStatus.ok
        ..headers.contentType =
            ContentType.parse(_mimeTypes[ext] ?? 'application/octet-stream')
        ..headers.contentLength = data.lengthInBytes
        ..add(data.buffer.asUint8List());
      await request.response.close();
    } catch (_) {
      try {
        request.response
          ..statusCode = HttpStatus.notFound
          ..close();
      } catch (_) {
        // The response is already closed; nothing more to do.
      }
    }
  }

  Future<void> stop() async {
    final server = _server;
    _server = null;
    await server?.close(force: true);
  }
}
