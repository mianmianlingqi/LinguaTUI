# LinguaTUI

终端里的双栏翻译器：**左栏打字，右栏自动出译文**；按住 `Ctrl` 拖选一段也能单独看译文，
两栏可以互相翻译。

```
 ⭘                             LinguaTUI — 输入即译 · Ctrl+拖选即译
 ╭───────────────────────────────────────────────╮╭───────────────────────────────────────────────╮
 │ ● 左栏 简体中文  16 字 · 译入 English         ││   右栏 English 英语  59 字 · 译入 简体中文    │
 │ 今天天气不错，我们去公园散步吧。              ││ The weather is nice today, let's go for a     │
 │                                               ││ walk in the park.                             │
 │             ╭────────────────────────────────────────────────────────────────────╮             │
 │             │                                                                    │             │
 │             │  片段翻译 左栏   简体中文 → English 英语                           │             │
 │             │  引擎 Google 翻译（免费，无需 key） · 16 字                        │             │
 │             │                                                                    │             │
 │             │  原文                                                              │             │
 │             │   今天天气不错，我们去公园散步吧。                                 │             │
 │             │                                                                    │             │
 │             │  译文                                                              │             │
 │             │   The weather is nice today, let's go for a walk in the park.      │             │
 │             │                                                                    │             │
 │             │     ▔▔▔▔▔▔▔▔▔▔▔ ▔▔▔▔▔▔▔▔▔▔▔ ▔▔▔▔▔▔▔▔▔▔▔ ▔▔▔▔▔▔▔▔▔▔▔ ▔▔▔▔▔▔▔▔▔▔▔▔▔  │             │
 │             │       替换(a)     插入(i)     复制(c)     重译(r)     关闭(esc)    │             │
 │             │     ▁▁▁▁▁▁▁▁▁▁▁ ▁▁▁▁▁▁▁▁▁▁▁ ▁▁▁▁▁▁▁▁▁▁▁ ▁▁▁▁▁▁▁▁▁▁▁ ▁▁▁▁▁▁▁▁▁▁▁▁▁  │             │
 │             │                                                                    │             │
 │             ╰────────────────────────────────────────────────────────────────────╯             │
 │                                               ││                                               │
 │                                               ││                                               │
 ╰───────────────────────────────────────────────╯╰───────────────────────────────────────────────╯
 引擎 google  ·  简体中文 ⇄ English 英语  ·  输入即译 左→右  ·  Ctrl+拖选 关
 ^q 退出  ^t 全文译  ^g 选中译  ^r 输入即译  ^l 交换  ^o 语言  ^p 引擎  f1 帮助
```

## 安装 / 启动

```bat
git clone https://github.com/mianmianlingqi/LinguaTUI.git
cd LinguaTUI
pip install -e .

:: 三种启动方式，任选一种
linguatui                 :: 已装好，任意目录直接敲（推荐）
python -m linguatui
.\run.cmd                 :: PowerShell 必须带 .\ ；cmd 里可直接 run.cmd 或双击
```

> PowerShell 不会从当前目录执行命令，所以 `run.cmd` 要写成 `.\run.cmd`，
> 否则会报 “run.cmd is not recognized as a name of a cmdlet”。

需要 Python 3.10+。推荐 Windows Terminal（鼠标拖选、中文显示都正常）。

想要**词级**对照高亮，再装可选依赖（多语言 BERT，一次性下载约 700MB）：

```bat
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install transformers
:: 或者： pip install -e ".[align]"
```

## 用法

### ① 输入即译（默认开启）

在左栏打中文，停顿约 1 秒，右栏自动出现英文 —— 不用按任何键。

* `Ctrl+R` 循环方向：`关 → 当前栏 → 另一栏 → 关`，状态栏显示 `输入即译 左→右`；
* 只会把「被编辑的那一栏」翻到另一栏，所以改译文不会被反向覆盖；
* 超过 3000 字会自动暂停，改用 `Ctrl+T`（避免每敲一个字就发一次大请求）。

### ② 对照选中 + Ctrl+拖选即译（鼠标）

* **普通拖拽**：只选字、不弹窗；**松开后另一栏会高亮对应位置**，一眼看清你选中的词对应哪几个英文词（`F3` 开关）；
* **Ctrl + 拖拽**：选中一段，**松手即弹出译文小窗**；
* **Ctrl + 单击**（不拖）：直接翻译光标所在段落；
* 弹窗里 `a` 替换原文、`i` 插入另一栏、`c` 复制、`r` 重译、`Esc` 关闭；
* 方向永远指向**另一栏的语言**：在右栏选英文，小窗里就是中文。

