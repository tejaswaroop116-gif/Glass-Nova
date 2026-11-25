"""test_offline_capability.py - Unit & integration tests for Glass Nova Maximum Offline Capability.

Tests:
1. OfflineCapabilityManager hardware/VRAM profile, warnings, and summary formatting.
2. ConversationContextManager rolling compression, deduplication, and online->offline continuity.
3. OllamaClient TTL model caching, tagless model matching, 4 GB VRAM single-model unload, automatic CPU fallback, and local vision permission gating.
4. Screen & Document offline fallbacks (screen_agent.analyze_screen & document_agent.analyze_document).
5. LocalKnowledgeStore opt-in folder indexing (never auto-indexes, blocks system roots, skips .env/secrets/.venv), TF-IDF search, removal, and permission-gated local file/knowledge search.
6. run_safe_python AST sandbox (allows safe math/logic, blocks imports/dunders/bare-except/memory-bombs/timeouts even inside try-except Exception).
7. OfflineAgent plain Q&A (including coding replies with braces/JSON), ask_user, bounded multi-step plan execution via ApprovedActionBroker, single bounded replan on failure, permission denial stop, max_steps cap, and immediate cancellation.
8. Hybrid Offline Mode in LiveManager & TOOL_DEFS parity.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import document_agent
import nova_action_router as router
import nova_local_knowledge as nlk
import nova_offline_agent as noa
import nova_offline_ai as noai
import nova_tool_defs
import screen_agent
from permissions import permissions


class TestConversationContextManager(unittest.TestCase):
    def test_rolling_compression_keeps_budget_bounded(self):
        ctx = noai.ConversationContextManager(max_turns=6, max_context_chars=2200)
        for i in range(14):
            ctx.add_turn("user", f"Turn {i} user question about python and windows automation " + ("x" * 180))
            ctx.add_turn("assistant", f"Turn {i} assistant detailed response " + ("y" * 180))
        self.assertLessEqual(len(ctx.turns), 6)
        self.assertTrue(ctx.rolling_summary, "Expected rolling summary to capture compressed earlier turns")
        msgs = ctx.build_messages("System prompt", "What did we discuss earlier?", extra_context="[notes.md] local snippet")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Compressed earlier conversation context:", msgs[0]["content"])
        self.assertIn("Relevant local knowledge / memory:", msgs[0]["content"])
        self.assertEqual(msgs[-1], {"role": "user", "content": "What did we discuss earlier?"})

    def test_deduplicates_consecutive_identical_turns(self):
        ctx = noai.ConversationContextManager(max_turns=6)
        ctx.add_turn("user", "Explain recursion")
        ctx.add_turn("user", "Explain recursion")
        self.assertEqual(len(ctx.turns), 1)
        msgs = ctx.build_messages("Sys", "Explain recursion")
        user_msgs = [m for m in msgs if m["role"] == "user"]
        self.assertEqual(len(user_msgs), 1)

    def test_clear_resets_turns_and_summary(self):
        ctx = noai.ConversationContextManager(max_turns=4)
        for i in range(6):
            ctx.add_turn("user", f"hello {i}")
        ctx.clear()
        self.assertEqual(ctx.turns, [])
        self.assertEqual(ctx.rolling_summary, "")


class TestOllamaClientAndCapabilities(unittest.TestCase):
    def _make_client(self):
        client = noai.OllamaClient(base_url="http://127.0.0.1:11434")
        client.preferred_model = ""
        client.preferred_vision_model = ""
        client.cpu_only = False
        return client

    def test_select_best_model_and_tagless_preference_matching_and_ttl_cache(self):
        client = self._make_client()
        fake_tags = {
            "models": [
                {"name": "llama3:70b", "size": 40 * (1024 ** 3)},
                {"name": "qwen2.5:3b", "size": 1900 * (1024 ** 2)},
                {"name": "moondream:latest", "size": 1700 * (1024 ** 2)},
            ]
        }
        with mock.patch.object(client, "_http_json", return_value=fake_tags) as m_http:
            self.assertEqual(client.select_best_model(vision=False), "qwen2.5:3b")
            self.assertEqual(client.select_best_model(vision=True), "moondream:latest")
            # Tagless preference "moondream" matches "moondream:latest"
            client.preferred_vision_model = "moondream"
            self.assertEqual(client.select_best_model(vision=True), "moondream:latest")
            # TTL cache ensures _http_json("/api/tags") was only called once across all 3 calls!
            self.assertEqual(m_http.call_count, 1)

    def test_chat_unloads_previous_model_and_falls_back_to_cpu_on_vram_oom(self):
        client = self._make_client()
        client._active_loaded_model = "llama3.2:3b"
        calls = []

        def fake_http(path, payload=None, timeout=None):
            calls.append((path, payload))
            if path == "/api/tags":
                return {"models": [{"name": "qwen2.5:3b", "size": 1900 * (1024 ** 2)}]}
            if path == "/api/generate" and payload and payload.get("keep_alive") == 0:
                return {"done": True}
            if path == "/api/chat":
                opts = (payload or {}).get("options") or {}
                if opts.get("num_gpu") != 0:
                    raise RuntimeError("CUDA out of memory allocating KV cache")
                return {"message": {"content": "CPU fallback succeeded cleanly."}}
            return {}

        with mock.patch.object(client, "_http_json", side_effect=fake_http):
            res = client.chat([{"role": "user", "content": "Explain recursion briefly."}], model="qwen2.5:3b")

        self.assertTrue(res["ok"])
        self.assertTrue(res["cpu_mode"])
        self.assertIn("CPU fallback succeeded", res["content"])
        unload_calls = [p for path, p in calls if path == "/api/generate" and (p or {}).get("keep_alive") == 0]
        self.assertTrue(any(p.get("model") == "llama3.2:3b" for p in unload_calls))

    def test_local_vision_is_gated_by_screen_view_permission(self):
        client = self._make_client()
        with mock.patch.object(permissions, "request_permission", return_value=False):
            res = client.analyze_image_local(b"\x89PNG\r\n\x1a\nfake", question="What is on screen?")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("permission_denied"))

    def test_screen_and_document_agents_fall_back_to_offline_ai(self):
        # 1. screen_agent.analyze_screen falls back to OllamaClient.analyze_image_local when online Gemini fails
        with mock.patch.object(permissions, "request_permission", return_value=True), \
             mock.patch.object(screen_agent, "capture_screen_jpeg", return_value=b"fake_jpeg"), \
             mock.patch.object(screen_agent, "get_client", side_effect=RuntimeError("Offline")), \
             mock.patch.object(noai.OllamaClient, "analyze_image_local", return_value={"ok": True, "verified": True, "model": "moondream:latest", "message": "VS Code window is visible."}):
            s_res = screen_agent.analyze_screen("What is on my screen?")
        self.assertTrue(s_res["ok"])
        self.assertTrue(s_res["verified"])
        self.assertIn("VS Code", s_res["message"])

        # 2. document_agent.analyze_document falls back to local extractive / Ollama summary when online Gemini fails
        with tempfile.TemporaryDirectory() as td:
            doc_p = Path(td) / "syllabus.txt"
            doc_p.write_text("Module 1: Linear Regression\nModule 2: Neural Networks", encoding="utf-8")
            with mock.patch.object(permissions, "request_permission", return_value=True), \
                 mock.patch.object(document_agent, "get_client", side_effect=RuntimeError("Offline")), \
                 mock.patch.object(noai.OllamaClient, "is_available", return_value=False):
                d_res = document_agent.analyze_document(path=str(doc_p), query="List modules")
        self.assertTrue(d_res["ok"])
        self.assertTrue(d_res["verified"])
        self.assertIn("Linear Regression", d_res["message"])

    def test_capability_manager_reports_4gb_vram_profile(self):
        client = self._make_client()
        fake_hw = {
            "ram_total_gb": 16.0,
            "ram_available_gb": 5.2,
            "gpu_name": "NVIDIA GeForce RTX 3050 Laptop GPU",
            "gpu_vram_total_mb": 4096,
            "gpu_vram_free_mb": 3400,
            "low_vram_profile": True,
            "warnings": ["RTX 3050 (4096 MB VRAM) detected"],
        }
        with mock.patch.object(noai, "detect_hardware_profile", return_value=fake_hw), \
             mock.patch.object(client, "is_available", return_value=True), \
             mock.patch.object(client, "list_models", return_value=[{"name": "qwen2.5:3b", "size_gb": 1.9, "estimated_vram_gb": 2.28, "is_vision": False}]):
            mgr = noai.OfflineCapabilityManager(client)
            rep = mgr.inspect()
            summary = mgr.format_summary(rep)

        self.assertTrue(rep["ok"])
        self.assertTrue(rep["ollama"]["running"])
        self.assertEqual(rep["ollama"]["selected_text_model"], "qwen2.5:3b")
        self.assertIn("qwen2.5:3b", summary)
        self.assertIn("RTX 3050", summary)


class TestLocalKnowledgeAndSafeTools(unittest.TestCase):
    def test_opt_in_knowledge_store_skips_secrets_and_venv_and_blocks_system_roots(self):
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            idx_file = td_path / "index.json"
            docs_dir = td_path / "notes"
            docs_dir.mkdir()
            (docs_dir / "architecture.md").write_text(
                "Glass Nova uses ApprovedActionBroker for fail-closed permissions and Ollama for offline AI.",
                encoding="utf-8",
            )
            # Secret file and .venv directory must NEVER be indexed
            (docs_dir / ".env").write_text("GEMINI_API_KEY=super_secret_key_12345", encoding="utf-8")
            (docs_dir / "my_credentials.txt").write_text("password=top_secret_value", encoding="utf-8")
            venv_dir = docs_dir / ".venv"
            venv_dir.mkdir()
            (venv_dir / "pkg.py").write_text(" internal_venv_symbol = 123 ", encoding="utf-8")

            store = nlk.LocalKnowledgeStore(index_file=idx_file)
            self.assertEqual(store.list_folders(), [])

            # Block indexing C:\Windows or drive root
            bad_root = store.add_folder("C:\\Windows")
            self.assertFalse(bad_root["ok"])

            add_res = store.add_folder(str(docs_dir))
            self.assertTrue(add_res["ok"])
            self.assertEqual(len(store.list_folders()), 1)

            hits = store.search("ApprovedActionBroker Ollama")
            self.assertTrue(hits)
            self.assertEqual(hits[0]["name"], "architecture.md")

            # Verify secrets and .venv were excluded
            self.assertEqual(store.search("super_secret_key_12345"), [])
            self.assertEqual(store.search("top_secret_value"), [])
            self.assertEqual(store.search("internal_venv_symbol"), [])

            rem_res = store.remove_folder(str(docs_dir))
            self.assertTrue(rem_res["ok"])
            self.assertEqual(store.list_folders(), [])

    def test_local_knowledge_and_file_search_require_document_permission(self):
        with mock.patch.object(nlk, "_check_perm", return_value=False):
            res_add = nlk.local_knowledge_control(action="add_folder", path="C:\\nonexistent")
            res_search_kb = nlk.local_knowledge_control(action="search", query="notes")
            with tempfile.TemporaryDirectory() as td:
                res_search_files = nlk.search_local_files("budget", folder=td)
        self.assertFalse(res_add["ok"])
        self.assertTrue(res_add.get("permission_denied"))
        self.assertFalse(res_search_kb["ok"])
        self.assertTrue(res_search_kb.get("permission_denied"))
        self.assertFalse(res_search_files["ok"])
        self.assertTrue(res_search_files.get("permission_denied"))

    def test_search_local_files_finds_matching_files_when_permitted(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "quarterly_budget_2026.txt"
            p.write_text("budget", encoding="utf-8")
            with mock.patch.object(nlk, "_check_perm", return_value=True):
                res = nlk.search_local_files("budget", folder=td)
            self.assertTrue(res["ok"])
            self.assertTrue(res["verified"])
            self.assertEqual(len(res["results"]), 1)
            self.assertEqual(res["results"][0]["name"], "quarterly_budget_2026.txt")

    def test_safe_python_sandbox_allows_math_and_blocks_escapes_and_memory_bombs(self):
        ok_res = nlk.run_safe_python("vals = [i * i for i in range(1, 6)]\nprint(sum(vals))")
        self.assertTrue(ok_res["ok"])
        self.assertTrue(ok_res["verified"])
        self.assertEqual(ok_res["stdout"], "55")

        # Block imports & dunders
        self.assertFalse(nlk.run_safe_python("import os")["ok"])
        self.assertFalse(nlk.run_safe_python("print((1).__class__.__mro__)")["ok"])

        # Block bare except: and except BaseException:
        self.assertFalse(nlk.run_safe_python("try:\n    x = 1\nexcept:\n    pass")["ok"])
        self.assertFalse(nlk.run_safe_python("try:\n    x = 1\nexcept BaseException:\n    pass")["ok"])

        # Block memory/CPU bombs (huge power, huge sequence multiply, huge range, huge bit-shift)
        self.assertFalse(nlk.run_safe_python("x = 9 ** 9 ** 9")["ok"])
        self.assertFalse(nlk.run_safe_python("s = 'a' * (10 ** 8)")["ok"])
        self.assertFalse(nlk.run_safe_python("r = list(range(10 ** 7))")["ok"])
        self.assertFalse(nlk.run_safe_python("y = 1 << 50000")["ok"])

        # Block infinite loop even when user code wraps it in 'except Exception: pass'
        loop_res = nlk.run_safe_python(
            "try:\n    while True:\n        pass\nexcept Exception:\n    pass",
            timeout_seconds=0.3,
        )
        self.assertFalse(loop_res["ok"])
        self.assertIn("timed out", loop_res["message"].lower())


class TestOfflineAgent(unittest.TestCase):
    def _make_broker(self, allow_perm: bool = True, allow_approval: bool = True):
        executed = []
        broker = router.ApprovedActionBroker(
            approval=lambda act: allow_approval,
            permission_checker=lambda cat, desc: allow_perm,
            verifier=lambda name, args, res: bool(isinstance(res, dict) and res.get("ok") and res.get("verified")),
        )

        def fake_calc(expression: str):
            executed.append(("calculator_operation", expression))
            return {"ok": True, "verified": True, "message": f"Result of {expression} is 42"}

        def fake_sys():
            executed.append(("system_status",))
            return {"ok": True, "verified": True, "message": "CPU 12%, RAM 48%"}

        broker.register("calculator_operation", fake_calc)
        broker.register("system_status", fake_sys)
        return broker, executed

    def test_plain_text_qa_and_coding_replies_with_braces_are_not_misparsed(self):
        coding_reply = (
            "Here is a Python example using dictionaries:\n"
            "```python\n"
            "cfg = {'host': 'localhost', 'port': 8080}\n"
            "other = {'status': 'ok'}\n"
            "```"
        )
        ollama = mock.Mock()
        ollama.chat.return_value = {"ok": True, "model": "qwen2.5:3b", "content": coding_reply}
        broker, _ = self._make_broker()
        ctx = noai.ConversationContextManager()
        kb = nlk.LocalKnowledgeStore(index_file=Path(tempfile.gettempdir()) / "nova_test_empty_kb.json")
        agent = noa.OfflineAgent(ollama=ollama, broker=broker, context=ctx, knowledge=kb)

        res = agent.run_turn("Show me a Python dictionary example")
        self.assertTrue(res["ok"])
        self.assertEqual(res["mode"], "answer")
        self.assertIn("cfg =", res["message"])

    def test_bounded_multi_step_plan_and_single_replan_recovery(self):
        plan_1 = json.dumps({
            "mode": "plan",
            "reply": "Trying first action.",
            "steps": [
                {"action": "calculator_operation", "arguments": {"expression": "fail"}, "description": "Failing step"},
            ],
        })
        plan_recovery = json.dumps({
            "mode": "plan",
            "reply": "Recovering with system_status.",
            "steps": [
                {"action": "system_status", "arguments": {}, "description": "Check system status"},
            ],
        })
        ollama = mock.Mock()
        ollama.chat.side_effect = [
            {"ok": True, "model": "qwen2.5:3b", "content": plan_1},
            {"ok": True, "model": "qwen2.5:3b", "content": plan_recovery},
        ]
        broker, executed = self._make_broker(allow_perm=True, allow_approval=True)
        broker.register("calculator_operation", lambda expression: {"ok": False, "verified": False, "message": "Invalid expression"})
        kb = nlk.LocalKnowledgeStore(index_file=Path(tempfile.gettempdir()) / "nova_test_empty_kb2.json")
        agent = noa.OfflineAgent(ollama=ollama, broker=broker, knowledge=kb, max_steps=5)

        res = agent.run_turn("Try calc and recover")
        self.assertTrue(res["ok"])
        self.assertTrue(res["replanned"])
        self.assertEqual(res["steps_executed"], 2)
        self.assertEqual(executed, [("system_status",)])

    def test_plan_stops_immediately_on_permission_denial_or_unsafe_step_without_replanning(self):
        plan_json = json.dumps({
            "mode": "plan",
            "reply": "Running step.",
            "steps": [
                {"action": "system_status", "arguments": {}, "description": "Check system status"},
                {"action": "calculator_operation", "arguments": {"expression": "2+2"}, "description": "Calc"},
            ],
        })
        ollama = mock.Mock()
        ollama.chat.return_value = {"ok": True, "model": "qwen2.5:3b", "content": plan_json}
        broker, executed = self._make_broker(allow_perm=True, allow_approval=False)
        kb = nlk.LocalKnowledgeStore(index_file=Path(tempfile.gettempdir()) / "nova_test_empty_kb3.json")
        agent = noa.OfflineAgent(ollama=ollama, broker=broker, knowledge=kb)

        res = agent.run_turn("Do two steps")
        self.assertFalse(res["ok"])
        self.assertFalse(res.get("replanned"))
        self.assertEqual(res["steps_executed"], 1)
        self.assertEqual(executed, [])
        self.assertIn("denied", res["message"].lower())

        bad_plan = json.dumps({
            "mode": "plan",
            "reply": "Running shell",
            "steps": [{"action": "run_shell", "arguments": {"cmd": "del /f /q *"}, "description": "Bad"}],
        })
        ollama.chat.return_value = {"ok": True, "model": "qwen2.5:3b", "content": bad_plan}
        broker_ok, executed_ok = self._make_broker(allow_perm=True, allow_approval=True)
        agent2 = noa.OfflineAgent(ollama=ollama, broker=broker_ok, knowledge=kb)
        res2 = agent2.run_turn("Delete everything")
        self.assertFalse(res2["ok"])
        self.assertEqual(executed_ok, [])
        self.assertIn("rejected", res2["message"].lower())

    def test_max_steps_cap_and_immediate_cancel(self):
        many_steps = [
            {"action": "system_status", "arguments": {}, "description": f"Step {i}"}
            for i in range(10)
        ]
        ollama = mock.Mock()
        ollama.chat.return_value = {"ok": True, "model": "qwen2.5:3b", "content": json.dumps({"mode": "plan", "steps": many_steps})}
        broker, executed = self._make_broker()
        kb = nlk.LocalKnowledgeStore(index_file=Path(tempfile.gettempdir()) / "nova_test_empty_kb4.json")

        agent = noa.OfflineAgent(ollama=ollama, broker=broker, knowledge=kb, max_steps=3)
        res = agent.run_turn("Check status repeatedly")
        self.assertTrue(res["ok"])
        self.assertEqual(res["steps_executed"], 3)

        executed.clear()

        def on_ev(kind, payload):
            if kind == "step_done" and payload.get("step") == 1:
                agent.cancel()

        agent.on_step_event = on_ev
        res_cancel = agent.run_turn("Check status repeatedly")
        self.assertFalse(res_cancel["ok"])
        self.assertTrue(res_cancel["cancelled"])
        self.assertEqual(res_cancel["steps_executed"], 1)


class TestOfflineRouterToolDefsAndLiveManagerHybrid(unittest.TestCase):
    def test_tool_defs_and_router_include_all_offline_tools(self):
        declared = {fd.name for fd in nova_tool_defs.TOOL_DEFS[0].function_declarations}
        for tool_name in ("offline_capabilities", "local_knowledge_control", "search_local_files", "run_safe_python"):
            self.assertIn(tool_name, declared)
            self.assertIn(tool_name, router.ACTION_SCHEMAS)

        self.assertEqual(router.match_offline_command("offline capabilities")["action"], "offline_capabilities")
        self.assertEqual(router.match_offline_command("list indexed folders")["action"], "local_knowledge_control")
        self.assertEqual(router.match_offline_command("remove indexed folder C:\\notes")["action"], "local_knowledge_control")
        self.assertEqual(router.match_offline_command("look at my screen")["action"], "look_at_screen")
        self.assertEqual(router.match_offline_command("summarize pdf")["action"], "inspect_document")
        self.assertEqual(
            router.match_offline_command("search local knowledge for permissions")["action"],
            "local_knowledge_control",
        )
        self.assertEqual(
            router.match_offline_command("search files for resume")["action"],
            "search_local_files",
        )

    def test_live_manager_hybrid_offline_fallback_and_controls(self):
        import pc_assistant as pa

        mgr = pa.LiveManager(None)
        mgr.voice_output = False
        with mock.patch.object(pa.offline_ollama, "is_available", return_value=True), \
             mock.patch.object(pa.offline_ollama, "select_best_model", return_value="qwen2.5:3b"), \
             mock.patch.object(pa.offline_agent, "run_turn", return_value={"ok": True, "model": "qwen2.5:3b", "message": "Quantum bits can be in superposition."}):
            handled = mgr.execute_offline_action_sync("explain quantum computing in simple terms", from_voice=False)
        self.assertTrue(handled)

        with mock.patch.object(pa.offline_agent, "cancel") as m_cancel:
            self.assertTrue(mgr.execute_offline_action_sync("stop task", from_voice=False))
            m_cancel.assert_called_once()

        orig_cpu = pa.offline_ollama.cpu_only
        orig_vmod = pa.offline_ollama.preferred_vision_model
        try:
            with mock.patch.object(pa.offline_ollama, "save_config"):
                self.assertTrue(mgr.execute_offline_action_sync("offline cpu mode", from_voice=False))
                self.assertTrue(pa.offline_ollama.cpu_only)
                self.assertTrue(mgr.execute_offline_action_sync("offline gpu mode", from_voice=False))
                self.assertFalse(pa.offline_ollama.cpu_only)
                self.assertTrue(mgr.execute_offline_action_sync("switch offline vision model to moondream", from_voice=False))
                self.assertEqual(pa.offline_ollama.preferred_vision_model, "moondream")
        finally:
            pa.offline_ollama.cpu_only = orig_cpu
            pa.offline_ollama.preferred_vision_model = orig_vmod

    def test_gemini_live_model_fallbacks_and_startup_script_and_offline_tts(self):
        import pc_assistant as pa
        import video_agent

        # 1. Gemini Live models include real fallbacks and never default to invalid 'gemini-3.8-live'
        live_models = pa.get_live_models()
        self.assertTrue(len(live_models) >= 3)
        self.assertNotIn("gemini-3.8-live", pa.LIVE_MODEL_FALLBACKS)
        env_example = (Path(__file__).resolve().parent / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn("gemini-3.8-live", env_example)

        # 2. Offline SAPI TTS initializes cleanly via comtypes (real COM test on Windows!)
        voice, cleanup_fn, info = pa.LiveManager._create_sapi_voice()
        if voice is not None:
            try:
                self.assertIn("SAPI", info)
            finally:
                if cleanup_fn:
                    cleanup_fn()

        # 3. Start_Glass_Nova.bat does NOT unconditionally run pip install --upgrade pip
        bat_txt = (Path(__file__).resolve().parent / "Start_Glass_Nova.bat").read_text(encoding="utf-8")
        self.assertNotIn("pip install --upgrade pip", bat_txt)
        self.assertIn("NEED_INSTALL", bat_txt)

        # 4. requirements.txt removes unused mss/uiautomation and pins bounds
        req_txt = (Path(__file__).resolve().parent / "requirements.txt").read_text(encoding="utf-8")
        self.assertNotIn("\nmss", req_txt)
        self.assertNotIn("uiautomation", req_txt)
        self.assertIn("comtypes>=", req_txt)

        # 5. Free-form offline Vosk recognizer is initialized WITHOUT a closed phrase grammar
        mgr = pa.LiveManager(None)
        fake_vosk = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"vosk": fake_vosk}), \
             mock.patch.object(pa, "_get_vosk_model", return_value=object()):
            rec = mgr._get_offline_vosk_recognizer()
        self.assertIsNotNone(rec)
        # Verify KaldiRecognizer was called with (model, 16000) — only 2 args, no closed grammar!
        args, _kwargs = fake_vosk.KaldiRecognizer.call_args
        self.assertEqual(len(args), 2)
        self.assertEqual(args[1], 16000)

        # 6. Offline video analysis falls back to local Ollama vision/OCR and returns verified=True
        with mock.patch.object(permissions, "request_permission", return_value=True), \
             mock.patch.object(video_agent, "capture_video_frame", return_value=b"fake_jpg"), \
             mock.patch.object(video_agent, "get_client", side_effect=RuntimeError("Offline")), \
             mock.patch.object(noai.OllamaClient, "analyze_image_local", return_value={"ok": True, "verified": True, "model": "moondream:latest", "message": "Slide shows gradient descent."}):
            v_res = video_agent.analyze_video("Explain the slide")
        self.assertTrue(v_res["ok"])
        self.assertTrue(v_res["verified"])
        self.assertIn("gradient descent", v_res["message"])

        # 7. Offline walkthrough uses local Ollama when Gemini client is None
        orig_client = pa.client
        try:
            pa.client = None
            ol_plan = json.dumps({"title": "Check Disk", "steps": [{"title": "Open Settings", "instruction": "Press Win+I", "target_label": None, "caution": None}]})
            with mock.patch.object(pa.offline_ollama, "is_available", return_value=True), \
                 mock.patch.object(pa.offline_ollama, "select_best_model", return_value="qwen2.5:3b"), \
                 mock.patch.object(pa.offline_ollama, "chat", return_value={"ok": True, "content": ol_plan}):
                steps = pa._generate_walkthrough_steps_via_ai("Check disk storage")
            self.assertEqual(len(steps), 1)
            self.assertEqual(steps[0]["title"], "Open Settings")
        finally:
            pa.client = orig_client


if __name__ == "__main__":
    unittest.main()
