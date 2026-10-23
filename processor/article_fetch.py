"""公众号正文抓取（requests + BeautifulSoup4）。

策略：
    · 正文优先取 `#js_content`（微信正文容器），逐级回落到 `#content` / `article`
    · 微信正文带大量 data-* 与 <script>，抓取后做统一清洗
    · 反爬失败 / 超时 / 非微信域名 → 降级为「链接 + 标题」，绝不抛异常到主流程

可选依赖说明：
    BeautifulSoup4 是推荐依赖（pip install '.[all]'），装上后正文抽取更准；
    没装也不会崩，会自动回落到内置的正则抽取，宁可少抽一点也不让流程断在这里。
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

try:  # BeautifulSoup4 是可选依赖，缺失时走正则降级
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:  # pragma: no cover - 取决于运行环境
    BeautifulSoup = None  # type: ignore[assignment]
    _HAS_BS4 = False

from collector.models import TYPE_ARTICLE, Message
from core.log import get_logger

logger = get_logger("article_fetch")

_URL_RE = re.compile(r"https?://[^\s\"'<>）)】\]]+")
_WX_HOST_RE = re.compile(r"(mp\.weixin\.qq\.com|weixin\.qq\.com)", re.I)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ArticleFetcher:
    """公众号文章抓取器。

    Args:
        timeout: 单次请求超时秒数。
        user_agent: 请求 UA。
        max_chars: 正文最大保留字符数。
        retry: 失败重试次数。
        enabled: 总开关。
    """

    def __init__(
        self,
        timeout: int = 15,
        user_agent: str = DEFAULT_UA,
        max_chars: int = 3000,
        retry: int = 1,
        enabled: bool = True,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.max_chars = max_chars
        self.retry = max(0, int(retry))
        self.enabled = enabled
        self._session = requests.Session()
        self._session.trust_env = False
        self._cache: Dict[str, Dict[str, str]] = {}
        self._stats = {"ok": 0, "failed": 0, "skipped": 0, "chars": 0}
        if not _HAS_BS4:
            logger.warning(
                "未安装 beautifulsoup4，公众号正文将走正则降级抽取。"
                "安装命令：pip install beautifulsoup4"
            )

    # -------------------------------------------------------------- #
    # 对外入口
    # -------------------------------------------------------------- #
    def fetch(self, url: str) -> Dict[str, str]:
        """抓取文章。

        Args:
            url: 文章链接。

        Returns:
            `{"title": str, "text": str, "ok": bool}` 语义字典，
            ok 为 False 时 title 仍可能是从 URL 或页面里尽力提出来的。
        """
        result: Dict[str, Any] = {"title": "", "text": "", "ok": False, "url": url}
        if not url or not url.startswith("http"):
            self._stats["skipped"] += 1
            return result

        if url in self._cache:
            return dict(self._cache[url])

        if not self.enabled:
            logger.info("公众号抓取已禁用（article.enabled=false）")
            self._stats["skipped"] += 1
            return result

        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        html = ""
        for attempt in range(self.retry + 1):
            try:
                resp = self._session.get(
                    url, headers=headers, timeout=self.timeout, allow_redirects=True
                )
                if resp.status_code == 200:
                    html = resp.text
                    break
                logger.debug("抓取 %s 返回 %s", url, resp.status_code)
            except requests.exceptions.RequestException as exc:
                logger.debug("抓取 %s 异常（第 %d 次）：%s", url, attempt + 1, exc)

        if not html:
            logger.warning("公众号正文抓取失败，降级为链接：%s", url)
            self._stats["failed"] += 1
            result["title"] = self._title_from_url(url)
            return result

        try:
            title, text = self._extract(html)
        except Exception as exc:  # 解析库行为不可预期，兜住以免崩整个流程
            logger.warning("文章解析异常：%s", exc)
            title, text = "", ""

        if not text:
            logger.warning("未抽到正文，降级为链接：%s", url)
            self._stats["failed"] += 1
            result["title"] = title or self._title_from_url(url)
            return result

        if len(text) > self.max_chars:
            text = text[: self.max_chars].rstrip() + "…（正文过长，已截断）"

        result.update({"title": title or self._title_from_url(url), "text": text, "ok": True})
        self._stats["ok"] += 1
        self._stats["chars"] += len(text)
        self._cache[url] = dict(result)
        logger.info("公众号正文抓取成功（%d 字）：%s", len(text), result["title"][:30])
        return result

    def process_message(self, message: Message, fetcher: Optional[Callable[[Message], Optional[str]]] = None) -> bool:
        """就地处理一条链接消息，把标题 + 正文写回 message.content。

        Args:
            message: msg_type 为 article 的消息。
            fetcher: 可选，用于从消息里再拨一次 URL（默认直接用 content）。

        Returns:
            True 表示成功抓到正文。
        """
        if message.msg_type != TYPE_ARTICLE:
            return False

        url = (message.content or "").strip()
        if fetcher:
            try:
                url = (fetcher(message) or url).strip()
            except Exception as exc:  # 自定义取值器失败不能影响主流程
                logger.debug("URL 取值失败：%s", exc)

        # note 里可能存着 chatlog 解析出来的标题，先用上
        preset_title = ""
        if message.note.startswith("链接标题："):
            preset_title = message.note[len("链接标题：") :].strip()

        article = self.fetch(url)
        title = article.get("title") or preset_title or "未命名链接"

        if article.get("ok"):
            message.content = f"[公众号文章：《{title}》]\n正文：{article['text']}"
            message.raw_content = url
            message.note = "正文抓取成功"
            return True

        message.content = f"[公众号文章：《{title}》]\n链接：{url}（正文抓取失败，请以原链接为准）"
        message.raw_content = url
        message.note = "正文抓取失败，降级为链接"
        return False

    @property
    def stats(self) -> Dict[str, int]:
        """本次运行的抓取统计。"""
        return dict(self._stats)

    # -------------------------------------------------------------- #
    # 内部实现
    # -------------------------------------------------------------- #
    def _extract(self, html: str) -> Tuple[str, str]:
        """从 HTML 里同时抽出标题与正文文本。

        优先用 BeautifulSoup（准确），缺失时回落到正则抽取（能用）。
        """
        title = self._title(html)
        text = self._extract_bs4(html) if _HAS_BS4 else self._extract_regex(html)
        return title, self._clean(text)

    def _extract_bs4(self, html: str) -> str:
        """用 BeautifulSoup 抽取正文。"""
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as exc:
            logger.debug("BeautifulSoup 解析失败，回落正则：%s", exc)
            return self._extract_regex(html)

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        body = None
        for selector in ("#js_content", "#img-content", "#content", ".rich_media_content", "article"):
            node = soup.select_one(selector)
            if node and len(node.get_text(strip=True)) > 30:
                body = node
                break
        if body is None:
            body = soup.body or soup
        return body.get_text("\n", strip=True)

    def _extract_regex(self, html: str) -> str:
        """正则兜底抽取：去标签、去脚本样式，按段落断行。

        准确度不如 DOM 解析，但保证「没有 bs4 也能拿到可读正文」。
        """
        cleaned = re.sub(r"<head[\s\S]{0,200000}?</head>", " ", html, flags=re.I)
        cleaned = re.sub(r"<script[\s\S]{0,200000}?</script>", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"<style[\s\S]{0,200000}?</style>", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"<(br|p|div|li|h[1-6]|tr)[^>]*>", "\n", cleaned, flags=re.I)
        cleaned = re.sub(r"<[^>]{1,300}>", "", cleaned)
        replacements = {
            "&nbsp;": " ", "&amp;": "&", "&lt;": "<", "&gt;": ">",
            "&quot;": '"', "&#39;": "'", "&mdash;": "—", "&ldquo;": "“", "&rdquo;": "”",
        }
        for entity, char in replacements.items():
            cleaned = cleaned.replace(entity, char)
        return cleaned

    def _title(self, html: str) -> str:
        """抽取标题：og:title -> #activity-name -> h1 -> <title>。"""
        found = re.search(
            r'<meta[^>]+(?:property|name)=["\'](?:og:title|title)["\'][^>]*content=["\']([^"\']{1,200})["\']',
            html,
            re.I,
        )
        if not found:  # 属性顺序反过来也要兼容
            found = re.search(
                r'<meta[^>]+content=["\']([^"\']{1,200})["\'][^>]+(?:property|name)=["\'](?:og:title|title)["\']',
                html,
                re.I,
            )
        if found:
            return found.group(1).strip()
        for pattern in (
            r'id="activity-name"[^>]*>([^<]{1,200})<',
            r"<h1[^>]*>([^<]{1,200})</h1>",
            r"<title>([^<]{1,200})</title>",
        ):
            found = re.search(pattern, html, re.I)
            if found:
                return found.group(1).strip()
        return ""

    @staticmethod
    def _clean(text: str) -> str:
        """清洗正文：压空行、去重复、去常见水印行。"""
        noise = ("点击上方", "关注我们", "扫码", "长按 alert", "预览时标签不可点", "轻点两下取消赞")
        lines: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) < 2:
                continue
            if any(n in line for n in noise):
                continue
            lines.append(line)

        deduped: List[str] = []
        for line in lines:
            if not deduped or deduped[-1] != line:
                deduped.append(line)
        return "\n".join(deduped)

    @staticmethod
    def _title_from_url(url: str) -> str:
        """URL 里也提不出标题时的兜底文案。"""
        if "mp.weixin.qq.com" in url:
            return "微信公众号文章"
        return "外链文章"


def extract_urls(text: str) -> List[str]:
    """从一段文本里抽出所有 http(s) 链接（去重、保持顺序）。"""
    found: List[str] = []
    for url in _URL_RE.findall(text or ""):
        url = url.rstrip(".,;:!?")
        if url not in found:
            found.append(url)
    return found


def is_wechat_article(url: str) -> bool:
    """判断是否为微信公众号域名。"""
    return bool(_WX_HOST_RE.search(url or ""))


def build_fetcher_from_config(cfg: Dict[str, Any]) -> ArticleFetcher:
    """从 settings.yaml 解析结果构造 ArticleFetcher。"""
    article_cfg = cfg.get("article", {}) or {}
    return ArticleFetcher(
        timeout=int(article_cfg.get("timeout", 15)),
        user_agent=str(article_cfg.get("user_agent") or DEFAULT_UA),
        max_chars=int(article_cfg.get("max_chars", 3000)),
        retry=int(article_cfg.get("retry", 1)),
        enabled=bool(article_cfg.get("enabled", True)),
    )
