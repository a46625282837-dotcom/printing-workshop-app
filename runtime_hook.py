import os
import sys

if hasattr(sys, '_MEIPASS'):
    dll_dir = os.path.join(sys._MEIPASS, 'onnxruntime', 'capi')
    if os.path.isdir(dll_dir):
        os.environ['PATH'] = dll_dir + os.pathsep + os.environ.get('PATH', '')
