"""Nova - single-file glass UI for the PC voice assistant. Run: python ui.py"""
import sys, math, random, time, os, html, json, winreg, threading
from pathlib import Path
from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer, QSettings, QPointF, QRectF, QPoint, QEvent, Signal, QObject, QThread, QPropertyAnimation, QVariantAnimation, QEasingCurve, QAbstractAnimation
from PySide6.QtGui import QColor as C, QPainter, QPen, QBrush, QRadialGradient, QLinearGradient, QPainterPath, QCursor, QGuiApplication, QIcon, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QGridLayout, QStackedWidget, QTextEdit, QLineEdit, QMessageBox, QInputDialog, QSlider, QSizeGrip, QMenu, QSystemTrayIcon, QDialog, QListWidget, QListWidgetItem, QScrollArea, QComboBox, QFileDialog, QProgressBar)
from permissions import CATEGORIES, PermissionManager
from wake_word import WakeWordListener
from nova_companion_bridge import CompanionBridge
from nova_lifecycle import LifecycleManager, SingleInstanceGuard
from nova_voice_control import VoiceControlState, match_voice_control_command
from nova_offline_ai import OllamaClient, RECOMMENDED_4GB_TEXT_MODELS, RECOMMENDED_4GB_VISION_MODELS
from nova_local_knowledge import knowledge_store

_UI_OLLAMA = OllamaClient()
from nova_screen_pointing import RegionSelector, ScreenPointingService
from nova_walkthrough import Walkthrough, WalkthroughStep, walkthrough_from_plan
from nova_workflows import (
    Workflow, WorkflowStep, WorkflowRecorder, WorkflowStore, WorkflowPlayer,
    list_workflow_templates, instantiate_template, WorkflowDebugger,
)
from nova_updater import (
    APP_VERSION, RELEASES_PAGE_URL, UpdateCheckResult, DownloadResult,
    check_for_updates, download_update_installer, format_size_mb,
    is_trusted_release_url, launch_verified_installer, open_trusted_release_url,
)

from bot import DesktopBot, STATES, cl, ease, glow, gold, hl, lgrad, onyx, pearl, rgrad, st
from ui_dialogs import (
    MicSensitivityWidget, MicWaveformCanvas, PermissionDialog,
    WalkthroughHighlightOverlay, WorkflowEditorDialog, WorkflowStepConfirmDialog,
    WorkflowTemplatesDialog, WorkflowDebuggerDialog,
)
from ui_theme import (
    APP_NAME, BOT_NAMES, GlassRoot, MENU_CSS, NavPill, ORB, OrbWidget, ROOT,
    STYLE, WakeSignal, _make_tray_icon, apply_backdrop, is_run_at_startup,
    set_run_at_startup,
)


