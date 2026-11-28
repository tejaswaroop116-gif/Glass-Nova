"""Automated tests for Glass Nova Windows Installer packaging & Safe In-App Update Checker."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import build_exe
import build_installer
import tools_files
from nova_updater import (
    APP_VERSION,
    RELEASES_PAGE_URL,
    DownloadResult,
    UpdateCheckResult,
    build_installer_launch_args,
    check_for_updates,
    cleanup_stale_update_files,
    compare_versions,
    compute_file_sha256,
    detect_installed_app_dir,
    download_update_installer,
    format_size_mb,
    get_current_version,
    get_update_temp_dir,
    is_trusted_asset_url,
    is_trusted_redirect_url,
    is_trusted_release_url,
    is_update_available,
    launch_verified_installer,
    parse_github_release_payload,
    parse_semver,
    verify_installer_file,
)


class SemanticVersionAndUrlTests(unittest.TestCase):
    def test_current_version_is_valid_semver(self):
        ver = get_current_version()
        self.assertEqual(ver, APP_VERSION)
        self.assertIsNotNone(parse_semver(ver))

    def test_semver_comparison_handles_multi_digit_and_prereleases(self):
        self.assertEqual(compare_versions("v1.2.0", "v1.10.0"), -1)
        self.assertEqual(compare_versions("v1.10.0", "v1.2.0"), 1)
        self.assertEqual(compare_versions("1.2.0", "v1.2.0"), 0)
        self.assertEqual(compare_versions("v1.0.0-beta", "v1.0.0"), -1)
        self.assertEqual(compare_versions("v1.0.0-alpha", "v1.0.0-beta"), -1)
        self.assertEqual(compare_versions("v1.0.0-beta", "v1.0.0-rc.1"), -1)
        self.assertTrue(is_update_available("1.2.0", "v1.10.0"))
        self.assertFalse(is_update_available("1.10.0", "v1.2.0"))
        self.assertFalse(is_update_available("1.0.0", "1.0.0-beta"))
        self.assertFalse(is_update_available("1.0.0", "not-a-version"))

        with self.assertRaises(ValueError):
            compare_versions("1.0.0", "invalid")

    def test_strict_official_github_release_url_validation(self):
        self.assertTrue(is_trusted_release_url("https://github.com/tejaswaroop30/Glass-Nova/releases"))
        self.assertTrue(is_trusted_release_url("https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v1.2.0"))
        self.assertTrue(is_trusted_release_url("https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe"))

        # Reject untrusted hosts, schemes, repos, spoofed suffixes, traversal, and injection chars
        for bad in (
            "http://github.com/tejaswaroop30/Glass-Nova/releases",
            "https://github.com/tejaswaroop30/Glass-Nova/releases-fake",
            "https://github.com/tejaswaroop30/Glass-Nova/releases_evil/tag/v1.0.0",
            "https://github.com/tejaswaroop30/Glass-Nova/releases2",
            "javascript:alert(1)",
            "file:///C:/Windows/System32/cmd.exe",
            "https://github.com.evil.com/tejaswaroop30/Glass-Nova/releases",
            "https://github.com/otheruser/Glass-Nova/releases",
            "https://github.com/tejaswaroop30/OtherRepo/releases",
            "https://github.com/tejaswaroop30/Glass-Nova/releases/../../other/repo",
            "https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v1.0.0\" && calc",
            "",
        ):
            self.assertFalse(is_trusted_release_url(bad), f"Expected untrusted URL to be rejected: {bad}")


class GitHubReleaseParsingAndNetworkTests(unittest.TestCase):
    def test_parse_valid_newer_release_and_up_to_date(self):
        payload = {
            "tag_name": "v2.0.0",
            "name": "Glass Nova v2.0.0",
            "html_url": "https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v2.0.0",
            "body": "Major update with signed installer.",
            "published_at": "2026-12-01T00:00:00Z",
        }
        res = parse_github_release_payload(payload, current_version="1.1.0-beta")
        self.assertEqual(res.status, "update_available")
        self.assertTrue(res.has_update)
        self.assertEqual(res.latest_version, "v2.0.0")
        self.assertEqual(res.release_url, payload["html_url"])
        self.assertIn("signed installer", res.release_notes)

        same = parse_github_release_payload(payload, current_version="2.0.0")
        self.assertEqual(same.status, "up_to_date")
        self.assertFalse(same.has_update)

    def test_spoofed_release_url_falls_back_to_official_releases_page(self):
        payload = {
            "tag_name": "v9.9.9",
            "html_url": "https://evil.example.com/malware.exe",
            "body": "Spoofed link",
        }
        res = parse_github_release_payload(payload, current_version="1.0.0")
        self.assertEqual(res.status, "update_available")
        self.assertEqual(res.release_url, RELEASES_PAGE_URL)

    def test_malformed_json_and_missing_fields_handled_gracefully(self):
        bad_json = check_for_updates(fetcher=lambda _u, _t: b"{not valid json")
        self.assertEqual(bad_json.status, "error")

        non_dict = check_for_updates(fetcher=lambda _u, _t: b"[1, 2, 3]")
        self.assertEqual(non_dict.status, "error")

        bad_tag = check_for_updates(fetcher=lambda _u, _t: json.dumps({"tag_name": "latest-nightly"}).encode("utf-8"))
        self.assertEqual(bad_tag.status, "error")

    def test_network_offline_timeout_and_rate_limit_handled_without_crashing(self):
        def raise_offline(_u, _t):
            raise URLError("No internet")

        def raise_timeout(_u, _t):
            raise TimeoutError("Timed out")

        def raise_rate_limit(u, _t):
            raise HTTPError(u, 403, "Rate limit exceeded", {}, None)

        def raise_not_found(u, _t):
            raise HTTPError(u, 404, "Not Found", {}, None)

        self.assertEqual(check_for_updates(fetcher=raise_offline).status, "offline")
        self.assertEqual(check_for_updates(fetcher=raise_timeout).status, "offline")
        self.assertEqual(check_for_updates(fetcher=raise_rate_limit).status, "rate_limited")
        self.assertEqual(check_for_updates(fetcher=raise_not_found).status, "error")


class PackagingAndInstallerConfigTests(unittest.TestCase):
    def test_inno_setup_script_and_packaging_configuration(self):
        info = build_installer.validate_packaging_setup()
        self.assertTrue(info["ok"])
        self.assertTrue(info["iss_exists"])
        self.assertTrue(info["preserves_user_data"])
        self.assertTrue(info["excludes_env"])

        iss_text = Path(info["iss_path"]).read_text(encoding="utf-8")
        self.assertIn("PrivilegesRequired=lowest", iss_text)
        self.assertIn("desktopicon", iss_text)
        self.assertIn("CloseApplications=yes", iss_text)
        self.assertIn("MB_YESNO or MB_DEFBUTTON2", iss_text)
        self.assertIn("not UninstallSilent", iss_text)
        self.assertIn("RegDeleteValue(HKCU, 'Software\\Microsoft\\Windows\\CurrentVersion\\Run', 'GlassNova')", iss_text)
        self.assertIn("PC_Complete_Assistant", iss_text)
        self.assertEqual(iss_text.count('Source: "{#SourceDistDir}'), 1)

        self.assertIn("nova_updater", build_exe.HIDDEN_IMPORTS)
        self.assertIn("pc_assistant", build_exe.HIDDEN_IMPORTS)
        cmd = build_exe.build_command()
        self.assertIn("--exclude-module", cmd)
        self.assertIn("--collect-binaries", cmd)
        self.assertIn("vosk", cmd)
        self.assertIn("sounddevice", cmd)
        self.assertIn("--collect-data", cmd)
        self.assertIn("mediapipe", cmd)

    def test_frozen_startup_registry_command_omits_ui_py(self):
        import ui_theme
        captured = {}

        class _FakeKey:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_set(_key, name, _res, _typ, val):
            captured[name] = val

        with patch.object(ui_theme.winreg, "OpenKey", return_value=_FakeKey()), \
             patch.object(ui_theme.winreg, "SetValueEx", side_effect=fake_set), \
             patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", r"C:\Apps\GlassNova\GlassNova.exe"):
            ok = ui_theme.set_run_at_startup(True)
            self.assertTrue(ok)
            self.assertEqual(captured["GlassNova"], r'"C:\Apps\GlassNova\GlassNova.exe"')
            self.assertNotIn("ui.py", captured["GlassNova"])

    def test_user_appdata_dotenv_takes_priority_over_local_dotenv(self):
        try:
            from dotenv import load_dotenv
        except Exception as exc:
            self.skipTest(f"python-dotenv unavailable: {exc}")

        with tempfile.TemporaryDirectory() as tmp:
            user_env = Path(tmp) / "user.env"
            app_env = Path(tmp) / "app.env"
            user_env.write_text("NOVA_TEST_KEY=from_appdata\n", encoding="utf-8")
            app_env.write_text("NOVA_TEST_KEY=from_install_dir\nNOVA_FALLBACK_KEY=only_local\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NOVA_TEST_KEY", None)
                os.environ.pop("NOVA_FALLBACK_KEY", None)
                load_dotenv(user_env, override=False)
                load_dotenv(app_env, override=False)
                self.assertEqual(os.environ.get("NOVA_TEST_KEY"), "from_appdata")
                self.assertEqual(os.environ.get("NOVA_FALLBACK_KEY"), "only_local")

    def test_frozen_install_directory_protected_by_tools_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_dir = Path(tmp) / "GlassNovaInstall"
            install_dir.mkdir()
            fake_exe = install_dir / "GlassNova.exe"
            fake_exe.write_text("bin", encoding="utf-8")
            nested_file = install_dir / "assets" / "icon.png"
            nested_file.parent.mkdir()
            nested_file.write_text("img", encoding="utf-8")

            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(fake_exe)):
                self.assertTrue(tools_files._is_protected_path(install_dir))
                self.assertTrue(tools_files._is_protected_path(nested_file))

    def test_update_worker_lifecycle_duplicate_guard_and_safe_shutdown(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTextEdit
            import ui
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        release_evt = threading.Event()
        calls = []

        def slow_check(_ver):
            calls.append(1)
            release_evt.wait(timeout=1.5)
            return UpdateCheckResult(status="up_to_date", current_version="1.1.0-beta", message="Up to date")

        class _WorkerHost:
            _ui_check_for_updates = ui.Nova._ui_check_for_updates
            _on_update_worker_finished = ui.Nova._on_update_worker_finished
            _stop_update_worker = ui.Nova._stop_update_worker
            _apply_update_check_result = ui.Nova._apply_update_check_result

            def __init__(self):
                self._latest_update_url = RELEASES_PAGE_URL
                self._update_worker = None
                self.update_check_btn = QPushButton("Check")
                self.update_open_btn = QPushButton("Open")
                self.update_status_lbl = QLabel("")
                self.update_notes_box = QTextEdit()

        host = _WorkerHost()
        with patch.object(ui, "check_for_updates", side_effect=slow_check):
            host._ui_check_for_updates()
            worker_ref = host._update_worker
            self.assertIsNotNone(worker_ref)
            # Duplicate click while running must not spawn a second worker
            host._ui_check_for_updates()
            self.assertIs(host._update_worker, worker_ref)
            # Clean shutdown stops & clears worker without freezing or crashing
            release_evt.set()
            host._stop_update_worker()
            self.assertIsNone(host._update_worker)
            worker_ref.wait(1000)
            self.assertEqual(len(calls), 1)
        app.processEvents()

    def test_update_checker_ui_state_transitions_offscreen(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
            import ui
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        app = QApplication.instance() or QApplication([])
        # Verify UI state handler logic on a lightweight dummy matching Nova's update widgets
        from PySide6.QtWidgets import QLabel, QPushButton, QTextEdit

        class _DummyWin:
            _apply_update_check_result = ui.Nova._apply_update_check_result

            def __init__(self):
                self._latest_update_url = RELEASES_PAGE_URL
                self.update_check_btn = QPushButton("Check")
                self.update_open_btn = QPushButton("Open")
                self.update_status_lbl = QLabel("")
                self.update_notes_box = QTextEdit()

        dummy = _DummyWin()

        # 1. Update available with spoofed URL -> falls back to official URL
        dummy._apply_update_check_result(
            UpdateCheckResult(
                status="update_available",
                current_version="1.1.0-beta",
                latest_version="v1.2.0",
                release_name="Glass Nova v1.2.0",
                release_notes="New features!",
                release_url="https://evil.example.com/bad",
            )
        )
        self.assertTrue(dummy.update_check_btn.isEnabled())
        self.assertEqual(dummy._latest_update_url, RELEASES_PAGE_URL)
        self.assertIn("v1.2.0", dummy.update_status_lbl.text())
        self.assertIn("New features!", dummy.update_notes_box.toPlainText())

        # 2. Up to date
        dummy._apply_update_check_result(
            UpdateCheckResult(status="up_to_date", current_version="1.1.0-beta", message="Glass Nova is up to date (v1.1.0-beta).")
        )
        self.assertIn("up to date", dummy.update_status_lbl.text().lower())

        # 3. Offline
        dummy._apply_update_check_result(
            UpdateCheckResult(status="offline", current_version="1.1.0-beta", message="Could not reach GitHub.")
        )
        self.assertIn("github", dummy.update_status_lbl.text().lower())
        app.processEvents()


class OneClickUpdaterSecurityAndDownloadTests(unittest.TestCase):
    def _make_fake_pe(self, size: int = 128 * 1024) -> tuple[bytes, str]:
        payload = b"MZ\x90\x00" + (b"NOVA_INSTALLER_DATA_" * ((size // 20) + 1))[: size - 4]
        digest = hashlib.sha256(payload).hexdigest().upper()
        return payload, digest

    def test_asset_parsing_with_api_digest_and_body_fallback(self):
        _, digest = self._make_fake_pe(1024)
        payload = {
            "tag_name": "v1.2.0",
            "name": "Glass Nova v1.2.0",
            "html_url": "https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v1.2.0",
            "body": "Bug fixes and one-click updater.",
            "assets": [
                {
                    "name": "checksums.txt",
                    "size": 120,
                    "browser_download_url": "https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/checksums.txt",
                },
                {
                    "name": "GlassNova-Setup-1.2.0.exe",
                    "size": 182000000,
                    "digest": f"sha256:{digest}",
                    "browser_download_url": "https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe",
                },
            ],
        }
        res = parse_github_release_payload(payload, current_version="1.1.0-beta")
        self.assertEqual(res.status, "update_available")
        self.assertTrue(res.has_downloadable_installer)
        self.assertEqual(res.asset_name, "GlassNova-Setup-1.2.0.exe")
        self.assertEqual(res.asset_size, 182000000)
        self.assertEqual(res.asset_sha256, digest)
        self.assertIn("MB", format_size_mb(res.asset_size))

        # Body SHA-256 fallback when API asset.digest is omitted
        payload_fallback = {
            "tag_name": "v1.2.0",
            "html_url": "https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v1.2.0",
            "body": f"Release notes\nSHA-256: `{digest.upper()}`\n",
            "assets": [
                {
                    "name": "GlassNova-Setup-1.2.0.exe",
                    "size": 1024,
                    "browser_download_url": "https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe",
                },
            ],
        }
        res_fb = parse_github_release_payload(payload_fallback, current_version="1.1.0-beta")
        self.assertEqual(res_fb.asset_sha256, digest)
        self.assertTrue(res_fb.has_downloadable_installer)

        # Untrusted asset URL is ignored
        payload_bad_asset = {
            "tag_name": "v1.2.0",
            "html_url": "https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v1.2.0",
            "assets": [
                {
                    "name": "GlassNova-Setup-1.2.0.exe",
                    "size": 1024,
                    "digest": f"sha256:{digest}",
                    "browser_download_url": "https://evil.example.com/GlassNova-Setup-1.2.0.exe",
                },
            ],
        }
        res_bad = parse_github_release_payload(payload_bad_asset, current_version="1.1.0-beta")
        self.assertFalse(res_bad.has_downloadable_installer)
        self.assertEqual(res_bad.asset_download_url, "")

    def test_asset_and_redirect_url_validation(self):
        self.assertTrue(
            is_trusted_asset_url(
                "https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe"
            )
        )
        for bad in (
            "http://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe",
            "https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v1.2.0",
            "https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/readme.txt",
            "https://github.com/other/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe",
            "https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/../../evil.exe",
        ):
            self.assertFalse(is_trusted_asset_url(bad), f"Expected rejected asset URL: {bad}")

        self.assertTrue(
            is_trusted_redirect_url(
                "https://objects.githubusercontent.com/github-production-release-asset-2e65be/12345"
            )
        )
        self.assertTrue(
            is_trusted_redirect_url(
                "https://release-assets.githubusercontent.com/github-production-release-asset/12345"
            )
        )
        for bad_redir in (
            "http://objects.githubusercontent.com/file.exe",
            "https://objects.githubusercontent.com.evil.com/file.exe",
            "https://evil.example.com/GlassNova-Setup-1.2.0.exe",
            "file:///C:/Temp/evil.exe",
        ):
            self.assertFalse(is_trusted_redirect_url(bad_redir), f"Expected rejected redirect: {bad_redir}")

    def test_download_happy_path_progress_verification_and_cleanup(self):
        payload, digest = self._make_fake_pe(150 * 1024)
        update = UpdateCheckResult(
            status="update_available",
            current_version="1.1.0-beta",
            latest_version="v1.2.0",
            release_url="https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v1.2.0",
            asset_name="GlassNova-Setup-1.2.0.exe",
            asset_size=len(payload),
            asset_download_url="https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe",
            asset_sha256=digest,
        )
        progress_events = []

        class _FakeResp(io.BytesIO):
            headers = {"Content-Length": str(len(payload))}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp) / "updates"
            temp_dir.mkdir()
            stale = temp_dir / "old.part"
            stale.write_bytes(b"stale")

            with patch("nova_updater.get_update_temp_dir", return_value=temp_dir), \
                 patch("nova_updater.build_opener") as mock_opener:
                mock_opener.return_value.open.return_value = _FakeResp(payload)
                res = download_update_installer(
                    update,
                    on_progress=lambda dl, tot, pct: progress_events.append((dl, tot, pct)),
                )

            self.assertEqual(res.status, "verified")
            self.assertTrue(res.verified)
            self.assertEqual(res.sha256, digest)
            self.assertEqual(res.size_bytes, len(payload))
            self.assertTrue(Path(res.file_path).is_file())
            self.assertEqual(Path(res.file_path).parent, temp_dir)
            self.assertFalse(stale.exists())
            self.assertGreaterEqual(len(progress_events), 2)
            self.assertEqual(progress_events[-1][0], len(payload))
            self.assertEqual(progress_events[-1][2], 100)

    def test_download_rejects_missing_digest_corrupted_truncated_non_pe_cancelled_and_network_error(self):
        payload, digest = self._make_fake_pe(64 * 1024)
        base_url = "https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe"

        class _FakeResp(io.BytesIO):
            def __init__(self, data: bytes):
                super().__init__(data)
                self.headers = {"Content-Length": str(len(data))}
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp) / "updates"
            temp_dir.mkdir()

            # 1. Missing official SHA-256 -> fail closed (unverified)
            no_digest_update = UpdateCheckResult(
                status="update_available",
                current_version="1.1.0-beta",
                latest_version="v1.2.0",
                asset_name="GlassNova-Setup-1.2.0.exe",
                asset_size=len(payload),
                asset_download_url=base_url,
                asset_sha256="",
            )
            with patch("nova_updater.get_update_temp_dir", return_value=temp_dir):
                res_no_digest = download_update_installer(no_digest_update)
            self.assertEqual(res_no_digest.status, "unverified")
            self.assertFalse(res_no_digest.verified)

            # 2. SHA-256 mismatch -> corrupted & deletes temp file
            wrong_digest_update = UpdateCheckResult(
                status="update_available",
                current_version="1.1.0-beta",
                latest_version="v1.2.0",
                asset_name="GlassNova-Setup-1.2.0.exe",
                asset_size=len(payload),
                asset_download_url=base_url,
                asset_sha256="0" * 64,
            )
            with patch("nova_updater.get_update_temp_dir", return_value=temp_dir), \
                 patch("nova_updater.build_opener") as mock_opener:
                mock_opener.return_value.open.return_value = _FakeResp(payload)
                res_bad_hash = download_update_installer(wrong_digest_update)
            self.assertEqual(res_bad_hash.status, "corrupted")
            self.assertFalse(res_bad_hash.verified)
            self.assertEqual(list(temp_dir.glob("*")), [])

            # 3. Truncated / incomplete download -> corrupted
            good_update = UpdateCheckResult(
                status="update_available",
                current_version="1.1.0-beta",
                latest_version="v1.2.0",
                asset_name="GlassNova-Setup-1.2.0.exe",
                asset_size=len(payload),
                asset_download_url=base_url,
                asset_sha256=digest,
            )
            with patch("nova_updater.get_update_temp_dir", return_value=temp_dir), \
                 patch("nova_updater.build_opener") as mock_opener:
                mock_opener.return_value.open.return_value = _FakeResp(payload[:1024])
                res_trunc = download_update_installer(good_update)
            self.assertEqual(res_trunc.status, "corrupted")
            self.assertEqual(list(temp_dir.glob("*")), [])

            # 4. Non-PE payload (HTML error page with matching hash) -> corrupted
            html_bytes = b"<html><body>Not an installer</body></html>"
            html_digest = hashlib.sha256(html_bytes).hexdigest().upper()
            html_update = UpdateCheckResult(
                status="update_available",
                current_version="1.1.0-beta",
                latest_version="v1.2.0",
                asset_name="GlassNova-Setup-1.2.0.exe",
                asset_size=len(html_bytes),
                asset_download_url=base_url,
                asset_sha256=html_digest,
            )
            with patch("nova_updater.get_update_temp_dir", return_value=temp_dir), \
                 patch("nova_updater.build_opener") as mock_opener:
                mock_opener.return_value.open.return_value = _FakeResp(html_bytes)
                res_html = download_update_installer(html_update)
            self.assertEqual(res_html.status, "corrupted")
            self.assertEqual(list(temp_dir.glob("*")), [])

            # 5. User cancellation -> cancelled & deletes .part
            with patch("nova_updater.get_update_temp_dir", return_value=temp_dir), \
                 patch("nova_updater.build_opener") as mock_opener:
                mock_opener.return_value.open.return_value = _FakeResp(payload)
                res_cancel = download_update_installer(good_update, is_cancelled=lambda: True)
            self.assertEqual(res_cancel.status, "cancelled")
            self.assertEqual(list(temp_dir.glob("*")), [])

            # 6. Network URLError -> error & cleans up
            with patch("nova_updater.get_update_temp_dir", return_value=temp_dir), \
                 patch("nova_updater.build_opener") as mock_opener:
                mock_opener.return_value.open.side_effect = URLError("Connection dropped")
                res_err = download_update_installer(good_update)
            self.assertEqual(res_err.status, "error")
            self.assertEqual(list(temp_dir.glob("*")), [])

    def test_detect_install_dir_launch_args_and_toctou_reverification(self):
        payload, digest = self._make_fake_pe(32 * 1024)

        # Dev mode returns None; frozen mode returns exe parent dir
        with patch.object(sys, "frozen", False, create=True):
            self.assertIsNone(detect_installed_app_dir())

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp) / "updates"
            temp_dir.mkdir()
            install_dir = Path(tmp) / "InstalledGlassNova"
            install_dir.mkdir()
            fake_app_exe = install_dir / "GlassNova.exe"
            fake_app_exe.write_bytes(b"MZ_APP")

            installer_path = temp_dir / "GlassNova-Setup-1.2.0.exe"
            installer_path.write_bytes(payload)

            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(fake_app_exe)), \
                 patch("nova_updater.get_update_temp_dir", return_value=temp_dir):
                detected = detect_installed_app_dir()
                self.assertEqual(detected, install_dir.resolve())

                args = build_installer_launch_args(installer_path, install_dir=detected)
                self.assertEqual(args[0], str(installer_path.resolve()))
                self.assertIn("/CLOSEAPPLICATIONS", args)
                self.assertIn("/RESTARTAPPLICATIONS", args)
                self.assertIn(f"/DIR={install_dir.resolve()}", args)
                # Never silent install
                for flag in args:
                    self.assertNotIn("SILENT", flag.upper())

                # Rejects launching an installer outside temp update dir
                outside = Path(tmp) / "outside.exe"
                outside.write_bytes(payload)
                with self.assertRaises(ValueError):
                    build_installer_launch_args(outside)

                # Launch succeeds when verified and untouched (and passes /DIR automatically when frozen)
                verified_res = DownloadResult(
                    status="verified",
                    file_path=str(installer_path),
                    size_bytes=len(payload),
                    sha256=digest,
                    verified=True,
                )
                with patch("nova_updater.subprocess.Popen") as mock_popen:
                    ok, msg = launch_verified_installer(verified_res)
                    self.assertTrue(ok, msg)
                    mock_popen.assert_called_once()
                    launched_args, kwargs = mock_popen.call_args
                    self.assertIn(f"/DIR={install_dir.resolve()}", launched_args[0])
                    self.assertFalse(kwargs.get("shell", False))

                # TOCTOU defense: if file is tampered with after download, launch MUST refuse
                installer_path.write_bytes(b"MZ_TAMPERED_PAYLOAD" + payload[19:])
                with patch("nova_updater.subprocess.Popen") as mock_popen:
                    ok, msg = launch_verified_installer(verified_res)
                    self.assertFalse(ok)
                    self.assertIn("verification failed", msg.lower())
                    mock_popen.assert_not_called()

                # Refuses unverified DownloadResult
                unverified_res = DownloadResult(status="corrupted", file_path=str(installer_path), verified=False)
                ok2, _ = launch_verified_installer(unverified_res)
                self.assertFalse(ok2)


class OneClickUpdaterUiTests(unittest.TestCase):
    def _build_ui_host(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton, QTextEdit
            import ui
        except Exception as exc:
            self.skipTest(f"PySide6 unavailable: {exc}")

        app = QApplication.instance() or QApplication([])

        class _FullUpdateHost:
            _apply_update_check_result = ui.Nova._apply_update_check_result
            _ui_download_update = ui.Nova._ui_download_update
            _on_download_progress = ui.Nova._on_download_progress
            _ui_cancel_download = ui.Nova._ui_cancel_download
            _on_download_worker_finished = ui.Nova._on_download_worker_finished
            _stop_download_worker = ui.Nova._stop_download_worker
            _apply_download_result = ui.Nova._apply_download_result
            _ui_install_downloaded_update = ui.Nova._ui_install_downloaded_update

            def __init__(self):
                self._latest_update_url = RELEASES_PAGE_URL
                self._latest_update_result = None
                self._verified_download = None
                self._update_worker = None
                self._download_worker = None
                self._download_cancel_evt = None
                self.update_check_btn = QPushButton("Check")
                self.update_download_btn = QPushButton("Download Update")
                self.update_download_btn.setVisible(False)
                self.update_cancel_btn = QPushButton("Cancel")
                self.update_cancel_btn.setVisible(False)
                self.update_install_btn = QPushButton("Install & Restart")
                self.update_install_btn.setVisible(False)
                self.update_open_btn = QPushButton("Open Releases")
                self.update_status_lbl = QLabel("")
                self.update_progress_bar = QProgressBar()
                self.update_progress_bar.setVisible(False)
                self.update_notes_box = QTextEdit()
                self.quit_called = False
                self.logs = []

            def append_log(self, msg):
                self.logs.append(msg)

            def quit_app(self):
                self.quit_called = True

        return app, ui, _FullUpdateHost()

    def test_download_worker_progress_cancel_and_install_confirmation_offscreen(self):
        app, ui, host = self._build_ui_host()

        update = UpdateCheckResult(
            status="update_available",
            current_version="1.1.0-beta",
            latest_version="v1.2.0",
            release_name="Glass Nova v1.2.0",
            release_notes="One-click update test",
            release_url="https://github.com/tejaswaroop30/Glass-Nova/releases/tag/v1.2.0",
            asset_name="GlassNova-Setup-1.2.0.exe",
            asset_size=10 * 1024 * 1024,
            asset_download_url="https://github.com/tejaswaroop30/Glass-Nova/releases/download/v1.2.0/GlassNova-Setup-1.2.0.exe",
            asset_sha256="A" * 64,
        )
        host._apply_update_check_result(update)
        self.assertTrue(host.update_download_btn.isEnabled())
        self.assertIn("10.00 MB", host.update_status_lbl.text())

        # Progress bar updates
        host._on_download_progress(5 * 1024 * 1024, 10 * 1024 * 1024, 50)
        self.assertEqual(host.update_progress_bar.value(), 50)
        self.assertIn("50%", host.update_status_lbl.text())

        # Verified download transitions UI to Install & Restart
        verified_dl = DownloadResult(
            status="verified",
            file_path=r"C:\Temp\GlassNova\updates\GlassNova-Setup-1.2.0.exe",
            size_bytes=10 * 1024 * 1024,
            sha256="A" * 64,
            verified=True,
            message="Download verified.",
        )
        host._apply_download_result(verified_dl)
        self.assertIs(host._verified_download, verified_dl)
        self.assertEqual(host.update_progress_bar.value(), 100)
        self.assertIn("verified", host.update_status_lbl.text().lower())

        # Install confirmation: user clicks No -> does NOT launch or quit
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No), \
             patch.object(ui, "launch_verified_installer") as mock_launch:
            host._ui_install_downloaded_update()
            mock_launch.assert_not_called()
            self.assertFalse(host.quit_called)

        # Install confirmation: user clicks Yes -> launches verified installer & schedules clean quit
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
             patch.object(ui, "launch_verified_installer", return_value=(True, "Launched")) as mock_launch, \
             patch.object(ui.QTimer, "singleShot", side_effect=lambda _ms, fn: fn()):
            host._ui_install_downloaded_update()
            mock_launch.assert_called_once_with(verified_dl)
            self.assertTrue(host.quit_called)

        # Corrupted download state resets verified download and enables retry
        host._apply_download_result(DownloadResult(status="corrupted", message="SHA-256 mismatch"))
        self.assertIsNone(host._verified_download)
        self.assertTrue(host.update_download_btn.isEnabled())
        self.assertFalse(host.update_install_btn.isEnabled())

        # Background download worker duplicate guard + clean stop
        release_evt = threading.Event()

        def fake_dl(_upd, on_progress=None, is_cancelled=None):
            release_evt.wait(timeout=1.5)
            return DownloadResult(status="cancelled", message="Download cancelled.")

        with patch.object(ui, "download_update_installer", side_effect=fake_dl):
            host._ui_download_update()
            w1 = host._download_worker
            self.assertIsNotNone(w1)
            host._ui_download_update()  # Duplicate click ignored
            self.assertIs(host._download_worker, w1)
            host._ui_cancel_download()
            self.assertTrue(host._download_cancel_evt.is_set())
            release_evt.set()
            host._stop_download_worker()
            self.assertIsNone(host._download_worker)
            w1.wait(1000)

        app.processEvents()


if __name__ == "__main__":
    unittest.main()
