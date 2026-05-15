import zipfile
import tempfile
import unittest
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

from stock_assistant.core.config import DEFAULTS
from stock_assistant.core.skills import (
    install_skill_from_url,
    iri_to_uri,
    list_installed_skills,
    read_skill_content,
    strip_html_tags,
    web_search_results,
)


class FakeHttpResponse:
    def __init__(self, body: bytes, content_type: str = "text/markdown; charset=utf-8", url: str = "https://example.com/SKILL.md"):
        self.body = body
        self.headers = {"Content-Type": content_type}
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body

    def geturl(self):
        return self.url


class TestSkills(unittest.TestCase):
    def config(self):
        tmp = tempfile.TemporaryDirectory()
        config = deepcopy(DEFAULTS)
        config["skills"]["install_dir"] = tmp.name
        self.addCleanup(tmp.cleanup)
        return config

    def markdown_response(self):
        return FakeHttpResponse(b'---\nname: test-skill\ndescription: "A test skill"\n---\n\n# Body\n')

    def test_iri_to_uri_encodes_non_ascii_query(self):
        uri = iri_to_uri("https://example.com/search?q=股市 行情")

        self.assertEqual(uri, "https://example.com/search?q=%E8%82%A1%E5%B8%82%20%E8%A1%8C%E6%83%85")

    def test_strip_html_tags_removes_script_and_style_noise(self):
        text = strip_html_tags(
            "<html><head><style>.x{display:none}</style><script>window.noise = true;</script></head>"
            "<body><h1>财经滚动新闻</h1><p>上证综指上涨 1%。</p></body></html>"
        )

        self.assertIn("财经滚动新闻", text)
        self.assertIn("上证综指上涨 1%。", text)
        self.assertNotIn("window.noise", text)
        self.assertNotIn("display:none", text)

    def test_web_search_results_extracts_structured_links(self):
        html = (
            '<html><body><a href="/html/">搜索首页</a>'
            '<a href="/l/?uddg=https%3A%2F%2Ffinance.example.com%2Fa">今日股市行情</a>'
            '<p>上证综指上涨。</p></body></html>'
        )
        with patch(
            "stock_assistant.core.skills.fetch_url_bytes",
            return_value=(html.encode("utf-8"), "text/html", "https://duckduckgo.com/html/?q=x"),
        ):
            results = web_search_results("今天股市行情", ["duckduckgo"], max_results=5, timeout_seconds=20)

        self.assertEqual(results[0]["title"], "今日股市行情")
        self.assertEqual(results[0]["url"], "https://finance.example.com/a")
        self.assertEqual(results[0]["engine"], "duckduckgo")

    def zip_response(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "SKILL.md",
                '---\nname: multi-search-engine\ndescription: "Multi search"\n---\n\n# Multi Search Engine\n',
            )
            archive.writestr("references/advanced-search.md", "# Advanced\n")
            archive.writestr("scripts/search.py", "print('search')\n")
            archive.writestr("assets/icon.txt", "icon\n")
        return FakeHttpResponse(
            buffer.getvalue(),
            "application/zip",
            url="https://wry-manatee-359.convex.site/api/v1/download?slug=multi-search-engine",
        )

    def test_lists_and_reads_installed_skill(self):
        config = self.config()
        with patch("stock_assistant.core.skills.urllib.request.urlopen", return_value=self.markdown_response()):
            record = install_skill_from_url(config, "https://example.com/SKILL.md", name="test-skill")

        skills = list_installed_skills(config)
        payload = read_skill_content(config, "test-skill")

        self.assertEqual(record.name, "test-skill")
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].description, "A test skill")
        self.assertIn("# Body", payload["content"])

    def test_installs_skill_from_url(self):
        config = self.config()
        with patch("stock_assistant.core.skills.urllib.request.urlopen", return_value=self.markdown_response()):
            record = install_skill_from_url(config, "https://example.com/SKILL.md")

        self.assertEqual(record.name, "test-skill")
        self.assertTrue((record.path / "SKILL.md").exists())
        self.assertEqual((record.path / "source_url.txt").read_text(encoding="utf-8").strip(), "https://example.com/SKILL.md")

    def test_installs_skill_from_clawhub_page(self):
        config = self.config()
        with patch("stock_assistant.core.skills.urllib.request.urlopen", return_value=self.zip_response()):
            record = install_skill_from_url(config, "https://clawhub.ai/gpyangyoujun/multi-search-engine")

        content = (record.path / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(record.name, "multi-search-engine")
        self.assertIn("# Multi Search Engine", content)
        self.assertTrue((record.path / "references" / "advanced-search.md").exists())
        self.assertTrue((record.path / "scripts" / "search.py").exists())
        self.assertTrue((record.path / "assets" / "icon.txt").exists())
        self.assertEqual(
            (record.path / "source_url.txt").read_text(encoding="utf-8").strip(),
            "https://clawhub.ai/gpyangyoujun/multi-search-engine",
        )


if __name__ == "__main__":
    unittest.main()
