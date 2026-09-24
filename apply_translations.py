# -*- coding: utf-8 -*-
"""Apply English translations to all translatable Chinese text in src/.

Approach: single-pass longest-first raw text replacement on all .py files,
plus build.json and py-xiaozhi.spec.

Functional Chinese (regex patterns, API markers, config defaults, etc.)
is preserved via an explicit SKIP list of exact substrings that must not
be touched.
"""
import io
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "src"

# ---------------------------------------------------------------------------
# 1. TRANSLATION DICTIONARY (longest keys first to avoid substring collisions)
# ---------------------------------------------------------------------------
TRANSLATIONS = {
    # === core/event_bus ===
    "设备状态/协议相关/网络错误/音频通道/应用生命周期/音乐播放器事件/音乐控制命令/UI 操作/配置变更事件":
    "device state/protocol related/network errors/audio channels/app lifecycle/music player events/music control commands/UI operations/config change events",

    # === protocols/websocket_protocol ===
    "连接成功": "Connected",
    "等待响应超时": "Response timed out",
    "无法连接服务": "Cannot connect to server",
    "服务端关闭连接": "Server closed connection",
    "连接错误": "Connection error",
    "连接状态异常": "Connection state abnormal",
    "连接被重置": "Connection reset",
    "网络I/O错误": "Network I/O error",
    "消息处理异常": "Message processing exception",
    "发送音频时服务端关闭": "Server closed while sending audio",
    "发送音频失败": "Send audio failed",
    "发送音频异常": "Send audio exception",
    "WebSocket 已关闭": "WebSocket closed",
    "发送文本失败": "Send text failed",
    "发送文本时服务端关闭": "Server closed while sending text",
    "发送文本错误": "Send text error",
    "发送文本异常": "Send text exception",
    "处理服务器响应失败": "Failed to process server response",
    "连接已关闭": "Connection closed",

    # === protocols/mqtt_protocol ===
    "endpoint不能为空": "endpoint must not be empty",
    "端口号必须在1-65535之间": "port must be between 1 and 65535",
    "无效的端口号": "Invalid port number",

    # === protocols/protocol.py ===
    "send_text方法必须由子类实现": "send_text method must be implemented by subclass",

    # === activation/identity.py ===
    "efuse 根节点须为 object,实际为": "efuse root must be object, got",

    # === activation/ota.py ===
    "OTA URL 或 DEVICE_ID 未配置": "OTA URL or DEVICE_ID not configured",
    "OTA服务器返回错误": "OTA server returned error",

    # === activation/service.py ===
    "v1协议初始化完成": "v1 protocol initialization complete",
    "初始化失败": "Initialization failed",

    # === bootstrap/health.py ===
    "降级:音频不可用(无麦/扬声器)。设置仍可用,修复设备后请重启。":
    "Degraded: audio unavailable (no mic/speaker). Settings still work; please restart after fixing the device.",
    "无音频调试可设 XIAOZHI_DISABLE_AUDIO=1;或设 XIAOZHI_DEGRADED_AUDIO=1 以无麦模式继续(UI/设置可用)。":
    "For no-audio debugging set XIAOZHI_DISABLE_AUDIO=1; or set XIAOZHI_DEGRADED_AUDIO=1 to continue in no-mic mode (UI/settings still work).",
    "关键插件启动失败": "Critical plugin startup failed",
    "应用将退出以避免空转(zombie)。": "Application will exit to avoid idle spin(zombie).",

    # === utils/config_manager.py ===
    "ConfigManager 未初始化:请在入口调用 initialize_config()":
    "ConfigManager not initialized: call initialize_config() at entry point",
    "根节点须为 object,实际为": "Root node must be object, got",
    "配置路径前缀不是对象,无法写入": "Config path prefix is not an object, cannot write",
    "配置路径无法写入": "Config path cannot be written",
    "按住说话": "Hold to Talk",
    "自动对话": "Auto Conversation",
    "中断对话": "Interrupt Conversation",
    "切换模式": "Switch Mode",
    "显示/隐藏窗口": "Show/Hide Window",

    # === utils/audio_device.py ===
    "无法找到可用的输入设备": "Cannot find available input device",
    "无法找到可用的输出设备": "Cannot find available output device",

    # === constants/system.py ===
    "第一阶段:设备身份准备": "Stage 1: Device identity preparation",
    "第二阶段:配置管理初始化": "Stage 2: Configuration management initialization",
    "第三阶段:OTA获取配置": "Stage 3: OTA configuration retrieval",
    "第四阶段:激活流程": "Stage 4: Activation flow",
    "系统常量定义": "System constants definition",
    "应用信息": "Application info",
    "程序标识名(ASCII,用于目录、配置、bundle_id)": "Program identifier name (ASCII, used for directories, config, bundle_id)",
    "显示名称(用于窗口标题、Launchpad、安装器 UI)": "Display name (used for window title, Launchpad, installer UI)",
    "默认超时设置": "Default timeout settings",
    "文件名常量": "Filename constants",
    "小智": "Xiaozhi",

    # === mcp/tooling.py ===
    "属性类型枚举": "Property type enum",
    "MCP工具属性定义": "MCP tool property definition",
    "属性列表": "Property list",
    "验证并返回值": "Validate and return value",
    "转换为JSON格式": "Convert to JSON format",
    "获取必需的属性名称列表": "Get list of required property names",
    "解析并验证参数": "Parse and validate parameters",
    "返回值类型": "Return value type",

    # === mcp/mcp_server.py ===
    "MCP服务器实现": "MCP server implementation",
    "设置发送消息的回调函数": "Set callback function for sending messages",
    "注入摄像头": "Inject camera",
    "容器关闭时解绑运行时依赖": "Unbind runtime dependencies when container closes",
    "按名称移除工具": "Remove tool by name",
    "卸载外挂插件已注册的工具": "Unregister tools registered by external plugin",
    "仅重载外挂": "Reload external plugin only",
    "添加通用工具": "Add common tools",
    "从参数创建McpTool": "Create McpTool from parameters",
    "检查是否已存在": "Check if already exists",
    "卸掉当前登记的全部外挂工具": "Remove all currently registered external tools",

    # === mcp/tool_catalog.py ===
    "MCP 工具目录:分组与展示名": "MCP Tool Catalog: groups and display names",
    "正则:扫描注册源码": "Regex: scan registration source code",
    "不写死中文,组标题=目录名": "Do not hardcode Chinese, group title = directory name",
    "self.application": "self.application",
    "tuple of frozendict-like...": "tuple of frozendict-like...",

    # === mcp/plugins/host.py ===
    "props 项须为 Property 或 dict,得到": "props item must be Property or dict, got",
    "外挂 MCP 宿主 API(稳定契约)": "External MCP host API (stable contract)",
    "绑定到一次装配过程的宿主门面": "Host facade bound to one assembly process",
    "返回绑定到指定 plugin_id 的视图": "Return view bound to specified plugin_id",
    "注册工具;记录名称供卸载/诊断": "Register tools; record names for uninstall/diagnosis",
    "按白名单返回宿主能力": "Return host capabilities by allowlist",
    "装饰器:将函数注册为 McpTool": "Decorator: register function as McpTool",
    "支持简化 dict": "Support simplified dict",
    "默认允许的 host.get 名称": "Default allowed host.get names",

    # === mcp/plugins/subprocess_runtime.py ===
    "缺少 worker 脚本": "Missing worker script",
    "bootstrap 返回 tools 非列表": "bootstrap returned tools not a list",
    "外挂插件 python-subprocess runtime": "External plugin python-subprocess runtime",
    "管理一个插件子进程的生命周期与 RPC": "Manage a plugin subprocess lifecycle and RPC",
    "启动进程并 bootstrap,返回 tools schema 列表": "Start process and bootstrap, return tools schema list",
    "capabilities 须可 JSON 序列化": "capabilities must be JSON serializable",
    "单次 call 默认超时(秒)": "Default timeout per call (seconds)",
    "避免子进程继承 GUI/Qt 相关干扰(可选)": "Avoid subprocess inheriting GUI/Qt related interference (optional)",
    "后台读 stderr,避免管道堵死": "Read stderr in background to avoid pipe blocking",
    "ConfigManager → 只读 dict 快照": "ConfigManager → read-only dict snapshot",

    # === plugins/mcp.py ===
    "McpPlugin 需要容器注入的 McpServer": "McpPlugin requires McpServer injected by container",
    "McpPlugin 需要容器注入的 MusicPlayer": "McpPlugin requires MusicPlayer injected by container",
    "MCP 插件. 管理 MCP 工具和消息处理": "MCP plugin. Manages MCP tools and message handling",
    "工具注册,需要较早初始化": "Tool registration, needs to be initialized early",
    "摄像头:懒创建一次,挂到 server,供 vision 配置与 take_photo 共用":
    "Camera: lazily created once, mounted on server, shared by vision config and take_photo",

    # === plugins/shortcuts ===
    "快捷键插件模块.": "Shortcuts plugin module.",
    "提供跨平台的全局快捷键支持:": "Provides cross-platform global shortcut support:",
    "macOS: 使用 Quartz Event Tap (PyObjC)": "macOS: Uses Quartz Event Tap (PyObjC)",
    "Linux/Windows: 使用 pynput": "Linux/Windows: Uses pynput",
    "创建适合当前平台的快捷键后端.": "Create shortcut backend suitable for current platform.",
    "快捷键后端实例,如果无法创建则返回 None": "Shortcut backend instance, returns None if cannot create",
    "macOS 快捷键后端.": "macOS shortcut backend.",
    "使用 Carbon API 的 RegisterEventHotKey 注册全局热键。":
    "Registers global hotkeys using Carbon API's RegisterEventHotKey.",
    "这与 Electron 的 globalShortcut 底层使用相同的 API。":
    "This uses the same API underneath as Electron's globalShortcut.",
    "使用 Quartz Event Tap 监听全局热键事件。": "Listens for global hotkey events using Quartz Event Tap.",
    "快捷键插件.": "Shortcuts plugin.",
    "最低优先级,依赖 UIPlugin": "Lowest priority, depends on UIPlugin",
    "快捷键名称常量": "Shortcut name constants",
    "创建 Quartz Event Tap.": "Create Quartz Event Tap.",
    "监听键盘按下事件": "Listen for key press events",
    "ListenOnly:不拦截事件流,Ctrl+C / 系统快捷键可正常到达终端":
    "ListenOnly: does not intercept event stream, Ctrl+C / system shortcuts can reach terminal normally",
    "Carbon 虚拟键码映射": "Carbon virtual key code mapping",
    "修饰键掩码": "Modifier key mask",
    "macOS global shortcut listening started (Quartz Event Tap)":
    "macOS global shortcut listening started (Quartz Event Tap)",
    "Failed to start macOS shortcut listening": "Failed to start macOS shortcut listening",
    "PyObjC is required for macOS hotkey support. ": "PyObjC is required for macOS hotkey support. ",
    "Install with: pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa":
    "Install with: pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa",
    "Failed to load macOS backend: ": "Failed to load macOS backend: ",
    "Falling back to pynput backend": "Falling back to pynput backend",
    "Failed to load pynput backend: ": "Failed to load pynput backend: ",
    "Using pynput backend": "Using pynput backend",
    "Using macOS Quartz Event Tap backend": "Using macOS Quartz Event Tap backend",
    "快捷键命令适配器.": "Shortcut command adapter.",
    "Failed to toggle conversation state": "Failed to toggle conversation state",
    "Failed to interrupt conversation": "Failed to interrupt conversation",
    "健康检查间隔(秒)": "Health check interval (seconds)",
    "控制字符映射": "Control character mapping",
    "concurrent.futures.Future 不能直接 await,需要特殊处理":
    "concurrent.futures.Future cannot be directly awaited, needs special handling",
    "等待 Future 完成(忽略取消异常)": "Wait for Future to complete (ignore cancellation exception)",
    "name -> hotkey_id": "name -> hotkey_id",
    "hotkey_id -> name": "hotkey_id -> name",

    # === plugins/audio.py ===
    "音频插件.": "Audio plugin.",
    "负责音频采集、编码、播放和发送。": "Responsible for audio capture, encoding, playback and sending.",
    "AudioCodec 经 Events.AUDIO_CODEC_CHANGED 发布,不直连 MusicPlayer。":
    "AudioCodec is published via Events.AUDIO_CODEC_CHANGED, does not directly connect to MusicPlayer.",
    "界面插件:起界面、转展示、接用户操作.": "UI plugin: creates interface, transforms display, handles user operations.",
    "界面插件.": "UI plugin.",
    "把状态/协议消息画到界面上.": "Draw state/protocol messages onto the interface.",
    "写界面:对话、音乐、状态、表情、按钮等.": "Write interface: chat, music, status, emotion, buttons, etc.",
    "界面上的按键、发文本、模式切换等,转成协议调用.":
    "UI key presses, text sending, mode switches, etc., converted to protocol calls.",
    "会话相关操作.": "Session-related operations.",
    "唤醒词插件.": "Wake word plugin.",
    "检测唤醒词并触发对话。": "Detects wake word and triggers conversation.",
    "配置变更时重新加载唤醒词模型.": "Reload wake word model when configuration changes.",
    "热重载唤醒词模型.": "Hot-reload wake word model.",
    "唤醒词检测回调.": "Wake word detection callback.",
    "通过依赖注入获取 AudioPlugin.": "Get AudioPlugin via dependency injection.",
    "设备状态变化时处理.": "Handle device state changes.",
    "配置变更时重新加载音频设备(含 PortAudio 重枚举).":
    "Reload audio devices when configuration changes (including PortAudio re-enumeration).",
    "设置页请求刷新设备列表:停流 → 重枚举 → 再开流.":
    "Settings page requests device list refresh: stop streams → re-enumerate → restart streams.",
    "payload 可为 asyncio.Future,完成后 set_result(list_audio_devices 结果)。":
    "payload can be asyncio.Future, after completion set_result(list_audio_devices result).",
    "停流后才能安全 _terminate PortAudio": "Must stop streams before safely _terminate PortAudio",
    "按当前配置重新打开流(名称匹配可能已指向新 index)":
    "Re-open streams with current config (name match may point to new index)",
    "无 codec(禁用音频)时仍尽量枚举,供设置页展示":
    "When no codec (audio disabled), still enumerate as much as possible for settings page display",
    "AudioPlugin: config change event received; reloading audio device":
    "AudioPlugin: config change event received; reloading audio device",
    "AudioPlugin: device refresh complete ": "AudioPlugin: device refresh complete ",
    "AudioPlugin: failed to reopen audio stream after device refresh":
    "AudioPlugin: failed to reopen audio stream after device refresh",
    "AudioPlugin: device refresh failed: ": "AudioPlugin: device refresh failed: ",
    "Could not publish AUDIO_CODEC_CHANGED: PluginContext / EventBus not ready":
    "Could not publish AUDIO_CODEC_CHANGED: PluginContext / EventBus not ready",
    "Failed to publish AUDIO_CODEC_CHANGED: ": "Failed to publish AUDIO_CODEC_CHANGED: ",
    "Failed to handle music state change: ": "Failed to handle music state change: ",
    "Failed to handle lyrics update: ": "Failed to handle lyrics update: ",
    "Invalid music state data received: ": "Invalid music state data received: ",
    "Invalid lyrics data received: ": "Invalid lyrics data received: ",
    "向订阅者(如 MusicPlayer)发布 AudioCodec 实例或 None.":
    "Publish AudioCodec instance or None to subscribers (e.g. MusicPlayer).",
    "XIAOZHI_DISABLE_AUDIO=1; audio plugin running in disabled mode":
    "XIAOZHI_DISABLE_AUDIO=1; audio plugin running in disabled mode",
    "Failed to import wake word detector: ": "Failed to import wake word detector: ",
    "Failed to start wake word detector: ": "Failed to start wake word detector: ",
    "Failed to stop wake word detector: ": "Failed to stop wake word detector: ",
    "Wake word detector is not enabled or failed to initialize":
    "Wake word detector is not enabled or failed to initialize",
    "Failed to hot-reload wake word model: ": "Failed to hot-reload wake word model: ",
    "Detector not initialized; cannot hot-reload": "Detector not initialized; cannot hot-reload",
    "audio_codec not found; cannot start wake word detection":
    "audio_codec not found; cannot start wake word detection",
    "Failed to handle wake word detection: ": "Failed to handle wake word detection: ",
    "WakeWordPlugin: config change event received; reloading wake word model":
    "WakeWordPlugin: config change event received; reloading wake word model",
    "UIPlugin subscribed to music/network/system prompt events":
    "UIPlugin subscribed to music/network/system prompt events",
    "SessionActions subscribed to UI user action events":
    "SessionActions subscribed to UI user action events",
    "Could not establish protocol connection; cancelling session action":
    "Could not establish protocol connection; cancelling session action",
    "Listen session started: mode=": "Listen session started: mode=",
    "Sending text: ": "Sending text: ",
    "Invalid send-text data: ": "Invalid send-text data: ",
    "Failed to toggle conversation state: ": "Failed to toggle conversation state: ",
    "Failed to interrupt conversation: ": "Failed to interrupt conversation: ",

    # === ui/shared/activation.py ===
    "激活服务未初始化": "Activation service not initialized",
    "未获取到激活数据": "Failed to get activation data",

    # === ui/shared/factory.py ===
    "按 mode 创建界面实现(返回 ViewPort).": "Create interface implementation by mode (returns ViewPort).",
    "gui / cli / tui / gpio;gpio 仅 Linux,其它平台回退 cli.":
    "gui / cli / tui / gpio; gpio only on Linux, other platforms fall back to cli.",

    # === ui/shared/viewport.py ===
    "界面统一接口.": "Unified interface for all views.",

    # === ui/cli/display.py ===
    "待命": "Idle",
    "正在关闭应用...": "Shutting down application...",
    "自动": "Auto",
    "手动": "Manual",
    "已连接": "Connected",
    "未连接": "Disconnected",
    "状态:": "State:",
    "连接:": "Connection:",
    "表情:": "Emotion:",
    "对话:": "Chat:",
    "音乐:": "Music:",
    "输入:": "Input:",
    "命令: r=开始/停止 | x=打断 | q=退出 | h=帮助 | 其他=发送文本":
    "Commands: r=start/stop | x=interrupt | q=quit | h=help | others=send text",
    "调度渲染失败:": "Render scheduling failed:",
    "CLI 渲染任务异常": "CLI render task exception",
    "创建渲染任务失败": "Failed to create render task",
    "日志记录失败": "Log recording failed",

    # === ui/cli/activation.py ===
    "设备激活": "Device Activation",
    "设备信息:": "Device info:",
    "序列号:": "Serial number:",
    "MAC地址:": "MAC address:",
    "需重新激活": "Needs re-activation",
    "已自动修复": "Auto-fixed",
    "已激活": "Activated",
    "未激活": "Not activated",
    "状态:": "Status:",
    "激活信息": "Activation info",
    "验证码:": "Verification code:",
    "说明:": "Instructions:",
    "请访问 xiaozhi.me 输入验证码": "Please visit xiaozhi.me to enter verification code",
    "激活步骤:": "Activation steps:",
    "设备激活成功!": "Device activation successful!",
    "设备已成功添加到您的账户": "Device successfully added to your account",
    "正在启动{...}...": "Starting {...}...",
    "设备激活失败": "Device activation failed",
    "可能的原因:": "Possible causes:",
    "解决方案:": "Solutions:",

    # === ui/cli/manager.py ===
    "待命": "Idle",

    # === ui/gpio/manager.py ===
    "待命": "Idle",

    # === ui/gpio/input.py ===
    "GPIO 输入模块.": "GPIO input module.",
    "初始化 GPIO 输入.": "Initialize GPIO input.",
    "设置 GPIO 引脚.": "Setup GPIO pins.",

    # === ui/tui/app.py ===
    "取消": "Cancel",
    "退出": "Quit",
    "设置": "Settings",
    "帮助": "Help",
    "编辑后点「保存」写盘并热应用 | Esc 取消 | choice 字段请填合法值(见占位提示)":
    "Edit then press Save to write and hot-apply | Esc to cancel | choice fields enter valid values (see placeholder)",
    "可选: ": "Optional: ",
    "待命": "Idle",
    "状态: 待命": "State: Idle",
    "连接: 未连接 | 模式: 手动 | 表情: neutral": "Connection: Disconnected | Mode: Manual | Emotion: neutral",
    "对话: —": "Chat: —",
    "音乐: —": "Music: —",
    "输入文本发送 | r 对话 | x 打断 | s 设置 | q 退出 | h 帮助":
    "Type text to send | r chat | x interrupt | s settings | q quit | h help",
    "F2 设置 · F1 帮助 · Ctrl+C 退出": "F2 Settings · F1 Help · Ctrl+C Quit",
    "已连接": "Connected",
    "未连接": "Disconnected",
    "自动": "Auto",
    "手动": "Manual",
    "帮助": "Help",
    "文本 → 发送给助手": "Text → send to assistant",
    "r → 开始/停止对话": "r → start/stop conversation",
    "x → 打断": "x → interrupt",
    "s / F2 → 设置": "s / F2 → settings",
    "q / Ctrl+C → 退出": "q / Ctrl+C → quit",
    "h / F1 → 帮助": "h / F1 → help",
    "配置已保存,正在热应用...": "Configuration saved, hot-applying...",
    "热应用失败: ": "Hot-apply failed: ",
    "小智 TUI 主应用.": "Xiaozhi TUI main application.",

    # === ui/tui/settings_data.py ===
    "系统": "System",
    "音频": "Audio",
    "摄像头": "Camera",
    "唤醒词": "Wake Word",
    "设备 ID": "Device ID",
    "客户端 ID": "Client ID",
    "输入设备名": "Input Device Name",
    "输出设备名": "Output Device Name",
    "Opus 输出采样率": "Opus Output Sample Rate",
    "帧时长 ms": "Frame Duration ms",
    "采集后端": "Capture Backend",
    "设备路径": "Device Path",
    "设备 index": "Device index",
    "宽度": "Width",
    "高度": "Height",
    "启用唤醒词": "Enable Wake Word",
    "唤醒词": "Wake Word",
    "语言": "Language",
    "OTA / 激活配置地址": "OTA / activation config address",
    "WebSocket 服务地址": "WebSocket server address",
    "Device-Id(通常为 MAC)": "Device-Id (usually MAC)",
    "按名称匹配麦克风(热插拔后 ID 会变)": "Match microphone by name (ID changes after hot-plug)",
    "按名称匹配扬声器/耳机": "Match speaker/headphone by name",
    "官方 24000 / 第三方常 16000": "Official 24000 / third-party often 16000",
    "20 低延迟 / 60 低 CPU": "20 low latency / 60 low CPU",
    "auto 先 OpenCV,失败再 Pi CSI": "auto: try OpenCV first, then Pi CSI on failure",
    "如 /dev/video0;非空优先于 index": "e.g. /dev/video0; non-empty takes priority over index",
    "OpenCV 数字索引": "OpenCV numeric index",
    "如:你好小智": "e.g.: Xiaozhi",
    "无变更": "No changes",
    "保存失败(写盘错误)": "Save failed (write error)",
    "已保存 {len(updates)} 项": "Saved {len(updates)} items",
    "保存失败: ": "Save failed: ",

    # === ui/tui/manager.py ===
    "待命": "Idle",

    # === mcp/tools/music/playback.py ===
    "没有正在播放的歌曲": "No song is playing",
    "已停止": "Stopped",
    "停止失败: ": "Stop failed: ",
    "已经处于暂停状态": "Already paused",
    "已暂停": "Paused",
    "暂停失败: ": "Pause failed: ",
    "当前未暂停": "Not currently paused",
    "已恢复播放": "Playback resumed",
    "恢复播放失败": "Playback resume failed",
    "恢复失败: ": "Resume failed: ",
    "无法刷新播放地址": "Cannot refresh playback URL",
    "没有可恢复的音源": "No resumable source",
    "无法找到音频文件": "Cannot find audio file",
    "跳转失败": "Seek failed",
    "跳转失败: ": "Seek failed: ",
    "未知歌曲总时长,无法按百分比跳转": "Unknown song duration; cannot seek by percent",
    "请提供 position(秒)或 percent(0-100)": "Please provide position (seconds) or percent (0-100)",
    "已跳转到 ": "Seeked to ",
    "(约 {percent:.0f}%)": "(about {percent:.0f}%)",
    "未能解析播放地址": "Failed to parse playback URL",
    "没有可跳转的音源": "No seekable source",
    "在在线直链模板,TTS 结束后可重新 resolve 拿新 CDN":
    "Online direct-link template; can re-resolve for new CDN after TTS ends",
    "TTS 逐句闪避:保留解码器与队列...": "TTS per-sentence ducking: keep decoder and queue...",
    "快速恢复(tts 闪避路径)...": "Fast recovery (TTS ducking path)...",
    "只清音乐播放队列;TTS 队列独立,不受跳转影响":
    "Only clear music play queue; TTS queue is independent and unaffected by seek",

    # === mcp/tools/music/music_player.py ===
    "本地文件不存在: ": "Local file does not exist: ",
    "正在播放: ": "Now playing: ",
    "播放失败": "Playback failed",
    "播放失败: ": "Playback failed: ",
    "未找到歌曲: ": "Song not found: ",
    "未知原因": "Unknown reason",
    "操作失败: ": "Operation failed: ",
    "当前歌曲没有歌词": "No lyrics for current song",
    "获取到 {len(self.lyrics)} 行歌词": "Retrieved {len(self.lyrics)} lines of lyrics",
    "未播放": "Not playing",
    "已暂停": "Paused",
    "播放中": "Playing",
    "未知": "Unknown",
    "当前歌曲: ": "Current song: ",
    "播放状态: ": "Playback status: ",
    "暂停来源: ": "Pause source: ",
    "tts=说话时临时暂停": "tts=temporary pause during speaking",
    "总时长秒: ": "Total duration (seconds): ",
    "当前位置秒: ": "Current position (seconds): ",
    "播放时长: ": "Playback duration: ",
    "当前位置: ": "Current position: ",
    "播放进度: ": "Playback progress: ",
    "歌词可用: 是/否": "Lyrics available: yes/no",
    "提示: 跳转百分之N请调用 seek(percent=N),不要用歌词推算":
    "Tip: to seek to N percent call seek(percent=N), do not calculate from lyrics",

    # === mcp/tools/music/register.py ===
    "MCP 工具侧默认 manual;TTS 暂停走 EventBus 不经此工具":
    "MCP tool side defaults to manual; TTS pause goes through EventBus, not this tool",
    "搜索播放完成": "Search and playback complete",
    "已暂停": "Paused",
    "已恢复播放": "Playback resumed",
    "停止播放完成": "Stop playback complete",
    "跳转完成": "Seek complete",
    "无法获取状态": "Cannot get status",
    "获取歌词失败": "Failed to get lyrics",
    "请指定 percent(0-100)或 position(秒)": "Please specify percent (0-100) or position (seconds)",
    "歌词内容:\n": "Lyrics content:\n",
    "本地音乐歌单 (共{total_count}首):": "Local music playlist (total {total_count} songs):",
    "本地缓存中没有音乐文件": "No music files in local cache",
    "获取本地歌单失败": "Failed to get local playlist",
    "MCP 工具": "MCP Tools",
    "搜索并播放": "Search and Play",
    "暂停": "Pause",
    "恢复": "Resume",
    "停止": "Stop",
    "跳转": "Seek",
    "获取状态": "Get Status",
    "获取歌词": "Get Lyrics",
    "本地歌单": "Local Playlist",

    # === mcp/tools/music/download.py ===
    "直链 API 返回无法解析的数据": "Direct-link API returned unparseable data",
    "直链 API 请求过于频繁,请稍后再试": "Direct-link API request too frequent, please try again later",
    "直链 API 获取播放地址失败(曲库无源或解析失败)":
    "Direct-link API failed to get playback URL (no source in library or parse failure)",
    "直链 API 内部错误": "Direct-link API internal error",
    "直链 API 参数错误": "Direct-link API parameter error",
    "直链 API 失败: ": "Direct-link API failed: ",
    "直链 API 未能返回播放 URL: ": "Direct-link API did not return playback URL: ",
    "直链 API 网络请求失败": "Direct-link API network request failed",
    "缺少歌曲 ID,无法回退官方接口": "Missing song ID, cannot fall back to official API",
    "酷我官方接口网络请求失败": "Kuwo official API network request failed",
    "该歌曲为付费内容,官方接口无法试听(": "This song is paid content, official API cannot preview(",
    "酷我官方接口未返回 URL: ": "Kuwo official API did not return URL: ",
    "未能解析播放地址": "Failed to parse playback URL",
    "解析播放 URL 异常: ": "Failed to parse playback URL: ",
    "下载文件为空": "Downloaded file is empty",
    "下载失败: ": "Download failed: ",
    "直链搞不定时,走酷我官方试听": "When direct-link fails, fall back to Kuwo official preview",
    "上次失败原因,给上层提示用": "Last failure reason, for upper-level prompt",
    "各家 JSON 字段不太一样,尽量抠出 url": "JSON fields vary across providers, try to extract url",
    "lx-music-api 常见 code": "Common lx-music-api codes",
    "对齐 Huibq/keep-alive render_api.js:只认 Key + UA":
    "Aligned with Huibq/keep-alive render_api.js: only recognizes Key + UA",
    "酷我官方 playUrl 用": "Kuwo official playUrl usage",
    "旧名": "Old name",
    "先按配置音质试,再试 128k": "Try configured quality first, then try 128k",
    "IP 被封了换音质也没用": "Changing quality won't help if IP is blocked",
    "免费歌能听;付费歌官方会直接说不行": "Free songs can be heard; paid songs official will say no",
    "没传 song_id 时从 url 路径里抠": "When song_id not passed, extract from URL path",
    "直链 → 降音质 → 官方接口": "Direct-link → lower quality → official API",
    "CDN 偶发 RemoteDisconnected,多试两次": "CDN occasionally RemoteDisconnected, try twice more",
    "同一首歌已在预取": "Same song already prefetched",
    "直链 API": "Direct-link API",
    "酷我": "Kuwo",

    # === mcp/tools/camera/register.py ===
    "摄像头 MCP 工具注册与工厂.": "Camera MCP tool registration and factory.",
    "按配置创建一个摄像头实现并返回": "Create a camera implementation by config and return",
    "拍摄照片.": "Take a photo.",
    "图中描绘的是什么景象?请详细描述。": "What scene is depicted in the image? Please describe in detail.",

    # === mcp/tools/camera/vl_camera.py ===
    "智普AI摄像头实现.": "Zhipu AI camera implementation.",
    "初始化智普AI摄像头.": "Initialize Zhipu AI camera.",
    "捕获图像(OpenCV/V4L2 或 picamera2,见 capture_backend).":
    "Capture image (OpenCV/V4L2 or picamera2, see capture_backend).",

    # === mcp/tools/camera/*.py ===
    "摄像头模块.": "Camera module.",
    "基础摄像头类.": "Base camera class.",
    "普通摄像头实现.": "Normal camera implementation.",
    "摄像头捕获后端.": "Camera capture backend.",

    # === mcp/tools/screenshot/register.py ===
    "桌面截图 MCP 工具注册.": "Desktop screenshot MCP tool registration.",
    "创建桌面截图实现": "Create desktop screenshot implementation",
    "按配置创建一个截图实现并返回": "Create a screenshot implementation by config and return",
    "与拍照共用 vision 配置(若已配置)": "Share vision config with photo camera (if configured)",
    "analyze 若需要 fallback 到 photo camera 的 explain 设置,挂引用":
    "analyze falls back to photo camera's explain settings if needed, mount reference",
    "截图.": "Take a screenshot.",
    "截取桌面截图并返回图像数据.": "Capture desktop screenshot and return image data.",
    "主屏": "Main display",
    "副屏": "Secondary display",
    "笔记本": "Laptop",
    "内屏": "Built-in display",
    "外接": "External",
    "外屏": "External display",
    "第二屏": "Second screen",
    "主显示器": "Main monitor",
    "副显示器": "Secondary monitor",

    # === mcp/tools/screenshot/screenshot_camera.py ===
    "截图摄像头模块.": "Screenshot camera module.",
    "桌面截图摄像头实现.": "Desktop screenshot camera implementation.",

    # === mcp/tools/weather/service.py ===
    "天气数据(当前 mock;待接真 API).": "Weather data (currently mock; real API pending).",
    "TODO: 实际项目中应调用天气API": "TODO: In production should call weather API",
    "北京": "Beijing",
    "晴朗": "Clear",
    "东北风 3级": "Northeast wind 3",
    "今天": "Today",
    "明天": "Tomorrow",
    "后天": "Day after tomorrow",
    "晴": "Clear",
    "多云": "Cloudy",
    "小雨": "Light rain",

    # === mcp/tools/app/killer.py ===
    "找到 {len(apps)} 个正在运行的应用程序": "Found {len(apps)} running applications",
    "列出运行中应用程序失败: ": "Failed to list running applications: ",

    # === mcp/tools/app/scanner.py ===
    "不支持的操作系统": "Unsupported operating system",
    "成功扫描到 {len(apps)} 个已安装应用程序": "Successfully scanned {len(apps)} installed applications",
    "扫描应用程序失败: ": "Failed to scan applications: ",

    # === mcp/tools/app/scanner_windows.py ===
    "Windows应用程序扫描器.": "Windows application scanner.",
    "专门用于Windows系统的应用程序扫描和管理": "Specifically for Windows system application scanning and management",
    "扫描Windows系统中已安装的应用程序.": "Scan installed applications in Windows system.",
    "应用程序列表": "Application list",
    "扫描开始菜单中的主要应用程序(最直接的方法)": "Scan major applications in Start Menu (most direct method)",
    "扫描注册表中的主要第三方应用(过滤系统组件)": "Scan major third-party apps in registry (filter system components)",
    "添加常见的系统应用(只保留用户常用的)": "Add common system apps (keep only user-frequently-used)",
    "计算器": "Calculator",
    "记事本": "Notepad",
    "画图": "Paint",
    "文件资源管理器": "File Explorer",
    "任务管理器": "Task Manager",
    "控制面板": "Control Panel",

    # === mcp/tools/app/scanner_linux.py ===
    "Linux应用程序扫描器.": "Linux application scanner.",
    "专门用于Linux系统的应用程序扫描和管理": "Specifically for Linux system application scanning and management",
    "扫描Linux系统中已安装的应用程序.": "Scan installed applications in Linux system.",
    "文本编辑器": "Text Editor",
    "Firefox浏览器": "Firefox Browser",
    "文件管理器": "File Manager",
    "终端": "Terminal",

    # === mcp/tools/app/scanner_mac.py ===
    "macOS应用程序扫描器.": "macOS application scanner.",
    "专门用于macOS系统的应用程序扫描和管理": "Specifically for macOS system application scanning and management",
    "扫描macOS系统中已安装的应用程序.": "Scan installed applications in macOS system.",
    "文本编辑": "TextEdit",
    "预览": "Preview",
    "Safari浏览器": "Safari Browser",
    "访达": "Finder",
    "系统偏好设置": "System Preferences",

    # === mcp/tools/volume/register.py ===
    "音量设为50": "Set volume to 50",
    "调大声音": "Turn up volume",
    "声音小一点": "Turn down volume slightly",
    "静音": "Mute",

    # === mcp/tools/app/register.py ===
    "QQ音乐": "QQ Music",
    "微信": "WeChat",
    "计算器": "Calculator",
    "记事本": "Notepad",
    "浏览器": "Browser",

    # === mcp/tools/camera/register.py ===
    "摄像头 MCP 工具注册与工厂.": "Camera MCP tool registration and factory.",

    # === src/plugins/shortcuts/base.py ===
    "快捷键后端抽象基类.": "Shortcut backend abstract base class.",
    "快捷键配置.": "Shortcut configuration.",
    "启动快捷键监听.": "Start shortcut listening.",
    "停止快捷键监听.": "Stop shortcut listening.",
    "注册快捷键.": "Register shortcut.",
    "注销快捷键.": "Unregister shortcut.",
    "注销所有快捷键.": "Unregister all shortcuts.",
    "是否正在运行.": "Is running.",
    "运行回调函数(线程安全).": "Run callback (thread-safe).",
    "pynput 快捷键后端.": "pynput shortcut backend.",
    "使用 pynput 监听全局快捷键.": "Listens for global shortcuts using pynput.",

    # === src/utils/config_manager.py ===
    "ConfigManager 未初始化:请在入口调用 initialize_config()":
    "ConfigManager not initialized: call initialize_config() at entry point",
}

