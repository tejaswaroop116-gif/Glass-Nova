"""Comprehensive test suite for Glass Nova Updates 1 & 2:
1. Permissions Management
2. Wake Word Listener
3. Screen, Document & Desktop Agents
4. UI Integration (Tray, Badges, Handover, Permission Dialog)
"""
import sys, os, time, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def test_permissions():
    print("--- 1. Testing permissions.py ---")
    from permissions import PermissionManager, CATEGORIES
    pm = PermissionManager()
    pm.revoke_all()

    # Verify categories exist
    assert "screen_view" in CATEGORIES
    assert "mouse_keyboard" in CATEGORIES
    assert "document_access" in CATEGORIES

    # Verify initially not granted
    assert not pm.is_granted("screen_view")

    # Set callback that grants for session
    def auto_grant(req_id, category, details):
        return "allow_session"

    pm.set_prompt_callback(auto_grant)
    res = pm.request_permission("screen_view", "Testing screen view")
    assert res is True
    assert pm.request("mouse_keyboard", "Testing request alias") is True
    assert pm.is_granted("screen_view")
    assert pm.is_granted("mouse_keyboard")

    # Revoke
    pm.revoke("screen_view")
    assert not pm.is_granted("screen_view")
    # Verify unknown category does not silently grant screen_view
    pm.revoke_all()
    unknown_granted = pm.grant_by_voice("unknown_category_xyz")
    assert unknown_granted == [], f"Expected empty list for unknown category, got {unknown_granted}"
    assert not pm.is_granted("screen_view")

    # Verify voice grant for camera, mouse, and combined phrases
    cam_granted = pm.grant_by_voice("camera")
    assert "screen_view" in cam_granted and "media_access" in cam_granted
    assert pm.is_granted("screen_view") and pm.is_granted("media_access")
    pm.revoke_all()

    combo_granted = pm.grant_by_voice("access the camera or mouse")
    assert "screen_view" in combo_granted and "mouse_keyboard" in combo_granted
    assert pm.is_granted("screen_view") and pm.is_granted("mouse_keyboard")
    pm.revoke_all()
    print("Permissions test passed!")

def test_wake_word():
    print("--- 2. Testing wake_word.py ---")
    from wake_word import WakeWordListener
    detected = False
    def on_det():
        nonlocal detected
        detected = True

    listener = WakeWordListener(on_detected=on_det)
    assert listener.status == "uninitialized"

    # Test keyword matching helper
    assert listener._check_wake_text("hey nova") is True
    # Cooldown prevents immediate re-trigger
    assert listener._check_wake_text("hey nova") is False
    # After resetting cooldown
    listener._last_trigger_time = 0.0
    assert listener._check_wake_text("nova what is the weather") is True
    assert listener._check_wake_text("hello assistant") is False
    print("Wake word test passed!")

def test_screen_agent():
    print("--- 3. Testing screen_agent.py ---")
    from screen_agent import capture_screen_jpeg, get_active_window_title
    title = get_active_window_title()
    print("Active window detected:", title)

    jpeg_bytes = capture_screen_jpeg()
    assert jpeg_bytes is not None and len(jpeg_bytes) > 0
    print(f"Captured screen JPEG: {len(jpeg_bytes)} bytes")
    print("Screen agent test passed!")

