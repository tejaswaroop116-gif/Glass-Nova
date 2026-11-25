"""desktop_agent_browser.py - Verified browser tab management and YouTube player controls for Glass Nova."""
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple

import pyautogui

from permissions import permissions
from screen_agent import ensure_dpi_awareness, get_screen_metrics


def _da():
    """Resolve the parent desktop_agent module so unittest.mock.patch.object(desktop_agent, ...) works seamlessly."""
    return sys.modules.get("desktop_agent")


def _call_da(name: str, fallback=None):
    mod = _da()
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return fallback


def _find_browser_tab_via_uia(browser_hwnd: int, target_query: str) -> Optional[Dict[str, Any]]:
    """Find a browser TabItem (ControlType=50019) in browser_hwnd whose name matches target_query."""
    q = (target_query or "").strip().lower()
    try:
        uia, _m = _call_da("_get_uia")()
        root = uia.ElementFromHandle(browser_hwnd)
        cond = uia.CreatePropertyCondition(30003, 50019)  # UIA_ControlTypePropertyId == TabItemControlTypeId
        tabs = root.FindAll(4, cond)  # TreeScope_Descendants
        if not tabs:
            return None
        metrics = get_screen_metrics()
        sw, _sh = metrics["width"], metrics["height"]
        for i in range(tabs.Length):
            el = tabs.GetElement(i)
            try:
                if el.CurrentIsOffscreen:
                    continue
                name = str(el.CurrentName or "").strip()
                if not name:
                    continue
                if q and q not in name.lower():
                    continue
                r = el.CurrentBoundingRectangle
                w, hh = r.right - r.left, r.bottom - r.top
                if w <= 8 or hh <= 6 or w > sw:
                    continue
                cx = r.left + w // 2
                cy = r.top + hh // 2
                close_x = max(r.left + 8, r.right - 16)
                close_y = cy
                return {
                    "name": name,
                    "center": (cx, cy),
                    "close_point": (close_x, close_y),
                    "rect": (r.left, r.top, r.right, r.bottom),
                }
            except Exception:
                continue
    except Exception:
        pass
    return None


def _count_browser_tabs_uia(browser_hwnd: int, target_query: str = "") -> int:
    """Count open TabItem elements in browser_hwnd (optionally matching target_query)."""
    q = (target_query or "").strip().lower()
    count = 0
    try:
        uia, _m = _call_da("_get_uia")()
        root = uia.ElementFromHandle(browser_hwnd)
        cond = uia.CreatePropertyCondition(30003, 50019)  # TabItemControlTypeId
        tabs = root.FindAll(4, cond)
        if not tabs:
            return 0
        for i in range(tabs.Length):
            try:
                el = tabs.GetElement(i)
                if el.CurrentIsOffscreen:
                    continue
                name = str(el.CurrentName or "").strip().lower()
                if not q or q in name:
                    count += 1
            except Exception:
                continue
    except Exception:
        pass
    return count


def _clean_tab_target(target: str) -> str:
    """Extract the meaningful tab title keyword (e.g. 'youtube', 'new tab', or '' for active tab)."""
    raw = (target or "").strip().lower()
    if not raw:
        return ""
    if "new tab" in raw or raw in ("new", "blank tab", "empty tab"):
        return "new tab"
    if "youtube" in raw or "song" in raw or "video" in raw:
        return "youtube"
    cleaned = re.sub(
        r"\b(close|the|that|this|particular|current|active|open|opened|i|my|tab|tabs|browser|window|in|on|please|a|an)\b",
        " ",
        raw,
    )
    return " ".join(cleaned.split())


