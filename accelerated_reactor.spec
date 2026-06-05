# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['accelerated_reactor.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'engineio.async_drivers.threading',
        'socketio.async_drivers.threading',
        'flask_socketio',
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        'serial.tools.list_ports_windows',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AcceleratedReactor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,
)
