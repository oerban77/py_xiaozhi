# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('models', 'models'), ('scripts', 'scripts'), ('src', 'src'), ('libs', 'libs'), ('assets', 'assets')]
binaries = [('.venv/Lib/site-packages/sherpa_onnx/lib/onnxruntime.dll', 'sherpa_onnx/lib'), ('.venv/Lib/site-packages/sherpa_onnx/lib/sherpa-onnx-c-api.dll', 'sherpa_onnx/lib'), ('.venv/Lib/site-packages/sherpa_onnx/lib/sherpa-onnx-cxx-api.dll', 'sherpa_onnx/lib')]
hiddenimports = ['numpy', 'sounddevice', 'soxr', 'opuslib', 'opuslib.api.decoder', 'opuslib.api.encoder', 'sherpa_onnx', 'cv2', 'PIL', 'PIL.ImageGrab', 'pypdf', 'olefile', 'mutagen', 'mutagen.id3', 'pyperclip', 'pynput', 'pynput.keyboard', 'machineid', 'psutil', 'platformdirs', 'cryptography', 'cryptography.hazmat', 'aiohttp', 'requests', 'websockets', 'paho', 'paho.mqtt', 'paho.mqtt.client', 'openai', 'httpx', 'pypinyin', 'textual', 'win32com', 'win32com.client', 'winreg', 'ctypes.wintypes', 'comtypes', 'comtypes.client', 'pycaw', 'pycaw.pycaw', 'textual.widgets._tab_pane']
tmp_ret = collect_all('sherpa_onnx')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['terminal_entry.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6', 'qasync'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='py-xiaozhi-terminal',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/icon.png'],
    contents_directory='.',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='py-xiaozhi-terminal',
)
