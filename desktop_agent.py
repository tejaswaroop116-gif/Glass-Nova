"""Visual mouse and keyboard desktop control agent for Glass Nova.

Combines Windows UIAutomation and Gemini vision coordinate estimation to locate
and interact with buttons, menus, text fields, and applications.
All actions check mouse_keyboard and sensitive permissions, with verification of results.
"""
import os, time, re, json
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
import pyautogui
from dotenv import load_dotenv
from google.genai import types

from permissions import permissions
from screen_agent import (
    get_active_window_title,
    capture_screen_jpeg,
    capture_screen_with_metadata,
    compute_visual_change,
    ensure_dpi_awareness,
    get_screen_metrics,
    normalized_to_screen_coords,
    query_vision_json,
    get_client,
    get_vision_models,
    remember_working_vision_model,
)

ensure_dpi_awareness()

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.08

def _normalize_ui_label(text: str) -> Tuple[str, str]:
    """Return (full_alnum, stem_alnum) with spaces/punctuation/common suffixes stripped."""
    s = (text or "").strip().lower()
    s = re.sub(r"\b(on my screen|on screen|on the screen|in file explorer|in documents|the|file|icon|button)\b", " ", s)
    full_norm = re.sub(r"[^a-z0-9]", "", s)
    stem_norm = re.sub(r"(pdf|docx|doc|txt|pptx|xlsx|mp4|mp3|png|jpg)$", "", full_norm)
    return full_norm, stem_norm or full_norm


# Interactive UIAutomation ControlTypeIds (Button=50000, CheckBox=50002, ComboBox=50003,
# Edit=50004, Hyperlink=50005, ListItem=50007, MenuItem=50011, RadioButton=50013,
# TabItem=50019, TreeItem=50024, SplitButton=50031)
_INTERACTIVE_UIA_TYPES = {50000, 50002, 50003, 50004, 50005, 50007, 50011, 50013, 50019, 50024, 50031}
_BROWSER_WINDOW_HINTS = ("chrome", "edge", "firefox", "brave", "opera", "youtube")
_MEDIA_CONTROL_HINTS = ("skip ad", "skip ads", "skip", "play", "pause", "mute", "unmute", "fullscreen", "full screen", "captions", "subtitles", "theater", "miniplayer", "next video")


def _is_browser_media_target(label: str, active_window: str) -> bool:
    lbl = (label or "").strip().lower()
    win = (active_window or "").strip().lower()
    return any(b in win for b in _BROWSER_WINDOW_HINTS) and any(m in lbl for m in _MEDIA_CONTROL_HINTS)


def _is_skip_ad_label(label: str) -> bool:
    lbl = re.sub(r"[^a-z ]", " ", (label or "").lower())
    lbl = " ".join(lbl.split())
    return lbl in ("skip", "skip ad", "skip ads", "skip the ad", "skip this ad", "skip add", "skip adds") or "skip ad" in lbl


# Accessible names YouTube uses for its skip button (it changes between versions).
_SKIP_AD_NAMES = ("Skip", "Skip Ad", "Skip Ads", "Skip ad", "Skip ads", "Skip Ad(s)", "Skip ad(s)")

_UIA_MODULE = None


def _get_uia():
    """Create a working IUIAutomation client in the current thread.

    The previous code used a mistyped CLSID ({...-4701-...} instead of {...-4201-...}),
    so Windows UIAutomation silently NEVER worked and every click fell back to vision guesses.
    """
    global _UIA_MODULE
    import comtypes
    import comtypes.client
    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    if _UIA_MODULE is None:
        _UIA_MODULE = comtypes.client.GetModule("UIAutomationCore.dll")
    m = _UIA_MODULE
    return comtypes.client.CreateObject(m.CUIAutomation, interface=m.IUIAutomation), m


def _own_pids() -> set:
    pids = {os.getpid()}
    try:
        pids.add(os.getppid())
    except Exception:
        pass
    return pids


def _hwnd_pid(hwnd) -> int:
    try:
        import ctypes
        from ctypes import wintypes
        pid = wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def _top_target_windows(limit: int = 4) -> List[int]:
    """Visible top-level windows in Z-order (front first), excluding Nova's own windows."""
    out: List[int] = []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetWindow.restype = wintypes.HWND
        user32.GetTopWindow.restype = wintypes.HWND
        own = _own_pids()
        h = user32.GetTopWindow(None)
        guard = 0
        while h and len(out) < limit and guard < 600:
            guard += 1
            if user32.IsWindowVisible(h) and not user32.IsIconic(h) and user32.GetWindowTextLengthW(h) > 0:
                if _hwnd_pid(h) not in own:
                    rect = wintypes.RECT()
                    user32.GetWindowRect(h, ctypes.byref(rect))
                    if (rect.right - rect.left) > 120 and (rect.bottom - rect.top) > 120:
                        out.append(int(h))
            h = user32.GetWindow(h, 2)  # GW_HWNDNEXT
    except Exception:
        pass
    return out


def _root_window_at(x: int, y: int) -> int:
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.restype = wintypes.HWND
        h = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        if h:
            return int(user32.GetAncestor(h, 2) or h)  # GA_ROOT
    except Exception:
        pass
    return 0


def _physical_click(cx: int, cy: int, button: str = "left", clicks: int = 1) -> None:
    """Reliable real hardware mouse tap: focus target window, move cursor, and send Win32 mouse_event."""
    cx, cy = int(cx), int(cy)
    if hasattr(pyautogui.click, "_mock_name"):
        pyautogui.click(cx, cy, clicks=max(1, int(clicks)), button=button)
        return
    root = _root_window_at(cx, cy)
    activated_window = False
    if root and _hwnd_pid(root) not in _own_pids():
        try:
            import ctypes
            if ctypes.windll.user32.GetForegroundWindow() != root:
                _force_foreground_window(root)
                activated_window = True
        except Exception:
            pass
    if activated_window:
        time.sleep(0.12)

    try:
        pyautogui.moveTo(cx, cy, duration=0.15)
    except Exception:
        pass

    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.SetCursorPos(cx, cy)
        time.sleep(0.08)  # let hover state (e.g. YouTube Skip button highlight) settle
        down_flag, up_flag = {
            "left": (0x0002, 0x0004),
            "right": (0x0008, 0x0010),
            "middle": (0x0020, 0x0040),
        }.get((button or "left").lower(), (0x0002, 0x0004))
        for i in range(max(1, int(clicks))):
            user32.SetCursorPos(cx, cy)
            user32.mouse_event(down_flag, 0, 0, 0, 0)
            time.sleep(0.06)
            user32.mouse_event(up_flag, 0, 0, 0, 0)
            if i + 1 < clicks:
                time.sleep(0.08)
        return
    except Exception:
        pass

    for i in range(max(1, int(clicks))):
        pyautogui.mouseDown(x=cx, y=cy, button=button)
        time.sleep(0.07)
        pyautogui.mouseUp(x=cx, y=cy, button=button)
        if i + 1 < clicks:
            time.sleep(0.09)


