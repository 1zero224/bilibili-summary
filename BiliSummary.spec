# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for BiliSummary macOS app.
Build with: pyinstaller BiliSummary.spec
"""

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

block_cipher = None

# Collect hidden imports
hiddenimports = [
    'uvicorn',
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
    'anthropic',
    'dotenv',
    'toml',
    'aiohttp',
    'webview',
    'pydantic',
    'starlette',
    'starlette.routing',
    'starlette.middleware',
    'starlette.responses',
    'starlette.staticfiles',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',
    'httptools',
    'httptools.parser',
    'httptools.parser.parser',
    'av',
    'routes',
    'routes.deps',
    'routes.favorites',
    'routes.asr',
    'routes.settings',
    'routes.auth',
]

# Add bilibili_api submodules
hiddenimports += collect_submodules('bilibili_api')
hiddenimports += collect_submodules('av')
hiddenimports += collect_submodules('faster_whisper')
hiddenimports += collect_submodules('ctranslate2')

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=(
        collect_dynamic_libs('av')
        + collect_dynamic_libs('ctranslate2')
        + collect_dynamic_libs('onnxruntime')
    ),
    datas=[
        ('static', 'static'),           # Bundle static/ directory
        ('config.toml', '.'),            # Bundle config.toml
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'tkinter', 'cv2',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='BiliSummary',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,         # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BiliSummary',
)

app = BUNDLE(
    coll,
    name='BiliSummary.app',
    icon='icon.icns',
    bundle_identifier='com.bilisummary.app',
    info_plist={
        'CFBundleName': 'BiliSummary',
        'CFBundleDisplayName': 'BiliSummary',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '10.15',
    },
)
