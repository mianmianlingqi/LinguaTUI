"""Translation engines.

Every backend implements ``async translate(client, text, source, target)`` and
returns a :class:`Translation`.  Line breaks are preserved by all of them, so a
paragraph selection comes back with the same layout.
"""

from __future__ import annotations

import asyncio
import html
import re
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import ClassVar, Protocol

import httpx

from . import langs
from .config import Config


class TranslateError(Exception):
    """A user-facing translation failure."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Translation:
    text: str
    source: str
    provider: str
    cached: bool = False


class Backend(Protocol):
    key: ClassVar[str]
    label: ClassVar[str]

    async def translate(
        self, client: httpx.AsyncClient, text: str, source: str, target: str
    ) -> Translation: ...

    def missing_reason(self, cfg: Config) -> str | None: ...


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #

_SPLIT_RE = re.compile(r"(?<=[。！？；!?;\n])")


def _hard_split(atom: str, limit: int) -> list[str]:
    pieces: list[str] = []
    while len(atom) > limit:
        cut = max(
            atom.rfind(" ", 0, limit),
            atom.rfind("，", 0, limit),
            atom.rfind(",", 0, limit),
        )
        if cut < limit // 2:
            cut = limit
        pieces.append(atom[:cut])
        atom = atom[cut:]
    if atom:
        pieces.append(atom)
    return pieces


def split_chunks(text: str, limit: int) -> list[str]:
    """Split text into <= limit sized chunks, keeping every character."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    buffer = ""
    for atom in _SPLIT_RE.split(text):
        if not atom:
            continue
        for piece in _hard_split(atom, limit) if len(atom) > limit else [atom]:
            if len(buffer) + len(piece) > limit and buffer:
                chunks.append(buffer)
                buffer = ""
            buffer += piece
    if buffer:
        chunks.append(buffer)
    return chunks or [text]


async def _with_retry(operation, attempts: int = 2):
    for attempt in range(attempts):
        try:
            return await operation()
        except TranslateError as exc:
            if not exc.retryable or attempt == attempts - 1:
                raise
            await asyncio.sleep(0.7 * (attempt + 1))
    raise AssertionError("unreachable")


def _http_error(provider: str, response: httpx.Response) -> TranslateError:
    detail = response.text.strip().replace("\n", " ")[:180]
    retryable = response.status_code in {429, 500, 502, 503, 504}
    return TranslateError(
        f"{provider} 返回 HTTP {response.status_code}：{detail or '无响应内容'}",
        retryable=retryable,
    )


# --------------------------------------------------------------------------- #
# Google (free web endpoint, no key)
# --------------------------------------------------------------------------- #


