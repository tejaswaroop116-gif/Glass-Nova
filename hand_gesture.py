"""
Glass Nova — Hand Gesture Mouse Controller (Windows 10/11)

Upgraded from hand_control_v3.py with:
- Multi-backend webcam auto-detection (DirectShow, MSMF, Any) + frame validation
- Resilient chunked/resumable download for hand_landmarker.task
- Anti-drift knuckle-stabilized cursor tracking (pinching thumb + index no longer pulls cursor off target)
- Forgiving pointing & fist clutch detection so the cursor never freezes mid-movement
- Thread-safe HandGestureManager for seamless voice & UI control inside Glass Nova
- Built-in OpenCV fallback hand tracker if MediaPipe is unavailable
"""

import ctypes
from ctypes import wintypes
import math
import os
import sys
import threading
import time
import urllib.request
import zipfile
from collections import deque
from pathlib import Path
import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

mp = None
mp_python = None
vision = None
HAS_MEDIAPIPE_TASKS = False

# =====================================================================
#  SETTINGS
# =====================================================================
CAMERA_INDEX = 0
FRAME_W, FRAME_H = 640, 480

# Area of the camera image that maps to the whole screen (fractions 0..1)
ACTIVE_X = (0.12, 0.88)
ACTIVE_Y = (0.12, 0.82)

# Cursor smoothing: slow movement = heavy smoothing, fast movement = light
MIN_ALPHA = 0.16
MAX_ALPHA = 0.86
FAST_PX = 280          # pixel jump at which smoothing is lightest
DEADZONE_PX = 2.5      # ignore tiny sub-pixel jitter

# Finger-state detection
EXT_RATIO = 1.04       # tip farther from wrist than PIP joint (more forgiving for tilted hands)
FIST_MAX = 1.30        # all 4 fingertips closer than this (x palm size) to wrist => fist

# Pinch thresholds (distance / palm size). Hysteresis: ON to start, OFF to end
PINCH_ON = 0.32
PINCH_OFF = 0.45
PINCH_PRE_FREEZE = 0.38  # freeze cursor slightly before pinch closes to prevent click drift
PINCH_MARGIN = 0.05      # winner must beat other pinches by this margin
DRAG_HOLD_S = 0.36       # hold a left pinch this long to start dragging
LOCK_LOOKBACK_S = 0.14   # subtle lookback anchor on pinch press
RIGHT_COOLDOWN_S = 0.45
DOUBLE_COOLDOWN_S = 0.75

# Scroll
SCROLL_OPEN = 0.40       # thumb distance from index & middle tips during 2-finger scroll
SCROLL_ENTER_S = 0.10    # pose held this long before scrolling starts
SCROLL_DEADZONE = 0.0025
SCROLL_GAIN = 3800
SCROLL_MIN_SEND = 15
SCROLL_MAX_SEND = 480

# Zoom
ZOOM_ENTER_S = 0.28
ZOOM_STEP = 0.42
ZOOM_COOLDOWN_S = 0.22
ZOOM_GRACE_S = 0.25

LOST_GRACE_S = 0.25

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
MODEL_PATH = os.path.join(APP_DIR, "hand_landmarker.task")

# =====================================================================
#  WINDOWS MOUSE CONTROL
# =====================================================================
user32 = ctypes.windll.user32 if sys.platform == "win32" else None

if user32 is not None:
    user32.mouse_event.argtypes = [
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, ctypes.c_size_t
    ]
    user32.keybd_event.argtypes = [
        wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t
    ]
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

LEFT_DOWN, LEFT_UP = 0x0002, 0x0004
RIGHT_DOWN, RIGHT_UP = 0x0008, 0x0010
WHEEL = 0x0800
VK_CONTROL = 0x11
KEYEVENTF_KEYUP = 0x0002


def get_screen_size():
    if user32 is not None:
        w = int(user32.GetSystemMetrics(0))
        h = int(user32.GetSystemMetrics(1))
        if w > 0 and h > 0:
            return w, h
    return 1920, 1080


def set_cursor(x, y):
    if user32 is None:
        return
    sw, sh = get_screen_size()
    x = int(max(0, min(sw - 1, x)))
    y = int(max(0, min(sh - 1, y)))
    user32.SetCursorPos(x, y)


def left_down():
    if user32 is not None:
        user32.mouse_event(LEFT_DOWN, 0, 0, 0, 0)


def left_up():
    if user32 is not None:
        user32.mouse_event(LEFT_UP, 0, 0, 0, 0)


