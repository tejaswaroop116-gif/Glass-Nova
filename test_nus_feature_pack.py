"""Unit and integration tests for the Nova × NŪS Feature Pack:
1. CompanionBridge (nova_companion_bridge.py)
2. ScreenPointingService & RegionSelector (nova_screen_pointing.py)
3. Walkthrough & WalkthroughStep (nova_walkthrough.py)
4. WorkflowRecorder, WorkflowStore, WorkflowPlayer (nova_workflows.py)
5. NovaActionRouter, ApprovedActionBroker, validate_proposed_action (nova_action_router.py)
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-unit-tests")

from nova_companion_bridge import CompanionBridge
from nova_screen_pointing import ScreenPointingService
from nova_walkthrough import Walkthrough, WalkthroughStep, walkthrough_from_plan
from nova_workflows import (
    Workflow,
    WorkflowStep,
    WorkflowRecorder,
    WorkflowStore,
    WorkflowPlayer,
    ALLOWLISTED_WORKFLOW_ACTIONS,
)
from nova_action_router import (
    NovaActionRouter,
    ApprovedActionBroker,
    validate_proposed_action,
)


class CompanionBridgeTests(unittest.TestCase):
    def test_show_hide_toggle_and_wake_word_restore(self):
        events = []
        bridge = CompanionBridge(
            show_companion=lambda: events.append("show"),
            hide_companion=lambda: events.append("hide"),
            bring_main_window_forward=lambda: events.append("forward"),
        )
        self.assertFalse(bridge.visible)
        bridge.show()
        self.assertTrue(bridge.visible)
        bridge.hide()
        self.assertFalse(bridge.visible)
        bridge.toggle()
        self.assertTrue(bridge.visible)

        class DummyWakeListener:
            on_detected = None

        listener = DummyWakeListener()
        bridge.attach_wake_listener(listener)
        listener.on_detected()
        self.assertEqual(events, ["show", "hide", "show", "show", "forward"])


class ScreenPointingTests(unittest.TestCase):
    def test_permission_denial_prevents_capture_and_selector(self):
        captured = []
        analyzed = []
        results = []

        service = ScreenPointingService(
            request_screen_permission=lambda _desc: False,
            capture_region_jpeg=lambda rect: (captured.append(rect) or b"jpeg"),
            analyze_region=lambda b, q, r: (analyzed.append((b, q, r)) or "answer"),
        )
        selector = service.select_and_explain("What is this button?", results.append)
        self.assertIsNone(selector)
        self.assertEqual(captured, [])
        self.assertEqual(analyzed, [])
        self.assertEqual(results, ["Screen access was not approved."])

    def test_capture_region_jpeg_captures_only_selected_rectangle(self):
        from screen_agent import capture_region_jpeg
        from PIL import Image
        import io
        data = capture_region_jpeg((20, 20, 160, 120))
        self.assertTrue(data and len(data) > 100)
        im = Image.open(io.BytesIO(data))
        self.assertEqual(im.size, (160, 120))


class WalkthroughTests(unittest.TestCase):
    def test_walkthrough_navigation_next_back_pause_stop(self):
        wt = walkthrough_from_plan(
            "Configure Microphone",
            [
                {"title": "Step 1", "instruction": "Open Settings", "target_label": "Settings"},
                {"title": "Step 2", "instruction": "Adjust Mic Sensitivity", "caution": "Do not mute"},
                {"title": "Step 3", "instruction": "Enable Auto Nearest"},
            ],
        )
        self.assertTrue(wt.active)
        self.assertEqual(wt.progress, (1, 3))
        self.assertEqual(wt.current.title, "Step 1")

        # Back at start stays at step 1
        self.assertEqual(wt.back().title, "Step 1")
        self.assertEqual(wt.progress, (1, 3))

        # Next advances to step 2
        self.assertEqual(wt.next().title, "Step 2")
        self.assertEqual(wt.progress, (2, 3))

        # Pause and resume
        wt.pause()
        self.assertTrue(wt.paused)
        wt.resume()
        self.assertFalse(wt.paused)

        # Back returns to step 1, Next -> 2 -> 3 -> finished
        self.assertEqual(wt.back().title, "Step 1")
        self.assertEqual(wt.next().title, "Step 2")
        self.assertEqual(wt.next().title, "Step 3")
        self.assertIsNone(wt.next())
        self.assertFalse(wt.active)
        self.assertIsNone(wt.current)

    def test_walkthrough_stop_immediately_deactivates(self):
        wt = Walkthrough("Test", [WalkthroughStep("S1", "Do X"), WalkthroughStep("S2", "Do Y")])
        wt.stop()
        self.assertFalse(wt.active)
        self.assertIsNone(wt.current)
        self.assertIsNone(wt.next())


class WorkflowTests(unittest.TestCase):
    def test_workflow_recording_excludes_secrets_and_raw_input(self):
        rec = WorkflowRecorder(allowed_actions=ALLOWLISTED_WORKFLOW_ACTIONS)
        rec.start()
        self.assertTrue(rec.is_recording)

        # Secret keys must be rejected
        with self.assertRaises(ValueError):
            rec.record("open_url", {"url": "https://example.com", "password": "123"}, "Login")
        with self.assertRaises(ValueError):
            rec.record("write_in_notepad", {"text": "api_key=AIzaSySecretKey"}, "Write secret")

        # Raw keyboard/mouse/clipboard actions must be rejected
        with self.assertRaises(ValueError):
            rec.record("press_key", {"key": "enter"}, "Raw key")
        with self.assertRaises(ValueError):
            rec.record("clipboard_control", {"action": "get"}, "Clipboard")

        # Valid semantic actions are recorded
        rec.record("open_app", {"app": "notepad"}, "Open Notepad")
        rec.record("write_in_notepad", {"text": "Hello Nova workflow"}, "Write greeting")
        wf = rec.stop("Notepad Greeting")
        self.assertFalse(rec.is_recording)
        self.assertEqual(wf.name, "Notepad Greeting")
        self.assertEqual(len(wf.steps), 2)

    def test_workflow_store_serialization_and_directory_restriction(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = WorkflowStore(tmpdir)
            rec = WorkflowRecorder(allowed_actions=ALLOWLISTED_WORKFLOW_ACTIONS)
            rec.start()
            rec.record("open_app", {"app": "calculator"}, "Open Calculator")
            wf = rec.stop("Calc Flow")
            saved_path = store.save(wf)
            self.assertTrue(saved_path.exists())

            loaded = store.load(saved_path)
            self.assertEqual(loaded.workflow_id, wf.workflow_id)
            self.assertEqual(loaded.name, "Calc Flow")
            self.assertEqual(len(loaded.steps), 1)
            self.assertEqual(loaded.steps[0].action, "open_app")

            # Loading from outside the workflows directory must fail
            outside = Path(tmpdir).parent / "outside.json"
            with self.assertRaises(ValueError):
                store.load(outside)

            self.assertEqual(len(store.list_workflows()), 1)
            self.assertTrue(store.delete(wf.workflow_id))
            self.assertEqual(len(store.list_workflows()), 0)

    def test_workflow_player_stops_on_denial_cancellation_or_verification_failure(self):
        steps = [
            WorkflowStep("open_app", {"app": "notepad"}, "Step 1", 1.0),
            WorkflowStep("write_in_notepad", {"text": "hello"}, "Step 2", 2.0),
            WorkflowStep("system_status", {}, "Step 3", 3.0),
        ]
        wf = Workflow("wf-1", "Test Flow", 1.0, steps)

        # 1. Denial at step 2 stops immediately
        executed = []
        player_deny = WorkflowPlayer(
            action_executor=lambda act, args: (executed.append(act) or {"ok": True, "verified": True}),
            confirm_step=lambda idx, _s: idx < 2,
            verify_step=lambda _idx, _s, res: res.get("verified", False),
        )
        res_deny = player_deny.play(wf)
        self.assertFalse(res_deny["ok"])
        self.assertTrue(res_deny.get("denied"))
        self.assertEqual(res_deny.get("stopped_at"), 2)
        self.assertEqual(executed, ["open_app"])

        # 2. Verification failure at step 2 stops immediately
        executed.clear()
        player_vfail = WorkflowPlayer(
            action_executor=lambda act, args: (executed.append(act) or {"ok": True, "verified": (act == "open_app")}),
            confirm_step=lambda _idx, _s: True,
            verify_step=lambda _idx, _s, res: bool(res.get("verified")),
        )
        res_vfail = player_vfail.play(wf)
        self.assertFalse(res_vfail["ok"])
        self.assertEqual(res_vfail.get("verification_failed_at"), 2)
        self.assertEqual(executed, ["open_app", "write_in_notepad"])

        # 3. Cancellation between steps stops immediately
        executed.clear()
        player_cancel = WorkflowPlayer(
            action_executor=lambda act, args: (executed.append(act) or {"ok": True, "verified": True}),
            confirm_step=lambda idx, _s: (player_cancel.cancel() or True) if idx == 2 else True,
            verify_step=lambda _idx, _s, res: True,
        )
        res_cancel = player_cancel.play(wf)
        self.assertFalse(res_cancel["ok"])
        self.assertTrue(res_cancel.get("cancelled"))
        self.assertEqual(executed, ["open_app"])


class ActionRouterAndBrokerTests(unittest.TestCase):
    def test_router_separates_direct_conversation_and_planner(self):
        planned = []
        router = NovaActionRouter(
            ai_planner=lambda text: (
                planned.append(text)
                or [{"action": "open_app", "arguments": {"app": "notepad"}}]
            )
        )

        # Direct commands
        r_show = router.route("Hey Nova, show the companion")
        self.assertEqual(r_show.route, "direct")
        self.assertEqual(r_show.intent.kind, "show_companion")

        r_hide = router.route("hide companion")
        self.assertEqual(r_hide.route, "direct")
        self.assertEqual(r_hide.intent.kind, "hide_companion")

        r_point = router.route("point at my screen")
        self.assertEqual(r_point.route, "direct")
        self.assertEqual(r_point.intent.kind, "point_at_screen")

        r_rec = router.route("start recording a workflow")
        self.assertEqual(r_rec.route, "direct")
        self.assertEqual(r_rec.intent.kind, "workflow_record_start")

        # Normal conversation never enters action planner
        r_conv = router.route("What is the capital of France and how are you today?")
        self.assertEqual(r_conv.route, "conversation")
        self.assertEqual(planned, [])

        # Action request goes to planner as a validated proposal without executing
        r_plan = router.route("Open Notepad and write my notes")
        self.assertEqual(r_plan.route, "planner")
        self.assertEqual(len(r_plan.plan), 1)
        self.assertEqual(r_plan.plan[0]["action"], "open_app")

    def test_router_rejects_unknown_or_dangerous_planner_output(self):
        bad_router = NovaActionRouter(
            ai_planner=lambda _t: [{"action": "run_shell_cmd", "arguments": {"cmd": "powershell.exe"}}]
        )
        res = bad_router.route("Open command shell")
        self.assertEqual(res.route, "unknown")
        self.assertIn("not in the allow-listed schema", res.response)

        valid, reason = validate_proposed_action({"action": "open_app", "arguments": {"app": "cmd.exe /c del /q"}})
        self.assertFalse(valid)
        self.assertIn("dangerous", reason.lower())

    def test_approved_action_broker_checks_approval_permission_and_verification(self):
        calls = []
        approved_flag = True
        perm_flag = True

        broker = ApprovedActionBroker(
            approval=lambda _a: approved_flag,
            permission_checker=lambda _cat, _desc: perm_flag,
        )
        broker.register("write_in_notepad", lambda text, open_if_closed=True: (
            calls.append(text) or {"ok": True, "verified": True, "message": "Wrote text"}
        ))

        # 1. Unregistered action rejected
        r_unreg = broker.execute({"action": "unknown_tool", "arguments": {}})
        self.assertFalse(r_unreg["ok"])

        # 2. User denial prevents execution
        approved_flag = False
        r_denied = broker.execute({"action": "write_in_notepad", "arguments": {"text": "Hello"}})
        self.assertFalse(r_denied["ok"])
        self.assertTrue(r_denied.get("denied"))
        self.assertEqual(calls, [])

        # 3. Permission denial prevents execution
        approved_flag = True
        perm_flag = False
        r_perm = broker.execute({"action": "write_in_notepad", "arguments": {"text": "Hello"}})
        self.assertFalse(r_perm["ok"])
        self.assertTrue(r_perm.get("permission_denied"))
        self.assertEqual(calls, [])

        # 4. Approved & permitted executes and verifies
        perm_flag = True
        r_ok = broker.execute({"action": "write_in_notepad", "arguments": {"text": "Hello"}})
        self.assertTrue(r_ok["ok"])
        self.assertTrue(r_ok["verified"])
        self.assertEqual(calls, ["Hello"])

    def test_live_action_broker_and_barge_in_and_workflow_editor(self):
        import pc_assistant
        from PySide6.QtWidgets import QApplication
        import ui

        # 1. Verify every tool in TOOLS is registered in live_action_broker and ACTION_SCHEMAS
        for tool_name in pc_assistant.TOOLS:
            self.assertIn(tool_name, pc_assistant.live_action_broker.handlers)
            self.assertIn(tool_name, pc_assistant.ACTION_SCHEMAS)

        # 2. Verify adaptive near-field barge-in unlocks speaking mute after 3 loud near-field frames
        pc_assistant._mic_stream_state["speaking"] = True
        pc_assistant._mic_stream_state["playback_rms"] = 220.0
        pc_assistant._mic_stream_state["barge_in_hits"] = 0
        pc_assistant._mic_stream_state["barge_in_triggered"] = False
        self.assertTrue(pc_assistant._is_mic_muted_for_echo())

        # Quiet echo frames (150 RMS) do not trigger barge-in
        self.assertFalse(pc_assistant._check_near_field_barge_in(150.0))
        self.assertTrue(pc_assistant._is_mic_muted_for_echo())

        # Loud near-field voice (950 RMS) for 3 consecutive frames triggers barge-in
        self.assertFalse(pc_assistant._check_near_field_barge_in(950.0))
        self.assertFalse(pc_assistant._check_near_field_barge_in(950.0))
        self.assertTrue(pc_assistant._check_near_field_barge_in(950.0))
        self.assertFalse(pc_assistant._is_mic_muted_for_echo())
        pc_assistant._mic_stream_state["barge_in_triggered"] = False
        pc_assistant._mic_stream_state["speaking"] = False

        # 3. Verify structured WorkflowEditorDialog reorders and removes steps cleanly
        app = QApplication.instance() or QApplication([])
        wf_dict = {
            "workflow_id": "wf-edit-test",
            "name": "Morning Routine",
            "steps": [
                {"action": "open_app", "arguments": {"app": "notepad"}, "description": "Step A"},
                {"action": "write_in_notepad", "arguments": {"text": "notes"}, "description": "Step B"},
            ],
        }
        dlg = ui.WorkflowEditorDialog(wf_dict)
        self.assertEqual(dlg.list_widget.count(), 2)
        dlg.list_widget.setCurrentRow(1)
        dlg._move_up()
        self.assertEqual(dlg.steps[0]["action"], "write_in_notepad")
        dlg.list_widget.setCurrentRow(1)
        dlg._remove_step()
        self.assertEqual(len(dlg.steps), 1)
        dlg.close()

    def test_offline_local_fallback_and_modular_ui_imports(self):
        import asyncio
        from nova_action_router import match_offline_command, validate_proposed_action
        from wake_word import WakeWordListener
        import pc_assistant
        import bot
        import ui_dialogs
        import ui

        # 1. Verify DesktopBot is shared between bot.py and ui.py (no duplicate class definition)
        self.assertIs(ui.DesktopBot, bot.DesktopBot)
        self.assertIs(ui.WorkflowEditorDialog, ui_dialogs.WorkflowEditorDialog)
        self.assertIs(ui.WalkthroughHighlightOverlay, ui_dialogs.WalkthroughHighlightOverlay)

        # 2. Verify deterministic offline local command matching & strict ACTION_SCHEMAS validation
        sample_queries = [
            ("please open calculator", "open_app", {"app": "calculator"}),
            ("calculator", "open_app", {"app": "calculator"}),
            ("open downloads", "open_folder", {"path": "downloads"}),
            ("set volume to 45%", "volume_control", {"action": "set", "value": 45}),
            ("hey nova set volume to fifty", "volume_control", {"action": "set", "value": 50}),
            ("set brightness to 70", "brightness_control", {"action": "set", "value": 70}),
            ("close calculator", "app_control", {"action": "close", "app": "calculator"}),
            ("minimize window", "window_control", {"action": "minimize"}),
            ("write hello world in notepad", "write_in_notepad", {"text": "hello world", "open_if_closed": True}),
            ("take a screenshot", "take_screenshot", {}),
            ("check system status", "system_status", {}),
            ("what time is it", "task_control", {"action": "now"}),
            ("calculate 25 * 4", "calculator_operation", {"expression": "25 * 4"}),
            ("show companion", "nova_control", {"action": "show_floating_bot"}),
        ]
        for query, expected_action, expected_args in sample_queries:
            act = match_offline_command(query)
            self.assertIsNotNone(act, f"Failed to match offline command: {query}")
            self.assertEqual(act["action"], expected_action)
            self.assertEqual(act["arguments"], expected_args)
            valid, reason = validate_proposed_action(act)
            self.assertTrue(valid, f"Offline command '{query}' failed schema validation: {reason}")

        self.assertIsNone(match_offline_command("explain quantum computing in simple terms"))

        # 3. Verify single-breath wake-word trailing command extraction
        wl = WakeWordListener()
        self.assertEqual(wl._extract_trailing_command("hey nova open calculator"), "open calculator")
        self.assertEqual(wl.last_command, "open calculator")
        self.assertEqual(wl._extract_trailing_command("hey nova"), "")

        # 4. Verify end-to-end offline execution in LiveManager (< 0.1s with session=None)
        pc_assistant.ducker.enabled = False
        mgr = pc_assistant.LiveManager(None)
        mgr.voice_output = False
        try:
            self.assertTrue(mgr.execute_offline_action_sync("hello"))
            self.assertTrue(mgr.execute_offline_action_sync("what time is it"))
            self.assertTrue(mgr.execute_offline_action_sync("calculate 25 * 4"))
            self.assertFalse(mgr.execute_offline_action_sync("explain quantum computing in simple terms"))
        finally:
            pc_assistant.ducker.stop()
            mgr.player.close()


if __name__ == "__main__":
    unittest.main()
