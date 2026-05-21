import re
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from html import unescape
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlsplit, urlunsplit

from stock_assistant.core.config import ROOT
from stock_assistant.core.utils import config_bool


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    path: Path
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "source": self.source,
        }


def skill_config(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("skills", {})


def skill_install_dir(config: dict[str, Any]) -> Path:
    return Path(skill_config(config).get("install_dir", ROOT / "data" / "skills")).expanduser()


def skill_roots(config: dict[str, Any]) -> list[Path]:
    roots = [skill_install_dir(config)]
    for item in skill_config(config).get("roots", []) or []:
        path = Path(str(item)).expanduser()
        if path not in roots:
            roots.append(path)
    return roots


def safe_skill_name(value: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip(".-_").lower()
    if not name:
        raise ValueError("skill 名称不能为空")
    return name[:80]


def parse_skill_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def skill_markdown_path(path: Path) -> Path | None:
    if path.is_file() and path.name == "SKILL.md":
        return path
    candidate = path / "SKILL.md"
    return candidate if candidate.is_file() else None


def read_skill_record(path: Path, *, max_chars: int = 20000) -> SkillRecord:
    skill_path = skill_markdown_path(path)
    if skill_path is None:
        raise ValueError(f"未找到 SKILL.md: {path}")
    text = skill_path.read_text(encoding="utf-8")
    metadata = parse_skill_frontmatter(text[:max_chars])
    local_name_path = skill_path.parent / "local_name.txt"
    local_name = local_name_path.read_text(encoding="utf-8").strip() if local_name_path.exists() else ""
    name = local_name or metadata.get("name") or skill_path.parent.name
    description = metadata.get("description", "")
    source_path = skill_path.parent / "source_url.txt"
    source = source_path.read_text(encoding="utf-8").strip() if source_path.exists() else ""
    return SkillRecord(name=name, description=description, path=skill_path.parent, source=source)


def list_installed_skills(config: dict[str, Any]) -> list[SkillRecord]:
    if not config_bool(skill_config(config).get("enabled", True)):
        return []
    records: dict[str, SkillRecord] = {}
    max_chars = int(skill_config(config).get("max_skill_chars", 20000) or 20000)
    for root in skill_roots(config):
        if not root.exists():
            continue
        candidates = [root] if skill_markdown_path(root) else [item for item in root.iterdir() if item.is_dir()]
        for candidate in candidates:
            try:
                record = read_skill_record(candidate, max_chars=max_chars)
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            records.setdefault(record.name, record)
    return sorted(records.values(), key=lambda item: item.name)


def find_skill(config: dict[str, Any], name: str) -> SkillRecord:
    requested = name.strip().lower()
    for record in list_installed_skills(config):
        if record.name.lower() == requested or record.path.name.lower() == requested:
            return record
    raise ValueError(f"未找到 skill: {name}")


def read_skill_content(config: dict[str, Any], name: str) -> dict[str, Any]:
    record = find_skill(config, name)
    max_chars = int(skill_config(config).get("max_skill_chars", 20000) or 20000)
    content = (record.path / "SKILL.md").read_text(encoding="utf-8")
    truncated = len(content) > max_chars
    return {
        **record.to_dict(),
        "content": content[:max_chars],
        "truncated": truncated,
        "summary": f"返回 skill: {record.name}" + ("（内容已截断）" if truncated else ""),
    }


def skill_file_paths(config: dict[str, Any], name: str) -> list[str]:
    record = find_skill(config, name)
    paths: list[str] = []
    for path in record.path.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(record.path).as_posix()
        if relative in {"source_url.txt", "local_name.txt"}:
            continue
        paths.append(relative)
    return sorted(paths)


def read_skill_file_content(config: dict[str, Any], name: str, file_path: str) -> dict[str, Any]:
    record = find_skill(config, name)
    relative_path = Path(file_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"不安全的 skill 文件路径: {file_path}")
    path = record.path / relative_path
    if not path.is_file():
        raise ValueError(f"未找到 skill 文件: {file_path}")
    max_chars = int(skill_config(config).get("max_skill_chars", 20000) or 20000)
    content = path.read_text(encoding="utf-8")
    truncated = len(content) > max_chars
    return {
        "name": record.name,
        "path": relative_path.as_posix(),
        "content": content[:max_chars],
        "truncated": truncated,
        "summary": f"返回 skill 文件: {record.name}/{relative_path.as_posix()}" + ("（内容已截断）" if truncated else ""),
    }


def raw_github_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc != "github.com" or "/blob/" not in parsed.path:
        return url
    owner_repo, rest = parsed.path.lstrip("/").split("/blob/", 1)
    return f"https://raw.githubusercontent.com/{owner_repo}/{rest}"


def iri_to_uri(url: str) -> str:
    parts = urlsplit(url)
    path = quote(parts.path, safe="/%:@")
    query = quote(parts.query, safe="=&?/:;+,%@")
    fragment = quote(parts.fragment, safe="=&?/:;+,%@")
    netloc = parts.netloc.encode("idna").decode("ascii")
    return urlunsplit((parts.scheme, netloc, path, query, fragment))


def skill_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    filename = Path(parsed.path).stem
    if filename.lower() == "skill":
        parent = Path(parsed.path).parent.name
        return safe_skill_name(parent or "skill")
    return safe_skill_name(filename or parsed.netloc or "skill")


class HtmlTextExtractor(HTMLParser):
    BLOCK_TAGS = {
        "article",
        "br",
        "dd",
        "div",
        "dt",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }
    SKIP_TAGS = {"canvas", "noscript", "script", "style", "svg", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        text = unescape("".join(self.parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        return "\n".join(lines)


def strip_html_tags(text: str) -> str:
    parser = HtmlTextExtractor()
    try:
        parser.feed(text)
        parser.close()
        parsed = parser.text()
        if parsed:
            return parsed
    except Exception:  # noqa: BLE001
        pass
    text = re.sub(r"<(script|style|noscript|svg|template|canvas)\b[^>]*>.*?</\1>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<(br|p|div|li|h[1-6])\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text)


class HtmlLinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._current_href = ""
        self._current_text: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in HtmlTextExtractor.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "a":
            attrs_dict = {key.lower(): value or "" for key, value in attrs}
            self._current_href = attrs_dict.get("href", "")
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in HtmlTextExtractor.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._current_href:
            title = re.sub(r"\s+", " ", unescape("".join(self._current_text))).strip()
            href = normalize_result_url(self._current_href, self.base_url)
            if title and href:
                self.links.append({"title": title, "url": href})
            self._current_href = ""
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._current_href:
            return
        self._current_text.append(data)


def normalize_result_url(href: str, base_url: str) -> str:
    href = href.strip()
    if not href or href.startswith(("#", "javascript:", "mailto:")):
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("/"):
        href = urljoin(base_url, href)
    parsed = urlparse(href)
    if parsed.netloc in {"www.google.com", "google.com"} and parsed.path == "/url":
        query = parse_qs(parsed.query)
        href = (query.get("q") or query.get("url") or [""])[0]
    elif parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        href = (query.get("uddg") or [""])[0]
    elif parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/"):
        query = parse_qs(parsed.query)
        href = (query.get("u") or [""])[0]
        if href.startswith("a1"):
            href = href[2:]
        href = unquote(href)
    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if is_search_noise_url(href):
        return ""
    return href


def is_search_noise_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if any(item in host for item in {"google.com", "bing.com", "baidu.com", "so.com", "duckduckgo.com"}):
        noise_paths = {
            "/",
            "/search",
            "/s",
            "/web",
            "/html/",
            "/maps",
            "/images",
            "/preferences",
            "/account",
            "/intl",
        }
        return path in noise_paths or path.startswith("/search") or path.startswith("/s?")
    return False


def result_relevance_score(link: dict[str, str], engine: str) -> int:
    url = link.get("url", "")
    title = link.get("title", "")
    parsed = urlparse(url)
    score = 0
    if parsed.netloc:
        score += 10
    if engine == "duckduckgo" and not parsed.netloc.endswith("duckduckgo.com"):
        score += 20
    if "bing.com" in parsed.netloc or "google.com" in parsed.netloc or "baidu.com" in parsed.netloc:
        score -= 20
    if any(token in title.lower() for token in {"图片", "images", "videos", "maps", "登录", "settings"}):
        score -= 15
    if len(title) >= 8:
        score += 3
    return score


def extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    parser = HtmlLinkExtractor(base_url)
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001
        return []
    seen: set[str] = set()
    links: list[dict[str, str]] = []
    for link in parser.links:
        url = link["url"]
        if url in seen:
            continue
        seen.add(url)
        links.append(link)
    return links


SEARCH_ENGINE_URLS = {
    "baidu": "https://www.baidu.com/s?{query}",
    "bing_cn": "https://cn.bing.com/search?{query}&ensearch=0",
    "bing": "https://www.bing.com/search?{query}",
    "duckduckgo": "https://duckduckgo.com/html/?{query}",
    "google": "https://www.google.com/search?{query}",
    "360": "https://www.so.com/s?{query}",
}

SEARCH_ENGINE_ALIASES = {
    "baidu": "baidu",
    "百度": "baidu",
    "bing cn": "bing_cn",
    "bing_cn": "bing_cn",
    "必应中国版": "bing_cn",
    "必应 cn": "bing_cn",
    "bing int": "bing",
    "bing": "bing",
    "duckduckgo": "duckduckgo",
    "duck duck go": "duckduckgo",
    "ddg": "duckduckgo",
    "google": "google",
    "360": "360",
    "360搜索": "360",
}


def normalize_search_engine(engine: str) -> str:
    key = re.sub(r"[\s_-]+", " ", str(engine).strip().lower())
    normalized = SEARCH_ENGINE_ALIASES.get(key) or SEARCH_ENGINE_ALIASES.get(key.replace(" ", "_"))
    if not normalized:
        raise ValueError(f"不支持的搜索引擎: {engine}")
    return normalized


def search_url(engine: str, query: str) -> str:
    engine = normalize_search_engine(engine)
    key = "wd" if engine == "baidu" else "q"
    encoded_query = urlencode({key: query})
    return SEARCH_ENGINE_URLS[engine].format(query=encoded_query)


def infer_snippet(title: str, page_text: str) -> str:
    if not page_text:
        return ""
    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if title and title[:20] in line:
            snippet = " ".join(lines[index:index + 3])
            return snippet[:500]
    return ""


def web_search_results(
    query: str,
    engines: list[str],
    *,
    max_results: int,
    timeout_seconds: int,
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_engine in engines:
        try:
            engine = normalize_search_engine(raw_engine)
            url = search_url(engine, query)
            data, content_type, final_url = fetch_url_bytes(url, timeout_seconds=timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            output.append({
                "engine": str(raw_engine),
                "title": "搜索失败",
                "url": "",
                "snippet": str(exc),
            })
            continue
        html = data.decode("utf-8", errors="replace")
        text = strip_html_tags(html) if "html" in content_type else html
        links = sorted(extract_links(html, final_url), key=lambda item: result_relevance_score(item, engine), reverse=True)
        for link in links:
            if result_relevance_score(link, engine) <= 0:
                continue
            link_url = link["url"]
            if link_url in seen:
                continue
            seen.add(link_url)
            output.append({
                "engine": engine,
                "title": link["title"][:200],
                "url": link_url,
                "snippet": infer_snippet(link["title"], text),
            })
            if len([item for item in output if item.get("title") != "搜索失败"]) >= max_results:
                return output
    return output


def extract_clawhub_skill_markdown(html: str) -> str:
    title_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
    html_title = strip_html_tags(title_match.group(1)).strip() if title_match else ""
    plain_text = strip_html_tags(html)
    marker = re.search(r"(?m)^#\s+.+", plain_text)
    if marker:
        skill_text = plain_text[marker.start():]
    elif html_title and html_title in plain_text:
        skill_text = "# " + plain_text[plain_text.index(html_title):]
    else:
        raise ValueError("无法从 ClawHub 页面提取 SKILL.md 内容")
    stop = re.search(r"(?m)^(Downloads|Owner|Current version|Versions|License|Last updated)\b", skill_text)
    if stop:
        skill_text = skill_text[:stop.start()]
    skill_text = re.sub(r"\n{3,}", "\n\n", skill_text).strip()
    if not skill_text:
        raise ValueError("ClawHub 页面中的 SKILL.md 内容为空")
    return skill_text


def fetch_skill_markdown(url: str, *, timeout_seconds: int) -> str:
    request = urllib.request.Request(
        raw_github_url(url),
        headers={"User-Agent": "stock-assistant-skill-installer/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"下载 skill 失败: {exc}") from exc
    text = data.decode("utf-8")
    parsed = urlparse(url)
    if parsed.netloc.endswith("clawhub.ai") and "text/html" in content_type:
        return extract_clawhub_skill_markdown(text)
    if "SKILL.md" not in url and "text/" not in content_type and "markdown" not in content_type:
        raise ValueError("URL 必须指向可读取的 Markdown 文本，建议使用 raw SKILL.md 链接")
    if not text.strip():
        raise ValueError("下载到的 skill 内容为空")
    return text


def clawhub_download_url(url: str) -> str:
    parsed = urlparse(url)
    slug = Path(parsed.path).name
    if not slug:
        raise ValueError("ClawHub URL 缺少 skill slug")
    return f"https://wry-manatee-359.convex.site/api/v1/download?slug={slug}"


def fetch_url_bytes(url: str, *, timeout_seconds: int) -> tuple[bytes, str, str]:
    request = urllib.request.Request(
        iri_to_uri(raw_github_url(url)),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl() if hasattr(response, "geturl") else url
            data = response.read()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"URL 请求失败: {exc}") from exc
    return data, content_type, final_url


def zip_entries(data: bytes) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            path = Path(item.filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"zip 包含不安全路径: {item.filename}")
            normalized = path.as_posix()
            if normalized.startswith("__MACOSX/") or path.name == ".DS_Store":
                continue
            entries[normalized] = archive.read(item)
    if "SKILL.md" not in entries:
        raise ValueError("zip 包缺少 SKILL.md")
    return entries


def fetch_skill_package(url: str, *, timeout_seconds: int) -> tuple[dict[str, bytes], str]:
    parsed = urlparse(url)
    if parsed.netloc.endswith("clawhub.ai"):
        data, content_type, _ = fetch_url_bytes(clawhub_download_url(url), timeout_seconds=timeout_seconds)
        if "zip" in content_type or zipfile.is_zipfile(BytesIO(data)):
            return zip_entries(data), url
        text = fetch_skill_markdown(url, timeout_seconds=timeout_seconds)
        return {"SKILL.md": text.encode("utf-8")}, url

    data, content_type, final_url = fetch_url_bytes(url, timeout_seconds=timeout_seconds)
    if "zip" in content_type or zipfile.is_zipfile(BytesIO(data)):
        return zip_entries(data), final_url
    text = data.decode("utf-8")
    if "SKILL.md" not in url and "text/" not in content_type and "markdown" not in content_type:
        raise ValueError("URL 必须指向 zip 包或可读取的 Markdown 文本")
    if not text.strip():
        raise ValueError("下载到的 skill 内容为空")
    return {"SKILL.md": text.encode("utf-8")}, final_url


def write_skill_package(target_dir: Path, entries: dict[str, bytes]) -> None:
    for name, data in entries.items():
        target_path = target_dir / name
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)


def install_skill_from_url(config: dict[str, Any], url: str, name: str | None = None) -> SkillRecord:
    if not config_bool(skill_config(config).get("allow_url_install", True)):
        raise ValueError("配置已禁用 skills.allow_url_install")
    timeout_seconds = int(skill_config(config).get("install_timeout_seconds", 30) or 30)
    entries, source_url = fetch_skill_package(url, timeout_seconds=timeout_seconds)
    skill_text = entries["SKILL.md"].decode("utf-8")
    metadata = parse_skill_frontmatter(skill_text)
    skill_name = safe_skill_name(name or metadata.get("name") or skill_name_from_url(url))
    install_dir = skill_install_dir(config)
    target_dir = install_dir / skill_name
    install_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)
    write_skill_package(target_dir, entries)
    if name:
        (target_dir / "local_name.txt").write_text(skill_name + "\n", encoding="utf-8")
    (target_dir / "source_url.txt").write_text(source_url.strip() + "\n", encoding="utf-8")
    return read_skill_record(target_dir, max_chars=int(skill_config(config).get("max_skill_chars", 20000) or 20000))