def left_click():
    left_down()
    left_up()


def double_click():
    left_click()
    time.sleep(0.04)
    left_click()


def right_click():
    if user32 is not None:
        user32.mouse_event(RIGHT_DOWN, 0, 0, 0, 0)
        user32.mouse_event(RIGHT_UP, 0, 0, 0, 0)


def scroll(amount):
    if user32 is not None:
        user32.mouse_event(WHEEL, 0, 0, int(amount) & 0xFFFFFFFF, 0)


def zoom(direction):
    """Ctrl + mouse wheel. direction: +1 zoom in, -1 zoom out."""
    if user32 is None:
        return
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    try:
        scroll(direction * 120)
    finally:
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


# =====================================================================
#  HELPERS & MODEL DOWNLOAD
# =====================================================================
def ensure_model(model_path: str = MODEL_PATH, retries: int = 5) -> bool:
    """Ensure hand_landmarker.task exists and is complete (~7.5 MB), supporting resumable download."""
    if os.path.exists(model_path) and os.path.getsize(model_path) > 7_000_000:
        return True

    part_path = model_path + ".part"
    for attempt in range(1, retries + 1):
        try:
            existing_bytes = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Mozilla/5.0"})
            if existing_bytes > 0:
                req.add_header("Range", f"bytes={existing_bytes}-")
            with urllib.request.urlopen(req, timeout=25) as resp:
                mode = "ab" if (existing_bytes > 0 and resp.status == 206) else "wb"
                with open(part_path, mode) as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
            if os.path.exists(part_path) and os.path.getsize(part_path) > 7_000_000:
                os.replace(part_path, model_path)
                return True
        except Exception as e:
            print(f"[HandGesture] Model download attempt {attempt}/{retries} failed: {e}", flush=True)
            time.sleep(0.8)
    return os.path.exists(model_path) and os.path.getsize(model_path) > 7_000_000


class SimpleLandmark:
    """Lightweight landmark with normalized (x, y, z) coordinates."""
    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float = 0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)


def dist(a, b, w, h):
    """Distance in PIXELS (so the 4:3 camera image does not distort results)."""
    return math.hypot((a.x - b.x) * w, (a.y - b.y) * h)


def finger_up(hand, tip, pip, w, h):
    return dist(hand[0], hand[tip], w, h) > dist(hand[0], hand[pip], w, h) * EXT_RATIO


def map_to_screen(x_norm: float, y_norm: float):
    sw, sh = get_screen_size()
    nx = (x_norm - ACTIVE_X[0]) / (ACTIVE_X[1] - ACTIVE_X[0])
    ny = (y_norm - ACTIVE_Y[0]) / (ACTIVE_Y[1] - ACTIVE_Y[0])
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    return nx * (sw - 1), ny * (sh - 1)


HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


def draw_hand(frame, hand):
    if cv2 is None:
        return
    h, w = frame.shape[:2]
    for s, e in HAND_CONNECTIONS:
        p1 = (int(hand[s].x * w), int(hand[s].y * h))
        p2 = (int(hand[e].x * w), int(hand[e].y * h))
        cv2.line(frame, p1, p2, (255, 200, 40), 2, cv2.LINE_AA)
    for idx, pt in enumerate(hand):
        color = (0, 255, 120) if idx in (4, 8, 12, 16, 20) else (255, 255, 255)
        radius = 5 if idx in (4, 8) else 3
        cv2.circle(frame, (int(pt.x * w), int(pt.y * h)), radius, color, -1, cv2.LINE_AA)


# =====================================================================
#  OPENCV DNN 21-LANDMARK TFLITE TRACKER (Bypass for WinError 4551)
# =====================================================================
def _generate_palm_anchors() -> np.ndarray:
    """Generate the 2016 MediaPipe SSD palm detection anchors for 192x192 input."""
    anchors = []
    # Layer 0: 24x24 grid, 2 anchors per cell (1152)
    for y in range(24):
        for x in range(24):
            cx = (x + 0.5) / 24.0
            cy = (y + 0.5) / 24.0
            anchors.append((cx, cy))
            anchors.append((cx, cy))
    # Layers 1..3: 12x12 grid, 6 anchors per cell (864)
    for y in range(12):
        for x in range(12):
            cx = (x + 0.5) / 12.0
            cy = (y + 0.5) / 12.0
            for _ in range(6):
                anchors.append((cx, cy))
    return np.array(anchors, dtype=np.float32)


