"""LinguaTUI —— 双栏、拖动选中即译的终端翻译器。"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from typing import Sequence

import httpx
from rich.markup import escape
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    Markdown,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option
from textual.widgets.text_area import Selection

from . import __version__, backends, clipboard, config, langs

SIDES = ("left", "right")
SIDE_CN = {"left": "左栏", "right": "右栏"}
LIVE_DELAY = 1.1  # seconds of "typing pause" before live translation fires
LIVE_MAX = 3000  # don't auto-translate huge documents on every keystroke

# --------------------------------------------------------------------------- #
# popup: translation of the selected paragraph
# --------------------------------------------------------------------------- #


class SelectionScreen(ModalScreen[tuple[str, str] | None]):
    """Small floating window with the translation of the dragged selection."""

    BINDINGS = [
        Binding("escape", "close_dialog", "关闭", show=False),
        Binding("q", "close_dialog", "关闭", show=False),
        Binding("a", "replace", "替换选中", show=False),
        Binding("i", "insert", "插入另一栏", show=False),
        Binding("c", "copy", "复制译文", show=False),
        Binding("r", "retry", "重译", show=False),
    ]

    def __init__(
        self,
        original: str,
        source: str,
        target: str,
        provider: str,
        provider_label: str,
        auto: bool = False,
        side: str = "left",
        mode: str = "selection",
    ) -> None:
        super().__init__()
        self.original = original
        self.source = source
        self.target = target
        self.provider = provider
        self.provider_label = provider_label
        self.auto = auto
        self.side = side
        self.mode = mode
        self.result_text: str | None = None
        self.detected: str | None = None
        self._alive = True
        self._loading = True
        self._pending: tuple[backends.Translation | None, str | None] | None = None

    # -- layout ------------------------------------------------------------
    def _title(self) -> str:
        tag = " [dim]· Ctrl+拖选[/dim]" if self.auto else ""
        what = "光标段落" if self.mode == "paragraph" else "片段翻译"
        return (
            f"[b]{what}[/b] [dim]{SIDE_CN[self.side]}[/dim]{tag}   "
            f"[cyan]{escape(langs.label(self.source))}[/cyan]"
            f" → [green]{escape(langs.label(self.target))}[/green]"
        )

    def _meta(self) -> str:
        bits = [f"引擎 {escape(self.provider_label)}", f"{len(self.original)} 字"]
        if self.detected and self.detected != self.source:
            bits.insert(0, f"自动识别为 {escape(langs.label(self.detected))}")
        return " · ".join(bits)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(self._title(), id="dlg-title")
            yield Static(self._meta(), id="dlg-meta", classes="muted")
            yield Static("原文", classes="section-label")
            with VerticalScroll(id="dlg-source-box"):
                yield Static(escape(self.original), id="dlg-source")
            yield Static("译文", classes="section-label")
            with VerticalScroll(id="dlg-target-box"):
                yield Static("翻译中…", id="dlg-target", classes="muted")
                yield LoadingIndicator(id="dlg-loading")
            with Horizontal(id="dlg-buttons"):
                yield Button("替换(a)", id="btn-replace", variant="primary", disabled=True)
                yield Button("插入(i)", id="btn-insert", disabled=True)
                yield Button("复制(c)", id="btn-copy", disabled=True)
                yield Button("重译(r)", id="btn-retry")
                yield Button("关闭(esc)", id="btn-close")

    def on_mount(self) -> None:
        self.query_one("#btn-close", Button).focus()
        if self._pending is not None:
            result, error = self._pending
            self._pending = None
            self._apply(result, error)

    def on_unmount(self) -> None:
        self._alive = False

    # -- state -------------------------------------------------------------
    def start_loading(self) -> None:
        self._loading = True
        self._pending = None
        self.result_text = None
        self.detected = None
        try:
            target = self.query_one("#dlg-target", Static)
            target.add_class("muted")
            target.update("翻译中…")
            self.query_one("#dlg-loading", LoadingIndicator).display = True
            self.query_one("#dlg-meta", Static).update(self._meta())
            for button in self.query("#dlg-buttons Button").results(Button):
                button.disabled = button.id != "btn-close"
        except Exception:
            pass

    def finish(self, result: backends.Translation | None = None, error: str | None = None) -> None:
        """Deliver the outcome, also when it arrives before the screen mounts (cache hit)."""
        if not self._alive:
            return
        if not self.is_mounted:
            self._pending = (result, error)
            return
        self._apply(result, error)

    def _apply(self, result: backends.Translation | None, error: str | None) -> None:
        self._loading = False
        try:
            target = self.query_one("#dlg-target", Static)
            self.query_one("#dlg-loading", LoadingIndicator).display = False
        except Exception:
            return
        if error is not None:
            target.remove_class("muted")
            target.update(f"[red]{escape(error)}[/red]")
        else:
            self.result_text = result.text if result else ""
            self.detected = result.source if result else None
            suffix = "  [dim](缓存)[/dim]" if result and result.cached else ""
            target.remove_class("muted")
            target.update(escape(self.result_text) + suffix)
            try:
                self.query_one("#dlg-meta", Static).update(self._meta())
            except Exception:
                pass
        for button in self.query("#dlg-buttons Button").results(Button):
            button.disabled = False

    # -- actions -----------------------------------------------------------
    def action_close_dialog(self) -> None:
        self.dismiss(None)

    def _send(self, action: str) -> None:
        if not self.result_text:
            if self._loading:
                self.app.notify("译文还没回来，稍等一下或按 r 重试", severity="warning", timeout=2)
            return
        self.dismiss((action, self.result_text))

    def action_replace(self) -> None:
        self._send("replace")

    def action_insert(self) -> None:
        self._send("insert")

    def action_retry(self) -> None:
        self.start_loading()
        try:
            self.app.request_translation(self)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover
            self.finish(error=f"无法重译：{exc}")

    async def action_copy(self) -> None:
        if not self.result_text:
            return
        ok = await clipboard.copy(self.result_text)
        if not ok:
            self.app.copy_to_clipboard(self.result_text)  # OSC 52 fallback
        self.app.notify("译文已复制" if ok else "已通过 OSC52 发送到终端剪贴板", timeout=2)

    @on(Button.Pressed, "#btn-replace")
    def _p_replace(self) -> None:
        self.action_replace()

    @on(Button.Pressed, "#btn-insert")
    def _p_insert(self) -> None:
        self.action_insert()

    @on(Button.Pressed, "#btn-copy")
    async def _p_copy(self) -> None:
        await self.action_copy()

    @on(Button.Pressed, "#btn-retry")
    def _p_retry(self) -> None:
        self.action_retry()

    @on(Button.Pressed, "#btn-close")
    def _p_close(self) -> None:
        self.action_close_dialog()


# --------------------------------------------------------------------------- #
# generic filterable picker (languages / engines)
# --------------------------------------------------------------------------- #


class PickerScreen(ModalScreen[str | None]):
    """A modal list with a filter box. Items are ``(id, markup, haystack)``."""

    BINDINGS = [
        Binding("escape", "cancel_pick", "取消", show=False),
        Binding("down", "move_pick(1)", "下一个", show=False),
        Binding("up", "move_pick(-1)", "上一个", show=False),
    ]

    def __init__(
        self,
        heading: str,
        items: Sequence[tuple[str, str, str]],
        current: str | None = None,
        filterable: bool = True,
        placeholder: str = "输入关键字筛选",
    ) -> None:
        super().__init__()
        self.heading = heading
        self.items = list(items)
        self.current = current
        self.filterable = filterable
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(self.heading, id="picker-title")
            if self.filterable:
                yield Input(placeholder=self.placeholder, id="picker-input")
            yield OptionList(id="picker-list")

    def on_mount(self) -> None:
        self._refresh("")
        if self.filterable:
            self.query_one("#picker-input", Input).focus()
        else:
            self.query_one(OptionList).focus()

    def _refresh(self, needle: str) -> None:
        option_list = self.query_one(OptionList)
        needle = needle.strip().lower()
        options: list[Option] = []
        highlight = 0
        for item_id, markup, haystack in self.items:
            if needle and needle not in haystack.lower():
                continue
            mark = "» " if item_id == self.current else "  "
            if item_id == self.current:
                highlight = len(options)
            options.append(Option(f"{mark}{markup}", id=item_id))
        option_list.clear_options()
        option_list.add_options(options)
        if options:
            option_list.highlighted = highlight

    @on(Input.Changed, "#picker-input")
    def _filter(self, event: Input.Changed) -> None:
        self._refresh(event.value)

    @on(Input.Submitted, "#picker-input")
    def _submit(self, event: Input.Submitted) -> None:
        self.action_confirm()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_confirm(self) -> None:
        option = self.query_one(OptionList).highlighted_option
        if option is not None:
            self.dismiss(option.id)

    def action_move_pick(self, delta: int) -> None:
        option_list = self.query_one(OptionList)
        count = option_list.option_count
        if not count:
            return
        current = option_list.highlighted
        index = 0 if current is None else current
        option_list.highlighted = max(0, min(count - 1, index + delta))

    def action_cancel_pick(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- #
# help
# --------------------------------------------------------------------------- #

HELP_MD = """
# LinguaTUI

