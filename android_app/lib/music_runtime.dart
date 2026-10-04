import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:just_audio/just_audio.dart';
import 'package:shared_preferences/shared_preferences.dart';

class MusicRuntime {
  static const module = 'music';
  static const toolNames = <String>{
    'music_player.search_and_play',
    'music_player.pause',
    'music_player.resume',
    'music_player.stop',
    'music_player.seek',
    'music_player.set_volume',
    'music_player.get_volume',
    'music_player.get_status',
    'music_player.get_lyrics',
    'music_player.play_url',
  };

  static const tools = <Map<String, Object?>>[
    {
      'name': 'music_player.search_and_play',
      'description': 'Search for a song online by name and start playback.',
      'inputSchema': {
        'type': 'object',
        'properties': {'song_name': {'type': 'string'}},
        'required': ['song_name'],
      },
    },
    {
      'name': 'music_player.pause',
      'description': 'Pause current music playback and preserve its position.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'music_player.resume',
      'description': 'Resume paused music playback.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'music_player.stop',
      'description': 'Stop current music playback.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'music_player.seek',
      'description': 'Seek to a percentage or position in the current song.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'percent': {'type': 'integer', 'default': -1, 'minimum': -1, 'maximum': 100},
          'position': {'type': 'integer', 'default': -1, 'minimum': -1},
        },
        'required': [],
      },
    },
    {
      'name': 'music_player.set_volume',
      'description': 'Set music player volume from 0 to 100 percent.',
      'inputSchema': {
        'type': 'object',
        'properties': {'volume': {'type': 'integer', 'minimum': 0, 'maximum': 100}},
        'required': ['volume'],
      },
    },
    {
      'name': 'music_player.get_volume',
      'description': 'Get the current music playback volume.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'music_player.get_status',
      'description': 'Get the current song, playback state, position, duration, and progress.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'music_player.get_lyrics',
      'description': 'Get lyrics for the currently playing song.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'music_player.play_url',
      'description': 'Play a direct HTTP or HTTPS audio URL.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'url': {'type': 'string'},
          'title': {'type': 'string', 'default': ''},
        },
        'required': ['url'],
      },
    },
  ];

  static const _searchUrl = 'http://search.kuwo.cn/r.s';
  static const _resolveBase = 'https://lxmusicapi.onrender.com';
  static const _preferencesKey = 'android_music_volume';
  static final AudioPlayer _player = AudioPlayer();
  static String _currentSong = '';
  static String _currentUrl = '';
  static List<String> _lyrics = [];
  static int _volume = 100;
  static bool _initialized = false;

  static Future<void> initialize() async {
    if (_initialized) return;
    final preferences = await SharedPreferences.getInstance();
    _volume = (preferences.getInt(_preferencesKey) ?? 100).clamp(0, 100).toInt();
    await _player.setVolume(_volume / 100);
    _initialized = true;
  }

  static Future<String> call(String name, Map<String, dynamic> arguments) async {
    await initialize();
    switch (name) {
      case 'music_player.search_and_play':
        return _searchAndPlay(_requiredString(arguments, 'song_name'));
      case 'music_player.pause':
        await _player.pause();
        return 'Music paused.';
      case 'music_player.resume':
        if (_currentUrl.isEmpty) return 'No paused music to resume.';
        unawaited(_player.play());
        return 'Music resumed.';
      case 'music_player.stop':
        await _player.stop();
        _currentUrl = '';
        _lyrics = [];
        return 'Music stopped.';
      case 'music_player.seek':
        return _seek(arguments);
      case 'music_player.set_volume':
        return _setVolume(_requiredInt(arguments, 'volume').clamp(0, 100).toInt());
      case 'music_player.get_volume':
        return 'Current music volume: $_volume%';
      case 'music_player.get_status':
        return _getStatus();
      case 'music_player.get_lyrics':
        return _getLyrics();
      case 'music_player.play_url':
        final url = _requiredString(arguments, 'url');
        final uri = Uri.tryParse(url);
        if (uri == null || !{'http', 'https'}.contains(uri.scheme) || uri.host.isEmpty) {
          throw ArgumentError('Only valid HTTP/HTTPS audio URLs are supported.');
        }
        _lyrics = [];
        return _playUrl(url, _optionalString(arguments['title']).isEmpty
            ? url
            : _optionalString(arguments['title']));
      default:
        throw ArgumentError('Unknown music tool: $name');
    }
  }

  static Future<String> _searchAndPlay(String query) async {
    final uri = Uri.parse(_searchUrl).replace(queryParameters: {
      'client': 'kt',
      'all': query,
      'pn': '0',
      'rn': '20',
      'uid': '794762570',
      'ver': 'kwplayer_ar_9.2.2.1',
      'vipver': '1',
      'show_copyright_off': '1',
      'newver': '1',
      'ft': 'music',
      'cluster': '0',
      'strategy': '2012',
      'encoding': 'utf8',
      'rformat': 'json',
      'vermerge': '1',
      'mobi': '1',
      'issubtitle': '1',
    });
    final search = await _requestJson(uri, headers: const {
      'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36',
      'Accept': 'application/json, text/plain, */*',
    });
    final results = search['abslist'];
    if (results is! List || results.isEmpty || results.first is! Map) {
      return 'Song not found: $query';
    }
    final hit = results.first as Map;
    final musicRid = '${hit['MUSICRID'] ?? ''}';
    final songId = musicRid.startsWith('MUSIC_') ? musicRid.substring(6) : musicRid;
    if (songId.isEmpty) return 'Search result did not include a song ID.';
    final title = '${hit['SONGNAME'] ?? query}'.trim();
    final artist = '${hit['ARTIST'] ?? ''}'.trim();
    final album = '${hit['ALBUM'] ?? ''}'.trim();
    var displayName = artist.isEmpty ? title : '$title - $artist';
    if (album.isNotEmpty) displayName += ' ($album)';
    _lyrics = await _fetchLyrics(songId);

    final apiUrl = Uri.parse('$_resolveBase/url/kw/$songId/320k');
    String? audioUrl;
    try {
      final resolved = await _requestJson(apiUrl, headers: const {
        'X-Request-Key': 'share-v3',
        'User-Agent': 'lx-music-request',
      });
      audioUrl = _extractAudioUrl(resolved);
    } catch (_) {}

    audioUrl ??= await _resolveOfficialKuwo(songId);
    if (audioUrl == null) return 'Could not resolve a playable URL for $displayName.';
    return _playUrl(audioUrl, displayName);
  }

  static Future<String?> _resolveOfficialKuwo(String songId) async {
    final uri = Uri.https('wapi.kuwo.cn', '/api/v1/www/music/playUrl', {
      'mid': songId,
      'type': 'music',
      'httpsStatus': '1',
      'br': '320kmp3',
    });
    try {
      final data = await _requestJson(uri, headers: const {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36',
        'Referer': 'https://www.kuwo.cn/',
      });
      return _extractAudioUrl(data);
    } catch (_) {
      return null;
    }
  }

  static String? _extractAudioUrl(Map<String, dynamic> data) {
    final direct = data['url'];
    if (direct is String && direct.startsWith('http')) return direct;
    final inner = data['data'];
    if (inner is String && inner.startsWith('http')) return inner;
    if (inner is Map && inner['url'] is String && '${inner['url']}'.startsWith('http')) {
      return '${inner['url']}';
    }
    return null;
  }

  static Future<String> _playUrl(String url, String title) async {
    try {
      await _player.setAudioSource(
        AudioSource.uri(
          Uri.parse(url),
          headers: const {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36',
            'Accept': '*/*',
            'Referer': 'https://www.kuwo.cn/',
          },
        ),
      );
      await _player.setVolume(_volume / 100);
      _currentSong = title;
      _currentUrl = url;
      unawaited(_player.play());
      return 'Now playing: $title';
    } catch (error) {
      _currentUrl = '';
      return 'Music playback failed: $error';
    }
  }

  static String _seek(Map<String, dynamic> arguments) {
    if (_currentUrl.isEmpty) return 'No music is playing.';
    final duration = _player.duration;
    if (duration == null || duration == Duration.zero) return 'Song duration is not available yet.';
    final percent = _optionalInt(arguments['percent']) ?? -1;
    final position = _optionalInt(arguments['position']) ?? -1;
    final Duration target;
    if (percent >= 0) {
      target = Duration(milliseconds: duration.inMilliseconds * percent.clamp(0, 100) ~/ 100);
    } else if (position >= 0) {
      target = Duration(seconds: position);
    } else {
      return 'Specify percent (0-100) or position (seconds).';
    }
    unawaited(_player.seek(target > duration ? duration : target));
    return 'Seeked to ${_formatDuration(target)}.';
  }

  static Future<String> _setVolume(int value) async {
    _volume = value;
    final preferences = await SharedPreferences.getInstance();
    await preferences.setInt(_preferencesKey, _volume);
    await _player.setVolume(_volume / 100);
    return 'Music volume set to $_volume%.';
  }

  static String _getStatus() {
    if (_currentUrl.isEmpty) return 'Music is not playing.';
    final state = _player.playing ? 'Playing' : 'Paused';
    final duration = _player.duration ?? Duration.zero;
    final position = _player.position;
    final percent = duration.inMilliseconds == 0
        ? 0
        : (position.inMilliseconds * 100 ~/ duration.inMilliseconds).clamp(0, 100);
    return 'Current song: $_currentSong\n'
        'Playback state: $state\n'
        'Total duration (s): ${duration.inSeconds}\n'
        'Current position (s): ${position.inSeconds}\n'
        'Progress: $percent%';
  }

  static String _getLyrics() => _lyrics.isEmpty
      ? 'No lyrics available for the current song.'
      : 'Lyrics content:\n${_lyrics.join('\n')}';

  static Future<List<String>> _fetchLyrics(String songId) async {
    final uri = Uri.parse('http://m.kuwo.cn/newh5/singles/songinfoandlrc')
        .replace(queryParameters: {'musicId': songId});
    try {
      final response = await _requestJson(uri, headers: const {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36',
        'Referer': 'https://www.kuwo.cn/',
      });
      if (response['status'] != 200 || response['data'] is! Map) return [];
      final lines = (response['data'] as Map)['lrclist'];
      if (lines is! List) return [];
      final lyrics = <String>[];
      for (final line in lines.whereType<Map>()) {
        final text = '${line['lineLyric'] ?? ''}'.trim();
        final time = double.tryParse('${line['time'] ?? ''}');
        if (text.isEmpty || time == null ||
            const ['by:', 'ar:', 'al:', 'ti:', 'offset:', 'id:', 'hash:']
                .any(text.startsWith)) {
          continue;
        }
        final timestamp = _formatDuration(
          Duration(milliseconds: (time * 1000).round()),
        );
        lyrics.add('[$timestamp] $text');
      }
      return lyrics;
    } catch (_) {
      return [];
    }
  }

  static Future<Map<String, dynamic>> _requestJson(
    Uri uri, {
    Map<String, String> headers = const {},
  }) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 12);
    try {
      final request = await client.getUrl(uri);
      request.headers.set(HttpHeaders.userAgentHeader, 'XiaozhiAndroid/1.0');
      for (final entry in headers.entries) {
        request.headers.set(entry.key, entry.value);
      }
      final response = await request.close().timeout(const Duration(seconds: 15));
      final body = await utf8.decoder.bind(response).join();
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw HttpException('HTTP ${response.statusCode} from ${uri.host}');
      }
      final decoded = jsonDecode(body);
      if (decoded is! Map<String, dynamic>) throw const FormatException('Invalid JSON response');
      return decoded;
    } finally {
      client.close(force: true);
    }
  }

  static String _requiredString(Map<String, dynamic> arguments, String name) {
    final value = arguments[name];
    if (value is! String || value.trim().isEmpty) throw ArgumentError('$name is required');
    return value.trim();
  }

  static int _requiredInt(Map<String, dynamic> arguments, String name) {
    final value = _optionalInt(arguments[name]);
    if (value == null) throw ArgumentError('$name is required');
    return value;
  }

  static int? _optionalInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse('$value');
  }

  static String _optionalString(Object? value) => value is String ? value.trim() : '';

  static String _formatDuration(Duration duration) {
    final minutes = duration.inMinutes;
    final seconds = duration.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$minutes:$seconds';
  }

  static Future<void> dispose() => _player.dispose();
}