class OpenCVDnnHandLandmarker:
    """
    Runs the official MediaPipe hand_detector.tflite + hand_landmarks_detector.tflite
    models natively inside OpenCV DNN (cv2.dnn.readNetFromTFLite).
    Works even when Windows 11 Smart App Control blocks libmediapipe.dll (WinError 4551).
    """

    def __init__(self, task_path: str = MODEL_PATH):
        import tempfile
        cache_dir = Path(task_path).resolve().parent / ".hand_dnn_cache"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            cache_dir = Path(tempfile.gettempdir()) / "nova_hand_dnn_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
        det_path = cache_dir / "hand_detector.tflite"
        lm_path = cache_dir / "hand_landmarks_screen.tflite"

        if not det_path.exists() or not lm_path.exists():
            with zipfile.ZipFile(task_path, "r") as z:
                det_path.write_bytes(z.read("hand_detector.tflite"))
                raw_lm = bytearray(z.read("hand_landmarks_detector.tflite"))
                # Swap conv_landmarks and conv_world_landmarks buffer indices so default output is 21x3 screen coords (0..224)
                p_w_world = raw_lm.find(b"conv_world_landmarks/MatMul\x00") - 34
                p_w_screen = raw_lm.find(b"conv_landmarks/MatMul\x00") - 34
                p_b_screen = raw_lm.find(b"conv_landmarks/BiasAdd/ReadVariableOp/resource\x00") - 34
                p_b_world = raw_lm.find(b"conv_world_landmarks/BiasAdd/ReadVariableOp/resource\x00") - 34
                if min(p_w_world, p_w_screen, p_b_screen, p_b_world) > 0:
                    raw_lm[p_w_world], raw_lm[p_w_screen] = raw_lm[p_w_screen], raw_lm[p_w_world]
                    raw_lm[p_b_world], raw_lm[p_b_screen] = raw_lm[p_b_screen], raw_lm[p_b_world]
                lm_path.write_bytes(raw_lm)

        self.det_net = cv2.dnn.readNetFromTFLite(str(det_path))
        self.lm_net = cv2.dnn.readNetFromTFLite(str(lm_path))
        self.anchors = _generate_palm_anchors()
        self.prev_rois = []  # list of (cx_norm, cy_norm, size_norm)
        self.frame_idx = 0

    def _detect_palms(self, frame_rgb: np.ndarray):
        inp = cv2.resize(frame_rgb, (192, 192), interpolation=cv2.INTER_LINEAR)
        blob = inp.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
        self.det_net.setInput(blob)
        logits = self.det_net.forward("Identity_1")[0, :, 0]
        raw_boxes = self.det_net.forward("Identity")[0]

        mask = logits > 0.0  # sigmoid > 0.50
        if not np.any(mask):
            return []

        scores = 1.0 / (1.0 + np.exp(-np.clip(logits[mask], -20.0, 20.0)))
        boxes = raw_boxes[mask]
        anc = self.anchors[mask]

        cx = anc[:, 0] + boxes[:, 0] / 192.0
        cy = anc[:, 1] + boxes[:, 1] / 192.0
        bw = boxes[:, 2] / 192.0
        bh = boxes[:, 3] / 192.0
        # Shift box center slightly toward fingers (keypoint 0 = wrist, keypoint 2 = middle MCP)
        kp0_x = anc[:, 0] + boxes[:, 4] / 192.0
        kp0_y = anc[:, 1] + boxes[:, 5] / 192.0
        kp2_x = anc[:, 0] + boxes[:, 8] / 192.0
        kp2_y = anc[:, 1] + boxes[:, 9] / 192.0
        vx = kp2_x - kp0_x
        vy = kp2_y - kp0_y
        cx = cx + vx * 0.50
        cy = cy + vy * 0.50
        size = np.maximum(bw, bh) * 2.6

        order = np.argsort(-scores)
        selected = []
        for idx in order:
            cxi, cyi, si = float(cx[idx]), float(cy[idx]), float(size[idx])
            if any(math.hypot(cxi - sx, cyi - sy) < max(si, ss) * 0.45 for sx, sy, ss in selected):
                continue
            selected.append((cxi, cyi, max(si, 0.18)))
            if len(selected) >= 2:
                break
        return selected

    def detect(self, frame_bgr: np.ndarray):
        self.frame_idx += 1
        h, w = frame_bgr.shape[:2]
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        rois = self.prev_rois
        if not rois or (self.frame_idx % 15 == 0 and len(rois) < 2):
            detected = self._detect_palms(frame_rgb)
            if detected:
                rois = detected

        hands_out = []
        next_rois = []
        for cx_n, cy_n, size_n in rois[:2]:
            half_px = max(32.0, size_n * max(w, h) * 0.5)
            cx_px = cx_n * w
            cy_px = cy_n * h
            x1 = int(round(cx_px - half_px))
            y1 = int(round(cy_px - half_px))
            x2 = int(round(cx_px + half_px))
            y2 = int(round(cy_px + half_px))
            crop_w = max(1, x2 - x1)
            crop_h = max(1, y2 - y1)

            # Pad out-of-bounds cleanly
            pad_l = max(0, -x1)
            pad_t = max(0, -y1)
            pad_r = max(0, x2 - w)
            pad_b = max(0, y2 - h)
            sub = frame_rgb[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
            if sub.size == 0:
                continue
            if pad_l or pad_t or pad_r or pad_b:
                sub = cv2.copyMakeBorder(sub, pad_t, pad_b, pad_l, pad_r, cv2.BORDER_REPLICATE)

            inp224 = cv2.resize(sub, (224, 224), interpolation=cv2.INTER_LINEAR)
            blob = inp224.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
            self.lm_net.setInput(blob)
            presence = float(self.lm_net.forward("Identity_1")[0, 0])
            if presence < 0.45:
                continue

            pts = self.lm_net.forward().reshape(21, 3)
            lms = []
            xs, ys = [], []
            for px, py, pz in pts:
                gx = (x1 + (float(px) / 224.0) * crop_w) / w
                gy = (y1 + (float(py) / 224.0) * crop_h) / h
                gz = float(pz) / 224.0
                gx = max(0.0, min(1.0, gx))
                gy = max(0.0, min(1.0, gy))
                lms.append(SimpleLandmark(gx, gy, gz))
                xs.append(gx)
                ys.append(gy)
            hands_out.append(lms)

            # Compute next frame ROI directly from the 21 landmarks
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            ncx = (min_x + max_x) * 0.5
            ncy = (min_y + max_y) * 0.5
            nsize = max(max_x - min_x, max_y - min_y) * 1.55
            next_rois.append((ncx, ncy, max(0.18, min(1.0, nsize))))

        self.prev_rois = next_rois
        return hands_out

    def close(self):
        self.prev_rois.clear()


class OpenCVFallbackHandTracker:
    """Fallback skin-segmentation + convex-hull hand landmark synthesizer if model file is missing."""

    def detect(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        ycrcb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, np.array([0, 133, 77], dtype=np.uint8), np.array([255, 173, 127], dtype=np.uint8))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return []
        cnt = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(cnt)
        if area < (w * h * 0.018):
            return []
        M = cv2.moments(cnt)
        if M["m00"] == 0:
            return []
        cx = M["m10"] / M["m00"]
        cy = M["m01"] / M["m00"]
        topmost = tuple(cnt[cnt[:, :, 1].argmin()][0])
        leftmost = tuple(cnt[cnt[:, :, 0].argmin()][0])

        wrist_x, wrist_y = cx / w, min(0.98, (cy + math.sqrt(area) * 0.45) / h)
        palm_x, palm_y = cx / w, cy / h
        tip_x, tip_y = topmost[0] / w, topmost[1] / h
        thumb_x, thumb_y = leftmost[0] / w, leftmost[1] / h

        lms = [SimpleLandmark(palm_x, palm_y) for _ in range(21)]
        lms[0] = SimpleLandmark(wrist_x, wrist_y)
        lms[4] = SimpleLandmark(thumb_x, thumb_y)
        lms[5] = SimpleLandmark(palm_x, (palm_y + tip_y) * 0.5)
        lms[6] = SimpleLandmark((palm_x + tip_x) * 0.5, (palm_y + tip_y) * 0.5)
        lms[8] = SimpleLandmark(tip_x, tip_y)
        lms[9] = SimpleLandmark(palm_x, palm_y)
        return [lms]


def create_best_hand_tracker(model_path: str = MODEL_PATH):
    """
    Create the 21-landmark hand tracker using OpenCVDnnHandLandmarker (which runs the
    official MediaPipe hand_detector.tflite + hand_landmarks_detector.tflite models
    natively inside signed OpenCV DNN without triggering Windows Smart App Control
    popups on unsigned libmediapipe.dll).
    Returns (mode, tracker_instance).
    """
    has_model = ensure_model(model_path)
    if cv2 is not None and has_model:
        try:
            return ("opencv_dnn", OpenCVDnnHandLandmarker(model_path))
        except Exception as e:
            print(f"[HandGesture] OpenCV DNN init error ({e}); using fallback tracker.", flush=True)

    return ("fallback", OpenCVFallbackHandTracker())


# =====================================================================
#  GESTURE CONTROLLER
# =====================================================================
class GestureMouse:
    def __init__(self):
        sw, sh = get_screen_size()
        self.cx = sw / 2
        self.cy = sh / 2
        self.history = deque(maxlen=90)      # (time, x, y) of recent cursor positions

        self.left_state = "idle"             # idle | pending | dragging
        self.pinch = None                    # None | left | right | double
        self.pinch_start = 0.0
        self.right_cd = 0.0
        self.double_cd = 0.0

        self.scroll_since = None
        self.scroll_prev = None
        self.scroll_acc = 0.0

        self.zoom_since = None
        self.zoom_ref = None
        self.zoom_active = False
        self.zoom_cd = 0.0
        self.last_two = 0.0

        self.last_seen = 0.0
        self.status = "NO HAND"

    # ---------- state helpers ----------
    def scroll_reset(self):
        self.scroll_since = None
        self.scroll_prev = None
        self.scroll_acc = 0.0

    def zoom_reset(self):
        self.zoom_since = None
        self.zoom_ref = None
        self.zoom_active = False

    def release_all(self):
        if self.left_state == "dragging":
            left_up()
        self.left_state = "idle"
        self.pinch = None
        self.scroll_reset()

    # ---------- cursor ----------
    def move_cursor(self, tx, ty):
        dx, dy = tx - self.cx, ty - self.cy
        d = math.hypot(dx, dy)
        if d < DEADZONE_PX:
            return
        alpha = MIN_ALPHA + (MAX_ALPHA - MIN_ALPHA) * min(1.0, d / FAST_PX)
        self.cx += dx * alpha
        self.cy += dy * alpha
        set_cursor(self.cx, self.cy)

    def position_at(self, t):
        """Cursor position at (or just before) time t."""
        found = None
        for ts, x, y in self.history:
            if ts <= t:
                found = (x, y)
            else:
                break
        if found is None:
            found = (self.history[0][1], self.history[0][2]) if self.history else (self.cx, self.cy)
        return found

    def lock_cursor(self, now):
        """Anchor the cursor where it was right before the pinch started so clicks never drift."""
        self.cx, self.cy = self.position_at(now - LOCK_LOOKBACK_S)
        set_cursor(self.cx, self.cy)

    # ---------- pinch state machine ----------
    def update_pinch(self, ratios):
        if self.pinch is not None:
            if ratios[self.pinch] > PINCH_OFF:
                name, self.pinch = self.pinch, None
                return "release", name
            return "hold", self.pinch
        cand = min(ratios, key=ratios.get)
        clear_winner = all(
            v > ratios[cand] + PINCH_MARGIN
            for k, v in ratios.items() if k != cand
        )
        if ratios[cand] < PINCH_ON and clear_winner:
            self.pinch = cand
            return "press", cand
        return None, None

    # ---------- scroll ----------
    def do_scroll(self, hand, now):
        if self.scroll_since is None:
            self.scroll_since = now
        cur = (hand[8].y + hand[12].y) / 2
        if now - self.scroll_since < SCROLL_ENTER_S:
            self.scroll_prev = cur
            return
        if self.scroll_prev is None:
            self.scroll_prev = cur
            return
        delta = self.scroll_prev - cur       # hand moves up => positive => scroll up
        self.scroll_prev = cur
        if abs(delta) < SCROLL_DEADZONE:
            return
        self.scroll_acc += delta * SCROLL_GAIN
        amount = int(self.scroll_acc)
        if abs(amount) >= SCROLL_MIN_SEND:
            amount = max(-SCROLL_MAX_SEND, min(SCROLL_MAX_SEND, amount))
            scroll(amount)
            self.scroll_acc -= amount

    # ---------- one hand ----------
    def one_hand(self, hand, now, w, h):
        palm = max(dist(hand[0], hand[9], w, h), 1.0)

        idx = finger_up(hand, 8, 6, w, h)
        mid = finger_up(hand, 12, 10, w, h)
        ring = finger_up(hand, 16, 14, w, h)
        lit = finger_up(hand, 20, 18, w, h)

        li = dist(hand[4], hand[8], w, h) / palm
        rm = dist(hand[4], hand[12], w, h) / palm
        rr = dist(hand[4], hand[16], w, h) / palm

        # Closed fist = rest / clutch mode. All 4 fingers folded toward wrist.
        is_fist = (
            not idx and not mid and not ring and not lit
            and all(dist(hand[0], hand[t], w, h) / palm < FIST_MAX for t in (8, 12, 16, 20))
        )
        if is_fist:
            self.release_all()
            self.status = "REST (fist)"
            return

        # ---- scroll (index + middle up, ring + pinky down, fingers not pinching) ----
        scroll_pose = (
            idx and mid and not ring and not lit
            and li > SCROLL_OPEN and rm > SCROLL_OPEN
        )
        if scroll_pose and self.pinch is None and self.left_state == "idle":
            self.do_scroll(hand, now)
            self.status = "SCROLL"
            self.history.append((now, self.cx, self.cy))
            return
        self.scroll_reset()

        # ---- pinch gestures ----
        event, name = self.update_pinch({"left": li, "right": rm, "double": rr})

        if event == "press":
            self.lock_cursor(now)
            if name == "left":
                self.left_state = "pending"
                self.pinch_start = now
            elif name == "right" and now > self.right_cd:
                right_click()
                self.right_cd = now + RIGHT_COOLDOWN_S
            elif name == "double" and now > self.double_cd:
                double_click()
                self.double_cd = now + DOUBLE_COOLDOWN_S

        elif event == "release" and name == "left":
            if self.left_state == "dragging":
                left_up()
            else:
                left_click()
            self.left_state = "idle"

        if (
            self.pinch == "left"
            and self.left_state == "pending"
            and now - self.pinch_start >= DRAG_HOLD_S
        ):
            left_down()
            self.left_state = "dragging"

        # ---- cursor movement (knuckle-stabilized to prevent pinch drift) ----
        dragging = self.left_state == "dragging"
        min_pinch_ratio = min(li, rm, rr)
        # Freeze cursor just as fingers close into a click pinch (unless already dragging)
        pre_pinch_freeze = (self.pinch is not None or min_pinch_ratio < PINCH_PRE_FREEZE) and not dragging

        if not pre_pinch_freeze:
            # Blend index tip (hand[8]) with stable index MCP knuckle (hand[5])
            ax = 0.68 * hand[8].x + 0.32 * hand[5].x
            ay = 0.68 * hand[8].y + 0.32 * hand[5].y
            tx, ty = map_to_screen(ax, ay)
            self.move_cursor(tx, ty)

        self.history.append((now, self.cx, self.cy))

        if dragging:
            self.status = "DRAGGING"
        elif self.pinch == "left":
            self.status = "LEFT CLICK / HOLD TO DRAG"
        elif self.pinch == "right":
            self.status = "RIGHT CLICK"
        elif self.pinch == "double":
            self.status = "DOUBLE CLICK"
        elif idx:
            self.status = "MOVE (pointing)"
        else:
            self.status = "MOVE (open hand)"

    # ---------- two hands ----------
    def two_hands(self, hands, now, w, h):
        a, b = hands[0], hands[1]
        palm = (max(dist(a[0], a[9], w, h), 1.0) + max(dist(b[0], b[9], w, h), 1.0)) / 2
        d = dist(a[9], b[9], w, h) / palm

        if not self.zoom_active:
            if self.zoom_since is None:
                self.zoom_since = now
            self.zoom_ref = d
            if now - self.zoom_since >= ZOOM_ENTER_S:
                self.release_all()
                self.zoom_active = True
            self.status = "TWO HANDS (starting zoom...)"
            return

        change = d - self.zoom_ref
        if abs(change) >= ZOOM_STEP and now > self.zoom_cd:
            zoom(1 if change > 0 else -1)
            self.zoom_ref = d
            self.zoom_cd = now + ZOOM_COOLDOWN_S
        self.status = "ZOOM"

    # ---------- per-frame entry ----------
    def update(self, hands, now, w, h):
        n = len(hands)

        if n != 2 and now - self.last_two > ZOOM_GRACE_S:
            self.zoom_reset()

        if n == 0:
            if now - self.last_seen > LOST_GRACE_S:
                self.release_all()
                self.status = "NO HAND"
            return

        self.last_seen = now

        if n >= 2:
            self.last_two = now
            self.two_hands(hands, now, w, h)
            return

        if self.zoom_active:
            self.status = "ZOOM"
            return
        self.one_hand(hands[0], now, w, h)


# =====================================================================
#  CAMERA & THREADED RUNTIME FOR GLASS NOVA
# =====================================================================
def _open_working_camera(preferred_index: int = CAMERA_INDEX):
    """Try multiple camera backends and indices, verifying a real frame is returned."""
    if cv2 is None:
        return None
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    indices = [preferred_index] + [i for i in (0, 1, 2) if i != preferred_index]

    for idx in indices:
        for backend in backends:
            cap = None
            try:
                cap = cv2.VideoCapture(idx, backend)
                if not cap or not cap.isOpened():
                    if cap:
                        cap.release()
                    continue
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FPS, 30)
                # Read up to 8 warm-up frames to verify non-empty output
                for _ in range(8):
                    ok, frame = cap.read()
                    if ok and frame is not None and frame.size > 0 and float(frame.mean()) > 1.0:
                        return cap
                    time.sleep(0.03)
                # Even if dark room (mean <= 1.0), accept if ok & valid shape
                ok, frame = cap.read()
                if ok and frame is not None and frame.size > 0:
                    return cap
                cap.release()
            except Exception:
                if cap:
                    try:
                        cap.release()
                    except Exception:
                        pass
    return None