双栏终端翻译器。左栏写原文、右栏出译文；两栏可以互相翻译，也能按住 Ctrl 拖选一段单独查看译文。

## 三种翻译方式

**① 输入即译（默认开启）**

在左栏打中文，停顿约 1 秒，右栏自动出现英文。不用按任何键。

* 方向由 `Ctrl+R` 循环切换：`关 → 当前栏 → 另一栏 → 关`，状态栏会显示 `输入即译 左→右`；
* 只会把「被编辑的那一栏」翻到另一栏，所以改译文不会被反向覆盖；
* 正文超过 3000 字时会自动暂停（避免每敲一个字就发一次大请求），改用 `Ctrl+T`。

**② 对照选中 + Ctrl+拖选即译**

* **普通拖拽**：只选字、不弹窗；**松开后另一栏会高亮对应位置** —— 先对齐段落、再对齐句子、最后在句内定位到词组（`F3` 开关）；
* **Ctrl + 拖拽**：选中一段，**松手即弹出译文小窗**；
* **Ctrl + 单击**（不拖）：直接翻译光标所在段落；
* `a` 替换原文 / `i` 插入另一栏 / `c` 复制 / `r` 重译 / `Esc` 关闭；
* 方向永远指向另一栏的语言：右栏选英文，小窗里就是中文。

对齐是分层的：段落（空行）→ 句子（。！？；.!?）→ 句内按比例定位，再把首尾扩到完整单词和包含它的分句。
所以即使比例算差一两个词，高亮也一定覆盖到正确的分句（英文没逗号时就是整句），不会只点亮隔壁半句。
两栏段落数不一致时会退化成“按整篇相对位置找段”；改动任一栏文字或点击别处，高亮自动清除。

如果某个终端不上报鼠标事件的 Ctrl 修饰键，选中后按 `Ctrl+G`，效果一样。

**③ 光标段落即译（不用鼠标）**

把光标放在某一段文字里，直接按 `Ctrl+G` —— 不用选中任何东西，弹窗里就是光标所在段落的译文。
用 `Shift+方向键` 选中后按 `Ctrl+G` 则是选中的那一段。

## 快捷键

| 键 | 作用 |
| --- | --- |
| 直接打字 | 输入即译，另一栏自动出译文（`Ctrl+R` 开关） |
| 普通鼠标拖拽 | 只选字，并在另一栏高亮对应段落 |
| `Ctrl` + 鼠标拖拽 | 选中片段，松手即弹出译文窗口 |
| `Ctrl` + 单击 | 翻译光标所在段落 |
| `Ctrl+G` | 翻译光标所在段落 / 当前选中片段 |
| `Ctrl+R` | 输入即译：关 / 左→右 / 右→左 循环 |
| `Ctrl+T` | 把当前栏整篇翻译到另一栏（覆盖对方） |
| `Alt+T` | 整篇翻译并追加到另一栏末尾 |
| `Ctrl+L` | 交换左右两栏（语言 + 内容） |
| `Ctrl+O` | 修改当前栏的语言 |
| `Ctrl+P` | 切换翻译引擎 |
| `F2` | 开 / 关 `Ctrl+拖拽` 弹窗 |
| `F3` | 开 / 关“另一栏高亮对应段落” |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做本栏编辑 |
| `F1` | 本帮助 |
| `Ctrl+Q` | 退出 |

## 鼠标拖拽没反应？

Windows 旧控制台（conhost，标题栏是 `Administrator: C:\\...` 那种）默认开启「快速编辑模式」，
它会抢走鼠标拖拽、让程序收不到选择事件。任选一种解决：

