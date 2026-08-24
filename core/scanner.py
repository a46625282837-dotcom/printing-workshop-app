"""WIA scanner interface for Windows."""
import logging
import os
import tempfile

from PySide6.QtGui import QImage

logger = logging.getLogger(__name__)

_win32com = None
try:
    import win32com.client as _win32com
except ImportError:
    pass


def is_available():
    return _win32com is not None


def list_scanners():
    if not _win32com:
        return []
    try:
        dm = _win32com.Dispatch("WIA.DeviceManager")
        scanners = []
        for info in dm.DeviceInfos:
            try:
                if info.Type == 1:
                    scanners.append({
                        "name": info.Properties("Name").Value,
                        "device_id": info.DeviceID,
                    })
            except Exception:
                pass
        return scanners
    except Exception as e:
        logger.warning("Cannot list scanners: %s", e)
        return []


def _try_read_image_file(wia_img):
    """Try multiple methods to extract image from WIA ImageFile."""
    tmpdir = tempfile.gettempdir()

    for ext in [".bmp", ".png", ".tiff", ".jpg"]:
        path = os.path.join(tmpdir, "_wwk_scan" + ext)
        try:
            wia_img.SaveFile(path)
            qimg = QImage(path)
            if not qimg.isNull():
                try:
                    os.remove(path)
                except OSError:
                    pass
                return qimg
            try:
                os.remove(path)
            except OSError:
                pass
        except Exception as e:
            logger.debug("SaveFile(%s) failed: %s", ext, e)
            try:
                os.remove(path)
            except OSError:
                pass

    try:
        file_data = wia_img.FileData
        if file_data and len(file_data) > 0:
            qimg = QImage()
            qimg.loadFromData(bytes(file_data))
            if not qimg.isNull():
                return qimg
    except Exception as e:
        logger.debug("FileData read failed: %s", e)

    try:
        qimg = QImage()
        qimg.loadFromData(wia_img.IPImageData)
        if not qimg.isNull():
            return qimg
    except Exception as e:
        logger.debug("IPImageData read failed: %s", e)

    return None


def scan_with_dialog(device_id=None):
    if not _win32com:
        return None
    try:
        dlg = _win32com.Dispatch("WIA.CommonDialog")
        if device_id:
            try:
                dm = _win32com.Dispatch("WIA.DeviceManager")
                dev = dm.CreateDevice(device_id)
                wia_img = dlg.ShowAcquireImage(dev)
            except Exception as e:
                logger.warning("CreateDevice failed, trying without device: %s", e)
                wia_img = dlg.ShowAcquireImage()
        else:
            wia_img = dlg.ShowAcquireImage()

        if not wia_img:
            logger.info("User cancelled scan or no image returned")
            return None

        result = _try_read_image_file(wia_img)
        if result:
            return result

        logger.error("All scan methods failed")
        return None
    except Exception as e:
        logger.error("Scan error: %s", e)
        return None


def scan_direct(device_id):
    if not _win32com or not device_id:
        logger.warning("scan_direct: no win32com or device_id")
        return None
    try:
        dm = _win32com.Dispatch("WIA.DeviceManager")
        dev = dm.CreateDevice(device_id)
        items = dev.Items
        if items.Count == 0:
            logger.warning("scan_direct: no items")
            return None
        wia_item = items(1)
        try:
            result = wia_item.Transfer()
        except Exception as e:
            logger.warning("Transfer() failed: %s, trying ShowAcquireImage", e)
            return scan_with_dialog(device_id)
        if not result:
            return None
        if isinstance(result, str):
            qimg = QImage(result)
            if not qimg.isNull():
                return qimg
        qimg = _try_read_image_file(result)
        if qimg:
            return qimg
        logger.error("scan_direct: all methods failed")
        return None
    except Exception as e:
        logger.error("scan_direct error: %s", e)
        return None
