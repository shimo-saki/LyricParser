# LyricParser

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

** 歌词工具库** — 统一的解析与序列化 API，支持 LRC、ESLRC、Enhanced LRC、SPL、QRC、YRC、TTML、ASS 共 8 种歌词格式。

---

## ✨ 特性

- 🎵 **8 种格式** 全面支持，涵盖 LRC 家族 / QQ音乐 / 网易云 / Apple Music / ASS 字幕
- 🔄 **统一数据模型**：所有格式解析后转为 `Lyric` / `Line` / `Word` 结构，可以自由转换
- 🧩 **对象方法 & 静态函数双风格**：`lyric.stringify_lrc()` 与 `stringify_lrc(lyric)` 等价
- 🔤 **逐字歌词**、**注音（ruby）**、**翻译**、**罗马字**、**背景人声** 完整保留
- 📝 **纯文本歌词** 自动识别（无时间戳），支持从纯文本导入或导出
- ⏱️ **时间平移**：`lyric << 500`（提前）/ `lyric >> 500`（延后）
- 🔗 **合并工具**：`lyric + other` / `lyric.combine(other)` 把翻译挂到对应行
- 🎛️ **可配置**：`ParserOptions` / `StringifyOptions` dataclass 统一配置，带类型校验
- 🪶 **零第三方依赖**：只用 Python 标准库

---

## 📦 安装

### 从 GitHub 安装最新版

```bash
pip install git+https://github.com/shimo-saki/LyricParser.git
```

### 本地开发

```bash
git clone https://github.com/shimo-saki/LyricParser.git
cd LyricParser
pip install -e .
```

**要求**：Python 3.10 或更高版本。

---

## 🚀 快速开始

```python
import LyricParser as lp

# ── 解析 LRC
lyric = lp.parse_lrc_like(
    "[ti:Song]\n[ar:Artist]\n[00:01.00]Hello\n[00:03.00]World"
)

for line in lyric:
    print(f"[{line.start_time:>6}ms] {line.text}")

# 输出：
# [  1000ms] Hello
# [  3000ms] World

# ── 序列化为不同格式
print(lyric.stringify_lrc())
print(lyric.stringify_enhanced_lrc())
print(lyric.stringify_ttml())
```

### 格式转换

```python
# LRC → Enhanced LRC → TTML → LRC
lrc_text  = "[00:01.00]Hello\n[00:03.00]World"
lines     = lp.parse_lrc(lrc_text)
elrc_text = lp.stringify_enhanced_lrc(lines)
ttml_text = lp.stringify_ttml(lp.Lyric(lines=lines))
lrc_back  = lp.stringify_lrc(lines)
```

---

## 📋 支持的格式

| 格式 | 解析 | 序列化 | 时间戳 | 逐字 | 翻译 | 备注 |
|---|---|---|---|---|---|---|
| **LRC** | ✅ | ✅ | `[mm:ss.xx]` | ❌ | 独立行 | 最常见 |
| **ESLRC** | ✅ | ✅ | `[mm:ss.xx]` | ✅ `[t]` | 独立行 | enhanced LRC（方括号）|
| **Enhanced LRC** | ✅ | ✅ | `[mm:ss.xx]` | ✅ `<t>` | 独立行 | enhanced LRC（尖括号）|
| **SPL** | ✅ | ✅ | `[mm:ss.xx]` | ✅ `<t>` | 独立行 | Lyricify Syllable |
| **QRC** | ✅ | ✅ | 整数毫秒 | ✅ | ❌ | QQ 音乐 |
| **YRC** | ✅ | ✅ | 整数毫秒 | ✅ | ❌ | 网易云音乐 |
| **TTML** | ✅ | ✅ | `mm:ss.xxx` | ✅ | ✅ sidecar | Apple Music / AMLL 扩展 |
| **ASS** | ❌ | ✅ | `h:mm:ss.cc` | ✅ | ✅ | 字幕格式 |

---

## 🧱 核心数据模型

### `Lyric` — 完整歌词

