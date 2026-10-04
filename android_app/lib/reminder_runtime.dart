import 'dart:convert';

import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:timezone/data/latest.dart' as timezone_data;
import 'package:timezone/timezone.dart' as timezone;

class ReminderRuntime {
  static const module = 'reminder';
  static const toolNames = <String>{
    'add_reminder_in',
    'add_reminder',
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
      'description': 'Add a once, daily, hourly, workday, weekly, monthly, or yearly reminder.',
      'inputSchema': {
        'type': 'object',
        'properties': {
          'title': {'type': 'string'},
          'message': {'type': 'string', 'default': ''},
          'mode': {'type': 'string', 'default': 'once'},
          'datetime': {'type': 'string', 'default': ''},
          'time': {'type': 'string', 'default': ''},
          'interval_hours': {'type': 'integer', 'default': 1, 'minimum': 1, 'maximum': 720},
          'minute': {'type': 'integer', 'default': 0, 'minimum': 0, 'maximum': 59},
          'weekday': {'type': 'string', 'default': ''},
          'day': {'type': 'integer', 'default': 1, 'minimum': 1, 'maximum': 31},
          'month': {'type': 'integer', 'default': 1, 'minimum': 1, 'maximum': 12},
        },
        'required': ['title'],
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
          'mode': {'type': 'string', 'default': ''},
          'datetime': {'type': 'string', 'default': ''},
          'time': {'type': 'string', 'default': ''},
          'interval_hours': {'type': 'integer', 'default': 1, 'minimum': 1, 'maximum': 720},
          'minute': {'type': 'integer', 'default': 0, 'minimum': 0, 'maximum': 59},
          'weekday': {'type': 'string', 'default': ''},
          'day': {'type': 'integer', 'default': 1, 'minimum': 1, 'maximum': 31},
          'month': {'type': 'integer', 'default': 1, 'minimum': 1, 'maximum': 12},
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
  static const _maxDurationSeconds = 86400 * 366;
  static const _weekdayIndices = <String, int>{
    'senin': 1,
    'selasa': 2,
    'rabu': 3,
    'kamis': 4,
    'jumat': 5,
    'sabtu': 6,
    'minggu': 7,
    'monday': 1,
    'tuesday': 2,
    'wednesday': 3,
    'thursday': 4,
    'friday': 5,
    'saturday': 6,
    'sunday': 7,
  };

  static final _notifications = FlutterLocalNotificationsPlugin();
  static Future<void>? _initialization;

  static Future<void> initialize() => _initialization ??= _initialize();

  static Future<void> _initialize() async {
    timezone_data.initializeTimeZones();
    try {
      final localTimezone = await FlutterTimezone.getLocalTimezone();
      timezone.setLocalLocation(timezone.getLocation(localTimezone.identifier));
    } catch (_) {
      timezone.setLocalLocation(timezone.getLocation('UTC'));
    }
    await _notifications.initialize(
      const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      ),
    );
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
      'interval_hours': _int(arguments['interval_hours'], fallback: 1),
      'minute': _int(arguments['minute']),
      'weekday': _optionalString(arguments['weekday']),
      'day': _int(arguments['day'], fallback: 1),
      'month': _int(arguments['month'], fallback: 1),
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
      'notificationIds': <int>[],
    };
    reminders.add(reminder);
    await _save(reminders);
    await _schedule(reminder);
    return 'Reminder added: [$id] ${reminder['title']} — ${_formatSchedule(reminder)}';
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
    await _cancel(reminders[index]);
    reminders.removeAt(index);
    await _save(reminders);
    return 'Reminder deleted: $id';
  }

  static Future<String> _toggle(Map<String, dynamic> arguments) async {
    final id = _requiredInt(arguments, 'id');
    final enabled = arguments['enabled'] is bool ? arguments['enabled'] as bool : true;
    final reminders = await _load();
    final reminder = _findReminder(reminders, id);
    if (reminder == null) return 'Reminder not found: $id';
    reminder['enabled'] = enabled;
    await _save(reminders);
    if (enabled) {
      await _schedule(reminder);
    } else {
      await _cancel(reminder);
    }
    return 'Reminder [$id] ${enabled ? 'enabled' : 'disabled'}.';
  }

