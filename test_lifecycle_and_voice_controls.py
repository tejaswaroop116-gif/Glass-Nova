"""test_lifecycle_and_voice_controls.py - Comprehensive unit & integration tests for:
1. Application Lifecycle (startup order, duplicate-init guard, idempotent shutdown, error isolation,
   single-instance guard, shutdown during active conversation/TTS/offline-agent/pending-permissions).
2. Independent Microphone Listening Control (ON/OFF, repeated toggling, no unexpected reactivation,
   typed chat while mic OFF, wake-word privacy honesty).
3. Independent Spoken Reply Control (ON/OFF, immediate stop speaking, all 4 combinations).
4. Voice Control Command Layer (all commands across Offline, Online, and Hybrid modes without AI reply).
5. Mode Switching (Offline <-> Online <-> Hybrid preserves mic & spoken reply states).
6. Chat Quick-Access UI Controls & Status Indicators (offscreen Qt check).

NOTE: Hardware audio devices (physical microphone/speakers) and live Gemini Live sockets are mocked
so this suite runs deterministically in CI and headless environments.
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import nova_lifecycle as nl
import nova_voice_control as nvc


def _drive_coro_sync(coro):
    """Drive a coroutine that uses only already-ready awaits / mocked async helpers without needing
    an OS loopback socketpair (works cleanly inside restricted sandboxes and CI)."""
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("Coroutine yielded unexpectedly in synchronous test driver")


class TestApplicationLifecycle(unittest.TestCase):
    def test_ordered_startup_duplicate_guard_and_reverse_idempotent_shutdown(self):
        events = []
        lm = nl.LifecycleManager()
        lm.register("ui", start_fn=lambda: events.append("start:ui"), stop_fn=lambda: events.append("stop:ui"), priority=10)
        lm.register("audio", start_fn=lambda: events.append("start:audio"), stop_fn=lambda: events.append("stop:audio"), priority=20)
        lm.register("backend", start_fn=lambda: events.append("start:backend"), stop_fn=lambda: events.append("stop:backend"), priority=30)

        res1 = lm.start_all()
        self.assertEqual(res1, {"ui": True, "audio": True, "backend": True})
        self.assertEqual(events, ["start:ui", "start:audio", "start:backend"])

        # Duplicate start_all does NOT re-initialize components
        lm.start_all()
        self.assertEqual(events, ["start:ui", "start:audio", "start:backend"])

        # Reverse-order shutdown
        errs = lm.shutdown_all()
        self.assertEqual(errs, [])
        self.assertEqual(events, ["start:ui", "start:audio", "start:backend", "stop:backend", "stop:audio", "stop:ui"])

        # Idempotent second shutdown does nothing
        lm.shutdown_all()
        self.assertEqual(len(events), 6)

    def test_shutdown_isolates_component_errors_so_all_resources_cleanup(self):
        cleaned = []
        lm = nl.LifecycleManager()
        lm.register("mic", stop_fn=lambda: cleaned.append("mic"), priority=10)
        lm.register("broken", stop_fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")), priority=20)
        lm.register("offline_agent", stop_fn=lambda: cleaned.append("offline_agent"), priority=30)
        lm.start_all()

        errs = lm.shutdown_all()
        self.assertEqual(len(errs), 1)
        self.assertEqual(cleaned, ["offline_agent", "mic"])
        self.assertEqual(lm.state, "stopped")

    def test_single_instance_guard_blocks_duplicate_and_releases_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            g1 = nl.SingleInstanceGuard(app_id="TestNovaLock", lock_dir=Path(td))
            g2 = nl.SingleInstanceGuard(app_id="TestNovaLock", lock_dir=Path(td))
            self.assertTrue(g1.acquire(use_qt_ipc=False))
            # Write a different alive PID (or simulate another live process)
            g1._lock_file.write_text(str(os.getpid() + 999999), encoding="utf-8")
            with mock.patch.object(nl.SingleInstanceGuard, "_is_pid_alive", return_value=True):
                self.assertFalse(g2.acquire(use_qt_ipc=False))
            # Restore own PID and release
            g1._lock_file.write_text(str(os.getpid()), encoding="utf-8")
            g1.release()
            self.assertFalse(g1.is_primary)
            self.assertTrue(g2.acquire(use_qt_ipc=False))
            g2.release()

    def test_backend_shutdown_cancels_offline_agent_tts_audio_and_pending_permissions(self):
        import pc_assistant as pa

        mgr = pa.LiveManager(None)
        mgr.voice_output = True
        mgr.mic_on = True

        # Simulate a pending permission waiter and active offline agent + active SAPI voice
        perm_evt = threading.Event()
        pa._pending_perms["test_req_shutdown"] = {"event": perm_evt, "decision": None}
        fake_voice = mock.Mock()
        mgr._sapi_voice = fake_voice

        class _ImmediateAwaitable:
            def __init__(self, val):
                self._val = val

            def __await__(self):
                if False:
                    yield None
                return self._val

        with mock.patch.object(pa.offline_agent, "cancel") as m_cancel_agent, \
             mock.patch.object(mgr.player, "interrupt") as m_interrupt_audio, \
             mock.patch.object(mgr.player, "close") as m_close_audio, \
             mock.patch.object(pa.gesture_manager, "stop") as m_stop_gesture, \
             mock.patch.object(pa.ducker, "stop") as m_stop_ducker, \
             mock.patch("pc_assistant.asyncio.to_thread", side_effect=lambda fn, *a, **kw: _ImmediateAwaitable(fn(*a, **kw))):
            _drive_coro_sync(mgr.shutdown())
            # Second shutdown is idempotent
            _drive_coro_sync(mgr.shutdown())

        m_cancel_agent.assert_called_once()
        m_interrupt_audio.assert_called_once()
        m_close_audio.assert_called_once()
        m_stop_gesture.assert_called_once()
        m_stop_ducker.assert_called_once()
        fake_voice.Speak.assert_called_once()
        self.assertTrue(perm_evt.is_set())
        self.assertEqual(pa._pending_perms["test_req_shutdown"]["decision"], "deny")
        pa._pending_perms.pop("test_req_shutdown", None)
        self.assertFalse(mgr.mic_on)
        self.assertTrue(mgr.closing)


async def _async_return(val):
    return val


class TestVoiceControlStateAndFourCombinations(unittest.TestCase):
    def test_four_independent_combinations(self):
        st = nvc.VoiceControlState(mic_listening=True, spoken_replies=True, wake_word_enabled=True)
        # 1. ON / ON
        self.assertEqual(st.interaction_mode_label(), "Voice Conversation")
        # 2. ON / OFF
        st.set_spoken_replies(False)
        self.assertTrue(st.mic_listening)
        self.assertFalse(st.spoken_replies)
        self.assertEqual(st.interaction_mode_label(), "Mic On • Text Replies")
        # 3. OFF / ON
        st.set_mic_listening(False, manual=True)
        st.set_spoken_replies(True)
        self.assertFalse(st.mic_listening)
        self.assertTrue(st.spoken_replies)
        self.assertTrue(st.user_manually_disabled_mic)
        self.assertEqual(st.interaction_mode_label(), "Typed Chat • Spoken Replies")
        # 4. OFF / OFF
        st.set_spoken_replies(False)
        self.assertEqual(st.interaction_mode_label(), "Text-Only Mode")

    def test_privacy_honesty_when_wake_word_is_active_vs_disabled(self):
        st = nvc.VoiceControlState(mic_listening=False, spoken_replies=True, wake_word_enabled=True)
        st.set_wake_word(enabled=True, active=True)
        self.assertIn("wake-word monitoring is still active", st.privacy_mic_status().lower())

        st.set_wake_word(enabled=False, active=False)
        self.assertIn("all microphone monitoring is off", st.privacy_mic_status().lower())

    def test_mode_switching_preserves_mic_and_spoken_reply_states(self):
        st = nvc.VoiceControlState(mic_listening=False, spoken_replies=False, wake_word_enabled=True)
        st.set_mic_listening(False, manual=True)
        # Offline -> Online -> Hybrid -> Offline
        st.set_ai_backend("offline", "Ollama (qwen2.5:3b)")
        st.set_ai_backend("online", "Gemini Live")
        st.set_ai_backend("hybrid", "Reconnecting")
        st.set_ai_backend("offline", "Local Commands")
        self.assertFalse(st.mic_listening)
        self.assertFalse(st.spoken_replies)
        self.assertTrue(st.user_manually_disabled_mic)

        # Now Mic ON + Spoken OFF preserved across Online <-> Offline
        st.set_mic_listening(True, manual=True)
        st.set_spoken_replies(False)
        st.set_ai_backend("online", "Gemini Live")
        st.set_ai_backend("offline", "Ollama (qwen2.5:3b)")
        self.assertTrue(st.mic_listening)
        self.assertFalse(st.spoken_replies)


class TestVoiceControlCommandLayer(unittest.TestCase):
    def test_all_voice_commands_recognized_deterministically(self):
        cases = [
            ("Nova, start listening.", "start_listening"),
            ("Hey Nova start listening", "start_listening"),
            ("Nova, stop listening.", "stop_listening"),
            ("turn off microphone", "stop_listening"),
            ("Nova, enable voice replies.", "enable_spoken_replies"),
            ("Nova, resume speaking.", "enable_spoken_replies"),
            ("Nova, mute your voice.", "disable_spoken_replies"),
            ("disable spoken replies", "disable_spoken_replies"),
            ("Nova, stop speaking.", "stop_speaking"),
            ("stop talking", "stop_speaking"),
            ("Nova, switch to text mode.", "text_mode"),
            ("Nova, switch to voice mode.", "voice_mode"),
        ]
        for phrase, expected in cases:
            res = nvc.match_voice_control_command(phrase)
            self.assertIsNotNone(res, f"Expected match for: {phrase}")
            self.assertEqual(res["control"], expected, f"Wrong control for: {phrase}")

        # Normal user questions must NOT be hijacked by the control layer
        self.assertIsNone(nvc.match_voice_control_command("Explain how microphones work"))
        self.assertIsNone(nvc.match_voice_control_command("open calculator"))

    def test_live_manager_controls_are_independent_and_keep_session_open(self):
        import pc_assistant as pa

        mgr = pa.LiveManager(None)
        mgr.voice_output = True
        fake_session = mock.Mock()
        mgr.session = fake_session
        mgr.ready.set()

        # Disable spoken replies -> stops speech immediately, keeps session & mic untouched
        with mock.patch.object(mgr.player, "interrupt") as m_int, \
             mock.patch.object(mgr, "stop_offline_tts") as m_stop_tts:
            res = _drive_coro_sync(mgr.handle_voice_control_async("disable_spoken_replies", from_voice=True))
        self.assertTrue(res["ok"])
        self.assertFalse(mgr.voice_output)
        self.assertFalse(mgr.voice_state.spoken_replies)
        self.assertIs(mgr.session, fake_session)  # Gemini Live NOT disconnected
        m_int.assert_called_once()
        m_stop_tts.assert_called_once()

        # Enable spoken replies again
        res2 = _drive_coro_sync(mgr.handle_voice_control_async("enable_spoken_replies", from_voice=False))
        self.assertTrue(res2["ok"])
        self.assertTrue(mgr.voice_output)
        self.assertTrue(mgr.voice_state.spoken_replies)

        # Stop speaking interrupts current speech WITHOUT disabling future spoken replies
        with mock.patch.object(mgr.player, "interrupt") as m_int2, \
             mock.patch.object(mgr, "stop_offline_tts") as m_stop_tts2:
            res3 = _drive_coro_sync(mgr.handle_voice_control_async("stop_speaking", from_voice=True))
        self.assertTrue(res3["ok"])
        self.assertTrue(mgr.voice_output)  # still enabled for future turns!
        m_int2.assert_called_once()
        m_stop_tts2.assert_called_once()

        # Typed offline commands still work while mic is OFF
        mgr.mic_on = False
        mgr.voice_output = False
        mgr.ready.clear()
        mgr.session = None
        self.assertTrue(mgr.execute_offline_action_sync("calculate 12 * 12", from_voice=False))

    def test_tts_epoch_prevents_overlapping_speech(self):
        import pc_assistant as pa

        mgr = pa.LiveManager(None)
        mgr.voice_output = True
        e0 = mgr._tts_epoch
        mgr.stop_offline_tts()
        self.assertEqual(mgr._tts_epoch, e0 + 1)
        self.assertFalse(mgr.voice_state.speaking)


class TestChatQuickAccessUI(unittest.TestCase):
    def test_chat_quick_controls_and_status_indicators_exist(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import ui

        app = QApplication.instance() or QApplication([])
        with mock.patch.object(ui.Nova, "_auto_start_backend", return_value=None), \
             mock.patch.object(ui.Nova, "_register_win32_global_hotkeys", return_value=None), \
             mock.patch.object(ui.WakeWordListener, "start", return_value=None):
            win = ui.Nova()
            try:
                self.assertTrue(hasattr(win, "chat_mic_btn"))
                self.assertTrue(hasattr(win, "chat_voice_btn"))
                self.assertTrue(hasattr(win, "chat_stop_speak_btn"))
                self.assertTrue(hasattr(win, "chat_mode_btn"))
                self.assertTrue(hasattr(win, "chat_status_bar"))
                self.assertTrue(hasattr(win, "mode_badge"))
                self.assertTrue(hasattr(win, "wake_badge"))

                # Toggle spoken replies independently via UI helper
                before_voice = win.voice_output
                win.toggle_voice_output()
                self.assertEqual(win.voice_output, not before_voice)
                self.assertIn("Spoken Replies:", win.chat_status_bar.text())
                self.assertIn("Wake Word:", win.chat_status_bar.text())
                # Restore
                win.toggle_voice_output()
            finally:
                win.quit_app()
        self.assertIsNotNone(app)

    def test_text_mode_stays_locked_and_blocks_wake_word_auto_reactivation(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import ui

        app = QApplication.instance() or QApplication([])
        with mock.patch.object(ui.Nova, "_auto_start_backend", return_value=None), \
             mock.patch.object(ui.Nova, "_register_win32_global_hotkeys", return_value=None), \
             mock.patch.object(ui.WakeWordListener, "start", return_value=None), \
             mock.patch.object(ui.WakeWordListener, "pause") as m_pause, \
             mock.patch.object(ui.WakeWordListener, "resume") as m_resume:
            win = ui.Nova()
            try:
                # Start cleanly in Voice Mode state, then switch to Text Mode
                win.text_mode_active = False
                win.voice_output = True
                win.mic_on = True
                win.toggle_text_voice_mode()

                self.assertTrue(win.text_mode_active)
                self.assertTrue(win.voice_state.text_mode_locked)
                self.assertFalse(win.voice_output)
                self.assertIn("Paused (Text Mode)", win.wake_badge.text())
                self.assertTrue(m_pause.called)
                m_resume.reset_mock()

                # Backend mic: off event during Text Mode must NOT resume wake_listener
                win._on_event({"t": "mic", "v": "off"})
                m_resume.assert_not_called()
                self.assertTrue(win.text_mode_active)

                # Backend Online/Offline/Hybrid transitions must NOT exit Text Mode
                win.voice_state.set_ai_backend("online", "Gemini Live")
                win.voice_state.set_ai_backend("offline", "Ollama (qwen2.5:3b)")
                win.voice_state.set_ai_backend("hybrid", "Reconnecting")
                self.assertTrue(win.voice_state.text_mode_locked)
                self.assertFalse(win.voice_state.mic_listening)
                self.assertFalse(win.voice_state.spoken_replies)

                # Stray wake-word trigger while in Text Mode must NOT show companion bot or start mic
                with mock.patch.object(win.companion_bridge, "on_wake_word") as m_bot_wake, \
                     mock.patch.object(win, "send_cmd") as m_send:
                    win._handle_wake_word_qt()
                m_bot_wake.assert_not_called()
                self.assertFalse(win.mic_on)
                self.assertFalse(win.mic_starting)
                self.assertTrue(win.text_mode_active)

                # Manual switch back to Voice Mode works cleanly
                with mock.patch.object(win, "ensure_backend", return_value=True), \
                     mock.patch.object(win, "send_cmd") as m_send_vm:
                    win.toggle_text_voice_mode()
                self.assertFalse(win.text_mode_active)
                self.assertFalse(win.voice_state.text_mode_locked)
                self.assertTrue(win.voice_output)
                self.assertTrue(win.mic_starting)
                m_send_vm.assert_called()
            finally:
                # Reset persisted text_mode_active setting after test
                win.settings.setValue("text_mode_active", "false")
                win.quit_app()
        self.assertIsNotNone(app)

    def test_window_x_close_exits_by_default_and_respects_explicit_tray_opt_in(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QCloseEvent
        from PySide6.QtWidgets import QApplication
        import ui

        app = QApplication.instance() or QApplication([])
        with mock.patch.object(ui.Nova, "_auto_start_backend", return_value=None), \
             mock.patch.object(ui.Nova, "_register_win32_global_hotkeys", return_value=None), \
             mock.patch.object(ui.WakeWordListener, "start", return_value=None):
            win = ui.Nova()
            try:
                # 1. Explicit minimize_to_tray=True -> X hides window without quitting
                win.minimize_to_tray = True
                ev_tray = QCloseEvent()
                with mock.patch.object(win, "quit_app") as m_quit_tray:
                    win.closeEvent(ev_tray)
                self.assertFalse(ev_tray.isAccepted())
                m_quit_tray.assert_not_called()

                # 2. Default minimize_to_tray=False -> X fully exits via centralized quit_app() -> lifecycle.shutdown_all()
                win.minimize_to_tray = False
                ev_exit = QCloseEvent()
                with mock.patch.object(win.lifecycle, "shutdown_all", wraps=win.lifecycle.shutdown_all) as m_shut, \
                     mock.patch.object(ui.QApplication, "quit") as m_app_quit:
                    win.closeEvent(ev_exit)
                self.assertTrue(ev_exit.isAccepted())
                self.assertTrue(win._quitting)
                m_shut.assert_called_once()
                m_app_quit.assert_called_once()
                self.assertEqual(win.lifecycle.state, "stopped")
            finally:
                win.settings.setValue("close_to_tray", "false")
                if not win._quitting:
                    win.quit_app()
        self.assertIsNotNone(app)


if __name__ == "__main__":
    unittest.main()
