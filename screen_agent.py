"""Screen understanding agent for Glass Nova.

Captures and analyzes the Windows screen or active window using Gemini vision,
providing UI element identification, error diagnostics, and content explanation.
All operations check permission_manager first.
"""
import os, io, time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from PIL import Image, ImageGrab, ImageChops
from dotenv import load_dotenv
from google import genai
from google.genai import types

from permissions import permissions

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
API_KEY = os.getenv("GEMINI_API_KEY")
VISION_MODEL = os.getenv("VISION_MODEL", "gemini-3.5-flash")

_client = None
_working_vision_model = None

def get_vision_models() -> list:
    """Return ordered candidate vision models, prioritizing any model already verified to work."""
    candidates = []
    if _working_vision_model:
        candidates.append(_working_vision_model)
    env_model = os.getenv("VISION_MODEL", "").strip()
    for m in (
        env_model,
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.8-flash",
        "gemini-3.1-flash-lite",
    ):
        if m and m not in candidates:
            candidates.append(m)
    return candidates

def remember_working_vision_model(model_name: str):
    global _working_vision_model
    if model_name:
        _working_vision_model = model_name

def get_client() -> genai.Client:
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY is missing from .env")
        _client = genai.Client(api_key=key)
    return _client

_dpi_initialized = False


def ensure_dpi_awareness() -> None:
    """Ensure the process is DPI-aware so Win32, PIL, UIA, and PyAutoGUI share physical pixels."""
    global _dpi_initialized
    if _dpi_initialized:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        try:
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (-4)
            if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                _dpi_initialized = True
                return
        except Exception:
            pass
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            _dpi_initialized = True
            return
        except Exception:
            pass
        try:
            user32.SetProcessDPIAware()
            _dpi_initialized = True
        except Exception:
            pass
    except Exception:
        pass


def get_screen_metrics(prefer_active_monitor: bool = False) -> Dict[str, Any]:
    """Return physical primary or active-monitor screen dimensions, origin, and DPI scale factor."""
    ensure_dpi_awareness()
    w, h = 1920, 1080
    ox, oy = 0, 0
    dpi_scale = 1.0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        pw = int(user32.GetSystemMetrics(0))
        ph = int(user32.GetSystemMetrics(1))
        if pw > 0 and ph > 0:
            w, h = pw, ph
        if prefer_active_monitor:
            try:
                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD),
                    ]
                hwnd = user32.GetForegroundWindow()
                # MONITOR_DEFAULTTONEAREST = 2
                hmon = user32.MonitorFromWindow(hwnd, 2) if hwnd else 0
                if not hmon:
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    hmon = user32.MonitorFromPoint(pt, 2)
                if hmon:
                    mi = MONITORINFO()
                    mi.cbSize = ctypes.sizeof(MONITORINFO)
                    if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                        mw = int(mi.rcMonitor.right - mi.rcMonitor.left)
                        mh = int(mi.rcMonitor.bottom - mi.rcMonitor.top)
                        if mw > 0 and mh > 0:
                            ox = int(mi.rcMonitor.left)
                            oy = int(mi.rcMonitor.top)
                            w, h = mw, mh
            except Exception:
                pass
        try:
            dpi = int(user32.GetDpiForSystem())
            if dpi > 0:
                dpi_scale = round(dpi / 96.0, 3)
        except Exception:
            pass
    except Exception:
        try:
            import pyautogui
            pw, ph = pyautogui.size()
            if pw > 0 and ph > 0:
                w, h = int(pw), int(ph)
        except Exception:
            pass
    return {
        "width": w,
        "height": h,
        "origin_x": ox,
        "origin_y": oy,
        "dpi_scale": dpi_scale,
    }


def normalized_to_screen_coords(
    norm_y: float,
    norm_x: float,
    meta: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int]:
    """Convert normalized [0..1000] (y, x) image coordinates to physical screen (x, y) pixels."""
    if meta and meta.get("screen_width") and meta.get("screen_height"):
        sw = int(meta["screen_width"])
        sh = int(meta["screen_height"])
        ox = int(meta.get("origin_x", 0))
        oy = int(meta.get("origin_y", 0))
    else:
        m = get_screen_metrics()
        sw, sh, ox, oy = m["width"], m["height"], m["origin_x"], m["origin_y"]

    nx = max(0.0, min(1000.0, float(norm_x))) / 1000.0
    ny = max(0.0, min(1000.0, float(norm_y))) / 1000.0
    px = ox + int(round(nx * max(1, sw - 1)))
    py = oy + int(round(ny * max(1, sh - 1)))
    px = max(ox, min(ox + sw - 1, px))
    py = max(oy, min(oy + sh - 1, py))
    return px, py