def uia_find_and_invoke(names, hwnds: Optional[List[int]] = None, invoke: bool = True) -> Dict[str, Any]:
    """Find an element by exact accessible name (case-insensitive) inside target windows and
    activate it through UIAutomation Invoke / LegacyIAccessible (no coordinates needed).
    Returns {"found", "invoked", "coordinates", "name"}."""
    result = {"found": False, "invoked": False, "coordinates": None, "name": ""}
    try:
        uia, m = _get_uia()
        conds = [uia.CreatePropertyConditionEx(30005, n, 1) for n in names]  # UIA_NamePropertyId, IgnoreCase
        cond = conds[0]
        for c in conds[1:]:
            cond = uia.CreateOrCondition(cond, c)
        targets = hwnds if hwnds is not None else _top_target_windows(3)
        metrics = get_screen_metrics()
        sw, sh = metrics["width"], metrics["height"]
        for attempt in range(2):  # Chrome builds its web accessibility tree on first UIA query
            for h in targets:
                try:
                    root = uia.ElementFromHandle(h)
                    found = root.FindAll(4, cond)  # TreeScope_Descendants
                except Exception:
                    continue
                if not found:
                    continue
                for i in range(found.Length):
                    el = found.GetElement(i)
                    try:
                        if el.CurrentIsOffscreen:
                            continue
                        r = el.CurrentBoundingRectangle
                        w, hh = r.right - r.left, r.bottom - r.top
                        if w <= 2 or hh <= 2 or w > sw * 0.5 or hh > sh * 0.5:
                            continue
                        coords = (r.left + w // 2, r.top + hh // 2)
                    except Exception:
                        continue
                    result.update(found=True, coordinates=coords, name=str(el.CurrentName or ""))
                    if not invoke:
                        return result
                    try:
                        pat = el.GetCurrentPattern(10000)  # Invoke
                        if pat:
                            pat.QueryInterface(m.IUIAutomationInvokePattern).Invoke()
                            result["invoked"] = True
                            return result
                    except Exception:
                        pass
                    try:
                        pat = el.GetCurrentPattern(10018)  # LegacyIAccessible
                        if pat:
                            pat.QueryInterface(m.IUIAutomationLegacyIAccessiblePattern).DoDefaultAction()
                            result["invoked"] = True
                            return result
                    except Exception:
                        pass
                    return result
            if attempt == 0:
                time.sleep(0.6)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def find_element_via_uia(name: str) -> Optional[Tuple[int, int]]:
    """Attempts to find a visible, on-screen interactive UI element's center coordinates via Windows UIAutomation.
    Searches the active foreground window first, then open Explorer windows, then top-level windows.
    """
    ensure_dpi_awareness()
    target_lower = (name or "").strip().lower()
    if not target_lower:
        return None
    target_norm, target_stem = _normalize_ui_label(target_lower)
    if not target_norm:
        return None
    metrics = get_screen_metrics()
    sw, sh = metrics["width"], metrics["height"]
    try:
        import ctypes
        from ctypes import wintypes
        uia, _m = _get_uia()
        cond = uia.CreateTrueCondition()
        walker = uia.CreateTreeWalker(cond)

        def _extract_center(elem) -> Optional[Tuple[int, int]]:
            try:
                if getattr(elem, "CurrentIsOffscreen", 0):
                    return None
                rect = elem.CurrentBoundingRectangle
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                # Reject zero-sized, offscreen, or oversized container elements
                if w <= 2 or h <= 2 or w > int(sw * 0.65) or h > int(sh * 0.65):
                    return None
                cx = rect.left + w // 2
                cy = rect.top + h // 2
                if cx < 0 or cy < 0 or cx >= sw or cy >= sh:
                    return None
                return cx, cy
            except Exception:
                pass
            return None

        def _search_subtree(start_elem, max_depth: int = 8, max_nodes: int = 800) -> Optional[Tuple[int, int]]:
            if not start_elem:
                return None
            queue = [(start_elem, 0)]
            visited = 0
            exact_non_interactive = None
            substring_interactive = None
            while queue and visited < max_nodes:
                node, depth = queue.pop(0)
                visited += 1
                try:
                    w_name = (node.CurrentName or "").strip()
                    if w_name:
                        w_lower = w_name.lower()
                        w_norm, w_stem = _normalize_ui_label(w_lower)
                        ctype = getattr(node, "CurrentControlType", 0)
                        is_interactive = ctype in _INTERACTIVE_UIA_TYPES
                        is_exact = (
                            w_lower == target_lower
                            or (w_norm and w_norm == target_norm)
                            or (len(target_stem) >= 4 and w_stem == target_stem)
                        )
                        if is_exact:
                            pt = _extract_center(node)
                            if pt:
                                if is_interactive:
                                    return pt
                                if exact_non_interactive is None:
                                    exact_non_interactive = pt
                        elif substring_interactive is None and is_interactive and (
                            target_lower in w_lower
                            or (len(target_stem) >= 4 and target_stem in w_norm)
                        ):
                            pt = _extract_center(node)
                            if pt:
                                substring_interactive = pt
                except Exception:
                    pass

                if depth < max_depth:
                    try:
                        child = walker.GetFirstChildElement(node)
                        siblings = 0
                        while child and siblings < 120:
                            queue.append((child, depth + 1))
                            siblings += 1
                            child = walker.GetNextSiblingElement(child)
                    except Exception:
                        pass
            return exact_non_interactive or substring_interactive

        user32 = ctypes.windll.user32

        # 1. Search inside the front-most non-Nova windows first (Nova's own window is often focused
        #    because the user is talking to it, so the real target is the window behind it).
        fg_hwnd = 0
        try:
            fg = user32.GetForegroundWindow()
            if fg and _hwnd_pid(fg) not in _own_pids():
                fg_hwnd = fg
            front = ([fg_hwnd] if fg_hwnd else []) + [h for h in _top_target_windows(2) if h != fg_hwnd]
            for h in front:
                pt = _search_subtree(uia.ElementFromHandle(h), max_depth=9, max_nodes=900)
                if pt:
                    return pt
        except Exception:
            pass

        # 2. Search inside any open File Explorer windows (CabinetWClass)
        try:
            user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
            user32.FindWindowExW.restype = wintypes.HWND
            exp_hwnd = None
            for _ in range(10):
                exp_hwnd = user32.FindWindowExW(None, exp_hwnd, "CabinetWClass", None)
                if not exp_hwnd:
                    break
                if exp_hwnd == fg_hwnd:
                    continue
                exp_elem = uia.ElementFromHandle(exp_hwnd)
                pt = _search_subtree(exp_elem, max_depth=9, max_nodes=900)
                if pt:
                    return pt
        except Exception:
            pass

        # 3. Search top-level windows and shallow descendants (e.g., taskbar / desktop icons)
        root = uia.GetRootElement()
        return _search_subtree(root, max_depth=3, max_nodes=300)
    except Exception:
        pass
    return None


def find_element_rect_via_uia(name: str) -> Optional[Tuple[int, int, int, int]]:
    """Find a visible UI element's physical bounding rectangle (x, y, width, height) via Windows UIAutomation.
    Read-only metadata lookup for visual walkthrough highlights — never clicks or invokes the element."""
    ensure_dpi_awareness()
    target_lower = (name or "").strip().lower()
    if not target_lower:
        return None
    target_norm, target_stem = _normalize_ui_label(target_lower)
    if not target_norm:
        return None
    metrics = get_screen_metrics()
    sw, sh = metrics["width"], metrics["height"]
    try:
        uia, _m = _get_uia()
        cond = uia.CreateTrueCondition()
        walker = uia.CreateTreeWalker(cond)

        def _extract_rect(elem) -> Optional[Tuple[int, int, int, int]]:
            try:
                if getattr(elem, "CurrentIsOffscreen", 0):
                    return None
                r = elem.CurrentBoundingRectangle
                w = int(r.right - r.left)
                h = int(r.bottom - r.top)
                if w <= 4 or h <= 4 or w > int(sw * 0.65) or h > int(sh * 0.65):
                    return None
                if r.left < 0 or r.top < 0 or r.right > sw or r.bottom > sh:
                    return None
                return int(r.left), int(r.top), w, h
            except Exception:
                return None

        for h in _top_target_windows(3):
            try:
                start_elem = uia.ElementFromHandle(h)
            except Exception:
                continue
            if not start_elem:
                continue
            queue = [(start_elem, 0)]
            visited = 0
            fallback_rect = None
            while queue and visited < 700:
                node, depth = queue.pop(0)
                visited += 1
                try:
                    w_name = (node.CurrentName or "").strip()
                    if w_name:
                        w_lower = w_name.lower()
                        w_norm, w_stem = _normalize_ui_label(w_lower)
                        ctype = getattr(node, "CurrentControlType", 0)
                        is_interactive = ctype in _INTERACTIVE_UIA_TYPES
                        is_exact = (
                            w_lower == target_lower
                            or (w_norm and w_norm == target_norm)
                            or (len(target_stem) >= 4 and w_stem == target_stem)
                        )
                        if is_exact:
                            box = _extract_rect(node)
                            if box:
                                if is_interactive:
                                    return box
                                if fallback_rect is None:
                                    fallback_rect = box
                        elif fallback_rect is None and is_interactive and (
                            target_lower in w_lower
                            or (len(target_stem) >= 4 and target_stem in w_norm)
                        ):
                            box = _extract_rect(node)
                            if box:
                                fallback_rect = box
                except Exception:
                    pass
                if depth < 8:
                    try:
                        child = walker.GetFirstChildElement(node)
                        siblings = 0
                        while child and siblings < 100:
                            queue.append((child, depth + 1))
                            siblings += 1
                            child = walker.GetNextSiblingElement(child)
                    except Exception:
                        pass
            if fallback_rect:
                return fallback_rect
    except Exception:
        pass
    return None

def find_element_via_vision_details(
    label: str,
    screen_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Use Gemini Vision to understand the screen and locate the exact UI element bounding box & center."""
    meta = screen_meta or capture_screen_with_metadata(max_dimension=1600, quality=85)
    active_win = meta.get("active_window") or get_active_window_title()
    img_bytes = meta["bytes"]

    sys_inst = (
        "You are Nova's precision visual UI element locator on Windows 11. "
        "First understand what is currently displayed on the screen, then locate the exact requested UI element "
        "(button, icon, menu item, browser control, YouTube player button, or Skip Ads button). "
        "Never guess or invent coordinates if the element is NOT clearly visible on screen. "
        "Return ONLY valid JSON with keys: "
        '{"screen_summary": str, "found": bool, "matched_text": str, '
        '"box_2d": [ymin, xmin, ymax, xmax], "center": [y, x], "reason": str}. '
        "All coordinates must be normalized to [0..1000] where [0,0] is top-left and [1000,1000] is bottom-right."
    )
    prompt = (
        f"Active window: '{active_win}'.\n"
        f"Target UI element to click: '{label}'.\n"
        "Inspect the screenshot carefully. If the target is a YouTube/video control (like 'Skip Ads', 'Skip Ad', 'Skip', 'Play', 'Pause'), "
        "locate the actual player button, NOT video title text or unrelated ads. "
        "If the element is not visible right now, set \"found\": false."
    )
    parsed = query_vision_json(
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
            types.Part.from_text(text=prompt),
        ],
        system_instruction=sys_inst,
        temperature=0.1,
        max_output_tokens=450,
    )
    if not parsed:
        return {"found": False, "coordinates": None, "screen_summary": "", "reason": "Vision model returned no structured result.", "meta": meta}

    found = bool(parsed.get("found"))
    if not found:
        return {
            "found": False,
            "coordinates": None,
            "screen_summary": str(parsed.get("screen_summary") or ""),
            "reason": str(parsed.get("reason") or f"'{label}' is not visible on screen."),
            "meta": meta,
        }

    norm_y = norm_x = None
    box = parsed.get("box_2d")
    if isinstance(box, list) and len(box) == 4:
        try:
            ymin, xmin, ymax, xmax = [float(v) for v in box]
            if 0 <= ymin <= ymax <= 1000 and 0 <= xmin <= xmax <= 1000:
                norm_y = (ymin + ymax) * 0.5
                norm_x = (xmin + xmax) * 0.5
        except Exception:
            pass
    if norm_y is None or norm_x is None:
        ctr = parsed.get("center")
        if isinstance(ctr, list) and len(ctr) == 2:
            try:
                norm_y, norm_x = float(ctr[0]), float(ctr[1])
            except Exception:
                pass

    if norm_y is None or norm_x is None:
        return {
            "found": False,
            "coordinates": None,
            "screen_summary": str(parsed.get("screen_summary") or ""),
            "reason": "Vision response did not include valid coordinates.",
            "meta": meta,
        }

    coords = normalized_to_screen_coords(norm_y, norm_x, meta)
    return {
        "found": True,
        "coordinates": coords,
        "matched_text": str(parsed.get("matched_text") or label),
        "screen_summary": str(parsed.get("screen_summary") or ""),
        "reason": str(parsed.get("reason") or ""),
        "meta": meta,
    }


def find_element_via_vision(label: str, screen_meta: Optional[Dict[str, Any]] = None) -> Optional[Tuple[int, int]]:
    """Uses Gemini vision to estimate the physical pixel coordinates of an interface element."""
    res = find_element_via_vision_details(label, screen_meta=screen_meta)
    return res.get("coordinates") if res.get("found") else None


def verify_action_result(
    before_meta: Dict[str, Any],
    after_meta: Dict[str, Any],
    action_desc: str,
    click_coords: Optional[Tuple[int, int]] = None,
    target_label: str = "",
    uia_existed_before: bool = False,
) -> Dict[str, Any]:
    """Verify whether a UI action actually succeeded by comparing before/after screenshots, UIA state, and window state.
    Never treats a generic screen pixel change by itself as sufficient proof of success."""
    before_title = before_meta.get("active_window") or ""
    after_title = after_meta.get("active_window") or ""
    title_changed = bool(before_title and after_title and before_title != after_title)

    diff_info = compute_visual_change(
        before_meta["bytes"],
        after_meta["bytes"],
        focus_point=click_coords,
        meta=after_meta,
    )

    # Check if a UIA-located transient button/menu item (e.g., dialog OK, Skip Ad, menu item) disappeared after local click
    uia_disappeared = False
    if uia_existed_before and target_label:
        try:
            still_there = find_element_via_uia(target_label)
            if still_there is None and float(diff_info.get("local_changed_ratio", 0.0) or 0.0) >= 0.02:
                uia_disappeared = True
        except Exception:
            pass

    sys_inst = (
        "You are Nova's action verification engine. Compare the BEFORE screenshot and the AFTER screenshot "
        "to determine if the requested UI action actually succeeded. "
        "Never assume success just because a click occurred or because unrelated background video pixels moved. "
        "Verify the specific expected button, control, dialog, menu, or application state. "
        'Return ONLY JSON: {"verified": bool, "what_changed": str, "reason": str}.'
    )
    prompt = (
        f"Requested action: {action_desc}\n"
        f"Window title before: '{before_title}' -> after: '{after_title}'\n"
        f"Local pixel change around click: {diff_info.get('local_changed_ratio')}, global change: {diff_info.get('global_changed_ratio')}.\n"
        "Look at both screenshots: did the intended UI state change actually happen (e.g., ad skipped, video started playing/paused, menu opened/closed, dialog dismissed, button state toggled)?"
    )
    parsed = query_vision_json(
        contents=[
            types.Part.from_text(text="BEFORE SCREENSHOT:"),
            types.Part.from_bytes(data=before_meta["bytes"], mime_type="image/jpeg"),
            types.Part.from_text(text="AFTER SCREENSHOT:"),
            types.Part.from_bytes(data=after_meta["bytes"], mime_type="image/jpeg"),
            types.Part.from_text(text=prompt),
        ],
        system_instruction=sys_inst,
        temperature=0.1,
        max_output_tokens=300,
    )
    if parsed and "verified" in parsed:
        return {
            "verified": bool(parsed["verified"]),
            "unverified": False,
            "what_changed": str(parsed.get("what_changed") or ""),
            "reason": str(parsed.get("reason") or ""),
            "visual_diff": diff_info,
            "title_changed": title_changed,
        }

    # If Vision verification is unavailable, only confirm on strong structural UI evidence (window title change or UIA target transition),
    # NEVER on a generic screen pixel change alone.
    if title_changed or uia_disappeared:
        what = f"Active window changed from '{before_title}' to '{after_title}'" if title_changed else f"UI element '{target_label}' activated and closed"
        return {
            "verified": True,
            "unverified": False,
            "what_changed": what,
            "reason": "Verified via deterministic window/UIA state transition.",
            "visual_diff": diff_info,
            "title_changed": title_changed,
        }

    return {
        "verified": False,
        "unverified": True,
        "what_changed": "Screen pixels changed, but specific UI state could not be confirmed" if diff_info.get("changed") else "No verifiable UI state change detected",
        "reason": "Could not independently confirm the expected UI state change.",
        "visual_diff": diff_info,
        "title_changed": title_changed,
    }


def _wait_for_screen_change(
    before_meta: Dict[str, Any],
    coords: Optional[Tuple[int, int]],
    timeout: float = 2.4,
    interval: float = 0.35,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Poll fresh screenshots until the screen visibly changes (or timeout). Returns (after_meta, diff)."""
    deadline = time.monotonic() + timeout
    after_meta, diff = before_meta, {"changed": False}
    while True:
        time.sleep(interval)
        after_meta = capture_screen_with_metadata(max_dimension=1600, quality=85)
        diff = compute_visual_change(before_meta["bytes"], after_meta["bytes"], focus_point=coords, meta=after_meta)
        if diff.get("changed") or time.monotonic() >= deadline:
            return after_meta, diff


def _get_hwnd_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value or ""
    except Exception:
        return ""


def _get_hwnd_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        buf = ctypes.create_unicode_buffer(256)
        if user32.GetClassNameW(hwnd, buf, 256) > 0:
            return buf.value or ""
    except Exception:
        pass
    return ""


def _browser_windows() -> List[int]:
    out = []
    try:
        for h in _top_target_windows(10):
            title_l = _get_hwnd_title(h).lower()
            cls_l = _get_hwnd_class(h).lower()
            if (
                any(b in title_l for b in _BROWSER_WINDOW_HINTS)
                or "new tab" in title_l
                or cls_l in ("chrome_widgetwin_1", "mozillawindowclass")
            ) and "nova" not in title_l:
                out.append(h)
    except Exception:
        pass
    return out


def skip_youtube_ad() -> Dict[str, Any]:
    """Skip a YouTube ad: find the real Skip button, physically tap/invoke it, and confirm the ad was skipped."""
    ensure_dpi_awareness()
    if not permissions.request_permission("mouse_keyboard", "Click the YouTube 'Skip Ad' button"):
        return {"ok": False, "message": "Mouse and keyboard permission was denied.", "permission_denied": True}

    browsers = _browser_windows() or _top_target_windows(3)

    def _skip_button_present() -> bool:
        return bool(browsers and uia_find_and_invoke(_SKIP_AD_NAMES, browsers, invoke=False).get("found"))

    before_meta = capture_screen_with_metadata(max_dimension=1600, quality=85)

    # 1) UIAutomation: find accessible "Skip"/"Skip Ad"/"Skip Ads" button, invoke AND physically tap it.
    res = uia_find_and_invoke(_SKIP_AD_NAMES, browsers, invoke=True) if browsers else {"found": False}
    method = ""
    coords = res.get("coordinates")
    if res.get("found"):
        if coords:
            # Always perform a real physical tap in addition to UIA Invoke because YouTube ad overlays
            # often require a trusted hardware pointer event (isTrusted=true) to skip the ad.
            _physical_click(coords[0], coords[1])
            method = "UIAutomation + physical tap"
        elif res.get("invoked"):
            method = "UIAutomation Invoke"

    # 2) Vision fallback: locate on screen and perform a real hardware click.
    if not method:
        v = find_element_via_vision_details(
            "the YouTube player's 'Skip' / 'Skip Ad' / 'Skip Ads' button (bottom-right of the video player, NOT a countdown like 'Ad will end in' or 'Skip in 5')",
            screen_meta=before_meta,
        )
        if not (v.get("found") and v.get("coordinates")):
            reason = v.get("reason") or "No skippable ad button is visible."
            return {
                "ok": False,
                "verified": False,
                "message": f"There's no clickable Skip button right now. {reason} (The ad may still be counting down or may be unskippable.)",
                "screen_summary": v.get("screen_summary", ""),
            }
        coords = v["coordinates"]
        _physical_click(coords[0], coords[1])
        method = "Visual Recognition tap"

    # 3) Verify: wait for screen change and confirm the Skip button disappeared.
    after_meta, diff = _wait_for_screen_change(before_meta, coords, timeout=1.8)
    if res.get("found") and not _skip_button_present():
        return {"ok": True, "verified": True, "message": f"Skipped the YouTube ad ({method}).", "coordinates": coords}

    ver = verify_action_result(
        before_meta,
        after_meta,
        "Skip the YouTube ad (the Skip Ad button should disappear and the main video should play)",
        coords,
    )
    if ver.get("verified"):
        return {
            "ok": True,
            "verified": True,
            "message": f"Skipped the YouTube ad ({method}). {ver.get('what_changed', '')}".strip(),
            "coordinates": coords,
        }

    # 4) Safe second tap retry (in case the first tap only focused the browser/player or woke the controls).
    retry_coords = None
    if browsers:
        retry_uia = uia_find_and_invoke(_SKIP_AD_NAMES, browsers, invoke=False)
        if retry_uia.get("found") and retry_uia.get("coordinates"):
            retry_coords = retry_uia["coordinates"]
    if not retry_coords:
        v2 = find_element_via_vision_details(
            "the YouTube player's 'Skip' / 'Skip Ad' / 'Skip Ads' button",
            screen_meta=after_meta,
        )
        if v2.get("found") and v2.get("coordinates"):
            retry_coords = v2["coordinates"]
    if retry_coords:
        _physical_click(retry_coords[0], retry_coords[1])
        after_meta2, _ = _wait_for_screen_change(after_meta, retry_coords, timeout=1.5)
        ver2 = verify_action_result(after_meta, after_meta2, "Skip the YouTube ad", retry_coords)
        if ver2.get("verified") or (res.get("found") and not _skip_button_present()):
            return {"ok": True, "verified": True, "message": "Skipped the YouTube ad on the second tap.", "coordinates": retry_coords}

    return {
        "ok": False,
        "verified": False,
        "message": "I tapped the Skip Ad button, but the ad is still playing. It may still be counting down.",
        "coordinates": coords,
    }


def click_element(label: str, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
    """Unified visual interaction: SEE SCREEN -> UNDERSTAND SCREEN -> FIND ELEMENT -> PERFORM ACTION -> VERIFY RESULT."""
    ensure_dpi_awareness()
    if _is_skip_ad_label(label):
        return skip_youtube_ad()
    action_desc = f"Click UI element '{label}'"
    if not permissions.request_permission("mouse_keyboard", action_desc):
        return {"ok": False, "verified": False, "message": "Mouse and keyboard permission was denied.", "permission_denied": True}

    num_clicks = max(1, int(clicks or 1))
    btn = (button or "left").lower().strip()
    if btn not in ("left", "right", "middle"):
        btn = "left"

    lbl_clean = (label or "").strip().lower()
    if lbl_clean in ("", "cursor", "cursor position", "the cursor position", "current position", "mouse position", "here", "pointer"):
        before_meta = capture_screen_with_metadata(max_dimension=1600, quality=85)
        cx, cy = pyautogui.position()
        _physical_click(cx, cy, button=btn, clicks=num_clicks)
        time.sleep(0.35)
        after_meta = capture_screen_with_metadata(max_dimension=1600, quality=85)
        ver = verify_action_result(before_meta, after_meta, f"Click at cursor ({cx}, {cy})", (cx, cy))
        act_word = "Double-clicked" if num_clicks == 2 else "Clicked"
        return {
            "ok": True if ver.get("unverified") else bool(ver["verified"]),
            "verified": bool(ver["verified"]),
            "message": f"{act_word} at cursor position ({cx}, {cy}). {ver['what_changed'] or ver['reason']}".strip(),
            "coordinates": (cx, cy),
        }

    # Step 1: SEE SCREEN
    before_meta = capture_screen_with_metadata(max_dimension=1600, quality=85)
    before_title = before_meta.get("active_window") or get_active_window_title()
    prefer_vision = _is_browser_media_target(label, before_title)

    last_screen_summary = ""
    last_reason = ""
    tried_coords = []
    last_diff_changed = False
    last_unverified = False

    for attempt in range(2):
        current_meta = before_meta if attempt == 0 else capture_screen_with_metadata(max_dimension=1600, quality=85)

        # If looking for YouTube/video controls on retry, gently hover near bottom-center of screen to reveal auto-hidden controls
        if attempt == 1 and prefer_vision:
            sw, sh = current_meta["screen_width"], current_meta["screen_height"]
            try:
                pyautogui.moveTo(int(sw * 0.5), int(sh * 0.6), duration=0.15)
                time.sleep(0.35)
                current_meta = capture_screen_with_metadata(max_dimension=1600, quality=85)
            except Exception:
                pass

        # Step 2 & 3: UNDERSTAND SCREEN & FIND ELEMENT
        coords = None
        method = ""
        uia_found = False
        if attempt == 0:
            coords = find_element_via_uia(label)
            if coords:
                method = "Windows UIAutomation"
                uia_found = True

        if not coords:
            v_res = find_element_via_vision_details(label, screen_meta=current_meta)
            last_screen_summary = v_res.get("screen_summary", "")
            last_reason = v_res.get("reason", "")
            if v_res.get("found") and v_res.get("coordinates"):
                coords = v_res["coordinates"]
                method = "Visual Recognition"

        if not coords:
            if tried_coords and last_diff_changed:
                # We clicked once, local pixels around the target changed, and the target control is no longer present.
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Clicked '{label}'; the element disappeared after activation, confirming the action took effect.",
                    "coordinates": tried_coords[-1],
                    "attempts": len(tried_coords),
                }
            if attempt == 0 and prefer_vision:
                continue
            # If the user asked to click/double-click a file or PDF, resolve it on disk
            try:
                import tools
                f = tools._find_user_file(label)
                if f is not None and f.is_file():
                    if num_clicks >= 2 or any(w in lbl_clean for w in ("pdf", "doc", "file", "open")):
                        return tools.open_file(str(f))
            except Exception:
                pass
            detail = last_reason or (f"Screen shows: {last_screen_summary}" if last_screen_summary else "")
            return {
                "ok": False,
                "verified": False,
                "message": f"Could not find '{label}' on screen. {detail}".strip(),
                "screen_summary": last_screen_summary,
            }

        cx, cy = coords
        tried_coords.append((cx, cy))

        # Step 4: PERFORM ACTION (real press/hold/release on the focused target window)
        try:
            _physical_click(cx, cy, button=btn, clicks=num_clicks)
        except Exception as e:
            return {"ok": False, "verified": False, "message": f"Failed to click '{label}': {e}"}

        # Step 5: VERIFY RESULT (wait for the UI to react before judging)
        after_meta, diff_now = _wait_for_screen_change(current_meta, (cx, cy), timeout=2.0)
        last_diff_changed = bool(float(diff_now.get("local_changed_ratio", 0.0) or 0.0) >= 0.02 or diff_now.get("changed"))
        ver = verify_action_result(
            current_meta,
            after_meta,
            action_desc,
            (cx, cy),
            target_label=label,
            uia_existed_before=uia_found,
        )
        if ver["verified"]:
            act_word = "Double-clicked" if num_clicks == 2 else "Clicked"
            return {
                "ok": True,
                "verified": True,
                "message": f"{act_word} '{label}' at ({cx}, {cy}) using {method}. Verified: {ver['what_changed'] or ver['reason']}",
                "coordinates": (cx, cy),
                "window_before": before_title,
                "window_after": after_meta.get("active_window") or get_active_window_title(),
                "attempts": attempt + 1,
            }
        last_unverified = bool(ver.get("unverified"))
        last_reason = ver.get("reason") or "Click had no visible effect."

    if last_unverified and last_diff_changed:
        return {
            "ok": True,
            "verified": False,
            "message": f"Clicked '{label}' at {tried_coords[-1]}, and the UI changed locally, but the final state could not be independently confirmed ({last_reason}).",
            "coordinates": tried_coords[-1] if tried_coords else None,
            "screen_summary": last_screen_summary,
            "attempts": len(tried_coords),
        }

    return {
        "ok": False,
        "verified": False,
        "message": f"Clicked '{label}' at {tried_coords[-1]}, but could not verify that the action took effect ({last_reason}).",
        "coordinates": tried_coords[-1] if tried_coords else None,
        "screen_summary": last_screen_summary,
        "attempts": len(tried_coords),
    }

_last_notepad_write = {"text": "", "ts": 0.0}


def _read_notepad_text(notepad_hwnd: int) -> Optional[str]:
    """Read back the current text buffer from an open Notepad window via Win32 WM_GETTEXT or UIAutomation."""
    if not notepad_hwnd:
        return None
    # 1. Try Win32 FindWindowExW for classic 'Edit' or Windows 11 'RichEditD2DPT' child windows
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowExW.restype = wintypes.HWND
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.SendMessageW.restype = ctypes.c_long
        WM_GETTEXT = 0x000D
        WM_GETTEXTLENGTH = 0x000E

        def _find_edit_child(parent_h: int, depth: int = 0) -> int:
            if depth > 4 or not parent_h:
                return 0
            for cls_name in ("RichEditD2DPT", "Edit"):
                ch = user32.FindWindowExW(parent_h, None, cls_name, None)
                if ch:
                    return int(ch)
            child = user32.FindWindowExW(parent_h, None, None, None)
            while child:
                found = _find_edit_child(int(child), depth + 1)
                if found:
                    return found
                child = user32.FindWindowExW(parent_h, child, None, None)
            return 0

        edit_hwnd = _find_edit_child(notepad_hwnd)
        if edit_hwnd:
            length = int(user32.SendMessageW(edit_hwnd, WM_GETTEXTLENGTH, 0, 0))
            if length >= 0:
                buf_len = min(max(length + 4, 32), 262144)
                buf = ctypes.create_unicode_buffer(buf_len)
                user32.SendMessageW(edit_hwnd, WM_GETTEXT, buf_len, ctypes.addressof(buf))
                return buf.value
    except Exception:
        pass

    # 2. Try Windows UIAutomation Document/Edit pattern on Notepad window
    try:
        uia, m = _get_uia()
        root = uia.ElementFromHandle(notepad_hwnd)
        cond_doc = uia.CreatePropertyCondition(30003, 50030)   # UIA_DocumentControlTypeId
        cond_edit = uia.CreatePropertyCondition(30003, 50004)  # UIA_EditControlTypeId
        cond = uia.CreateOrCondition(cond_doc, cond_edit)
        elems = root.FindAll(4, cond)
        if elems and elems.Length > 0:
            for i in range(elems.Length):
                el = elems.GetElement(i)
                try:
                    tp = el.GetCurrentPattern(10014)  # UIA_TextPatternId
                    if tp:
                        tp_iface = tp.QueryInterface(m.IUIAutomationTextPattern)
                        doc_range = tp_iface.DocumentRange
                        if doc_range:
                            return str(doc_range.GetText(65536) or "")
                except Exception:
                    pass
                try:
                    vp = el.GetCurrentPattern(10002)  # UIA_ValuePatternId
                    if vp:
                        vp_iface = vp.QueryInterface(m.IUIAutomationValuePattern)
                        return str(vp_iface.CurrentValue or "")
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _read_focused_element_text() -> Optional[str]:
    """Read back text from the currently focused UI element via Windows UIAutomation."""
    try:
        uia, m = _get_uia()
        el = uia.GetFocusedElement()
        if not el:
            return None
        try:
            vp = el.GetCurrentPattern(10002)  # UIA_ValuePatternId
            if vp:
                vp_iface = vp.QueryInterface(m.IUIAutomationValuePattern)
                val = vp_iface.CurrentValue
                if val is not None:
                    return str(val)
        except Exception:
            pass
        try:
            tp = el.GetCurrentPattern(10014)  # UIA_TextPatternId
            if tp:
                tp_iface = tp.QueryInterface(m.IUIAutomationTextPattern)
                doc_range = tp_iface.DocumentRange
                if doc_range:
                    return str(doc_range.GetText(32768) or "")
        except Exception:
            pass
        try:
            lp = el.GetCurrentPattern(10018)  # UIA_LegacyIAccessiblePatternId
            if lp:
                lp_iface = lp.QueryInterface(m.IUIAutomationLegacyIAccessiblePattern)
                val = lp_iface.CurrentValue
                if val is not None:
                    return str(val)
        except Exception:
            pass
    except Exception:
        pass
    return None


def _text_contains_normalized(haystack: Optional[str], needle: str) -> bool:
    if haystack is None:
        return False
    h = " ".join(haystack.replace("\r\n", "\n").replace("\r", "\n").split())
    n = " ".join(needle.replace("\r\n", "\n").replace("\r", "\n").split())
    if not n:
        return True
    return n in h or (len(n) > 30 and n[:30] in h and n[-20:] in h)


def _force_foreground_window(hwnd: int) -> bool:
    """Reliably bring hwnd to the foreground on Windows 11, bypassing background process foreground lock."""
    if not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL

        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd == hwnd:
            return True

        fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        cur_tid = kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and cur_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            else:
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_tid, cur_tid, False)
        time.sleep(0.18)
        return True
    except Exception:
        return False


def _find_notepad_window(launch_if_missing: bool = False) -> int:
    """Find an open Notepad window handle, optionally launching Notepad if not open."""
    try:
        import ctypes
        from ctypes import wintypes
        import subprocess
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND

        hwnd = user32.FindWindowW("Notepad", None)
        if hwnd:
            return int(hwnd)

        if launch_if_missing:
            subprocess.Popen(["notepad.exe"], shell=False)
            deadline = time.monotonic() + 2.5
            while time.monotonic() < deadline:
                time.sleep(0.15)
                hwnd = user32.FindWindowW("Notepad", None)
                if hwnd:
                    time.sleep(0.2)
                    return int(hwnd)
    except Exception:
        pass
    return 0


def _focus_notepad_if_appropriate(force: bool = False) -> bool:
    """If a Notepad window is open and foreground is Nova, Desktop, or a PDF viewer (or force=True), focus Notepad."""
    try:
        notepad_hwnd = _find_notepad_window(launch_if_missing=force)
        if not notepad_hwnd:
            return False
        fg_title = (get_active_window_title() or "").lower()
        if force or ("notepad" not in fg_title and (
            not fg_title
            or fg_title in ("desktop", "nova")
            or any(k in fg_title for k in ("wps", ".pdf", "explorer", "file", "nova", "untitled"))
        )):
            _force_foreground_window(notepad_hwnd)
            return True
        return "notepad" in fg_title
    except Exception:
        return False


def write_in_notepad(text: str, open_if_closed: bool = True) -> Dict[str, Any]:
    """Write or paste text directly into the Windows Notepad application window on screen, with readback verification."""
    s = str(text or "").strip()
    if not s:
        return {"ok": False, "verified": False, "message": "No text provided to write into Notepad."}

    action_desc = f"Write into Notepad: '{s[:30]}...'" if len(s) > 30 else f"Write into Notepad: '{s}'"
    if not permissions.request_permission("mouse_keyboard", action_desc):
        return {"ok": False, "verified": False, "message": "Permission to write into Notepad was denied.", "permission_denied": True}

    try:
        import tools
        notepad_hwnd = _find_notepad_window(launch_if_missing=open_if_closed)
        if not notepad_hwnd:
            return {"ok": False, "verified": False, "message": "Notepad is not open and could not be launched."}

        text_before = _read_notepad_text(notepad_hwnd)

        # Duplicate guard: only skip pasting if we recently wrote the exact same text AND readback confirms it is actually in Notepad
        now = time.monotonic()
        if _last_notepad_write["text"] == s and (now - _last_notepad_write["ts"]) < 4.5:
            if _text_contains_normalized(text_before, s):
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Verified {len(s)} characters are already present in Notepad.",
                }

        _force_foreground_window(notepad_hwnd)
        normalized_text = s.replace("\r\n", "\n").replace("\n", "\r\n") + "\r\n"
        tools._clipboard_set_text(normalized_text)
        time.sleep(0.08)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.16)

        text_after = _read_notepad_text(notepad_hwnd)
        if text_after is not None:
            if _text_contains_normalized(text_after, s) and (text_before is None or len(text_after) >= len(text_before) + min(len(s), 3)):
                _last_notepad_write["text"] = s
                _last_notepad_write["ts"] = time.monotonic()
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Wrote and verified {len(s)} characters in Notepad.",
                }
            # Safe single retry if paste did not land in the Notepad editor
            _force_foreground_window(notepad_hwnd)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.18)
            text_after = _read_notepad_text(notepad_hwnd)
            if text_after is not None and _text_contains_normalized(text_after, s):
                _last_notepad_write["text"] = s
                _last_notepad_write["ts"] = time.monotonic()
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Wrote and verified {len(s)} characters in Notepad.",
                }
            return {
                "ok": False,
                "verified": False,
                "message": "Attempted to write into Notepad, but text readback showed the text did not appear.",
            }

        # Readback unavailable (e.g., custom editor or mocked environment)
        _last_notepad_write["text"] = s
        _last_notepad_write["ts"] = time.monotonic()
        return {
            "ok": True,
            "verified": False,
            "message": f"Sent {len(s)} characters to Notepad, but could not verify the text content via readback.",
        }
    except Exception as e:
        return {"ok": False, "verified": False, "message": f"Failed to write into Notepad: {e}"}


