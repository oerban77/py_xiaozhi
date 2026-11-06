import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:py_xiaozhi_android/avatar_3d_server.dart';

/// Unit tests for the 3D avatar's loopback server (avatar_3d_server.dart).
/// The server is what lets the WebView load the bundled page as ES
/// modules: `loadFlutterAsset` serves the page as a `file://` URL and
/// Chrome/WebView refuses module imports (and `fetch()` of the .glb)
/// from `file://`, so the page never reports ready and the widget falls
/// back to the emoji. These tests pin the server contract: the bundled
/// files are served over http://127.0.0.1 with the MIME types a module
/// loader requires, and anything outside the avatar directory is a 404.
/// Lets the test use a real loopback [HttpClient]: the test binding
/// replaces [HttpClient] with a mock that answers 400 to everything,
/// so a real server cannot be hit without this override.
class _RealHttpOverrides extends HttpOverrides {
  // The override body is just super on purpose: the test binding sets
  // HttpOverrides.global to a mock that answers 400 to everything, and
  // installing this class restores the real HttpClient. (The lint
  // flags the bare super call; it is load-bearing, not redundant.)
  @override
  // ignore: unnecessary_overrides
  HttpClient createHttpClient(SecurityContext? context) =>
      super.createHttpClient(context);
}

void main() {
  // rootBundle needs the test binding initialized (plain test() does
  // not do it); the real app always has it.
  TestWidgetsFlutterBinding.ensureInitialized();

  test('serves the bundled page, three.js and model with MIME types',
      () async {
    HttpOverrides.global = _RealHttpOverrides();
    addTearDown(() => HttpOverrides.global = null);
    final server = Avatar3dServer();
    addTearDown(server.stop);
    await server.start();
    expect(server.isRunning, isTrue);
    final base = Uri.parse(server.baseUrl!);

    final client = HttpClient();
    addTearDown(client.close);

    Future<(int, String?, List<int>)> get(String path) async {
      final request = await client.getUrl(base.resolve(path));
      final response = await request.close();
      final bytes = await response.fold<List<int>>(
        <int>[],
        (buffer, chunk) => buffer..addAll(chunk),
      );
      return (
        response.statusCode,
        response.headers.contentType?.mimeType,
        bytes,
      );
    }

    final (pageStatus, pageType, pageBytes) = await get('avatar3d.html');
    expect(pageStatus, HttpStatus.ok);
    expect(pageType, 'text/html');
    expect(utf8.decode(pageBytes), contains('window.__avatar3d'));

    final (jsStatus, jsType, jsBytes) =
        await get('three.module.min.js');
    expect(jsStatus, HttpStatus.ok);
    expect(jsType, 'text/javascript');
    expect(jsBytes.length, 670681);

    final (loaderStatus, loaderType, _) =
        await get('addons/loaders/GLTFLoader.js');
    expect(loaderStatus, HttpStatus.ok);
    expect(loaderType, 'text/javascript');

    final (glbStatus, glbType, glbBytes) =
        await get('models/halfBody.glb');
    expect(glbStatus, HttpStatus.ok);
    expect(glbType, 'model/gltf-binary');
    expect(glbBytes.length, 5928964);

    final (missingStatus, _, _) = await get('does-not-exist.glb');
    expect(missingStatus, HttpStatus.notFound);

    final (escapeStatus, _, _) = await get('../pubspec.yaml');
    expect(escapeStatus, HttpStatus.notFound);
  });
}