class GoogleBackend:
    key = "google"
    label = "Google 翻译（免费，无需 key）"
    ENDPOINT = "https://translate.googleapis.com/translate_a/single"
    CHUNK = 1500

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg

    def missing_reason(self, cfg: Config) -> str | None:
        return None

    async def translate(
        self, client: httpx.AsyncClient, text: str, source: str, target: str
    ) -> Translation:
        pieces: list[str] = []
        detected: str | None = None
        for chunk in split_chunks(text, self.CHUNK):
            part, src = await _with_retry(
                lambda chunk=chunk: self._translate_chunk(client, chunk, source, target)
            )
            pieces.append(part)
            detected = detected or src
        return Translation("".join(pieces), detected or source, self.key)

    async def _translate_chunk(
        self, client: httpx.AsyncClient, text: str, source: str, target: str
    ) -> tuple[str, str | None]:
        params = {
            "client": "gtx",
            "sl": source if source != "auto" else "auto",
            "tl": target,
            "dt": "t",
            "dj": "1",
            "q": text,
        }
        try:
            response = await client.get(self.ENDPOINT, params=params)
        except httpx.HTTPError as exc:
            raise TranslateError(f"网络错误：{exc}", retryable=True) from exc
        if response.status_code != 200:
            raise _http_error("Google", response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise TranslateError("Google 返回了无法解析的数据") from exc

        if isinstance(payload, dict):  # dj=1 form
            sentences = payload.get("sentences") or []
            translated = "".join(str(s.get("trans") or "") for s in sentences)
            return translated, payload.get("src")
        # classic nested-array form
        try:
            translated = "".join(seg[0] for seg in payload[0] if seg and seg[0])
        except (IndexError, TypeError) as exc:
            raise TranslateError("Google 返回了无法识别的数据") from exc
        detected = payload[2] if len(payload) > 2 and isinstance(payload[2], str) else None
        return translated, detected


# --------------------------------------------------------------------------- #
# MyMemory (free, rate limited, needs both languages)
# --------------------------------------------------------------------------- #


class MyMemoryBackend:
    key = "mymemory"
    label = "MyMemory（免费，每日限额）"
    ENDPOINT = "https://api.mymemory.translated.net/get"
    CHUNK = 450

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg

    def missing_reason(self, cfg: Config) -> str | None:
        return None

    async def translate(
        self, client: httpx.AsyncClient, text: str, source: str, target: str
    ) -> Translation:
        if source == "auto":
            raise TranslateError("MyMemory 需要明确指定源语言（不能是自动检测）")
        pieces = []
        for chunk in split_chunks(text, self.CHUNK):
            pieces.append(await _with_retry(lambda c=chunk: self._one(client, c, source, target)))
        return Translation("".join(pieces), source, self.key)

    async def _one(
        self, client: httpx.AsyncClient, text: str, source: str, target: str
    ) -> str:
        try:
            response = await client.get(
                self.ENDPOINT, params={"q": text, "langpair": f"{source}|{target}"}
            )
        except httpx.HTTPError as exc:
            raise TranslateError(f"网络错误：{exc}", retryable=True) from exc
        if response.status_code != 200:
            raise _http_error("MyMemory", response)
        payload = response.json()
        if payload.get("quotaFinished"):
            raise TranslateError("MyMemory 今日免费额度已用完，换一个引擎吧")
        data = payload.get("responseData") or {}
        translated = data.get("translatedText")
        if not translated:
            raise TranslateError(str(payload.get("responseDetails") or "MyMemory 没有返回译文"))
        return html.unescape(str(translated))


# --------------------------------------------------------------------------- #
# DeepL
# --------------------------------------------------------------------------- #


class DeeplBackend:
    key = "deepl"
    label = "DeepL（需 key，质量高）"
    FREE_ENDPOINT = "https://api-free.deepl.com/v2/translate"
    PRO_ENDPOINT = "https://api.deepl.com/v2/translate"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def missing_reason(self, cfg: Config) -> str | None:
        if not cfg.deepl_key:
            return "未配置 DeepL key：设置环境变量 LINGUA_DEEPL_KEY，或写入配置文件 deepl_key"
        return None

    @property
    def endpoint(self) -> str:
        return self.FREE_ENDPOINT if self.cfg.deepl_key.rstrip().endswith(":fx") else self.PRO_ENDPOINT

    async def translate(
        self, client: httpx.AsyncClient, text: str, source: str, target: str
    ) -> Translation:
        target_code = langs.deepl_code(target)
        if not target_code:
            raise TranslateError(f"DeepL 不支持目标语言 {langs.label(target)}")
        chunks = split_chunks(text, 2500)
        data: dict[str, object] = {
            "text": chunks,
            "target_lang": target_code,
            "preserve_formatting": "1",
        }
        if source != "auto":
            source_code = langs.deepl_code(source)
            if not source_code:
                raise TranslateError(f"DeepL 不支持源语言 {langs.label(source)}")
            data["source_lang"] = source_code
        headers = {"Authorization": f"DeepL-Auth-Key {self.cfg.deepl_key}"}
        try:
            response = await client.post(self.endpoint, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise TranslateError(f"网络错误：{exc}", retryable=True) from exc
        if response.status_code == 403:
            raise TranslateError("DeepL key 无效或已过期")
        if response.status_code == 456:
            raise TranslateError("DeepL 本月翻译额度已用完")
        if response.status_code != 200:
            raise _http_error("DeepL", response)
        payload = response.json()
        translations = payload.get("translations") or []
        if not translations:
            raise TranslateError("DeepL 没有返回译文")
        detected = translations[0].get("detected_source_language")
        return Translation(
            "".join(str(t.get("text", "")) for t in translations),
            (detected or source).lower() if source == "auto" else source,
            self.key,
        )


# --------------------------------------------------------------------------- #
# Any OpenAI-compatible chat endpoint
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


class OpenAIBackend:
    key = "openai"
    label = "OpenAI 兼容接口（需 key，质量最好）"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def missing_reason(self, cfg: Config) -> str | None:
        if not cfg.openai_key:
            return (
                "未配置 LLM key：设置环境变量 LINGUA_OPENAI_KEY"
                "（可选 LINGUA_OPENAI_BASE / LINGUA_OPENAI_MODEL），或写入配置文件 openai_key"
            )
        return None

    def _prompt(self, source: str, target: str) -> str:
        src = "the source language" if source == "auto" else langs.english(source)
        return (
            "You are a precise translation engine. "
            f"Translate the user's text from {src} into {langs.english(target)}. "
            "Keep every line break, paragraph, list marker, HTML tag and inline "
            "markup exactly as it is. Preserve the original tone and do not "
            "explain, summarise, add notes or wrap the output in quotes. "
            "Reply with the translation only."
        )

    async def translate(
        self, client: httpx.AsyncClient, text: str, source: str, target: str
    ) -> Translation:
        url = self.cfg.openai_base.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.cfg.openai_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": self._prompt(source, target)},
                {"role": "user", "content": text},
            ],
        }
        headers = {"Authorization": f"Bearer {self.cfg.openai_key}"}
        try:
            response = await client.post(
                url, json=payload, headers=headers, timeout=max(self.cfg.timeout, 60)
            )
        except httpx.HTTPError as exc:
            raise TranslateError(f"网络错误：{exc}", retryable=True) from exc
        if response.status_code in {401, 403}:
            raise TranslateError(f"API key 被拒绝（HTTP {response.status_code}）")
        if response.status_code == 404:
            raise TranslateError(f"接口不存在：{url}（检查 openai_base 配置）")
        if response.status_code != 200:
            raise _http_error("LLM", response)
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise TranslateError("模型返回了无法解析的数据") from exc
        content = str(content or "").strip()
        fenced = _FENCE_RE.match(content)
        if fenced:
            content = fenced.group(1).strip()
        if not content:
            raise TranslateError("模型返回了空译文")
        return Translation(content, source, self.key)