def test_desktop_agent():
    print("--- 4. Testing desktop_agent.py ---")
    from desktop_agent import find_element_via_uia, scroll_desktop
    from tools import open_app
    elem = find_element_via_uia("NonExistentFakeButtonXYZ")
    assert elem is None

    # Test scroll, play_youtube & hand_gesture_control tool definitions and WebSocket resilience helpers
    import numpy as np
    from pc_assistant import TOOLS, _is_transient_ws_error, _build_live_config
    from hand_gesture import (
        GestureMouse, SimpleLandmark, gesture_manager,
        MODEL_PATH, create_best_hand_tracker, mp
    )
    assert "scroll_desktop" in TOOLS
    assert "play_youtube" in TOOLS
    assert "hand_gesture_control" in TOOLS
    assert os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 7_000_000
    t_mode, tracker = create_best_hand_tracker(MODEL_PATH)
    assert t_mode in ("mediapipe", "opencv_dnn")
    dummy_bgr = np.zeros((480, 640, 3), dtype=np.uint8)
    if t_mode == "mediapipe":
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=dummy_bgr)
        det = tracker.detect_for_video(mp_img, 1)
        assert hasattr(det, "hand_landmarks")
    else:
        hands = tracker.detect(dummy_bgr)
        assert isinstance(hands, list)
    if hasattr(tracker, "close"):
        tracker.close()
    assert _is_transient_ws_error(RuntimeError("sent 1011 (internal error) keepalive ping timeout; no close frame received")) is True
    assert _is_transient_ws_error(ValueError("invalid tool argument")) is False
    cfg = _build_live_config({"handle": "test_handle_123", "use_extras": True})
    assert cfg is not None

    # Test GestureMouse state machine (fist clutch & pointing)
    gm = GestureMouse()
    fist_hand = [SimpleLandmark(0.5, 0.5) for _ in range(21)]
    fist_hand[0] = SimpleLandmark(0.5, 0.7)
    fist_hand[9] = SimpleLandmark(0.5, 0.5)
    gm.update([fist_hand], 1.0, 640, 480)
    assert gm.status == "REST (fist)"
    assert gesture_manager.get_status()["ok"] is True
    from pc_assistant import hand_gesture_control
    assert hand_gesture_control(action="status")["ok"] is True

    # Test safe File Manager / File Explorer close (must NEVER taskkill explorer.exe)
    import tools
    from permissions import permissions
    from unittest.mock import patch
    orig_cb = permissions.prompt_callback
    permissions.set_prompt_callback(lambda cat, desc, det: "allow_session" if cat == "mouse_keyboard" else "deny")
    try:
        with patch.object(tools, "_close_file_explorer_windows", return_value=1) as mock_close_exp, \
             patch.object(tools.subprocess, "run") as mock_sub_run:
            res_fm = tools.app_control("close", "file manager")
            assert res_fm["ok"] is True
            res_exp = tools.app_control("close", "explorer")
            assert res_exp["ok"] is True
            assert mock_close_exp.call_count == 2
            mock_sub_run.assert_not_called()

        # Protected system processes must never be killed by app_control
        res_prot = tools.app_control("close", "dwm.exe")
        assert res_prot["ok"] is False and "protected" in res_prot["message"].lower()
    finally:
        permissions.revoke_all()
        permissions.set_prompt_callback(orig_cb)

    # Test fuzzy file resolution ('machine learning PDF' -> 'MACHINELEARNING.pdf'), open_file, and cursor click
    import tempfile
    from desktop_agent import _normalize_ui_label
    assert _normalize_ui_label("machine learning PDF")[1] == _normalize_ui_label("MACHINELEARNING.pdf")[1]
    assert "open_file" in TOOLS

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        fake_pdf = tmp_path / "MACHINELEARNING.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 dummy")
        tools._last_opened_folder = tmp_path

        resolved_f = tools._find_user_file("machine learning PDF")
        assert resolved_f == fake_pdf, f"Expected {fake_pdf}, got {resolved_f}"
        # Pronoun resolution ('it' / 'the pdf')
        assert tools._find_user_file("it") == fake_pdf

        # Verify open_file fails closed without document_access, then succeeds with explicit permission
        assert tools.open_file("machine learning PDF").get("permission_denied") is True
        orig_doc_cb = permissions.prompt_callback
        permissions.set_prompt_callback(lambda cat, desc, det: "allow_session" if cat == "document_access" else "deny")
        try:
            with patch("os.startfile") as mock_startfile:
                res_open = tools.open_file("machine learning PDF")
                assert res_open["ok"] is True
                mock_startfile.assert_called_once_with(str(fake_pdf))

            with patch.object(tools, "_resolve_app_executable", return_value=r"C:\WPS\wpspdf.exe"), \
                 patch.object(tools.subprocess, "Popen") as mock_popen:
                res_wps = tools.open_file("it", app="WS Office")
                assert res_wps["ok"] is True
                mock_popen.assert_called_once_with([r"C:\WPS\wpspdf.exe", str(fake_pdf)], shell=False)
        finally:
            permissions.revoke_all()
            permissions.set_prompt_callback(orig_doc_cb)

        from document_agent import find_target_document, _should_auto_write_to_notepad
        assert find_target_document("the whole PDF") == fake_pdf
        assert find_target_document(None) == fake_pdf
        assert _should_auto_write_to_notepad("Read out the whole PDF and write the five module names on notepad") is True
        assert _should_auto_write_to_notepad("Summarize this PDF") is False

    # Test AudioPlayer & LiveManager non-blocking architecture and write_in_notepad tool
    from pc_assistant import AudioPlayer, LiveManager
    assert "write_in_notepad" in TOOLS
    ap = AudioPlayer(lambda: None)
    ap.close()

    # Test click_mouse at current cursor position without x, y (with explicit mocked permission callback)
    import pyautogui
    orig_cb2 = permissions.prompt_callback
    permissions.set_prompt_callback(lambda cat, desc, det: "allow_session" if cat == "mouse_keyboard" else "deny")
    try:
        with patch.object(pyautogui, "click") as mock_click:
            res_cur = tools.click_mouse()
            assert res_cur["ok"] is True
            assert mock_click.call_count == 1
    finally:
        permissions.revoke_all()
        permissions.set_prompt_callback(orig_cb2)
    print("Desktop agent, hand_gesture_control, play_youtube, open_file, cursor click & safe File Explorer close test passed!")

