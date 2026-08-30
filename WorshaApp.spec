# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = [('ui', 'ui'), ('core', 'core'), ('img', 'img')]
datas += [d for d in collect_data_files('cv2')
          if 'videoio_ffmpeg' not in d[0].replace('\\', '/')]
datas += collect_data_files('mediapipe')


# Dead-weight dependencies verified to be unused at runtime:
#  - torch/torchvision/gfpgan/realesrgan/basicsr/facexlib: core/ai_enhance.py
#    (the only importer, and its enhance_image() is never called anywhere in
#    the app) plus these packages are not even installed (they always import-fail
#    so the enhancement already falls back to MediaPipe + OpenCV).
#  - scipy: referenced only in test files / docstrings, never loaded at runtime.
#  - numba/llvmlite: used only by numpy type-checking stubs and by torch.
#  - matplotlib: pulled only by mediapipe.tasks...drawing_utils (unused API;
#    the app uses mediapipe.solutions.face_mesh only).
# Removing these cuts the frozen size significantly without touching features.
_HEAVY_EXCLUDES = [
    'torch', 'torchvision', 'gfpgan', 'realesrgan', 'basicsr', 'facexlib',
    'scipy', 'numba', 'llvmlite', 'matplotlib', 'pytest', 'IPython',
]


a = Analysis(
    ['G:\\hp\\Documents\\pro\\idcard_app\\main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['PySide6.QtSvg', 'PySide6.QtSvgWidgets', 'mediapipe', 'mediapipe.solutions', 'cv2', 'onnxruntime', 'core.updater'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_HEAVY_EXCLUDES,
    noarchive=False,
    optimize=0,
)


# The app is image-only: drop OpenCV's large videoio (FFmpeg) binaries that
# are pulled in by PyInstaller's cv2 hook but never used at runtime.
a.binaries = [b for b in a.binaries
              if 'videoio_ffmpeg' not in b[0].replace('\\', '/')]


pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='WorshaApp',
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
    icon=['G:\\hp\\Documents\\pro\\idcard_app\\i1.ico'],
)