# --------------------------------------------------------------------------- #
# registry + cache
# --------------------------------------------------------------------------- #

BACKEND_CLASSES = [GoogleBackend, MyMemoryBackend, DeeplBackend, OpenAIBackend]
BACKENDS: dict[str, type] = {cls.key: cls for cls in BACKEND_CLASSES}
ORDER = [cls.key for cls in BACKEND_CLASSES]

_CACHE: "OrderedDict[tuple[str, str, str, str], Translation]" = OrderedDict()
_CACHE_MAX = 200


def get_backend(key: str, cfg: Config):
    cls = BACKENDS.get(key, GoogleBackend)
    return cls(cfg)


def label_of(key: str) -> str:
    cls = BACKENDS.get(key)
    return cls.label if cls else key


async def translate(
    backend,
    client: httpx.AsyncClient,
    text: str,
    source: str,
    target: str,
    *,
    use_cache: bool = True,
) -> Translation:
    """Translate ``text`` and cache small results."""
    cache_key = (backend.key, source, target, text)
    if use_cache and cache_key in _CACHE:
        _CACHE.move_to_end(cache_key)
        return replace(_CACHE[cache_key], cached=True)

    result = await backend.translate(client, text, source, target)
    if not result.text.strip():
        raise TranslateError("引擎返回了空译文，换个引擎试试")

    if use_cache and len(text) <= 8000:
        _CACHE[cache_key] = result
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return result


def clear_cache() -> None:
    _CACHE.clear()
