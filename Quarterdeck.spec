# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Quarterdeck macOS app."""
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None
project_root = os.path.dirname(os.path.abspath(SPEC))

# collect_all gathers all submodules, data files, and binaries for packages
# that use dynamic imports (watchdog picks its Observer backend at runtime).
watchdog_datas, watchdog_binaries, watchdog_hiddenimports = collect_all('watchdog')
qrcode_datas, qrcode_binaries, qrcode_hiddenimports = collect_all('qrcode')

a = Analysis(
    [os.path.join(project_root, 'app.py')],
    pathex=[project_root],
    binaries=watchdog_binaries + qrcode_binaries,
    datas=[
        (os.path.join(project_root, 'frontend', 'dist'), 'frontend/dist'),
        (os.path.join(project_root, 'backend'), 'backend'),
        (os.path.join(project_root, 'scripts'), 'scripts'),
    ] + watchdog_datas + qrcode_datas,
    hiddenimports=watchdog_hiddenimports + qrcode_hiddenimports + [
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        'fastapi',
        'fastapi.staticfiles',
        'fastapi.responses',
        'fastapi.middleware',
        'fastapi.middleware.cors',
        'starlette.responses',
        'starlette.staticfiles',
        'starlette.middleware',
        'starlette.routing',
        'webview',
        'webview.platforms.cocoa',
        'objc',
        'Foundation',
        'AppKit',
        'WebKit',
        'Quartz',
        'backend',
        'backend.api',
        'backend.config',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', '_tkinter', 'matplotlib', 'numpy', 'PIL'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Quarterdeck',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Quarterdeck',
)

app = BUNDLE(
    coll,
    name='Quarterdeck.app',
    icon='icon.icns',
    bundle_identifier='com.vidanov.quarterdeck',
    info_plist={
        'CFBundleName': 'Quarterdeck',
        'CFBundleDisplayName': 'Quarterdeck',
        'CFBundleShortVersionString': '0.1.0',
        'CFBundleVersion': '0.1.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '13.0',
        # Apple Events are still used, but only for scriptable, explicit actions:
        # reading the front Finder window's folder, the folder-picker dialog, and
        # handing a session to Terminal.app or iTerm2. Accessibility is no longer
        # requested — nothing simulates keystrokes any more.
        'NSAppleEventsUsageDescription': (
            'Quarterdeck uses AppleScript to read the front Finder window as a default '
            'directory and to open a session in Terminal.app or iTerm2.'
        ),
    },
)