class HandGestureManager:
    """Thread-safe controller for starting, pausing, resuming, and stopping Hand Gesture Mouse Control."""

    WINDOW_NAME = "Glass Nova - Hand Gesture Mouse"

    def __init__(self, on_state_change=None):
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()
        self._paused = False
        self._show_preview = True
        self._status = "stopped"
        self._gesture_status = "IDLE"
        self._last_error = ""
        self._on_state_change = on_state_change

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_status(self) -> dict:
        return {
            "ok": True,
            "running": self.is_running,
            "paused": self._paused,
            "status": self._status,
            "gesture": self._gesture_status,
            "show_preview": self._show_preview,
            "error": self._last_error,
        }

    def _notify(self, state: str):
        self._status = state
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception:
                pass

    def start(self, show_preview: bool = True, camera_index: int = CAMERA_INDEX) -> dict:
        with self._lock:
            if self.is_running:
                if self._paused:
                    self._paused = False
                    self._notify("running")
                    return {
                        "ok": True,
                        "status": "running",
                        "message": "Hand gesture mouse control was paused and is now resumed.",
                    }
                self._show_preview = show_preview
                return {
                    "ok": True,
                    "status": "running",
                    "message": "Hand gesture mouse control is already running.",
                }

            if cv2 is None:
                self._last_error = "OpenCV (cv2) is not installed."
                return {"ok": False, "message": self._last_error}

            self._stop_event.clear()
            self._paused = False
            self._show_preview = show_preview
            self._last_error = ""
            self._ready_event = threading.Event()

            self._thread = threading.Thread(
                target=self._run_loop,
                args=(camera_index,),
                daemon=True,
                name="NovaHandGestureThread",
            )
            self._thread.start()

        # Wait briefly for camera/model startup to report immediate success or camera error
        self._ready_event.wait(timeout=8.0)
        if self._last_error:
            return {"ok": False, "status": "error", "message": self._last_error}
        return {
            "ok": True,
            "status": "running",
            "message": (
                "Hand gesture mouse control is now active! Point or move your hand to move the cursor, "
                "pinch thumb + index to left-click (or hold to drag), thumb + middle to right-click, "
                "thumb + ring to double-click, hold index + middle up to scroll, or make a fist to rest."
            ),
        }

    def stop(self) -> dict:
        with self._lock:
            if not self.is_running:
                self._notify("stopped")
                return {"ok": True, "status": "stopped", "message": "Hand gesture mouse control is already stopped."}
            self._stop_event.set()
            t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3.0)
        self._notify("stopped")
        return {"ok": True, "status": "stopped", "message": "Hand gesture mouse control has been turned off."}

    def pause(self) -> dict:
        if not self.is_running:
            return {"ok": False, "message": "Hand gesture mouse control is not currently running."}
        self._paused = True
        self._notify("paused")
        return {"ok": True, "status": "paused", "message": "Hand gesture mouse control is paused."}

    def resume(self) -> dict:
        if not self.is_running:
            return self.start(show_preview=self._show_preview)
        self._paused = False
        self._notify("running")
        return {"ok": True, "status": "running", "message": "Hand gesture mouse control is resumed."}

    def set_preview(self, visible: bool) -> dict:
        self._show_preview = bool(visible)
        return {
            "ok": True,
            "show_preview": self._show_preview,
            "message": f"Hand gesture camera preview {'shown' if self._show_preview else 'hidden'}.",
        }

    def _run_loop(self, camera_index: int):
        ctl = GestureMouse()
        cap = None
        tracker_mode = "fallback"
        tracker = None
        window_created = False

        try:
            self._notify("starting")
            tracker_mode, tracker = create_best_hand_tracker(MODEL_PATH)

            cap = _open_working_camera(camera_index)
            if cap is None:
                self._last_error = (
                    "Could not open webcam. Please make sure your camera is connected, not in use by another app, "
                    "and enabled in Windows Settings > Privacy & security > Camera."
                )
                self._notify("error")
                self._ready_event.set()
                return

            self._notify("running")
            self._ready_event.set()

            last_ts = -1
            t0 = time.monotonic()
            failed = 0

            while not self._stop_event.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    failed += 1
                    if failed > 40:
                        self._last_error = "Camera stream disconnected."
                        break
                    time.sleep(0.01)
                    continue
                failed = 0

                frame = cv2.flip(frame, 1)
                h, w = frame.shape[:2]
                now = time.monotonic()

                if tracker_mode == "mediapipe":
                    rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    ts = int((now - t0) * 1000)
                    if ts <= last_ts:
                        ts = last_ts + 1
                    last_ts = ts
                    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    result = tracker.detect_for_video(mp_image, ts)
                    hands = result.hand_landmarks or []
                else:
                    hands = tracker.detect(frame)

                if self._paused:
                    ctl.release_all()
                    ctl.zoom_reset()
                    ctl.status = "PAUSED (Press P or ask Nova to resume)"
                else:
                    ctl.update(hands, now, w, h)

                self._gesture_status = ctl.status

                if self._show_preview:
                    for hand in hands:
                        draw_hand(frame, hand)

                    x1, x2 = int(ACTIVE_X[0] * w), int(ACTIVE_X[1] * w)
                    y1, y2 = int(ACTIVE_Y[0] * h), int(ACTIVE_Y[1] * h)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 190, 40), 1)

                    # HUD banner
                    overlay = frame.copy()
                    cv2.rectangle(overlay, (0, 0), (w, 42), (18, 24, 34), -1)
                    cv2.rectangle(overlay, (0, h - 30), (w, h), (18, 24, 34), -1)
                    cv2.addWeighted(overlay, 0.72, frame, 0.28, 0, frame)

                    color = (100, 255, 140) if not self._paused else (60, 180, 255)
                    cv2.putText(
                        frame, f"NOVA GESTURE: {ctl.status}", (14, 27),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.62, color, 2, cv2.LINE_AA
                    )
                    cv2.putText(
                        frame, "Index: Move | Pinch: Click/Drag | 2-Finger: Scroll | Fist: Rest | P: Pause | Q: Quit",
                        (12, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (215, 235, 250), 1, cv2.LINE_AA
                    )

                    cv2.imshow(self.WINDOW_NAME, frame)
                    if not window_created:
                        window_created = True
                        try:
                            cv2.setWindowProperty(self.WINDOW_NAME, cv2.WND_PROP_TOPMOST, 1)
                        except Exception:
                            pass

                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        self._stop_event.set()
                        break
                    elif key == ord("p"):
                        self._paused = not self._paused
                        self._notify("paused" if self._paused else "running")
                    elif key == ord("h"):
                        self._show_preview = False
                        try:
                            cv2.destroyWindow(self.WINDOW_NAME)
                        except Exception:
                            pass
                        window_created = False

                    if window_created:
                        try:
                            if cv2.getWindowProperty(self.WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                                self._stop_event.set()
                                break
                        except cv2.error:
                            self._stop_event.set()
                            break
                else:
                    if window_created:
                        try:
                            cv2.destroyWindow(self.WINDOW_NAME)
                        except Exception:
                            pass
                        window_created = False
                    time.sleep(0.005)
        except Exception as e:
            self._last_error = f"Hand gesture controller error: {e}"
            print(f"[HandGesture] Error: {e}", flush=True)
        finally:
            self._ready_event.set()
            ctl.release_all()
            if tracker is not None:
                try:
                    tracker.close()
                except Exception:
                    pass
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if window_created and cv2 is not None:
                try:
                    cv2.destroyWindow(self.WINDOW_NAME)
                except Exception:
                    pass
            self._notify("stopped")


# Singleton manager instance
gesture_manager = HandGestureManager()


def main():
    """Standalone entry point for running hand_gesture.py directly."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
    res = gesture_manager.start(show_preview=True)
    if not res.get("ok"):
        print(res.get("message"))
        sys.exit(1)
    print(res.get("message"))
    try:
        while gesture_manager.is_running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        gesture_manager.stop()


if __name__ == "__main__":
    main()