# ---------------------------------------------------------------------------
# 2. SKIP LIST — exact substrings that must NOT be translated
# ---------------------------------------------------------------------------
SKIP_STRINGS = [
    # activation_keywords in common_utils.py
    "激活码",
    "输入验证码",
    "验证码",
    "登录",
    "控制面板",
    "激活",
    "绑定设备",
    "添加设备",
    "面板",
    "xiaozhi.me",
    # regex patterns in common_utils.py
    "验证码[::]",
    "输入验证码[::]",
    "激活码[::]",
    # side_effects.py announcement text
    ".请登录到控制面板添加设备,输入验证码:",
    # download.py functional checks
    "禁止批量下载",
    "封禁",
    "付费",
    # app/utils.py functional mappings
    "QQ音乐",
    "腾讯会议",
    "微信",
    "钉钉",
    "飞书",
    # config_manager defaults
    "你好小智",
    # ota.py header
    "Accept-Language",
    "zh-CN",
    # download.py header
    "zh-CN,zh;q=0.9,en;q=0.8",
    # settings_data.py boolean accept value
    '"是"',
    # activation_announcer.py locale
    "zh-CN",
    # screenshot register alias keys
    '"main"',
    '"secondary"',
]


def get_cjk_runs(text):
    """Find all maximal runs of CJK characters."""
    runs = []
    i = 0
    while i < len(text):
        cp = ord(text[i])
        if 0x4E00 <= cp <= 0x9FFF:
            j = i
            while j < len(text) and 0x4E00 <= ord(text[j]) <= 0x9FFF:
                j += 1
            runs.append((i, j, text[i:j]))
            i = j
        else:
            i += 1
    return runs


