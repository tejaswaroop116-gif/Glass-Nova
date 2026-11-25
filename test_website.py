"""Lightweight automated validation suite for the Glass Nova GitHub Pages website and docs.

Uses Python standard library only (runs in <0.1s with zero network calls):
- HTML structure, landmarks, heading hierarchy, accessibility & responsive CSS rules
- SEO metadata (title, description, canonical, Open Graph, Twitter, JSON-LD)
- sitemap.xml and robots.txt validity
- Internal anchor targets, HTTPS link hygiene, and username/URL consistency
"""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
SITE_URL = "https://tejaswaroop30.github.io/Glass-Nova/"
REPO_URL = "https://github.com/tejaswaroop30/Glass-Nova"
OLD_USERNAME = "tejaswaroop116-gif"


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.html_lang = ""
        self.title = ""
        self._in_title = False
        self.meta: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.anchors: list[dict[str, str]] = []
        self.headings: list[int] = []
        self.tags: set[str] = set()
        self.ids: set[str] = set()
        self.json_ld_blocks: list[str] = []
        self._in_json_ld = False
        self._json_ld_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        self.tags.add(t)
        d = {k.lower(): (v or "") for k, v in attrs}
        if "id" in d and d["id"]:
            self.ids.add(d["id"])
        if t == "html":
            self.html_lang = d.get("lang", "")
        elif t == "title":
            self._in_title = True
        elif t == "meta":
            self.meta.append(d)
        elif t == "link":
            self.links.append(d)
        elif t == "a":
            self.anchors.append(d)
        elif t in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(int(t[1]))
        elif t == "script" and d.get("type", "").lower() == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_buf = []

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "title":
            self._in_title = False
        elif t == "script" and self._in_json_ld:
            self._in_json_ld = False
            self.json_ld_blocks.append("".join(self._json_ld_buf))

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_json_ld:
            self._json_ld_buf.append(data)


class WebsiteValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index_path = DOCS / "index.html"
        cls.robots_path = DOCS / "robots.txt"
        cls.sitemap_path = DOCS / "sitemap.xml"
        cls.nojekyll_path = DOCS / ".nojekyll"
        cls.html_text = cls.index_path.read_text(encoding="utf-8")
        cls.parser = _PageParser()
        cls.parser.feed(cls.html_text)

    def test_required_github_pages_files_exist(self):
        for p in (self.index_path, self.robots_path, self.sitemap_path, self.nojekyll_path):
            self.assertTrue(p.is_file(), f"Missing required GitHub Pages file: {p}")

    def test_html_structure_accessibility_and_responsive_css(self):
        self.assertTrue(self.html_text.lstrip().lower().startswith("<!doctype html>"))
        self.assertEqual(self.parser.html_lang, "en")
        for landmark in ("header", "main", "nav", "footer"):
            self.assertIn(landmark, self.parser.tags, f"Missing semantic <{landmark}> landmark")

        # Exactly one H1, and heading levels never skip (e.g., h1 -> h3)
        self.assertEqual(self.parser.headings.count(1), 1, "Page must contain exactly one <h1>")
        self.assertEqual(self.parser.headings[0], 1, "First heading must be <h1>")
        for prev, curr in zip(self.parser.headings, self.parser.headings[1:]):
            self.assertLessEqual(curr - prev, 1, f"Skipped heading level: h{prev} -> h{curr}")

        # Keyboard focus, skip-to-content link, and mobile responsive rules
        self.assertIn(":focus-visible", self.html_text)
        self.assertIn("@media (max-width: 640px)", self.html_text)
        self.assertIn("prefers-reduced-motion", self.html_text)
        skip_links = [a for a in self.parser.anchors if a.get("href") == "#main-content"]
        self.assertTrue(skip_links, "Missing skip-to-content link targeting #main-content")
        self.assertIn("main-content", self.parser.ids)

    def test_seo_metadata_and_json_ld(self):
        title = self.parser.title.strip()
        self.assertTrue(15 <= len(title) <= 75, f"Title length should be concise and descriptive: '{title}'")

        meta_by_name = {m.get("name", "").lower(): m.get("content", "") for m in self.parser.meta if m.get("name")}
        meta_by_prop = {m.get("property", "").lower(): m.get("content", "") for m in self.parser.meta if m.get("property")}

        self.assertTrue(80 <= len(meta_by_name.get("description", "")) <= 260)
        self.assertIn("index", meta_by_name.get("robots", "").lower())
        self.assertTrue(meta_by_name.get("viewport"))
        self.assertTrue(meta_by_name.get("theme-color"))

        # Canonical & favicon
        canonicals = [l.get("href") for l in self.parser.links if l.get("rel") == "canonical"]
        self.assertEqual(canonicals, [SITE_URL])
        icons = [l.get("href") for l in self.parser.links if l.get("rel") == "icon"]
        self.assertTrue(icons, "Missing favicon link")

        # Open Graph & Twitter
        self.assertEqual(meta_by_prop.get("og:type"), "website")
        self.assertEqual(meta_by_prop.get("og:url"), SITE_URL)
        self.assertTrue(meta_by_prop.get("og:title"))
        self.assertTrue(meta_by_prop.get("og:description"))
        self.assertEqual(meta_by_name.get("twitter:card"), "summary")
        self.assertEqual(meta_by_name.get("twitter:url"), SITE_URL)
        self.assertTrue(meta_by_name.get("twitter:title"))
        self.assertTrue(meta_by_name.get("twitter:description"))

        # Valid JSON-LD SoftwareApplication
        self.assertEqual(len(self.parser.json_ld_blocks), 1)
        ld = json.loads(self.parser.json_ld_blocks[0])
        self.assertEqual(ld.get("@type"), "SoftwareApplication")
        self.assertEqual(ld.get("name"), "Glass Nova")
        self.assertEqual(ld.get("url"), SITE_URL)
        self.assertEqual(ld.get("codeRepository"), REPO_URL)
        self.assertIn("Windows", ld.get("operatingSystem", ""))

    def test_sitemap_and_robots_txt(self):
        tree = ET.parse(self.sitemap_path)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [el.text.strip() for el in tree.getroot().findall(".//sm:loc", ns) if el.text]
        self.assertEqual(locs, [SITE_URL])

        robots = self.robots_path.read_text(encoding="utf-8")
        self.assertIn("User-agent: *", robots)
        self.assertIn("Allow: /", robots)
        self.assertIn(f"Sitemap: {SITE_URL}sitemap.xml", robots)

    def test_links_and_username_consistency(self):
        self.assertTrue(self.parser.anchors, "Page must contain navigation links")
        for a in self.parser.anchors:
            href = a.get("href", "").strip()
            self.assertTrue(href, "Found empty anchor href")
            if href.startswith("#"):
                self.assertIn(href[1:], self.parser.ids, f"Broken internal anchor: {href}")
            else:
                self.assertTrue(href.startswith("https://"), f"External link must use HTTPS: {href}")

        # Verify docs, README, and CONTRIBUTING contain zero old username references and consistent URLs
        for rel in ("README.md", "CONTRIBUTING.md", "docs/index.html", "docs/robots.txt", "docs/sitemap.xml"):
            txt = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn(OLD_USERNAME, txt, f"Found outdated username '{OLD_USERNAME}' in {rel}")

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(SITE_URL, readme)
        self.assertIn(REPO_URL, readme)
        self.assertNotIn("once GitHub Pages is enabled", readme)


if __name__ == "__main__":
    unittest.main()
