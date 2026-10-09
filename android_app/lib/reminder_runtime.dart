import 'dart:convert';

import 'package:android_intent_plus/android_intent.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Registers MCP reminders as real entries in the phone's built-in clock app.
///
/// Scheduling uses the public `android.intent.action.SET_ALARM` Intent
/// (android.provider.AlarmClock), so a reminder fires through the same alarm
/// screen, snooze buttons and alarm volume the user already knows, and uses the
/// alarm sound picked in the settings screen. The Intent contract only supports
/// a single time of day plus an optional weekly repeat, so `hourly`, `monthly`
/// and `yearly` modes are rejected instead of being approximated.
///
/// SET_ALARM cannot list or delete clock-app alarms, so this class keeps its own
/// records in SharedPreferences as the source of truth for list/edit/toggle/
/// delete, and re-sends the Intent whenever a record changes.
class ReminderRuntime {
  static const module = 'reminder';
  static const toolNames = <String>{
    'add_reminder_in',
    'add_reminder',
    'add_prayer_reminders',
    'list_reminders',
    'delete_reminder',
    'edit_reminder',
    'toggle_reminder',
  };

  static const tools = <Map<String, Object?>>[
    {
      'name': 'add_reminder_in',
      'description': 'Add a one-shot reminder after a relative duration.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'title': {'type': 'string'},
          'message': {'type': 'string', 'default': ''},
          'seconds': {'type': 'integer', 'default': 0, 'minimum': 0},
          'minutes': {'type': 'integer', 'default': 0, 'minimum': 0},
          'hours': {'type': 'integer', 'default': 0, 'minimum': 0},
        },
        'required': ['title'],
      },
    },
    {
      'name': 'add_reminder',
      'description': 'Create an alarm in the phone built-in clock app. Supports '
          'once, daily, workday (Monday to Friday) and weekly recurrence. The '
          'hourly, monthly and yearly modes are not supported by the Android '
          'alarm contract and are rejected.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'title': {'type': 'string'},
          'message': {'type': 'string', 'default': ''},
          'mode': {
            'type': 'string',
            'enum': ['once', 'daily', 'workday', 'weekly'],
            'default': 'once',
          },
          'datetime': {'type': 'string', 'default': ''},
          'time': {'type': 'string', 'default': ''},
          'weekday': {'type': 'string', 'default': ''},
        },
        'required': ['title'],
      },
    },
    {
      'name': 'add_prayer_reminders',
      'description': 'Fetch today prayer times for the specified Indonesian province and city, then create one native Android reminder per requested prayer. Use daily by default, once for today only, or workday for Monday-Friday. Default prayers: Subuh, Dzuhur, Ashar, Maghrib, and Isya. This tool performs the prayer lookup itself before scheduling.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'province': {'type': 'string'},
          'city': {'type': 'string'},
          'mode': {'type': 'string', 'enum': ['once', 'daily', 'workday'], 'default': 'daily'},
          'prayers': {
            'type': 'array',
            'items': {'type': 'string', 'enum': ['subuh', 'dzuhur', 'ashar', 'maghrib', 'isya']},
          },
        },
        'required': ['province', 'city'],
      },
    },
    {
      'name': 'list_reminders',
      'description': 'List all reminders, their schedule, and next trigger time.',
      'inputSchema': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
      'name': 'delete_reminder',
      'description': 'Delete a reminder by its ID.',
      'inputSchema': {
        'type': 'object',
        'properties': {'id': {'type': 'integer', 'minimum': 1, 'maximum': 100000}},
        'required': ['id'],
      },
    },
    {
      'name': 'edit_reminder',
      'description': 'Edit reminder fields by ID.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'id': {'type': 'integer', 'minimum': 1, 'maximum': 100000},
          'title': {'type': 'string', 'default': ''},
          'message': {'type': 'string', 'default': ''},
          'mode': {
            'type': 'string',
            'enum': ['once', 'daily', 'workday', 'weekly'],
            'default': '',
          },
          'datetime': {'type': 'string', 'default': ''},
          'time': {'type': 'string', 'default': ''},
          'weekday': {'type': 'string', 'default': ''},
          'enabled': {'type': 'boolean', 'default': true},
        },
        'required': ['id'],
      },
    },
    {
      'name': 'toggle_reminder',
      'description': 'Enable or disable a reminder by ID.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'id': {'type': 'integer', 'minimum': 1, 'maximum': 100000},
          'enabled': {'type': 'boolean', 'default': true},
        },
        'required': ['id'],
      },
    },
  ];

  static const _storageKey = 'android_mcp_reminders';
  static const _alarmSoundUriKey = 'android_reminder_sound_uri';
  static const _alarmSoundNameKey = 'android_reminder_sound_name';
  static const defaultAlarmSoundUri = 'content://settings/system/alarm_alert';
  static const _maxDurationSeconds = 86400 * 366;

  /// Day-of-week values for AlarmClock.EXTRA_DAYS (Calendar.SUNDAY..SATURDAY).
  static const _sunday = 1;
  static const _monday = 2;
  static const _tuesday = 3;
  static const _wednesday = 4;
  static const _thursday = 5;
  static const _friday = 6;
  static const _saturday = 7;

  static const _weekdayIndices = <String, int>{
    'senin': _monday,
    'selasa': _tuesday,
    'rabu': _wednesday,
    'kamis': _thursday,
    'jumat': _friday,
    'sabtu': _saturday,
    'minggu': _sunday,
    'monday': _monday,
    'tuesday': _tuesday,
    'wednesday': _wednesday,
    'thursday': _thursday,
    'friday': _friday,
    'saturday': _saturday,
    'sunday': _sunday,
  };

  /// Modes that can be expressed with the AlarmClock intent contract.
  static const _supportedModes = <String>{'once', 'daily', 'workday', 'weekly'};

  static const _actionSetAlarm = 'android.intent.action.SET_ALARM';
  static const _extraMessage = 'android.intent.extra.alarm.MESSAGE';
  static const _extraHour = 'android.intent.extra.alarm.HOUR';
  static const _extraMinutes = 'android.intent.extra.alarm.MINUTES';
  static const _extraDays = 'android.intent.extra.alarm.DAYS';
  static const _extraRingtone = 'android.intent.extra.alarm.RINGTONE';
  static const _extraVibrate = 'android.intent.extra.alarm.VIBRATE';
  static const _extraSkipUi = 'android.intent.extra.alarm.SKIP_UI';

  static Future<void>? _initialization;

  static Future<void> initialize() => _initialization ??= _initialize();

  static Future<void> _initialize() async {
    // Nothing to bootstrap: the clock app owns the alarm schedule. Re-registering
    // on startup would create duplicate alarms, because SET_ALARM re-uses an
    // existing alarm only when every extra matches, and the stored records
    // carry no clock-app alarm id to deduplicate against.
  }

  static Future<String> call(String name, Map<String, dynamic> arguments) async {
    await initialize();
    switch (name) {
      case 'add_reminder_in':
        return _addRelative(arguments);
      case 'add_reminder':
        return _add(arguments);
      case 'list_reminders':
        return _list();
      case 'delete_reminder':
        return _delete(_requiredInt(arguments, 'id'));
      case 'edit_reminder':
        return _edit(arguments);
      case 'toggle_reminder':
        return _toggle(arguments);
      default:
        throw ArgumentError('Unknown reminder tool: $name');
    }
  }

  static Future<(String, String)> getAlarmSound() async {
    final preferences = await SharedPreferences.getInstance();
    return (
      preferences.getString(_alarmSoundUriKey) ?? defaultAlarmSoundUri,
      preferences.getString(_alarmSoundNameKey) ?? 'Suara bawaan perangkat',
    );
  }

  static Future<void> setAlarmSound({required String uri, required String name}) async {
    await initialize();
    final preferences = await SharedPreferences.getInstance();
    await preferences.setString(
      _alarmSoundUriKey,
      uri.isEmpty ? defaultAlarmSoundUri : uri,
    );
    await preferences.setString(_alarmSoundNameKey, name);
    // The clock app reads the ringtone from the intent, so already-registered
    // alarms keep the previous sound until they are re-scheduled.
    final reminders = await _load();
    for (final reminder in reminders.where((item) => item['enabled'] == true)) {
      await _schedule(reminder);
    }
  }

  static Future<String> _addRelative(Map<String, dynamic> arguments) async {
    final title = _requiredString(arguments, 'title');
    final seconds = _int(arguments['seconds']) +
        _int(arguments['minutes']) * 60 +
        _int(arguments['hours']) * 3600;
    if (seconds <= 0 || seconds > _maxDurationSeconds) {
      throw ArgumentError('Reminder duration must be between 1 second and 366 days');
    }
    final due = DateTime.now().add(Duration(seconds: seconds));
    return _create({
      'title': title,
      'message': _optionalString(arguments['message']).isEmpty
          ? title
          : _optionalString(arguments['message']),
      'mode': 'once',
      'datetime': due.toIso8601String(),
      'enabled': true,
    });
  }

  static Future<String> _add(Map<String, dynamic> arguments) async {
    final title = _requiredString(arguments, 'title');
    final mode = _optionalString(arguments['mode']).isEmpty
        ? 'once'
        : _optionalString(arguments['mode']).toLowerCase();
    final reminder = <String, Object?>{
      'title': title,
      'message': _optionalString(arguments['message']).isEmpty
          ? title
          : _optionalString(arguments['message']),
      'mode': mode,
      'datetime': _optionalString(arguments['datetime']),
      'time': _optionalString(arguments['time']),
      'weekday': _optionalString(arguments['weekday']),
      'enabled': true,
    };
    _validate(reminder);
    return _create(reminder);
  }

  static Future<String> _create(Map<String, Object?> data) async {
    final reminders = await _load();
    final id = reminders.isEmpty
        ? 1
        : reminders.map((item) => _int(item['id'])).reduce((a, b) => a > b ? a : b) + 1;
    final reminder = <String, Object?>{
      ...data,
      'id': id,
      'enabled': true,
    };

    try {
      await _schedule(reminder);
      return 'Reminder added: [$id] ${reminder['title']} — ${_formatSchedule(reminder)}';
    } on Object catch (error) {
      reminder['enabled'] = false;
      await _save(await _loadAndReplace(reminder));
      return 'Reminder saved locally but the system clock app could not be reached. '
          'Open the clock app and add the alarm manually. Details: $error';
    }
  }

  static Future<String> _list() async {
    final reminders = await _load();
    if (reminders.isEmpty) return 'No reminders.';
    final lines = <String>[];
    for (final reminder in reminders) {
      final next = _nextTrigger(reminder);
      lines.add(
        '[${reminder['id']}] ${reminder['enabled'] == true ? 'ON' : 'OFF'} '
        '${reminder['title']}\n    Schedule: ${_formatSchedule(reminder)}\n'
        '    Next: ${next?.toLocal().toString() ?? '-'}',
      );
    }
    return lines.join('\n');
  }

  static Future<String> _delete(int id) async {
    final reminders = await _load();
    final index = reminders.indexWhere((item) => _int(item['id']) == id);
    if (index < 0) return 'Reminder not found: $id';
    // SET_ALARM cannot delete an alarm, so removing the local record is all
    // this class can do. The user is told to open the clock app.
    reminders.removeAt(index);
    await _save(reminders);
    return 'Reminder deleted: $id. The matching alarm stays in the clock app; '
        'remove it there if it should not ring again.';
  }

  static Future<String> _toggle(Map<String, dynamic> arguments) async {
    final id = _requiredInt(arguments, 'id');
    final enabled = arguments['enabled'] is bool ? arguments['enabled'] as bool : true;
    final reminders = await _load();
    final reminder = _findReminder(reminders, id);
    if (reminder == null) return 'Reminder not found: $id';
    final wasEnabled = reminder['enabled'] == true;
    reminder['enabled'] = enabled;
    if (enabled) {
      try {
        await _schedule(reminder);
      } catch (_) {
        reminder['enabled'] = wasEnabled;
        await _save(reminders);
        rethrow;
      }
    } else {
      await _save(reminders);
    }
    if (!enabled) {
      return 'Reminder [$id] disabled. The matching alarm stays in the clock '
          'app; turn it off there if it should not ring.';
    }
    return 'Reminder [$id] enabled.';
  }

  static Future<String> _edit(Map<String, dynamic> arguments) async {
    final id = _requiredInt(arguments, 'id');
    final reminders = await _load();
    final reminder = _findReminder(reminders, id);
    if (reminder == null) throw StateError('Reminder not found: $id');
    final previous = Map<String, dynamic>.from(reminder);
    for (final key in [
      'title',
      'message',
      'mode',
      'datetime',
      'time',
      'weekday',
      'enabled',
    ]) {
      if (arguments.containsKey(key) && arguments[key] != null) {
        reminder[key] = arguments[key];
      }
    }
    _validate(reminder);
    if (reminder['enabled'] == true) {
      try {
        await _schedule(reminder);
      } catch (_) {
        reminders[reminders.indexOf(reminder)] = previous;
        await _save(reminders);
        rethrow;
      }
    } else {
      await _save(reminders);
    }
    return 'Reminder updated: [$id] ${reminder['title']} — ${_formatSchedule(reminder)}';
  }

  static Future<List<Map<String, dynamic>>> _load() async {
    final preferences = await SharedPreferences.getInstance();
    final raw = preferences.getString(_storageKey) ?? '[]';
    final decoded = jsonDecode(raw);
    if (decoded is! List) return [];
    return decoded.whereType<Map>().map((item) => Map<String, dynamic>.from(item)).toList();
  }

  static Future<void> _save(List<Map<String, dynamic>> reminders) async {
    final preferences = await SharedPreferences.getInstance();
    await preferences.setString(_storageKey, jsonEncode(reminders));
  }

  /// Optional test seam for the platform launch. Production code leaves this
  /// null and [AndroidIntent] sends the real SET_ALARM Intent. Unit tests set
  /// it to inspect the AlarmClock payload, or to throw and exercise the
  /// "saved locally" fallback for a device whose clock app is unavailable.
  static Future<void> Function(Map<String, dynamic> extras)? launchAlarm;

  /// Registers the reminder as an alarm in the phone's clock app.
  static Future<void> _schedule(Map<String, dynamic> reminder) async {
    if (reminder['enabled'] != true) return;
    final trigger = _nextTrigger(reminder);
    if (trigger == null) throw ArgumentError('Reminder has no future trigger');
    final title = '${reminder['title'] ?? 'Reminder'}';
    final message = '${reminder['message'] ?? title}';
    final (soundUri, _) = await getAlarmSound();
    reminder['alarmSoundUri'] = soundUri;

    final arguments = <String, dynamic>{
      _extraMessage: message,
      _extraHour: trigger.hour,
      _extraMinutes: trigger.minute,
      _extraSkipUi: true,
      _extraVibrate: true,
      _extraRingtone: soundUri.isEmpty ? defaultAlarmSoundUri : soundUri,
    };
    final days = _alarmDays(reminder);
    if (days != null) arguments[_extraDays] = days;

    final launcher = launchAlarm;
    if (launcher != null) {
      await launcher(arguments);
    } else {
      await AndroidIntent(
        action: _actionSetAlarm,
        arguments: arguments,
      ).launch();
    }
    await _save(await _loadAndReplace(reminder));
  }

  /// Weekdays for AlarmClock.EXTRA_DAYS, or null for a one-shot alarm.
  static List<int>? _alarmDays(Map<String, dynamic> reminder) {
    switch ('${reminder['mode']}') {
      case 'daily':
        return const [_sunday, _monday, _tuesday, _wednesday, _thursday, _friday, _saturday];
      case 'workday':
        return const [_monday, _tuesday, _wednesday, _thursday, _friday];
      case 'weekly':
        final weekday = _weekdayIndices['${reminder['weekday']}'.toLowerCase()];
        if (weekday == null) throw ArgumentError('Invalid weekday');
        return [weekday];
      default:
        return null;
    }
  }

  static Future<List<Map<String, dynamic>>> _loadAndReplace(
    Map<String, dynamic> reminder,
  ) async {
    final reminders = await _load();
    final index = reminders.indexWhere((item) => _int(item['id']) == _int(reminder['id']));
    if (index < 0) {
      reminders.add(reminder);
    } else {
      reminders[index] = reminder;
    }
    return reminders;
  }

  static Map<String, dynamic>? _findReminder(
    List<Map<String, dynamic>> reminders,
    int id,
  ) {
    for (final reminder in reminders) {
      if (_int(reminder['id']) == id) return reminder;
    }
    return null;
  }

  static void _validate(Map<String, dynamic> reminder) {
    final mode = '${reminder['mode'] ?? 'once'}';
    if (!_supportedModes.contains(mode)) {
      // The AlarmClock intent can only express a single time of day, optionally
      // repeated on selected weekdays. Anything else cannot be scheduled as a
      // clock-app alarm, so it is rejected rather than approximated.
      throw ArgumentError(
          'Unsupported reminder mode: $mode. The phone clock app only supports '
          'once, daily, workday and weekly reminders.');
    }
    if ('${reminder['title'] ?? ''}'.trim().isEmpty) {
      throw ArgumentError('Reminder title is required');
    }
    if (mode == 'once') {
      final date = _parseDateTime('${reminder['datetime'] ?? ''}');
      if (!date.isAfter(DateTime.now())) throw ArgumentError('Reminder time must be in the future');
    }
    if (mode == 'daily' || mode == 'workday') _parseTime('${reminder['time'] ?? ''}');
    if (mode == 'weekly') {
      _parseTime('${reminder['time'] ?? ''}');
      if (!_weekdayIndices.containsKey('${reminder['weekday']}'.toLowerCase())) {
        throw ArgumentError('Invalid weekday');
      }
    }
  }

  static DateTime? _nextTrigger(Map<String, dynamic> reminder) {
    final now = DateTime.now();
    final mode = '${reminder['mode'] ?? 'once'}';
    if (mode == 'once') {
      final parsed = DateTime.tryParse('${reminder['datetime'] ?? ''}'.replaceFirst(' ', 'T'));
      return parsed != null && parsed.isAfter(now) ? parsed : null;
    }
    final (hour, minute) = _parseTime('${reminder['time'] ?? '00:00'}');
    if (mode == 'daily') return _nextWeekdayTime(null, hour, minute);
    if (mode == 'workday') {
      for (var offset = 0; offset <= 7; offset++) {
        final day = now.add(Duration(days: offset));
        if (day.weekday > DateTime.friday) continue;
        final candidate = DateTime(day.year, day.month, day.day, hour, minute);
        if (candidate.isAfter(now)) return candidate;
      }
      return null;
    }
    if (mode == 'weekly') {
      final weekday = _weekdayIndices['${reminder['weekday']}'.toLowerCase()];
      return _nextWeekdayTime(weekday, hour, minute);
    }
    return null;
  }

  static DateTime _nextWeekdayTime(int? calendarWeekday, int hour, int minute) {
    final now = DateTime.now();
    for (var offset = 0; offset <= 7; offset++) {
      final day = now.add(Duration(days: offset));
      if (calendarWeekday != null && _calendarDayOf(day) != calendarWeekday) continue;
      final candidate = DateTime(day.year, day.month, day.day, hour, minute);
      if (candidate.isAfter(now)) return candidate;
    }
    return now.add(const Duration(days: 7));
  }

  /// DateTime.weekday is 1=Monday..7=Sunday; AlarmClock.EXTRA_DAYS uses
  /// Calendar.SUNDAY=1..Calendar.SATURDAY=7.
  static int _calendarDayOf(DateTime date) => (date.weekday % 7) + 1;

  static (int, int) _parseTime(String value) {
    final match = RegExp(r'^(\d{1,2}):(\d{2})$').firstMatch(value.trim());
    if (match == null) throw ArgumentError('Invalid time format; expected HH:MM');
    final hour = int.parse(match.group(1)!);
    final minute = int.parse(match.group(2)!);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
      throw ArgumentError('Invalid time of day');
    }
    return (hour, minute);
  }

  static DateTime _parseDateTime(String value) {
    final parsed = DateTime.tryParse(value.trim().replaceFirst(' ', 'T'));
    if (parsed == null) throw ArgumentError('Invalid datetime; expected YYYY-MM-DD HH:MM');
    return parsed;
  }

  static String _formatSchedule(Map<String, dynamic> reminder) {
    final mode = '${reminder['mode']}';
    switch (mode) {
      case 'once':
        return 'Once at ${reminder['datetime']}';
      case 'daily':
        return 'Daily at ${reminder['time']}';
      case 'workday':
        return 'Workdays at ${reminder['time']}';
      case 'weekly':
        return 'Every ${reminder['weekday']} at ${reminder['time']}';
      default:
        return mode;
    }
  }

  static String _requiredString(Map<String, dynamic> arguments, String key) {
    final value = arguments[key];
    if (value is! String || value.trim().isEmpty) throw ArgumentError('$key is required');
    return value.trim();
  }

  static int _requiredInt(Map<String, dynamic> arguments, String key) {
    final value = _optionalInt(arguments[key]);
    if (value == null) throw ArgumentError('$key is required');
    return value;
  }

  static int _int(Object? value, {int fallback = 0}) => _optionalInt(value) ?? fallback;

  static int? _optionalInt(Object? value) {
    if (value is int) return value;
    if (value is num) return value.toInt();
    return int.tryParse('$value');
  }

  static String _optionalString(Object? value) => value is String ? value.trim() : '';
}