def image_pixels_to_screen_coords(
    img_x: float,
    img_y: float,
    meta: Dict[str, Any],
) -> Tuple[int, int]:
    """Convert pixel coordinates inside a resized screenshot to physical screen (x, y) pixels."""
    sw = int(meta.get("screen_width") or get_screen_metrics()["width"])
    sh = int(meta.get("screen_height") or get_screen_metrics()["height"])
    iw = max(1, int(meta.get("image_width") or sw))
    ih = max(1, int(meta.get("image_height") or sh))
    ox = int(meta.get("origin_x", 0))
    oy = int(meta.get("origin_y", 0))

    px = ox + int(round(float(img_x) * (sw / float(iw))))
    py = oy + int(round(float(img_y) * (sh / float(ih))))
    px = max(ox, min(ox + sw - 1, px))
    py = max(oy, min(oy + sh - 1, py))
    return px, py


def get_active_window_title() -> str:
    """Returns the title of the currently focused window on Windows."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        return buff.value or "Desktop"
    except Exception:
        return "Desktop"

def _resize_and_encode_jpeg(img: Image.Image, max_dimension: int, quality: int) -> Tuple[bytes, int, int]:
    """Resize preserving aspect ratio and encode to JPEG, returning (bytes, width, height)."""
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_dimension:
        scale = max_dimension / float(max(w, h))
        img = img.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue(), img.width, img.height


def capture_screen_with_metadata(max_dimension: int = 1600, quality: int = 80, prefer_active_monitor: bool = True) -> Dict[str, Any]:
    """Capture the current desktop (or active monitor on multi-monitor setups) and return JPEG bytes plus physical/image coordinate metadata."""
    metrics = get_screen_metrics(prefer_active_monitor=prefer_active_monitor)
    sw, sh = metrics["width"], metrics["height"]
    ox, oy = metrics["origin_x"], metrics["origin_y"]
    active_window = get_active_window_title()
    ts = time.monotonic()

    def _pack(data: bytes, iw: int, ih: int, method: str) -> Dict[str, Any]:
        return {
            "bytes": data,
            "screen_width": sw,
            "screen_height": sh,
            "image_width": iw,
            "image_height": ih,
            "origin_x": ox,
            "origin_y": oy,
            "dpi_scale": metrics["dpi_scale"],
            "active_window": active_window,
            "timestamp": ts,
            "method": method,
        }

    # Method 1: Windows GDI BitBlt (direct Win32 desktop DC grab, reliable across sessions & multi-monitor aware)
    try:
        import ctypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        # Ensure the calling thread is attached to the interactive desktop station
        hdesk = None
        try:
            hdesk = user32.OpenDesktopW("Default", 0, False, 0x000000FF | 0x00000001 | 0x00000002)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
        except Exception:
            pass

        w = sw if sw > 0 else user32.GetSystemMetrics(0)
        h = sh if sh > 0 else user32.GetSystemMetrics(1)
        if w > 0 and h > 0:
            hdc = user32.GetDC(0)
            if hdc:
                try:
                    mdc = gdi32.CreateCompatibleDC(hdc)
                    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
                    gdi32.SelectObject(mdc, bmp)
                    gdi32.BitBlt(mdc, 0, 0, w, h, hdc, ox, oy, 0x00CC0020 | 0x40000000)

                    class BITMAPINFOHEADER(ctypes.Structure):
                        _fields_ = [
                            ('biSize', ctypes.c_uint32),
                            ('biWidth', ctypes.c_int32),
                            ('biHeight', ctypes.c_int32),
                            ('biPlanes', ctypes.c_uint16),
                            ('biBitCount', ctypes.c_uint16),
                            ('biCompression', ctypes.c_uint32),
                            ('biSizeImage', ctypes.c_uint32),
                            ('biXPelsPerMeter', ctypes.c_int32),
                            ('biYPelsPerMeter', ctypes.c_int32),
                            ('biClrUsed', ctypes.c_uint32),
                            ('biClrImportant', ctypes.c_uint32),
                        ]

                    bmi = BITMAPINFOHEADER()
                    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                    bmi.biWidth = w
                    bmi.biHeight = -h
                    bmi.biPlanes = 1
                    bmi.biBitCount = 32
                    bmi.biCompression = 0

                    buf = ctypes.create_string_buffer(w * h * 4)
                    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
                    gdi32.DeleteObject(bmp)
                    gdi32.DeleteDC(mdc)

                    img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
                    colors = img.getcolors(maxcolors=2)
                    # If GDI returned a blank/black screen due to non-interactive station, fall through
                    if not (colors and len(colors) == 1):
                        data, iw, ih = _resize_and_encode_jpeg(img, max_dimension, quality)
                        if data and len(data) > 1000:
                            sw, sh = w, h
                            return _pack(data, iw, ih, "gdi")
                finally:
                    user32.ReleaseDC(0, hdc)
                    if hdesk:
                        try:
                            user32.CloseDesktop(hdesk)
                        except Exception:
                            pass
    except Exception:
        pass

    # Method 2: PIL ImageGrab fallback (primary screen to match coordinate space)
    try:
        img = ImageGrab.grab(all_screens=False)
        sw, sh = img.size
        data, iw, ih = _resize_and_encode_jpeg(img, max_dimension, quality)
        return _pack(data, iw, ih, "imagegrab")
    except Exception:
        pass

    # Method 3: Qt screen grab (if running inside interactive Qt GUI)
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import QBuffer, QIODevice, Qt
        screen = QGuiApplication.primaryScreen()
        if screen:
            pix = screen.grabWindow(0)
            if not pix.isNull():
                qimg = pix.toImage()
                iw, ih = qimg.width(), qimg.height()
                if max(iw, ih) > max_dimension:
                    qimg = qimg.scaled(max_dimension, max_dimension, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    iw, ih = qimg.width(), qimg.height()
                buf = QBuffer()
                buf.open(QIODevice.WriteOnly)
                qimg.save(buf, 'JPEG', quality)
                data = bytes(buf.data())
                buf.close()
                if data:
                    return _pack(data, iw, ih, "qt")
    except Exception:
        pass

    # Method 4: Fallback image
    img = Image.new("RGB", (1280, 720), color=(20, 20, 30))
    data, iw, ih = _resize_and_encode_jpeg(img, max_dimension, quality)
    return _pack(data, iw, ih, "fallback")


def capture_screen_jpeg(max_dimension: int = 1600, quality: int = 80) -> bytes:
    """Captures the current desktop and compresses to JPEG bytes."""
    return capture_screen_with_metadata(max_dimension=max_dimension, quality=quality)["bytes"]


def capture_region_jpeg(rect_or_box, max_dimension: int = 1400, quality: int = 85) -> bytes:
    """Capture only a user-selected screen rectangle (QRect or (x, y, w, h) tuple) as JPEG bytes.
    Respects multi-monitor virtual desktop coordinates and Windows DPI scaling."""
    ensure_dpi_awareness()
    if hasattr(rect_or_box, "x") and hasattr(rect_or_box, "width"):
        rx = int(rect_or_box.x())
        ry = int(rect_or_box.y())
        rw = max(1, int(rect_or_box.width()))
        rh = max(1, int(rect_or_box.height()))
    elif isinstance(rect_or_box, dict):
        rx = int(rect_or_box.get("x", 0))
        ry = int(rect_or_box.get("y", 0))
        rw = max(1, int(rect_or_box.get("width", 100)))
        rh = max(1, int(rect_or_box.get("height", 100)))
    else:
        rx, ry, rw, rh = [int(v) for v in rect_or_box[:4]]
        rw, rh = max(1, rw), max(1, rh)

    # Method 1: Win32 GDI BitBlt from virtual desktop coordinates
    try:
        import ctypes
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        if hdc:
            try:
                mdc = gdi32.CreateCompatibleDC(hdc)
                bmp = gdi32.CreateCompatibleBitmap(hdc, rw, rh)
                gdi32.SelectObject(mdc, bmp)
                gdi32.BitBlt(mdc, 0, 0, rw, rh, hdc, rx, ry, 0x00CC0020 | 0x40000000)

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", ctypes.c_uint32),
                        ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32),
                        ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16),
                        ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32),
                        ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32),
                        ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32),
                    ]

                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = rw
                bmi.biHeight = -rh
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0

                buf = ctypes.create_string_buffer(rw * rh * 4)
                gdi32.GetDIBits(mdc, bmp, 0, rh, buf, ctypes.byref(bmi), 0)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mdc)

                img = Image.frombuffer("RGBA", (rw, rh), buf, "raw", "BGRA", 0, 1).convert("RGB")
                colors = img.getcolors(maxcolors=2)
                if not (colors and len(colors) == 1):
                    data, _, _ = _resize_and_encode_jpeg(img, max_dimension, quality)
                    if data:
                        return data
            finally:
                user32.ReleaseDC(0, hdc)
    except Exception:
        pass

    # Method 2: PIL ImageGrab bbox
    try:
        img = ImageGrab.grab(bbox=(rx, ry, rx + rw, ry + rh), all_screens=True)
        data, _, _ = _resize_and_encode_jpeg(img, max_dimension, quality)
        if data:
            return data
    except Exception:
        pass

    # Method 3: Crop from full capture
    full = capture_screen_with_metadata(max_dimension=2400, quality=quality)
    im = Image.open(io.BytesIO(full["bytes"])).convert("RGB")
    sw = max(1, int(full.get("screen_width", im.width)))
    sh = max(1, int(full.get("screen_height", im.height)))
    sx = im.width / float(sw)
    sy = im.height / float(sh)
    left = max(0, min(im.width - 1, int(round(rx * sx))))
    top = max(0, min(im.height - 1, int(round(ry * sy))))
    right = max(left + 1, min(im.width, int(round((rx + rw) * sx))))
    bottom = max(top + 1, min(im.height, int(round((ry + rh) * sy))))
    cropped = im.crop((left, top, right, bottom))
    data, _, _ = _resize_and_encode_jpeg(cropped, max_dimension, quality)
    return data


def analyze_region_bytes(
    image_bytes: bytes,
    question: str = "Explain this selected screen region.",
    rect=None,
    check_permission: bool = True,
) -> str:
    """Send user-selected region JPEG bytes and question to the configured Gemini vision model.
    Guidance only — never clicks or types, and does not log password or sensitive content."""
    if check_permission and not permissions.request_permission("screen_view", f"Analyze selected screen region: '{question}'"):
        raise PermissionError("Screen viewing permission was denied by the user.")
    if not image_bytes:
        raise ValueError("Selected region image is empty.")
    active_window = get_active_window_title()
    rect_desc = ""
    if rect is not None:
        if hasattr(rect, "x") and hasattr(rect, "width"):
            rect_desc = f" (Selected region: x={rect.x()}, y={rect.y()}, w={rect.width()}, h={rect.height()})"
        elif isinstance(rect, (tuple, list)) and len(rect) >= 4:
            rect_desc = f" (Selected region: x={rect[0]}, y={rect[1]}, w={rect[2]}, h={rect[3]})"
    q = (question or "Explain what is inside this selected screen region and how to use it.").strip()
    prompt = (
        f"{q}{rect_desc}\n"
        "Provide clear, concise visual guidance about this selected region only. "
        "Do not reveal or transcribe passwords, tokens, or payment secrets."
    )
    client = get_client()
    return generate_screen_vision(client, image_bytes, prompt, active_window)


def compute_visual_change(
    before_bytes: bytes,
    after_bytes: bytes,
    focus_point: Optional[Tuple[int, int]] = None,
    meta: Optional[Dict[str, Any]] = None,
    radius_px: int = 120,
) -> Dict[str, Any]:
    """Measure global and click-region visual change between two JPEG screenshots."""
    try:
        im1 = Image.open(io.BytesIO(before_bytes)).convert("RGB")
        im2 = Image.open(io.BytesIO(after_bytes)).convert("RGB")
        if im1.size != im2.size:
            im2 = im2.resize(im1.size, Image.Resampling.BILINEAR)
        diff = ImageChops.difference(im1, im2).convert("L")
        hist = diff.histogram()
        total_px = max(1, im1.width * im1.height)
        changed_px = sum(hist[18:])
        global_mean = sum(i * c for i, c in enumerate(hist)) / float(total_px)
        global_ratio = changed_px / float(total_px)

        local_mean = global_mean
        local_ratio = global_ratio
        if focus_point is not None and meta:
            sw = max(1, int(meta.get("screen_width", im1.width)))
            sh = max(1, int(meta.get("screen_height", im1.height)))
            ox = int(meta.get("origin_x", 0))
            oy = int(meta.get("origin_y", 0))
            ix = int(round((focus_point[0] - ox) * (im1.width / float(sw))))
            iy = int(round((focus_point[1] - oy) * (im1.height / float(sh))))
            r = max(24, int(round(radius_px * (im1.width / float(sw)))))
            box = (max(0, ix - r), max(0, iy - r), min(im1.width, ix + r), min(im1.height, iy + r))
            if box[2] > box[0] and box[3] > box[1]:
                crop = diff.crop(box)
                chist = crop.histogram()
                ctot = max(1, (box[2] - box[0]) * (box[3] - box[1]))
                local_mean = sum(i * c for i, c in enumerate(chist)) / float(ctot)
                local_ratio = sum(chist[18:]) / float(ctot)

        return {
            "global_mean": round(global_mean, 2),
            "global_changed_ratio": round(global_ratio, 4),
            "local_mean": round(local_mean, 2),
            "local_changed_ratio": round(local_ratio, 4),
            "changed": (local_ratio >= 0.015) or (global_ratio >= 0.01) or (local_mean >= 3.0),
        }
    except Exception:
        return {"global_mean": 0.0, "global_changed_ratio": 0.0, "local_mean": 0.0, "local_changed_ratio": 0.0, "changed": False}

def generate_screen_vision(client, image_bytes: bytes, query: str, active_window: str) -> str:
    """Queries Gemini vision with automatic fallback across models if quota is exhausted."""
    models = get_vision_models()
    system_instruction = (
        "You are Nova's visual screen perception engine. You analyze Windows 11 desktop screenshots. "
        f"The currently focused window is: '{active_window}'. "
        "Examine the screen carefully. Identify visible applications, windows, buttons, menus, icons, "
        "browser tabs, video player controls (such as Play, Pause, Skip Ads), error dialogs, code, and text fields. "
        "Answer the user's question directly, accurately, and conversationally. If asked where to click or what "
        "is visible, be specific about visual labels and locations."
    )
    last_err = None
    for model_name in models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    types.Part.from_text(text=f"User question: {query}\nFocused window: {active_window}")
                ],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.2,
                    max_output_tokens=900
                )
            )
            remember_working_vision_model(model_name)
            return (response.text or "").strip()
        except Exception as e:
            last_err = e
            continue
    raise last_err or RuntimeError("Screen analysis failed across all models.")


def query_vision_json(
    contents: list,
    system_instruction: str,
    temperature: float = 0.1,
    max_output_tokens: int = 450,
) -> Optional[Dict[str, Any]]:
    """Run a Gemini Vision request expecting a JSON object response and parse it safely."""
    import json, re
    client = get_client()
    models = get_vision_models()
    for model_name in models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                ),
            )
            remember_working_vision_model(model_name)
            raw = (response.text or "").strip()
            if not raw:
                continue
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
            m = re.search(r"\{[\s\S]*\}", cleaned)
            if m:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    return parsed
        except Exception:
            continue
    return None

def analyze_screen(query: str = "Explain what is on the screen and identify key UI elements or errors.",
                   continuous: bool = False) -> Dict[str, Any]:
    """
    Main screen observation entrypoint.
    Checks permissions, captures the screen, and sends to Gemini vision.
    """
    category = "continuous_screen" if continuous else "screen_view"
    action_desc = f"Inspect active screen: '{query}'"

    if not permissions.request_permission(category, action_desc):
        return {
            "ok": False,
            "message": "Screen access permission was denied by the user.",
            "permission_denied": True
        }

    try:
        active_window = get_active_window_title()
        image_bytes = capture_screen_jpeg()
        client = get_client()

        answer = generate_screen_vision(client, image_bytes, query, active_window)
        if not answer:
            answer = "I looked at your screen, but could not discern any specific text or UI details."

        return {
            "ok": True,
            "message": answer,
            "active_window": active_window,
            "query": query
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Screen analysis failed: {e}"
        }
