# -*- mode: python ; coding: utf-8 -*-
# بناء IDPhotoPro — يجمع PyMuPDF (fitz/pymupdf) بكامل ملفاتها الثنائية
# وكل الاعتماديات حتى لا تظهر رسالة "pymupdf مفقود" في تشخيص الإقلاع.

from PyInstaller.utils.hooks import collect_all

# ---- جمع PyMuPDF بالكامل (وحدات + ملفات ثنائية + بيانات) ----
_extra_datas = []
_extra_binaries = []
_extra_hidden = []
for _pkg in ('fitz', 'pymupdf'):
    try:
        _res = collect_all(_pkg)
        _d, _b, _h = _res
        _extra_datas += list(_d)
        _extra_binaries += list(_b)
        _extra_hidden += list(_h)
        print("collect_all(%s): +%d datas +%d binaries +%d hidden" % (
            _pkg, len(_d), len(_b), len(_h)))
    except Exception as _e:
        print("collect_all(%s) فشل: %s" % (_pkg, _e))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_extra_binaries,
    datas=[('core/ai_config.json', 'core'), ('img', 'img'), ('models', 'models')] + _extra_datas,
    hiddenimports=['fitz', 'pymupdf'] + _extra_hidden,
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
    [],
    exclude_binaries=True,
    name='IDPhotoPro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    name='IDPhotoPro',
)
