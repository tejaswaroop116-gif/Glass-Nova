"""Automated tests for Glass Nova Windows Installer packaging & Safe In-App Update Checker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from urllib.error import HTTPError, URLError

import build_exe
import build_installer
from nova_updater import (
    APP_VERSION,
    RELEASES_PAGE_URL,
    UpdateCheckResult,
    check_for_updates,
    compare_versions,
    get_current_version,
    is_trusted_release_url,
    is_update_available,
    parse_github_release_payload,
    parse_semver,
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

        # Reject untrusted hosts, schemes, repos, traversal, and injection chars
        for bad in (
            "http://github.com/tejaswaroop30/Glass-Nova/releases",
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
        self.assertIn("PC_Complete_Assistant", iss_text)

        self.assertIn("nova_updater", build_exe.HIDDEN_IMPORTS)
        self.assertIn("pc_assistant", build_exe.HIDDEN_IMPORTS)
        cmd = build_exe.build_command()
        self.assertIn("--exclude-module", cmd)

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


if __name__ == "__main__":
    unittest.main()