1. 右键标题栏 → 属性 → 选项 → 取消「快速编辑模式」，重开程序；
2. 改用 **Windows Terminal**（默认就没这个问题，体验也更好）；
3. 完全不用鼠标：光标放到段落里按 `Ctrl+G`，或 `Shift+方向键` 选中后 `Ctrl+G`。

## 翻译引擎

| 引擎 | 需要 key | 说明 |
| --- | --- | --- |
| `google` | 否 | 免费网页接口，默认，够快够用 |
| `mymemory` | 否 | 免费但有每日额度，必须固定源语言 |
| `deepl` | 是 | 译文最自然，需 `LINGUA_DEEPL_KEY` |
| `openai` | 是 | 任意 OpenAI 兼容接口（OpenAI / DeepSeek / 本地 Ollama 等），`LINGUA_OPENAI_KEY` + `LINGUA_OPENAI_BASE` / `LINGUA_OPENAI_MODEL` |

## 配置文件

退出时自动保存语言、引擎、开关状态（默认不保存正文）：

```
{config_file}
```

把 `remember_text = true` 写进配置，就能在下次启动时恢复两栏文字。

## 小贴士

* 窗口太窄时两栏会自动上下排列；
* 译文弹窗里可以继续用普通鼠标拖选复制；
* 终端建议用 Windows Terminal / iTerm2 / kitty，字号大一点更舒服。
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape,f1,q", "close_help", "关闭", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static("[b]LinguaTUI 使用帮助[/b]   [dim]Esc 关闭[/dim]", id="help-title")
            with VerticalScroll(id="help-body"):
                yield Markdown(HELP_MD.replace("{config_file}", str(config.config_path())))

    def on_mount(self) -> None:
        self.query_one(VerticalScroll).focus()

    def action_close_help(self) -> None:
        self.dismiss(None)


# --------------------------------------------------------------------------- #
# the editor panes
# --------------------------------------------------------------------------- #


# 备注：普通拖选后的“对应段落”直接复用 TextArea 自己的选区高亮（另一栏平时没有选区，所以一眼就能看出来）。


class TranslatePane(TextArea):
    """TextArea that turns **Ctrl+拖拽 / Ctrl+单击** into a translation request.

    普通拖拽只是选择文字（可以安心复制、不会弹窗），按住 Ctrl 拖拽才会弹译文窗；
    Ctrl+单击（不拖）等价于“翻译光标所在段落”。
    """

    class Settled(Message):
        """Posted when a mouse selection in the pane settles (mouse button released)."""

        def __init__(self, pane: "TranslatePane", dragged: bool, ctrl: bool) -> None:
            super().__init__()
            self.pane = pane
            self.dragged = dragged
            self.ctrl = ctrl

        @property
        def control(self) -> "TranslatePane":
            return self.pane

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._ctrl_click = False

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button == 1:
            self._ctrl_click = bool(event.ctrl or event.meta)
        await super()._on_mouse_down(event)

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        await super()._on_mouse_up(event)
        ctrl, self._ctrl_click = self._ctrl_click, False
        dragged = bool(self.selected_text.strip())
        if not dragged and not ctrl:
            return  # 普通单击：不需要任何动作
        self.post_message(self.Settled(self, dragged, ctrl))


# --------------------------------------------------------------------------- #
# offset helpers (段落/句子/词的对齐)
# --------------------------------------------------------------------------- #

_SENTENCE_END = re.compile(r"[。！？；!?;]+[\"'”’）)\]]*\s*|\.(?=\s|$)\s*")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Sentence ranges inside a block, punctuation included."""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        end = match.end()
        if end > start:
            spans.append((start, end))
            start = end
    if start < len(text):
        spans.append((start, len(text)))
    trimmed: list[tuple[int, int]] = []
    for begin, end in spans:
        while end > begin and text[end - 1].isspace():
            end -= 1
        if end > begin:
            trimmed.append((begin, end))
    return trimmed


def _sentence_index(spans: list[tuple[int, int]], offset: int) -> int:
    for index, (begin, end) in enumerate(spans):
        if begin <= offset < end:
            return index
    if spans and offset >= spans[-1][1]:
        return len(spans) - 1
    return 0


def _word_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Grow a character range out to whole ASCII words so it is readable."""
    length = len(text)
    start = max(0, min(start, length))
    end = max(start, min(end, length))
    if end - start < 2:
        end = min(length, start + 2)
    while start > 0 and text[start - 1].isascii() and text[start - 1].isalnum():
        start -= 1
    while end < length and text[end].isascii() and text[end].isalnum():
        end += 1
    return start, end


def _map_offsets(src: str, tgt: str, start: int, end: int) -> tuple[int, int]:
    """Map a character range of ``src`` onto ``tgt``.

    两边句子数一致时先对到同一个句子，再在句内按比例定位；否则整段按比例。
    """
    if not tgt:
        return 0, 0
    start = max(0, min(start, len(src)))
    end = max(start, min(end, len(src)))
    src_sentences = _sentence_spans(src)
    tgt_sentences = _sentence_spans(tgt)

    if len(src_sentences) == len(tgt_sentences) > 1:
        first = _sentence_index(src_sentences, start)
        last = _sentence_index(src_sentences, max(start, end - 1))
        src_begin, src_end = src_sentences[first]
        tgt_begin, tgt_end = tgt_sentences[first]
        head = tgt_begin + int(
            (start - src_begin) / max(1, src_end - src_begin) * max(1, tgt_end - tgt_begin) + 0.5
        )
        if last > first:
            tail = tgt_sentences[last][1]
        else:
            tail_begin, tail_end = src_sentences[last]
            tgt_tail_begin, tgt_tail_end = tgt_sentences[last]
            tail = tgt_tail_begin + int(
                (min(end, tail_end) - tail_begin)
                / max(1, tail_end - tail_begin)
                * max(1, tgt_tail_end - tgt_tail_begin)
                + 0.5
            )
        return head, max(tail, head + 1)

    ratio_start = start / max(1, len(src))
    ratio_end = max(ratio_start + 0.02, end / max(1, len(src)))
    return int(ratio_start * len(tgt) + 0.5), int(ratio_end * len(tgt) + 0.5)


_CLAUSE_STOP = "，,；;、。.!?！？\n"