class Nova(QMainWindow):
    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint); self.setWindowTitle(f'{APP_NAME} — Personal AI'); self.resize(1240, 800); self.setMinimumSize(980, 650)
        self.setAttribute(Qt.WA_TranslucentBackground); self.setAttribute(Qt.WA_NoSystemBackground); self.setAutoFillBackground(False)
        self.settings = QSettings('Nova', 'Nova'); self.glass_mode = 'clear' if str(self.settings.value('glass_mode', 'frost')) == 'clear' else 'frost'
        try: self.tint = cl(int(self.settings.value('tint', 224)), 145, 252)
        except (TypeError, ValueError): self.tint = 224
        self.wake_enabled = str(self.settings.value('wake_enabled', 'true')).lower() != 'false'
        # Default: clicking X on the main window fully exits Glass Nova (close_to_tray is opt-in)
        self.minimize_to_tray = str(self.settings.value('close_to_tray', 'false')).lower() == 'true'
        self.text_mode_active = str(self.settings.value('text_mode_active', 'false')).lower() == 'true'
        try: self.mic_sensitivity = max(0, min(100, int(self.settings.value('mic_sensitivity', 68))))
        except (TypeError, ValueError): self.mic_sensitivity = 68
        self.mic_auto_nearest = str(self.settings.value('mic_auto_nearest', 'false')).lower() == 'true'
        self._mic_sens_widgets = []
        self._force_quit = False
        self._quitting = False
        self._instance_guard = None
        self.lifecycle = LifecycleManager(on_log=self.append_log)
        saved_voice_out = False if self.text_mode_active else (str(self.settings.value('voice_output', 'true')).lower() != 'false')
        self.voice_state = VoiceControlState(
            mic_listening=False,
            spoken_replies=saved_voice_out,
            wake_word_enabled=self.wake_enabled,
            text_mode_locked=self.text_mode_active,
        )
        self.ai_mode = 'hybrid'
        self.ai_provider = 'Local'
        self.gesture_active = False
        self._active_perm_dialogs = {}
        self.chat_entries, self._awaiting_reply = [], False                  # one shared transcript for voice + typed chat
        self._chat_timer = QTimer(self, singleShot=True, timeout=self._render_chat)
        self.voice_output = saved_voice_out
        self.mic_on = self.mic_starting = self._stopping = self._fatal_shown = False
        self.backend_state, self._pending, self._out_raw, self._err_raw, self._err_tail = 'stopped', [], b'', b'', []
        self.process = QProcess(self); self.process.setWorkingDirectory(str(ROOT))
        env = QProcessEnvironment.systemEnvironment(); env.insert('PYTHONIOENCODING', 'utf-8'); env.insert('PYTHONUTF8', '1'); env.insert('PYTHONUNBUFFERED', '1')
        self.process.setProcessEnvironment(env)
        self.process.started.connect(self._backend_started)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self.backend_finished)
        self.process.errorOccurred.connect(self._backend_error)
        self.bots, self.assistant_state, self._bot_error = {}, 'idle', ''
        self.bot_kind = str(self.settings.value('bot_kind', 'mechanical'))
        if self.bot_kind not in BOT_NAMES: self.bot_kind = 'mechanical'
        # If settings contain an unsupported companion choice, use NOVA's original robot.
        self.orb = self._make_bot(self.bot_kind)
        if self.orb is None:
            self.bot_kind = 'mechanical'; self.settings.setValue('bot_kind', self.bot_kind)
            self.orb = self._make_bot('mechanical')
        self.companion_bridge = CompanionBridge(
            show_companion=self._show_companion_only,
            hide_companion=self._hide_companion_only,
            bring_main_window_forward=self.open_main_window,
        )
        self.walkthrough_overlay = WalkthroughHighlightOverlay()
        self._active_region_selector = None
        self._saved_workflows_cache = []
        self._workflow_recording = False
        self.screen_pointing_service = ScreenPointingService(
            request_screen_permission=self._request_local_screen_permission,
            capture_region_jpeg=self._capture_selected_region_jpeg,
            analyze_region=self._analyze_selected_region,
        )
        self.root = GlassRoot(); self.root.set_tint(self.tint); self.setCentralWidget(self.root)
        self.lay = QVBoxLayout(self.root); self.lay.setContentsMargins(32, 26, 32, 32); self.lay.setSpacing(10)
        tb = QHBoxLayout(); tb.setContentsMargins(8, 2, 2, 0); tb.setSpacing(8)
        mark = QLabel(f'◉  {APP_NAME.upper()}'); mark.setStyleSheet('font-weight:700;color:#b7f4ff'); tb.addWidget(mark)
        self.wake_badge = QLabel('👂 Wake Word: Active (Local)')
        self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(30,120,180,90);border:1px solid rgba(80,210,255,140);border-radius:10px;font-size:9pt;font-weight:600;color:#a5f1ff')
        self.wake_badge.setToolTip('Local offline wake-word monitoring status (separate from conversational microphone listening)')
        tb.addWidget(self.wake_badge)
        self.mode_badge = QLabel('⚡ Hybrid • Starting')
        self.mode_badge.setStyleSheet('padding:3px 10px;background:rgba(45,75,145,110);border:1px solid rgba(110,180,255,135);border-radius:10px;font-size:9pt;font-weight:600;color:#cde8ff')
        self.mode_badge.setToolTip('Current AI mode (Offline / Online / Hybrid) and active provider')
        tb.addWidget(self.mode_badge)
        self.screen_badge = QLabel('👁 Screen Active')
        self.screen_badge.setStyleSheet('padding:3px 10px;background:rgba(20,160,180,140);border:1px solid #39e5ff;border-radius:10px;font-size:9pt;font-weight:700;color:#ffffff')
        self.screen_badge.setToolTip('Nova is actively observing or capturing your screen')
        self.screen_badge.hide()
        tb.addWidget(self.screen_badge)
        self.control_badge = QLabel('🖱 Control Active')
        self.control_badge.setStyleSheet('padding:3px 10px;background:rgba(220,110,20,150);border:1px solid #ffa339;border-radius:10px;font-size:9pt;font-weight:700;color:#ffffff')
        self.control_badge.setToolTip('Nova is interacting with your mouse or keyboard')
        self.control_badge.hide()
        tb.addWidget(self.control_badge)
        self.gesture_badge = QLabel('🖐 Gesture Active')
        self.gesture_badge.setStyleSheet('padding:3px 10px;background:rgba(40,180,100,150);border:1px solid #55ff99;border-radius:10px;font-size:9pt;font-weight:700;color:#ffffff')
        self.gesture_badge.setToolTip('Hand Gesture Mouse Control is active via webcam')
        self.gesture_badge.hide()
        tb.addWidget(self.gesture_badge)
        self.state_badge = QLabel('● Idle')
        self.state_badge.setStyleSheet('padding:3px 10px;background:rgba(25,95,145,110);border:1px solid rgba(90,210,255,130);border-radius:10px;font-size:9pt;font-weight:600;color:#b8f4ff')
        self.state_badge.setToolTip('Current assistant runtime state')
        tb.addWidget(self.state_badge)
        tb.addStretch(1)
        for txt, fn in (('—', self.showMinimized), ('□', self._toggle_max), ('×', self.close)):
            b = self.button(txt, fn, 'ctl'); b.setFixedSize(34, 28); tb.addWidget(b)
            if txt == '×':
                self.close_btn = b
                self.close_btn.setToolTip(
                    'Minimize Glass Nova to System Tray (use Tray → Exit Glass Nova to quit)'
                    if self.minimize_to_tray
                    else 'Close & Exit Glass Nova'
                )
        self.lay.addLayout(tb); content = QHBoxLayout(); content.setSpacing(16); self.lay.addLayout(content, 1)
        side = QFrame(); side.setObjectName('sidebar'); side.setFixedWidth(218); sl = QVBoxLayout(side); sl.setContentsMargins(16, 18, 16, 18); sl.setSpacing(8)
        brand = QLabel(f'◉  {APP_NAME.upper()}'); brand.setObjectName('brand'); sl.addWidget(brand)
        sub = QLabel('PERSONAL AI ASSISTANT'); sub.setObjectName('muted'); sub.setStyleSheet('font-size:8pt;letter-spacing:1px'); sl.addWidget(sub); sl.addSpacing(20)
        self.stack, self.nav_buttons = QStackedWidget(), []
        for i, (name, icon) in enumerate((('Overview', '⌂'), ('Chat && Voice', '◉'), ('Tasks && Memory', '▤'), ('Computer Tools', '▦'), ('Settings', '⚙'))):
            b = self.button(f'{icon}   {name}', lambda _=False, i=i: self.change_page(i), 'nav'); b.setCheckable(True); sl.addWidget(b); self.nav_buttons.append(b)
        sl.addStretch(1); self.status_dot = QLabel('●  Assistant stopped'); self.status_dot.setObjectName('muted'); sl.addWidget(self.status_dot)
        self.sidebar_gesture_btn = self.button('🖐  Hand Gesture: Off', self.toggle_hand_gesture)
        sl.addWidget(self.sidebar_gesture_btn)
        sl.addWidget(self.button('◉  Toggle Floating Bot', self.toggle_orb)); content.addWidget(side)
        right = QVBoxLayout(); right.setSpacing(14); content.addLayout(right, 1); top = QHBoxLayout()
        title = QLabel('Your personal AI workspace'); title.setObjectName('heading'); top.addWidget(title); top.addStretch()
        self.voice_btn = self.button('▶  Start voice assistant', self.toggle_voice, 'primary'); top.addWidget(self.voice_btn); right.addLayout(top)
        for pg in (self._overview(), self._chat(), self._tasks(), self._tools(), self._settings()): self.stack.addWidget(pg)
        right.addWidget(self.stack, 1); self.change_page(0); self.setStyleSheet(STYLE)
        self.grip = QSizeGrip(self.root); self.grip.setFixedSize(16, 16); self._glass_text()
        self.pill = NavPill(side); self.pill.lower(); self.pill.hide(); curve = QEasingCurve(QEasingCurve.OutBack); curve.setOvershoot(1.1)
        self.pill_anim = QPropertyAnimation(self.pill, b'geometry', self); self.pill_anim.setDuration(430); self.pill_anim.setEasingCurve(curve)
        self.boost_anim = QVariantAnimation(self, startValue=1.0, endValue=0.0, duration=560, valueChanged=self.pill.set_boost); self.boost_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._layout_mode()
        # System Tray Icon
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(_make_tray_icon())
        self.tray_icon.setToolTip(f'{APP_NAME} — Personal AI Assistant')

        tray_menu = QMenu()
        tray_menu.setStyleSheet(MENU_CSS)
        tray_menu.addAction('Open Glass Nova', self.open_main_window)
        self.tray_gesture_act = tray_menu.addAction('Hand Gesture Mouse: Off', self.toggle_hand_gesture)
        self.tray_wake_act = tray_menu.addAction('Wake Word: Active', self.toggle_wake_word)
        self.tray_voice_out_act = tray_menu.addAction('Spoken Replies: On', self.toggle_voice_output)
        self.tray_startup_act = tray_menu.addAction('Start with Windows', self.toggle_startup)
        self.tray_startup_act.setCheckable(True)
        self.tray_startup_act.setChecked(is_run_at_startup())
        tray_menu.addSeparator()
        tray_menu.addAction(f'Exit {APP_NAME}', self.quit_app)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

        # Wake-word listener & signals
        self._wake_signal = WakeSignal()
        self._wake_signal.detected.connect(self._handle_wake_word_qt)
        self._wake_signal.status.connect(self._handle_wake_status_qt)

        self.wake_listener = WakeWordListener(
            on_detected=lambda: self._wake_signal.detected.emit(),
            on_status_change=lambda s: self._wake_signal.status.emit(s)
        )
        if self.wake_enabled and not self.text_mode_active:
            self.wake_listener.start()
        elif self.text_mode_active:
            self._update_wake_badge(paused=True)
        else:
            self._handle_wake_status_qt('stopped')
        self.shortcut_key_str = str(self.settings.value('companion_shortcut', 'Ctrl+Shift+N') or 'Ctrl+Shift+N')
        self._companion_shortcut = QShortcut(QKeySequence(self.shortcut_key_str), self)
        self._companion_shortcut.setContext(Qt.ApplicationShortcut)
        self._companion_shortcut.activated.connect(self.toggle_orb)
        self._point_shortcut = QShortcut(QKeySequence('Ctrl+Shift+P'), self)
        self._point_shortcut.setContext(Qt.ApplicationShortcut)
        self._point_shortcut.activated.connect(self.start_screen_pointing_ui)
        self._global_hotkeys_registered = False
        self._register_lifecycle_components()
        QTimer.singleShot(300, self._register_win32_global_hotkeys)
        QTimer.singleShot(200, self._auto_start_backend)

    def _register_lifecycle_components(self):
        """Register all UI-owned resources with LifecycleManager for ordered, error-isolated shutdown."""
        self.lifecycle.register('instance_guard', stop_fn=lambda: self._instance_guard.release() if getattr(self, '_instance_guard', None) else None, priority=10)
        self.lifecycle.register('tray_icon', stop_fn=lambda: self.tray_icon.hide() if hasattr(self, 'tray_icon') else None, priority=20)
        self.lifecycle.register('companions', stop_fn=self._close_all_companions, priority=30)
        self.lifecycle.register('backend', stop_fn=self.shutdown_backend, priority=40)
        self.lifecycle.register('wake_listener', stop_fn=lambda: self.wake_listener.stop() if getattr(self, 'wake_listener', None) else None, priority=50)
        self.lifecycle.register('update_download_worker', stop_fn=self._stop_download_worker, priority=54)
        self.lifecycle.register('update_worker', stop_fn=self._stop_update_worker, priority=55)
        self.lifecycle.register('overlays_and_dialogs', stop_fn=self._close_overlays_and_dialogs, priority=60)
        self.lifecycle.register('hotkeys', stop_fn=self._unregister_win32_global_hotkeys, priority=70)
        self.lifecycle.start_all()

    def _close_overlays_and_dialogs(self):
        if hasattr(self, '_chat_timer') and self._chat_timer:
            self._chat_timer.stop()
        if getattr(self, '_active_region_selector', None) is not None:
            self._active_region_selector.close()
            self._active_region_selector = None
        for dlg in list(getattr(self, '_active_perm_dialogs', {}).values()):
            dlg.reject()
        self._active_perm_dialogs.clear()
        if hasattr(self, 'walkthrough_overlay') and self.walkthrough_overlay:
            self.walkthrough_overlay.hide_target()
            self.walkthrough_overlay.close()

    def _close_all_companions(self):
        for b in self.bots.values():
            b.close()

    # -- builders
    def button(self, text, fn, name=None):
        b = QPushButton(text); b.clicked.connect(fn)
        if name: b.setObjectName(name)
        if name != 'ctl': b.setMinimumHeight(34)
        return b
    def textbox(self, ph):
        t = QTextEdit(); t.setReadOnly(True); t.setPlaceholderText(ph); return t
    def panel(self, title, desc=None):
        f = QFrame(); f.setObjectName('panel'); v = QVBoxLayout(f); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(8)
        h = QLabel(title); h.setStyleSheet('font-size:13pt;font-weight:650'); v.addWidget(h)
        if desc: d = QLabel(desc); d.setObjectName('muted'); d.setWordWrap(True); d.setMinimumWidth(120); v.addWidget(d)
        return f, v
    def _create_mic_sensitivity_widget(self):
        w = MicSensitivityWidget(self.mic_sensitivity, self.mic_auto_nearest, self)
        w.sensitivityChanged.connect(self._on_ui_mic_sensitivity_changed)
        self._mic_sens_widgets.append(w)
        return w

    def _on_ui_mic_sensitivity_changed(self, sensitivity: int, auto_nearest: bool, calibrate_now: bool):
        self.mic_sensitivity = max(0, min(100, int(sensitivity)))
        self.mic_auto_nearest = bool(auto_nearest)
        self.settings.setValue('mic_sensitivity', self.mic_sensitivity)
        self.settings.setValue('mic_auto_nearest', 'true' if self.mic_auto_nearest else 'false')
        for w in self._mic_sens_widgets:
            w.sync_state(self.mic_sensitivity, self.mic_auto_nearest)
        if self.backend_running():
            self.send_cmd(
                cmd='mic_sensitivity',
                sensitivity=self.mic_sensitivity,
                auto_nearest=self.mic_auto_nearest,
                calibrate_now=bool(calibrate_now),
            )

    def _overview(self):
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(14); hero, h = self.panel('Welcome back', 'A calmer, clearer home for your AI assistant.')
        self.hero_orb = OrbWidget(); self.hero_orb.setMinimumHeight(240); h.addWidget(self.hero_orb, 1)
        self.big_status = QLabel('Ready when you are'); self.big_status.setAlignment(Qt.AlignCenter); self.big_status.setStyleSheet('font-size:15pt;font-weight:600'); h.addWidget(self.big_status)
        self.overview_voice_btn = self.button('▶  Start voice assistant', self.toggle_voice, 'primary'); h.addWidget(self.overview_voice_btn); v.addWidget(hero, 1); row = QHBoxLayout()
        self.mic_sens_overview = self._create_mic_sensitivity_widget()
        b, bv = self.panel('Activity monitor', 'Recent status messages appear here.'); self.activity = self.textbox('Assistant activity will appear here when started.'); self.activity.setMaximumHeight(115); bv.addWidget(self.activity)
        row.addWidget(self.mic_sens_overview, 1); row.addWidget(b, 1); v.addLayout(row); return w
    def _chat(self):
        w = QWidget(); v = QVBoxLayout(w); pnl, p = self.panel('Chat & Voice', 'Type a message or use the live voice assistant. Toggle microphone listening and spoken replies independently anytime.')
        # Compact live status strip clearly separating Mic Listening, Spoken Replies, Wake Word, and AI Mode
        self.chat_status_bar = QLabel('')
        self.chat_status_bar.setObjectName('muted')
        self.chat_status_bar.setStyleSheet('font-size:8.5pt;padding:4px 8px;background:rgba(15,35,58,110);border:1px solid rgba(90,190,240,80);border-radius:8px;color:#b8e6ff')
        p.addWidget(self.chat_status_bar)

        self.chatlog = self.textbox(f'Type a message below to chat with {APP_NAME}. Voice transcriptions also appear here.'); p.addWidget(self.chatlog, 1)

        # Quick-access independent voice & spoken-reply controls right above chat input
        qbar = QHBoxLayout(); qbar.setSpacing(8)
        self.chat_mic_btn = self.button('🎙  Mic: OFF', self.toggle_voice)
        self.chat_mic_btn.setToolTip('Toggle conversational microphone listening ON/OFF without ending chat or disconnecting Gemini Live')
        self.chat_voice_btn = self.button('🔊  Spoken Replies: ON', self.toggle_voice_output)
        self.chat_voice_btn.setToolTip('Toggle spoken replies ON/OFF independently of microphone listening (immediately stops speech when turned off)')
        self.chat_stop_speak_btn = self.button('⏹  Stop Speaking', self.stop_speaking_ui)
        self.chat_stop_speak_btn.setToolTip('Immediately stop Nova from speaking right now without disabling future spoken replies')
        self.chat_mode_btn = self.button('💬  Text Mode', self.toggle_text_voice_mode)
        self.chat_mode_btn.setToolTip('Quick shortcut to switch between Text-Only Mode (Mic OFF, Voice OFF) and Voice Mode (Mic ON, Voice ON)')
        qbar.addWidget(self.chat_mic_btn)
        qbar.addWidget(self.chat_voice_btn)
        qbar.addWidget(self.chat_stop_speak_btn)
        qbar.addWidget(self.chat_mode_btn)
        qbar.addStretch(1)
        p.addLayout(qbar)

        row = QHBoxLayout()
        self.chat_input = QLineEdit(); self.chat_input.setPlaceholderText(f'Message {APP_NAME}… (or type "stop listening", "mute your voice", "stop speaking")'); self.chat_input.returnPressed.connect(self.send_chat); row.addWidget(self.chat_input, 1)
        self.send_button = self.button('Send', self.send_chat, 'primary'); row.addWidget(self.send_button)
        p.addLayout(row); v.addWidget(pnl, 1)
        self._sync_voice_control_ui()
        return w
    def _tasks(self):
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(14)
        tp, tv = self.panel('Tasks & Reminders', 'Manage tracked goals, tasks, and scheduled reminders stored locally.')
        self.tasks_view = self.textbox('No tracked tasks yet. Add one below or ask Nova by voice.')
        tv.addWidget(self.tasks_view, 1)
        t_btns = QHBoxLayout()
        t_btns.addWidget(self.button('＋  Add Task', self._ui_add_task, 'primary'))
        t_btns.addWidget(self.button('✓  Complete Task', self._ui_complete_task))
        t_btns.addWidget(self.button('🗑  Delete Task', self._ui_delete_task))
        t_btns.addWidget(self.button('↻  Refresh', self._refresh_tasks_and_memory))
        tv.addLayout(t_btns)
        v.addWidget(tp, 1)

        mp, mv = self.panel('Persistent Local Memory', 'Manage approved non-sensitive facts and preferences Nova remembers across sessions.')
        self.memories_view = self.textbox('No saved memories yet. Save one below or ask Nova to remember something.')
        mv.addWidget(self.memories_view, 1)
        m_btns = QHBoxLayout()
        m_btns.addWidget(self.button('＋  Save Memory', self._ui_add_memory, 'primary'))
        m_btns.addWidget(self.button('🗑  Forget Memory', self._ui_forget_memory))
        m_btns.addWidget(self.button('🧹  Clear All Memories', self._ui_clear_memories))
        mv.addLayout(m_btns)
        v.addWidget(mp, 1)
        QTimer.singleShot(100, self._refresh_tasks_and_memory)
        return w

    def _refresh_tasks_and_memory(self):
        try:
            import tools
            t_res = tools.task_control(action='list')
            tasks = t_res.get('tasks', []) if t_res.get('ok') else []
            if not tasks:
                self.tasks_view.setHtml('<i style="color:#9cb5c6">No tracked tasks yet.</i>')
            else:
                lines = []
                for t in tasks:
                    tid = html.escape(str(t.get('id', '')))
                    title = html.escape(str(t.get('title', '')))
                    status = str(t.get('status', 'active'))
                    due = html.escape(str(t.get('due_at') or ''))
                    details = html.escape(str(t.get('details') or ''))
                    badge = '✅ Completed' if status == 'completed' else '⏳ Active'
                    due_str = f' &nbsp;<span style="color:#7dc4ea">(Due: {due})</span>' if due else ''
                    det_str = f'<br><span style="color:#b8e2f8;font-size:9pt">{details}</span>' if details else ''
                    lines.append(f'<p style="margin:0 0 8px 0"><b>[{tid}]</b> {badge} — <b>{title}</b>{due_str}{det_str}</p>')
                self.tasks_view.setHtml(''.join(lines))

            m_res = tools.memory_control(action='list')
            mems = m_res.get('memories', []) if m_res.get('ok') else []
            if not mems:
                self.memories_view.setHtml('<i style="color:#9cb5c6">No saved memories yet.</i>')
            else:
                mlines = []
                for m in mems:
                    k = html.escape(str(m.get('key', '')))
                    val = html.escape(str(m.get('value', '')))
                    mlines.append(f'<p style="margin:0 0 6px 0"><b>• {k}:</b> {val}</p>')
                self.memories_view.setHtml(''.join(mlines))
        except Exception as e:
            self.append_log(f'[Tasks/Memory UI] Refresh error: {e}')

    def _ui_add_task(self):
        import tools
        title, ok = QInputDialog.getText(self, 'Add Task', 'Task title:')
        if not ok or not title.strip():
            return
        details, _ = QInputDialog.getText(self, 'Task Details', 'Optional details (or leave blank):')
        due, _ = QInputDialog.getText(self, 'Due Time (Optional)', 'Optional ISO 8601 due time (e.g. 2026-11-15T18:00:00) or leave blank:')
        res = tools.task_control(action='create', title=title.strip(), details=details.strip(), due_at=due.strip())
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not add task', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_complete_task(self):
        import tools
        tid, ok = QInputDialog.getText(self, 'Complete Task', 'Enter Task ID (shown in [brackets]):')
        if not ok or not tid.strip():
            return
        res = tools.task_control(action='complete', task_id=tid.strip().strip('[]'))
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not complete task', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_delete_task(self):
        import tools
        tid, ok = QInputDialog.getText(self, 'Delete Task', 'Enter Task ID to delete (shown in [brackets]):')
        if not ok or not tid.strip():
            return
        res = tools.task_control(action='delete', task_id=tid.strip().strip('[]'), confirm=True)
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not delete task', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_add_memory(self):
        import tools
        key, ok = QInputDialog.getText(self, 'Save Memory', 'Memory key / topic (e.g. Favorite IDE):')
        if not ok or not key.strip():
            return
        val, ok2 = QInputDialog.getText(self, 'Save Memory', f'Value to remember for "{key.strip()}":')
        if not ok2 or not val.strip():
            return
        res = tools.memory_control(action='remember', key=key.strip(), value=val.strip())
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not save memory', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_forget_memory(self):
        import tools
        key, ok = QInputDialog.getText(self, 'Forget Memory', 'Enter memory key to remove:')
        if not ok or not key.strip():
            return
        res = tools.memory_control(action='forget', key=key.strip())
        if not res.get('ok'):
            QMessageBox.warning(self, 'Could not forget memory', str(res.get('message', 'Error')))
        self._refresh_tasks_and_memory()

    def _ui_clear_memories(self):
        import tools
        ans = QMessageBox.question(self, 'Clear All Memories', 'Are you sure you want to delete all saved memories?', QMessageBox.Yes | QMessageBox.No)
        if ans == QMessageBox.Yes:
            tools.memory_control(action='clear', confirm=True)
            self._refresh_tasks_and_memory()
    def _tools(self):
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(7)
        for i, (label, kind) in enumerate((('🎯 Point at Screen', 'point_screen'), ('🖐 Toggle Hand Gesture Mouse', 'gesture'), ('Open Calculator', 'calc'), ('Open Notepad', 'notepad'), ('Open File Explorer', 'explorer'), ('Open Downloads', 'downloads'), ('Search Web', 'web'), ('System Status', 'status'))):
            b = self.button(label, lambda _=False, k=kind: self.quick_action(k)); grid.addWidget(b, i // 2, i % 2)
        p, pv = self.panel('Computer tools & Point at Screen', 'Quick actions run only when clicked. Point at Screen captures only your user-selected rectangle after permission approval.'); pv.addLayout(grid)
        pt_row = QHBoxLayout()
        self.point_question_input = QLineEdit()
        self.point_question_input.setPlaceholderText('Question for Point at Screen (e.g. "What does this error dialog mean?")…')
        self.point_question_input.returnPressed.connect(self.start_screen_pointing_ui)
        pt_row.addWidget(self.point_question_input, 1)
        pt_row.addWidget(self.button('🎯  Select Region & Explain', self.start_screen_pointing_ui, 'primary'))
        pv.addLayout(pt_row)
        self.point_result_view = self.textbox('Point at Screen explanation will appear here. Guidance only — Nova never clicks or types during screen pointing.')
        self.point_result_view.setMinimumHeight(46)
        self.point_result_view.setMaximumHeight(64)
        pv.addWidget(self.point_result_view)
        v.addWidget(p)

        bottom_row = QHBoxLayout(); bottom_row.setSpacing(10)

        # Guided Walkthroughs compact panel
        wt_p, wt_v = self.panel('Guided Walkthroughs', 'Step-by-step visual guidance (Next / Back / Pause / Stop). Nova highlights UI targets without clicking or typing.')
        wt_top = QHBoxLayout()
        self.walkthrough_goal_input = QLineEdit()
        self.walkthrough_goal_input.setPlaceholderText('Ask "Show me how to…" (e.g. change Windows display scale)…')
        self.walkthrough_goal_input.returnPressed.connect(self._ui_start_walkthrough)
        wt_top.addWidget(self.walkthrough_goal_input, 1)
        wt_top.addWidget(self.button('▶  Guide Me', self._ui_start_walkthrough, 'primary'))
        wt_v.addLayout(wt_top)
        self.walkthrough_view = self.textbox('No active walkthrough. Enter a topic above or ask Nova "show me how to do this".')
        self.walkthrough_view.setMinimumHeight(54)
        self.walkthrough_view.setMaximumHeight(82)
        wt_v.addWidget(self.walkthrough_view)
        wt_btns = QHBoxLayout()
        wt_btns.addWidget(self.button('◀  Back', lambda: self._ui_walkthrough_nav('back')))
        wt_btns.addWidget(self.button('⏸  Pause / Resume', self._ui_walkthrough_toggle_pause))
        wt_btns.addWidget(self.button('▶  Next', lambda: self._ui_walkthrough_nav('next'), 'primary'))
        wt_btns.addWidget(self.button('■  Stop', lambda: self._ui_walkthrough_nav('stop')))
        wt_v.addLayout(wt_btns)
        bottom_row.addWidget(wt_p, 1)

        # Reusable Semantic Workflows compact panel
        wf_p, wf_v = self.panel('Reusable Workflows', 'Record approved semantic actions (no raw keys or secrets). Review, edit, delete, or replay with per-step approval.')
        self.workflow_view = self.textbox('No saved workflows yet. Start recording or refresh.')
        self.workflow_view.setMinimumHeight(54)
        self.workflow_view.setMaximumHeight(82)
        wf_v.addWidget(self.workflow_view)
        wf_btns1 = QHBoxLayout()
        self.wf_rec_btn = self.button('●  Start Recording', self._ui_toggle_workflow_recording, 'primary')
        wf_btns1.addWidget(self.wf_rec_btn)
        wf_btns1.addWidget(self.button('▶  Replay Workflow', self._ui_replay_workflow))
        wf_btns1.addWidget(self.button('■  Cancel Replay', self._ui_cancel_workflow_replay))
        wf_v.addLayout(wf_btns1)
        wf_btns2 = QHBoxLayout()
        wf_btns2.addWidget(self.button('✎  Review / Edit', self._ui_edit_workflow))
        wf_btns2.addWidget(self.button('🗑  Delete', self._ui_delete_workflow))
        wf_btns2.addWidget(self.button('↻  Refresh', self._ui_refresh_workflows))
        wf_v.addLayout(wf_btns2)
        wf_btns3 = QHBoxLayout(); wf_btns3.setSpacing(6)
        wf_btns3.addWidget(self.button('✦  Templates', self._ui_open_workflow_templates))
        wf_btns3.addWidget(self.button('🪲  Debug / Dry-Run', self._ui_debug_workflow))
        wf_v.addLayout(wf_btns3)
        bottom_row.addWidget(wf_p, 1)

        v.addLayout(bottom_row, 1)
        QTimer.singleShot(250, self._ui_refresh_workflows)
        return w

    def _settings(self):
        w = QWidget(); outer = QVBoxLayout(w); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget(); v = QVBoxLayout(container); v.setContentsMargins(0, 0, 6, 0); v.setSpacing(10)
        p, pv = self.panel('Settings', 'Keep your Gemini API key in the local .env file; never paste it into chat.')
        self.orb_check = self.button(
            'Disable floating bot' if self.orb.isVisible() else 'Enable floating bot',
            self.toggle_orb)
        pv.addWidget(self.orb_check)
        self.gesture_btn = self.button('', self.toggle_hand_gesture); pv.addWidget(self.gesture_btn); self._update_gesture_ui()
        self.bot_btn = self.button('', self.choose_bot); pv.addWidget(self.bot_btn); self._bot_text()
        self.voice_out_btn = self.button('', self.toggle_voice_output); pv.addWidget(self.voice_out_btn); self._voice_out_text()

        # Wake word toggle
        self.wake_btn = self.button('', self.toggle_wake_word); pv.addWidget(self.wake_btn); self._update_wake_btn_text()

        # Start with Windows toggle
        self.startup_btn = self.button('', self.toggle_startup); pv.addWidget(self.startup_btn); self._update_startup_btn_text()

        # Minimize to Tray toggle
        self.tray_toggle_btn = self.button('', self.toggle_minimize_to_tray); pv.addWidget(self.tray_toggle_btn); self._update_tray_btn_text()

        self.glass_btn = self.button('', self.cycle_glass); pv.addWidget(self.glass_btn)
        tl = QLabel('Glass tint — left is clearest, right is easier to read'); tl.setObjectName('muted'); pv.addWidget(tl)
        self.tint_slider = QSlider(Qt.Horizontal); self.tint_slider.setRange(145, 252); self.tint_slider.setValue(cl(self.tint, 145, 252)); self.tint_slider.valueChanged.connect(self.set_tint); pv.addWidget(self.tint_slider)

        # Microphone Sensitivity & AUTO NEAREST HUD section
        self.mic_sens_settings = self._create_mic_sensitivity_widget()
        pv.addWidget(self.mic_sens_settings)

        # Offline AI (Ollama) & Local Knowledge section
        off_box, off_v = self.panel(
            'Offline AI & Local Knowledge',
            'Choose local Ollama models, GPU/CPU mode, and opt-in folders for offline semantic search.',
        )
        chat_row = QHBoxLayout()
        chat_lbl = QLabel('Ollama Chat Model:'); chat_lbl.setObjectName('muted')
        self.ollama_chat_combo = QComboBox(); self.ollama_chat_combo.setEditable(True)
        chat_row.addWidget(chat_lbl); chat_row.addWidget(self.ollama_chat_combo, 1)
        off_v.addLayout(chat_row)

        vis_row = QHBoxLayout()
        vis_lbl = QLabel('Ollama Vision Model:'); vis_lbl.setObjectName('muted')
        self.ollama_vision_combo = QComboBox(); self.ollama_vision_combo.setEditable(True)
        vis_row.addWidget(vis_lbl); vis_row.addWidget(self.ollama_vision_combo, 1)
        off_v.addLayout(vis_row)

        model_btns = QHBoxLayout()
        self.ollama_apply_btn = self.button('Apply Models', self._apply_offline_models)
        self.ollama_refresh_btn = self.button('Refresh Installed', self._refresh_offline_settings)
        self.ollama_gpu_btn = self.button('', self._toggle_ollama_cpu_only)
        model_btns.addWidget(self.ollama_apply_btn); model_btns.addWidget(self.ollama_refresh_btn); model_btns.addWidget(self.ollama_gpu_btn)
        off_v.addLayout(model_btns)

        kb_lbl = QLabel('Opt-In Local Knowledge Folders:'); kb_lbl.setObjectName('muted')
        off_v.addWidget(kb_lbl)
        self.kb_folders_list = QListWidget(); self.kb_folders_list.setMaximumHeight(96)
        off_v.addWidget(self.kb_folders_list)

        kb_btns = QHBoxLayout()
        self.kb_add_btn = self.button('+ Add Folder…', self._kb_add_folder)
        self.kb_remove_btn = self.button('Remove Selected', self._kb_remove_folder)
        self.kb_reindex_btn = self.button('Re-index All', self._kb_reindex_all)
        kb_btns.addWidget(self.kb_add_btn); kb_btns.addWidget(self.kb_remove_btn); kb_btns.addWidget(self.kb_reindex_btn)
        off_v.addLayout(kb_btns)

        self.offline_settings_lbl = QLabel(''); self.offline_settings_lbl.setObjectName('muted'); self.offline_settings_lbl.setWordWrap(True)
        off_v.addWidget(self.offline_settings_lbl)
        pv.addWidget(off_box)
        self._refresh_offline_settings(probe_ollama=False)

        # Security & Permissions section
        sec_box, sec_v = self.panel('Security & Permissions', 'Manage active session permissions for visual screen and desktop controls.')
        self.perm_status_lbl = QLabel(self._get_permissions_summary()); self.perm_status_lbl.setObjectName('muted'); self.perm_status_lbl.setWordWrap(True)
        sec_v.addWidget(self.perm_status_lbl)
        self.revoke_btn = self.button('🔒  Revoke All Active Permissions', self.revoke_all_permissions)
        sec_v.addWidget(self.revoke_btn)
        pv.addWidget(sec_box)

        # Software Updates section (verified one-click GitHub Releases updater)
        upd_box, upd_v = self.panel(
            'Software Updates',
            'Checks official Glass-Nova GitHub Releases, verifies SHA-256 integrity, and installs only with your approval.',
        )
        self._latest_update_url = RELEASES_PAGE_URL
        self._latest_update_result = None
        self._verified_download = None
        self._download_worker = None
        self._download_cancel_evt = None

        self.update_version_lbl = QLabel(f'Installed Version: v{APP_VERSION.lstrip("vV")}')
        self.update_version_lbl.setStyleSheet('font-weight: 600; color: #a5f1ff;')
        upd_v.addWidget(self.update_version_lbl)

        self.update_status_lbl = QLabel('Click "Check for Updates" to check GitHub Releases.')
        self.update_status_lbl.setObjectName('muted')
        self.update_status_lbl.setWordWrap(True)
        upd_v.addWidget(self.update_status_lbl)

        self.update_progress_bar = QProgressBar()
        self.update_progress_bar.setRange(0, 100)
        self.update_progress_bar.setValue(0)
        self.update_progress_bar.setTextVisible(True)
        self.update_progress_bar.setStyleSheet(
            'QProgressBar{background:rgba(4,18,34,210);border:1px solid rgba(81,190,238,145);border-radius:8px;height:18px;text-align:center;color:#eaf8ff;font-size:8.5pt}'
            'QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #38e8ff,stop:1 #7c6bff);border-radius:7px}'
        )
        self.update_progress_bar.setVisible(False)
        upd_v.addWidget(self.update_progress_bar)

        upd_btns1 = QHBoxLayout(); upd_btns1.setSpacing(8)
        self.update_check_btn = self.button('↻  Check for Updates', self._ui_check_for_updates)
        self.update_download_btn = self.button('⬇  Download Update', self._ui_download_update)
        self.update_download_btn.setEnabled(False)
        self.update_cancel_btn = self.button('✕  Cancel', self._ui_cancel_download)
        self.update_cancel_btn.setVisible(False)
        upd_btns1.addWidget(self.update_check_btn)
        upd_btns1.addWidget(self.update_download_btn)
        upd_btns1.addWidget(self.update_cancel_btn)
        upd_v.addLayout(upd_btns1)

        upd_btns2 = QHBoxLayout(); upd_btns2.setSpacing(8)
        self.update_install_btn = self.button('⚡  Install Update', self._ui_install_downloaded_update, 'primary')
        self.update_install_btn.setEnabled(False)
        self.update_open_btn = self.button('↗  Open Releases Page', self._ui_open_release_page)
        upd_btns2.addWidget(self.update_install_btn)
        upd_btns2.addWidget(self.update_open_btn)
        upd_v.addLayout(upd_btns2)

        self.update_notes_box = self.textbox('Release notes will appear here after checking for updates.')
        self.update_notes_box.setMinimumHeight(76)
        self.update_notes_box.setMaximumHeight(130)
        upd_v.addWidget(self.update_notes_box)
        pv.addWidget(upd_box)

        info = QLabel(f'Version: v{APP_VERSION.lstrip("vV")}\nAPI key: %APPDATA%\\PC_Complete_Assistant\\.env or local .env\nVoice engine: Gemini Live & Offline Local Mode'); info.setObjectName('muted'); pv.addWidget(info)
        self.log = self.textbox('Backend logs…')
        self.log.setMinimumHeight(110)
        pv.addWidget(self.log, 1)
        v.addWidget(p, 1)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)
        return w
    def change_page(self, i):
        self.stack.setCurrentIndex(i)
        for j, b in enumerate(self.nav_buttons): b.setChecked(i == j)
        if i == 2 and hasattr(self, 'tasks_view'):
            self._refresh_tasks_and_memory()
        self._move_pill(True)
    def _move_pill(self, animate=True):
        if not hasattr(self, 'pill_anim'): return
        r = self.nav_buttons[self.stack.currentIndex()].geometry(); a = self.pill_anim
        if r.width() <= 100: return                                                          # layout not ready yet
        if not animate:
            if a.state() != QAbstractAnimation.Running: self.pill.setGeometry(r); self.pill.show()
        elif self.pill.isVisible():
            a.stop(); a.setStartValue(self.pill.geometry()); a.setEndValue(r); a.start(); self.boost_anim.stop(); self.boost_anim.start()
        else: self.pill.setGeometry(r); self.pill.show()
    def showEvent(self, e):
        super().showEvent(e)
        for ms in (0, 150): QTimer.singleShot(ms, lambda: self._move_pill(False))
    def _place_grip(self): self.grip.move(self.width() - 22 - self.root.inset, self.height() - 22 - self.root.inset); self.grip.raise_()
    def _layout_mode(self):                      # frosted/maximized: glass fills the whole window, so the blur has no stray frame around it
        m = self.isMaximized(); flat = m or self.glass_mode == 'frost'; self.root.set_flat(flat, 0 if m else 10)
        self.lay.setContentsMargins(*((18, 12, 18, 18) if flat else (32, 26, 32, 32))); self.grip.setVisible(not m); self._place_grip()

    # -- window behaviour
    def _toggle_max(self): self.showNormal() if self.isMaximized() else self.showMaximized()
    def _in_titlebar(self, e): return e.position().y() <= self.root.inset + 46
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._in_titlebar(e) and not self.isMaximized() and self.windowHandle() and self.windowHandle().startSystemMove(): return e.accept()
        super().mousePressEvent(e)
    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self._in_titlebar(e): return self._toggle_max()
        super().mouseDoubleClickEvent(e)
    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, 'grip'): self._place_grip()
        if self.glass_mode == 'frost' and self.isVisible(): apply_backdrop(self, True)           # keep blur region matched
    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange and hasattr(self, 'grip'):
            self._layout_mode()
            if self.glass_mode == 'frost': apply_backdrop(self, True)
        super().changeEvent(e)
    def open_main_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _register_win32_global_hotkeys(self):
        """Register OS-wide Windows hotkeys via user32.RegisterHotKey on Nova's HWND (no background polling thread)."""
        if sys.platform != 'win32' or self._global_hotkeys_registered:
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            # MOD_CONTROL (0x0002) | MOD_SHIFT (0x0004) | MOD_NOREPEAT (0x4000) = 0x4006
            # 0x4E = 'N' (ID 9001: Toggle Floating Companion), 0x50 = 'P' (ID 9002: Point at Screen)
            r1 = bool(user32.RegisterHotKey(hwnd, 9001, 0x4006, 0x4E))
            r2 = bool(user32.RegisterHotKey(hwnd, 9002, 0x4006, 0x50))
            self._global_hotkeys_registered = r1 or r2
        except Exception:
            pass

    def _unregister_win32_global_hotkeys(self):
        if sys.platform != 'win32' or not getattr(self, '_global_hotkeys_registered', False):
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.UnregisterHotKey(hwnd, 9001)
            user32.UnregisterHotKey(hwnd, 9002)
        except Exception:
            pass
        self._global_hotkeys_registered = False

    def nativeEvent(self, eventType, message):
        if sys.platform == 'win32':
            try:
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312:  # WM_HOTKEY
                    hk_id = int(msg.wParam)
                    if hk_id == 9001:
                        QTimer.singleShot(0, self.toggle_orb)
                        return True, 0
                    elif hk_id == 9002:
                        QTimer.singleShot(0, self.start_screen_pointing_ui)
                        return True, 0
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def closeEvent(self, e):
        if self.minimize_to_tray and not self._force_quit:
            e.ignore()
            self.hide()
            if hasattr(self, 'tray_icon') and self.tray_icon.isVisible():
                self.tray_icon.showMessage(
                    APP_NAME,
                    'Nova is running in the background. Say "Hey Nova" or click this icon to open.',
                    QSystemTrayIcon.Information,
                    2500
                )
            return
        self.quit_app()
        e.accept()

    def quit_app(self):
        if getattr(self, '_quitting', False):
            return
        self._quitting = True
        self._force_quit = True
        try:
            self.hide()
        except Exception:
            pass
        if hasattr(self, 'lifecycle') and self.lifecycle:
            self.lifecycle.shutdown_all()
        QApplication.quit()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_main_window()

    def _handle_wake_word_qt(self):
        trailing_cmd = getattr(self.wake_listener, 'last_command', '') if self.wake_listener else ''
        if self.wake_listener:
            self.wake_listener.last_command = ''

        # Strict Text Mode lock: never auto-reactivate Nova bot, conversational mic, or voice mode
        if getattr(self, 'text_mode_active', False):
            if self.wake_listener:
                self.wake_listener.pause()
            self._update_wake_badge(paused=True)
            self.append_log('[Text Mode] Ignored wake-word trigger while Text Mode is active.')
            return

        self.append_log(f'[Wake Word] "Hey Nova" detected!{(" (" + trailing_cmd + ")") if trailing_cmd else ""}')
        if hasattr(self, 'orb') and self.orb and hasattr(self.orb, 'react'):
            self.orb.react(2.0)

        # If the user spoke a direct voice-control command while conversational listening is OFF
        # (e.g., "Nova, mute your voice", "Nova, stop speaking", "Nova, switch to text mode"),
        # execute it without secretly activating full conversational listening!
        vc_hit = match_voice_control_command(trailing_cmd) if trailing_cmd else None
        if vc_hit is not None and vc_hit['control'] not in ('start_listening', 'voice_mode'):
            if self.ensure_backend():
                self.send_cmd(cmd='voice_control', control=vc_hit['control'], from_voice=True)
            if self.wake_enabled and self.wake_listener and not (self.mic_on or self.mic_starting):
                self.wake_listener.resume()
                self._update_wake_badge(paused=False)
            return

        if hasattr(self, 'companion_bridge') and self.companion_bridge:
            self.companion_bridge.on_wake_word()
        else:
            self.open_main_window()
        if self.wake_listener:
            self.wake_listener.pause()
        self._update_wake_badge(paused=True)
        if not (self.mic_on or self.mic_starting):
            if self.ensure_backend():
                self.mic_starting = True
                self._voice_btn_text()
                QTimer.singleShot(150, lambda c=trailing_cmd: self.send_cmd(cmd='wake_greet', command=c))
        elif trailing_cmd and self.backend_running():
            self.send_cmd(cmd='text', text=trailing_cmd)

    def _handle_wake_status_qt(self, status):
        self._update_wake_badge(status=status)

    def _update_wake_badge(self, paused=False, status=None):
        st = status or (self.wake_listener.status if hasattr(self, 'wake_listener') and self.wake_listener else 'stopped')
        in_text_mode = bool(getattr(self, 'text_mode_active', False))
        wake_active_now = bool(self.wake_enabled and not paused and not in_text_mode and st == 'listening' and not (self.mic_on or self.mic_starting))
        if hasattr(self, 'voice_state'):
            self.voice_state.set_wake_word(enabled=self.wake_enabled, active=wake_active_now)
        if hasattr(self, 'wake_badge') and self.wake_badge is not None:
            if not self.wake_enabled:
                self.wake_badge.setText('👂 Wake Word: Off')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(40,50,60,90);border:1px solid rgba(120,130,140,100);border-radius:10px;font-size:9pt;font-weight:600;color:#9cb5c6')
                self.wake_badge.setToolTip('Wake-word monitoring is OFF. Use the Mic button to start listening.')
            elif in_text_mode:
                self.wake_badge.setText('👂 Wake Word: Paused (Text Mode)')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(95,70,155,105);border:1px solid #b59bff;border-radius:10px;font-size:9pt;font-weight:600;color:#efe6ff')
                self.wake_badge.setToolTip('Wake-word reactivation is paused while Text Mode is active. Switch back to Voice Mode or click Mic ON manually to re-enable.')
            elif paused or st == 'paused' or self.mic_on or self.mic_starting:
                self.wake_badge.setText('👂 Wake Word: Paused (Mic Active)')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(180,120,30,90);border:1px solid rgba(255,180,60,140);border-radius:10px;font-size:9pt;font-weight:600;color:#ffe4a0')
                self.wake_badge.setToolTip('Wake-word listener is paused while conversational microphone listening is active.')
            elif st == 'error':
                self.wake_badge.setText('👂 Wake Word: Error')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(180,40,40,90);border:1px solid rgba(255,80,80,140);border-radius:10px;font-size:9pt;font-weight:600;color:#ffa0a0')
            else:
                self.wake_badge.setText('👂 Wake Word: Active (Local)')
                self.wake_badge.setStyleSheet('padding:3px 10px;background:rgba(30,120,180,90);border:1px solid rgba(80,210,255,140);border-radius:10px;font-size:9pt;font-weight:600;color:#a5f1ff')
                self.wake_badge.setToolTip('Local offline wake-word monitoring is active even when conversational mic is OFF. Say "Nova, start listening" to activate full listening.')
        if self.backend_running():
            self.send_cmd(cmd='wake_state', enabled=self.wake_enabled, active=wake_active_now)
        self._update_wake_btn_text()
        self._sync_voice_control_ui()

    def _update_wake_btn_text(self):
        txt = 'Wake Word ("Hey Nova"): Active   (click to pause)' if self.wake_enabled else 'Wake Word ("Hey Nova"): Paused   (click to resume)'
        if hasattr(self, 'wake_btn'):
            self.wake_btn.setText(txt)
        if hasattr(self, 'tray_wake_act'):
            self.tray_wake_act.setText('Wake Word: Active' if self.wake_enabled else 'Wake Word: Paused')

    def toggle_wake_word(self):
        self.wake_enabled = not self.wake_enabled
        self.settings.setValue('wake_enabled', 'true' if self.wake_enabled else 'false')
        if self.wake_enabled:
            if self.wake_listener:
                self.wake_listener.start()
                self.wake_listener.resume()
            self.append_log('Wake word "Hey Nova" resumed.')
        else:
            if self.wake_listener:
                self.wake_listener.pause()
            self.append_log('Wake word "Hey Nova" paused.')
        self._update_wake_badge()

    def toggle_startup(self):
        now = is_run_at_startup()
        set_run_at_startup(not now)
        self._update_startup_btn_text()

    # -- Offline AI (Ollama) & Local Knowledge Settings
    def _update_ollama_gpu_btn(self):
        if hasattr(self, 'ollama_gpu_btn'):
            self.ollama_gpu_btn.setText('Mode: CPU-Only' if _UI_OLLAMA.cpu_only else 'Mode: GPU + CPU')

    def _refresh_offline_settings(self, probe_ollama=True):
        installed_chat, installed_vision = [], []
        if probe_ollama:
            try:
                for m in _UI_OLLAMA.list_models(timeout=1.2, force_refresh=True):
                    name = m.get('name', '')
                    if not name:
                        continue
                    if m.get('is_vision'):
                        installed_vision.append(name)
                    else:
                        installed_chat.append(name)
            except Exception:
                pass
        chat_choices = list(dict.fromkeys(
            ([_UI_OLLAMA.preferred_model] if _UI_OLLAMA.preferred_model else [])
            + installed_chat
            + list(RECOMMENDED_4GB_TEXT_MODELS)
        ))
        vision_choices = list(dict.fromkeys(
            ([_UI_OLLAMA.preferred_vision_model] if _UI_OLLAMA.preferred_vision_model else [])
            + installed_vision
            + list(RECOMMENDED_4GB_VISION_MODELS)
        ))
        if hasattr(self, 'ollama_chat_combo'):
            self.ollama_chat_combo.clear()
            self.ollama_chat_combo.addItem('(Auto-select best installed)', '')
            for name in chat_choices:
                self.ollama_chat_combo.addItem(name, name)
            self.ollama_chat_combo.setCurrentText(_UI_OLLAMA.preferred_model or '(Auto-select best installed)')
        if hasattr(self, 'ollama_vision_combo'):
            self.ollama_vision_combo.clear()
            self.ollama_vision_combo.addItem('(Auto-select best vision)', '')
            for name in vision_choices:
                self.ollama_vision_combo.addItem(name, name)
            self.ollama_vision_combo.setCurrentText(_UI_OLLAMA.preferred_vision_model or '(Auto-select best vision)')
        self._update_ollama_gpu_btn()
        if hasattr(self, 'kb_folders_list'):
            self.kb_folders_list.clear()
            for folder in knowledge_store.list_folders():
                self.kb_folders_list.addItem(QListWidgetItem(folder))
        if hasattr(self, 'offline_settings_lbl'):
            if probe_ollama:
                total_inst = len(installed_chat) + len(installed_vision)
                self.offline_settings_lbl.setText(
                    f'Ollama models detected: {total_inst} · Indexed folders: {len(knowledge_store.list_folders())} · Local storage: DPAPI encrypted'
                )
            else:
                self.offline_settings_lbl.setText(
                    f'Indexed folders: {len(knowledge_store.list_folders())} · Click "Refresh Installed" to query local Ollama models.'
                )

    def _apply_offline_models(self):
        chat_val = self.ollama_chat_combo.currentText().strip()
        if chat_val.startswith('(Auto-select'):
            chat_val = ''
        vis_val = self.ollama_vision_combo.currentText().strip()
        if vis_val.startswith('(Auto-select'):
            vis_val = ''
        _UI_OLLAMA.preferred_model = chat_val
        _UI_OLLAMA.preferred_vision_model = vis_val
        _UI_OLLAMA.save_config()
        if self.backend_running():
            self.send_cmd(
                cmd='offline_config',
                model=chat_val,
                vision_model=vis_val,
                cpu_only=_UI_OLLAMA.cpu_only,
            )
        if hasattr(self, 'offline_settings_lbl'):
            self.offline_settings_lbl.setText(
                f"Saved Ollama preferences: chat='{chat_val or 'auto'}', vision='{vis_val or 'auto'}'."
            )

    def _toggle_ollama_cpu_only(self):
        _UI_OLLAMA.cpu_only = not _UI_OLLAMA.cpu_only
        _UI_OLLAMA.save_config()
        self._update_ollama_gpu_btn()
        if self.backend_running():
            self.send_cmd(
                cmd='offline_config',
                model=_UI_OLLAMA.preferred_model,
                vision_model=_UI_OLLAMA.preferred_vision_model,
                cpu_only=_UI_OLLAMA.cpu_only,
            )
        if hasattr(self, 'offline_settings_lbl'):
            self.offline_settings_lbl.setText(
                'Switched Ollama to CPU-only mode (0 VRAM).' if _UI_OLLAMA.cpu_only
                else 'Switched Ollama to GPU acceleration with automatic CPU fallback.'
            )

    def _kb_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select Folder for Local Knowledge Indexing')
        if not folder:
            return
        res = knowledge_store.add_folder(folder)
        if res.get('ok'):
            idx_res = knowledge_store.index_folder(folder)
            msg = idx_res.get('message') or res.get('message', 'Folder added.')
        else:
            msg = res.get('message', 'Could not add folder.')
        self._refresh_offline_settings(probe_ollama=False)
        if hasattr(self, 'offline_settings_lbl'):
            self.offline_settings_lbl.setText(msg)

    def _kb_remove_folder(self):
        item = self.kb_folders_list.currentItem() if hasattr(self, 'kb_folders_list') else None
        if not item:
            if hasattr(self, 'offline_settings_lbl'):
                self.offline_settings_lbl.setText('Select a folder from the list first to remove it.')
            return
        res = knowledge_store.remove_folder(item.text())
        self._refresh_offline_settings(probe_ollama=False)
        if hasattr(self, 'offline_settings_lbl'):
            self.offline_settings_lbl.setText(res.get('message', 'Folder removed.'))

    def _kb_reindex_all(self):
        folders = knowledge_store.list_folders()
        if not folders:
            if hasattr(self, 'offline_settings_lbl'):
                self.offline_settings_lbl.setText('No opt-in folders configured yet. Click "+ Add Folder…" first.')
            return
        indexed = 0
        for f in folders:
            r = knowledge_store.index_folder(f)
            if r.get('ok'):
                indexed += 1
        if hasattr(self, 'offline_settings_lbl'):
            self.offline_settings_lbl.setText(f'Re-indexed {indexed}/{len(folders)} local knowledge folder(s).')

    def _update_startup_btn_text(self):
        active = is_run_at_startup()
        txt = 'Start with Windows: Enabled   (click to disable)' if active else 'Start with Windows: Disabled   (click to enable)'
        if hasattr(self, 'startup_btn'):
            self.startup_btn.setText(txt)
        if hasattr(self, 'tray_startup_act'):
            self.tray_startup_act.setChecked(active)

    def toggle_minimize_to_tray(self):
        self.minimize_to_tray = not self.minimize_to_tray
        self.settings.setValue('close_to_tray', 'true' if self.minimize_to_tray else 'false')
        self._update_tray_btn_text()

    def _update_tray_btn_text(self):
        txt = (
            'Close Button (×): Minimize to Tray   (click to make × exit Nova)'
            if self.minimize_to_tray
            else 'Close Button (×): Exit Nova Completely   (click to minimize to tray instead)'
        )
        if hasattr(self, 'tray_toggle_btn'):
            self.tray_toggle_btn.setText(txt)
        if hasattr(self, 'close_btn') and self.close_btn is not None:
            self.close_btn.setToolTip(
                'Minimize Glass Nova to System Tray (use Tray → Exit Glass Nova to quit)'
                if self.minimize_to_tray
                else 'Close & Exit Glass Nova'
            )

    def _prompt_permission(self, req_id: str, cat: str, details: str) -> str:
        self.open_main_window()
        was_wake_listening = bool(
            hasattr(self, 'wake_listener') and self.wake_listener and self.wake_listener.status == 'listening'
        )
        if was_wake_listening:
            self.wake_listener.pause()
        dlg = PermissionDialog(cat, details, self)
        self._active_perm_dialogs[req_id] = dlg
        try:
            dlg.exec()
        finally:
            self._active_perm_dialogs.pop(req_id, None)
            if was_wake_listening and self.wake_enabled and not self.text_mode_active and not (self.mic_on or self.mic_starting):
                self.wake_listener.resume()
        decision = dlg.selected_decision
        if decision in ('session', 'allow_session'):
            PermissionManager().grant_session(cat)
        self.append_log(f'[Permission] {cat} -> {decision}')
        self._refresh_perms_ui()
        return decision

    def revoke_all_permissions(self):
        self.send_cmd(cmd='perm_revoke', category='all')
        PermissionManager().revoke_all()
        self.append_log('[Permission] Revoked all active session permissions.')
        self._refresh_perms_ui()

    def _get_permissions_summary(self) -> str:
        grants = PermissionManager().get_status()
        active_grants = [k for k, v in grants.items() if v]
        if not active_grants:
            return 'No session permissions currently granted. Nova will prompt when needed.'
        lines = ['Active Session Permissions:']
        for k in active_grants:
            info = CATEGORIES.get(k, {})
            title = info.get('title', k)
            lines.append(f'  • {title} ({k})')
        return '\n'.join(lines)

    def _refresh_perms_ui(self):
        if hasattr(self, 'perm_status_lbl'):
            self.perm_status_lbl.setText(self._get_permissions_summary())

    # -- glass + orb settings
    def apply_backdrop(self):
        msg = apply_backdrop(self, self.glass_mode == 'frost')
        if msg: self.append_log(msg)
    def _glass_text(self): self.glass_btn.setText('Glass: Clear — 100% see-through   (click for Frosted blur)' if self.glass_mode == 'clear' else 'Glass: Frosted blur   (click for Clear)')
    def cycle_glass(self):
        self.glass_mode = 'frost' if self.glass_mode == 'clear' else 'clear'; self.settings.setValue('glass_mode', self.glass_mode); self._glass_text(); self._layout_mode(); self.apply_backdrop()
    def set_tint(self, v): self.tint = cl(int(v), 145, 252); self.root.set_tint(self.tint); self.settings.setValue('tint', self.tint)
    def set_orb_state(self, s):
        self.assistant_state = s
        self.hero_orb.set_state(s)
        self.orb.set_assistant_state(s)
        if hasattr(self, 'state_badge') and self.state_badge is not None:
            labels = {
                'idle': ('● Idle', 'rgba(25,95,145,110)', 'rgba(90,210,255,130)', '#b8f4ff'),
                'listening': ('🎙 Listening', 'rgba(20,145,195,140)', '#45e6ff', '#ffffff'),
                'thinking': ('✦ Thinking', 'rgba(85,95,215,145)', '#a8b8ff', '#ffffff'),
                'speaking': ('🔊 Speaking', 'rgba(25,175,175,145)', '#65fff0', '#ffffff'),
                'waiting_approval': ('⏳ Waiting Approval', 'rgba(195,130,20,165)', '#ffd060', '#fff8e0'),
                'executing': ('⚙ Executing', 'rgba(30,170,105,165)', '#60ffaa', '#ffffff'),
                'paused': ('⏸ Paused', 'rgba(70,85,105,135)', 'rgba(150,175,200,130)', '#d0e2f2'),
                'error': ('⚠ Error', 'rgba(185,40,50,165)', '#ff8088', '#ffe6e8'),
            }
            txt, bg, border, col = labels.get(s, labels['idle'])
            self.state_badge.setText(txt)
            self.state_badge.setStyleSheet(f'padding:3px 10px;background:{bg};border:1px solid {border};border-radius:10px;font-size:9pt;font-weight:700;color:{col}')

    def _show_companion_only(self):
        if hasattr(self, 'orb') and self.orb:
            self.orb.show_bot()
            if hasattr(self, 'orb_check'):
                self.orb_check.setText('Disable floating bot')

    def _hide_companion_only(self):
        if hasattr(self, 'orb') and self.orb:
            self.orb.hide()
            if hasattr(self, 'orb_check'):
                self.orb_check.setText('Enable floating bot')
    def _make_bot(self, kind):                   # bots are created once (Ghost only when first chosen) and reused
        if kind in self.bots: return self.bots[kind]
        try:
            if kind == 'ghost':
                from ghost import GhostWidget
                b = GhostWidget(self.settings)
            elif kind == 'pet':
                from pet_engine import PetWidget
                b = PetWidget(self.settings)
            elif kind == 'cloudy':
                from cloudy import CloudyWidget
                b = CloudyWidget(self.settings)
            else: b = DesktopBot(self.settings)
        except Exception as e: self._bot_error = f'{type(e).__name__}: {e}'; return None
        if hasattr(b, 'open_requested'): b.open_requested.connect(self.open_main_window)
        if hasattr(b, 'exit_requested'): b.exit_requested.connect(self.quit_app)
        if hasattr(b, 'hidden'): b.hidden.connect(lambda: self.orb_check.setText('Enable floating bot'))
        self.bots[kind] = b; return b
    def _bot_text(self):
        self.bot_btn.setText(f"Companion: {BOT_NAMES[self.bot_kind]}   (click to switch)")
    def choose_bot(self):
        m = QMenu(self); m.setStyleSheet(MENU_CSS)
        for kind, name in BOT_NAMES.items(): m.addAction(('●   ' if kind == self.bot_kind else '      ') + name).setData(kind)
        a = m.exec(self.bot_btn.mapToGlobal(QPoint(0, self.bot_btn.height())))
        if a is not None: self.set_bot(a.data())
    def set_bot(self, kind):
        if kind not in BOT_NAMES or kind == self.bot_kind: return
        old = self.orb
        centre = old.pos() + QPoint(old.width() // 2, old.height() // 2)
        new = self._make_bot(kind)
        if new is None:
            self.append_log(f'Could not load the {kind} bot: {self._bot_error}')
            return
        old.hide()
        self.orb, self.bot_kind = new, kind
        self.settings.setValue('bot_kind', kind)
        new.set_assistant_state(self.assistant_state)
        new.show_bot()
        if centre: new.move(centre - QPoint(new.width() // 2, new.height() // 2))
        if hasattr(new, 'ensure_on_screen'): new.ensure_on_screen()
        self.orb_check.setText('Disable floating bot')
        self._bot_text()

    def toggle_orb(self):
        on = not self.orb.isVisible()
        if hasattr(self, 'companion_bridge') and self.companion_bridge:
            self.companion_bridge.show() if on else self.companion_bridge.hide()
        else:
            self.orb.show_bot() if on else self.orb.hide()
        self.orb_check.setText('Disable floating bot' if on else 'Enable floating bot')

    # -- assistant backend: pc_assistant.py runs ONE Gemini Live session; voice and typed chat both talk to it
    MARK = '@@NOVA@@'
    def backend_running(self): return self.process.state() != QProcess.NotRunning
    def ensure_backend(self):
        """Start the single backend process if it is not running (supports both dev Python and packaged GlassNova.exe)."""
        if self.backend_running(): return True
        self._out_raw = self._err_raw = b''; self._stopping = self._fatal_shown = False; self.backend_state = 'connecting'
        self.process.setProgram(sys.executable)
        if getattr(sys, 'frozen', False):
            self.process.setArguments(['--backend'])
        else:
            self.process.setArguments(['-u', str(ROOT / 'pc_assistant.py')])
        self.process.start()
        self._refresh_status(); self.set_orb_state('thinking'); self.append_log('Starting assistant…')
        return True
    def send_cmd(self, **cmd):
        data = (json.dumps(cmd, ensure_ascii=False) + '\n').encode('utf-8')
        if self.process.state() == QProcess.Running: self.process.write(data)
        elif self.process.state() == QProcess.Starting: self._pending.append(data)        # sent as soon as the process is up
    def _backend_started(self):
        self.process.write((json.dumps({'cmd': 'voice_output', 'on': self.voice_output}) + '\n').encode('utf-8'))
        if self.text_mode_active:
            self.process.write((json.dumps({'cmd': 'text_mode_sync', 'active': True}) + '\n').encode('utf-8'))
        self.process.write((json.dumps({
            'cmd': 'mic_sensitivity',
            'sensitivity': self.mic_sensitivity,
            'auto_nearest': self.mic_auto_nearest,
            'calibrate_now': False,
        }) + '\n').encode('utf-8'))
        for data in self._pending: self.process.write(data)
        self._pending.clear()
    def shutdown_backend(self):
        """Ask the backend to close its session, mic and speaker; force-stop it only if it does not exit."""
        if self.process.state() == QProcess.NotRunning: return
        self._stopping = True
        if self.process.state() == QProcess.Starting and not self.process.waitForStarted(2000): self.process.kill(); self.process.waitForFinished(1500); return
        self.process.write(b'{"cmd": "shutdown"}\n'); self.process.closeWriteChannel()
        if not self.process.waitForFinished(3500): self.process.kill(); self.process.waitForFinished(1500)

    def toggle_voice(self):                       # Start/Stop VOICE only: the Live session and typed chat stay alive
        if self.mic_on or self.mic_starting:
            self.mic_starting = False
            if self.backend_running():
                self.send_cmd(cmd='mic_stop')
            else:
                self.mic_on = False
                if self.wake_enabled and not self.text_mode_active and self.wake_listener:
                    self.wake_listener.resume()
                    self._update_wake_badge(paused=False)
            self._voice_btn_text()
            return
        # Manual Mic ON explicitly exits Text Mode
        if self.text_mode_active:
            self.text_mode_active = False
            self.settings.setValue('text_mode_active', 'false')
        if not self.ensure_backend(): return
        if self.wake_listener:
            self.wake_listener.pause()
            self._update_wake_badge(paused=True)
        self.mic_starting = True; self._voice_btn_text(); self.send_cmd(cmd='mic_start')
    def _voice_btn_text(self):
        txt = '■  Stop voice assistant' if (self.mic_on or self.mic_starting) else '▶  Start voice assistant'
        self.voice_btn.setText(txt)
        if hasattr(self, 'overview_voice_btn'): self.overview_voice_btn.setText(txt)
        self._sync_voice_control_ui()
    def _auto_start_backend(self):
        self.ensure_backend()
    def _refresh_status(self):
        if not self.backend_running(): s, b = '●  Assistant stopped', 'Ready when you are'
        elif self.backend_state == 'offline':
            s = '●  Offline Mode (voice on)' if self.mic_on else '●  Offline Mode (local commands)'
            b = 'Offline Local Mode — local voice & chat commands ready'
        elif self.backend_state == 'reconnecting': s, b = '●  Reconnecting…', 'Reconnecting to Gemini Live (offline commands active)…'
        elif self.backend_state != 'ready': s, b = '●  Connecting…', 'Connecting to Gemini Live…'
        elif self.mic_on: s, b = '●  Assistant running', 'Listening and ready'
        else: s, b = '●  Connected (voice off)', 'Connected — type a message or start voice'
        self.status_dot.setText(s); self.big_status.setText(b)
    def _read_stdout(self):
        self._out_raw += bytes(self.process.readAllStandardOutput().data())
        while b'\n' in self._out_raw:
            line, self._out_raw = self._out_raw.split(b'\n', 1); self._handle_line(line.decode('utf-8', 'replace').rstrip('\r'))
    def _read_stderr(self):
        self._err_raw += bytes(self.process.readAllStandardError().data())
        while b'\n' in self._err_raw:
            line, self._err_raw = self._err_raw.split(b'\n', 1); line = line.decode('utf-8', 'replace').rstrip('\r')
            if line.strip(): self._err_tail = (self._err_tail + [line])[-6:]; self.append_log(line)
    def _handle_line(self, line):
        if not line.strip(): return
        if line.startswith(self.MARK):
            try: ev = json.loads(line[len(self.MARK):])
            except ValueError: return self.append_log(line)
            if isinstance(ev, dict): return self._on_event(ev)
        self.append_log(line)
    def _on_event(self, ev):
        t, text = ev.get('t'), str(ev.get('text', ''))
        if t == 'user': self._chat_stream('voice', text)
        elif t == 'model': self._awaiting_reply = False; self._chat_stream('model', text)
        elif t == 'turn_end': self._awaiting_reply = False; self._chat_close('voice'); self._chat_close('model'); self._chat_dirty()
        elif t == 'state': self.set_orb_state(str(ev.get('v', 'idle')))
        elif t == 'status':
            self.backend_state = str(ev.get('v', '')); self._refresh_status()
            if self.backend_state == 'ready': self.append_log('Connected to Gemini Live.')
        elif t == 'mic':
            v = ev.get('v'); self.mic_on = v == 'on'; self.mic_starting = v == 'starting'
            if hasattr(self, 'voice_state'):
                self.voice_state.set_mic_listening(self.mic_on or self.mic_starting)
            if v == 'error': self._chat_error(text or 'The microphone could not be started.')
            self._voice_btn_text(); self._refresh_status(); self._sync_voice_control_ui()
            if not self.mic_on and not self.mic_starting:
                if self.text_mode_active:
                    if self.wake_listener:
                        self.wake_listener.pause()
                    self._update_wake_badge(paused=True)
                elif self.wake_enabled and self.wake_listener:
                    self.wake_listener.resume()
                    self._update_wake_badge(paused=False)
        elif t == 'voice_state':
            if 'text_mode_locked' in ev:
                self.text_mode_active = bool(ev['text_mode_locked'])
                self.settings.setValue('text_mode_active', 'true' if self.text_mode_active else 'false')
                if self.text_mode_active:
                    self.voice_state.enter_text_mode()
                    if self.wake_listener:
                        self.wake_listener.pause()
                    self._update_wake_badge(paused=True)
            if 'spoken_replies' in ev:
                self.voice_output = bool(ev['spoken_replies'])
                self.settings.setValue('voice_output', 'true' if self.voice_output else 'false')
                self.voice_state.set_spoken_replies(self.voice_output)
            if 'mic_listening' in ev:
                self.voice_state.set_mic_listening(bool(ev['mic_listening']))
            if 'ai_mode' in ev or 'ai_provider' in ev:
                self.ai_mode = str(ev.get('ai_mode', self.ai_mode))
                self.ai_provider = str(ev.get('ai_provider', self.ai_provider))
                self.voice_state.set_ai_backend(self.ai_mode, self.ai_provider)
            self._voice_out_text()
            self._sync_voice_control_ui()
        elif t == 'mic_level':
            rms = float(ev.get('rms', 0.0) or 0.0)
            gate = float(ev.get('gate', 120.0) or 120.0)
            act = bool(ev.get('active', False))
            sens = ev.get('sensitivity')
            auto_n = ev.get('auto_nearest')
            if sens is not None and auto_n is not None and (int(sens) != self.mic_sensitivity or bool(auto_n) != self.mic_auto_nearest):
                self.mic_sensitivity = max(0, min(100, int(sens)))
                self.mic_auto_nearest = bool(auto_n)
                self.settings.setValue('mic_sensitivity', self.mic_sensitivity)
                self.settings.setValue('mic_auto_nearest', 'true' if self.mic_auto_nearest else 'false')
                for w in self._mic_sens_widgets:
                    w.sync_state(self.mic_sensitivity, self.mic_auto_nearest)
            for w in self._mic_sens_widgets:
                w.push_audio_level(rms, gate, act)
        elif t == 'mic_sensitivity':
            sens = int(ev.get('sensitivity', self.mic_sensitivity))
            auto_n = bool(ev.get('auto_nearest', self.mic_auto_nearest))
            self.mic_sensitivity = max(0, min(100, sens))
            self.mic_auto_nearest = auto_n
            self.settings.setValue('mic_sensitivity', self.mic_sensitivity)
            self.settings.setValue('mic_auto_nearest', 'true' if self.mic_auto_nearest else 'false')
            for w in self._mic_sens_widgets:
                w.sync_state(self.mic_sensitivity, self.mic_auto_nearest)
        elif t == 'indicator':
            kind = ev.get('kind')
            active = bool(ev.get('active', False))
            if kind == 'screen': self.screen_badge.setVisible(active)
            elif kind == 'control': self.control_badge.setVisible(active)
            elif kind == 'gesture': self._update_gesture_ui(active=active)
        elif t == 'gesture':
            st = str(ev.get('status', 'stopped'))
            self._update_gesture_ui(status=st)
        elif t == 'ui_cmd':
            act = ev.get('action')
            if act in ('close', 'exit', 'quit'):
                QTimer.singleShot(100, self.quit_app)
            elif act == 'minimize':
                self.showMinimized()
            elif act == 'toggle_floating_bot':
                self.toggle_orb()
            elif act == 'show_floating_bot':
                if hasattr(self, 'orb') and self.orb and not self.orb.isVisible():
                    self.toggle_orb()
            elif act == 'hide_floating_bot':
                if hasattr(self, 'orb') and self.orb and self.orb.isVisible():
                    self.toggle_orb()
            elif act == 'switch_bot':
                target_bot = str(ev.get('bot', '')).strip()
                if target_bot in BOT_NAMES:
                    self.set_bot(target_bot)
        elif t == 'perm_sync':
            active = ev.get('active', {})
            for k, v in active.items():
                if v:
                    PermissionManager().grant_session(k)
                else:
                    PermissionManager().revoke(k)
            self._refresh_perms_ui()
        elif t == 'perm_notification':
            cat = ev.get('category', '')
            desc = ev.get('desc', '')
            self.append_log(f"[Voice Permission Granted] {cat}: {desc}")
            self._refresh_perms_ui()
        elif t == 'perm_voice_resolved':
            req_id = ev.get('id')
            decision = ev.get('decision', 'allow_session')
            cat = ev.get('category', '')
            if decision in ('session', 'allow_session') and cat:
                PermissionManager().grant_session(cat)
            dlg = self._active_perm_dialogs.get(req_id)
            if dlg is not None:
                dlg._choose(decision)
            self._refresh_perms_ui()
        elif t == 'perm_request':
            req_id = ev.get('id')
            cat = ev.get('category', '')
            details = ev.get('desc') or ev.get('details') or ''
            if isinstance(details, dict):
                details = ', '.join(f"{k}: {v}" for k, v in details.items())
            def _run_perm_dialog(r=req_id, c=cat, d=str(details)):
                decision = self._prompt_permission(r, c, d)
                self.send_cmd(cmd='perm_response', id=r, decision=decision)
            QTimer.singleShot(0, _run_perm_dialog)
        elif t == 'tool':
            self._chat_close('voice')
            t_name = ev.get('name')
            self.append_log(f"[tool] {t_name}: {ev.get('result')}")
            if t_name in ('task_control', 'memory_control') and hasattr(self, 'tasks_view'):
                self._refresh_tasks_and_memory()
            if t_name == 'workflow_control':
                self._ui_refresh_workflows()
        elif t == 'point_at_screen':
            q = str(ev.get('question', '') or 'What is this region on my screen and how do I use it?')
            QTimer.singleShot(0, lambda q=q: self.start_screen_pointing_ui(question=q))
        elif t == 'walkthrough_state':
            self._on_walkthrough_state_event(ev)
        elif t == 'workflow_state':
            self._on_workflow_state_event(ev)
        elif t == 'workflow_step_request':
            QTimer.singleShot(0, lambda e=dict(ev): self._on_workflow_step_request(e))
        elif t == 'workflow_replay_status':
            st = ev.get('status')
            wf_name = ev.get('name', 'Workflow')
            if st == 'started':
                self.append_log(f"[Workflow Replay] Started '{wf_name}' ({ev.get('total', 0)} steps).")
            elif st == 'finished':
                outcome = ev.get('outcome', {})
                self.append_log(f"[Workflow Replay] Finished '{wf_name}': {outcome}")
                self._ui_refresh_workflows()
        elif t == 'log': self.append_log(text)
        elif t == 'error': self.append_log(text); self._chat_error(text)
        elif t == 'fatal': self.append_log(text); self._fatal_shown = True; self._chat_error(text)
    def append_log(self, text): self.activity.append(text.rstrip()); self.log.append(text.rstrip())
    def _backend_error(self, err):
        if err == QProcess.FailedToStart:
            self._chat_error('The assistant process could not be started. Check that Python and the packages from requirements.txt are installed.')
            self.backend_finished(1)
    def backend_finished(self, code=0, *_):
        self._read_stdout(); self._read_stderr()
        if self._out_raw.strip(): self._handle_line(self._out_raw.decode('utf-8', 'replace').strip()); self._out_raw = b''
        crashed = not self._stopping and not self._fatal_shown and code not in (0, None)
        self.backend_state, self.mic_on, self.mic_starting, self._awaiting_reply = 'stopped', False, False, False; self._pending.clear()
        self._voice_btn_text(); self._refresh_status(); self.set_orb_state('idle'); self.append_log('Assistant stopped.')
        if crashed: self._chat_error(f'The assistant stopped unexpectedly (exit code {code}). Your next message will start it again. See Settings → logs for details.')
        else: self._chat_dirty()

    # -- shared transcript (voice transcripts, typed messages and replies all live here, so nothing is shown twice)
    def _chat_stream(self, role, text):
        self._chat_close('model' if role == 'voice' else 'voice')
        last = self.chat_entries[-1] if self.chat_entries else None
        if last and last['role'] == role and last['open']: last['text'] += text
        else: self.chat_entries.append({'role': role, 'text': text, 'open': True}); del self.chat_entries[:-300]
        self._chat_dirty()
    def _chat_close(self, role):
        if self.chat_entries and self.chat_entries[-1]['role'] == role: self.chat_entries[-1]['open'] = False
    def _chat_error(self, text):
        self._awaiting_reply = False; self._chat_close('model'); self._chat_close('voice')
        self.chat_entries.append({'role': 'error', 'text': text, 'open': False}); del self.chat_entries[:-300]; self._chat_dirty()
    def _chat_dirty(self):
        if not self._chat_timer.isActive(): self._chat_timer.start(40)
    def _render_chat(self):
        out = []
        for e in self.chat_entries:
            txt = html.escape(e['text'].strip()).replace('\n', '<br>')
            if not txt: continue
            r = e['role']
            if r == 'error': out.append(f'<span style="color:#ff9aa8">{txt}</span>')
            else: out.append({'you': '<b>You:</b> ', 'voice': '<b>You (voice):</b> ', 'model': f'<b>{APP_NAME}:</b> '}.get(r, '') + txt)
        if self._awaiting_reply: out.append(f'<i>{APP_NAME} is thinking…</i>')
        self.chatlog.setHtml(''.join(f'<p style="margin:0 0 8px 0">{o}</p>' for o in out))
        bar = self.chatlog.verticalScrollBar(); bar.setValue(bar.maximum())

    # -- typed chat: sent into the SAME live session the microphone uses
    def send_chat(self):
        msg = self.chat_input.text().strip()
        if not msg or not self.ensure_backend(): return
        self.chat_input.clear(); self._chat_close('voice'); self._chat_close('model')
        self.chat_entries.append({'role': 'you', 'text': msg, 'open': False}); del self.chat_entries[:-300]
        self._awaiting_reply = True; self._chat_dirty(); self.set_orb_state('thinking')
        self.send_cmd(cmd='text', text=msg); self.chat_input.setFocus()
    def _voice_out_text(self):
        txt = 'Spoken replies: On   (click to turn off)' if self.voice_output else 'Spoken replies: Off   (click to turn on)'
        if hasattr(self, 'voice_out_btn'): self.voice_out_btn.setText(txt)
        if hasattr(self, 'tray_voice_out_act'): self.tray_voice_out_act.setText('Spoken Replies: On' if self.voice_output else 'Spoken Replies: Off')
        self._sync_voice_control_ui()

    def _sync_voice_control_ui(self):
        """Synchronize Chat quick-access controls and top AI mode/provider badges with current independent states."""
        mic_active = bool(self.mic_on or self.mic_starting)
        spoken_on = bool(self.voice_output)
        wake_on = bool(self.wake_enabled)
        mode_lbl = (getattr(self, 'ai_mode', 'hybrid') or 'hybrid').capitalize()
        prov_lbl = getattr(self, 'ai_provider', 'Local') or 'Local'

        if hasattr(self, 'mode_badge') and self.mode_badge is not None:
            self.mode_badge.setText(f'⚡ {mode_lbl} • {prov_lbl}')
            if mode_lbl == 'Online':
                self.mode_badge.setStyleSheet('padding:3px 10px;background:rgba(20,135,105,120);border:1px solid #55f0b8;border-radius:10px;font-size:9pt;font-weight:600;color:#e6fff6')
            elif mode_lbl == 'Offline':
                self.mode_badge.setStyleSheet('padding:3px 10px;background:rgba(95,70,155,120);border:1px solid #b59bff;border-radius:10px;font-size:9pt;font-weight:600;color:#f0e8ff')
            else:
                self.mode_badge.setStyleSheet('padding:3px 10px;background:rgba(45,75,145,110);border:1px solid rgba(110,180,255,135);border-radius:10px;font-size:9pt;font-weight:600;color:#cde8ff')

        if hasattr(self, 'chat_mic_btn') and self.chat_mic_btn is not None:
            self.chat_mic_btn.setText('🎙  Mic: ON' if mic_active else '🎙  Mic: OFF')
            self.chat_mic_btn.setObjectName('primary' if mic_active else '')
            self.chat_mic_btn.style().unpolish(self.chat_mic_btn); self.chat_mic_btn.style().polish(self.chat_mic_btn)
            if mic_active:
                self.chat_mic_btn.setToolTip('Conversational microphone listening is ON. Click to turn OFF (keeps chat & AI session open).')
            elif wake_on:
                self.chat_mic_btn.setToolTip('Conversational microphone listening is OFF. Local "Hey Nova" wake-word monitoring is still active. Click to start full listening.')
            else:
                self.chat_mic_btn.setToolTip('All microphone monitoring is OFF. Click to start conversational listening.')

        if hasattr(self, 'chat_voice_btn') and self.chat_voice_btn is not None:
            self.chat_voice_btn.setText('🔊  Spoken Replies: ON' if spoken_on else '🔇  Spoken Replies: OFF')
            self.chat_voice_btn.setObjectName('primary' if spoken_on else '')
            self.chat_voice_btn.style().unpolish(self.chat_voice_btn); self.chat_voice_btn.style().polish(self.chat_voice_btn)

        if hasattr(self, 'chat_mode_btn') and self.chat_mode_btn is not None:
            if mic_active or spoken_on:
                self.chat_mode_btn.setText('💬  Text Mode')
                self.chat_mode_btn.setToolTip('Switch to Text-Only Mode (Mic OFF + Spoken Replies OFF)')
            else:
                self.chat_mode_btn.setText('🎙  Voice Mode')
                self.chat_mode_btn.setToolTip('Switch to Voice Mode (Mic ON + Spoken Replies ON)')

        if hasattr(self, 'chat_status_bar') and self.chat_status_bar is not None:
            in_tm = bool(getattr(self, 'text_mode_active', False))
            if in_tm:
                wake_txt = 'Paused (Text Mode)'
            elif wake_on and mic_active:
                wake_txt = 'Paused (Mic ON)'
            elif wake_on:
                wake_txt = 'ON (Local)'
            else:
                wake_txt = 'OFF'
            self.chat_status_bar.setText(
                f"Mic Listening: {'ON' if mic_active else 'OFF'}   •   "
                f"Spoken Replies: {'ON' if spoken_on else 'OFF'}   •   "
                f"Wake Word: {wake_txt}   •   "
                f"AI: {mode_lbl} ({prov_lbl})"
            )

    def stop_speaking_ui(self):
        """Immediately stop Nova from speaking right now without disabling future spoken replies."""
        if self.backend_running():
            self.send_cmd(cmd='stop_speaking')
        if self.assistant_state == 'speaking':
            self.set_orb_state('listening' if self.mic_on else 'idle')
        self.append_log('[Voice] Stopped current speech output.')

    def toggle_text_voice_mode(self):
        """One-click toggle between Text-Only Mode (Mic OFF, Spoken OFF, Wake-Word paused) and Voice Mode (Mic ON, Spoken ON)."""
        if self.mic_on or self.mic_starting or self.voice_output or not self.text_mode_active:
            # Enter locked Text-Only Mode: stays in Text Mode indefinitely until user manually switches back
            self.text_mode_active = True
            self.settings.setValue('text_mode_active', 'true')
            self.voice_output = False
            self.settings.setValue('voice_output', 'false')
            self.voice_state.enter_text_mode()
            self.mic_starting = False
            if self.wake_listener:
                self.wake_listener.pause()
            if self.backend_running():
                self.send_cmd(cmd='voice_control', control='text_mode')
            else:
                self.mic_on = False
            self._update_wake_badge(paused=True)
            self._voice_out_text()
            self._voice_btn_text()
        else:
            # Manually exit Text Mode and switch to Voice Mode
            self.text_mode_active = False
            self.settings.setValue('text_mode_active', 'false')
            self.voice_output = True
            self.settings.setValue('voice_output', 'true')
            self.voice_state.exit_text_mode(mic_on=True, spoken_on=True)
            if self.ensure_backend():
                if self.wake_listener:
                    self.wake_listener.pause()
                    self._update_wake_badge(paused=True)
                self.mic_starting = True
                self._voice_btn_text()
                self.send_cmd(cmd='voice_control', control='voice_mode')
            self._voice_out_text()

    def toggle_voice_output(self):
        self.voice_output = not self.voice_output; self.settings.setValue('voice_output', 'true' if self.voice_output else 'false')
        if self.voice_output and self.text_mode_active:
            self.text_mode_active = False
            self.settings.setValue('text_mode_active', 'false')
            if self.wake_enabled and self.wake_listener and not (self.mic_on or self.mic_starting):
                self.wake_listener.start()
                self.wake_listener.resume()
                self._update_wake_badge(paused=False)
        self.voice_state.set_spoken_replies(self.voice_output)
        self._voice_out_text()
        if self.backend_running(): self.send_cmd(cmd='voice_output', on=self.voice_output)

    def toggle_hand_gesture(self):
        if self.ensure_backend():
            self.send_cmd(cmd='gesture_control', action='toggle')

    def _update_gesture_ui(self, status=None, active=None):
        if status is not None:
            self.gesture_active = status in ('starting', 'running', 'paused')
            paused = (status == 'paused')
        elif active is not None:
            self.gesture_active = bool(active)
            paused = False
        else:
            paused = False

        if hasattr(self, 'gesture_badge') and self.gesture_badge is not None:
            if paused:
                self.gesture_badge.setText('🖐 Gesture Paused')
            else:
                self.gesture_badge.setText('🖐 Gesture Active')
            self.gesture_badge.setVisible(self.gesture_active)
        if hasattr(self, 'sidebar_gesture_btn'):
            self.sidebar_gesture_btn.setText('🖐  Hand Gesture: Active' if self.gesture_active else '🖐  Hand Gesture: Off')
        if hasattr(self, 'gesture_btn'):
            self.gesture_btn.setText(
                'Hand Gesture Mouse Control: Active   (click to stop)'
                if self.gesture_active
                else 'Hand Gesture Mouse Control: Off   (click to start)'
            )
        if hasattr(self, 'tray_gesture_act'):
            self.tray_gesture_act.setText('Hand Gesture Mouse: Active' if self.gesture_active else 'Hand Gesture Mouse: Off')

    # -- Feature 2: Screen Pointing (user-selected region only, permission-gated, guidance only)
    def _request_local_screen_permission(self, description: str) -> bool:
        pm = PermissionManager()
        if pm.is_granted('screen_view'):
            return True
        decision = self._prompt_permission('point_screen_req', 'screen_view', description)
        return decision in ('session', 'allow_session', 'once', 'allow_once')

    def _capture_selected_region_jpeg(self, rect):
        from screen_agent import capture_region_jpeg
        self.screen_badge.setVisible(True)
        try:
            return capture_region_jpeg(rect)
        finally:
            QTimer.singleShot(800, lambda: self.screen_badge.setVisible(False))

    def _analyze_selected_region(self, image_bytes: bytes, question: str, rect) -> str:
        from screen_agent import analyze_region_bytes
        self.set_orb_state('thinking')
        try:
            # ScreenPointingService already obtained explicit user screen_view permission before capture
            return analyze_region_bytes(image_bytes, question, rect, check_permission=False)
        finally:
            self.set_orb_state('idle')

    def start_screen_pointing_ui(self, question: str = None):
        q = (question if isinstance(question, str) and question.strip() else None)
        if not q and hasattr(self, 'point_question_input'):
            q = self.point_question_input.text().strip()
        if not q:
            q = 'Explain what is in this selected screen region and identify key controls or messages.'

        def _on_point_result(answer: str):
            if hasattr(self, 'point_result_view'):
                self.point_result_view.setHtml(f"<b>Region Analysis:</b><br>{html.escape(answer).replace(chr(10), '<br>')}")
            self.append_log(f"[Point at Screen] {answer}")
            self.chat_entries.append({'role': 'model', 'text': f"[Point at Screen] {answer}", 'open': False})
            self._chat_dirty()

        self._active_region_selector = self.screen_pointing_service.select_and_explain(q, _on_point_result)

    # -- Feature 3: Guided Walkthroughs (manual Next / Back / Pause / Stop, guidance only)
    def _ui_start_walkthrough(self):
        goal = self.walkthrough_goal_input.text().strip() if hasattr(self, 'walkthrough_goal_input') else ''
        if not goal:
            goal, ok = QInputDialog.getText(self, 'Guided Walkthrough', 'What would you like a step-by-step walkthrough for?')
            if not ok or not goal.strip():
                return
            goal = goal.strip()
        if hasattr(self, 'walkthrough_goal_input'):
            self.walkthrough_goal_input.clear()
        if self.ensure_backend():
            self.send_cmd(cmd='walkthrough_cmd', action='start', goal=goal)

    def _ui_walkthrough_nav(self, action: str):
        if self.backend_running():
            self.send_cmd(cmd='walkthrough_cmd', action=action)
        elif action == 'stop' and hasattr(self, 'walkthrough_overlay'):
            self.walkthrough_overlay.hide_target()

    def _ui_walkthrough_toggle_pause(self):
        paused = getattr(self, '_walkthrough_paused', False)
        act = 'resume' if paused else 'pause'
        if self.backend_running():
            self.send_cmd(cmd='walkthrough_cmd', action=act)

    def _on_walkthrough_state_event(self, ev: dict):
        active = bool(ev.get('active', False))
        paused = bool(ev.get('paused', False))
        self._walkthrough_paused = paused
        if not active or not ev.get('step'):
            if hasattr(self, 'walkthrough_overlay'):
                self.walkthrough_overlay.hide_target()
            if hasattr(self, 'walkthrough_view'):
                self.walkthrough_view.setHtml('<i style="color:#9cb5c6">Walkthrough finished or stopped.</i>')
            if self.assistant_state == 'paused':
                self.set_orb_state('idle')
            return
        if paused:
            self.set_orb_state('paused')
        step = ev.get('step') or {}
        idx = ev.get('index', 1)
        total = ev.get('total', 1)
        title = html.escape(str(step.get('title', 'Step')))
        inst = html.escape(str(step.get('instruction', '')))
        target_lbl = step.get('target_label')
        target_rect = step.get('target_rect')
        caution = step.get('caution')
        pause_badge = ' <span style="color:#ffd060">[PAUSED]</span>' if paused else ''
        target_html = f'<br><span style="color:#5efaff">🎯 UI Target: {html.escape(str(target_lbl))}</span>' if target_lbl else ''
        caution_html = f'<br><span style="color:#ffb366">⚠ Caution: {html.escape(str(caution))}</span>' if caution else ''
        if hasattr(self, 'walkthrough_view'):
            self.walkthrough_view.setHtml(
                f"<b>Step {idx} of {total}: {title}</b>{pause_badge}<br>{inst}{target_html}{caution_html}"
                f"<br><small style='color:#9cb5c6'>Guidance only — perform the step yourself, then click Next.</small>"
            )
        if target_rect and not paused and hasattr(self, 'walkthrough_overlay'):
            self.walkthrough_overlay.show_target(target_rect, str(target_lbl or title))
        elif hasattr(self, 'walkthrough_overlay'):
            self.walkthrough_overlay.hide_target()

    # -- Feature 4: Reusable Semantic Workflows (Record / Review / Edit / Delete / Replay with per-step confirmation)
    def _ui_toggle_workflow_recording(self):
        if not self.ensure_backend():
            return
        if self._workflow_recording:
            name, ok = QInputDialog.getText(self, 'Save Recorded Workflow', 'Enter a name for this workflow:')
            if not ok:
                return
            self.send_cmd(cmd='workflow_cmd', action='stop_recording', name=name.strip() or 'Saved Workflow')
        else:
            self.send_cmd(cmd='workflow_cmd', action='start_recording')

    def _ui_refresh_workflows(self):
        if self.backend_running():
            self.send_cmd(cmd='workflow_cmd', action='list')
            return
        try:
            wf_dir = Path(os.environ.get('APPDATA') or Path.home()) / 'PC_Complete_Assistant' / 'workflows'
            store = WorkflowStore(wf_dir)
            items = []
            for wf in store.list_workflows():
                items.append({
                    'workflow_id': wf.workflow_id,
                    'name': wf.name,
                    'created_at': wf.created_at,
                    'steps': [{'action': s.action, 'arguments': s.arguments, 'description': s.description, 'created_at': s.created_at} for s in wf.steps],
                })
            self._on_workflow_state_event({'recording': False, 'recorded_steps': 0, 'workflows': items})
        except Exception:
            pass

    def _on_workflow_state_event(self, ev: dict):
        self._workflow_recording = bool(ev.get('recording', False))
        rec_steps = int(ev.get('recorded_steps', 0))
        self._saved_workflows_cache = list(ev.get('workflows') or [])
        if hasattr(self, 'wf_rec_btn'):
            self.wf_rec_btn.setText(f'■  Stop & Save ({rec_steps} steps)' if self._workflow_recording else '●  Start Recording')
        if not hasattr(self, 'workflow_view'):
            return
        lines = []
        if self._workflow_recording:
            lines.append(f"<p style='margin:0 0 6px 0;color:#5efaff'><b>● Recording active</b> ({rec_steps} approved semantic steps captured)</p>")
        if not self._saved_workflows_cache:
            lines.append('<i style="color:#9cb5c6">No saved workflows yet.</i>')
        else:
            for wf in self._saved_workflows_cache:
                wid = html.escape(str(wf.get('workflow_id', ''))[:8])
                wname = html.escape(str(wf.get('name', 'Untitled')))
                steps = wf.get('steps') or []
                step_names = ', '.join(html.escape(str(s.get('action', ''))) for s in steps[:5])
                lines.append(f"<p style='margin:0 0 6px 0'><b>[{wid}] {wname}</b> ({len(steps)} steps): <span style='color:#b8e2f8'>{step_names}</span></p>")
        self.workflow_view.setHtml(''.join(lines))

    def _pick_workflow_from_cache(self, title: str):
        if not self._saved_workflows_cache:
            QMessageBox.information(self, title, 'No saved workflows available.')
            return None
        choices = [f"{w.get('name', 'Untitled')} [{str(w.get('workflow_id', ''))[:8]}] ({len(w.get('steps') or [])} steps)" for w in self._saved_workflows_cache]
        choice, ok = QInputDialog.getItem(self, title, 'Select workflow:', choices, 0, False)
        if not ok or not choice:
            return None
        idx = choices.index(choice)
        return self._saved_workflows_cache[idx]

    def _ui_replay_workflow(self):
        wf = self._pick_workflow_from_cache('Replay Workflow')
        if not wf:
            return
        steps = wf.get('steps') or []
        step_lines = '\n'.join(f"  {i}. {s.get('action')} — {s.get('description', '')}" for i, s in enumerate(steps, 1))
        ans = QMessageBox.question(
            self,
            'Confirm Workflow Replay',
            f"Workflow: {wf.get('name')}\n\nSteps to replay:\n{step_lines or '  (no steps)'}\n\nEach step will also ask for your confirmation before executing. Start replay?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans == QMessageBox.Yes and self.ensure_backend():
            self.send_cmd(cmd='workflow_cmd', action='replay', workflow_id=wf.get('workflow_id'))

    def _ui_cancel_workflow_replay(self):
        if self.backend_running():
            self.send_cmd(cmd='workflow_cmd', action='cancel_replay')
            self.append_log('[Workflow] Requested replay cancellation.')

    def _ui_edit_workflow(self):
        wf = self._pick_workflow_from_cache('Review / Edit Workflow')
        if not wf:
            return
        dlg = WorkflowEditorDialog(wf, self)
        if dlg.exec() == QDialog.Accepted and self.ensure_backend():
            self.send_cmd(
                cmd='workflow_cmd',
                action='update_steps',
                workflow_id=wf.get('workflow_id'),
                name=dlg.workflow_name,
                steps=dlg.steps,
            )

    def _ui_delete_workflow(self):
        wf = self._pick_workflow_from_cache('Delete Workflow')
        if not wf:
            return
        ans = QMessageBox.question(
            self,
            'Delete Workflow',
            f"Delete saved workflow '{wf.get('name')}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans == QMessageBox.Yes and self.ensure_backend():
            self.send_cmd(cmd='workflow_cmd', action='delete', workflow_id=wf.get('workflow_id'))

    def _ui_open_workflow_templates(self):
        dlg = WorkflowTemplatesDialog(list_workflow_templates(), self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_template_id:
            if self.backend_running():
                self.send_cmd(
                    cmd='workflow_cmd',
                    action='install_template',
                    template_id=dlg.selected_template_id,
                    name=dlg.custom_name,
                )
            else:
                try:
                    wf_dir = Path(os.environ.get('APPDATA') or Path.home()) / 'PC_Complete_Assistant' / 'workflows'
                    store = WorkflowStore(wf_dir)
                    wf_obj = instantiate_template(dlg.selected_template_id, custom_name=dlg.custom_name or None)
                    store.save(wf_obj)
                    self.append_log(f"[Workflow] Installed template '{wf_obj.name}'.")
                    self._ui_refresh_workflows()
                except Exception as exc:
                    QMessageBox.warning(self, 'Template Install Error', str(exc))

    def _ui_debug_workflow(self):
        wf = self._pick_workflow_from_cache('Visual Workflow Debugger')
        if not wf:
            return
        dlg = WorkflowDebuggerDialog(wf, WorkflowDebugger, self)
        if dlg.exec() == QDialog.Accepted and dlg.request_live_replay:
            steps = wf.get('steps') or []
            step_lines = '\n'.join(f"  {i}. {s.get('action')} — {s.get('description', '')}" for i, s in enumerate(steps, 1))
            ans = QMessageBox.question(
                self,
                'Confirm Workflow Replay',
                f"Workflow: {wf.get('name')}\n\nSteps to replay:\n{step_lines or '  (no steps)'}\n\nEach step will also ask for your confirmation before executing. Start replay?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ans == QMessageBox.Yes and self.ensure_backend():
                self.send_cmd(cmd='workflow_cmd', action='replay', workflow_id=wf.get('workflow_id'))

    # -- Safe In-App Update Checker (background thread, never auto-executes)
    def _ui_check_for_updates(self):
        existing = getattr(self, '_update_worker', None)
        if existing is not None and existing.isRunning():
            return
        self.update_check_btn.setEnabled(False)
        self.update_status_lbl.setStyleSheet('color: #a5f1ff;')
        self.update_status_lbl.setText('Checking official Glass-Nova GitHub Releases…')

        class _UpdateWorker(QThread):
            finished_result = Signal(object)

            def run(self):
                res = check_for_updates(APP_VERSION)
                if not self.isInterruptionRequested():
                    self.finished_result.emit(res)

        worker = _UpdateWorker()
        self._update_worker = worker
        worker.finished_result.connect(self._apply_update_check_result)
        worker.finished.connect(lambda w=worker: self._on_update_worker_finished(w))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_update_worker_finished(self, worker):
        if getattr(self, '_update_worker', None) is worker:
            self._update_worker = None

    def _stop_update_worker(self):
        worker = getattr(self, '_update_worker', None)
        if worker is None:
            return
        self._update_worker = None
        try:
            worker.finished_result.disconnect(self._apply_update_check_result)
        except Exception:
            pass
        try:
            worker.requestInterruption()
            if worker.isRunning():
                worker.wait(350)
        except Exception:
            pass

    def _apply_update_check_result(self, result: UpdateCheckResult):
        self.update_check_btn.setEnabled(True)
        self._latest_update_result = result
        self._verified_download = None
        if hasattr(self, 'update_install_btn'):
            self.update_install_btn.setEnabled(False)
        if hasattr(self, 'update_progress_bar'):
            self.update_progress_bar.setVisible(False)
            self.update_progress_bar.setValue(0)
        safe_url = result.release_url if is_trusted_release_url(result.release_url) else RELEASES_PAGE_URL
        self._latest_update_url = safe_url

        if result.status == 'update_available':
            size_txt = f' ({format_size_mb(result.asset_size)})' if result.asset_size > 0 else ''
            if hasattr(self, 'update_version_lbl'):
                self.update_version_lbl.setText(f'Installed: v{result.current_version.lstrip("vV")}  ·  Latest: {result.latest_version}{size_txt}')
            self.update_status_lbl.setStyleSheet('color: #5efaff; font-weight: 700;')
            if result.has_downloadable_installer and result.asset_sha256:
                self.update_status_lbl.setText(f'✦ Update available: {result.latest_version}{size_txt} · Official SHA-256 digest ready.')
                if hasattr(self, 'update_download_btn'):
                    self.update_download_btn.setEnabled(True)
                    self.update_download_btn.setText(f'⬇  Download {result.latest_version}')
            elif result.has_downloadable_installer:
                self.update_status_lbl.setText(f'✦ Update available: {result.latest_version}{size_txt} (no official SHA-256 digest published — use Open Releases Page).')
                if hasattr(self, 'update_download_btn'):
                    self.update_download_btn.setEnabled(False)
            else:
                self.update_status_lbl.setText(f'✦ Update available: {result.latest_version} (open GitHub Releases to download).')
                if hasattr(self, 'update_download_btn'):
                    self.update_download_btn.setEnabled(False)
            self.update_open_btn.setText(f'↗  Open {result.latest_version} Page')
        elif result.status == 'up_to_date':
            if hasattr(self, 'update_version_lbl'):
                self.update_version_lbl.setText(f'Installed Version: v{result.current_version.lstrip("vV")} (Up to date)')
            self.update_status_lbl.setStyleSheet('color: #6ee7b7; font-weight: 600;')
            self.update_status_lbl.setText(result.message)
            if hasattr(self, 'update_download_btn'):
                self.update_download_btn.setEnabled(False)
            self.update_open_btn.setText('↗  Open Releases Page')
        elif result.status in ('offline', 'rate_limited'):
            self.update_status_lbl.setStyleSheet('color: #fbbf24;')
            self.update_status_lbl.setText(result.message)
            if hasattr(self, 'update_download_btn'):
                self.update_download_btn.setEnabled(False)
        else:
            self.update_status_lbl.setStyleSheet('color: #fda4af;')
            self.update_status_lbl.setText(result.message or 'Update check failed.')
            if hasattr(self, 'update_download_btn'):
                self.update_download_btn.setEnabled(False)

        if result.release_notes:
            header = f"{result.release_name or result.latest_version}\n" + ("─" * 32) + "\n"
            self.update_notes_box.setPlainText(header + result.release_notes)

    def _ui_download_update(self):
        res = getattr(self, '_latest_update_result', None)
        if not res or not res.has_downloadable_installer:
            return
        existing = getattr(self, '_download_worker', None)
        if existing is not None and existing.isRunning():
            return

        cancel_evt = threading.Event()
        self._download_cancel_evt = cancel_evt
        self._verified_download = None
        self.update_download_btn.setEnabled(False)
        self.update_check_btn.setEnabled(False)
        self.update_install_btn.setEnabled(False)
        self.update_cancel_btn.setVisible(True)
        self.update_cancel_btn.setEnabled(True)
        self.update_progress_bar.setValue(0)
        self.update_progress_bar.setVisible(True)
        self.update_status_lbl.setStyleSheet('color: #a5f1ff;')
        self.update_status_lbl.setText(f'Downloading {res.asset_name}…')

        class _DownloadWorker(QThread):
            progress = Signal(int, int, int)
            completed = Signal(object)

            def run(self):
                dl = download_update_installer(
                    res,
                    on_progress=lambda done, tot, pct: self.progress.emit(done, tot, pct) if not cancel_evt.is_set() else None,
                    is_cancelled=lambda: cancel_evt.is_set() or self.isInterruptionRequested(),
                )
                if not self.isInterruptionRequested():
                    self.completed.emit(dl)

        worker = _DownloadWorker()
        self._download_worker = worker
        worker.progress.connect(self._on_download_progress)
        worker.completed.connect(self._apply_download_result)
        worker.finished.connect(lambda w=worker: self._on_download_worker_finished(w))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_download_progress(self, downloaded: int, total: int, pct: int):
        self.update_progress_bar.setValue(max(0, min(100, int(pct))))
        if total > 0:
            self.update_status_lbl.setText(f'Downloading update… {pct}% ({format_size_mb(downloaded)} / {format_size_mb(total)})')
        else:
            self.update_status_lbl.setText(f'Downloading update… {format_size_mb(downloaded)}')

    def _ui_cancel_download(self):
        evt = getattr(self, '_download_cancel_evt', None)
        if evt is not None:
            evt.set()
        if hasattr(self, 'update_cancel_btn'):
            self.update_cancel_btn.setEnabled(False)
        self.update_status_lbl.setStyleSheet('color: #fbbf24;')
        self.update_status_lbl.setText('Cancelling download…')

    def _on_download_worker_finished(self, worker):
        if getattr(self, '_download_worker', None) is worker:
            self._download_worker = None

    def _stop_download_worker(self):
        evt = getattr(self, '_download_cancel_evt', None)
        if evt is not None:
            evt.set()
        worker = getattr(self, '_download_worker', None)
        if worker is None:
            return
        self._download_worker = None
        try:
            worker.progress.disconnect(self._on_download_progress)
        except Exception:
            pass
        try:
            worker.completed.disconnect(self._apply_download_result)
        except Exception:
            pass
        try:
            worker.requestInterruption()
            if worker.isRunning():
                worker.wait(350)
        except Exception:
            pass

    def _apply_download_result(self, dl: DownloadResult):
        self.update_check_btn.setEnabled(True)
        self.update_cancel_btn.setVisible(False)
        if dl.status == 'verified' and dl.verified:
            self._verified_download = dl
            self.update_progress_bar.setValue(100)
            self.update_status_lbl.setStyleSheet('color: #6ee7b7; font-weight: 700;')
            self.update_status_lbl.setText(f'✓ {dl.message} — Ready to install.')
            self.update_install_btn.setEnabled(True)
            self.update_download_btn.setEnabled(False)
        elif dl.status == 'cancelled':
            self._verified_download = None
            self.update_progress_bar.setVisible(False)
            self.update_status_lbl.setStyleSheet('color: #fbbf24;')
            self.update_status_lbl.setText('Download cancelled. Click Download Update to retry anytime.')
            self.update_install_btn.setEnabled(False)
            self.update_download_btn.setEnabled(bool(self._latest_update_result and self._latest_update_result.has_downloadable_installer))
        else:
            self._verified_download = None
            self.update_progress_bar.setVisible(False)
            self.update_status_lbl.setStyleSheet('color: #fda4af;')
            self.update_status_lbl.setText(dl.message or 'Download failed. You can retry or use Open Releases Page.')
            self.update_install_btn.setEnabled(False)
            self.update_download_btn.setEnabled(bool(self._latest_update_result and self._latest_update_result.has_downloadable_installer and self._latest_update_result.asset_sha256))

    def _ui_install_downloaded_update(self):
        dl = getattr(self, '_verified_download', None)
        if not dl or not dl.verified or dl.status != 'verified':
            QMessageBox.warning(self, 'Unverified Update', 'No verified installer is ready. Please download and verify the update first.')
            return

        fname = Path(dl.file_path).name
        ans = QMessageBox.question(
            self,
            'Install Verified Glass Nova Update',
            f'Verified installer ready:\n  {fname}\n  SHA-256: {dl.sha256[:16]}…\n\n'
            'Glass Nova will close cleanly and launch the Windows installer so you can complete the update.\n'
            '• Your settings, API key, and workflows in %APPDATA%\\PC_Complete_Assistant will be preserved.\n'
            '• If Glass Nova does not reopen automatically after installation, launch it from your Start Menu or Desktop shortcut.\n\n'
            'Close Glass Nova and launch the installer now?',
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans != QMessageBox.Yes:
            return

        self.update_install_btn.setEnabled(False)
        self.update_status_lbl.setStyleSheet('color: #5efaff; font-weight: 600;')
        self.update_status_lbl.setText('Launching verified Windows installer and closing Glass Nova…')
        ok, msg = launch_verified_installer(dl)
        if not ok:
            self.update_status_lbl.setStyleSheet('color: #fda4af;')
            self.update_status_lbl.setText(msg)
            QMessageBox.warning(self, 'Update Launch Failed', f'{msg}\n\nYou can also install manually via Open Releases Page.')
            return

        self.append_log(f'[Updater] {msg}')
        QTimer.singleShot(150, self.quit_app)

    def _ui_open_release_page(self):
        target = self._latest_update_url if is_trusted_release_url(self._latest_update_url) else RELEASES_PAGE_URL
        ans = QMessageBox.question(
            self,
            'Open Official GitHub Release',
            f'Open the official Glass Nova release page in your browser?\n\n{target}\n\nGlass Nova will never run installers without your explicit confirmation.',
            QMessageBox.Yes | QMessageBox.No,
        )
        if ans == QMessageBox.Yes:
            open_trusted_release_url(target)

    def _on_workflow_step_request(self, ev: dict):
        req_id = ev.get('id')
        step_idx = int(ev.get('step', 1))
        total = int(ev.get('total', 1))
        act = str(ev.get('action', ''))
        args = ev.get('arguments') or {}
        desc = str(ev.get('description', ''))
        dlg = WorkflowStepConfirmDialog(step_idx, total, act, args, desc, self)
        dlg.exec()
        self.send_cmd(
            cmd='workflow_step_response',
            id=req_id,
            approved=bool(dlg.approved),
            cancelled=bool(dlg.cancelled),
        )

    def quick_action(self, kind):
        try:
            import tools
            if kind == 'point_screen':
                self.start_screen_pointing_ui()
                return
            elif kind == 'gesture':
                self.toggle_hand_gesture()
                return
            elif kind == 'web':
                q, ok = QInputDialog.getText(self, 'Search the web', 'What should I search for?')
                if not ok or not q.strip(): return
                r = tools.search_web(q.strip())
            elif kind == 'downloads': r = tools.open_folder(str(Path.home() / 'Downloads'))
            elif kind == 'status': r = tools.system_status()
            else: r = tools.open_app({'calc': 'calculator', 'notepad': 'notepad', 'explorer': 'file explorer'}[kind])
            self.append_log(str(r))
        except Exception as e: self.append_log(f'Action failed: {e}')


def main():
    if '--backend' in sys.argv:
        import asyncio
        import pc_assistant
        asyncio.run(pc_assistant.amain())
        return
    app = QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setQuitOnLastWindowClosed(False); app.setStyle('Fusion')
    guard = SingleInstanceGuard()
    win_holder = {}

    def _focus_existing():
        w = win_holder.get('win')
        if w is not None:
            QTimer.singleShot(0, w.open_main_window)

    if not guard.acquire(on_focus_requested=_focus_existing, use_qt_ipc=True):
        sys.exit(0)

    win = Nova()
    win._instance_guard = guard
    win_holder['win'] = win
    app.aboutToQuit.connect(win.quit_app)
    win.winId(); win.show(); win.apply_backdrop()
    try:
        sys.exit(app.exec())   # native handle must exist before DWM calls
    finally:
        guard.release()
if __name__ == '__main__': main()
