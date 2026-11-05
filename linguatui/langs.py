"""Language catalogue shared by the TUI and the translation backends."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Language:
    code: str  # stable code stored in the config file
    label: str  # native label shown in the UI
    english: str  # English name, used in LLM prompts / DeepL hints
    deepl: str | None = None  # DeepL language code (None = unsupported)


AUTO = Language("auto", "自动检测 (源语言)", "auto-detect")

LANGUAGES: tuple[Language, ...] = (
    Language("zh-CN", "简体中文", "Chinese (Simplified)", "ZH"),
    Language("zh-TW", "繁體中文", "Chinese (Traditional)", "ZH-HANT"),
    Language("en", "English 英语", "English", "EN"),
    Language("ja", "日本語 日语", "Japanese", "JA"),
    Language("ko", "한국어 韩语", "Korean", "KO"),
    Language("fr", "Français 法语", "French", "FR"),
    Language("de", "Deutsch 德语", "German", "DE"),
    Language("es", "Español 西班牙语", "Spanish", "ES"),
    Language("pt", "Português 葡萄牙语", "Portuguese", "PT-PT"),
    Language("it", "Italiano 意大利语", "Italian", "IT"),
    Language("ru", "Русский 俄语", "Russian", "RU"),
    Language("ar", "العربية 阿拉伯语", "Arabic"),
    Language("hi", "हिन्दी 印地语", "Hindi"),
    Language("th", "ไทย 泰语", "Thai"),
    Language("vi", "Tiếng Việt 越南语", "Vietnamese"),
    Language("id", "Bahasa Indonesia 印尼语", "Indonesian", "ID"),
    Language("tr", "Türkçe 土耳其语", "Turkish", "TR"),
    Language("nl", "Nederlands 荷兰语", "Dutch", "NL"),
    Language("pl", "Polski 波兰语", "Polish", "PL"),
    Language("uk", "Українська 乌克兰语", "Ukrainian", "UK"),
    Language("cs", "Čeština 捷克语", "Czech", "CS"),
    Language("sk", "Slovenčina 斯洛伐克语", "Slovak", "SK"),
    Language("sl", "Slovenščina 斯洛文尼亚语", "Slovenian", "SL"),
    Language("sv", "Svenska 瑞典语", "Swedish", "SV"),
    Language("da", "Dansk 丹麦语", "Danish", "DA"),
    Language("nb", "Norsk 挪威语", "Norwegian", "NB"),
    Language("fi", "Suomi 芬兰语", "Finnish", "FI"),
    Language("ro", "Română 罗马尼亚语", "Romanian", "RO"),
    Language("hu", "Magyar 匈牙利语", "Hungarian", "HU"),
    Language("el", "Ελληνικά 希腊语", "Greek", "EL"),
    Language("et", "Eesti 爱沙尼亚语", "Estonian", "ET"),
    Language("lt", "Lietuvių 立陶宛语", "Lithuanian", "LT"),
    Language("lv", "Latviešu 拉脱维亚语", "Latvian", "LV"),
    Language("bg", "Български 保加利亚语", "Bulgarian", "BG"),
    Language("he", "עברית 希伯来语", "Hebrew"),
    Language("fa", "فارسی 波斯语", "Persian"),
    Language("ur", "اردو 乌尔都语", "Urdu"),
    Language("hi-IN", "हिन्दी (भारत) 印地语", "Hindi"),
    Language("ms", "Bahasa Melayu 马来语", "Malay"),
    Language("tl", "Filipino 菲律宾语", "Filipino"),
    Language("bn", "বাংলা 孟加拉语", "Bengali"),
    Language("ta", "தமிழ் 泰米尔语", "Tamil"),
    Language("te", "తెలుగు 泰卢固语", "Telugu"),
    Language("sw", "Kiswahili 斯瓦希里语", "Swahili"),
    Language("ne", "नेपाली 尼泊尔语", "Nepali"),
    Language("km", "ខ្មែរ 高棉语", "Khmer"),
    Language("lo", "ລາວ 老挝语", "Lao"),
    Language("my", "မြန်မာ 缅甸语", "Burmese"),
    Language("ca", "Català 加泰罗尼亚语", "Catalan"),
    Language("hr", "Hrvatski 克罗地亚语", "Croatian"),
    Language("sr", "Српски 塞尔维亚语", "Serbian"),
    Language("no", "Norsk Bokmål 挪威语", "Norwegian"),
    Language("cy", "Cymraeg 威尔士语", "Welsh"),
)

_INDEX: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}
_INDEX[AUTO.code] = AUTO

# tolerant aliases (people type "zh", "cn", "jp", "chinese", ...)
_ALIASES: dict[str, str] = {
    "zh": "zh-CN",
    "cn": "zh-CN",
    "chs": "zh-CN",
    "zh-hans": "zh-CN",
    "zh_cn": "zh-CN",
    "zh-hant": "zh-TW",
    "cht": "zh-TW",
    "zh_tw": "zh-TW",
    "tw": "zh-TW",
    "jp": "ja",
    "kr": "ko",
    "eng": "en",
    "deu": "de",
    "fra": "fr",
    "esp": "es",
    "rus": "ru",
    "por": "pt",
    "ita": "it",
    "auto": "auto",
    "detect": "auto",
}


def normalize(code: str | None) -> str:
    """Return a canonical language code (falls back to ``en``)."""
    if not code:
        return "en"
    key = code.strip()
    key = _ALIASES.get(key.lower(), key)
    if key in _INDEX:
        return key
    lowered = {c.lower(): c for c in _INDEX}
    return lowered.get(key.lower(), "en")


def get(code: str) -> Language:
    return _INDEX.get(normalize(code), _INDEX["en"])


def label(code: str) -> str:
    """Human label for a language code."""
    lang = _INDEX.get(code.strip()) if code else None
    if lang is not None:
        return lang.label
    return get(code).label


def english(code: str) -> str:
    return get(code).english


def short(code: str) -> str:
    """Short form of the label, e.g. ``English 英语`` -> ``English``."""
    return label(code).split(" ")[0]


def deepl_code(code: str) -> str | None:
    return get(code).deepl


def all_choices() -> list[Language]:
    """All selectable languages, auto-detect first."""
    return [AUTO, *LANGUAGES]