def type_text_into_ui(text: str, press_enter: bool = False) -> Dict[str, Any]:
    """Types text into the active UI element (auto-focusing open Notepad when appropriate) with UIA readback verification."""
    action_desc = f"Type text: '{text[:25]}...'" if len(text) > 25 else f"Type text: '{text}'"
    if not permissions.request_permission("mouse_keyboard", action_desc):
        return {"ok": False, "verified": False, "message": "Typing permission was denied.", "permission_denied": True}

    try:
        s = str(text)
        if not s:
            return {"ok": False, "verified": False, "message": "No text provided to type."}

        notepad_focused = _focus_notepad_if_appropriate(force=False)
        if notepad_focused and not press_enter:
            return write_in_notepad(s, open_if_closed=False)

        _focus_top_target_if_nova_focused(prefer_browser=False)
        title_before = get_active_window_title()
        text_before = _read_focused_element_text()

        # Duplicate guard: only skip if readback confirms the exact text is already present in the focused field
        now = time.monotonic()
        if _last_notepad_write["text"] == s.strip() and (now - _last_notepad_write["ts"]) < 3.5:
            if _text_contains_normalized(text_before, s.strip()):
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Verified {len(s)} characters are already present in the focused field.",
                }

        pasted = False
        if "\n" in s or len(s) > 40 or notepad_focused:
            try:
                import tools
                normalized_text = s.replace("\r\n", "\n").replace("\n", "\r\n")
                tools._clipboard_set_text(normalized_text)
                time.sleep(0.08)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.14)
                pasted = True
            except Exception:
                pasted = False

        if not pasted:
            pyautogui.write(s, interval=0.015)
            time.sleep(0.10)

        text_mid = _read_focused_element_text()
        if press_enter:
            pyautogui.press("enter")
            time.sleep(0.15)

        text_after = _read_focused_element_text()
        title_after = get_active_window_title()

        verified = False
        if _text_contains_normalized(text_mid, s.strip()) or _text_contains_normalized(text_after, s.strip()):
            verified = True
        elif text_before is not None and text_after is not None and text_after != text_before:
            verified = True
        elif press_enter and title_before and title_after and title_after != title_before:
            verified = True

        if verified:
            _last_notepad_write["text"] = s.strip()
            _last_notepad_write["ts"] = time.monotonic()
            return {
                "ok": True,
                "verified": True,
                "message": f"Typed and verified {len(s)} characters in the focused application.",
            }

        if text_before is not None and text_after is not None and not press_enter and text_after == text_before:
            return {
                "ok": False,
                "verified": False,
                "message": "Sent keystrokes, but UI Automation readback showed the target field text did not change.",
            }

        return {
            "ok": True,
            "verified": False,
            "message": f"Sent {len(s)} characters to the focused application, but text readback could not be confirmed for this control.",
        }
    except Exception as e:
        return {"ok": False, "verified": False, "message": f"Typing failed: {e}"}

