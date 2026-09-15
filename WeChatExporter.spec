# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the local-only Windows GUI."""

from PyInstaller.utils.hooks import collect_submodules


# gui.services deliberately imports the existing core lazily so its unit tests can
# run without Windows-only modules. Tell PyInstaller about those runtime imports.
hiddenimports = (
    collect_submodules("wxManager")
    + collect_submodules("exporter")
    + [
        "pythoncom",
        "pywintypes",
        "win32api",
        "win32com",
        "win32com.client",
        "win32timezone",
        "Crypto.Cipher.AES",
        "Crypto.Hash.SHA512",
        "Crypto.Protocol.KDF",
        "PIL.Image",
        "PIL.JpegImagePlugin",
        "docx",
        "docx.oxml",
    ]
)

# Keep destination paths identical to the source package layout because the
# existing exporters locate their template, emoji, avatar, and ffmpeg assets
# relative to exporter.__file__.
datas = [
    ("exporter/resources", "exporter/resources"),
    ("wxManager/parser/util/protocbuf", "wxManager/parser/util/protocbuf"),
]

a = Analysis(
    ["gui/app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WeChatExporter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
