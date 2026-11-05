"""可选的词对齐：用多语言 BERT 的上下文向量做 IterMax 对齐（SimAlign 风格）。

只在配置里 `align = "model"` 时启用。没装 torch/transformers 会自动回退到
启发式对齐，不影响主流程。

安装（CPU 版 torch 体积小得多）：

    pip install -e ".[align]"
    # 或者手动：
    pip install torch --index-url https://download.pytorch.org/whl/cpu
    pip install transformers
"""

from __future__ import annotations

import threading

DEFAULT_MODEL = "bert-base-multilingual-cased"
"""多语言 BERT：中英之间共享子词表，对齐效果比单语模型好很多。"""

SIMILARITY_FLOOR = 0.45
"""低于这个余弦相似度的 token 对直接丢弃，避免硬凑。"""


def missing_dependencies() -> str | None:
    """返回缺依赖的提示（None 表示可以跑）。"""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception:
        return (
            "词对齐需要 torch + transformers：pip install -e \".[align]\"；"
            "Windows 建议先装 CPU 版 torch："
            "pip install torch --index-url https://download.pytorch.org/whl/cpu"
        )
    return None


def span_from_pairs(
    pairs: list[tuple[tuple[int, int], tuple[int, int]]],
    start: int,
    end: int,
) -> tuple[int, int] | None:
    """把源栏的字符区间，映射成目标栏的字符区间。"""
    hits = [(t_start, t_end) for (s_start, s_end), (t_start, t_end) in pairs if s_end > start and s_start < end]
    if not hits:
        return None
    return min(h[0] for h in hits), max(h[1] for h in hits)


def expand_to_word(text: str, start: int, end: int) -> tuple[int, int]:
    """把子词边界扩到完整单词（weight -> weights）。"""
    while start > 0 and text[start - 1].isascii() and text[start - 1].isalnum():
        start -= 1
    while end < len(text) and text[end].isascii() and text[end].isalnum():
        end += 1
    return start, end


class WordAligner:
    """懒加载的多语言 BERT 词对齐器（线程安全，可重复调用）。"""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._ff = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            import torch
            from transformers import AutoModel, AutoTokenizer

            try:
                tokenizer = AutoTokenizer.from_pretrained(self.model_name, use_fast=True)
            except Exception:
                tokenizer = AutoTokenizer.from_pretrained(self.model_name, use_fast=False)
            model = AutoModel.from_pretrained(self.model_name, output_hidden_states=True)
            model.eval()
            self._tokenizer = tokenizer
            self._model = model
            self._torch = torch
            self._ff = torch.nn.functional

    def _encode(self, text: str) -> tuple[object, list[tuple[int, int]]]:
        tokenizer, torch = self._tokenizer, self._torch
        batch = tokenizer(
            text,
            return_tensors="pt",
            return_offsets_mapping=True,
            truncation=True,
            max_length=384,
        )
        offsets = [tuple(int(v) for v in pair) for pair in batch.pop("offset_mapping")[0].tolist()]
        with torch.no_grad():
            output = self._model(**batch)
        layers = output.hidden_states[-4:]
        hidden = torch.stack(layers).mean(dim=0)[0]
        hidden = self._ff.normalize(hidden, dim=-1)
        keep = [i for i, (start, end) in enumerate(offsets) if end > start]
        return hidden[keep], [offsets[i] for i in keep]

    def pairs(self, source: str, target: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
        """返回 ``[(源字符区间, 目标字符区间), ...]``，一一对应、单调性不保证但通常靠谱。"""
        self.load()
        source_vector, source_spans = self._encode(source)
        target_vector, target_spans = self._encode(target)
        if not source_spans or not target_spans:
            return []

        scores = source_vector @ target_vector.T
        result: list[tuple[tuple[int, int], tuple[int, int]]] = []
        rows = scores.size(0)
        columns = scores.size(1)
        for _ in range(min(rows, columns)):
            flat = int(scores.argmax())
            row, column = divmod(flat, columns)
            score = float(scores[row, column])
            if score < SIMILARITY_FLOOR:
                break
            result.append((source_spans[row], target_spans[column]))
            scores[row, :] = -1.0
            scores[:, column] = -1.0
        return result