def _focus_top_target_if_nova_focused(prefer_browser: bool = False) -> int:
    """If Nova's own window or the desktop shell is currently focused, focus the top application/browser window."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        fg = int(user32.GetForegroundWindow() or 0)
        fg_cls = _get_hwnd_class(fg)
        fg_title = _get_hwnd_title(fg).lower()
        is_nova_or_shell = (
            not fg
            or _hwnd_pid(fg) in _own_pids()
            or "nova" in fg_title
            or fg_cls in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd")
        )
        if prefer_browser:
            browsers = _browser_windows()
            if browsers:
                if fg not in browsers:
                    _force_foreground_window(browsers[0])
                return int(browsers[0])
        if is_nova_or_shell:
            targets = _browser_windows() if prefer_browser else _top_target_windows(3)
            if not targets:
                targets = _top_target_windows(3)
            if targets:
                _force_foreground_window(targets[0])
                return int(targets[0])
        return fg
    except Exception:
        return 0


# Browser tab management and YouTube player controls (modularized in desktop_agent_browser.py, re-exported here)
from desktop_agent_browser import (
    _clean_tab_target,
    _count_browser_tabs_uia,
    _find_browser_tab_via_uia,
    _inspect_youtube_player_state,
    _parse_timestamp_to_seconds,
    _parse_uia_duration_text,
    _send_ctrl_w,
    browser_tab_action,
    close_browser_tab,
    control_youtube_video,
)


def press_keyboard_hotkey(keys: str) -> Dict[str, Any]:
    """Presses a keyboard shortcut such as 'ctrl+c', 'ctrl+w', or 'win+r'."""
    action_desc = f"Press shortcut '{keys}'"
    if not permissions.request_permission("mouse_keyboard", action_desc):
        return {"ok": False, "verified": False, "message": "Keyboard shortcut permission was denied.", "permission_denied": True}

    try:
        key_list = [k.strip().lower() for k in re.split(r"[+, ]+", str(keys or "")) if k.strip()]
        if key_list in (["ctrl", "w"], ["control", "w"], ["ctrl", "f4"]):
            return close_browser_tab()
        if key_list in (["ctrl", "t"], ["control", "t"]):
            return browser_tab_action("new_tab")
        if key_list in (["ctrl", "shift", "t"], ["control", "shift", "t"]):
            return browser_tab_action("reopen_tab")
        if key_list in (["ctrl", "tab"], ["control", "tab"]):
            return browser_tab_action("next_tab")
        if key_list in (["ctrl", "shift", "tab"], ["control", "shift", "tab"]):
            return browser_tab_action("prev_tab")

        prefer_browser = key_list in (["ctrl", "r"], ["f5"])
        _focus_top_target_if_nova_focused(prefer_browser=prefer_browser)
        title_before = get_active_window_title()
        pyautogui.hotkey(*key_list)
        time.sleep(0.15)
        title_after = get_active_window_title()
        verified = bool(title_before and title_after and title_after != title_before)
        return {
            "ok": True,
            "verified": verified,
            "message": (
                f"Pressed hotkey {keys} (active window changed to '{title_after}')."
                if verified
                else f"Sent hotkey {keys} to '{title_after or title_before}', though its internal effect could not be independently confirmed."
            ),
        }
    except Exception as e:
        return {"ok": False, "verified": False, "message": f"Hotkey execution failed: {e}"}


def scroll_desktop(direction: str = "down", amount: int = 5) -> Dict[str, Any]:
    """Scrolls the active window, browser page, or document up or down and checks if the view visually scrolled."""
    action_desc = f"Scroll {direction} by {amount} steps"
    if not permissions.request_permission("mouse_keyboard", action_desc):
        return {"ok": False, "verified": False, "message": "Mouse and keyboard permission was denied.", "permission_denied": True}
    try:
        _focus_top_target_if_nova_focused(prefer_browser=False)
        before_meta = capture_screen_with_metadata(max_dimension=1280, quality=80)
        clicks = -amount * 120 if direction.lower() == "down" else amount * 120
        pyautogui.scroll(clicks)
        time.sleep(0.22)
        after_meta = capture_screen_with_metadata(max_dimension=1280, quality=80)
        diff = compute_visual_change(before_meta["bytes"], after_meta["bytes"], meta=after_meta)
        verified = bool(diff.get("changed"))
        return {
            "ok": True,
            "verified": verified,
            "message": (
                f"Scrolled {direction} by {amount} steps (verified screen content moved)."
                if verified
                else f"Sent scroll {direction} command ({amount} steps), but the visible page content did not move (it may already be at the end)."
            ),
        }
    except Exception as e:
        return {"ok": False, "verified": False, "message": f"Scroll failed: {e}"}
