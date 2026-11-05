"""Headless smoke test — exercises the real UI + the real network backends.

Run:  python tests/smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from textual.widgets import TextArea  # noqa: E402
from textual.widgets.text_area import Selection  # noqa: E402

from linguatui import config  # noqa: E402
from linguatui.app import Linguatui, SelectionScreen  # noqa: E402


def dump_screen(app) -> str:
    """Exact plain-text dump of what the compositor is showing."""
    strips = app.screen._compositor.render_strips()
    return "\n".join(strip.text.rstrip() for strip in strips)


async def main() -> int:
    cfg = config.load()
    cfg.left_lang, cfg.right_lang = "zh-CN", "en"
    cfg.provider = "google"
    cfg.remember_text = False
    cfg.auto_popup = True
    cfg.live = "off"  # 其余用例用键盘/鼠标手动触发，避免异步写入干扰断言

    app = Linguatui(cfg)
    failures: list[str] = []

    def check(condition: bool, label: str) -> None:
        print(("  ok   " if condition else "  FAIL ") + label)
        if not condition:
            failures.append(label)

    @staticmethod
    def doc_offset(pane, row: int, col: int = 0) -> tuple[int, int]:
        """Mouse offset that lands on a given document location."""
        dx = pane.content_region.x - pane.region.x
        dy = pane.content_region.y - pane.region.y
        return (col + dx, row + dy)

    async def wait_result(pilot, application, timeout: float = 25.0) -> bool:
        """Wait until the open SelectionScreen has its translation."""
        target_app = application
        waited = 0.0
        while waited < timeout:
            screen = target_app.screen
            if isinstance(screen, SelectionScreen) and not screen._loading:
                await pilot.pause(0.1)
                return True
            await pilot.pause(0.2)
            waited += 0.2
        return False

    async with app.run_test(size=(118, 38)) as pilot:
        left = app.query_one("#ta-left", TextArea)
        right = app.query_one("#ta-right", TextArea)

        # 1. typing works
        left.focus()
        await pilot.press(*"你好世界")
        await pilot.pause()
        check(left.text == "你好世界", f"输入文本 -> {left.text!r}")

        # 2. Ctrl + 拖拽 -> 松手即弹窗
        await pilot.mouse_down("#ta-left", offset=(0, 1), control=True)
        await pilot.hover("#ta-left", offset=(8, 1))
        await pilot.mouse_up("#ta-left", offset=(8, 1))
        check(await wait_result(pilot, app), "Ctrl+拖选 弹出译文窗")
        if isinstance(app.screen, SelectionScreen):
            print(f"         popup title : {app.screen.query_one('#dlg-title').render()!s:.80}")
            print(f"         translation : {app.screen.query_one('#dlg-target').render()!s}")
            check(bool(app.screen.result_text), "弹窗拿到译文")

        svg = dump_screen(app)
        Path("tests/_screen_popup.txt").write_text(svg, encoding="utf-8")
        # 3. "a" replaces the selection with the translation
        await pilot.press("a")
        await pilot.pause()
        check(left.text != "你好世界", f"替换选中 -> {left.text!r}")
        check(not isinstance(app.screen, SelectionScreen), "弹窗已关闭")

        # 4. whole-document translation Ctrl+T
        left.load_text("第一句。\n第二句。")
        right.load_text("")
        left.focus()
        await pilot.press("ctrl+t")
        await pilot.pause(2.5)
        check(bool(right.text.strip()), f"Ctrl+T 全文翻译 -> {right.text!r}")

        # 5. reverse direction: select English on the right -> Chinese popup
        right.focus()
        right.selection = Selection((0, 0), (0, len(right.text.splitlines()[0])))
        await pilot.press("ctrl+g")
        await pilot.pause(0.5)
        ok_popup = await wait_result(pilot, app)
        check(ok_popup, "右栏选中 -> Ctrl+G 弹窗")
        if isinstance(app.screen, SelectionScreen):
            print(f"         reverse     : {app.screen.query_one('#dlg-target').render()!s}")
            svg = dump_screen(app)
            Path("tests/_screen_reverse.txt").write_text(svg, encoding="utf-8")
        await pilot.press("escape")
        await pilot.pause()

        # 6. swap panes
        before_left, before_right = left.text, right.text
        await pilot.press("ctrl+l")
        await pilot.pause()
        check(
            left.text == before_right and right.text == before_left,
            f"Ctrl+L 交换两栏 ({app.sides})",
        )

        # 7. language picker actually switches the language
        side_before = app._focused_side()
        await pilot.press("ctrl+o")
        await pilot.pause()
        check(app.screen.__class__.__name__ == "PickerScreen", "Ctrl+O 打开语言选择器")
        await pilot.press(*"japanese")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        check(app.sides[side_before] == "ja", f"筛选并选中日本語 -> {app.sides}")
        app.sides["left"], app.sides["right"] = "zh-CN", "en"
        app._sync_titles()

        # 8. engine picker
        await pilot.press("ctrl+p")
        await pilot.pause()
        check(app.screen.__class__.__name__ == "PickerScreen", "Ctrl+P 打开引擎选择器")
        await pilot.press("down", "enter")
        await pilot.pause()
        check(app.provider == "mymemory", f"切换引擎 -> {app.provider}")
        await pilot.press("ctrl+p")
        await pilot.pause()
        await pilot.press("up", "enter")
        await pilot.pause()
        check(app.provider == "google", f"切回引擎 -> {app.provider}")

        # 9. help screen
        await pilot.press("f1")
        await pilot.pause()
        check(app.screen.__class__.__name__ == "HelpScreen", "F1 打开帮助")
        await pilot.press("escape")
        await pilot.pause()

        # 10. keyboard selection does NOT auto-popup any more; Ctrl+G still works
        left.load_text("春天的花开得很好。")
        left.focus()
        left.move_cursor((0, len(left.text)))
        await pilot.press("shift+home")
        await pilot.pause(1.0)
        check(not isinstance(app.screen, SelectionScreen), "键盘选中不会自动弹窗")
        await pilot.press("ctrl+g")
        await pilot.pause(0.4)
        check(await wait_result(pilot, app), "键盘选中 + Ctrl+G 弹窗")
        if isinstance(app.screen, SelectionScreen):
            print(f"         keyboard    : {app.screen.query_one('#dlg-target').render()!s}")
        await pilot.press("escape")
        await pilot.pause()

        # 10b. 普通鼠标拖拽：只选字，不弹窗
        left.load_text("鼠标拖拽选中这一段文字。")
        left.focus()
        await pilot.pause()
        await pilot.mouse_down("#ta-left", offset=(0, 1))
        await pilot.hover("#ta-left", offset=(20, 1))
        await pilot.mouse_up("#ta-left", offset=(20, 1))
        await pilot.pause(0.8)
        dragged = left.selected_text
        check(bool(dragged.strip()), f"普通拖拽产生选区 -> {dragged!r}")
        check(not isinstance(app.screen, SelectionScreen), "普通拖拽不弹窗（只是选字）")

        # 10c. Ctrl+拖拽：松手即弹窗
        await pilot.mouse_down("#ta-left", offset=(0, 1), control=True)
        await pilot.hover("#ta-left", offset=(20, 1))
        await pilot.mouse_up("#ta-left", offset=(20, 1))
        mouse_ok = await wait_result(pilot, app, timeout=15)
        check(mouse_ok, "Ctrl+拖拽 松手即弹窗")
        if mouse_ok:
            print(f"         ctrl-drag   : {app.screen.query_one('#dlg-target').render()!s}")
        await pilot.press("escape")
        await pilot.pause()

        # 10d. Ctrl+单击（不拖）：翻光标所在段落
        await pilot.mouse_down("#ta-left", offset=(2, 1), control=True)
        await pilot.mouse_up("#ta-left", offset=(2, 1))
        await pilot.pause(0.4)
        await wait_result(pilot, app, timeout=15)
        click_mode = getattr(app.screen, "mode", "")
        check(click_mode == "paragraph", f"Ctrl+单击 翻当前段落 -> {click_mode!r}")
        await pilot.press("escape")
        await pilot.pause()

        # 11. "i" inserts into the other pane at its cursor
        right.load_text("")
        right.move_cursor((0, 0))
        left.selection = Selection((0, 0), (0, len(left.text)))
        await pilot.press("ctrl+g")
        await pilot.pause(0.5)
        ok_popup = await wait_result(pilot, app)
        check(ok_popup, "Ctrl+G 打开弹窗（响应）")
        if isinstance(app.screen, SelectionScreen):
            await pilot.press("i")
            await pilot.pause()
        check(right.text.strip() != "", f"i 插入另一栏 -> {right.text!r}")

        # 11b. the same selection again must hit the cache and render at once
        left.selection = Selection((0, 0), (0, len(left.text)))
        await pilot.press("ctrl+g")
        await pilot.pause(0.3)
        cached_ok = await wait_result(pilot, app, timeout=8)
        blob = ""
        if isinstance(app.screen, SelectionScreen):
            blob = str(app.screen.query_one("#dlg-target").render())
            await pilot.press("escape")
            await pilot.pause()
        check(cached_ok and "缓存" in blob, f"重复选中命中缓存 -> {blob.strip()[:40]!r}")

        # 12. narrow terminals stack the panes
        await pilot.resize_terminal(64, 30)
        await pilot.pause()
        check(app.query_one("#panes").has_class("-narrow"), "窄窗口自动上下排列")
        Path("tests/_screen_narrow.txt").write_text(dump_screen(app), encoding="utf-8")
        await pilot.resize_terminal(118, 38)
        await pilot.pause()

        # 13. final layout screenshot
        right.load_text("The quick brown fox jumps over the lazy dog.")
        left.load_text("敏捷的棕色狐狸越过了那只懒狗。")
        await pilot.pause()
        Path("tests/_screen_main.txt").write_text(dump_screen(app), encoding="utf-8")

        # 14. popup screenshot in the polished style
        left.selection = Selection((0, 0), (0, len(left.text)))
        await pilot.press("ctrl+g")
        await pilot.pause(0.5)
        await wait_result(pilot, app)
        Path("tests/_screen_popup.txt").write_text(dump_screen(app), encoding="utf-8")
        await pilot.press("escape")
        await pilot.pause()

        # 15. long selection stays inside a scrollable box
        long_text = "\n".join(f"第{i}段：这是一段用来测试长文本弹窗的信息。" for i in range(1, 9))
        left.load_text(long_text)
        left.selection = Selection((0, 0), (7, len(long_text.splitlines()[-1])))
        await pilot.press("ctrl+g")
        await pilot.pause(0.5)
        await wait_result(pilot, app)
        Path("tests/_screen_long.txt").write_text(dump_screen(app), encoding="utf-8")
        check(isinstance(app.screen, SelectionScreen), "长文本也能弹窗")
        await pilot.press("escape")
        await pilot.pause()

        # 16. empty pane -> Ctrl+T warns instead of crashing
        left.load_text("   ")
        right.load_text("")
        left.focus()
        await pilot.press("ctrl+t")
        await pilot.pause()
        check(right.text == "", "空栏 Ctrl+T 只提示不崩溃")

    # 17. an engine without a key explains itself instead of hanging
    cfg_plain = config.load()
    cfg_plain.provider = "deepl"
    cfg_plain.deepl_key = ""
    app2 = Linguatui(cfg_plain)
    async with app2.run_test(size=(110, 34)) as pilot:
        ta2 = app2.query_one("#ta-left", TextArea)
        ta2.load_text("没有 key 的引擎")
        ta2.selection = Selection((0, 0), (0, 5))
        await pilot.press("ctrl+g")
        await pilot.pause(0.5)
        blob = ""
        for _ in range(20):
            await pilot.pause(0.2)
            if isinstance(app2.screen, SelectionScreen) and not app2.screen._loading:
                blob = str(app2.screen.query_one("#dlg-target").render())
                break
        check("DeepL key" in blob, f"缺 key 的引擎给出提示 -> {blob.strip()[:46]!r}")
        await pilot.press("escape")
        await pilot.pause()

    # 18. remember_text round trip
    cfg_mem = config.load()
    cfg_mem.remember_text = True
    cfg_mem.auto_popup = False
    app3 = Linguatui(cfg_mem)
    async with app3.run_test(size=(100, 30)) as pilot:
        app3.query_one("#ta-left", TextArea).load_text("记住我：下次还在")
        await pilot.pause()
    saved = config.load()
    check(saved.remember_text and saved.left_text == "记住我：下次还在", "退出时保存正文")
    app4 = Linguatui(saved)
    async with app4.run_test(size=(100, 30)) as pilot:
        restored = app4.query_one("#ta-left", TextArea).text
        check(restored == "记住我：下次还在", f"再次启动恢复正文 -> {restored!r}")
    # 19. 输入即译：只打字，另一栏自动出译文
    cfg_live = config.load()
    cfg_live.left_lang, cfg_live.right_lang = "zh-CN", "en"
    cfg_live.provider = "google"
    cfg_live.live = "left"
    cfg_live.auto_popup = False
    cfg_live.remember_text = False
    cfg_live.left_text = ""
    cfg_live.right_text = ""
    app5 = Linguatui(cfg_live)
    async with app5.run_test(size=(110, 34)) as pilot:
        lt = app5.query_one("#ta-left", TextArea)
        rt = app5.query_one("#ta-right", TextArea)
        lt.focus()
        await pilot.press(*"今天天气不错")
        live_ok = False
        for _ in range(40):
            await pilot.pause(0.2)
            if rt.text.strip():
                live_ok = True
                break
        check(live_ok, f"输入即译：右栏自动出译文 -> {rt.text!r}")
        check(lt.text == "今天天气不错", "输入即译不会反向覆盖源栏")

        # 20. Ctrl+R 循环开关
        await pilot.press("ctrl+r")
        await pilot.pause()
        mode1 = app5.live_mode
        await pilot.press("ctrl+r")
        await pilot.pause()
        mode2 = app5.live_mode
        await pilot.press("ctrl+r")
        await pilot.pause()
        mode3 = app5.live_mode
        check(
            (mode1, mode2, mode3) == ("right", "off", "left"),
            f"Ctrl+R 循环：left -> {mode1} -> {mode2} -> {mode3}",
        )

        # 21. 不选任何东西，光标放段落里 Ctrl+G
        app5.live_mode = "off"
        lt.load_text("第一段第一行\n第一段第二行\n\n第二段")
        lt.move_cursor((0, 3))
        await pilot.press("ctrl+g")
        await pilot.pause(0.5)
        await wait_result(pilot, app5)
        popup_mode, original = "", ""
        if isinstance(app5.screen, SelectionScreen):
            popup_mode = app5.screen.mode
            original = app5.screen.original
        check(popup_mode == "paragraph", f"光标段落模式 -> {popup_mode!r} {original!r}")
        check("第一段第一行" in original and "第二段" not in original, "只取光标所在段落")
        if isinstance(app5.screen, SelectionScreen):
            await pilot.press("a")
            await pilot.pause()
        check(
            "第二段" in lt.text and "第一段第一行" not in lt.text,
            f"段落模式下 a 替换整段 -> {lt.text!r}",
        )

    # 22. 普通拖选 -> 另一边高亮对应段落
    cfg_sync = config.load()
    cfg_sync.left_lang, cfg_sync.right_lang = "zh-CN", "en"
    cfg_sync.live = "off"
    cfg_sync.auto_popup = True
    cfg_sync.sync_selection = True
    cfg_sync.remember_text = False
    cfg_sync.left_text = ""
    cfg_sync.right_text = ""
    app6 = Linguatui(cfg_sync)
    async with app6.run_test(size=(110, 30)) as pilot:
        lt = app6.query_one("#ta-left", TextArea)
        rt = app6.query_one("#ta-right", TextArea)
        lt.load_text("第一段甲。\n第一段乙。\n\n第二段甲。\n\n第三段甲。\n第三段乙。")
        rt.load_text("Para 1a.\nPara 1b.\n\nPara 2a.\n\nPara 3a.\nPara 3b.")
        lt.focus()
        await pilot.pause()

        await pilot.mouse_down("#ta-left", offset=doc_offset(lt, 0, 0))
        await pilot.hover("#ta-left", offset=doc_offset(lt, 0, 6))
        await pilot.mouse_up("#ta-left", offset=doc_offset(lt, 0, 6))
        await pilot.pause(0.4)
        check(
            rt.selected_text.strip().startswith("Para 1a")
            and "Para 1b" not in rt.selected_text,
            f"选第一句 -> 右栏只高亮对应那句 {rt.selected_text!r}",
        )
        check(app6._mirror_side == "right", f"高亮落在右栏 -> {app6._mirror_side}")
        check(not isinstance(app6.screen, SelectionScreen), "普通拖选仍然不弹窗")

        await pilot.mouse_down("#ta-left", offset=doc_offset(lt, 3, 0))
        await pilot.hover("#ta-left", offset=doc_offset(lt, 3, 4))
        await pilot.mouse_up("#ta-left", offset=doc_offset(lt, 3, 4))
        await pilot.pause(0.4)
        sel2 = rt.selection
        check(
            tuple(sel2.start)[0] == 3 and tuple(sel2.end)[0] == 3,
            f"选第二段 -> 右栏第二段 {sel2}",
        )

        # 句级锚定：源栏第二句 -> 目标栏第二句
        lt.selection = Selection((1, 0), (1, 5))
        app6.mirror_selection(lt)
        await pilot.pause(0.2)
        check(
            rt.selected_text.strip() == "Para 1b.",
            f"句对句锚定 -> {rt.selected_text!r}",
        )

        # 反向 + 词级：右栏只选 4 个字母 -> 左栏只高亮对应的几个字
        rt.selection = Selection((3, 0), (3, 4))
        app6.mirror_selection(rt)
        await pilot.pause(0.2)
        check(
            lt.selected_text.strip() == "第二段甲",
            f"英文小段 -> 中文只高亮对应分句 {lt.selected_text!r}",
        )

        lt.selection = Selection((5, 0), (6, 3))
        app6.mirror_selection(lt)
        await pilot.pause(0.2)
        sel3 = rt.selection
        check(
            tuple(sel3.start)[0] == 5 and tuple(sel3.end)[0] == 6,
            f"跨两行的段落地图 {sel3}",
        )

        await pilot.mouse_down("#ta-right", offset=doc_offset(rt, 1, 0))
        await pilot.mouse_up("#ta-right", offset=doc_offset(rt, 1, 0))
        await pilot.pause(0.3)
        check(app6._mirror_side is None and tuple(rt.selection.start) == tuple(rt.selection.end), "点击另一栏后对照高亮清除")

        app6.sync_selection = False
        lt.selection = Selection((0, 0), (0, 5))
        app6.mirror_selection(lt)
        await pilot.pause(0.2)
        check(tuple(rt.selection.start) == tuple(rt.selection.end), "关掉对照后不再高亮")
        app6.sync_selection = True

        # 段落数对不上时不做对照
        rt.load_text("only one block")
        await pilot.pause()
        app6._clear_mirror()
        lt.selection = Selection((0, 0), (0, 5))
        app6.mirror_selection(lt)
        await pilot.pause(0.2)
        check(not app6._mirror_exact, "段落数不同 -> 标记为近似对照")
        check(bool(rt.selected_text.strip()), f"段落数不同也给近似高亮 {rt.selected_text!r}")

        # 分句兜底：比例定位会差一两个词，但最终一定落在正确的分句里
        lt.load_text("该方法也不同于 ROME 等模型编辑方法，后者使用固定算法进行狭窄的更改，例如更新单个事实。")
        rt.load_text(
            "The approach also differs from model editing methods such as ROME, which use a "
            "fixed algorithm to make narrow changes like updating a single fact."
        )
        await pilot.pause()
        lt.selection = Selection((0, 1), (0, 7))
        app6.mirror_selection(lt)
        await pilot.pause(0.2)
        check(
            "model editing methods" in rt.selected_text
            and "narrow changes" not in rt.selected_text,
            f"分句级兜底 -> {rt.selected_text!r}",
        )
        # 整句选择 -> 整句高亮
        lt.selection = Selection((0, 0), (0, len(lt.text)))
        app6.mirror_selection(lt)
        await pilot.pause(0.2)
        check(
            rt.selected_text.strip().startswith("The approach also differs"),
            f"整句选择 -> 整句高亮 {rt.selected_text[:40]!r}",
        )

    # leave the user's config tidy
    clean = config.load()
    clean.remember_text = False
    clean.left_text = ""
    clean.right_text = ""
    clean.provider = "google"
    clean.auto_popup = True
    clean.live = "left"
    config.save(clean)

    # 23. 对齐算法（纯函数）单测
    from linguatui.app import _loc_from_offset, _map_offsets, _sentence_spans, _word_span

    check(len(_sentence_spans("One. Two! Three?")) == 3, "切句：英文三句")
    check(len(_sentence_spans("第一句。第二句！第三句？")) == 3, "切句：中文三句")
    check(_sentence_spans("no terminator") == [(0, 13)], "切句：无标点时整段一句")
    pos = "甲。乙。丙。".index("乙。")
    mapped = _map_offsets("甲。乙。丙。", "Aaa. Bbb. Ccc.", pos, pos + 2)
    check(mapped == (5, 9), f"句对句：第二句 -> 第二句 {mapped}")
    start, end = _word_span("See also explainable AI.", 4, 7)
    check("See also explainable AI."[start:end] == "also", f"词边界收拢 -> {'See also explainable AI.'[start:end]!r}")
    check(
        _loc_from_offset(["abc", "def"], 0, 1, 5) == (1, 1),
        f"偏移转行列 -> {_loc_from_offset(['abc', 'def'], 0, 1, 5)}",
    )

    print()
    if failures:
        print(f"{len(failures)} 项失败: {failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
