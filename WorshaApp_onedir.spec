# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_all

_rembg_all_data, _rembg_all_bins, _rembg_all_hidden = collect_all('rembg')
_pooch_data, _pooch_bins, _pooch_hidden = collect_all('pooch')
_filetype_data, _filetype_bins, _filetype_hidden = collect_all('filetype')
_watchdog_data, _watchdog_bins, _watchdog_hidden = collect_all('watchdog')
_scipy_data, _scipy_bins, _scipy_hidden = collect_all('scipy')
_pymatting_data, _pymatting_bins, _pymatting_hidden = collect_all('pymatting')
_skimage_data, _skimage_bins, _skimage_hidden = collect_all('skimage')
_ultralytics_data, _ultralytics_bins, _ultralytics_hidden = collect_all('ultralytics')

# collect_all يسقط امتدادات (.pyd) ومكتبات scipy الديناميكية من تثبيت user-site.
# نجمعها يدوياً من مسار التثبيت الفعلي لضمان عمل scipy.ndimage في الحزمة.
import glob as _glob
import os as _os
import scipy as _scipy_mod
_scipy_root = _os.path.dirname(_scipy_mod.__file__)
_scipy_extra_bins = []
for _pyd in _glob.glob(_os.path.join(_scipy_root, '**', '*.pyd'), recursive=True):
    _scipy_extra_bins.append((_pyd, _os.path.relpath(_os.path.dirname(_pyd), _scipy_root)))
_scipy_libs_src = _os.path.join(_os.path.dirname(_scipy_root), 'scipy.libs')
if _os.path.isdir(_scipy_libs_src):
    _scipy_extra_bins.append((_scipy_libs_src, 'scipy.libs'))

# فقط النماذج المستخدمة فعلياً وقت التشغيل
datas = [('ui', 'ui'), ('core', 'core'), ('img', 'img'),
         ('models/card_detector.onnx', 'models'),
         ('models/u2netp.onnx', 'models'),
         ('models/id_card.onnx', 'models'),
         ('models/id_card.pt', 'models'),
         ('models/isnet-general-use.onnx', 'models'),
         ('i1.jpeg', '.'),
         ('card_sample..jpg', '.'),
         ('requirements-lock.txt', '.')]
# cv2: الاحتفاظ فقط بـ frontalface_default.xml (المُستخدم في الكود) وملفات الإعداد.
datas += [d for d in collect_data_files('cv2')
          if 'videoio_ffmpeg' not in d[0].replace('\\', '/')
          and (not d[0].lower().endswith('.xml')
               or d[0].replace('\\', '/').endswith('haarcascade_frontalface_default.xml'))]
datas += _rembg_all_data
datas += collect_data_files('mediapipe')
datas += _pooch_data + _filetype_data + _watchdog_data + _scipy_data + _pymatting_data + _skimage_data + _ultralytics_data
_EXTRA_BINARIES = _rembg_all_bins + _pooch_bins + _filetype_bins + _watchdog_bins + _scipy_bins + _pymatting_bins + _skimage_bins + _scipy_extra_bins + _ultralytics_bins

_HEAVY_EXCLUDES = [
    # matplotlib/pandas ليست تبعيات تشغيل حقيقية في هذا التطبيق.
    'matplotlib', 'pandas', 'pytest', 'IPython',
    'polars', 'polars-runtime-32', 'polars_lts', 'hf_xet',
    'huggingface_hub', 'tiktoken',
    # وحدات تُسحب تلقائياً بلا استخدام (QWidgets فقط، و win32ui لـ MFC لا
    # يستعمله السكener الذي يعتمد win32com.client فقط، و skimage مرفقة بلا استيراد).
    'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtPdf', 'PySide6.QtNetwork',
    'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtMultimedia',
    'PySide6.QtCharts', 'PySide6.Qt3DCore', 'PySide6.QtDataVisualization',
    'win32ui', 'sounddevice',
]

a = Analysis(
    ['G:\\hp\\Documents\\pro\\idcard_app\\main.py'],
    pathex=[],
    binaries=_EXTRA_BINARIES,
    datas=datas,
    hiddenimports=['PySide6.QtSvg', 'PySide6.QtSvgWidgets', 'mediapipe', 'mediapipe.solutions', 'cv2', 'onnxruntime', 'core.updater', 'core.yolo_onnx', 'core.photo_processor', 'PIL', 'PIL.Image', 'PIL.ImageOps', 'PIL.ImageFile', 'PIL.ImageDraw', 'PIL.ImageEnhance', 'PIL.ImageFilter', 'PIL.ImageSequence', 'PIL.PngImagePlugin', 'PIL.JpegImagePlugin', 'PIL.TiffImagePlugin', 'PIL.GifImagePlugin', 'PIL.BmpImagePlugin', 'PIL.WebPImagePlugin', 'PIL.TgaImagePlugin', 'PIL.IcoImagePlugin', 'scipy', 'scipy.ndimage', 'pymatting', 'skimage', 'torch', 'torchvision', 'ultralytics', 'fitz', 'pymupdf'] + _rembg_all_hidden + _pooch_hidden + _filetype_hidden + _watchdog_hidden + _scipy_hidden + _pymatting_hidden + _skimage_hidden + _ultralytics_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_HEAVY_EXCLUDES,
    noarchive=False,
    optimize=0,
)

# التطبيق QWidgets بحت: يستخدم QtCore/Gui/Widgets/Svg/PrintSupport فقط.
# إسقاط وحدات Qt المُجرَّدة بلا استيراد (Qml/Quick/Pdf/Network/OpenGL)
# مع إبقاء opengl32sw.dll (عرض برمجي احتياطي) لضمان عدم كسر الرسم.
_UNUSED_QT = ('qt6quick', 'qt6qml', 'qt6virtualkeyboard', 'qt6pdf',
              'qt6network', 'qt6opengl', 'qtquick_', 'qtqml')
a.binaries = [b for b in a.binaries
              if 'videoio_ffmpeg' not in b[0].replace('\\', '/')
              and not any(t in b[0].replace('\\', '/').lower() for t in _UNUSED_QT)]
a.datas = [d for d in a.datas
           if not any(t in d[0].replace('\\', '/').lower() for t in _UNUSED_QT)
           and not ('/translations/' in d[0].replace('\\', '/') and d[0].replace('\\', '/').endswith('.qm'))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='WorshaApp',
)