  static Future<String> _edit(Map<String, dynamic> arguments) async {
    final id = _requiredInt(arguments, 'id');
    final reminders = await _load();
    final reminder = _findReminder(reminders, id);
    if (reminder == null) return 'Reminder not found: $id';
    for (final key in [
      'title',
      'message',
      'mode',
      'datetime',
      'time',
      'interval_hours',
      'minute',
      'weekday',
      'day',
      'month',
      'enabled',
    ]) {
      if (arguments.containsKey(key) && arguments[key] != null) {
        reminder[key] = arguments[key];
      }
    }
    _validate(reminder);
    await _cancel(reminder);
    await _save(reminders);
    await _schedule(reminder);
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

  static Future<void> _schedule(Map<String, dynamic> reminder) async {
    await _cancel(reminder);
    if (reminder['enabled'] != true) return;
    final scheduleMode = await _requestScheduleMode();
    final next = _nextTrigger(reminder);
    if (next == null) throw ArgumentError('Reminder has no future trigger');
    final id = _int(reminder['id']);
    final title = '${reminder['title'] ?? 'Reminder'}';
    final message = '${reminder['message'] ?? title}';
    const details = NotificationDetails(
      android: AndroidNotificationDetails(
        'xiaozhi_reminders',
        'Reminders',
        channelDescription: 'Scheduled reminders from Xiaozhi',
        importance: Importance.max,
        priority: Priority.high,
      ),
    );
    final mode = '${reminder['mode']}';
    final notificationIds = <int>[];

    if (mode == 'hourly') {
      final interval = _int(reminder['interval_hours'], fallback: 1).clamp(1, 720).toInt();
      final scheduleId = id * 10;
      await _notifications.periodicallyShowWithDuration(
        scheduleId,
        title,
        message,
        Duration(hours: interval),
        details,
        androidScheduleMode: scheduleMode,
      );
      notificationIds.add(scheduleId);
    } else if (mode == 'workday') {
      final (hour, minute) = _parseTime('${reminder['time']}');
      for (var weekday = DateTime.monday; weekday <= DateTime.friday; weekday++) {
        final scheduleId = id * 10 + weekday;
        final occurrence = _nextWeekdayTime(weekday, hour, minute);
        await _zonedSchedule(
          scheduleId,
          title,
          message,
          occurrence,
          details,
          DateTimeComponents.dayOfWeekAndTime,
          scheduleMode,
        );
        notificationIds.add(scheduleId);
      }
    } else {
      final components = switch (mode) {
        'daily' => DateTimeComponents.time,
        'weekly' => DateTimeComponents.dayOfWeekAndTime,
        'monthly' => DateTimeComponents.dayOfMonthAndTime,
        'yearly' => DateTimeComponents.dateAndTime,
        _ => null,
      };
      final scheduleId = id * 10;
      await _zonedSchedule(scheduleId, title, message, next, details, components, scheduleMode);
      notificationIds.add(scheduleId);
    }
    reminder['notificationIds'] = notificationIds;
    await _save(await _loadAndReplace(reminder));
  }

  static Future<void> _zonedSchedule(
    int id,
    String title,
    String message,
    DateTime when,
    NotificationDetails details,
    DateTimeComponents? match,
    AndroidScheduleMode scheduleMode,
  ) async {
    await _notifications.zonedSchedule(
      id,
      title,
      message,
      timezone.TZDateTime.from(when, timezone.local),
      details,
      uiLocalNotificationDateInterpretation:
          UILocalNotificationDateInterpretation.absoluteTime,
      androidScheduleMode: scheduleMode,
      matchDateTimeComponents: match,
    );
  }

  static Future<AndroidScheduleMode> _requestScheduleMode() async {
    final android = _notifications.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android == null) return AndroidScheduleMode.inexactAllowWhileIdle;
    final notificationsGranted = await android.requestNotificationsPermission();
    if (notificationsGranted == false) {
      throw StateError('Notification permission was not granted');
    }
    final exactAlarmGranted = await android.requestExactAlarmsPermission();
    return exactAlarmGranted == false
        ? AndroidScheduleMode.inexactAllowWhileIdle
        : AndroidScheduleMode.exactAllowWhileIdle;
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

  static Future<void> _cancel(Map<String, dynamic> reminder) async {
    final ids = reminder['notificationIds'];
    if (ids is List) {
      for (final id in ids) {
        final parsed = _optionalInt(id);
        if (parsed != null) await _notifications.cancel(parsed);
      }
    }
    final id = _optionalInt(reminder['id']);
    if (id != null) {
      await _notifications.cancel(id);
      await _notifications.cancel(id * 10);
      for (var weekday = DateTime.monday; weekday <= DateTime.friday; weekday++) {
        await _notifications.cancel(id * 10 + weekday);
      }
    }
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
    const modes = {'once', 'daily', 'hourly', 'workday', 'weekly', 'monthly', 'yearly'};
    final mode = '${reminder['mode'] ?? 'once'}';
    if (!modes.contains(mode)) throw ArgumentError('Unsupported reminder mode: $mode');
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
    if (mode == 'monthly' || mode == 'yearly') {
      _parseTime('${reminder['time'] ?? ''}');
      final day = _int(reminder['day'], fallback: 1);
      if (day < 1 || day > 31) throw ArgumentError('Day must be between 1 and 31');
    }
    if (mode == 'yearly') {
      final month = _int(reminder['month'], fallback: 1);
      if (month < 1 || month > 12) throw ArgumentError('Month must be between 1 and 12');
    }
    if (mode == 'hourly') {
      final interval = _int(reminder['interval_hours'], fallback: 1);
      final minute = _int(reminder['minute']);
      if (interval < 1 || interval > 720 || minute < 0 || minute > 59) {
        throw ArgumentError('Invalid hourly interval or minute');
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
    if (mode == 'hourly') {
      final interval = _int(reminder['interval_hours'], fallback: 1).clamp(1, 720).toInt();
      final minute = _int(reminder['minute']).clamp(0, 59).toInt();
      var candidate = DateTime(now.year, now.month, now.day, now.hour, minute);
      while (!candidate.isAfter(now)) {
        candidate = candidate.add(Duration(hours: interval));
      }
      return candidate;
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
    if (mode == 'monthly') {
      final day = _int(reminder['day'], fallback: 1).clamp(1, 28).toInt();
      var candidate = DateTime(now.year, now.month, day, hour, minute);
      if (!candidate.isAfter(now)) {
        final monthDate = DateTime(now.year, now.month + 1, 1);
        candidate = DateTime(monthDate.year, monthDate.month, day, hour, minute);
      }
      return candidate;
    }
    if (mode == 'yearly') {
      final day = _int(reminder['day'], fallback: 1).clamp(1, 28).toInt();
      final month = _int(reminder['month'], fallback: 1).clamp(1, 12).toInt();
      var candidate = DateTime(now.year, month, day, hour, minute);
      if (!candidate.isAfter(now)) candidate = DateTime(now.year + 1, month, day, hour, minute);
      return candidate;
    }
    return null;
  }

  static DateTime _nextWeekdayTime(int? weekday, int hour, int minute) {
    final now = DateTime.now();
    for (var offset = 0; offset <= 7; offset++) {
      final day = now.add(Duration(days: offset));
      if (weekday != null && day.weekday != weekday) continue;
      final candidate = DateTime(day.year, day.month, day.day, hour, minute);
      if (candidate.isAfter(now)) return candidate;
    }
    return now.add(const Duration(days: 7));
  }

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
      case 'hourly':
        return "Every ${reminder['interval_hours']}h at :${_int(reminder['minute']).toString().padLeft(2, '0')}";
      case 'workday':
        return 'Workdays at ${reminder['time']}';
      case 'weekly':
        return 'Every ${reminder['weekday']} at ${reminder['time']}';
      case 'monthly':
        return 'Monthly on day ${reminder['day']} at ${reminder['time']}';
      case 'yearly':
        return 'Yearly on ${reminder['day']}/${reminder['month']} at ${reminder['time']}';
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