对齐是**分层**做的，尽量细到词：

1. 段落层：按空行切块，第 N 段对第 N 段（输入即译 / `Ctrl+T` 的译文都保留空行结构）；
2. 句子层：两边句数一致时，先锚定到同一句（中文 `。！？；` / 英文 `.!?` 都算句末）；
3. 词层：句内按字符比例定位，首尾扩到完整单词；若选中内容里有**两边写法相同**的 token（`3.5%`、`Qwen3.5-0.8B`、`[4]` 这类），就以它为锚点平移对齐——这是唯一能真正"精确"的部分；
4. 分句兜底：若定位结果跨了逗号，就扩到「包含它的分句」；英文没逗号时退化成整句。保证高亮一定覆盖对应内容，不会只点亮隔壁半句。

**按 `F4` 切到「词对齐模型」** 就是真正的词级了：用多语言 BERT 的上下文向量做 IterMax 对齐
（SimAlign 思路），每对句子约 0.1–0.2 秒，结果会缓存。启发式先出结果，模型算完再把范围收窄到词：

```
启发式：ROME 等模型编辑方法 → "The approach also differs from model editing methods such as ROME, which use a"
模型　：ROME 等模型编辑方法 → "model editing methods such as ROME"
```

实测（中→英，`python tests/align_bench.py` 给出 8/9 命中）：

| 选中 | 高亮到 |
| --- | --- |
| `后者使用固定算法进行狭窄的更改` | `which use a fixed algorithm to make narrow changes` |
| `例如更新单个事实` | `changes like updating a single fact` |
| `编辑 3.5% 的权重` | `...by editing 3.5% of its weights, and wrote...`（数字锚点） |
| `数千个权重` | `thousands of weights and` |
| `同样的任务` | `the same tasks` |

> 纯文字片段的词级位置是**启发式**的（中英语序会重排，翻译接口也不返回词对齐），误差约 ±1 个词组；
> 分句兜底保证"答案一定在高亮里"。要 100% 词级，需要词对齐模型（数百 MB 依赖）或 LLM 对齐请求——目前没引入。

两栏段落数对不上时，会退化为「按整篇相对位置找段」（相差超过 2 倍就不猜）。
改动任一栏的文字或点击别处，对照高亮会自动清除。

如果某个终端不上报鼠标事件的 Ctrl 修饰键，选中后按 `Ctrl+G`，效果一样。

### ③ 光标段落即译（不用鼠标）

把光标放进任意一段，直接 `Ctrl+G`，弹窗就是该段译文；
用 `Shift+方向键` 选中后 `Ctrl+G`，翻的就是选中的部分。

### 快捷键

| 键 | 作用 |
| --- | --- |
| 直接打字 | 输入即译，另一栏自动出译文 |
| 普通鼠标拖拽 | 只选字，并在另一栏高亮对应段落 |
| `Ctrl` + 鼠标拖拽 | 选中片段，松手即弹出译文窗口 |
| `Ctrl` + 单击 | 翻译光标所在段落 |
| `Ctrl+G` | 翻译光标所在段落 / 当前选中片段 |
| `Ctrl+R` | 输入即译方向：关 / 左→右 / 右→左 |
| `Ctrl+T` | 把当前栏整篇翻译到另一栏（覆盖对方） |
| `Alt+T` | 整篇翻译并追加到另一栏末尾 |
| `Ctrl+L` | 交换左右两栏（语言 + 内容一起换） |
| `Ctrl+O` | 修改当前栏的语言（可输入中文/English/代码筛选） |
| `Ctrl+P` | 切换翻译引擎 |
| `F2` | 开 / 关 `Ctrl+拖拽` 弹窗 |
| `F3` | 开 / 关“另一栏高亮对应段落” |
| `F4` | 对齐方式：启发式 ⇄ 词对齐模型 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做本栏编辑 |
| `F1` | 帮助 |
| `Ctrl+Q` | 退出 |

窗口窄于 88 列时两栏自动改为上下排列。

### 鼠标拖拽没反应？

Windows 旧控制台（conhost，标题栏是 `Administrator: C:\...` 那种）默认开启**「快速编辑模式」**，
它会抢走鼠标拖拽，导致程序收不到选择事件。任选一种：

1. 右键标题栏 → 属性 → 选项 → 取消「快速编辑模式」，重开程序；
2. 换成 **Windows Terminal**（默认没这个问题，渲染也更好）；
3. 不用鼠标：光标放到段落里按 `Ctrl+G`（或 `Ctrl`+单击）。