def test_ui_components():
    print("--- 5. Testing ui.py integration ---")
    from PySide6.QtWidgets import QApplication
    from ui import Nova, PermissionDialog, is_run_at_startup

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    assert not app.quitOnLastWindowClosed(), "quitOnLastWindowClosed must be False for system tray background operation"
    win = Nova()

    # Test top bar indicators & wake badge
    assert hasattr(win, 'wake_badge') and win.wake_badge is not None
    assert hasattr(win, 'screen_badge')
    assert hasattr(win, 'control_badge')
    assert hasattr(win, 'wake_btn')
    assert hasattr(win, 'tasks_view')
    assert hasattr(win, 'memories_view')
    assert win.wake_btn.text().startswith('Wake Word ("Hey Nova")')
    assert win.screen_badge.isHidden()
    assert win.control_badge.isHidden()

    # Test indicator events
    win._on_event({"t": "indicator", "kind": "screen", "active": True})
    assert not win.screen_badge.isHidden()
    win._on_event({"t": "indicator", "kind": "screen", "active": False})
    assert win.screen_badge.isHidden()

    win._on_event({"t": "indicator", "kind": "control", "active": True})
    assert not win.control_badge.isHidden()
    win._on_event({"t": "indicator", "kind": "control", "active": False})
    assert win.control_badge.isHidden()

    assert hasattr(win, 'gesture_badge')
    assert hasattr(win, 'sidebar_gesture_btn')
    assert hasattr(win, 'gesture_btn')
    win._on_event({"t": "gesture", "status": "running"})
    assert not win.gesture_badge.isHidden()
    win._on_event({"t": "gesture", "status": "stopped"})
    assert win.gesture_badge.isHidden()

    # Test system tray
    assert hasattr(win, 'tray_icon')
    assert win.tray_icon is not None
    assert win.tray_icon.toolTip().startswith("Nova")

    # Test permission dialog creation & hands-free voice resolution
    dlg = PermissionDialog("mouse_keyboard", "Testing permission dialog", win)
    assert dlg.category == "mouse_keyboard"
    win._active_perm_dialogs["req_voice_1"] = dlg
    win._on_event({"t": "perm_voice_resolved", "id": "req_voice_1", "category": "mouse_keyboard", "decision": "allow_session"})
    assert dlg.selected_decision == "allow_session"
    from pc_assistant import _parse_voice_perm_decision
    assert _parse_voice_perm_decision("allow for this session") == "allow_session"
    assert _parse_voice_perm_decision("give permission") == "allow_once"
    assert _parse_voice_perm_decision("allow once") == "allow_once"
    assert _parse_voice_perm_decision("okay") is None
    assert _parse_voice_perm_decision("yes") is None
    assert _parse_voice_perm_decision("no deny") == "deny"

    # Test wake-word pause and resume
    win.toggle_wake_word()
    assert win.wake_enabled is False
    win.toggle_wake_word()
    assert win.wake_enabled is True

    # Test clean quit
    win.quit_app()
    print("UI integration test passed!")

if __name__ == "__main__":
    test_permissions()
    test_wake_word()
    test_screen_agent()
    test_desktop_agent()
    test_ui_components()
    print("\nALL 5 INTEGRATION TESTS PASSED SUCCESSFULLY!")