```python
from LyricParser import Lyric, Line, Word, MetadataEntry

lyric = Lyric(
    lines=[
        Line(
            words=[Word(word="Hello", start_time=0, end_time=1000)],
            start_time=0,
            end_time=1000,
        ),
    ],
    metadata=[MetadataEntry("ti", ["Song"])],
)
```

### `Line` — 一行歌词

```python
line = lyric[0]
print(line.text)           # "Hello"          （拼接所有 word）
print(line.duration)       # 1000             （end_time - start_time）
print(line.word_count)     # 1
print(line.is_word_sync)   # False            （逐字判定）
```

### `Word` — 音节

```python
word = line.words[0]
print(word.word)           # "Hello"
print(word.start_time)     # 0
print(word.ruby)           # None
```

### `Lyric` 容器协议

```python
for line in lyric:              # 遍历
    ...

len(lyric)                      # 行数
lyric[0]                        # 索引（支持负数）
lyric[0:3]                      # 切片 → list[Line]
if some_line in lyric: ...      # 成员判断
for line in reversed(lyric): ... # 反向遍历

lyric.append(line)              # 追加（自动按 start_time 排序）
lyric.extend([l1, l2])          # 批量追加
lyric.sort_lines()              # 显式排序
```

---

## 🔧 高级用法

### 时间平移

```python
lyric = lp.parse_lrc_like("[00:01.00]Hello")

# 就地平移
lyric.shift(-500)          # 提前 500ms
lyric.adjust_offset(500)   # 延后 500ms（shift 的别名）

# 返回新对象（原对象不变）
earlier = lyric << 300     # 提前 300ms
later   = lyric >> 300     # 延后 300ms

# 链式组合
result = (lyric << 100) >> 50   # 净提前 50ms
```

### 合并翻译

```python
original = lp.parse_lrc_like("[00:01.00]Hello\n[00:03.00]World")
trans    = lp.parse_lrc_like("[00:01.00]你好\n[00:03.00]世界")

# 方式 1：就地合并（按 start_time 匹配）
original.combine(trans)

# 方式 2：返回新对象
merged = original + trans

# 方式 3：就地
original += trans

# 方式 4：批量
merged = sum([a, b, c])

# 方式 5：纯文本翻译（按索引顺序合并）
plain_trans = lp.parse_lrc_like("你好\n世界\n额外")
original.combine(plain_trans, other_as_refline_only=False)
```

### 纯文本歌词

```python
# 无时间戳文本 → 纯文本 Lyric
plain = lp.parse_lrc_like("[ti:Song]\n第一行\n第二行\n第三行")
print(plain.is_plain_text)   # True
print(plain.is_synced)       # False
print(plain[0].start_time)   # None

# 输出也是纯文本
print(plain.stringify_lrc())
# [ti:Song]
#
# 第一行
# 第二行
# 第三行
```

> ⚠️ **非 LRC 家族**（QRC / YRC / ASS / TTML）遇到纯文本歌词会抛出 `PlainTextNotSupportedError`。

---

## ⚙️ 配置选项

所有 `parser` 和 `stringify` 都接受 `options` 参数，可以是 `dict`、配置对象或 `None`。

### `ParserOptions` — 解析配置

```python
from LyricParser import ParserOptions

opts = ParserOptions(
    mode="enhanced",                   # "plain" / "enhanced"
    translation_language="zh-Hans",    # TTML 翻译首选语言
    romanization_language="ja-Latn",   # TTML 罗马字首选语言
)

lyric = lp.parse_ttml(ttml_text, opts)
lines = lp.parse_lrc(src, {"mode": "plain"})   # 丢弃逐字
```

### `StringifyOptions` — 序列化配置

```python
from LyricParser import StringifyOptions

opts = StringifyOptions(
    mode="enhanced",               # "plain" / "enhanced"
    inline_bracket="angle",        # "square" / "angle"
    end_bracket=None,              # 末尾时间戳括号（None 跟随 inline_bracket）
    ms_digits=3,                   # 时间戳小数位数，1..6
    include_metadata=True,         # 是否输出元数据
    metadata_keys={"ti", "ar"},    # 元数据白名单
    use_sidecar=False,             # TTML 专用
)

print(lp.stringify_lrc(lyric, opts))
print(lp.stringify_lrc(lyric, {"ms_digits": 2, "include_metadata": False}))
```

