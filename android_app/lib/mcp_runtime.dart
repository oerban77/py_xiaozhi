import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/foundation.dart' show visibleForTesting;
import 'package:camera/camera.dart';
import 'package:html/parser.dart' as html_parser;
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:mqtt_client/mqtt_client.dart';
import 'package:mqtt_client/mqtt_server_client.dart';
import 'package:xml/xml.dart';

import 'camera_runtime.dart';
import 'attachment_text_reader.dart';
import 'music_runtime.dart';
import 'reminder_runtime.dart';
import 'volume_runtime.dart';

class McpRuntime {
  static const _baseUrl = 'https://equran.id/api/v2/shalat';
  static const _holidayBaseUrl = 'https://api.kemendesa.link/libur-nasional';
  static String _discoveredSmartHomeBroker = '';
  static List<Map<String, Object?>> _discoveredSmartHomeDevices = [];

  static const _tools = <Map<String, Object?>>[
    {
      'name': 'prayer_times_today',
      'description': 'Get today prayer times for an Indonesian province and city.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'province': {'type': 'string'},
          'city': {'type': 'string'},
        },
        'required': ['province', 'city'],
      },
    },
    {
      'name': 'prayer_times_monthly',
      'description': 'Get monthly prayer times for an Indonesian province and city.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'province': {'type': 'string'},
          'city': {'type': 'string'},
          'month': {'type': 'integer', 'minimum': 1, 'maximum': 12},
          'year': {'type': 'integer', 'minimum': 1, 'maximum': 2100},
        },
        'required': ['province', 'city'],
      },
    },
    {
      'name': 'prayer_list_provinces',
      'description': 'List Indonesian provinces supported by the prayer times service.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'prayer_list_cities',
      'description': 'List cities and regencies available in an Indonesian province.',
      'inputSchema': {
        'type': 'object',
        'properties': {'province': {'type': 'string'}},
        'required': ['province'],
      },
    },
    {
      'name': 'get_weather',
      'description': 'Get the current weather for a given city.',
      'inputSchema': {
        'type': 'object',
        'properties': {'city': {'type': 'string', 'default': 'Beijing'}},
        'required': [],
      },
    },
    {
      'name': 'take_photo',
      'description': '[Photo Recognition] Take a photo with the selected Android camera and analyze its '
          'content, answering the user\'s question about the image.\n'
          'If the app has queued an attached image, analyze that selected file instead of capturing the '
          'camera. For requests like \'analisa gambar\', \'gambar yang saya lampirkan\', or questions about '
          'an uploaded image, you MUST call this tool. The app supplies the selected image and the user\'s '
          'exact question.\n'
          'Use cases: taking a photo to look at something, object or scene recognition, text recognition '
          '(OCR), and image Q&A.\n'
          'Args: `question` - The question that you want to ask about the photo.',
      'inputSchema': {
        'type': 'object',
        'properties': {'question': {'type': 'string'}},
        'required': ['question'],
      },
    },
    {
      'name': 'qrcode_read_file',
      'description': 'Read QR codes and barcodes from an image file on this Android device.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'path': {'type': 'string'},
          'prompt': {'type': 'string', 'default': ''},
        },
        'required': ['path'],
      },
    },
    {
      'name': 'get_forecast',
      'description': 'Get the weather forecast for a city for 1 to 7 days.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'city': {'type': 'string', 'default': 'Beijing'},
          'days': {'type': 'integer', 'default': 3, 'minimum': 1, 'maximum': 7},
        },
        'required': [],
      },
    },
    {
      'name': 'self.indonesia_holiday_query',
      'description': 'List or check Indonesian national holidays.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'action': {'type': 'string', 'default': 'list'},
          'year': {'type': 'integer', 'default': 0, 'minimum': 2000, 'maximum': 2100},
          'date': {'type': 'string', 'default': ''},
        },
        'required': [],
      },
    },
    {
      'name': 'get_news',
      'description': 'Get recent news headlines, optionally filtered by topic.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'topic': {'type': 'string', 'default': ''},
          'max_results': {'type': 'integer', 'default': 5, 'minimum': 1, 'maximum': 20},
        },
        'required': [],
      },
    },
    {
      'name': 'web_search',
      'description': 'Search the web for current information and articles.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'query': {'type': 'string'},
          'count': {'type': 'integer', 'default': 5, 'minimum': 1, 'maximum': 10},
          'language': {'type': 'string', 'default': 'en-US'},
        },
        'required': ['query'],
      },
    },
    {
      'name': 'read_article',
      'description': 'Read and extract the main text from an article URL.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'url': {'type': 'string'},
          'max_chars': {'type': 'integer', 'default': 6000},
        },
        'required': ['url'],
      },
    },
    {
      'name': 'device_status',
      'description': 'Show all smart-home devices (lights, switches, sensors, ...) and their status. Devices marked RO are read-only (discovered state topic, no known command topic).',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'device_control',
      'description': 'Control one configured smart-home device by its list index.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'deviceIndex': {'type': 'integer', 'minimum': 1, 'maximum': 128},
          'action': {'type': 'string', 'default': 'TOGGLE'},
        },
        'required': ['deviceIndex'],
      },
    },
    {
      'name': 'lights_all',
      'description': 'Switch all configured lights on or off.',
      'inputSchema': {
        'type': 'object',
        'properties': {'action': {'type': 'string'}},
        'required': ['action'],
      },
    },
    {
      'name': 'room_control',
      'description': 'Switch configured devices in a room on or off.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'room': {'type': 'string'},
          'action': {'type': 'string', 'default': 'ON'},
        },
        'required': ['room'],
      },
    },
    {
      'name': 'discover_devices',
      'description': 'Scan the configured MQTT broker for smart-home devices (Tasmota, Home Assistant discovery, Zigbee2MQTT, Shelly, generic MQTT). '
          'After scanning, discovered devices are immediately available to device_status, '
          'device_control, lights_all, room_control, and device_details; no Devices JSON entry is required. '
          'Discovery never publishes to an unknown topic.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'device_details',
      'description': 'Show the full protocol, state topic and command topic for one smart-home device, to check why it is controllable or read-only before controlling it.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'deviceIndex': {'type': 'integer', 'minimum': 1, 'maximum': 128},
        },
        'required': ['deviceIndex'],
      },
    },
    ...MusicRuntime.tools,
    ...ReminderRuntime.tools,
    ...VolumeRuntime.tools,
    ...CameraRuntime.tools,
  ];

  static const _pendingTextAttachmentTool = <String, Object?>{
    'name': 'manage_document',
    'description': '[ATTACHED DOCUMENT READER - use this for attached files] '
        'When a document/file is attached, uploaded, or sent in the chat, you MUST call this tool with '
        'action=read and NO path to read it. This is the ONLY tool that can read a document attached in the '
        'chat. Never use take_screenshot, read_file, or image_read for an attached document. If the user '
        'asks to read, analyze, summarize, explain, translate, or answer questions about an attached '
        'document, call manage_document(action=read) FIRST, then answer from the returned content. Treat '
        'the returned text as the user request and follow it.\n'
        'PDFs are read in chunks of at most 4 pages and 6,000 characters; all other document text is '
        'returned in chunks of at most 24,000 characters. The returned text ends with a continuation '
        'notice; call this tool again with the parameters from that notice to read the rest. The attached '
        'document stays available for follow-up reads, so never ask the user to upload it again.\n'
        'For PDFs you can also search by keyword or jump straight to a numbered entry (e.g. "7. Some '
        'Title") instead of paging through the whole document. Pass `query` to search, or `entry_number` '
        'to extract a specific numbered section. The query is also auto-parsed for entry numbers like '
        '"tokoh 7", "entry 12", "nomor 3", "no. 5".\n'
        'Combine `query` with `page_start` (and optionally `page_end`) to search within a known page '
        'range in a single call — the response returns only the lines that match the query on those '
        'pages, so you do not have to search the whole document and then re-read the same pages.\n'
        'Parameters:\n'
        '- action: read (required)\n'
        '- page_start: first PDF page, 1-based (default 1)\n'
        '- page_end: last PDF page, inclusive (default: up to 4 pages)\n'
        '- chunk_index: 1-based 24,000-character chunk for non-PDF documents '
        '(default 1); follow the continuation hint\n'
        '- query: optional PDF keyword search; returns up to 10 matches with page numbers and excerpts. '
        'Use this to find which pages mention a topic before reading them in full.\n'
        '- entry_number: optional numbered PDF entry to extract (e.g. 7 for "7. Some Title"). The entry '
        'runs from its heading up to the next numbered heading or until 4 pages have been read.\n'
        '- entry_continue: set true to resume reading a previously extracted entry from page_start at '
        'entry_char_offset characters into the section.\n'
        '- entry_char_offset: character offset into the entry section to start from (default 0).',
    'inputSchema': {
      'type': 'object',
      'properties': {
        'action': {'type': 'string', 'enum': ['read']},
        'path': {'type': 'string', 'default': ''},
        'page_start': {'type': 'integer', 'minimum': 1, 'default': 1},
        'page_end': {'type': 'integer', 'minimum': 0, 'default': 0},
        'chunk_index': {'type': 'integer', 'minimum': 1, 'default': 1},
        'query': {'type': 'string', 'default': ''},
        'entry_number': {'type': 'integer', 'minimum': 1, 'default': 0},
        'entry_continue': {'type': 'boolean', 'default': false},
        'entry_char_offset': {'type': 'integer', 'minimum': 0, 'default': 0},
      },
      'required': ['action'],
    },
  };

  static Future<void> initializeNotifications() async {
    await ReminderRuntime.initialize();
    await MusicRuntime.initialize();
  }

  static Future<void> dispose() => MusicRuntime.dispose();

  static Future<Map<String, Object?>?> handle(
    Map<String, dynamic> request, {
    required Set<String> disabledModules,
    Map<String, Object?> smartHomeConfig = const {},
    Map<String, Object?> visionConfig = const {},
    String? pendingTextAttachment,
    String pendingTextQuestion = '',
    PdfExtraction? pendingPdf,
    Uint8List? pendingImageAttachment,
    bool pendingImageAsDocument = false,
    String pendingImageQuestion = '',
    String pendingAttachmentName = '',
  }) async {
    final id = request['id'];
    final method = request['method'];
    if (method is! String) {
      return id == null ? null : _error(id, -32600, 'Invalid MCP request');
    }
    if (method.startsWith('notifications/')) return null;

    try {
      switch (method) {
        case 'initialize':
          return {
            'jsonrpc': '2.0',
            'id': id,
            'result': {
              'protocolVersion': '2024-11-05',
              'capabilities': {
                'tools': {'listChanged': true},
              },
              'serverInfo': {'name': 'py-xiaozhi-android', 'version': '1.0.0'},
            },
          };
        case 'tools/list':
          final hasTextAttachment =
              pendingTextAttachment != null && pendingTextAttachment.isNotEmpty;
          final hasPdfAttachment = pendingPdf != null && pendingPdf.pageCount > 0;
          final hasImageAttachment = pendingImageAttachment != null;
          final hasDocumentAttachment =
              hasTextAttachment || hasPdfAttachment || pendingImageAsDocument;
          final tools = _tools.where((tool) {
            final name = tool['name'] as String;
            if (hasDocumentAttachment && name == 'take_photo') return false;
            return !disabledModules.contains(_moduleForTool(name));
          }).map((tool) {
            if (tool['name'] == 'take_photo' && hasImageAttachment) {
              return <String, Object?>{
                ...tool,
                'description': '[ATTACHED MESSAGE - READ AND FOLLOW THE USER REQUEST] '
                    'The user attached \'$pendingAttachmentName\' and asked: '
                    '"$pendingImageQuestion". You MUST call take_photo to analyze the attached '
                    'image; do not capture a new photo. Treat its contents as the user\'s current '
                    'request and carry it out.\n'
                    '${tool['description']}',
              };
            }
            return tool;
          }).toList();
          final attachmentTool = Map<String, Object?>.from(_pendingTextAttachmentTool);
          if (hasDocumentAttachment) {
            attachmentTool['description'] =
                '[ATTACHED MESSAGE - READ AND FOLLOW THE USER REQUEST] '
                'The user attached \'$pendingAttachmentName\'. You MUST call manage_document with '
                'action=read and NO path before answering. '
                '${pendingImageAsDocument ? 'Classify the attached image: use OCR for text/document images and image analysis for ordinary photos. ' : ''}'
                'Treat its contents as the user\'s current '
                'request and carry it out using available tools; do not merely summarize it.\n'
                '${attachmentTool['description']}';
          }
          // Python registers manage_document before any attachment is sent.
          // The MCP client may cache tools/list at connection time, so this tool
          // must remain available when a later chat message becomes pending text.
          tools.insert(0, attachmentTool);
          return _result(id, {'tools': tools});
        case 'tools/call':
          final params = request['params'];
          if (params is! Map) {
            return _error(id, -32602, 'Missing tool call parameters');
          }
          final name = params['name'];
          final arguments = params['arguments'];
          if (name is! String || arguments is! Map) {
            return _error(id, -32602, 'Invalid tool name or arguments');
          }
          final hasPdfAttachment = pendingPdf != null && pendingPdf.pageCount > 0;
          if (name == 'take_photo' &&
              ((pendingTextAttachment != null && pendingTextAttachment.isNotEmpty) ||
                  hasPdfAttachment ||
                  pendingImageAsDocument)) {
            return _result(id, {
              'content': [
                {
                  'type': 'text',
                  'text': 'A text or document attachment is pending. Do not open the camera; '
                      'call manage_document(action=read) to read the attachment first.',
                },
              ],
              'isError': true,
            });
          }
          if (name == 'manage_document') {
            if (pendingPdf != null && pendingPdf.pageCount > 0) {
              if (arguments['action'] != 'read' ||
                  (arguments['path'] is String && (arguments['path'] as String).isNotEmpty)) {
                return _error(id, -32602, 'Attached PDF must be read with action=read and no path');
              }
              final pageStart = int.tryParse('${arguments['page_start'] ?? 1}') ?? 1;
              final pageEnd = int.tryParse('${arguments['page_end'] ?? 0}') ?? 0;
              final query = arguments['query'] is String
                  ? arguments['query'] as String
                  : '';
              final entryNumber =
                  int.tryParse('${arguments['entry_number'] ?? 0}') ?? 0;
              final entryContinue =
                  arguments['entry_continue'] == true;
              final entryCharOffset =
                  int.tryParse('${arguments['entry_char_offset'] ?? 0}') ?? 0;
              // Mirrors the question_hint in
              // src/mcp/tools/documents/service.document_manage: surface the file
              // name and the user's chat question alongside the content so the
              // LLM knows what to answer, even though only the short prompt
              // travelled over the detect channel.
              final questionHint = pendingTextQuestion.isEmpty
                  ? ''
                  : '[Attached file: $pendingAttachmentName | '
                      'User question: $pendingTextQuestion]\n\n';
              // Mirrors _read_pdf: an entry_number (or a query that names one,
              // e.g. "tokoh 7") jumps straight to that numbered section instead
              // of paging through the whole document.
              final requestedEntry = entryNumber != 0
                  ? entryNumber
                  : AttachmentTextReader.parseEntryNumberQuery(query);
              final body = requestedEntry != null
                  ? AttachmentTextReader.extractPdfEntry(
                      extraction: pendingPdf,
                      entryNumber: requestedEntry,
                      pageStart: pageStart,
                      entryContinue: entryContinue,
                      entryCharOffset: entryCharOffset,
                    )
                  : query.trim().isNotEmpty && pageStart > 1
                      ? // Combined search+read: when the LLM already knows
                        // roughly where to look (page_start > 1), filter the
                        // requested pages down to lines that match the query
                        // instead of searching the whole document and then
                        // re-reading the same pages. Mirrors _read_pdf in
                        // src/mcp/tools/documents/service.py.
                      AttachmentTextReader.searchPdfInPages(
                          extraction: pendingPdf,
                          query: query,
                          pageStart: pageStart,
                          pageEnd: pageEnd,
                        )
                      : query.trim().isNotEmpty
                          ? AttachmentTextReader.searchPdf(
                              extraction: pendingPdf,
                              query: query,
                            )
                          : AttachmentTextReader.slicePdf(
                              extraction: pendingPdf,
                              pageStart: pageStart,
                              pageEnd: pageEnd,
                            );
              return _result(id, {
                'content': [
                  {
                    'type': 'text',
                    'text': '$questionHint$body',
                  },
                ],
                'isError': false,
              });
            }
            if (pendingTextAttachment != null && pendingTextAttachment.isNotEmpty) {
              if (arguments['action'] != 'read' ||
                  (arguments['path'] is String && (arguments['path'] as String).isNotEmpty)) {
                return _error(id, -32602, 'Attached text must be read with action=read and no path');
              }
              // Mirrors the question_hint in
              // src/mcp/tools/documents/service.document_manage: surface the file
              // name and the user's chat question alongside the content so the
              // LLM knows what to answer, even though only the short prompt
              // travelled over the detect channel.
              final questionHint = pendingTextQuestion.isEmpty
                  ? ''
                  : '[Attached file: $pendingAttachmentName | '
                      'User question: $pendingTextQuestion]\n\n';
              final chunkIndex =
                  int.tryParse('${arguments['chunk_index'] ?? 1}') ?? 1;
              return _result(id, {
                'content': [
                  {
                    'type': 'text',
                    'text': '$questionHint'
                        '${AttachmentTextReader.sliceDocument(
                          text: pendingTextAttachment,
                          chunkIndex: chunkIndex,
                        )}',
                  },
                ],
                'isError': false,
              });
            }
            if (!pendingImageAsDocument || pendingImageAttachment == null) {
              return _error(id, -32603, 'No text attachment is pending');
            }
            if (arguments['action'] != 'read' ||
                (arguments['path'] is String && (arguments['path'] as String).isNotEmpty)) {
              return _error(id, -32602, 'Attached content must be read with action=read and no path');
            }
            final autoClassify = pendingImageQuestion.trim().isEmpty;
            final imageReaderPrompt = autoClassify
                ? 'Tentukan jenis gambar ini. Jika berupa dokumen, uang kertas, struk, atau gambar '
                    'yang tujuan utamanya adalah teks, gunakan OCR dan kembalikan teksnya dengan '
                    'ejaan, angka, dan urutan baris dipertahankan. Jika berupa foto biasa, jelaskan '
                    'isi dan objek pentingnya secara singkat dalam bahasa Indonesia.'
              : 'Periksa jenis lampiran dan jalankan permintaan pengguna. Jika berupa dokumen, '
                'uang kertas, struk, atau gambar yang tujuan utamanya adalah teks, gunakan OCR '
                'dan pertahankan ejaan serta angka. Jika berupa foto biasa, lakukan analisis '
                'visual sesuai permintaan pengguna.';
            final recognizedContent = await _takePhoto(
              {'question': imageReaderPrompt},
              visionConfig,
              attachedImage: pendingImageAttachment,
              attachedQuestion: autoClassify
                  ? imageReaderPrompt
                  : '$imageReaderPrompt Konteks permintaan pengguna: $pendingImageQuestion',
            );
            return _result(id, {
              'content': [
                {'type': 'text', 'text': recognizedContent},
              ],
              'isError': false,
            });
          }
          final module = _moduleForTool(name);
          if (module == null) {
            return _error(id, -32601, 'Unknown tool: $name');
          }
          if (disabledModules.contains(module)) {
            return _error(id, -32603, '$module module is disabled');
          }
          final text = await _callTool(
            name,
            Map<String, dynamic>.from(arguments),
            smartHomeConfig,
            visionConfig,
            pendingImageAttachment,
            pendingImageQuestion,
          );
          return _result(id, {
            'content': [
              {'type': 'text', 'text': text},
            ],
            'isError': false,
          });
        default:
          return _error(id, -32601, 'Method not found: $method');
      }
    } catch (error) {
      return _error(id, -32603, error.toString());
    }
  }

  static Map<String, Object?> _result(Object? id, Object result) => {
        'jsonrpc': '2.0',
        'id': id,
        'result': result,
      };

  static Map<String, Object?> _error(Object? id, int code, String message) => {
        'jsonrpc': '2.0',
        'id': id,
        'error': {'code': code, 'message': message},
      };

  static String? _moduleForTool(String name) {
    if (name == 'add_prayer_reminders') return ReminderRuntime.module;
    if (name.startsWith('prayer_')) return 'prayer';
    if (name == 'get_weather' || name == 'get_forecast') return 'weather';
    if (name == 'self.indonesia_holiday_query') return 'indonesia_holiday';
    if (name == 'get_news') return 'news';
    if (name == 'web_search' || name == 'read_article') return 'websearch';
    if ({'device_status', 'device_control', 'lights_all', 'room_control', 'discover_devices', 'device_details'}
        .contains(name)) {
      return 'smarthome';
    }
    if (name == 'take_photo') return 'camera';
    if (CameraRuntime.toolNames.contains(name)) return CameraRuntime.module;
    if (VolumeRuntime.toolNames.contains(name)) return VolumeRuntime.module;
    if (name == 'qrcode_read_file') return 'qrcode';
    if (name == 'manage_document') return 'chat_attachment';
    if (MusicRuntime.toolNames.contains(name)) return MusicRuntime.module;
    if (ReminderRuntime.toolNames.contains(name)) return ReminderRuntime.module;
    return null;
  }

  static Future<String> _callTool(
    String name,
    Map<String, dynamic> arguments,
    Map<String, Object?> smartHomeConfig,
    Map<String, Object?> visionConfig,
    Uint8List? pendingImageAttachment,
    String pendingImageQuestion,
  ) async {
    if (name.startsWith('prayer_')) return _callPrayerTool(name, arguments);
    if (name == 'get_weather') return _getWeather(arguments);
    if (name == 'get_forecast') return _getForecast(arguments);
    if (name == 'self.indonesia_holiday_query') {
      return _queryIndonesiaHolidays(arguments);
    }
    if (name == 'get_news') return _getNews(arguments);
    if (name == 'web_search') return _webSearch(arguments);
    if (name == 'read_article') return _readArticle(arguments);
    if (name == 'take_photo') {
      return _takePhoto(
        arguments,
        visionConfig,
        attachedImage: pendingImageAttachment,
        attachedQuestion: pendingImageQuestion,
      );
    }
    if (name == 'qrcode_read_file') return _readQrCode(arguments);
    if (MusicRuntime.toolNames.contains(name)) {
      return MusicRuntime.call(name, arguments);
    }
    if (ReminderRuntime.toolNames.contains(name)) {
      if (name == 'add_prayer_reminders') return _addPrayerReminders(arguments);
      return ReminderRuntime.call(name, arguments);
    }
    if (VolumeRuntime.toolNames.contains(name)) {
      return VolumeRuntime.call(name, arguments);
    }
    if (CameraRuntime.toolNames.contains(name)) {
      return CameraRuntime.call(name, arguments);
    }
    if (_moduleForTool(name) == 'smarthome') {
      return _callSmartHome(name, arguments, smartHomeConfig);
    }
    throw ArgumentError('Unknown tool: $name');
  }

  static String _getWeather(Map<String, dynamic> arguments) {
    final city = arguments['city'] is String && (arguments['city'] as String).isNotEmpty
        ? arguments['city'] as String
        : 'Beijing';
    return jsonEncode({
      'city': city,
      'temperature': 25,
      'condition': 'Clear',
      'humidity': 45,
      'wind': 'Northeast wind, force 3',
      'aqi': 52,
    });
  }

  static String _getForecast(Map<String, dynamic> arguments) {
    final city = arguments['city'] is String && (arguments['city'] as String).isNotEmpty
        ? arguments['city'] as String
        : 'Beijing';
    final days = (_optionalInt(arguments['days']) ?? 3).clamp(1, 7).toInt();
    final forecast = [
      {'date': 'Today', 'high': 28, 'low': 18, 'condition': 'Clear'},
      {'date': 'Tomorrow', 'high': 26, 'low': 17, 'condition': 'Cloudy'},
      {'date': 'Day after tomorrow', 'high': 24, 'low': 15, 'condition': 'Light rain'},
    ];
    return jsonEncode({'city': city, 'forecast': forecast.take(days).toList()});
  }

  static Future<String> _queryIndonesiaHolidays(
    Map<String, dynamic> arguments,
  ) async {
    final action = (arguments['action'] as String? ?? 'list').trim().toLowerCase();
    if (action == 'check') {
      final date = (arguments['date'] as String? ?? '').trim();
      final parsedDate = DateTime.tryParse(date);
      if (parsedDate == null || _formatDate(parsedDate) != date) {
        return 'Parameter date tidak valid. Format: YYYY-MM-DD, contoh: 2026-08-17.';
      }
      final data = await _getJsonFromUri(
        Uri.parse('$_holidayBaseUrl/api/is-holiday?date=${Uri.encodeQueryComponent(date)}'),
      );
      if (data['is_holiday'] != true) return '$date bukan hari libur nasional.';
      final holiday = data['data'] is Map ? data['data'] as Map : <Object?, Object?>{};
      return '$date adalah hari libur: ${holiday['name'] ?? '?'}${_holidayFlags(holiday)}';
    }

    late final String endpoint;
    late final int year;
    if (action == 'list') {
      year = DateTime.now().year;
      endpoint = '/api/holidays/latest';
    } else if (action == 'year') {
      year = _optionalInt(arguments['year']) ?? 0;
      if (year < 2000 || year > 2100) {
        return 'Parameter year tidak valid ($year). Gunakan tahun antara 2000-2100.';
      }
      endpoint = '/api/holidays/$year.json';
    } else {
      return "Action '$action' tidak dikenal. Gunakan: 'list', 'year', atau 'check'.";
    }

    final data = await _getJsonFromUri(Uri.parse('$_holidayBaseUrl$endpoint'));
    final holidays = data['data'];
    if (holidays is! List || holidays.isEmpty) {
      return 'Tidak ada data libur nasional untuk tahun $year.';
    }
    final lines = <String>['Daftar libur nasional tahun $year:', ''];
    for (final entry in holidays.whereType<Map>()) {
      lines.add('  ${entry['date'] ?? '?'}  ${entry['name'] ?? '?'}${_holidayFlags(entry)}');
    }
    final output = lines.join('\n');
    return output.length <= 16000
        ? output
        : '${output.substring(0, 16000)}\n... (dipotong, total ${output.length} karakter)';
  }

  static String _holidayFlags(Map holiday) {
    final flags = <String>[];
    if (holiday['is_civic'] == true) flags.add('sibik');
    if (holiday['is_religious'] == true) flags.add('religious');
    if (holiday['is_cuti_bersama'] == true) flags.add('cuti bersama');
    return flags.isEmpty ? '' : ' [${flags.join(', ')}]';
  }

  static String _formatDate(DateTime date) =>
      '${date.year.toString().padLeft(4, '0')}-'
      '${date.month.toString().padLeft(2, '0')}-'
      '${date.day.toString().padLeft(2, '0')}';

  static const _newsFeeds = <String, List<String>>{
    'default': [
      'https://www.antaranews.com/rss/terkini.xml',
      'https://www.cnnindonesia.com/nasional/rss',
      'https://rss.tempo.co/nasional',
    ],
    'indonesia': [
      'https://www.antaranews.com/rss/terkini.xml',
      'https://www.cnnindonesia.com/nasional/rss',
      'https://rss.tempo.co/nasional',
    ],
    'nasional': [
      'https://www.cnnindonesia.com/nasional/rss',
      'https://rss.tempo.co/nasional',
      'https://www.antaranews.com/rss/terkini.xml',
    ],
    'teknologi': ['https://www.antaranews.com/rss/tekno.xml'],
    'tech': ['https://www.antaranews.com/rss/tekno.xml'],
    'olahraga': ['https://www.antaranews.com/rss/olahraga.xml'],
    'sepakbola': ['https://www.antaranews.com/rss/olahraga.xml'],
    'ekonomi': ['https://www.antaranews.com/rss/ekonomi.xml'],
    'finance': ['https://www.antaranews.com/rss/ekonomi.xml'],
    'hiburan': ['https://www.antaranews.com/rss/hiburan.xml'],
    'entertainment': ['https://www.antaranews.com/rss/hiburan.xml'],
    'dunia': ['https://www.antaranews.com/rss/dunia.xml'],
    'internasional': ['https://www.antaranews.com/rss/dunia.xml'],
  };

  static Future<String> _getNews(Map<String, dynamic> arguments) async {
    final topic = (arguments['topic'] as String? ?? '').trim();
    final keyword = topic.toLowerCase();
    final maxResults = (_optionalInt(arguments['max_results']) ?? 5).clamp(1, 20).toInt();
    final feeds = _newsFeeds[keyword] ?? _newsFeeds['default']!;
    final filter = _newsFeeds.containsKey(keyword) ? '' : keyword;
    final results = <Map<String, String>>[];
    final seenUrls = <String>{};
    for (final feed in feeds) {
      final articles = await _readRssFeed(
        Uri.parse(feed),
        maxResults: filter.isEmpty ? maxResults : maxResults * 3,
        keyword: filter,
      );
      for (final article in articles) {
        if (seenUrls.add(article['url']!)) results.add(article);
        if (results.length >= maxResults) break;
      }
      if (results.length >= maxResults) break;
    }
    if (results.isEmpty) {
      return "No news found for that topic. Try another topic, e.g. 'indonesia', "
          "'nasional', 'teknologi', 'olahraga', 'ekonomi', 'hiburan', or 'dunia'.";
    }
    return _formatArticles(results, 'RSS');
  }

  static Future<String> _webSearch(Map<String, dynamic> arguments) async {
    final query = _requiredString(arguments, 'query');
    final count = (_optionalInt(arguments['count']) ?? 5).clamp(1, 10).toInt();
    final language = (arguments['language'] as String? ?? 'en-US').trim();
    List<Map<String, String>> results = [];
    try {
      final response = await _getJsonFromUri(
        Uri.parse('https://api.anysearch.com/v1/search'),
        method: 'POST',
        body: {'query': query, 'max_results': count},
      );
      final nested = response['data'];
      final items = nested is Map ? nested['results'] : response['results'];
      if (items is List) {
        for (final item in items.whereType<Map>()) {
          final title = '${item['title'] ?? ''}'.trim();
          final url = '${item['url'] ?? ''}'.trim();
          if (title.isEmpty || url.isEmpty) continue;
          final snippet = _cleanHtml('${item['snippet'] ?? item['content'] ?? ''}');
          results.add({
            'title': title,
            'snippet': snippet.substring(0, snippet.length.clamp(0, 300).toInt()),
            'url': url,
            'publisher_url': '',
            'date': '',
            'source': '${item['engine'] ?? 'anysearch'}',
          });
          if (results.length >= count) break;
        }
      }
    } catch (_) {
      results = [];
    }

    if (results.isEmpty) {
      final gl = language.split('-').last.toUpperCase();
      final uri = Uri.https('news.google.com', '/rss/search', {
        'q': query,
        'hl': language,
        'gl': gl,
      });
      results = await _readGoogleNewsFeed(uri, count);
    }
    if (results.isEmpty) return "No results found for '$query'. Try different keywords.";
    return _formatArticles(results, 'Google News');
  }

  static Future<String> _readArticle(Map<String, dynamic> arguments) async {
    final rawUrl = _requiredString(arguments, 'url');
    final uri = Uri.tryParse(rawUrl);
    if (uri == null || !{'http', 'https'}.contains(uri.scheme) || uri.host.isEmpty) {
      return 'The article URL must be a valid HTTP or HTTPS URL.';
    }
    if (uri.host.toLowerCase().endsWith('news.google.com') &&
        uri.path.contains('/articles/')) {
      return 'This is a Google News redirect link. Use the Publisher URL from the search result or open a direct article URL.';
    }
    final maxChars = (_optionalInt(arguments['max_chars']) ?? 6000).clamp(200, 20000).toInt();
    try {
      final raw = await _getTextFromUri(uri, headers: const {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36',
      });
      final document = html_parser.parse(raw);
      for (final node in document.querySelectorAll(
        'script,style,noscript,iframe,svg,nav,footer,header,aside,form',
      )) {
        node.remove();
      }
      final paragraphs = document
          .querySelectorAll('article p, main p, p')
          .map((element) => element.text.trim().replaceAll(RegExp(r'\s+'), ' '))
          .where((text) => text.length >= 40)
          .toList();
      var article = paragraphs.isNotEmpty
          ? paragraphs.join('\n\n')
          : (document.body?.text ?? '').replaceAll(RegExp(r'\s+'), ' ').trim();
      if (article.isEmpty) return 'Could not extract readable article content.';
      if (article.length > maxChars) article = article.substring(0, maxChars);
      return article;
    } catch (error) {
      return 'Failed to read the article: $error';
    }
  }

  static Future<List<Map<String, String>>> _readRssFeed(
    Uri uri, {
    required int maxResults,
    String keyword = '',
  }) async {
    try {
      final body = await _getTextFromUri(uri, headers: const {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36',
      });
      final document = XmlDocument.parse(body);
      final results = <Map<String, String>>[];
      for (final item in document.findAllElements('item')) {
        final title = item.getElement('title')?.innerText.trim() ?? '';
        final url = item.getElement('link')?.innerText.trim() ?? '';
        if (title.isEmpty || url.isEmpty) continue;
        final rawDescription = item.getElement('description')?.innerText ?? '';
        final snippet = _cleanHtml(rawDescription);
        if (keyword.isNotEmpty &&
            !title.toLowerCase().contains(keyword) &&
            !snippet.toLowerCase().contains(keyword)) {
          continue;
        }
        final source = item.getElement('source');
        results.add({
          'title': title,
          'snippet': snippet.substring(0, snippet.length.clamp(0, 300).toInt()),
          'url': url,
          'publisher_url': source?.getAttribute('url') ?? '',
          'date': item.getElement('pubDate')?.innerText.trim() ?? '',
          'source': source?.innerText.trim().isNotEmpty == true
              ? source!.innerText.trim()
              : 'RSS',
        });
        if (results.length >= maxResults) break;
      }
      return results;
    } catch (_) {
      return [];
    }
  }

  static Future<List<Map<String, String>>> _readGoogleNewsFeed(
    Uri uri,
    int count,
  ) async =>
      _readRssFeed(uri, maxResults: count);

  static String _formatArticles(List<Map<String, String>> articles, String source) {
    final lines = <String>[];
    for (var index = 0; index < articles.length; index++) {
      final article = articles[index];
      lines.add(
        '${index + 1}. ${article['title']}\n'
        '   Source: ${article['source']} | ${article['date']}\n'
        '   ${article['snippet']}\n'
        '   Link: ${article['url']}'
        '${article['publisher_url']!.isEmpty ? '' : '\n   Publisher: ${article['publisher_url']}'}',
      );
    }
    return 'Source: $source\n\n${lines.join('\n\n')}';
  }

  static String _cleanHtml(String value) {
    final document = html_parser.parse(value);
    for (final node in document.querySelectorAll('script,style,noscript')) {
      node.remove();
    }
    return (document.body?.text ?? document.documentElement?.text ?? value)
        .replaceAll(RegExp(r'\s+'), ' ')
        .trim();
  }

  static Future<String> _callSmartHome(
    String name,
    Map<String, dynamic> arguments,
    Map<String, Object?> config,
  ) async {
    final broker = '${config['broker'] ?? ''}'.trim();
    if (broker.isEmpty) {
      throw StateError('Set the Smart Home MQTT broker IP/host in Android settings first.');
    }
    if (_discoveredSmartHomeBroker != broker) {
      _discoveredSmartHomeBroker = broker;
      _discoveredSmartHomeDevices = [];
    }
    final port = _optionalInt(config['port']) ?? 1883;
    if (port < 1 || port > 65535) throw ArgumentError('Invalid MQTT port: $port');
    List<Map<String, Object?>> configuredDevices;
    try {
      configuredDevices = _parseSmartHomeDevices('${config['devices'] ?? '[]'}');
    } on FormatException {
      if (name != 'discover_devices') rethrow;
      configuredDevices = [];
    }
    var devices = _mergeSmartHomeDevices(
      configuredDevices,
      _discoveredSmartHomeDevices,
    );
    final clientId = 'xz${DateTime.now().microsecondsSinceEpoch % 1000000000000}';
    final client = MqttServerClient(broker, clientId)
      ..port = port
      ..secure = config['useTls'] == true || port == 8883
      ..keepAlivePeriod = 20
      ..connectTimeoutPeriod = 10000
      ..logging(on: false)
      ..connectionMessage = MqttConnectMessage()
          .withClientIdentifier(clientId)
          .authenticateAs(
            '${config['username'] ?? ''}'.isEmpty
                ? null
                : '${config['username']}',
            '${config['password'] ?? ''}'.isEmpty
                ? null
                : '${config['password']}',
          )
          .startClean();

    StreamSubscription<List<MqttReceivedMessage<MqttMessage>>>? updates;
    // Tasmota buffers (kept, as before).
    final states = <String, String>{};
    final online = <String, String>{};
    final discovered = <String, Map<String, Object?>>{};
    final queriedLwtTopics = <String>{};
    // Generic-MQTT discovery buffers.
    final haConfigs = <String, Map<String, Object?>>{};
    final z2mEntries = <String, Map<String, Object?>>{};
    final shellyRelays = <String, String>{};
    final shellyModels = <String, String>{};
    final shellyGen2States = <String, String>{};
    final genericTopics = <String, String>{};
    // State for devices already known at listener time (exact topic match).
    final knownStates = <String, Map<String, Object?>>{};
    final knownOnline = <String, bool?>{};
    try {
      await client.connect().timeout(const Duration(seconds: 12));
      if (client.connectionStatus?.state != MqttConnectionState.connected) {
        throw StateError('Could not connect to MQTT broker $broker:$port');
      }
      updates = client.updates?.listen((messages) {
        for (final message in messages) {
          final packet = message.payload;
          if (packet is! MqttPublishMessage) continue;
          final payload = MqttPublishPayload.bytesToStringAsString(
            packet.payload.message,
          ).trim();
          final topic = message.topic;
          // 1. Known device, exact state / availability topic.
          var handled = false;
          for (final device in devices) {
            final id = '${device['id'] ?? ''}';
            final stateTopic = '${device['stateTopic'] ?? ''}';
            if (stateTopic.isNotEmpty && topic == stateTopic) {
              knownStates[id] = _smartHomeParseState(
                payload,
                stateKey: device['stateKey'] as String?,
                payloadOn: '${device['payloadOn'] ?? 'ON'}',
                payloadOff: '${device['payloadOff'] ?? 'OFF'}',
              );
              handled = true;
              break;
            }
            final availabilityTopic = '${device['availabilityTopic'] ?? ''}';
            if (availabilityTopic.isNotEmpty && topic == availabilityTopic) {
              if (payload == '${device['availabilityOn'] ?? ''}') {
                knownOnline[id] = true;
              } else if (payload == '${device['availabilityOff'] ?? ''}') {
                knownOnline[id] = false;
              } else if ({'online', 'true', '1'}.contains(payload.toLowerCase())) {
                knownOnline[id] = true;
              } else if ({'offline', 'false', '0'}.contains(payload.toLowerCase())) {
                knownOnline[id] = false;
              }
              handled = true;
              break;
            }
          }
          if (handled) continue;
          // 2. Tasmota patterns (also feed discovery buffers).
          if (topic.startsWith('stat/')) {
            final parts = topic.split('/');
            if (parts.length == 3) states['${parts[1]}/${parts[2]}'] = payload;
          } else if (topic.startsWith('tele/') && topic.endsWith('/LWT')) {
            final parts = topic.split('/');
            if (parts.length == 3) {
              final deviceTopic = parts[1];
              online[deviceTopic] = payload;
              if (name == 'discover_devices' &&
                  payload == 'Online' &&
                  queriedLwtTopics.add(deviceTopic)) {
                for (final command in ['POWER', 'POWER1', 'POWER2', 'POWER3', 'POWER4']) {
                  _publishMqtt(client, 'cmnd/$deviceTopic/$command', '');
                }
              }
            }
          } else if (topic.startsWith('tasmota/discovery/') && topic.endsWith('/config')) {
            try {
              final decoded = jsonDecode(payload);
              if (decoded is Map) {
                for (final device in _devicesFromTasmotaConfig(decoded)) {
                  discovered['${device['id']}'] = device;
                }
              }
            } catch (_) {}
          } else if (topic.startsWith('homeassistant/') && topic.endsWith('/config')) {
            try {
              final decoded = jsonDecode(payload);
              if (decoded is Map) {
                haConfigs[topic] = Map<String, Object?>.from(decoded);
              }
            } catch (_) {}
          } else if (topic == 'zigbee2mqtt/bridge/devices') {
            try {
              final decoded = jsonDecode(payload);
              if (decoded is List) {
                for (final entry in decoded.whereType<Map>()) {
                  final friendly = '${entry['friendly_name'] ?? ''}'.trim();
                  if (friendly.isNotEmpty) {
                    z2mEntries[friendly] = Map<String, Object?>.from(entry);
                  }
                }
              }
            } catch (_) {}
          } else if (topic.startsWith('zigbee2mqtt/')) {
            // State/availability for a known device were handled above;
            // discovery itself only needs bridge/devices (handled earlier).
            if (name == 'discover_devices' &&
                topic.substring('zigbee2mqtt/'.length).contains('/') &&
                genericTopics.length < 1000) {
              genericTopics[topic] = payload;
            }
          } else if (topic.startsWith('shellies/')) {
            final parts = topic.split('/');
            if (parts.length == 4 && parts[2] == 'relay') {
              shellyRelays['${parts[1]}/${parts[3]}'] = payload;
            } else if (parts.length == 3 && parts[2] == 'announce') {
              try {
                final decoded = jsonDecode(payload);
                if (decoded is Map && decoded['model'] != null) {
                  shellyModels[parts[1]] = '${decoded['model']}';
                }
              } catch (_) {}
            }
          } else if (RegExp(r'^(.+)/status/(switch|light):(\d+)$').hasMatch(topic) &&
              topic.split('/').first.startsWith('shelly')) {
            shellyGen2States[topic] = payload;
          } else if (name == 'discover_devices' &&
              !topic.startsWith(r'$SYS/') &&
              genericTopics.length < 1000) {
            genericTopics[topic] = payload;
          }
        }
      });

      List<Map<String, Object?>> buildFound() => _buildSmartHomeDiscovery(
            tasmotaDiscovered: discovered.values.toList(),
            online: online,
            states: states,
            haConfigs: haConfigs,
            z2mEntries: z2mEntries,
            shellyRelays: shellyRelays,
            shellyModels: shellyModels,
            shellyGen2States: shellyGen2States,
            genericTopics: genericTopics,
          );

      if (devices.isEmpty && name != 'discover_devices') {
        client.subscribe('tasmota/discovery/#', MqttQos.atMostOnce);
        client.subscribe('tele/+/LWT', MqttQos.atMostOnce);
        client.subscribe('homeassistant/#', MqttQos.atMostOnce);
        client.subscribe('zigbee2mqtt/bridge/devices', MqttQos.atMostOnce);
        client.subscribe('zigbee2mqtt/+/availability', MqttQos.atMostOnce);
        client.subscribe('zigbee2mqtt/+', MqttQos.atMostOnce);
        client.subscribe('shellies/+/announce', MqttQos.atMostOnce);
        client.subscribe('shellies/+/online', MqttQos.atMostOnce);
        client.subscribe('shellies/+/relay/+', MqttQos.atMostOnce);
        await Future<void>.delayed(const Duration(seconds: 4));
        final found = buildFound();
        if (found.isNotEmpty) {
          _discoveredSmartHomeDevices = _mergeSmartHomeDevices(
            _discoveredSmartHomeDevices,
            found,
          );
          devices = _mergeSmartHomeDevices(configuredDevices, _discoveredSmartHomeDevices);
        }
      }

      if (name == 'discover_devices') {
        client.subscribe('tasmota/discovery/#', MqttQos.atMostOnce);
        client.subscribe('tele/+/LWT', MqttQos.atMostOnce);
        for (final command in ['POWER', 'POWER1', 'POWER2', 'POWER3', 'POWER4']) {
          client.subscribe('stat/+/$command', MqttQos.atMostOnce);
        }
        client.subscribe('homeassistant/#', MqttQos.atMostOnce);
        client.subscribe('zigbee2mqtt/bridge/devices', MqttQos.atMostOnce);
        client.subscribe('zigbee2mqtt/+/availability', MqttQos.atMostOnce);
        client.subscribe('zigbee2mqtt/+', MqttQos.atMostOnce);
        client.subscribe('shellies/+/announce', MqttQos.atMostOnce);
        client.subscribe('shellies/+/online', MqttQos.atMostOnce);
        client.subscribe('shellies/+/relay/+', MqttQos.atMostOnce);
        // Generic scan: read-only, capped at 1000 topics (see listener).
        // Never publishes to a topic it merely observed.
        client.subscribe('#', MqttQos.atMostOnce);
        await Future<void>.delayed(const Duration(seconds: 5));
        final found = buildFound();
        if (found.isEmpty) {
          return devices.isEmpty
              ? 'No device found. Check that the broker host, port, credentials, and TLS settings are correct and that devices are online on this broker (Tasmota, Home Assistant discovery, Zigbee2MQTT, Shelly, or a generic MQTT topic).'
              : 'No new devices discovered. ${devices.length} configured device(s) remain available.';
        }
        _discoveredSmartHomeDevices = _mergeSmartHomeDevices(
          _discoveredSmartHomeDevices,
          found,
        );
        final availableDevices = _mergeSmartHomeDevices(
          configuredDevices,
          _discoveredSmartHomeDevices,
        );
        return 'Discovered ${found.length} device(s):\n${jsonEncode(found)}\n'
            '${availableDevices.length} device(s) are now available for device_status, '
            'device_control, lights_all, room_control, and device_details. No JSON copy is needed.';
      }

      if (devices.isEmpty) {
        throw StateError(
          'No devices were discovered on this broker. Check that devices are online and announce MQTT discovery, or enter device topics manually in Smart Home settings.',
        );
      }
      for (final device in devices) {
        final stateTopic = '${device['stateTopic'] ?? ''}';
        if (stateTopic.isNotEmpty) {
          client.subscribe(stateTopic, MqttQos.atMostOnce);
        }
        final availabilityTopic = '${device['availabilityTopic'] ?? ''}';
        if (availabilityTopic.isNotEmpty) {
          client.subscribe(availabilityTopic, MqttQos.atMostOnce);
        }
        final pollTopic = '${device['pollTopic'] ?? ''}';
        if (pollTopic.isNotEmpty) {
          _publishMqtt(client, pollTopic, '');
        }
      }
      await Future<void>.delayed(const Duration(milliseconds: 350));

      if (name == 'device_status') {
        final lines = <String>['Smart Home — ${devices.length} device(s)'];
        for (var index = 0; index < devices.length; index++) {
          final device = devices[index];
          final id = '${device['id'] ?? ''}';
          final known = knownStates[id];
          String stateText;
          if (known != null) {
            final state = known['state'] as bool?;
            stateText = state == true
                ? 'ON'
                : state == false
                    ? 'OFF'
                    : (known['raw'] as String? ?? '').isNotEmpty
                        ? (known['raw'] as String).length > 20
                            ? (known['raw'] as String).substring(0, 20)
                            : (known['raw'] as String)
                        : 'unknown';
          } else {
            final legacyKey = '${device['topic']}/${device['powerCmd']}';
            stateText = states[legacyKey] ?? 'unknown';
          }
          String onlineText;
          if (knownOnline.containsKey(id)) {
            onlineText = knownOnline[id] == true
                ? 'Online'
                : knownOnline[id] == false
                    ? 'Offline'
                    : 'unknown';
          } else {
            onlineText = online['${device['topic']}'] ?? 'unknown';
          }
          final ro = device['controllable'] == true ? '' : ' RO';
          lines.add(
            '${index + 1}. ${device['name']} [${device['type']}] '
            '$stateText · $onlineText · ${device['room']} (${device['protocol']})$ro',
          );
        }
        return lines.join('\n');
      }

      if (name == 'device_control') {
        final index = _requiredInt(arguments, 'deviceIndex') - 1;
        if (index < 0 || index >= devices.length) {
          throw RangeError('Invalid device index: ${index + 1}');
        }
        final device = devices[index];
        final id = '${device['id'] ?? ''}';
        final known = knownStates[id];
        String? currentState;
        if (known != null && known['state'] is bool) {
          currentState = (known['state'] as bool) ? 'ON' : 'OFF';
        } else {
          currentState = states['${device['topic']}/${device['powerCmd']}'];
        }
        final rawAction = arguments['action'] as String? ?? 'TOGGLE';
        if (rawAction.trim().toUpperCase() == 'TOGGLE' && currentState == null) {
          throw ArgumentError('Cannot TOGGLE: current state is unknown, use ON or OFF.');
        }
        final action = _smartHomeAction(rawAction, currentState: currentState);
        _publishDevice(client, device, action);
        return '${device['name']} -> $action';
      }

      if (name == 'device_details') {
        final index = _requiredInt(arguments, 'deviceIndex') - 1;
        if (index < 0 || index >= devices.length) {
          throw RangeError('Invalid device index: ${index + 1}');
        }
        final device = devices[index];
        final id = '${device['id'] ?? ''}';
        final known = knownStates[id];
        final stateText = known == null
            ? 'unknown'
            : known['state'] is bool
                ? (known['state'] as bool ? 'ON' : 'OFF')
                : '${known['raw'] ?? ''}';
        final onlineText = knownOnline.containsKey(id)
            ? (knownOnline[id] == true
                ? 'Online'
                : knownOnline[id] == false
                    ? 'Offline'
                    : 'unknown')
            : online['${device['topic']}'] ?? 'unknown';
        return [
          'Device ${index + 1}: ${device['name']}',
          '  protocol: ${device['protocol']}',
          '  type: ${device['type']}  room: ${device['room']}',
          '  state_topic: ${device['stateTopic'] ?? ''}',
          '  command_topic: ${device['commandTopic'] ?? ''}',
          '  payload_on/off: ${device['payloadOn']} / ${device['payloadOff']}',
          '  state_key: ${device['stateKey'] ?? '-'}',
          '  availability_topic: ${device['availabilityTopic'] ?? ''}',
          '  controllable: ${device['controllable']}',
          '  state: $stateText  online: $onlineText',
          '  source: ${device['source']}',
        ].join('\n');
      }

      if (name == 'lights_all') {
        final action = _requiredString(arguments, 'action').trim().toUpperCase();
        if (action != 'ON' && action != 'OFF') {
          throw ArgumentError('Action must be ON or OFF.');
        }
        final lights = devices
            .where((device) => device['type'] == 'light' && device['controllable'] == true)
            .toList();
        if (lights.isEmpty) return 'No controllable lights found';
        for (final device in lights) {
          _publishDevice(client, device, action);
        }
        return 'All ${lights.length} lights -> $action';
      }

      if (name == 'room_control') {
        final room = _requiredString(arguments, 'room');
        final action = (arguments['action'] as String? ?? 'ON').trim().toUpperCase();
        if (action != 'ON' && action != 'OFF') {
          throw ArgumentError('Action must be ON or OFF.');
        }
        final roomDevices = devices
            .where((device) => '${device['room']}'.toLowerCase() == room.toLowerCase())
            .toList();
        if (roomDevices.isEmpty) throw ArgumentError('Room not found: $room');
        final controllable =
            roomDevices.where((device) => device['controllable'] == true).toList();
        if (controllable.isEmpty) {
          return "Room '$room' has ${roomDevices.length} device(s) but none are controllable";
        }
        for (final device in controllable) {
          _publishDevice(client, device, action);
        }
        return '${controllable.length} devices in $room -> $action';
      }
      throw ArgumentError('Unknown Smart Home tool: $name');
    } finally {
      await updates?.cancel();
      client.disconnect();
    }
  }
  // ── Smart Home: generic MQTT device model ──────────────────────────
  // Mirrors the Python patch (src/mcp/tools/smarthome/service.py).
  // A device is one map with, besides the legacy 'topic'/'powerCmd'
  // (kept for Tasmota), the fields below. 'controllable' is false for
  // every device whose command topic is unknown — control then throws
  // instead of publishing to a guessed topic.

  static String _smartHomeString(Object? value, [String fallback = '']) {
    if (value == null) return fallback;
    return '$value';
  }

  static Object? _smartHomeJsonValue(String payload, String key) {
    try {
      final decoded = jsonDecode(payload);
      var current = decoded;
      for (final part in key.split('.')) {
        if (current is Map && current.containsKey(part)) {
          current = current[part];
        } else {
          return null;
        }
      }
      return current;
    } catch (_) {
      return null;
    }
  }

  static bool? _smartHomeBoolFromValue(
    Object? value, {
    String payloadOn = 'ON',
    String payloadOff = 'OFF',
  }) {
    if (value is bool) return value;
    if (value is num) {
      if (value == 1) return true;
      if (value == 0) return false;
      return null;
    }
    final text = _smartHomeString(value).trim();
    if (text.isEmpty) return null;
    if (text == payloadOn || {'on', 'true', '1', 'yes'}.contains(text.toLowerCase())) {
      return true;
    }
    if (text == payloadOff || {'off', 'false', '0', 'no'}.contains(text.toLowerCase())) {
      return false;
    }
    return null;
  }

  static Map<String, Object?> _smartHomeParseState(
    String payload, {
    String? stateKey,
    String payloadOn = 'ON',
    String payloadOff = 'OFF',
  }) {
    final raw = payload.trim();
    if (stateKey != null && stateKey.isNotEmpty) {
      final value = _smartHomeJsonValue(raw, stateKey);
      if (value != null) {
        return {'state': _smartHomeBoolFromValue(value, payloadOn: payloadOn, payloadOff: payloadOff), 'raw': raw};
      }
    }
    if (raw.startsWith('{')) {
      final value = _smartHomeJsonValue(raw, 'state');
      if (value != null) {
        return {'state': _smartHomeBoolFromValue(value, payloadOn: payloadOn, payloadOff: payloadOff), 'raw': raw};
      }
      return {'state': null, 'raw': raw};
    }
    return {'state': _smartHomeBoolFromValue(raw, payloadOn: payloadOn, payloadOff: payloadOff), 'raw': raw};
  }

  static String _smartHomeDeviceId({
    required String protocol,
    required String stateTopic,
    required String commandTopic,
    required String topic,
    required String powerCmd,
  }) {
    if (protocol == 'tasmota' && topic.isNotEmpty) {
      return 'tasmota:$topic:${powerCmd.isNotEmpty ? powerCmd : 'POWER'}';
    }
    if (stateTopic.isNotEmpty) return '$protocol:$stateTopic';
    return '$protocol:$topic:$powerCmd';
  }

  static Map<String, Object?> _smartHomeDevice({
    required String protocol,
    required String name,
    String stateTopic = '',
    String commandTopic = '',
    String topic = '',
    String powerCmd = 'POWER',
    String room = 'unknown',
    String type = 'switch',
    String payloadOn = 'ON',
    String payloadOff = 'OFF',
    String? stateKey,
    String availabilityTopic = '',
    String availabilityOn = 'Online',
    String availabilityOff = 'Offline',
    String pollTopic = '',
    bool? controllable,
    String source = 'discovered',
  }) {
    final id = _smartHomeDeviceId(
      protocol: protocol,
      stateTopic: stateTopic,
      commandTopic: commandTopic,
      topic: topic,
      powerCmd: powerCmd,
    );
    return {
      'id': id,
      'protocol': protocol,
      'topic': topic,
      'powerCmd': powerCmd,
      'name': name.isNotEmpty ? name : (stateTopic.isNotEmpty ? stateTopic : topic),
      'room': room.isNotEmpty ? room : 'unknown',
      'type': type,
      'stateTopic': stateTopic,
      'commandTopic': commandTopic,
      'payloadOn': payloadOn,
      'payloadOff': payloadOff,
      'stateKey': stateKey,
      'availabilityTopic': availabilityTopic,
      'availabilityOn': availabilityOn,
      'availabilityOff': availabilityOff,
      'pollTopic': pollTopic,
      'controllable': controllable ?? commandTopic.isNotEmpty,
      'source': source,
    };
  }

  static Map<String, Object?> _tasmotaSmartHomeDevice(
    String topic,
    String powerCmd,
    String name, {
    String room = 'unknown',
    String type = 'light',
    String source = 'discovered',
  }) =>
      _smartHomeDevice(
        protocol: 'tasmota',
        name: name,
        topic: topic,
        powerCmd: powerCmd,
        stateTopic: 'stat/$topic/$powerCmd',
        commandTopic: 'cmnd/$topic/$powerCmd',
        availabilityTopic: 'tele/$topic/LWT',
        availabilityOn: 'Online',
        availabilityOff: 'Offline',
        pollTopic: 'cmnd/$topic/$powerCmd',
        room: room,
        type: type,
        source: source,
      );

  static Map<String, Object?>? _haSmartHomeDevice(String configTopic, Map config) {
    final parts = configTopic.split('/');
    if (parts.length < 3 || parts[0] != 'homeassistant') return null;
    final component = parts[1];
    final stateTopic = _smartHomeString(config['state_topic']).trim();
    if (stateTopic.isEmpty) return null;
    var commandTopic = _smartHomeString(config['command_topic']).trim();
    var name = _smartHomeString(config['name']).trim();
    final deviceInfo = config['device'] is Map ? config['device'] as Map : <String, Object?>{};
    if (name.isEmpty) {
      name = _smartHomeString(deviceInfo['name']).trim();
      if (name.isEmpty) name = stateTopic;
    }
    String type;
    if (component == 'light' || component == 'switch') {
      type = component;
    } else if (component == 'binary_sensor' || component == 'sensor') {
      type = 'sensor';
    } else {
      // cover / fan / lock / climate: command payloads are not ON/OFF
      // (open/close, ...), so v1 shows them read-only.
      type = component;
      commandTopic = '';
    }
    String? stateKey;
    final template = _smartHomeString(config['value_template']);
    final match = RegExp(r'value_json\.([A-Za-z0-9_.-]+)').firstMatch(template);
    if (match != null) stateKey = match.group(1);
    return _smartHomeDevice(
      protocol: 'homeassistant',
      name: name,
      stateTopic: stateTopic,
      commandTopic: commandTopic,
      payloadOn: _smartHomeString(config['payload_on'], 'ON'),
      payloadOff: _smartHomeString(config['payload_off'], 'OFF'),
      stateKey: stateKey,
      availabilityTopic: _smartHomeString(config['availability_topic']).trim(),
      availabilityOn: _smartHomeString(config['payload_available'], 'online'),
      availabilityOff: _smartHomeString(config['payload_not_available'], 'offline'),
      room: 'unknown',
      type: type,
      controllable: commandTopic.isNotEmpty && (component == 'light' || component == 'switch'),
    );
  }

  static Map<String, Object?>? _z2mSmartHomeDevice(Map entry) {
    final name = _smartHomeString(entry['friendly_name']).trim();
    if (name.isEmpty) return null;
    final stateTopic = 'zigbee2mqtt/$name';
    final definition = entry['definition'] is Map ? entry['definition'] as Map : <String, Object?>{};
    final exposes = definition['exposes'] is List ? definition['exposes'] as List : <Object?>[];
    String? kind;
    for (final expose in exposes.whereType<Map>()) {
      if (expose['type'] == 'light' || expose['type'] == 'switch') {
        final features = expose['features'] is List ? expose['features'] as List : <Object?>[];
        if (features.whereType<Map>().any((f) => f['property'] == 'state')) {
          kind = '${expose['type']}';
          break;
        }
      }
    }
    if (kind != null) {
      return _smartHomeDevice(
        protocol: 'zigbee2mqtt',
        name: name,
        stateTopic: stateTopic,
        commandTopic: '$stateTopic/set',
        stateKey: 'state',
        availabilityTopic: '$stateTopic/availability',
        availabilityOn: 'online',
        availabilityOff: 'offline',
        type: kind,
      );
    }
    return _smartHomeDevice(
      protocol: 'zigbee2mqtt',
      name: name,
      stateTopic: stateTopic,
      availabilityTopic: '$stateTopic/availability',
      availabilityOn: 'online',
      availabilityOff: 'offline',
      type: 'sensor',
      controllable: false,
    );
  }

  static Map<String, Object?> _shellyGen1SmartHomeDevice(String deviceId, int relay, String model) {
    final base = 'shellies/$deviceId';
    return _smartHomeDevice(
      protocol: 'shelly',
      name: '${model.isNotEmpty ? model : deviceId} relay $relay',
      stateTopic: '$base/relay/$relay',
      commandTopic: '$base/relay/$relay/command',
      payloadOn: 'on',
      payloadOff: 'off',
      availabilityTopic: '$base/online',
      availabilityOn: 'true',
      availabilityOff: 'false',
      type: 'switch',
    );
  }

  static Map<String, Object?> _shellyGen2SmartHomeDevice(String prefix, String kind, int index) =>
      _smartHomeDevice(
        protocol: 'shelly',
        name: '$prefix $kind $index',
        stateTopic: '$prefix/status/$kind:$index',
        commandTopic: '$prefix/rpc',
        stateKey: 'output',
        type: kind == 'light' ? 'light' : 'switch',
      );

  static const _smartHomeSkipTopicSuffixes = <String>[
    '/config',
    '/announce',
    '/set',
    '/command',
    '/cmnd',
    '/rpc',
    '/availability',
    '/online',
    '/LWT',
    '/info',
  ];

  static Map<String, Object?>? _genericSmartHomeDevice(String topic, String payload) {
    if (topic.startsWith(r'$SYS/') ||
        topic.startsWith('homeassistant/') ||
        topic.startsWith('tasmota/discovery/') ||
        topic.startsWith('zigbee2mqtt/bridge') ||
        topic.startsWith('shellies/')) {
      return null;
    }
    if (_smartHomeSkipTopicSuffixes.any((suffix) => topic.endsWith(suffix))) return null;
    final parts = topic.split('/').where((p) => p.isNotEmpty).toList();
    if (parts.length < 2) return null;
    final parsed = _smartHomeParseState(payload);
    final state = parsed['state'] as bool?;
    String name;
    String type;
    if (state != null) {
      type = {'light', 'lamp', 'lampu'}.any((word) => topic.toLowerCase().contains(word))
          ? 'light'
          : 'switch';
      name = parts.length >= 2 ? parts[parts.length - 2].replaceAll('_', ' ').replaceAll('-', ' ') : topic;
    } else {
      type = 'sensor';
      if (parts.length >= 3 && parts[0].toLowerCase() == 'xiaomi') {
        name = parts[1].replaceAll('_', ' ').replaceAll('-', ' ');
      } else if ({'status', 'state'}.contains(parts.last.toLowerCase()) && parts.length >= 2) {
        name = parts[parts.length - 2].replaceAll('_', ' ').replaceAll('-', ' ');
      } else {
        name = parts.map((p) => p.replaceAll('_', ' ').replaceAll('-', ' ')).join(' ');
      }
    }
    return _smartHomeDevice(
      protocol: 'generic',
      name: name,
      stateTopic: topic,
      type: type,
      controllable: false,
    );
  }

  static Map<String, String> _smartHomeControlMessage(
    Map<String, Object?> device,
    String action,
  ) {
    if (device['controllable'] != true) {
      throw ArgumentError(
        "Device '${device['name']}' is read-only "
        "(protocol=${device['protocol']}, state_topic=${device['stateTopic']}); "
        'configure a command_topic to make it controllable.',
      );
    }
    if (action != 'ON' && action != 'OFF') {
      throw ArgumentError('Action must be ON or OFF');
    }
    final protocol = '${device['protocol']}';
    final commandTopic = '${device['commandTopic']}';
    if (protocol == 'zigbee2mqtt') {
      return {
        'topic': commandTopic,
        'payload': jsonEncode({
          'state': action == 'ON' ? '${device['payloadOn']}' : '${device['payloadOff']}',
        }),
      };
    }
    if (protocol == 'shelly' && commandTopic.endsWith('/rpc')) {
      final match = RegExp(r'/status/(switch|light):(\d+)$').firstMatch('${device['stateTopic']}');
      final kind = match?.group(1) ?? 'switch';
      final index = match != null ? int.tryParse(match.group(2)!) ?? 0 : 0;
      return {
        'topic': commandTopic,
        'payload': jsonEncode({
          'id': 0,
          'src': 'xiaozhi',
          'method': kind == 'light' ? 'Light.Set' : 'Switch.Set',
          'params': {'id': index, 'on': action == 'ON'},
        }),
      };
    }
    return {
      'topic': commandTopic,
      'payload': action == 'ON' ? '${device['payloadOn']}' : '${device['payloadOff']}',
    };
  }

  static Map<String, Object?> _normalizeSmartHomeConfigDevice(Map item) {
    final protocol = _smartHomeString(item['protocol']).trim().toLowerCase();
    final stateTopic = _smartHomeString(item['state_topic'] ?? item['stateTopic']).trim();
    final commandTopic = _smartHomeString(item['command_topic'] ?? item['commandTopic']).trim();
    final topic = _smartHomeString(item['topic']).trim();
    final powerCmd = _smartHomeString(item['power_cmd'] ?? item['powerCmd'], 'POWER');
    final name = _smartHomeString(item['name']).trim();
    final room = _smartHomeString(item['room'], 'unknown');
    final type = _smartHomeString(item['type'], 'switch');
    if ((protocol == 'tasmota' && topic.isNotEmpty) ||
        (topic.isNotEmpty && stateTopic.isEmpty && protocol.isEmpty)) {
      final device = _tasmotaSmartHomeDevice(
        topic,
        powerCmd,
        name.isNotEmpty ? name : topic,
        room: room,
        type: _smartHomeString(item['type'], 'light'),
        source: 'configured',
      );
      return device;
    }
    final effectiveProtocol = protocol.isNotEmpty ? protocol : 'generic';
    bool? controllable;
    if (item['controllable'] is bool) controllable = item['controllable'] as bool;
    return _smartHomeDevice(
      protocol: effectiveProtocol,
      name: name.isNotEmpty ? name : (stateTopic.isNotEmpty ? stateTopic : topic),
      stateTopic: stateTopic,
      commandTopic: commandTopic,
      topic: topic,
      powerCmd: powerCmd,
      room: room,
      type: type,
      payloadOn: _smartHomeString(item['payload_on'] ?? item['payloadOn'], 'ON'),
      payloadOff: _smartHomeString(item['payload_off'] ?? item['payloadOff'], 'OFF'),
      stateKey: _smartHomeString(item['state_key'] ?? item['stateKey']).isNotEmpty
          ? _smartHomeString(item['state_key'] ?? item['stateKey'])
          : null,
      availabilityTopic: _smartHomeString(item['availability_topic'] ?? item['availabilityTopic']).trim(),
      availabilityOn: _smartHomeString(item['availability_on'] ?? item['availabilityOn'], 'Online'),
      availabilityOff: _smartHomeString(item['availability_off'] ?? item['availabilityOff'], 'Offline'),
      pollTopic: _smartHomeString(item['poll_topic'] ?? item['pollTopic']).trim(),
      controllable: controllable,
      source: 'configured',
    );
  }

  @visibleForTesting
  static List<Map<String, Object?>> parseSmartHomeDevicesForTest(String raw) =>
      _parseSmartHomeDevices(raw);

  static List<Map<String, Object?>> _parseSmartHomeDevices(String raw) {
    final decoded = jsonDecode(raw);
    if (decoded is! List) throw const FormatException('Devices must be a JSON array');
    final devices = <Map<String, Object?>>[];
    for (final item in decoded.whereType<Map>()) {
      final map = Map<String, Object?>.from(item);
      final hasTopic = _smartHomeString(map['topic']).trim().isNotEmpty;
      final hasStateTopic =
          _smartHomeString(map['state_topic'] ?? map['stateTopic']).trim().isNotEmpty;
      if (!hasTopic && !hasStateTopic) continue;
      devices.add(_normalizeSmartHomeConfigDevice(map));
    }
    return devices;
  }

  static List<Map<String, Object?>> _mergeSmartHomeDevices(
    List<Map<String, Object?>> configured,
    List<Map<String, Object?>> discovered,
  ) {
    final byId = <String, Map<String, Object?>>{};
    for (final device in discovered) {
      byId['${device['id'] ?? '${device['topic']}/${device['powerCmd']}'}'] = device;
    }
    for (final device in configured) {
      final id = '${device['id'] ?? '${device['topic']}/${device['powerCmd']}'}';
      final existing = byId[id];
      if (existing == null) {
        byId[id] = device;
      } else {
        final merged = Map<String, Object?>.from(existing);
        for (final key in ['name', 'room', 'type']) {
          if ('${device[key] ?? ''}'.isNotEmpty) merged[key] = device[key];
        }
        merged['source'] = 'configured';
        byId[id] = merged;
      }
    }
    // Configured devices keep their order first (stable device numbers);
    // newly discovered devices follow.
    final order = <String>[];
    for (final device in configured) {
      order.add('${device['id'] ?? '${device['topic']}/${device['powerCmd']}'}');
    }
    final configuredIds = order.toSet();
    for (final device in discovered) {
      final id = '${device['id'] ?? '${device['topic']}/${device['powerCmd']}'}';
      if (!configuredIds.contains(id)) order.add(id);
    }
    return order.map((id) => byId[id]!).toList();
  }

  @visibleForTesting
  static List<Map<String, Object?>> completeSmartHomeDiscovery({
    required List<Map<String, Object?>> discovered,
    required Map<String, String> online,
    required Map<String, String> states,
  }) {
    final foundById = <String, Map<String, Object?>>{};
    for (final device in discovered) {
      final normalized = _tasmotaSmartHomeDevice(
        '${device['topic']}',
        '${device['powerCmd'] ?? 'POWER'}',
        '${device['name'] ?? device['topic']}',
        room: '${device['room'] ?? 'unknown'}',
        type: '${device['type'] ?? 'light'}',
      );
      foundById['${normalized['id']}'] = normalized;
    }
    final configuredTopics = discovered.map((device) => '${device['topic']}').toSet();
    for (final entry in online.entries) {
      final topic = entry.key;
      if (entry.value != 'Online' || configuredTopics.contains(topic)) continue;
      final relayNumbers = states.keys
          .where((key) => key.startsWith('$topic/POWER'))
          .map((key) => RegExp(r'^POWER(\d+)$').firstMatch(key.split('/').last))
          .whereType<RegExpMatch>()
          .map((match) => int.parse(match.group(1)!))
          .toList();
      if (relayNumbers.isNotEmpty) {
        final relayCount = relayNumbers.reduce((a, b) => a > b ? a : b);
        for (var relay = 1; relay <= relayCount; relay++) {
          final device = _tasmotaSmartHomeDevice(topic, 'POWER$relay', '$topic Relay $relay', type: 'switch');
          foundById['${device['id']}'] = device;
        }
      } else {
        final device = _tasmotaSmartHomeDevice(topic, 'POWER', topic, type: 'light');
        foundById['${device['id']}'] = device;
      }
    }
    return foundById.values.toList();
  }

  @visibleForTesting
  static List<Map<String, Object?>> buildSmartHomeDiscovery({
    required List<Map<String, Object?>> tasmotaDiscovered,
    required Map<String, String> online,
    required Map<String, String> states,
    required Map<String, Map<String, Object?>> haConfigs,
    required Map<String, Map<String, Object?>> z2mEntries,
    required Map<String, String> shellyRelays,
    required Map<String, String> shellyModels,
    required Map<String, String> shellyGen2States,
    required Map<String, String> genericTopics,
  }) {
    final devices = <Map<String, Object?>>[];
    devices.addAll(completeSmartHomeDiscovery(
      discovered: tasmotaDiscovered,
      online: online,
      states: states,
    ));
    final haTopics = haConfigs.keys.toList()..sort();
    for (final configTopic in haTopics) {
      final device = _haSmartHomeDevice(configTopic, haConfigs[configTopic]!);
      if (device != null) devices.add(device);
    }
    final z2mNames = z2mEntries.keys.toList()..sort();
    for (final name in z2mNames) {
      final device = _z2mSmartHomeDevice(z2mEntries[name]!);
      if (device != null) devices.add(device);
    }
    final relayKeys = shellyRelays.keys.toList()..sort();
    for (final key in relayKeys) {
      final parts = key.split('/');
      if (parts.length != 2) continue;
      final relay = int.tryParse(parts[1]);
      if (relay == null) continue;
      devices.add(_shellyGen1SmartHomeDevice(parts[0], relay, shellyModels[parts[0]] ?? ''));
    }
    final gen2Topics = shellyGen2States.keys.toList()..sort();
    for (final stateTopic in gen2Topics) {
      final match = RegExp(r'^(.+)/status/(switch|light):(\d+)$').firstMatch(stateTopic);
      if (match != null) {
        devices.add(_shellyGen2SmartHomeDevice(
          match.group(1)!,
          match.group(2)!,
          int.parse(match.group(3)!),
        ));
      }
    }
    final covered = <String>{};
    for (final device in devices) {
      for (final key in ['stateTopic', 'commandTopic', 'availabilityTopic']) {
        final value = '${device[key] ?? ''}';
        if (value.isNotEmpty) covered.add(value);
      }
    }
    final genericKeys = genericTopics.keys.toList()..sort();
    for (final topic in genericKeys) {
      if (covered.contains(topic)) continue;
      final device = _genericSmartHomeDevice(topic, genericTopics[topic]!);
      if (device != null) {
        devices.add(device);
        covered.add(topic);
      }
    }
    final seen = <String>{};
    final unique = <Map<String, Object?>>[];
    for (final device in devices) {
      final id = '${device['id']}';
      if (seen.add(id)) unique.add(device);
    }
    return unique;
  }

  // Kept for callers that build a discovery list from Tasmota configs only.
  static List<Map<String, Object?>> _buildSmartHomeDiscovery({
    required List<Map<String, Object?>> tasmotaDiscovered,
    required Map<String, String> online,
    required Map<String, String> states,
    required Map<String, Map<String, Object?>> haConfigs,
    required Map<String, Map<String, Object?>> z2mEntries,
    required Map<String, String> shellyRelays,
    required Map<String, String> shellyModels,
    required Map<String, String> shellyGen2States,
    required Map<String, String> genericTopics,
  }) =>
      buildSmartHomeDiscovery(
        tasmotaDiscovered: tasmotaDiscovered,
        online: online,
        states: states,
        haConfigs: haConfigs,
        z2mEntries: z2mEntries,
        shellyRelays: shellyRelays,
        shellyModels: shellyModels,
        shellyGen2States: shellyGen2States,
        genericTopics: genericTopics,
      );

  static List<Map<String, Object?>> _devicesFromTasmotaConfig(Map config) {
    final topic = '${config['t'] ?? ''}'.trim();
    if (topic.isEmpty) return [];
    final names = config['fn'] is List ? config['fn'] as List : [topic];
    final relays = config['rl'] is List ? config['rl'] as List : [1];
    final active = <int>[];
    for (var index = 0; index < relays.length; index++) {
      if (_optionalInt(relays[index]) case final int relay when relay > 0) {
        active.add(index);
      }
    }
    if (active.length <= 1) {
      return [
        _tasmotaSmartHomeDevice(
          topic,
          'POWER',
          names.isNotEmpty ? '${names[0]}' : topic,
          type: 'light',
        ),
      ];
    }
    return [
      for (final index in active)
        _tasmotaSmartHomeDevice(
          topic,
          'POWER${index + 1}',
          index < names.length ? '${names[index]}' : '$topic Relay ${index + 1}',
          type: 'switch',
        ),
    ];
  }

  static int _requiredInt(Map<String, dynamic> arguments, String name) {
    final value = _optionalInt(arguments[name]);
    if (value == null) throw ArgumentError('$name is required');
    return value;
  }

  static String _smartHomeAction(String action, {String? currentState}) {
    final normalized = action.trim().toUpperCase();
    if (normalized == 'TOGGLE') {
      return currentState == 'ON' ? 'OFF' : 'ON';
    }
    if (normalized != 'ON' && normalized != 'OFF') {
      throw ArgumentError('Action must be ON, OFF or TOGGLE');
    }
    return normalized;
  }

  static void _publishDevice(
    MqttServerClient client,
    Map<String, Object?> device,
    String action,
  ) {
    final message = _smartHomeControlMessage(device, action);
    _publishMqtt(client, message['topic']!, message['payload']!);
  }
  static void _publishMqtt(MqttServerClient client, String topic, String payload) {
    final builder = MqttClientPayloadBuilder()..addString(payload);
    final packet = builder.payload;
    if (packet == null) throw StateError('Could not encode MQTT payload');
    client.publishMessage(topic, MqttQos.atMostOnce, packet);
  }

  static Future<String> _readQrCode(Map<String, dynamic> arguments) async {
    final path = _requiredString(arguments, 'path');
    if (!await File(path).exists()) {
      throw ArgumentError('Image file is not accessible on this Android device: $path');
    }
    final scanner = MobileScannerController(autoStart: false);
    try {
      final capture = await scanner.analyzeImage(path);
      final values = capture?.barcodes
              .map((barcode) => barcode.rawValue?.trim() ?? '')
              .where((value) => value.isNotEmpty)
              .toList() ??
          <String>[];
      if (values.isEmpty) return 'No QR code or barcode was found in the image.';
      final prompt = (arguments['prompt'] as String? ?? '').trim();
      return '${prompt.isEmpty ? '' : '$prompt\n'}Decoded ${values.length} code(s):\n'
          '${values.map((value) => '- $value').join('\n')}';
    } finally {
      await scanner.dispose();
    }
  }

  static Future<String> _takePhoto(
    Map<String, dynamic> arguments,
    Map<String, Object?> config, {
    Uint8List? attachedImage,
    String attachedQuestion = '',
  }
  ) async {
    // Use attachedQuestion if provided (from pendingImageQuestion), otherwise fall back to arguments['question']
    final requestQuestion = attachedQuestion.trim().isNotEmpty
        ? attachedQuestion
        : (arguments['question'] as String? ?? '');
    final question = requestQuestion.trim().isEmpty
        ? 'Jelaskan isi gambar ini secara singkat dan jelas dalam bahasa Indonesia.'
        : requestQuestion.trim();
    late final Uint8List imageBytes;
    if (attachedImage != null) {
      imageBytes = attachedImage;
    } else {
      final cameras = await availableCameras();
      final wanted = config['cameraFacing'] == 'front'
          ? CameraLensDirection.front
          : CameraLensDirection.back;
      CameraDescription? selected;
      for (final camera in cameras) {
        if (camera.lensDirection == wanted) {
          selected = camera;
          break;
        }
      }
      if (selected == null) throw StateError('Requested Android camera is not available');

      final cameraController = CameraController(
        selected,
        ResolutionPreset.max,
        enableAudio: false,
      );
      try {
        await cameraController.initialize();
        final image = await cameraController.takePicture();
        imageBytes = await image.readAsBytes();
      } finally {
        await cameraController.dispose();
      }
    }
    if (imageBytes.isEmpty) throw StateError('Android camera returned an empty image');

    final localUrl = '${config['localVlUrl'] ?? ''}'.trim();
    final localKey = '${config['vlApiKey'] ?? ''}'.trim();
    if (localUrl.isNotEmpty && localKey.isNotEmpty) {
      return _analyzeWithVisionModel(localUrl, localKey, question, imageBytes);
    }
    final explainUrl = '${config['visionUrl'] ?? ''}'.trim();
    if (explainUrl.isEmpty) {
      throw StateError('Configure Local VL URL + API key or Vision Service URL first');
    }
    return _analyzeWithVisionService(
      explainUrl,
      '${config['visionToken'] ?? ''}',
      '${config['deviceId'] ?? ''}',
      '${config['clientId'] ?? ''}',
      question,
      imageBytes,
    );
  }

  static Future<String> _analyzeWithVisionModel(
    String baseUrl,
    String apiKey,
    String question,
    Uint8List imageBytes,
  ) async {
    final baseUri = Uri.parse(baseUrl);
    final endpoint = baseUri.path.endsWith('/chat/completions')
        ? baseUri
        : baseUri.replace(
            path: '${baseUri.path.replaceFirst(RegExp(r'/$'), '')}/chat/completions',
          );
    final body = {
      'model': 'glm-4v-plus',
      'stream': false,
      'messages': [
        {'role': 'system', 'content': 'You are a helpful assistant.'},
        {
          'role': 'user',
          'content': [
            {
              'type': 'image_url',
              'image_url': {
                'url': 'data:image/jpeg;base64,${base64Encode(imageBytes)}',
              },
            },
            {'type': 'text', 'text': question},
          ],
        },
      ],
    };
    final data = await _getJsonFromUri(
      endpoint,
      method: 'POST',
      body: body,
      headers: {'Authorization': 'Bearer $apiKey'},
    );
    final choices = data['choices'];
    if (choices is List && choices.isNotEmpty && choices.first is Map) {
      final message = (choices.first as Map)['message'];
      if (message is Map) {
        final content = message['content'];
        if (content is String && content.trim().isNotEmpty) return content.trim();
        if (content is List) {
          return content
              .whereType<Map>()
              .map((part) => '${part['text'] ?? ''}')
              .where((part) => part.trim().isNotEmpty)
              .join('\n');
        }
      }
    }
    throw const FormatException('Vision model returned no text');
  }

  static Future<String> _analyzeWithVisionService(
    String url,
    String token,
    String deviceId,
    String clientId,
    String question,
    Uint8List imageBytes,
  ) async {
    final boundary = 'xiaozhi-${DateTime.now().microsecondsSinceEpoch}';
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 15);
    try {
      final request = await client.openUrl('POST', Uri.parse(url));
      request.headers.set('Content-Type', 'multipart/form-data; boundary=$boundary');
      request.headers.set('Device-Id', deviceId);
      request.headers.set('Client-Id', clientId);
      request.headers.set('Accept-Language', 'id-ID');
      if (token.isNotEmpty) request.headers.set('Authorization', 'Bearer $token');

      final multipart = BytesBuilder(copy: false);
      void addTextField(String name, String value) {
        multipart.add(utf8.encode('--$boundary\r\n'));
        multipart.add(utf8.encode('Content-Disposition: form-data; name="$name"\r\n\r\n'));
        multipart.add(utf8.encode('$value\r\n'));
      }

      addTextField('question', question);
      multipart.add(utf8.encode('--$boundary\r\n'));
      multipart.add(utf8.encode(
        'Content-Disposition: form-data; name="file"; filename="camera.jpg"\r\n'
        'Content-Type: image/jpeg\r\n\r\n',
      ));
      multipart.add(imageBytes);
      multipart.add(utf8.encode('\r\n--$boundary--\r\n'));
      request.add(multipart.takeBytes());

      final response = await request.close().timeout(const Duration(seconds: 30));
      final responseText = await utf8.decoder.bind(response).join();
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw HttpException('Vision service returned HTTP ${response.statusCode}');
      }
      try {
        final decoded = jsonDecode(responseText);
        if (decoded is Map) {
          if (decoded['success'] == false) {
            throw StateError('${decoded['message'] ?? 'Vision service rejected the image'}');
          }
          for (final key in ['text', 'result', 'response', 'content']) {
            final value = decoded[key];
            if (value is String && value.trim().isNotEmpty) return value.trim();
          }
        }
      } on FormatException {
        if (responseText.trim().isNotEmpty) return responseText.trim();
      }
      throw const FormatException('Vision service returned no result text');
    } finally {
      client.close(force: true);
    }
  }

  static Future<String> _addPrayerReminders(Map<String, dynamic> arguments) async {
    final province = await _resolveProvince(_requiredString(arguments, 'province'));
    final city = await _resolveCity(province, _requiredString(arguments, 'city'));
    final mode = (arguments['mode'] as String? ?? 'daily').toLowerCase();
    if (!{'once', 'daily', 'workday'}.contains(mode)) {
      throw ArgumentError('Prayer reminder mode must be once, daily, or workday');
    }

    const prayerLabels = <String, (String, String)>{
      'subuh': ('Subuh', 'subuh'),
      'dzuhur': ('Dzuhur', 'dzuhur'),
      'ashar': ('Ashar', 'ashar'),
      'maghrib': ('Maghrib', 'maghrib'),
      'isya': ('Isya', 'isya'),
    };
    const aliases = <String, String>{
      'fajr': 'subuh',
      'dhuhr': 'dzuhur',
      'zuhr': 'dzuhur',
      'asr': 'ashar',
      'isha': 'isya',
    };
    final requested = arguments['prayers'];
    final prayerKeys = requested is List
        ? requested
            .map((value) => '${value ?? ''}'.trim().toLowerCase())
            .map((value) => aliases[value] ?? value)
            .toSet()
        : prayerLabels.keys.toSet();
    if (prayerKeys.isEmpty || prayerKeys.any((key) => !prayerLabels.containsKey(key))) {
      throw ArgumentError('prayers must contain supported prayer names');
    }

    final now = DateTime.now();
    final data = await _getSchedule(province, city, now.month, now.year);
    final schedule = data['jadwal'];
    if (schedule is! List) throw const FormatException('Prayer schedule is unavailable');
    Map? today;
    for (final entry in schedule.whereType<Map>()) {
      if (int.tryParse('${entry['tanggal']}') == now.day) {
        today = entry;
        break;
      }
    }
    if (today == null) throw StateError('Prayer schedule is unavailable for $city today');

    final created = <String>[];
    final skipped = <String>[];
    final date = '${now.year.toString().padLeft(4, '0')}-'
        '${now.month.toString().padLeft(2, '0')}-'
        '${now.day.toString().padLeft(2, '0')}';
    for (final key in prayerLabels.keys.where(prayerKeys.contains)) {
      final (label, scheduleKey) = prayerLabels[key]!;
      final time = '${today[scheduleKey] ?? ''}'.trim();
      final parsed = DateTime.tryParse('$date $time');
      if (parsed == null) {
        throw FormatException('Invalid $label time in the prayer schedule: $time');
      }
      if (mode == 'once' && !parsed.isAfter(now)) {
        skipped.add(label);
        continue;
      }
      final result = await ReminderRuntime.call('add_reminder', {
        'title': 'Sholat $label - $city',
        'message': 'Waktu sholat $label di $city, $province.',
        'mode': mode,
        if (mode == 'once') 'datetime': '$date $time',
        if (mode != 'once') 'time': time,
      });
      created.add('$label $time: $result');
    }
    if (created.isEmpty) {
      return 'No upcoming prayer times remain today for $city. ${skipped.join(', ')} already passed.';
    }
    final skippedText = skipped.isEmpty ? '' : '\nSkipped past times: ${skipped.join(', ')}.';
    return 'Fetched today prayer schedule for $city, $province and created ${created.length} native reminder(s) ($mode):\n'
        '${created.join('\n')}$skippedText';
  }

  static Future<String> _callPrayerTool(
    String name,
    Map<String, dynamic> arguments,
  ) async {
    switch (name) {
      case 'prayer_list_provinces':
        final provinces = await _getProvinces();
        return 'Provinces:\n${provinces.map((value) => '- $value').join('\n')}';
      case 'prayer_list_cities':
        final provinceName = _requiredString(arguments, 'province');
        final province = await _resolveProvince(provinceName);
        final cities = await _getCities(province);
        return 'Cities in $province:\n${cities.map((value) => '- $value').join('\n')}';
      case 'prayer_times_today':
      case 'prayer_times_monthly':
        final province = await _resolveProvince(
          _requiredString(arguments, 'province'),
        );
        final city = await _resolveCity(
          province,
          _requiredString(arguments, 'city'),
        );
        final now = DateTime.now();
        final month = name == 'prayer_times_monthly'
            ? _optionalInt(arguments['month']) ?? now.month
            : now.month;
        final year = name == 'prayer_times_monthly'
            ? _optionalInt(arguments['year']) ?? now.year
            : now.year;
        final data = await _getSchedule(province, city, month, year);
        final schedule = data['jadwal'];
        if (schedule is! List || schedule.isEmpty) {
          return 'No prayer schedule found for $city.';
        }
        if (name == 'prayer_times_today') {
          Map? item;
          for (final entry in schedule.whereType<Map>()) {
            if (int.tryParse('${entry['tanggal']}') == now.day) {
              item = entry;
              break;
            }
          }
          if (item == null) return 'No prayer schedule found for $city today.';
          return _formatPrayerDay(province, city, item);
        }
        final limit = schedule.take(40).cast<Map>();
        final lines = <String>[
          'Prayer times for $city, $province — ${data['bulan_nama']} ${data['tahun']}',
          for (final item in limit)
            '${item['tanggal']} ${item['hari']}: Fajr ${item['subuh']} | '
                'Dhuhr ${item['dzuhur']} | Asr ${item['ashar']} | '
                'Maghrib ${item['maghrib']} | Isha ${item['isya']}',
        ];
        return lines.join('\n');
      default:
        throw ArgumentError('Unknown prayer tool: $name');
    }
  }

  static String _formatPrayerDay(String province, String city, Map item) =>
      'Prayer times for $city, $province\n'
      'Date: ${item['hari']}, ${item['tanggal_lengkap']}\n\n'
      'Imsak  : ${item['imsak']}\n'
      'Fajr   : ${item['subuh']}\n'
      'Sunrise: ${item['terbit']}\n'
      'Dhuha  : ${item['dhuha']}\n'
      'Dhuhr  : ${item['dzuhur']}\n'
      'Asr    : ${item['ashar']}\n'
      'Maghrib: ${item['maghrib']}\n'
      'Isha   : ${item['isya']}';

  static String _requiredString(Map<String, dynamic> arguments, String name) {
    final value = arguments[name];
    if (value is! String || value.trim().isEmpty) {
      throw ArgumentError('$name is required');
    }
    return value.trim();
  }

  static int? _optionalInt(Object? value) {
    if (value == null) return null;
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse('$value');
  }

  static Future<Map<String, dynamic>> _getJson(
    String method,
    String path, {
    Map<String, Object?>? body,
  }) async {
    return _getJsonFromUri(
      Uri.parse('$_baseUrl$path'),
      method: method,
      body: body,
    );
  }

  static Future<Map<String, dynamic>> _getJsonFromUri(
    Uri uri, {
    String method = 'GET',
    Map<String, Object?>? body,
    Map<String, String> headers = const {},
  }) async {
    final responseText = await _getTextFromUri(
      uri,
      method: method,
      body: body,
      headers: headers,
    );
    final decoded = jsonDecode(responseText);
    if (decoded is! Map<String, dynamic>) {
      throw const FormatException('Invalid JSON API response');
    }
    return decoded;
  }

  static Future<String> _getTextFromUri(
    Uri uri, {
    String method = 'GET',
    Map<String, Object?>? body,
    Map<String, String> headers = const {},
  }) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 15);
    try {
      final request = await client.openUrl(method, uri);
      request.headers.set(HttpHeaders.userAgentHeader, 'XiaozhiAndroid/1.0');
      for (final entry in headers.entries) {
        request.headers.set(entry.key, entry.value);
      }
      if (body != null) {
        request.headers.contentType = ContentType.json;
        request.write(jsonEncode(body));
      }
      final response = await request.close().timeout(const Duration(seconds: 15));
      final responseText = await utf8.decoder.bind(response).join();
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw HttpException('HTTP ${response.statusCode} from $uri');
      }
      return responseText;
    } finally {
      client.close(force: true);
    }
  }

  static Future<List<String>> _getProvinces() async {
    final response = await _getJson('GET', '/provinsi');
    final data = response['data'];
    if (data is! List) throw const FormatException('Prayer API returned no provinces');
    return data.map((value) => '$value').toList();
  }

  static Future<String> _resolveProvince(String query) async {
    final province = _bestMatch(query, await _getProvinces());
    if (province == null) throw ArgumentError('Province not found: $query');
    return province;
  }

  static Future<List<String>> _getCities(String province) async {
    final response = await _getJson(
      'POST',
      '/kabkota',
      body: {'provinsi': province},
    );
    final data = response['data'];
    if (data is! List) throw const FormatException('Prayer API returned no cities');
    return data.map((value) => '$value').toList();
  }

  static Future<String> _resolveCity(String province, String query) async {
    final city = _bestMatch(query, await _getCities(province));
    if (city == null) throw ArgumentError('City not found in $province: $query');
    return city;
  }

  static String? _bestMatch(String query, List<String> candidates) {
    final normalizedQuery = _normalizeLocation(query);
    for (final candidate in candidates) {
      if (_normalizeLocation(candidate) == normalizedQuery) return candidate;
    }
    for (final candidate in candidates) {
      final normalizedCandidate = _normalizeLocation(candidate);
      if (normalizedCandidate.startsWith(normalizedQuery) ||
          normalizedQuery.startsWith(normalizedCandidate)) {
        return candidate;
      }
    }
    for (final candidate in candidates) {
      final normalizedCandidate = _normalizeLocation(candidate);
      if (normalizedCandidate.contains(normalizedQuery) ||
          normalizedQuery.contains(normalizedCandidate)) {
        return candidate;
      }
    }
    return null;
  }

  static String _normalizeLocation(String value) {
    var normalized = value.trim().toLowerCase();
    for (final prefix in ['kab. ', 'kabupaten ', 'kota ']) {
      if (normalized.startsWith(prefix)) {
        normalized = normalized.substring(prefix.length);
        break;
      }
    }
    return normalized.trim();
  }

  static Future<Map<String, dynamic>> _getSchedule(
    String province,
    String city,
    int month,
    int year,
  ) async {
    if (month < 1 || month > 12 || year < 1 || year > 2100) {
      throw ArgumentError('Month or year is out of range');
    }
    final response = await _getJson(
      'POST',
      '',
      body: {
        'provinsi': province,
        'kabkota': city,
        'bulan': month,
        'tahun': year,
      },
    );
    if (response['code'] != 200 || response['data'] is! Map<String, dynamic>) {
      throw FormatException('Prayer API error: ${response['message']}');
    }
    return response['data'] as Map<String, dynamic>;
  }
}