def _dilate_to_clause(text: str, start: int, end: int) -> tuple[int, int]:
    """把区间扩到「包含它的分句」边界。

    纯按比例算出来的位置经常差一两个词（中英密度不同），扩到分句之后
    就不会出现“只点亮隔壁半句”的错觉；英文没有逗号时自然退化成整句。
    """
    left = max(0, min(start, len(text)))
    while left > 0 and text[left - 1] not in _CLAUSE_STOP:
        left -= 1
    right = max(left, min(end, len(text)))
    while right < len(text) and text[right] not in _CLAUSE_STOP:
        right += 1
    if right < len(text) and text[right] in "，,；;、":
        right += 1
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    return left, right


def _flat_offset(lines: list[str], first_row: int, location: tuple[int, int]) -> int:
    """Character offset of ``location`` relative to ``lines[first_row]``."""
    row, column = location
    offset = 0
    for index in range(first_row, min(row, len(lines))):
        offset += len(lines[index]) + 1
    return offset + column


def _loc_from_offset(
    lines: list[str], first_row: int, last_row: int, offset: int
) -> tuple[int, int]:
    offset = max(0, offset)
    for row in range(first_row, last_row + 1):
        length = len(lines[row])
        if offset <= length:
            return (row, offset)
        offset -= length + 1
    return (last_row, len(lines[last_row]))


# --------------------------------------------------------------------------- #
# the app
# --------------------------------------------------------------------------- #