def translate_file(filepath):
    """Translate a single Python file using longest-first raw text replacement."""
    with io.open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.strip():
        return False, content

    original = content

    # Get all CJK runs
    runs = get_cjk_runs(content)

    # Sort by length descending (longest first) to avoid substring collisions
    runs.sort(key=lambda x: len(x[2]), reverse=True)

    # Build replacement list, skipping protected strings
    replacements = []
    for start, end, run_text in runs:
        # Check if this run contains any skip string
        skip_match = any(skip in run_text for skip in SKIP_STRINGS)
        if skip_match:
            continue

        if run_text in TRANSLATIONS:
            replacements.append((start, end, TRANSLATIONS[run_text]))

    # Apply replacements from end to start to preserve positions
    replacements.sort(key=lambda x: x[0], reverse=True)

    for start, end, replacement in replacements:
        content = content[:start] + replacement + content[end:]

    return content != original, content


def translate_build_files():
    """Translate Chinese text in build.json and py-xiaozhi.spec."""
    # build.json
    build_path = ROOT / "build.json"
    if build_path.exists():
        with io.open(build_path, "r", encoding="utf-8") as f:
            content = f.read()
        new_content = content
        for cn, en in [
            ("此应用需要访问麦克风以实现录音功能", "This app needs access to the microphone for recording"),
            ("此应用需要使用语音识别功能以理解语音指令", "This app needs speech recognition to understand voice commands"),
            ("此应用需要访问摄像头以实现拍照或视频功能", "This app needs camera access for photo or video"),
            ("小智 安装器", "Xiaozhi Installer"),
        ]:
            if cn in new_content:
                new_content = new_content.replace(cn, en)
        if new_content != content:
            with io.open(build_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print("  TRANSLATED: build.json")

    # py-xiaozhi.spec
    spec_path = ROOT / "py-xiaozhi.spec"
    if spec_path.exists():
        with io.open(spec_path, "r", encoding="utf-8") as f:
            content = f.read()
        new_content = content
        for cn, en in [
            ("此应用需要访问麦克风以实现录音功能", "This app needs access to the microphone for recording"),
            ("此应用需要访问摄像头以实现拍照或视频功能", "This app needs camera access for photo or video"),
            ("此应用需要使用语音识别功能以理解语音指令", "This app needs speech recognition to understand voice commands"),
            ("小智", "Xiaozhi"),
        ]:
            if cn in new_content:
                new_content = new_content.replace(cn, en)
        if new_content != content:
            with io.open(spec_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print("  TRANSLATED: py-xiaozhi.spec")


def main():
    """Walk src/ and translate all Python files."""
    py_files = list(SRC.rglob("*.py"))
    total = len(py_files)
    changed_count = 0
    error_count = 0

    print(f"Found {total} Python files in src/")

    for filepath in sorted(py_files):
        try:
            changed, new_content = translate_file(filepath)
            if changed:
                with io.open(filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                changed_count += 1
                print(f"  TRANSLATED: {filepath.relative_to(ROOT)}")
        except Exception as e:
            error_count += 1
            print(f"  ERROR: {filepath.relative_to(ROOT)}: {e}")

    print(f"\nDone! Changed: {changed_count}, Errors: {error_count}")

    # Also translate build.json and py-xiaozhi.spec
    translate_build_files()


if __name__ == "__main__":
    main()
