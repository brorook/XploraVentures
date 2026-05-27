# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

esptool_datas, esptool_binaries, esptool_hiddenimports = collect_all('esptool')

a = Analysis(
    ['XploraVentures.py'],
    pathex=[],
    binaries=esptool_binaries,
    datas=esptool_datas,
    hiddenimports=[
        'engineio',
        'engineio.async_drivers.threading',
        'socketio',
        'flask_socketio',
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        'pkg_resources.py2_compat',
        *esptool_hiddenimports,
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
    a.zipfiles,
    a.datas,
    [],
    name='XploraVentures',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
