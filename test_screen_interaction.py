"""Tests for Glass Nova screen understanding, coordinate accuracy, clicking, and verification.

Run:  .venv\\Scripts\\python.exe -B test_screen_interaction.py -v
"""
import io
import os
import sys
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-unit-tests")

import screen_agent
import desktop_agent
from permissions import permissions


def _make_jpeg(width: int, height: int, color=(30, 30, 40), rect=None) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    if rect is not None:
        draw = ImageDraw.Draw(img)
        draw.rectangle(rect, fill=(240, 80, 80))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


class CoordinateMappingTests(unittest.TestCase):
    def test_dpi_awareness_and_physical_metrics(self):
        screen_agent.ensure_dpi_awareness()
        m = screen_agent.get_screen_metrics()
        self.assertGreaterEqual(m["width"], 800)
        self.assertGreaterEqual(m["height"], 600)

    def test_normalized_to_screen_coords_resized_image(self):
        meta = {
            "screen_width": 1920,
            "screen_height": 1080,
            "image_width": 1600,
            "image_height": 900,
            "origin_x": 0,
            "origin_y": 0,
        }
        self.assertEqual(screen_agent.normalized_to_screen_coords(0, 0, meta), (0, 0))
        self.assertEqual(screen_agent.normalized_to_screen_coords(1000, 1000, meta), (1919, 1079))
        cx, cy = screen_agent.normalized_to_screen_coords(500, 500, meta)
        self.assertAlmostEqual(cx, 960, delta=2)
        self.assertAlmostEqual(cy, 540, delta=2)

    def test_image_pixels_to_screen_coords_with_scaling_and_offset(self):
        meta = {
            "screen_width": 1920,
            "screen_height": 1080,
            "image_width": 1280,
            "image_height": 720,
            "origin_x": 100,
            "origin_y": 50,
        }
        px, py = screen_agent.image_pixels_to_screen_coords(640, 360, meta)
        self.assertEqual((px, py), (1060, 590))


class ScreenCaptureAndDiffTests(unittest.TestCase):
    def test_capture_screen_with_metadata(self):
        meta = screen_agent.capture_screen_with_metadata(max_dimension=1600, quality=85)
        self.assertIn("bytes", meta)
        self.assertGreater(len(meta["bytes"]), 1000)
        im = Image.open(io.BytesIO(meta["bytes"]))
        self.assertEqual(im.size, (meta["image_width"], meta["image_height"]))
        self.assertGreater(meta["screen_width"], 0)
        self.assertGreater(meta["screen_height"], 0)

    def test_compute_visual_change_detects_local_and_identical(self):
        meta = {"screen_width": 1920, "screen_height": 1080, "origin_x": 0, "origin_y": 0}
        b1 = _make_jpeg(1600, 900)
        b2 = _make_jpeg(1600, 900, rect=(1300, 720, 1480, 790))
        same = screen_agent.compute_visual_change(b1, b1, focus_point=(1660, 900), meta=meta)
        self.assertFalse(same["changed"])
        diff = screen_agent.compute_visual_change(b1, b2, focus_point=(1660, 900), meta=meta)
        self.assertTrue(diff["changed"])