程序启动时如果检测到自己跑在 conhost 里，会主动弹这条提示。

## 翻译引擎

| 引擎 | key | 说明 |
| --- | --- | --- |
| `google` | 不需要 | 免费网页接口，默认引擎，速度快、支持任意语言对 |
| `mymemory` | 不需要 | 免费但每日有额度；必须固定源语言（不能用“自动检测”） |
| `deepl` | `LINGUA_DEEPL_KEY` | 译文最自然，`free`/`pro` 端点自动判断 |
| `openai` | `LINGUA_OPENAI_KEY` | 任意 OpenAI 兼容接口：OpenAI、DeepSeek、本地 Ollama 等 |

```bat
set LINGUA_DEEPL_KEY=xxxxxxxx:fx
set LINGUA_OPENAI_KEY=sk-xxxx
set LINGUA_OPENAI_BASE=https://api.deepseek.com/v1
set LINGUA_OPENAI_MODEL=deepseek-chat
```

引擎失败时小窗里会直接显示原因（网络、额度、key 无效等），按 `r` 可重试。

## 配置文件

`%LOCALAPPDATA%\linguatui\config.toml`（首次运行后生成，退出时写入）：

```toml
provider = "google"
left_lang = "zh-CN"
right_lang = "en"
auto_popup = true
sync_selection = true    # 普通拖选后在另一栏高亮对应段落
align = "heuristic"      # "heuristic" 或 "model"（词对齐模型，需 .[align]）
align_model = "bert-base-multilingual-cased"
live = "left"            # 输入即译方向："off" / "left" / "right"
remember_text = false    # 改成 true 则下次启动恢复两栏正文
deepl_key = ""
openai_key = ""
openai_base = "https://api.openai.com/v1"
openai_model = "gpt-4o-mini"
timeout = 25.0
```

环境变量优先于配置文件。`linguatui --reset-config` 可重置。

## 命令行自检

```bat
python -m linguatui --check "今天天气不错" --to en -v
python -m linguatui --check "Hello." --from en --to zh-CN --provider mymemory
python -m linguatui --left-lang ja --right-lang zh-CN --provider deepl
```

## 测试

```bat
python tests\smoke.py
```

无头启动真实的 App（`App.run_test`）并打真实网络请求，跑通 58 项：输入即译、Ctrl+R 方向循环、
拖选对照（另一栏高亮对应段落）、Ctrl+拖选弹窗、替换、Ctrl+G 段落翻译、全文翻译、反向翻译、交换、语言/引擎选择器、帮助、
键盘选中、插入另一栏、缓存命中、窄窗口布局、长文本、缺 key 提示、配置往返。
渲染结果写到 `tests/_screen_*.txt`，README 顶部那张图由 `tests/make_readme_screenshot.py` 生成。

另有一组对照对齐的质量基准，改算法前后跑一下防止退化：

```bat
python testslign_bench.py             :: 启发式，命中 8/9
python testslign_bench.py --model     :: 词对齐模型，命中 9/9，范围精确到词
LINGUA_TEST_ALIGN=1 python tests\smoke.py   :: 额外跑模型集成测试（会加载模型）
```

## 命令行自检之外

```bat
linguatui --reset-config          :: 重置配置
linguatui --left-lang ja --right-lang zh-CN
```

## 实现要点

```
linguatui/
  app.py         Textual 界面：双栏 TextArea、输入即译、拖选弹窗、选择器、帮助
  backends.py    4 个翻译引擎 + 分块/重试/缓存（保持换行）
  aligner.py     可选：多语言 BERT 词对齐（IterMax，按需懒加载）
  langs.py       52 种语言表 + 别名归一化（zh/cn/jp…）
  config.py      config.toml 读写 + 环境变量覆盖
  clipboard.py   Win32 ctypes / pbcopy / wl-copy / OSC52
  linguatui.tcss 布局与配色
```

* 拖选用 `TextArea` 子类截 MouseDown/Up 修饰键：只有按住 `Ctrl` 才发翻译请求（正常拖拽不受影响）；
* 请求都是 Textual 异步 worker（`exclusive=True`），新输入/新选择会取消上一次请求；
* 结果落地前会核对「源栏内容是否仍等于发起请求时的文本」，过期结果直接丢弃，避免竞态写回；
* 相同片段第二次翻译直接命中内存缓存，弹窗瞬间出现；
* 长文本按句/段切块（Google 1500 字/块），拼回时一个字符都不丢，换行结构保留。
