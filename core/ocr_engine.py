import os
import logging
from typing import Optional

import easyocr
from PIL import Image
import numpy as np

from core.runtime import data_dir as runtime_data_dir

logger = logging.getLogger(__name__)

_reader: Optional[easyocr.Reader] = None
# مجلد نماذج OCR بياناتي قابل للكتابة، يمرّ من طبقة runtime الموحّدة.
MODEL_DIR = os.path.join(runtime_data_dir(), ".easyocr_models")


def _get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        os.makedirs(MODEL_DIR, exist_ok=True)
        _reader = easyocr.Reader(
            ["ar", "en"],
            model_storage_directory=MODEL_DIR,
            download_enabled=True,
        )
        logger.info("EasyOCR reader initialized (ar + en)")
    return _reader


def extract_text(pil_image: Image.Image, detail: int = 0) -> list[str]:
    reader = _get_reader()
    img_array = np.array(pil_image.convert("RGB"))
    results = reader.readtext(img_array, detail=detail)
    if detail == 0:
        return results
    return [{"bbox": r[0], "text": r[1], "confidence": r[2]} for r in results]


def get_full_text(pil_image: Image.Image) -> str:
    lines = extract_text(pil_image, detail=0)
    return "\n".join(lines)
