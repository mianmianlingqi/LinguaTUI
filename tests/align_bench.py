"""对照高亮的质量基准：给几组真实的中英句子，看选中中文时高亮的英文是否命中。

    python tests/align_bench.py            # 启发式（无需依赖）
    python tests/align_bench.py --model    # 词对齐模型（首次会下载 ~700MB）

改动对齐算法后跑一下，防止越改越差。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from linguatui.aligner import expand_to_word, span_from_pairs  # noqa: E402
from linguatui.app import (  # noqa: E402
    _dilate_to_clause,
    _map_offsets,
    _word_span,
)

CASES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "该方法也不同于 ROME 等模型编辑方法，后者使用固定算法进行狭窄的更改，例如更新单个事实。",
        "The approach also differs from model editing methods such as ROME, which use a fixed "
        "algorithm to make narrow changes like updating a single fact.",
        [
            ("后者使用固定算法进行狭窄的更改", "fixed"),
            ("例如更新单个事实", "single fact"),
            ("ROME 等模型编辑方法", "editing methods"),
            ("该方法也不同于", "differs"),
        ],
    ),
    (
        "评论员指出，大多数实验使用的网络只有数千个权重，并且这项工作尚未被独立复制。",
        "Commentators noted that most experiments used networks with only thousands of weights "
        "and that the work had not been independently replicated.",
        [("数千个权重", "thousands of weights"), ("尚未被独立复制", "independently replicated")],
    ),
    (
        "软件工程师 Sean Goedecke 认为，熟练的研究人员也能完成同样的任务。",
        "Software engineer Sean Goedecke argued that a skilled researcher could have performed "
        "the same tasks.",
        [("同样的任务", "same tasks"), ("熟练的研究人员", "skilled")],
    ),
    (
        "它还通过编辑 3.5% 的权重来修复失败的汽车驾驶策略，仅根据游戏规则编写游戏网络。",
        "It also repaired a failing car-driving policy by editing 3.5% of its weights, and wrote "
        "game-playing networks from only the games' rules.",
        [("编辑 3.5% 的权重", "3.5%")],
    ),
]


def highlight_heuristic(source: str, target: str, term: str) -> str:
    start = source.index(term)
    begin, end = _map_offsets(source, target, start, start + len(term))
    begin, end = _word_span(target, begin, end)
    if any(mark in target[begin:end] for mark in "，,；;、"):
        begin, end = _dilate_to_clause(target, begin, end)
    return target[begin:end].strip()


def main() -> int:
    use_model = "--model" in sys.argv
    aligner = None
    if use_model:
        from linguatui.aligner import WordAligner

        aligner = WordAligner()

    hits = total = 0
    for source, target, selections in CASES:
        pairs = aligner.pairs(source, target) if aligner else []
        for term, keyword in selections:
            if aligner:
                start = source.index(term)
                span = span_from_pairs(pairs, start, start + len(term))
                if span:
                    span = expand_to_word(target, span[0], span[1])
                got = target[span[0] : span[1]].strip() if span else ""
            else:
                got = highlight_heuristic(source, target, term)
            ok = keyword.lower() in got.lower()
            hits += ok
            total += 1
            print(f"  {'ok  ' if ok else 'MISS'} {term[:16]:18} -> {got[:80]!r}")
    print(f"\n{'模型' if aligner else '启发式'} 命中 {hits}/{total}")
    return 0 if hits == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
