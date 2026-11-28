"""Comprehensive automated tests for Glass Nova Workflow Templates & Visual Workflow Debugger."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from nova_action_router import ACTION_SCHEMAS, ApprovedActionBroker, validate_proposed_action
from nova_dpapi import _ENVELOPE_KEY, is_dpapi_available
from nova_workflows import (
    ACTION_PERMISSION_MAP,
    ALLOWLISTED_WORKFLOW_ACTIONS,
    WORKFLOW_TEMPLATES,
    Workflow,
    WorkflowDebugger,
    WorkflowPlayer,
    WorkflowStep,
    WorkflowStore,
    instantiate_template,
    list_workflow_templates,
)


class WorkflowTemplatesTests(unittest.TestCase):
    def test_all_builtin_templates_pass_real_action_schemas_and_preflight(self):
        templates = list_workflow_templates()
        self.assertEqual(len(templates), 6)
        seen_ids = set()
        for tpl in templates:
            tid = tpl["template_id"]
            self.assertTrue(tid)
            self.assertNotIn(tid, seen_ids)
            seen_ids.add(tid)
            self.assertTrue(tpl["steps"], f"Template '{tid}' has no steps")

            wf = instantiate_template(tid)
            for step in wf.steps:
                self.assertIn(step.action, ALLOWLISTED_WORKFLOW_ACTIONS)
                ok, reason = validate_proposed_action({"action": step.action, "arguments": step.arguments})
                self.assertTrue(ok, f"Template '{tid}' step '{step.action}' failed ACTION_SCHEMAS: {reason}")

            traces = WorkflowDebugger.preflight_validate(wf)
            self.assertEqual(len(traces), len(wf.steps))
            for tr in traces:
                self.assertNotEqual(tr.status, "blocked", f"Template '{tid}' step '{tr.action}' unexpectedly blocked: {tr.message}")

    def test_action_permission_map_matches_authoritative_action_schemas(self):
        for act in ALLOWLISTED_WORKFLOW_ACTIONS:
            expected_perm = ACTION_SCHEMAS.get(act, {}).get("permission")
            self.assertEqual(
                ACTION_PERMISSION_MAP.get(act),
                expected_perm,
                f"Permission mismatch for '{act}': {ACTION_PERMISSION_MAP.get(act)} != {expected_perm}",
            )

    def test_instantiate_template_dpapi_storage_on_disk_and_roundtrip(self):
        wf = instantiate_template("morning_workspace", custom_name="My Morning Flow")
        self.assertEqual(wf.name, "My Morning Flow")
        self.assertEqual(len(wf.steps), 4)

        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            saved_path = store.save(wf)
            self.assertTrue(saved_path.is_file())

            raw_text = saved_path.read_text(encoding="utf-8")
            if is_dpapi_available():
                raw_obj = json.loads(raw_text)
                self.assertIn(_ENVELOPE_KEY, raw_obj, "Expected DPAPI encrypted envelope on Windows")
                self.assertTrue(raw_obj.get("b64"), "Expected encrypted base64 payload in DPAPI envelope")
                self.assertNotIn("My Morning Flow", raw_text, "Workflow name should not be stored in plaintext under DPAPI")

            loaded = store.load(saved_path)
            self.assertEqual(loaded.workflow_id, wf.workflow_id)
            self.assertEqual(loaded.name, "My Morning Flow")
            self.assertEqual([s.action for s in loaded.steps], [s.action for s in wf.steps])
            self.assertEqual([s.arguments for s in loaded.steps], [s.arguments for s in wf.steps])

    def test_unknown_template_and_sensitive_custom_name_rejected(self):
        with self.assertRaises(ValueError):
            instantiate_template("nonexistent_template_xyz")
        with self.assertRaises(ValueError):
            instantiate_template("morning_workspace", custom_name="Work api_key=sk-live-12345")
        with self.assertRaises(ValueError):
            instantiate_template("morning_workspace", custom_name="Login password=supersecret")


class WorkflowDebuggerAndSecurityTests(unittest.TestCase):
    def test_preflight_maps_permissions_and_high_impact_warnings(self):
        wf = Workflow(
            workflow_id="dbg-1",
            name="Preflight Check",
            created_at=time.time(),
            steps=[
                WorkflowStep("system_status", {}, "Check system", time.time()),
                WorkflowStep("take_screenshot", {}, "Capture screen", time.time()),
                WorkflowStep("delete_file", {"path": "temp.txt"}, "Delete temp file", time.time()),
            ],
        )
        traces = WorkflowDebugger.preflight_validate(wf)
        self.assertEqual(len(traces), 3)
        self.assertEqual(traces[0].status, "verified")
        self.assertIsNone(traces[0].required_permission)
        self.assertEqual(traces[1].status, "verified")
        self.assertEqual(traces[1].required_permission, "screen_view")
        self.assertEqual(traces[2].status, "warning")
        self.assertEqual(traces[2].required_permission, "file_delete")

    def test_preflight_blocks_dangerous_commands_unsafe_args_and_secrets(self):
        wf = Workflow(
            workflow_id="dbg-unsafe",
            name="Unsafe Workflow",
            created_at=time.time(),
            steps=[
                WorkflowStep("open_app", {"app": "powershell.exe"}, "Dangerous shell", time.time()),
                WorkflowStep("open_app", {"app": "notepad && calc"}, "Shell metacharacters", time.time()),
                WorkflowStep("open_url", {"url": "javascript:alert(1)"}, "Unsafe URL scheme", time.time()),
                WorkflowStep("volume_control", {"action": "set", "value": 999}, "Out of range volume", time.time()),
                WorkflowStep("open_app", {"app_name": "notepad"}, "Invalid schema key", time.time()),
                WorkflowStep("open_url", {"url": "https://example.com", "api_key": "sk-secret"}, "Secret arg", time.time()),
            ],
        )
        traces = WorkflowDebugger.preflight_validate(wf)
        for tr in traces:
            self.assertEqual(tr.status, "blocked", f"Expected step {tr.step_index} ({tr.description}) to be blocked")

    def test_step_1_breakpoint_and_resume_and_blocked_halt(self):
        wf = instantiate_template("study_lecture_capture")

        # Step-1 breakpoint must pause BEFORE executing step 1
        p1 = WorkflowDebugger.dry_run(wf, breakpoints={1, 3}, start_step=1, skip_breakpoint_on_start=False)
        self.assertTrue(p1["ok"])
        self.assertTrue(p1["paused"])
        self.assertEqual(p1["paused_at"], 1)
        self.assertEqual(p1["traces"][0]["status"], "paused_breakpoint")

        # Resuming from step 1 with skip_breakpoint_on_start=True executes 1 & 2, pauses at 3
        p2 = WorkflowDebugger.dry_run(wf, breakpoints={1, 3}, start_step=1, skip_breakpoint_on_start=True)
        self.assertTrue(p2["ok"])
        self.assertTrue(p2["paused"])
        self.assertEqual(p2["paused_at"], 3)
        self.assertEqual([t["step_index"] for t in p2["traces"]], [1, 2, 3])
        self.assertEqual(p2["traces"][-1]["status"], "paused_breakpoint")

        # Resuming from step 3 completes workflow
        p3 = WorkflowDebugger.dry_run(wf, breakpoints={1, 3}, start_step=3, skip_breakpoint_on_start=True)
        self.assertTrue(p3["ok"])
        self.assertFalse(p3["paused"])
        self.assertEqual(p3["completed"], 1)

    def test_live_replay_enforces_permission_denial_fail_closed(self):
        wf = instantiate_template("study_lecture_capture")
        called = []

        def fake_screenshot():
            called.append("take_screenshot")
            return {"ok": True, "verified": True}

        broker = ApprovedActionBroker(
            approval=lambda _a: True,
            permission_checker=lambda cat, _desc: False,  # Deny all permissions!
            verifier=lambda _n, _a, res: bool(isinstance(res, dict) and res.get("ok")),
        )
        broker.register("take_screenshot", fake_screenshot)

        def exec_step(action_name, args):
            res = broker.execute({"action": action_name, "arguments": args, "description": action_name})
            return res

        player = WorkflowPlayer(
            action_executor=exec_step,
            confirm_step=lambda _i, _s: True,
            verify_step=lambda _i, _s, res: bool(isinstance(res, dict) and res.get("ok")),
        )
        outcome = player.play(wf)
        self.assertFalse(outcome["ok"])
        self.assertEqual(outcome["verification_failed_at"], 1)
        self.assertEqual(called, [], "Tool must NOT execute when permission is denied")

    def test_offscreen_pyside6_dialogs_breakpoints_blocked_halt_reset_and_replay_guard(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            from ui_dialogs import WorkflowTemplatesDialog, WorkflowDebuggerDialog
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable in this environment: {exc}")

        app = QApplication.instance() or QApplication([])

        # 1. Templates Dialog smoke
        tpl_dlg = WorkflowTemplatesDialog(list_workflow_templates())
        self.assertEqual(tpl_dlg.list_widget.count(), len(WORKFLOW_TEMPLATES))
        tpl_dlg._on_install()
        self.assertTrue(tpl_dlg.selected_template_id)

        # 2. Valid workflow: Step-1 breakpoint, resume, reset, summary counters & Replay Live enabled
        wf = instantiate_template("morning_workspace")
        wf_dict = {
            "workflow_id": wf.workflow_id,
            "name": wf.name,
            "created_at": wf.created_at,
            "steps": [{"action": s.action, "arguments": s.arguments, "description": s.description, "created_at": s.created_at} for s in wf.steps],
        }
        dbg = WorkflowDebuggerDialog(wf_dict, WorkflowDebugger)
        self.assertIn("4 Verified", dbg.summary_label.text())
        self.assertIn("0 Blocked", dbg.summary_label.text())
        self.assertTrue(dbg.live_btn.isEnabled())

        # Toggle breakpoint on Step 1 (row 0) and run dry-run -> must pause at step 1
        dbg.step_list.setCurrentRow(0)
        dbg._toggle_breakpoint()
        self.assertIn(1, dbg._breakpoints)
        dbg._run_dry()
        self.assertEqual(dbg._next_step, 1)
        self.assertTrue(dbg._paused_at_breakpoint)

        # Continue from Step 1 -> completes cleanly
        dbg._run_dry()
        self.assertEqual(dbg._next_step, 1)
        self.assertFalse(dbg._paused_at_breakpoint)

        # Reset clears traces
        dbg._reset_debug()
        self.assertEqual(dbg._traces_by_step, {})
        self.assertEqual(dbg._next_step, 1)

        # 3. Blocked workflow: Replay Live disabled & _step_next halts on blocked step!
        bad_dict = {
            "workflow_id": "bad-ui",
            "name": "Bad Flow",
            "created_at": time.time(),
            "steps": [
                {"action": "system_status", "arguments": {}, "description": "Safe step", "created_at": 0.0},
                {"action": "open_app", "arguments": {"app": "powershell.exe"}, "description": "Dangerous step", "created_at": 0.0},
                {"action": "open_app", "arguments": {"app": "notepad"}, "description": "Never reached", "created_at": 0.0},
            ],
        }
        bad_dbg = WorkflowDebuggerDialog(bad_dict, WorkflowDebugger)
        self.assertTrue(bad_dbg._has_blocked_steps)
        self.assertFalse(bad_dbg.live_btn.isEnabled())
        self.assertIn("1 Blocked", bad_dbg.summary_label.text())

        # Clicking _on_replay_live programmatically must still refuse
        bad_dbg._on_replay_live()
        self.assertFalse(bad_dbg.request_live_replay)

        # Step 1 succeeds, Step 2 halts at 2 without advancing to 3!
        bad_dbg._step_next()
        self.assertEqual(bad_dbg._next_step, 2)
        bad_dbg._step_next()
        self.assertEqual(bad_dbg._next_step, 2)
        self.assertIn("Halted", bad_dbg.status_banner.text())
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