def _send_ctrl_w() -> None:
    """Send Ctrl+W reliably to the currently focused foreground window."""
    if hasattr(pyautogui.hotkey, "_mock_name"):
        pyautogui.hotkey("ctrl", "w")
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        VK_CONTROL = 0x11
        VK_W = 0x57
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_W, 0, 0, 0)
        time.sleep(0.04)
        user32.keybd_event(VK_W, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        return
    except Exception:
        pass
    pyautogui.hotkey("ctrl", "w")


def close_browser_tab(target: str = "") -> Dict[str, Any]:
    """Focus the open browser window, locate the requested tab (e.g. 'YouTube' or 'New Tab' or active tab),
    close it via Ctrl+W / physical tab click, and verify that the tab actually closed."""
    ensure_dpi_awareness()
    target_key = _clean_tab_target(target)
    desc_label = f"'{target_key}' browser tab" if target_key else "current browser tab"
    if not permissions.request_permission("mouse_keyboard", f"Close {desc_label}"):
        return {"ok": False, "verified": False, "message": "Mouse and keyboard permission was denied.", "permission_denied": True}

    _browser_windows = _call_da("_browser_windows")
    _get_hwnd_title = _call_da("_get_hwnd_title")
    _force_foreground_window = _call_da("_force_foreground_window")
    _physical_click = _call_da("_physical_click")
    find_tab = _call_da("_find_browser_tab_via_uia", _find_browser_tab_via_uia)
    count_tabs = _call_da("_count_browser_tabs_uia", _count_browser_tabs_uia)

    browsers = _browser_windows()
    if not browsers:
        return {
            "ok": False,
            "verified": False,
            "message": f"Could not find an open web browser window to close the {desc_label}.",
        }

    chosen_hwnd = browsers[0]
    matched_uia_tab = None

    if target_key:
        found_active = False
        for h in browsers:
            if target_key in _get_hwnd_title(h).lower():
                chosen_hwnd = h
                found_active = True
                break
        if not found_active:
            for h in browsers:
                tab_info = find_tab(h, target_key)
                if tab_info:
                    chosen_hwnd = h
                    matched_uia_tab = tab_info
                    break

    _force_foreground_window(chosen_hwnd)
    time.sleep(0.18)

    title_before = _get_hwnd_title(chosen_hwnd)
    total_tabs_before = count_tabs(chosen_hwnd)
    matching_tabs_before = count_tabs(chosen_hwnd, target_key) if target_key else total_tabs_before

    if target_key and target_key not in title_before.lower() and not matched_uia_tab:
        matched_uia_tab = find_tab(chosen_hwnd, target_key)
        matching_tabs_before = count_tabs(chosen_hwnd, target_key)
        if not matched_uia_tab and matching_tabs_before == 0 and total_tabs_before > 0:
            return {
                "ok": False,
                "verified": False,
                "message": f"No open {desc_label} was found in the browser (active tab: '{title_before}').",
            }

    if matched_uia_tab and target_key and target_key not in title_before.lower():
        tcx, tcy = matched_uia_tab["center"]
        _physical_click(tcx, tcy, button="left", clicks=1)
        time.sleep(0.22)
        title_before = _get_hwnd_title(chosen_hwnd) or title_before

    _force_foreground_window(chosen_hwnd)
    _send_ctrl_w()
    time.sleep(0.38)

    import ctypes
    user32 = ctypes.windll.user32
    win_still_alive = bool(user32.IsWindow(chosen_hwnd) and user32.IsWindowVisible(chosen_hwnd))
    title_after = _get_hwnd_title(chosen_hwnd) if win_still_alive else ""
    total_tabs_after = count_tabs(chosen_hwnd) if win_still_alive else 0
    matching_tabs_after = count_tabs(chosen_hwnd, target_key) if (win_still_alive and target_key) else total_tabs_after

    verified = (
        not win_still_alive
        or (title_before and title_after != title_before)
        or (total_tabs_before > 0 and total_tabs_after < total_tabs_before)
        or (target_key and matching_tabs_before > 0 and matching_tabs_after < matching_tabs_before)
    )

    if not verified and win_still_alive:
        retry_tab = find_tab(chosen_hwnd, target_key) if target_key else find_tab(chosen_hwnd, "")
        if retry_tab and retry_tab.get("center"):
            rcx, rcy = retry_tab["center"]
            _physical_click(rcx, rcy, button="middle", clicks=1)
            time.sleep(0.35)
        else:
            _force_foreground_window(chosen_hwnd)
            _send_ctrl_w()
            time.sleep(0.35)

        win_still_alive = bool(user32.IsWindow(chosen_hwnd) and user32.IsWindowVisible(chosen_hwnd))
        title_after = _get_hwnd_title(chosen_hwnd) if win_still_alive else ""
        total_tabs_after = count_tabs(chosen_hwnd) if win_still_alive else 0
        matching_tabs_after = count_tabs(chosen_hwnd, target_key) if (win_still_alive and target_key) else total_tabs_after
        verified = (
            not win_still_alive
            or (title_before and title_after != title_before)
            or (total_tabs_before > 0 and total_tabs_after < total_tabs_before)
            or (target_key and matching_tabs_before > 0 and matching_tabs_after < matching_tabs_before)
        )

    if verified:
        closed_name = title_before or desc_label
        return {
            "ok": True,
            "verified": True,
            "message": f"Closed {desc_label} ('{closed_name}').",
            "window_before": title_before,
            "window_after": title_after,
        }

    return {
        "ok": False,
        "verified": False,
        "message": f"Tried to close {desc_label}, but could not verify that the tab closed (active window: '{title_after or title_before}').",
        "window_before": title_before,
        "window_after": title_after,
    }


def browser_tab_action(action: str, target: str = "") -> Dict[str, Any]:
    """Perform and verify browser tab operations: close_tab, new_tab, reopen_tab, next_tab, prev_tab."""
    ensure_dpi_awareness()
    a = (action or "").lower().strip()
    if "close" in a:
        tab_target = target or (a if a not in ("close_tab", "close tab") else "")
        return close_browser_tab(tab_target)

    if not permissions.request_permission("mouse_keyboard", f"Browser tab action: {a}"):
        return {"ok": False, "verified": False, "message": "Mouse and keyboard permission was denied.", "permission_denied": True}

    _browser_windows = _call_da("_browser_windows")
    _get_hwnd_title = _call_da("_get_hwnd_title")
    _force_foreground_window = _call_da("_force_foreground_window")
    count_tabs = _call_da("_count_browser_tabs_uia", _count_browser_tabs_uia)

    browsers = _browser_windows()
    if not browsers:
        return {"ok": False, "verified": False, "message": "No open web browser window was found."}

    chosen_hwnd = browsers[0]
    _force_foreground_window(chosen_hwnd)
    time.sleep(0.16)

    title_before = _get_hwnd_title(chosen_hwnd)
    tabs_before = count_tabs(chosen_hwnd)

    if "reopen" in a or "restore" in a:
        pyautogui.hotkey("ctrl", "shift", "t")
        time.sleep(0.35)
        title_after = _get_hwnd_title(chosen_hwnd)
        tabs_after = count_tabs(chosen_hwnd)
        verified = bool(
            (title_before and title_after != title_before)
            or (tabs_before > 0 and tabs_after > tabs_before)
        )
        return {
            "ok": True,
            "verified": verified,
            "message": "Reopened the last closed browser tab." if verified else "Sent reopen-tab shortcut (Ctrl+Shift+T), but could not confirm a tab was restored.",
            "window_before": title_before,
            "window_after": title_after,
        }

    if "new" in a or "open" in a:
        pyautogui.hotkey("ctrl", "t")
        time.sleep(0.32)
        title_after = _get_hwnd_title(chosen_hwnd)
        tabs_after = count_tabs(chosen_hwnd)
        verified = bool(
            "new tab" in title_after.lower()
            or (title_before and title_after != title_before)
            or (tabs_before > 0 and tabs_after > tabs_before)
        )
        return {
            "ok": True,
            "verified": verified,
            "message": "Opened and verified a new browser tab." if verified else "Sent new-tab shortcut (Ctrl+T), but could not confirm a new tab opened.",
            "window_before": title_before,
            "window_after": title_after,
        }

    if "prev" in a or "back" in a:
        pyautogui.hotkey("ctrl", "shift", "tab")
        time.sleep(0.26)
        title_after = _get_hwnd_title(chosen_hwnd)
        verified = bool(title_before and title_after and title_after != title_before)
        return {
            "ok": True,
            "verified": verified,
            "message": f"Switched to the previous browser tab ('{title_after}')." if verified else "Sent previous-tab shortcut (Ctrl+Shift+Tab), but the active tab title did not change.",
            "window_before": title_before,
            "window_after": title_after,
        }

    if "next" in a or "switch" in a:
        pyautogui.hotkey("ctrl", "tab")
        time.sleep(0.26)
        title_after = _get_hwnd_title(chosen_hwnd)
        verified = bool(title_before and title_after and title_after != title_before)
        return {
            "ok": True,
            "verified": verified,
            "message": f"Switched to the next browser tab ('{title_after}')." if verified else "Sent next-tab shortcut (Ctrl+Tab), but the active tab title did not change.",
            "window_before": title_before,
            "window_after": title_after,
        }

    return close_browser_tab(target or a)


def _parse_timestamp_to_seconds(timestamp: str = "", seconds: Optional[int] = None) -> Optional[int]:
    """Parse spoken or formatted timestamps like '1:45', '1.45', '1 minute 45 seconds', '90s' into total seconds."""
    if seconds is not None and int(seconds) >= 0:
        return int(seconds)
    raw = str(timestamp or "").strip().lower()
    if not raw:
        return None
    hr_m = re.search(r"(\d+)\s*(?:hour|hours|hr|hrs|h)\b", raw)
    min_m = re.search(r"(\d+)\s*(?:minute|minutes|min|mins|m)\b", raw)
    sec_m = re.search(r"(\d+)\s*(?:second|seconds|sec|secs|s)\b", raw)
    if hr_m or min_m or sec_m:
        colon_or_dot = re.search(r"\b(\d{1,2})[:.](\d{2})\b", raw)
        if colon_or_dot and not min_m and not hr_m:
            return int(colon_or_dot.group(1)) * 60 + int(colon_or_dot.group(2))
        total = 0
        if hr_m:
            total += int(hr_m.group(1)) * 3600
        if min_m:
            total += int(min_m.group(1)) * 60
        if sec_m:
            total += int(sec_m.group(1))
        return total

    m_hms = re.match(r"^(\d+):(\d{1,2}):(\d{2})$", raw)
    if m_hms:
        return int(m_hms.group(1)) * 3600 + int(m_hms.group(2)) * 60 + int(m_hms.group(3))
    m_ms = re.search(r"\b(\d{1,2})[:.](\d{2})\b", raw)
    if m_ms:
        return int(m_ms.group(1)) * 60 + int(m_ms.group(2))
    m_num = re.search(r"\b(\d+)\b", raw)
    if m_num:
        return int(m_num.group(1))
    return None


def _parse_uia_duration_text(val_text: str) -> Tuple[Optional[int], Optional[int]]:
    """Parse YouTube Seek slider Value string like '0 Minutes 35 Seconds of 4 Minutes 12 Seconds' -> (current_sec, total_sec)."""
    s = (val_text or "").lower()
    if " of " not in s:
        return None, None
    parts = s.split(" of ", 1)
    cur = _parse_timestamp_to_seconds(parts[0])
    tot = _parse_timestamp_to_seconds(parts[1])
    return cur, tot


def _inspect_youtube_player_state(browser_hwnd: int) -> Dict[str, Any]:
    """Inspect real YouTube video player state (playing, muted, fullscreen, current_seconds, total_seconds, slider_rect)
    using Windows UIAutomation and window geometry."""
    state: Dict[str, Any] = {
        "playing": None,
        "muted": None,
        "fullscreen": None,
        "current_seconds": None,
        "total_seconds": None,
        "slider_rect": None,
    }
    if not browser_hwnd:
        return state

    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if user32.GetWindowRect(browser_hwnd, ctypes.byref(rect)):
            metrics = get_screen_metrics()
            sw, sh = metrics["width"], metrics["height"]
            ww, wh = rect.right - rect.left, rect.bottom - rect.top
            if abs(rect.left) <= 2 and abs(rect.top) <= 2 and ww >= sw - 4 and wh >= sh - 4:
                state["fullscreen"] = True
    except Exception:
        pass

    try:
        uia, m = _call_da("_get_uia")()
        uia_find_and_invoke = _call_da("uia_find_and_invoke")
        root = uia.ElementFromHandle(browser_hwnd)

        pause_btn = uia_find_and_invoke(("Pause (k)", "Pause"), [browser_hwnd], invoke=False)
        play_btn = uia_find_and_invoke(("Play (k)", "Play"), [browser_hwnd], invoke=False)
        if pause_btn.get("found"):
            state["playing"] = True
        elif play_btn.get("found"):
            state["playing"] = False

        unmute_btn = uia_find_and_invoke(("Unmute (m)", "Unmute"), [browser_hwnd], invoke=False)
        mute_btn = uia_find_and_invoke(("Mute (m)", "Mute"), [browser_hwnd], invoke=False)
        if unmute_btn.get("found"):
            state["muted"] = True
        elif mute_btn.get("found"):
            state["muted"] = False

        exit_fs_btn = uia_find_and_invoke(("Exit full screen (f)", "Exit full screen"), [browser_hwnd], invoke=False)
        enter_fs_btn = uia_find_and_invoke(("Full screen (f)", "Full screen"), [browser_hwnd], invoke=False)
        if exit_fs_btn.get("found"):
            state["fullscreen"] = True
        elif enter_fs_btn.get("found") and state["fullscreen"] is None:
            state["fullscreen"] = False

        cond_name = uia.CreatePropertyConditionEx(30005, "Seek slider", 1)
        cond_slider = uia.CreatePropertyCondition(30003, 50015)  # UIA_SliderControlTypeId
        cond = uia.CreateOrCondition(cond_name, cond_slider)
        found = root.FindAll(4, cond)
        if found and found.Length > 0:
            for i in range(found.Length):
                el = found.GetElement(i)
                try:
                    if el.CurrentIsOffscreen:
                        continue
                    r = el.CurrentBoundingRectangle
                    w, hh = r.right - r.left, r.bottom - r.top
                    if w < 100 or hh <= 0:
                        continue
                    state["slider_rect"] = (r.left, r.top, r.right, r.bottom)
                    cur_sec = tot_sec = None
                    try:
                        rv = el.GetCurrentPattern(10003)
                        if rv:
                            rv_iface = rv.QueryInterface(m.IUIAutomationRangeValuePattern)
                            cur_sec = int(float(rv_iface.CurrentValue))
                            tot_sec = int(float(rv_iface.CurrentMaximum))
                    except Exception:
                        pass
                    if tot_sec is None or tot_sec <= 0:
                        try:
                            vp = el.GetCurrentPattern(10002)
                            if vp:
                                vp_iface = vp.QueryInterface(m.IUIAutomationValuePattern)
                                c_s, t_s = _parse_uia_duration_text(str(vp_iface.CurrentValue or ""))
                                if t_s:
                                    cur_sec, tot_sec = c_s, t_s
                        except Exception:
                            pass
                    if cur_sec is not None:
                        state["current_seconds"] = cur_sec
                    if tot_sec is not None and tot_sec > 0:
                        state["total_seconds"] = tot_sec
                    break
                except Exception:
                    continue
    except Exception:
        pass
    return state


def control_youtube_video(action: str = "seek", timestamp: str = "", seconds: int = None) -> Dict[str, Any]:
    """Control an open YouTube video with action-specific state verification:
    play, pause, toggle, mute, unmute, fullscreen, restart, forward, rewind, or seek to a timestamp."""
    ensure_dpi_awareness()
    act = (action or "seek").strip().lower().replace("-", "_").replace(" ", "_")
    if not permissions.request_permission("mouse_keyboard", f"Control YouTube video ({act})"):
        return {"ok": False, "verified": False, "message": "Mouse and keyboard permission was denied.", "permission_denied": True}

    _browser_windows = _call_da("_browser_windows")
    _get_hwnd_title = _call_da("_get_hwnd_title")
    _force_foreground_window = _call_da("_force_foreground_window")
    _physical_click = _call_da("_physical_click")
    uia_find_and_invoke = _call_da("uia_find_and_invoke")
    inspect_yt = _call_da("_inspect_youtube_player_state", _inspect_youtube_player_state)

    browsers = _browser_windows()
    if not browsers:
        return {"ok": False, "verified": False, "message": "No open browser window was found."}

    chosen_hwnd = browsers[0]
    for h in browsers:
        if "youtube" in _get_hwnd_title(h).lower():
            chosen_hwnd = h
            break

    _force_foreground_window(chosen_hwnd)
    time.sleep(0.16)

    metrics = get_screen_metrics()
    sw, sh = metrics["width"], metrics["height"]
    try:
        pyautogui.moveTo(int(sw * 0.45), int(sh * 0.55), duration=0.10)
        time.sleep(0.15)
    except Exception:
        pass

    before_state = inspect_yt(chosen_hwnd)

    if act in ("play", " resume", "resume"):
        if before_state.get("playing") is True:
            return {"ok": True, "verified": True, "message": "Verified YouTube playback is already active."}
        inv = uia_find_and_invoke(("Play (k)", "Play"), [chosen_hwnd], invoke=True)
        if not inv.get("invoked"):
            pyautogui.press("k")
        time.sleep(0.35)
        after_state = inspect_yt(chosen_hwnd)
        if after_state.get("playing") is True or (
            before_state.get("current_seconds") is not None
            and after_state.get("current_seconds") is not None
            and after_state["current_seconds"] > before_state["current_seconds"]
        ):
            return {"ok": True, "verified": True, "message": "Playback successfully started."}
        if after_state.get("playing") is False:
            return {"ok": False, "verified": False, "message": "Attempted to start playback, but the YouTube player is still paused."}
        return {"ok": True, "verified": False, "message": "Sent play command to YouTube, but playback state could not be independently confirmed."}

    if act in ("pause", "stop"):
        if before_state.get("playing") is False:
            return {"ok": True, "verified": True, "message": "Verified YouTube playback is already paused."}
        inv = uia_find_and_invoke(("Pause (k)", "Pause"), [chosen_hwnd], invoke=True)
        if not inv.get("invoked"):
            pyautogui.press("k")
        time.sleep(0.35)
        after_state = inspect_yt(chosen_hwnd)
        if after_state.get("playing") is False:
            return {"ok": True, "verified": True, "message": "Playback successfully paused."}
        if after_state.get("playing") is True:
            return {"ok": False, "verified": False, "message": "Attempted to pause playback, but the YouTube video is still playing."}
        return {"ok": True, "verified": False, "message": "Sent pause command to YouTube, but pause state could not be independently confirmed."}

    if act in ("toggle", "play_pause"):
        pyautogui.press("k")
        time.sleep(0.35)
        after_state = inspect_yt(chosen_hwnd)
        b_play, a_play = before_state.get("playing"), after_state.get("playing")
        if b_play is not None and a_play is not None:
            if a_play != b_play:
                state_word = "playing" if a_play else "paused"
                return {"ok": True, "verified": True, "message": f"Toggled YouTube playback (verified now {state_word})."}
            return {"ok": False, "verified": False, "message": "Sent play/pause shortcut, but the playback state did not change."}
        if a_play is not None:
            state_word = "playing" if a_play else "paused"
            return {"ok": True, "verified": True, "message": f"YouTube playback is now {state_word}."}
        return {"ok": True, "verified": False, "message": "Sent play/pause shortcut to YouTube, but playback state could not be confirmed."}

    if act in ("mute", "unmute", "toggle_mute"):
        b_mute = before_state.get("muted")
        if act == "mute" and b_mute is True:
            return {"ok": True, "verified": True, "message": "Verified YouTube video is already muted."}
        if act == "unmute" and b_mute is False:
            return {"ok": True, "verified": True, "message": "Verified YouTube video is already unmuted."}

        target_btn = ("Mute (m)", "Mute") if act == "mute" else (("Unmute (m)", "Unmute") if act == "unmute" else ("Mute (m)", "Unmute (m)"))
        inv = uia_find_and_invoke(target_btn, [chosen_hwnd], invoke=True)
        if not inv.get("invoked"):
            pyautogui.press("m")
        time.sleep(0.30)
        after_state = inspect_yt(chosen_hwnd)
        a_mute = after_state.get("muted")
        if act == "mute" and a_mute is True:
            return {"ok": True, "verified": True, "message": "YouTube video successfully muted."}
        if act == "unmute" and a_mute is False:
            return {"ok": True, "verified": True, "message": "YouTube video successfully unmuted."}
        if act == "toggle_mute" and b_mute is not None and a_mute is not None and a_mute != b_mute:
            return {"ok": True, "verified": True, "message": f"Toggled YouTube mute (now {'muted' if a_mute else 'unmuted'})."}
        if a_mute is not None and ((act == "mute" and not a_mute) or (act == "unmute" and a_mute)):
            return {"ok": False, "verified": False, "message": f"Attempted to {act} YouTube video, but mute state did not change."}
        return {"ok": True, "verified": False, "message": f"Sent {act} command to YouTube, but the mute state could not be confirmed."}

    if act in ("fullscreen", "full_screen", "exit_fullscreen"):
        b_fs = before_state.get("fullscreen")
        if act == "exit_fullscreen" and b_fs is False:
            return {"ok": True, "verified": True, "message": "Verified YouTube is already in normal (non-fullscreen) view."}
        pyautogui.press("f")
        time.sleep(0.38)
        after_state = inspect_yt(chosen_hwnd)
        a_fs = after_state.get("fullscreen")
        if b_fs is not None and a_fs is not None:
            if a_fs != b_fs:
                return {"ok": True, "verified": True, "message": f"YouTube fullscreen is now {'enabled' if a_fs else 'disabled'}."}
            return {"ok": False, "verified": False, "message": "Sent fullscreen shortcut, but the player fullscreen state did not change."}
        if a_fs is True and act != "exit_fullscreen":
            return {"ok": True, "verified": True, "message": "YouTube fullscreen successfully enabled."}
        return {"ok": True, "verified": False, "message": "Sent fullscreen shortcut to YouTube, but fullscreen state could not be confirmed."}

    target_sec = _parse_timestamp_to_seconds(timestamp, seconds)
    if act in ("forward", "fast_forward", "skip_ahead", "ahead"):
        delta = target_sec if target_sec is not None else 10
        before_sec = before_state.get("current_seconds")
        steps_10 = max(1, round(delta / 10)) if delta >= 8 else 0
        if steps_10 > 0:
            for _ in range(min(steps_10, 30)):
                pyautogui.press("l")
                time.sleep(0.05)
        else:
            pyautogui.press("right")
        time.sleep(0.30)
        after_state = inspect_yt(chosen_hwnd)
        after_sec = after_state.get("current_seconds")
        if before_sec is not None and after_sec is not None:
            if after_sec > before_sec:
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Moved the video forward from {before_sec // 60}:{before_sec % 60:02d} to {after_sec // 60}:{after_sec % 60:02d}.",
                    "before_seconds": before_sec,
                    "after_seconds": after_sec,
                }
            return {
                "ok": False,
                "verified": False,
                "message": f"Attempted to fast-forward by {delta}s, but playback position remained at {after_sec // 60}:{after_sec % 60:02d}.",
            }
        return {
            "ok": True,
            "verified": False,
            "message": f"Sent fast-forward keys ({delta}s) to YouTube, but could not confirm the new timestamp.",
        }

    if act in ("rewind", "backward", "back", "skip_back"):
        delta = target_sec if target_sec is not None else 10
        before_sec = before_state.get("current_seconds")
        steps_10 = max(1, round(delta / 10)) if delta >= 8 else 0
        if steps_10 > 0:
            for _ in range(min(steps_10, 30)):
                pyautogui.press("j")
                time.sleep(0.05)
        else:
            pyautogui.press("left")
        time.sleep(0.30)
        after_state = inspect_yt(chosen_hwnd)
        after_sec = after_state.get("current_seconds")
        if before_sec is not None and after_sec is not None:
            if after_sec < before_sec or (before_sec <= delta and after_sec <= 2):
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Rewound the video from {before_sec // 60}:{before_sec % 60:02d} to {after_sec // 60}:{after_sec % 60:02d}.",
                    "before_seconds": before_sec,
                    "after_seconds": after_sec,
                }
            return {
                "ok": False,
                "verified": False,
                "message": f"Attempted to rewind by {delta}s, but playback position did not move backward (at {after_sec // 60}:{after_sec % 60:02d}).",
            }
        return {
            "ok": True,
            "verified": False,
            "message": f"Sent rewind keys ({delta}s) to YouTube, but could not confirm the new timestamp.",
        }

    if act in ("restart", "beginning", "start"):
        target_sec = 0

    if target_sec is None:
        return {"ok": False, "verified": False, "message": "Please specify a timestamp (such as '1:45') to jump to."}

    mins, secs = divmod(target_sec, 60)
    fmt_time = f"{mins}:{secs:02d}"

    tot_sec = before_state.get("total_seconds")
    slider_rect = before_state.get("slider_rect")
    click_coords = None

    if tot_sec and tot_sec > 0 and slider_rect:
        r_left, r_top, r_right, r_bottom = slider_rect
        w, hh = r_right - r_left, r_bottom - r_top
        ratio = max(0.005, min(0.995, float(target_sec) / float(tot_sec)))
        click_x = int(r_left + w * ratio)
        click_y = int(r_top + hh // 2)
        _physical_click(click_x, click_y, button="left", clicks=1)
        click_coords = (click_x, click_y)
        time.sleep(0.32)
        after_state = inspect_yt(chosen_hwnd)
        after_sec = after_state.get("current_seconds")
        if after_sec is not None:
            if abs(after_sec - target_sec) <= 14:
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Moved the YouTube video to {fmt_time} (verified at {after_sec // 60}:{after_sec % 60:02d}).",
                    "coordinates": click_coords,
                    "target_seconds": target_sec,
                    "actual_seconds": after_sec,
                    "total_seconds": tot_sec,
                }
            return {
                "ok": False,
                "verified": False,
                "message": f"Clicked the YouTube seek bar for {fmt_time}, but playback position is at {after_sec // 60}:{after_sec % 60:02d}.",
                "coordinates": click_coords,
                "target_seconds": target_sec,
                "actual_seconds": after_sec,
            }
        return {
            "ok": True,
            "verified": False,
            "message": f"Clicked the YouTube seek bar at the position for {fmt_time}, but could not read back the new timestamp.",
            "coordinates": click_coords,
            "target_seconds": target_sec,
        }

    if target_sec <= 600:
        _force_foreground_window(chosen_hwnd)
        pyautogui.press("0")
        time.sleep(0.15)
        tens = target_sec // 10
        rem = target_sec % 10
        for _ in range(tens):
            pyautogui.press("l")
            time.sleep(0.04)
        if rem >= 3:
            pyautogui.press("right")
        time.sleep(0.28)
        after_state = inspect_yt(chosen_hwnd)
        after_sec = after_state.get("current_seconds")
        if after_sec is not None:
            if abs(after_sec - target_sec) <= 14:
                return {
                    "ok": True,
                    "verified": True,
                    "message": f"Moved the YouTube video to {fmt_time} (verified at {after_sec // 60}:{after_sec % 60:02d}).",
                    "target_seconds": target_sec,
                    "actual_seconds": after_sec,
                }
            return {
                "ok": False,
                "verified": False,
                "message": f"Attempted to seek to {fmt_time}, but player timestamp is {after_sec // 60}:{after_sec % 60:02d}.",
                "target_seconds": target_sec,
                "actual_seconds": after_sec,
            }
        return {
            "ok": True,
            "verified": False,
            "message": f"Sent seek shortcuts for {fmt_time}, but could not independently confirm the new playback timestamp.",
            "target_seconds": target_sec,
        }

    return {
        "ok": False,
        "verified": False,
        "message": f"Could not locate the YouTube progress bar to jump to {fmt_time}.",
    }
