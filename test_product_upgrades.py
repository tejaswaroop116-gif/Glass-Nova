"""test_product_upgrades.py - Regression tests for Windows DPAPI encryption, Offline Settings UI, and build_exe.py."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import build_exe
import nova_dpapi
from nova_local_knowledge import LocalKnowledgeStore
from nova_workflows import Workflow, WorkflowStep, WorkflowStore


class ProductUpgradeTests(unittest.TestCase):
    def test_dpapi_roundtrip_and_no_plaintext_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "state.json"
            secret_payload = {"memories": {"note": "super_private_value_9981"}, "tasks": []}
            nova_dpapi.dump_protected_json(p, secret_payload)
            raw_text = p.read_text(encoding="utf-8")
            if sys.platform == "win32":
                self.assertIn('"_nova_dpapi": 1', raw_text)
                self.assertNotIn("super_private_value_9981", raw_text)
            loaded = nova_dpapi.load_protected_json(p)
            self.assertEqual(loaded, secret_payload)

    def test_dpapi_reads_legacy_plaintext_json(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "legacy.json"
            legacy = {"version": 1, "memories": {"k": "v"}, "tasks": []}
            p.write_text(json.dumps(legacy), encoding="utf-8")
            self.assertEqual(nova_dpapi.load_protected_json(p), legacy)

    def test_workflow_and_knowledge_stores_use_dpapi(self):
        with tempfile.TemporaryDirectory() as td:
            wf_dir = Path(td) / "wfs"
            store = WorkflowStore(wf_dir)
            wf = Workflow(
                "wf_dpapi",
                "Test Flow",
                "2026-11-27T00:00:00",
                [WorkflowStep("open", {"app": "notepad"}, "Open Notepad", "2026-11-27T00:00:00")],
            )
            saved = store.save(wf)
            if sys.platform == "win32":
                self.assertIn('"_nova_dpapi": 1', saved.read_text(encoding="utf-8"))
            loaded = store.load(saved)
            self.assertEqual(loaded.name, "Test Flow")
            self.assertEqual(len(loaded.steps), 1)

            kb_file = Path(td) / "kb.json"
            kb = LocalKnowledgeStore(index_file=kb_file)
            kb._save()
            if sys.platform == "win32":
                self.assertIn('"_nova_dpapi": 1', kb_file.read_text(encoding="utf-8"))

    def test_build_exe_check_and_zip_packaging(self):
        self.assertEqual(build_exe.build(check_only=True), 0)
        self.assertIn("nova_dpapi", build_exe.HIDDEN_IMPORTS)
        self.assertIn("comtypes.client", build_exe.HIDDEN_IMPORTS)
        with tempfile.TemporaryDirectory() as td:
            fake_bundle = Path(td) / "GlassNova"
            fake_bundle.mkdir()
            (fake_bundle / "GlassNova.exe").write_bytes(b"MZ")
            orig_root = build_exe.ROOT
            try:
                build_exe.ROOT = Path(td)
                (Path(td) / "dist").mkdir(exist_ok=True)
                zip_path = build_exe.package_zip(fake_bundle)
                self.assertTrue(zip_path.is_file())
                with zipfile.ZipFile(zip_path) as zf:
                    self.assertIn("GlassNova/GlassNova.exe", zf.namelist())
            finally:
                build_exe.ROOT = orig_root

    def test_settings_ui_methods_and_ipc_wiring_exist(self):
        ui_src = (Path(__file__).resolve().parent / "ui.py").read_text(encoding="utf-8")
        for marker in (
            "Offline AI & Local Knowledge",
            "self.ollama_chat_combo",
            "self.ollama_vision_combo",
            "self.ollama_gpu_btn",
            "self.kb_folders_list",
            "def _apply_offline_models",
            "def _toggle_ollama_cpu_only",
            "def _kb_add_folder",
            "cmd='offline_config'",
        ):
            self.assertIn(marker, ui_src)

        backend_src = (Path(__file__).resolve().parent / "pc_assistant.py").read_text(encoding="utf-8")
        self.assertIn('elif cmd == "offline_config":', backend_src)


if __name__ == "__main__":
    unittest.main()
