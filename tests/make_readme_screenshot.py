"""Regenerate the ASCII screenshot used in README.md (real compositor output)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textual.widgets import TextArea  # noqa: E402
from textual.widgets.text_area import Selection  # noqa: E402

from linguatui import backends, config  # noqa: E402
from linguatui.app import Linguatui, SelectionScreen  # noqa: E402

WIDTH, HEIGHT = 100, 26


async def main() -> int:
    cfg = config.load()
    cfg.left_lang, cfg.right_lang = "zh-CN", "en"
    cfg.provider = "google"
    cfg.remember_text = False
    cfg.live = "left"
    cfg.auto_popup = False

    app = Linguatui(cfg)
    async with app.run_test(size=(WIDTH, HEIGHT)) as pilot:
        left = app.query_one("#ta-left", TextArea)
        right = app.query_one("#ta-right", TextArea)
        left.load_text("今天天气不错，我们去公园散步吧。")
        for _ in range(80):  # 输入即译：右栏自动出译文
            await pilot.pause(0.2)
            if right.text.strip():
                break
        await pilot.pause(0.3)
        backends.clear_cache()  # 让弹窗显示实时结果，而不是「(缓存)」
        left.selection = Selection((0, 0), (0, len(left.text.splitlines()[0])))
        await pilot.press("ctrl+g")
        for _ in range(80):
            await pilot.pause(0.2)
            if isinstance(app.screen, SelectionScreen) and not app.screen._loading:
                break
        strips = app.screen._compositor.render_strips()
        lines = [strip.text.rstrip() for strip in strips]
        while lines and not lines[-1].strip():
            lines.pop()
        Path("tests/_readme_block.txt").write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines))
    return 0


raise SystemExit(asyncio.run(main()))
