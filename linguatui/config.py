"""Config file handling (``%LOCALAPPDATA%\\linguatui\\config.toml`` on Windows)."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from platformdirs import user_config_dir


def config_dir() -> Path:
    return Path(user_config_dir("linguatui", appauthor=False))


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Config:
    provider: str = "google"
    left_lang: str = "zh-CN"
    right_lang: str = "en"
    auto_popup: bool = True
    sync_selection: bool = True  # 普通拖选后，在另一栏高亮对应段落
    align: str = "heuristic"  # "heuristic" | "model"
    align_model: str = "bert-base-multilingual-cased"
    live: str = "left"  # "off" | "left" | "right" —— 边输入边翻译的方向
    remember_text: bool = False
    left_text: str = ""
    right_text: str = ""
    deepl_key: str = ""
    openai_key: str = ""
    openai_base: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    timeout: float = 25.0

    def copy(self) -> "Config":
        return Config(**asdict(self))


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    # JSON string escaping is a valid TOML basic-string escaping subset
    return json.dumps(str(value), ensure_ascii=False)


def save(cfg: Config, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# LinguaTUI 配置 —— 可手动编辑，退出应用时会被覆盖写入",
        "# keys 也可以放在环境变量里：LINGUA_DEEPL_KEY / LINGUA_OPENAI_KEY",
        "",
    ]
    for f in fields(cfg):
        lines.append(f"{f.name} = {_toml_value(getattr(cfg, f.name))}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def load(path: Path | None = None) -> Config:
    cfg = Config()
    target = path or config_path()
    if target.exists():
        try:
            raw = tomllib.loads(target.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            raw = {}
        for f in fields(cfg):
            if f.name in raw:
                value = raw[f.name]
                expected = type(getattr(cfg, f.name))
                if expected is float and isinstance(value, int):
                    value = float(value)
                if isinstance(value, expected):
                    setattr(cfg, f.name, value)
    return apply_env(cfg)


def apply_env(cfg: Config) -> Config:
    """Environment variables win over the config file."""
    env = os.environ
    if env.get("LINGUA_DEEPL_KEY"):
        cfg.deepl_key = env["LINGUA_DEEPL_KEY"]
    if env.get("LINGUA_OPENAI_KEY"):
        cfg.openai_key = env["LINGUA_OPENAI_KEY"]
    if env.get("LINGUA_OPENAI_BASE"):
        cfg.openai_base = env["LINGUA_OPENAI_BASE"]
    if env.get("LINGUA_OPENAI_MODEL"):
        cfg.openai_model = env["LINGUA_OPENAI_MODEL"]
    if env.get("LINGUA_PROVIDER"):
        cfg.provider = env["LINGUA_PROVIDER"]
    return cfg


def reset(path: Path | None = None) -> None:
    target = path or config_path()
    if target.exists():
        target.unlink()
