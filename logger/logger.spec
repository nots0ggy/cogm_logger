# -*- mode: python ; coding: utf-8 -*-

import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# On non-Windows builds, exclude the Windows-only scapy backend so PyInstaller
# doesn't choke on it. On Windows, it must be bundled for network sniffing.
_excludes = [] if sys.platform == 'win32' else ['scapy.arch.windows']

# Scapy ships modules dynamically (layers, contribs, arch). PyInstaller's
# static analyzer misses many of them and silently produces a broken
# .scapy_all module, which then fails sibling imports like
# `from src.options import analyze` because analyze.py top-level imports
# scapy. collect_submodules walks the package and bundles everything.
_scapy_hidden = collect_submodules('scapy')

# Pin our own option modules so PyInstaller never elides one.
_app_hidden = [
    'src',
    'src.config',
    'src.parser',
    'src.options',
    'src.options.analyze',
    'src.options.open',
    'src.options.record',
    'src.options.sniff',
    'src.options.status_check',
    'src.options.update_config',
]


a = Analysis(
    ['logger.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=_scapy_hidden + _app_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
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
    name='logger',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon\\icon.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='logger',
)