class UnifiedClickAndVerifyTests(unittest.TestCase):
    def setUp(self):
        permissions.grant_session("mouse_keyboard")
        permissions.grant_session("screen_view")

    def test_skip_ads_not_found_does_not_click(self):
        fake_meta = {
            "bytes": _make_jpeg(1600, 900),
            "screen_width": 1920,
            "screen_height": 1080,
            "image_width": 1600,
            "image_height": 900,
            "origin_x": 0,
            "origin_y": 0,
            "active_window": "YouTube - Google Chrome",
        }
        with patch.object(desktop_agent, "capture_screen_with_metadata", return_value=fake_meta), \
             patch.object(desktop_agent, "uia_find_and_invoke", return_value={"found": False}), \
             patch.object(desktop_agent, "query_vision_json", return_value={
                 "screen_summary": "YouTube video playing without ads",
                 "found": False,
                 "reason": "No Skip Ads button is visible",
             }), \
             patch.object(desktop_agent, "_physical_click") as mock_click:
            res = desktop_agent.click_element("Skip Ads")
        self.assertFalse(res["ok"])
        self.assertFalse(res.get("verified", True))
        mock_click.assert_not_called()

    def test_skip_ads_found_clicks_and_verifies(self):
        before_meta = {
            "bytes": _make_jpeg(1600, 900, rect=(1360, 720, 1520, 770)),
            "screen_width": 1920,
            "screen_height": 1080,
            "image_width": 1600,
            "image_height": 900,
            "origin_x": 0,
            "origin_y": 0,
            "active_window": "YouTube - Google Chrome",
        }
        after_meta = {
            "bytes": _make_jpeg(1600, 900),
            "screen_width": 1920,
            "screen_height": 1080,
            "image_width": 1600,
            "image_height": 900,
            "origin_x": 0,
            "origin_y": 0,
            "active_window": "YouTube - Google Chrome",
        }
        vision_locator = {
            "screen_summary": "YouTube ad playing with Skip Ads button bottom-right",
            "found": True,
            "matched_text": "Skip Ads",
            "box_2d": [800, 850, 856, 950],
            "center": [828, 900],
            "reason": "Visible Skip Ads button",
        }
        vision_verify = {
            "verified": True,
            "what_changed": "The ad disappeared and main video started playing.",
            "reason": "Skip Ads button is gone.",
        }
        with patch.object(desktop_agent, "capture_screen_with_metadata", side_effect=[before_meta, after_meta]), \
             patch.object(desktop_agent, "uia_find_and_invoke", return_value={"found": False}), \
             patch.object(desktop_agent, "query_vision_json", side_effect=[vision_locator, vision_verify]), \
             patch.object(desktop_agent, "_physical_click") as mock_click:
            res = desktop_agent.click_element("Skip Ads")
        self.assertTrue(res["ok"])
        self.assertTrue(res["verified"])
        expected_x, expected_y = screen_agent.normalized_to_screen_coords(828, 900, before_meta)
        mock_click.assert_called_once_with(expected_x, expected_y)

    def test_failed_verification_retries_and_reports_failure(self):
        same_meta = {
            "bytes": _make_jpeg(1600, 900),
            "screen_width": 1920,
            "screen_height": 1080,
            "image_width": 1600,
            "image_height": 900,
            "origin_x": 0,
            "origin_y": 0,
            "active_window": "Demo App",
        }
        loc = {"found": True, "matched_text": "Save", "box_2d": [100, 100, 140, 200], "center": [120, 150], "reason": "Save button"}
        no_change = {"verified": False, "what_changed": "Nothing changed", "reason": "Dialog still unchanged"}
        with patch.object(desktop_agent, "find_element_via_uia", return_value=None), \
             patch.object(desktop_agent, "capture_screen_with_metadata", return_value=same_meta), \
             patch.object(desktop_agent, "_wait_for_screen_change", return_value=(same_meta, {"changed": False})), \
             patch.object(desktop_agent, "query_vision_json", side_effect=[loc, no_change, loc, no_change]), \
             patch.object(desktop_agent, "_physical_click") as mock_click:
            res = desktop_agent.click_element("Save")
        self.assertFalse(res["ok"])
        self.assertFalse(res["verified"])
        self.assertEqual(mock_click.call_count, 2)

    def test_move_then_press_enter_converts_to_physical_click(self):
        import tools
        with patch("pyautogui.moveTo"), \
             patch("pyautogui.position", return_value=(1720, 890)), \
             patch.object(desktop_agent, "_physical_click") as mock_click:
            tools.mouse_control("move", x=1720, y=890)
            res = tools.press_key("enter")
        self.assertTrue(res["ok"])
        self.assertIn("Clicked left mouse button", res["message"])
        mock_click.assert_called_once_with(1720, 890, button="left", clicks=1)

    def test_media_ducker_attenuates_and_restores(self):
        from media_ducking import MediaDucker, LISTEN_FACTOR, DUCK_FACTOR

        class FakeVol:
            def __init__(self, v=1.0):
                self.v = v
            def GetMasterVolume(self):
                return self.v
            def SetMasterVolume(self, v, _):
                self.v = v

        class FakeProc:
            def name(self):
                return "chrome.exe"

        class FakeSession:
            ProcessId = 999999
            Process = FakeProc()
            SimpleAudioVolume = FakeVol(1.0)

        s = FakeSession()
        d = MediaDucker()
        with patch.object(d, "_sessions", return_value=[s]):
            d.set_listening(True)
            d._apply_factor(LISTEN_FACTOR)
            self.assertAlmostEqual(s.SimpleAudioVolume.v, LISTEN_FACTOR, places=2)
            d._apply_factor(DUCK_FACTOR)
            self.assertAlmostEqual(s.SimpleAudioVolume.v, DUCK_FACTOR, places=2)
            d._restore_all()
            self.assertAlmostEqual(s.SimpleAudioVolume.v, 1.0, places=2)

    def test_close_browser_tab_focuses_browser_and_verifies(self):
        import tools
        fake_meta = {
            "bytes": _make_jpeg(1600, 900),
            "screen_width": 1920,
            "screen_height": 1080,
            "image_width": 1600,
            "image_height": 900,
            "origin_x": 0,
            "origin_y": 0,
            "active_window": "Baahubali Songs - YouTube - Google Chrome",
        }
        titles = [
            "Baahubali Songs - YouTube - Google Chrome",
            "Baahubali Songs - YouTube - Google Chrome",
            "New Tab - Google Chrome",
        ]
        with patch.object(desktop_agent, "_browser_windows", return_value=[12345]), \
             patch.object(desktop_agent, "_force_foreground_window", return_value=True) as mock_focus, \
             patch.object(desktop_agent, "_get_hwnd_title", side_effect=lambda _h: titles.pop(0) if titles else "New Tab - Google Chrome"), \
             patch.object(desktop_agent, "_count_browser_tabs_uia", side_effect=[2, 2, 1, 1]), \
             patch.object(desktop_agent, "capture_screen_with_metadata", return_value=fake_meta), \
             patch("pyautogui.hotkey") as mock_hotkey, \
             patch("ctypes.windll.user32.IsWindow", return_value=1), \
             patch("ctypes.windll.user32.IsWindowVisible", return_value=1):
            res = tools.app_control("close", "YouTube tab")
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("verified"))
        mock_focus.assert_called_with(12345)
        mock_hotkey.assert_called_once_with("ctrl", "w")

    def test_youtube_timestamp_parsing_and_seek(self):
        self.assertEqual(desktop_agent._parse_timestamp_to_seconds("1.45 seconds"), 105)
        self.assertEqual(desktop_agent._parse_timestamp_to_seconds("1:45"), 105)
        self.assertEqual(desktop_agent._parse_timestamp_to_seconds("2 minutes 30 seconds"), 150)
        self.assertEqual(desktop_agent._parse_timestamp_to_seconds("45 seconds"), 45)

    def test_self_echo_and_micro_sound_suppression(self):
        import array
        import time
        import pc_assistant

        # 1. Self-echo transcription matcher catches Nova's own spoken output
        now = time.monotonic()
        self.assertTrue(
            pc_assistant._is_self_echo_transcription(
                "I've skipped the",
                "I've skipped the ad for you.",
                now,
            )
        )
        self.assertTrue(
            pc_assistant._is_self_echo_transcription(
                "Is there anything else you",
                "Is there anything else you need help with right now?",
                now,
            )
        )
        self.assertFalse(
            pc_assistant._is_self_echo_transcription(
                "Play Baahubali songs on YouTube",
                "Is there anything else you need help with right now?",
                now,
            )
        )

        # 2. RMS calculation distinguishes quiet micro-sounds from real speech
        quiet_samples = array.array("h", [15, -15] * 512).tobytes()
        speech_samples = array.array("h", [1800, -1800] * 512).tobytes()
        self.assertLess(pc_assistant._pcm16_rms(quiet_samples), pc_assistant.MIC_NOISE_GATE_RMS)
        self.assertGreater(pc_assistant._pcm16_rms(speech_samples), pc_assistant.MIC_NOISE_GATE_RMS)

    def test_mic_sensitivity_hud_and_auto_nearest(self):
        import pc_assistant
        from PySide6.QtWidgets import QApplication
        import ui

        # 1. Verify sensitivity <-> RMS conversion and AUTO NEAREST calibration
        res_68 = pc_assistant.mic_sensitivity_control("set", value=68)
        self.assertTrue(res_68["ok"])
        self.assertEqual(res_68["sensitivity"], 68)
        self.assertFalse(res_68["auto_nearest"])
        self.assertAlmostEqual(res_68["gate_rms"], 121.1, delta=5.0)

        res_auto = pc_assistant.mic_sensitivity_control("auto_nearest")
        self.assertTrue(res_auto["ok"])
        self.assertTrue(res_auto["auto_nearest"])
        self.assertGreaterEqual(res_auto["sensitivity"], 10)
        self.assertLessEqual(res_auto["sensitivity"], 95)

        # Restore default 68%
        pc_assistant.mic_sensitivity_control("set", value=68)

        # 2. Verify MicSensitivityWidget HUD component renders and updates cleanly
        app = QApplication.instance() or QApplication([])
        hud = ui.MicSensitivityWidget(initial_sensitivity=68, initial_auto=False)
        self.assertEqual(hud.pct_label.text(), "68%")
        hud.push_audio_level(240.0, 121.0, True)
        hud.sync_state(75, True)
        self.assertEqual(hud.pct_label.text(), "75%")
        self.assertTrue(hud.auto_nearest)
        self.assertIn("ACTIVE", hud.auto_btn.text())
        hud.close()

    def test_youtube_controls_verification_states(self):
        with patch.object(desktop_agent, "_browser_windows", return_value=[111]), \
             patch.object(desktop_agent, "_get_hwnd_title", return_value="Song - YouTube - Google Chrome"), \
             patch.object(desktop_agent, "_force_foreground_window", return_value=True), \
             patch.object(desktop_agent, "uia_find_and_invoke", return_value={"found": False, "invoked": False}), \
             patch("pyautogui.moveTo"), \
             patch("pyautogui.press"), \
             patch.object(desktop_agent, "_physical_click"):

            # 1. Play: confirmed (False -> True), failed (False -> False), unverified (None -> None)
            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"playing": False}, {"playing": True}
            ]):
                r_play = desktop_agent.control_youtube_video("play")
                self.assertEqual((r_play["ok"], r_play["verified"]), (True, True))

            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"playing": False}, {"playing": False}
            ]):
                r_play_fail = desktop_agent.control_youtube_video("play")
                self.assertEqual((r_play_fail["ok"], r_play_fail["verified"]), (False, False))

            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"playing": None}, {"playing": None}
            ]):
                r_play_unv = desktop_agent.control_youtube_video("play")
                self.assertEqual((r_play_unv["ok"], r_play_unv["verified"]), (True, False))

            # 2. Pause: confirmed (True -> False)
            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"playing": True}, {"playing": False}
            ]):
                r_pause = desktop_agent.control_youtube_video("pause")
                self.assertEqual((r_pause["ok"], r_pause["verified"]), (True, True))

            # 3. Mute/Unmute: confirmed (False -> True)
            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"muted": False}, {"muted": True}
            ]):
                r_mute = desktop_agent.control_youtube_video("mute")
                self.assertEqual((r_mute["ok"], r_mute["verified"]), (True, True))

            # 4. Fullscreen: confirmed (False -> True)
            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"fullscreen": False}, {"fullscreen": True}
            ]):
                r_fs = desktop_agent.control_youtube_video("fullscreen")
                self.assertEqual((r_fs["ok"], r_fs["verified"]), (True, True))

            # 5. Forward / Rewind: confirmed vs failed
            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"current_seconds": 30}, {"current_seconds": 40}
            ]):
                r_fwd = desktop_agent.control_youtube_video("forward", seconds=10)
                self.assertEqual((r_fwd["ok"], r_fwd["verified"]), (True, True))

            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"current_seconds": 30}, {"current_seconds": 30}
            ]):
                r_fwd_fail = desktop_agent.control_youtube_video("forward", seconds=10)
                self.assertEqual((r_fwd_fail["ok"], r_fwd_fail["verified"]), (False, False))

            # 6. Seek: confirmed (target 105s, actual 105s) vs failed (actual 10s)
            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"current_seconds": 10, "total_seconds": 240, "slider_rect": (100, 800, 1100, 820)},
                {"current_seconds": 105, "total_seconds": 240, "slider_rect": (100, 800, 1100, 820)},
            ]):
                r_seek = desktop_agent.control_youtube_video("seek", timestamp="1:45")
                self.assertEqual((r_seek["ok"], r_seek["verified"]), (True, True))

            with patch.object(desktop_agent, "_inspect_youtube_player_state", side_effect=[
                {"current_seconds": 10, "total_seconds": 240, "slider_rect": (100, 800, 1100, 820)},
                {"current_seconds": 10, "total_seconds": 240, "slider_rect": (100, 800, 1100, 820)},
            ]):
                r_seek_fail = desktop_agent.control_youtube_video("seek", timestamp="1:45")
                self.assertEqual((r_seek_fail["ok"], r_seek_fail["verified"]), (False, False))

    def test_generic_screen_change_alone_is_not_verified(self):
        m1 = {
            "bytes": _make_jpeg(1600, 900),
            "screen_width": 1920, "screen_height": 1080,
            "image_width": 1600, "image_height": 900,
            "origin_x": 0, "origin_y": 0,
            "active_window": "App Window",
        }
        m2 = {
            "bytes": _make_jpeg(1600, 900, rect=(200, 200, 600, 500)),
            "screen_width": 1920, "screen_height": 1080,
            "image_width": 1600, "image_height": 900,
            "origin_x": 0, "origin_y": 0,
            "active_window": "App Window",
        }
        with patch.object(desktop_agent, "query_vision_json", return_value=None):
            ver = desktop_agent.verify_action_result(m1, m2, "Click Settings button", (300, 300))
        self.assertFalse(ver["verified"])
        self.assertTrue(ver.get("unverified"))

    def test_notepad_and_ui_text_readback_verification(self):
        import tools
        desktop_agent._last_notepad_write["text"] = ""
        desktop_agent._last_notepad_write["ts"] = 0.0

        with patch.object(desktop_agent, "_find_notepad_window", return_value=222), \
             patch.object(desktop_agent, "_force_foreground_window", return_value=True), \
             patch.object(tools, "_clipboard_set_text"), \
             patch("pyautogui.hotkey"):

            # 1. Readback confirms text appeared -> verified=True
            with patch.object(desktop_agent, "_read_notepad_text", side_effect=["", "Hello Glass Nova\r\n"]):
                r_ok = desktop_agent.write_in_notepad("Hello Glass Nova")
                self.assertEqual((r_ok["ok"], r_ok["verified"]), (True, True))

            # 2. Readback shows text did NOT appear even after retry -> ok=False, verified=False
            desktop_agent._last_notepad_write["text"] = ""
            with patch.object(desktop_agent, "_read_notepad_text", side_effect=["", "", ""]):
                r_fail = desktop_agent.write_in_notepad("Missing text")
                self.assertEqual((r_fail["ok"], r_fail["verified"]), (False, False))

            # 3. Readback unavailable (None) -> ok=True, verified=False
            desktop_agent._last_notepad_write["text"] = ""
            with patch.object(desktop_agent, "_read_notepad_text", return_value=None):
                r_unv = desktop_agent.write_in_notepad("Unverifiable text")
                self.assertEqual((r_unv["ok"], r_unv["verified"]), (True, False))

    def test_tools_window_tab_and_process_verification(self):
        import tools
        import pc_assistant

        # 1. _normalize_tool_result never converts missing 'verified' into True
        norm_unv = pc_assistant._normalize_tool_result("custom_tool", {"ok": True, "message": "Attempted action"})
        self.assertEqual((norm_unv["ok"], norm_unv["verified"], norm_unv["verification_status"]), (True, False, "unverified"))
        norm_ok = pc_assistant._normalize_tool_result("custom_tool", {"ok": True, "verified": True, "message": "Confirmed"})
        self.assertEqual((norm_ok["ok"], norm_ok["verified"], norm_ok["verification_status"]), (True, True, "confirmed"))
        norm_fail = pc_assistant._normalize_tool_result("custom_tool", {"ok": False, "message": "Failed"})
        self.assertEqual((norm_fail["ok"], norm_fail["verified"], norm_fail["verification_status"]), (False, False, "failed"))

        # 2. Browser tab actions: new_tab verified vs unverified
        with patch.object(desktop_agent, "_browser_windows", return_value=[333]), \
             patch.object(desktop_agent, "_force_foreground_window", return_value=True), \
             patch("pyautogui.hotkey"):
            with patch.object(desktop_agent, "_get_hwnd_title", side_effect=["Google - Chrome", "New Tab - Chrome"]), \
                 patch.object(desktop_agent, "_count_browser_tabs_uia", side_effect=[1, 2]):
                r_tab = tools.window_control("new_tab")
                self.assertEqual((r_tab["ok"], r_tab["verified"]), (True, True))

            with patch.object(desktop_agent, "_get_hwnd_title", side_effect=["Google - Chrome", "Google - Chrome"]), \
                 patch.object(desktop_agent, "_count_browser_tabs_uia", side_effect=[1, 1]):
                r_tab_unv = tools.window_control("next_tab")
                self.assertEqual((r_tab_unv["ok"], r_tab_unv["verified"]), (True, False))

        # 3. app_control("close", ...): verifies process termination
        with patch.object(tools, "_close_windows_by_title", return_value=1), \
             patch.object(tools.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            with patch.object(tools, "_find_matching_app_pids", side_effect=[[999], [], []]), \
                 patch.object(tools, "_count_matching_windows", side_effect=[1, 0]):
                r_close_ok = tools.app_control("close", "notepad")
                self.assertEqual((r_close_ok["ok"], r_close_ok["verified"]), (True, True))

            with patch.object(tools, "_find_matching_app_pids", return_value=[999]), \
                 patch.object(tools, "_count_matching_windows", return_value=1), \
                 patch.object(tools.time, "sleep"):
                r_close_fail = tools.app_control("close", "notepad")
                self.assertEqual((r_close_fail["ok"], r_close_fail["verified"]), (False, False))


if __name__ == "__main__":
    unittest.main()
