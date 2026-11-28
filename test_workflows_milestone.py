"""Automated unit tests for Glass Nova Workflow Templates & Visual Workflow Debugger."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest

from nova_workflows import (
    ALLOWLISTED_WORKFLOW_ACTIONS,
    ACTION_PERMISSION_MAP,
    WORKFLOW_TEMPLATES,
    Workflow,
    WorkflowDebugger,
    WorkflowStep,
    WorkflowStore,
    instantiate_template,
    list_workflow_templates,
)


class WorkflowTemplatesTests(unittest.TestCase):
    def test_all_builtin_templates_are_valid_and_allowlisted(self):
        templates = list_workflow_templates()
        self.assertGreaterEqual(len(templates), 6)
        seen_ids = set()
        for tpl in templates:
            tid = tpl["template_id"]
            self.assertTrue(tid)
            self.assertNotIn(tid, seen_ids)
            seen_ids.add(tid)
            self.assertTrue(tpl["steps"], f"Template '{tid}' has no steps")
            for step in tpl["steps"]:
                self.assertIn(step["action"], ALLOWLISTED_WORKFLOW_ACTIONS)
                self.assertIn(step["action"], ACTION_PERMISSION_MAP)

    def test_instantiate_template_and_store_roundtrip(self):
        wf = instantiate_template("morning_workspace", custom_name="My Morning Flow")
        self.assertEqual(wf.name, "My Morning Flow")
        self.assertEqual(len(wf.steps), 4)
        self.assertEqual(wf.steps[0].action, "system_status")

        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            saved_path = store.save(wf)
            self.assertTrue(saved_path.is_file())
            loaded = store.load(saved_path)
            self.assertEqual(loaded.workflow_id, wf.workflow_id)
            self.assertEqual(loaded.name, "My Morning Flow")
            self.assertEqual([s.action for s in loaded.steps], [s.action for s in wf.steps])

    def test_unknown_template_raises_value_error(self):
        with self.assertRaises(ValueError):
            instantiate_template("nonexistent_template_xyz")


class WorkflowDebuggerTests(unittest.TestCase):
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

    def test_preflight_blocks_disallowed_actions_and_secrets(self):
        wf = Workflow(
            workflow_id="dbg-2",
            name="Unsafe Workflow",
            created_at=time.time(),
            steps=[
                WorkflowStep("press_key", {"key": "enter"}, "Raw key", time.time()),
                WorkflowStep("open_url", {"url": "https://example.com", "api_key": "sk-secret123"}, "Secret URL", time.time()),
            ],
        )
        traces = WorkflowDebugger.preflight_validate(wf)
        self.assertEqual(traces[0].status, "blocked")
        self.assertEqual(traces[1].status, "blocked")

    def test_dry_run_full_and_breakpoints_and_blocking(self):
        wf = instantiate_template("study_lecture_capture")
        seen = []
        full = WorkflowDebugger.dry_run(wf, on_trace=lambda tr: seen.append(tr.step_index))
        self.assertTrue(full["ok"])
        self.assertFalse(full["paused"])
        self.assertEqual(full["completed"], len(wf.steps))
        self.assertEqual(seen, [1, 2, 3])

        # Pause at breakpoint on step 2, then resume from step 2
        paused = WorkflowDebugger.dry_run(wf, breakpoints={2}, start_step=1)
        self.assertTrue(paused["ok"])
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["paused_at"], 2)
        self.assertEqual(paused["traces"][-1]["status"], "paused_breakpoint")

        resumed = WorkflowDebugger.dry_run(wf, breakpoints={2}, start_step=2)
        self.assertTrue(resumed["ok"])
        self.assertFalse(resumed["paused"])
        self.assertEqual(resumed["completed"], 2)

        # Dry-run halts immediately on blocked step
        bad_wf = Workflow(
            workflow_id="dbg-3",
            name="Blocked Flow",
            created_at=time.time(),
            steps=[
                WorkflowStep("system_status", {}, "OK step", time.time()),
                WorkflowStep("press_key", {"key": "a"}, "Disallowed raw key", time.time()),
                WorkflowStep("open_app", {"app_name": "notepad"}, "Unreachable step", time.time()),
            ],
        )
        blocked = WorkflowDebugger.dry_run(bad_wf)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["blocked_at"], 2)
        self.assertEqual(len(blocked["traces"]), 2)

    def test_offscreen_pyside6_dialogs_smoke(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            from ui_dialogs import WorkflowTemplatesDialog, WorkflowDebuggerDialog
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable in this environment: {exc}")

        app = QApplication.instance() or QApplication([])
        tpl_dlg = WorkflowTemplatesDialog(list_workflow_templates())
        self.assertEqual(tpl_dlg.list_widget.count(), len(WORKFLOW_TEMPLATES))
        tpl_dlg._on_install()
        self.assertTrue(tpl_dlg.selected_template_id)

        wf = instantiate_template("morning_workspace")
        wf_dict = {
            "workflow_id": wf.workflow_id,
            "name": wf.name,
            "created_at": wf.created_at,
            "steps": [{"action": s.action, "arguments": s.arguments, "description": s.description, "created_at": s.created_at} for s in wf.steps],
        }
        dbg_dlg = WorkflowDebuggerDialog(wf_dict, WorkflowDebugger)
        self.assertEqual(dbg_dlg.step_list.count(), len(wf.steps))
        dbg_dlg.step_list.setCurrentRow(1)
        dbg_dlg._toggle_breakpoint()
        self.assertIn(2, dbg_dlg._breakpoints)
        dbg_dlg._run_dry()
        self.assertEqual(dbg_dlg._next_step, 2)
        dbg_dlg._run_dry()
        self.assertEqual(dbg_dlg._next_step, 1)
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