### 时间戳精度（`ms_digits`）

| `ms_digits` | 输出示例 | 行为 |
|---|---|---|
| 1 | `[00:01.2]` | 截断（不四舍五入）|
| 2 | `[00:01.23]` | 截断 |
| 3 | `[00:01.234]` | 原样（默认）|
| 4 | `[00:01.2340]` | 补 0 |
| 5 | `[00:01.23400]` | 补 0 |
| 6 | `[00:01.234000]` | 补 0 |

---

## 📚 API 一览

### 解析函数（返回 `Lyric` 或 `list[Line]`）

| 函数 | 返回 | 说明 |
|---|---|---|
| `parse_lrc_like(text, options=None)` | `Lyric` | LRC 家族通用解析（保留元数据）|
| `parse_lrc(text, options=None)` | `list[Line]` | 普通 LRC |
| `parse_eslrc(text, options=None)` | `list[Line]` | ESLRC（方括号内联）|
| `parse_enhanced_lrc(text, options=None)` | `list[Line]` | Enhanced LRC（尖括号内联）|
| `parse_spl(text, options=None)` | `list[Line]` | SPL |
| `parse_qrc(text)` | `list[Line]` | QRC |
| `parse_yrc(text)` | `list[Line]` | YRC |
| `parse_ttml(text, options=None)` | `Lyric` | TTML |

### 序列化函数

| 函数 | 输入 | 说明 |
|---|---|---|
| `stringify_lrc(source, options=None)` | `Lyric \| list[Line]` | LRC |
| `stringify_eslrc(source, options=None)` | 同上 | ESLRC |
| `stringify_enhanced_lrc(source, options=None)` | 同上 | Enhanced LRC |
| `stringify_spl(source, options=None)` | 同上 | SPL |
| `stringify_lrc_like(source, options=None)` | 同上 | LRC 家族通用 |
| `stringify_qrc(lines)` | `list[Line]` | QRC |
| `stringify_yrc(lines)` | `list[Line]` | YRC |
| `stringify_ass(lines)` | `list[Line]` | ASS |
| `stringify_ttml(lyric, options=None)` | `Lyric` | TTML |

### 对象方法

所有 `stringify` 都作为 `Lyric` 的方法存在，签名相同：

```python
lyric.stringify_lrc()
lyric.stringify_enhanced_lrc()
lyric.stringify_ttml({"include_metadata": False})
# ...
```

---

## 🔍 示例：完整转换

```python
import LyricParser as lp

# 从 Enhanced LRC 读入
src = """[ti:示例歌曲]
[ar:Artist]

[00:01.00]<00:01.00>Hello<00:02.00>
[00:01.00]你好
[00:03.00]<00:03.00>World<00:04.00>
[00:03.00]世界
"""

lines = lp.parse_enhanced_lrc(src)

# 转 TTML
ttml = lp.stringify_ttml(
    lp.Lyric(lines=lines, metadata=[
        lp.MetadataEntry("ti", ["示例歌曲"]),
    ]),
    {"ms_digits": 2},
)

# 转 ASS 字幕
ass = lp.stringify_ass(lines)

# 整体延后 500ms
later = lines >> 500
```

---

## 🧪 异常处理

```python
import LyricParser as lp

try:
    lp.stringify_lys(lp.parse_lrc("Hello\nWorld"))
except lp.PlainTextNotSupportedError as e:
    print(e)
    # LYS 格式要求每一行都带时间戳；第 0 行（'Hello'）没有 start_time。
    # 纯文本歌词请使用 LRC 系列格式。
```

所有 `PlainTextNotSupportedError` 都是 `ValueError` 子类，可用 `except ValueError` 统一捕获。

---