class Linguatui(App[None]):
    CSS_PATH = "linguatui.tcss"
    TITLE = "LinguaTUI"
    SUB_TITLE = "输入即译 · Ctrl+拖选即译"
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+t", "translate_document", "全文译", priority=True),
        Binding("alt+t", "translate_document_append", "追加译", show=False, priority=True),
        Binding("ctrl+g", "translate_selection", "选中译", priority=True),
        Binding("ctrl+r", "toggle_live", "输入即译", priority=True),
        Binding("ctrl+l", "swap_sides", "交换", priority=True),
        Binding("ctrl+o", "pick_language", "语言", priority=True),
        Binding("ctrl+p", "pick_provider", "引擎", priority=True),
        Binding("f2", "toggle_auto_popup", "自动弹窗", show=False),
        Binding("f3", "toggle_sync", "对照选中", show=False),
        Binding("f1", "help", "帮助", priority=True),
        Binding("ctrl+q", "quit", "退出", priority=True),
    ]

    def __init__(self, cfg: config.Config | None = None) -> None:
        super().__init__()
        self.cfg = cfg or config.load()
        self.sides: dict[str, str] = {
            "left": langs.normalize(self.cfg.left_lang),
            "right": langs.normalize(self.cfg.right_lang),
        }
        self.provider = self.cfg.provider if self.cfg.provider in backends.BACKENDS else "google"
        self.auto_popup = self.cfg.auto_popup
        self.live_mode = self.cfg.live if self.cfg.live in {"off", *SIDES} else "left"
        self._client: httpx.AsyncClient | None = None
        self._live_timer = None
        self._popup_pane: str = "left"
        self._popup_span: tuple | None = None
        self._busy = 0
        self._width = 0
        self._texts: dict[str, str] = {"left": "", "right": ""}
        self._programmatic: dict[str, str] = {}
        self._live_last: dict[str, tuple[str, str]] = {}
        self.sync_selection = self.cfg.sync_selection
        self._mirror_side: str | None = None
        self._mirror_exact = True
        self._mirror_timer = None

    # -- construction ------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="panes"):
            for side in SIDES:
                with Vertical(classes="pane", id=f"pane-{side}"):
                    yield Static("", id=f"title-{side}", classes="pane-title")
                    yield TranslatePane(
                        id=f"ta-{side}",
                        soft_wrap=True,
                        show_line_numbers=False,
                        placeholder=(
                            "在这里输入或粘贴文字，停 1 秒另一栏自动出译文…"
                            if side == "left"
                            else "Type or paste here — the other pane follows automatically…"
                        ),
                    )
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._client = httpx.AsyncClient(timeout=self.cfg.timeout, follow_redirects=True)
        left, right = self.ta("left"), self.ta("right")
        if self.cfg.remember_text:
            left.load_text(self.cfg.left_text)
            right.load_text(self.cfg.right_text)
        self._texts = {"left": left.text, "right": right.text}
        self._sync_titles()
        self._sync_status()
        left.focus()
        if not (left.text or right.text):
            self.notify(
                "左栏打字，右栏会自动出译文；拖选任意片段或用 Ctrl+G 可单独翻译一段。F1 查看全部快捷键。",
                title="开始使用",
                timeout=9,
            )
        if self.problem_with_current_provider():
            self.notify(
                self.problem_with_current_provider() or "",
                title="当前引擎不可用",
                severity="warning",
                timeout=10,
            )
        hint = self._legacy_console_hint()
        if hint:
            self.notify(hint, title="鼠标提示", severity="warning", timeout=14)

    @staticmethod
    def _legacy_console_hint() -> str | None:
        """Windows 旧控制台（conhost）的「快速编辑」会抢走鼠标拖拽。"""
        if os.name != "nt":
            return None
        if (
            os.environ.get("WT_SESSION")
            or os.environ.get("TERM_PROGRAM")
            or os.environ.get("ConEmuANSI")
        ):
            return None
        return (
            "看起来是 Windows 旧控制台：鼠标拖选会被「快速编辑模式」吃掉。"
            "可先按 Ctrl+G 翻译光标所在段落，或在标题栏右键→属性→选项里取消快速编辑。"
        )

    async def on_unmount(self) -> None:
        self._save_config()
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    # -- helpers -----------------------------------------------------------
    def ta(self, side: str) -> TextArea:
        return self.query_one(f"#ta-{side}", TextArea)

    @staticmethod
    def _other(side: str) -> str:
        return "right" if side == "left" else "left"

    @staticmethod
    def _side_of(pane: TextArea) -> str:
        return "left" if (pane.id or "").endswith("left") else "right"

    def _focused_side(self) -> str:
        focused = self.focused
        if isinstance(focused, TextArea):
            return self._side_of(focused)
        return "left"

    def _focused_text_area(self) -> TextArea:
        focused = self.focused
        if isinstance(focused, TextArea):
            return focused
        return self.ta("left")

    def target_lang(self, side: str) -> str:
        """Language of the *other* pane; never ``auto``."""
        target = self.sides[self._other(side)]
        if target == "auto":
            source = self.sides[side]
            return "zh-CN" if source == "en" else "en"
        return target

    def problem_with_current_provider(self) -> str | None:
        backend = backends.get_backend(self.provider, self.cfg)
        return backend.missing_reason(self.cfg)

    def _modal_open(self) -> bool:
        """True while a popup / picker / help screen is on top."""
        return isinstance(self.screen, ModalScreen)

    def _save_config(self) -> None:
        self.cfg.provider = self.provider
        self.cfg.left_lang = self.sides["left"]
        self.cfg.right_lang = self.sides["right"]
        self.cfg.auto_popup = self.auto_popup
        self.cfg.sync_selection = self.sync_selection
        self.cfg.live = self.live_mode
        if self.cfg.remember_text:
            self.cfg.left_text = self._texts["left"]
            self.cfg.right_text = self._texts["right"]
        try:
            config.save(self.cfg)
        except OSError:
            pass

    # -- chrome ------------------------------------------------------------
    def _sync_titles(self) -> None:
        for side in SIDES:
            text = self._texts[side]
            target = langs.short(self.target_lang(side))
            if self.sides[side] == "auto":
                state = f"自动识别 → {target}"
            else:
                state = f"译入 {target}"
            mark = "[green]●[/green] " if self.live_mode == side else "  "
            self.query_one(f"#title-{side}", Static).update(
                f"{mark}[b]{SIDE_CN[side]}[/b] [cyan]{escape(langs.label(self.sides[side]))}[/cyan]"
                f"  [dim]{len(text)} 字 · {escape(state)}[/dim]"
            )

    def _sync_status(self) -> None:
        provider = self.provider
        if self.problem_with_current_provider():
            provider += " [yellow]![/yellow]"
        focused = self.focused
        selected = len(focused.selected_text.strip()) if isinstance(focused, TextArea) else 0
        bits = [
            f"引擎 [b]{provider}[/b]",
            f"[cyan]{escape(langs.label(self.sides['left']))}[/cyan]"
            f" ⇄ [cyan]{escape(langs.label(self.sides['right']))}[/cyan]",
            "输入即译 "
            + (
                "[dim]关[/dim]"
                if self.live_mode == "off"
                else f"[green]{'左' if self.live_mode == 'left' else '右'}→"
                f"{'右' if self.live_mode == 'left' else '左'}[/green]"
            ),
        ]
        if selected:
            bits.append(f"已选 {selected} 字 · Ctrl+G 译")
        elif self._width >= 108:
            bits.append("[dim]Ctrl+拖选译 · Ctrl+T 全篇[/dim]")
        if self._mirror_side:
            bits.append("[dim]对照→{}{}[/dim]".format(
                "左" if self._mirror_side == "left" else "右",
                "" if self._mirror_exact else "≈",
            ))
        if not self.auto_popup:
            bits.append("[dim]Ctrl+拖选 关[/dim]")
        if self._busy:
            bits.append("[yellow]翻译中…[/yellow]")
        self.query_one("#status", Static).update("  ·  ".join(bits))

    def _set_busy(self, delta: int) -> None:
        self._busy = max(0, self._busy + delta)
        self._sync_status()

    # -- mirror selection (对应段落高亮) ------------------------------------
    @staticmethod
    def _blocks(text: str) -> list[tuple[int, int]]:
        """Paragraph blocks as ``(first_row, last_row)``, split on blank lines."""
        lines = text.split("\n")
        blocks: list[tuple[int, int]] = []
        start: int | None = None
        for index, line in enumerate(lines):
            if line.strip():
                if start is None:
                    start = index
            elif start is not None:
                blocks.append((start, index - 1))
                start = None
        if start is not None:
            blocks.append((start, len(lines) - 1))
        return blocks

    def mirror_selection(self, pane: TextArea) -> None:
        """Highlight the part of the other pane that matches the selection.

        段落 → 句子 → 词，逐层细化；两栏段落数不一致时也会先猜出对应段落，
        再在该段内部做细粒度定位（而不是只点亮整段）。
        """
        if not self.sync_selection:
            return
        side = self._side_of(pane)
        other = self.ta(self._other(side))
        source_text = pane.text
        target_text = other.text
        source_blocks = self._blocks(source_text)
        target_blocks = self._blocks(target_text)
        if not source_blocks or not target_blocks:
            self._clear_mirror()
            return

        start_loc, end_loc = pane.selection
        first = self._block_index(source_blocks, min(start_loc[0], end_loc[0]))
        last = self._block_index(source_blocks, max(start_loc[0], end_loc[0]))
        first = first if first is not None else last
        last = last if last is not None else first
        if first is None or last is None:
            self._clear_mirror()
            return

        source_lines = source_text.split("\n")
        target_lines = target_text.split("\n")
        exact = len(source_blocks) == len(target_blocks)
        if exact:
            target_first, target_last = first, last
        else:
            # 段落数对不上：按“字符累计位置”猜对应段（比按条数比例稳），逐个端点都猜
            target_first = self._guess_block(
                source_blocks, source_lines, target_blocks, target_lines, first
            )
            target_last = (
                target_first
                if last == first
                else max(
                    target_first,
                    self._guess_block(
                        source_blocks, source_lines, target_blocks, target_lines, last
                    ),
                )
            )

        if first != last:  # 跨段：整段区间对整段区间
            begin = target_blocks[target_first][0]
            finish = target_blocks[target_last][1]
            self._set_mirror(
                other, Selection((begin, 0), (finish, len(target_lines[finish]))), exact
            )
            return

        source_first, source_last = source_blocks[first]
        target_first, target_last = target_blocks[target_first]
        whole = Selection(
            (target_first, 0), (target_last, len(target_lines[target_last]))
        )
        if tuple(start_loc) == tuple(end_loc):  # 光标/段落模式：整段
            self._set_mirror(other, whole, exact)
            return

        source_block = "\n".join(source_lines[source_first : source_last + 1])
        target_block = "\n".join(target_lines[target_first : target_last + 1])
        offset_start = _flat_offset(source_lines, source_first, start_loc)
        offset_end = _flat_offset(source_lines, source_first, end_loc)
        offset_start, offset_end = sorted((offset_start, offset_end))
        if offset_end - offset_start >= 0.8 * max(1, len(source_block)):
            self._set_mirror(other, whole, exact)  # 基本选中了整段
            return
        target_start, target_end = _map_offsets(
            source_block, target_block, offset_start, offset_end
        )
        target_start, target_end = _dilate_to_clause(target_block, target_start, target_end)
        target_start, target_end = _word_span(target_block, target_start, target_end)
        self._set_mirror(
            other,
            Selection(
                _loc_from_offset(target_lines, target_first, target_last, target_start),
                _loc_from_offset(target_lines, target_first, target_last, target_end),
            ),
            exact,
        )

    @staticmethod
    def _block_index(blocks: list[tuple[int, int]], row: int) -> int | None:
        for index, (begin, finish) in enumerate(blocks):
            if begin <= row <= finish:
                return index
        return None

    @staticmethod
    def _guess_block(
        source_blocks: list[tuple[int, int]],
        source_lines: list[str],
        target_blocks: list[tuple[int, int]],
        target_lines: list[str],
        index: int,
    ) -> int:
        """Find the target block sitting at the same relative position in the document."""
        source_total = max(1, sum(len(line) + 1 for line in source_lines))
        target_total = max(1, sum(len(line) + 1 for line in target_lines))
        wanted = _flat_offset(source_lines, 0, (source_blocks[index][0], 0)) / source_total
        best_index, best_delta = 0, None
        for candidate, (begin, _finish) in enumerate(target_blocks):
            position = _flat_offset(target_lines, 0, (begin, 0)) / target_total
            delta = abs(position - wanted)
            if best_delta is None or delta < best_delta:
                best_index, best_delta = candidate, delta
        return best_index

    def _set_mirror(self, target: TextArea, span: Selection, exact: bool = True) -> None:
        self._mirror_side = self._side_of(target)
        self._mirror_exact = exact
        target.selection = span
        self._sync_status()

    def _clear_mirror(self) -> None:
        if self._mirror_timer is not None:
            self._mirror_timer.stop()
            self._mirror_timer = None
        if self._mirror_side is None:
            return
        side, self._mirror_side = self._mirror_side, None
        self._mirror_exact = True
        try:
            pane = self.ta(side)
            if tuple(pane.selection.start) != tuple(pane.selection.end):
                pane.selection = Selection.cursor(pane.selection.end)
        except Exception:
            pass
        self._sync_status()

    # -- events ------------------------------------------------------------
    def on_resize(self, event) -> None:
        """Stack the two panes when the terminal is too narrow for two columns."""
        panes = self.query_one("#panes", Horizontal)
        panes.set_class(event.size.width < 88, "-narrow")
        self._width = event.size.width
        try:
            self._sync_status()
        except Exception:
            pass

    @on(TextArea.Changed)
    def _on_text_changed(self, event: TextArea.Changed) -> None:
        side = self._side_of(event.text_area)
        text = event.text_area.text
        self._texts[side] = text
        self._sync_titles()
        self._clear_mirror()
        if self._programmatic.get(side) == text:
            return  # 这行文字是程序写入的译文，不要回译
        if self.live_mode != side or isinstance(self.screen, ModalScreen):
            return
        if not text.strip():
            return
        if self._live_timer is not None:
            self._live_timer.stop()
        self._live_timer = self.set_timer(LIVE_DELAY, lambda: self._live_fire(side))

    # -- live translation (type -> other pane) -----------------------------
    def _live_fire(self, side: str) -> None:
        self._live_timer = None
        if self.live_mode != side or isinstance(self.screen, ModalScreen):
            return
        text = self.ta(side).text.strip()
        if not text:
            return
        if len(text) > LIVE_MAX:
            self.notify(
                f"正文超过 {LIVE_MAX} 字，输入即译暂停；按 Ctrl+T 手动翻译全篇",
                title="输入即译",
                severity="warning",
                timeout=5,
            )
            return
        source = self.sides[side]
        target = self.target_lang(side)
        if source != "auto" and source == target:
            source = "auto"
        if self._live_last.get(side) == (text, target):
            return
        self._live_worker(side, text, source, target)

    @work(exclusive=True, group="live", exit_on_error=False)
    async def _live_worker(self, side: str, text: str, source: str, target: str) -> None:
        client = self._client
        if client is None:
            return
        backend = backends.get_backend(self.provider, self.cfg)
        if backend.missing_reason(self.cfg):
            return  # 启动时已经提示过了
        self._set_busy(1)
        try:
            result = await backends.translate(backend, client, text, source, target)
        except asyncio.CancelledError:
            raise
        except backends.TranslateError as exc:
            self.notify(f"输入即译失败：{exc}", severity="error", timeout=6)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"输入即译失败：{type(exc).__name__}: {exc}", severity="error", timeout=6)
        else:
            # 丢弃过期结果：方向已切走，或源栏内容又变了
            if self.live_mode != side or self.ta(side).text.strip() != text:
                return
            other_side = self._other(side)
            other = self.ta(other_side)
            payload = result.text
            self._programmatic[other_side] = payload
            if payload.strip() != other.text.strip():
                other.load_text(payload)
                other.scroll_home(animate=False)
            self._texts[other_side] = payload
            self._live_last[side] = (text, target)
            self._sync_titles()
        finally:
            self._set_busy(-1)

    @on(TextArea.SelectionChanged)
    def _on_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._sync_status()
        pane = event.text_area
        if isinstance(self.screen, ModalScreen):
            return
        if self._side_of(pane) == self._mirror_side:
            return  # 这是我们自己写进去的对照选区，不要再反射回去
        if not pane.selected_text.strip():
            return
        # 键盘选中（Shift+方向键）同样给出对照，防抖一点避免逐字触发
        if self._mirror_timer is not None:
            self._mirror_timer.stop()
        self._mirror_timer = self.set_timer(0.35, lambda: self._mirror_if_selected(pane))

    def _mirror_if_selected(self, pane: TextArea) -> None:
        self._mirror_timer = None
        if pane.selected_text.strip():
            self.mirror_selection(pane)

    @on(TranslatePane.Settled)
    def _on_selection_settled(self, event: TranslatePane.Settled) -> None:
        """鼠标拖选结束：高亮另一栏对应段落；按住 Ctrl 时再弹译文窗。"""
        if event.dragged:
            self.mirror_selection(event.pane)
        if not event.ctrl:
            return
        if not self.auto_popup or isinstance(self.screen, ModalScreen):
            return
        self.open_selection_popup(event.pane, auto=True)

    @on(events.MouseDown)
    def _on_mouse_down(self, event: events.MouseDown) -> None:
        self._clear_mirror()

    # -- selection popup ---------------------------------------------------
    def _paragraph_at_cursor(self, pane: TextArea):
        """The blank-line delimited block around the cursor, plus its span."""
        lines = pane.text.split("\n")
        if not lines:
            return "", None
        row = min(max(pane.cursor_location[0], 0), len(lines) - 1)

        def blank(index: int) -> bool:
            return not lines[index].strip()

        if blank(row):
            near = [i for i in range(row, -1, -1) if not blank(i)]
            near += [i for i in range(row, len(lines)) if not blank(i)]
            if not near:
                return "", None
            row = min(near, key=lambda i: abs(i - row))
        start = end = row
        while start > 0 and not blank(start - 1):
            start -= 1
        while end < len(lines) - 1 and not blank(end + 1):
            end += 1
        block = "\n".join(lines[start : end + 1])
        if not block.strip():
            return "", None
        return block.strip(), ((start, 0), (end, len(lines[end])))

    def open_selection_popup(self, pane: TextArea, auto: bool = False) -> None:
        selected = pane.selected_text.strip()
        text = selected
        mode = "selection"
        span = pane.selection if selected else None
        if not text:
            text, span = self._paragraph_at_cursor(pane)
            mode = "paragraph"
        if not text:
            self.notify(
                "先用鼠标拖选、或 Shift+方向键选中一段，也可以把光标放到一段文字里再按 Ctrl+G",
                severity="warning",
                timeout=4,
            )
            return
        side = self._side_of(pane)
        source = self.sides[side]
        target = self.target_lang(side)
        if source != "auto" and source == target:
            source = "auto"
        self._popup_pane = side
        self._popup_span = span
        screen = SelectionScreen(
            original=text,
            source=source,
            target=target,
            provider=self.provider,
            provider_label=backends.label_of(self.provider),
            auto=auto,
            side=side,
            mode=mode,
        )
        self.push_screen(screen, self._selection_closed)
        self.request_translation(screen)

    def request_translation(self, screen: SelectionScreen) -> None:
        self._selection_worker(screen.original, screen.source, screen.target, screen)

    @work(exclusive=True, group="selection", exit_on_error=False)
    async def _selection_worker(
        self, text: str, source: str, target: str, screen: SelectionScreen
    ) -> None:
        client = self._client
        if client is None:
            screen.finish(error="网络客户端未就绪")
            return
        backend = backends.get_backend(self.provider, self.cfg)
        reason = backend.missing_reason(self.cfg)
        if reason:
            screen.finish(error=reason)
            return
        try:
            result = await backends.translate(backend, client, text, source, target)
        except asyncio.CancelledError:
            raise
        except backends.TranslateError as exc:
            screen.finish(error=str(exc))
        except Exception as exc:  # noqa: BLE001 - always surface failures
            screen.finish(error=f"{type(exc).__name__}: {exc}")
        else:
            screen.finish(result=result)

    def _selection_closed(self, result: tuple[str, str] | None) -> None:
        pane = self.ta(self._popup_pane)
        if not result:
            return
        action, text = result
        other_side = self._other(self._popup_pane)
        other = self.ta(other_side)
        if action == "replace":
            edit = None
            span = self._popup_span
            if span and tuple(span[0]) != tuple(span[1]):
                try:
                    edit = pane.replace(text, span[0], span[1])
                except Exception:
                    edit = None
            if edit is None:
                edit = pane.insert(text)
            self._programmatic[self._popup_pane] = pane.text
            pane.move_cursor(edit.end_location)
            pane.focus()
            self.notify("已用译文替换原文", timeout=2)
        elif action == "insert":
            edit = other.insert(text)
            self._programmatic[other_side] = other.text
            other.move_cursor(edit.end_location)
            self.notify(f"已插入到{SIDE_CN[other_side]}", timeout=2)
        self._popup_span = None

    # -- whole-document translation ---------------------------------------
    def action_translate_document(self) -> None:
        if not self._modal_open():
            self._translate_document(append=False)

    def action_translate_document_append(self) -> None:
        if not self._modal_open():
            self._translate_document(append=True)

    def _translate_document(self, append: bool) -> None:
        pane = self._focused_text_area()
        side = self._side_of(pane)
        text = pane.text.strip()
        if not text:
            self.notify(f"{SIDE_CN[side]}还是空的", severity="warning", timeout=3)
            return
        source = self.sides[side]
        target = self.target_lang(side)
        if source != "auto" and source == target:
            source = "auto"
        self._document_worker(text, source, target, side, append)

    @work(exclusive=True, group="document", exit_on_error=False)
    async def _document_worker(
        self, text: str, source: str, target: str, side: str, append: bool
    ) -> None:
        client = self._client
        if client is None:
            return
        backend = backends.get_backend(self.provider, self.cfg)
        reason = backend.missing_reason(self.cfg)
        if reason:
            self.notify(reason, title="当前引擎不可用", severity="warning", timeout=10)
            return
        self._set_busy(1)
        try:
            result = await backends.translate(backend, client, text, source, target)
        except asyncio.CancelledError:
            raise
        except backends.TranslateError as exc:
            self.notify(str(exc), title="翻译失败", severity="error", timeout=10)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"{type(exc).__name__}: {exc}", title="翻译失败", severity="error", timeout=10)
        else:
            other = self.ta(self._other(side))
            payload = result.text
            if append and other.text.strip():
                payload = other.text.rstrip("\n") + "\n\n" + payload
            self._programmatic[self._other(side)] = payload
            other.load_text(payload)
            other.scroll_home(animate=False)
            self._texts[self._other(side)] = payload
            self._sync_titles()
            note = "（来自缓存）" if result.cached else ""
            self.notify(
                f"{len(text)} 字 → {len(result.text)} 字 {langs.label(target)}{note}",
                title="全文翻译完成",
                timeout=4,
            )
        finally:
            self._set_busy(-1)

    # -- commands ----------------------------------------------------------
    def action_translate_selection(self) -> None:
        if not self._modal_open():
            pane = self._focused_text_area()
            self.mirror_selection(pane)
            self.open_selection_popup(pane)

    def action_toggle_sync(self) -> None:
        if self._modal_open():
            return
        self.sync_selection = not self.sync_selection
        if not self.sync_selection:
            self._clear_mirror()
        self._save_config()
        self.notify(
            f"对照选中：{'开（拖选后在另一栏高亮对应段落）' if self.sync_selection else '关'}",
            timeout=3,
        )

    def action_swap_sides(self) -> None:
        if self._modal_open():
            return
        left, right = self.ta("left"), self.ta("right")
        left_text, right_text = left.text, right.text
        self.sides["left"], self.sides["right"] = self.sides["right"], self.sides["left"]
        self._programmatic["left"], self._programmatic["right"] = right_text, left_text
        self._live_last.clear()
        left.load_text(right_text)
        right.load_text(left_text)
        self._texts = {"left": right_text, "right": left_text}
        self._sync_titles()
        self._sync_status()
        self.notify("已交换左右两栏", timeout=2)

    def action_toggle_auto_popup(self) -> None:
        if self._modal_open():
            return
        self.auto_popup = not self.auto_popup
        self._sync_status()
        self.notify(
            f"Ctrl+拖选即译：{'开' if self.auto_popup else '关（普通拖拽只是选字）'}",
            timeout=3,
        )

    def action_toggle_live(self) -> None:
        """Cycle 关 → 当前栏 → 另一栏 → 关，决定“输入即译”的方向。"""
        if self._modal_open():
            return
        focused = self._focused_side()
        if self.live_mode == "off":
            self.live_mode = focused
        elif self.live_mode == focused:
            self.live_mode = self._other(focused)
        else:
            self.live_mode = "off"
        self._save_config()
        self._sync_status()
        self._sync_titles()
        if self.live_mode == "off":
            self.notify("输入即译已关闭（Ctrl+R 再打开）", timeout=3)
        else:
            self.notify(
                f"输入即译：{SIDE_CN[self.live_mode]} → {SIDE_CN[self._other(self.live_mode)]}，"
                "打字停顿 1 秒后自动把译文写进另一栏",
                timeout=5,
            )
            self._live_fire(self.live_mode)

    def action_pick_language(self) -> None:
        if self._modal_open():
            return
        side = self._focused_side()
        items = []
        for lang in langs.all_choices():
            hint = "  [dim](仅作源语言)[/dim]" if lang.code == "auto" else ""
            items.append(
                (
                    lang.code,
                    f"{escape(lang.label)}  [dim]{escape(lang.code)}[/dim]{hint}",
                    f"{lang.label} {lang.english} {lang.code}",
                )
            )
        self.push_screen(
            PickerScreen(
                f"[b]{SIDE_CN[side]}语言[/b]  [dim]↑↓ 选择 · Enter 确认 · Esc 取消[/dim]",
                items,
                current=self.sides[side],
                placeholder="筛选语言：中文 / English / ja / ko …",
            ),
            lambda code: self._language_picked(side, code),
        )

    def _language_picked(self, side: str, code: str | None) -> None:
        if not code:
            return
        previous = self.sides[side]
        self.sides[side] = code
        if code == self.sides[self._other(side)]:
            self.notify(
                f"两栏现在是同一种语言（{langs.label(code)}），把另一栏换掉才能互译",
                severity="warning",
                timeout=6,
            )
        elif code == "auto" and self.sides[self._other(side)] == "auto":
            self.notify("两栏都设成了自动检测，目标语言会退回到 English", severity="warning", timeout=6)
        if previous != code:
            self._sync_titles()
            self._sync_status()
            if self.live_mode != "off":
                self._live_last.pop(self.live_mode, None)
                self._live_fire(self.live_mode)

    def action_pick_provider(self) -> None:
        if self._modal_open():
            return
        items = []
        for key in backends.ORDER:
            backend = backends.get_backend(key, self.cfg)
            problem = backend.missing_reason(self.cfg)
            suffix = "" if not problem else "  [yellow]需要 key[/yellow]"
            items.append((key, f"{escape(key)}  [dim]{escape(backends.label_of(key))}[/dim]{suffix}", f"{key} {backends.label_of(key)}"))
        self.push_screen(
            PickerScreen(
                "[b]翻译引擎[/b]  [dim]↑↓ 选择 · Enter 确认 · Esc 取消[/dim]",
                items,
                current=self.provider,
                filterable=False,
            ),
            self._provider_picked,
        )

    def _provider_picked(self, key: str | None) -> None:
        if not key:
            return
        self.provider = key
        backend = backends.get_backend(key, self.cfg)
        problem = backend.missing_reason(self.cfg)
        self._sync_status()
        self._save_config()
        if problem:
            self.notify(problem, title="引擎不可用", severity="warning", timeout=10)
        else:
            self.notify(f"引擎切换为 {backends.label_of(key)}", timeout=2)

    def action_help(self) -> None:
        if self._modal_open():
            return
        self.push_screen(HelpScreen())


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linguatui",
        description="双栏、拖动选中即译的终端翻译器",
    )
    parser.add_argument("--provider", choices=backends.ORDER, help="翻译引擎（默认读配置）")
    parser.add_argument("--left-lang", help="左栏语言代码，例如 zh-CN / en / ja")
    parser.add_argument("--right-lang", help="右栏语言代码")
    parser.add_argument("--no-auto-popup", action="store_true", help="关闭拖选后自动弹窗")
    parser.add_argument("--reset-config", action="store_true", help="删除配置后启动")
    parser.add_argument("--check", metavar="TEXT", help="命令行翻译一段文字然后退出（自检用）")
    parser.add_argument("--from", dest="source", default="auto", help="--check 的源语言")
    parser.add_argument("--to", dest="target", default="en", help="--check 的目标语言")
    parser.add_argument("-v", "--verbose", action="store_true", help="--check 时打印引擎与识别结果")
    parser.add_argument("--version", action="version", version=f"linguatui {__version__}")
    return parser


async def _check_async(cfg: config.Config, text: str, source: str, target: str, verbose: bool = False) -> int:
    backend = backends.get_backend(cfg.provider, cfg)
    reason = backend.missing_reason(cfg)
    if reason:
        print(f"[{cfg.provider}] {reason}", file=sys.stderr)
        return 2
    async with httpx.AsyncClient(timeout=max(cfg.timeout, 30), follow_redirects=True) as client:
        try:
            result = await backends.translate(backend, client, text, source, target)
        except Exception as exc:  # noqa: BLE001
            print(f"[{cfg.provider}] 失败：{exc}", file=sys.stderr)
            return 1
    print(result.text)
    if verbose:
        print(f"# provider={result.provider} source={result.source}", file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    args = build_parser().parse_args(argv)
    if args.reset_config:
        config.reset()
    cfg = config.load()
    if args.provider:
        cfg.provider = args.provider
    if args.left_lang:
        cfg.left_lang = langs.normalize(args.left_lang)
    if args.right_lang:
        cfg.right_lang = langs.normalize(args.right_lang)
    if args.no_auto_popup:
        cfg.auto_popup = False

    if args.check:
        return asyncio.run(_check_async(cfg, args.check, args.source, args.target, args.verbose))

    Linguatui(cfg).run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
