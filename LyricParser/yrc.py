"""YRC（网易云音乐逐字歌词）解析与生成。

格式：``[start,dur](t,dur,0)text(t,dur,0)text...``，其中 ``0`` 字段是
YRC 规范中的保留位，恒为 0。
"""

import re

from .models import Line, Word
from .utils import ensure_synced

# 音节：(start,dur,0)text
WORD_REGEX = re.compile(r"^(.*?)\((\d+),(\d+),0\)")
# 行首：[start,dur]
LINE_REGEX = re.compile(r"^\[(\d+),(\d+)\]")
# 背景人声括号
BG_BEGIN = ("(", "（")
BG_END = (")", "）")


def parse_yrc(yrc: str) -> list[Line]:
    """解析 YRC 文本。

    Args:
        yrc: YRC 文本。

    Returns:
        行列表。
    """
    out: list[Line] = []
    for line_str in map(str.strip, re.split(r"\r?\n", yrc)):
        if not line_str or not (m := LINE_REGEX.match(line_str)):
            continue
        if not (content := line_str[m.end() :].strip()):
            continue
        line_start, line_dur = int(m.group(1)), int(m.group(2))
        words: list[Word] = []
        last_start, last_end = -1, -1
        while wm := WORD_REGEX.match(content):
            text, ws, wd = wm.group(1), int(wm.group(2)), int(wm.group(3))
            if text and last_start != -1:
                words.append(Word(word=text, start_time=last_start, end_time=last_end))
            last_start, last_end = ws, ws + wd
            content = content[wm.end() :]
        if last_start != -1 and content:
            words.append(Word(word=content, start_time=last_start, end_time=last_end))
        is_bg = bool(words) and words[0].word.startswith(BG_BEGIN) and words[-1].word.endswith(BG_END)
        if is_bg:
            words[0].word = words[0].word[1:]
            words[-1].word = words[-1].word[:-1]
        out.append(Line(start_time=line_start, end_time=line_start + line_dur, words=words, is_bg=is_bg))
    return out


def _make_full(text: str) -> str:
    """将半角括号替换为全角（避免与 YRC 的时长括号冲突）。"""
    return text.replace("(", "（").replace(")", "）")


def stringify_yrc(lines: list[Line]) -> str:
    """序列化为 YRC 文本。

    Args:
        lines: 歌词行列表。**必须全部带时间戳**；纯文本歌词会抛
            :class:`PlainTextNotSupportedError`。

    Returns:
        YRC 文本。

    Raises:
        PlainTextNotSupportedError: 存在无时间戳的行时。
    """
    ensure_synced(lines, "YRC")
    out: list[str] = []
    for line in lines:
        ls, le = int(line.start_time), int(line.end_time)
        buf: list[str] = []
        for idx, w in enumerate(line.words):
            if not w.word.strip() and buf:
                last = buf[-1]
                if (pos := last.find(")")) != -1:
                    buf[-1] = f"{last[:pos]}{w.word}{last[pos:]}"
                continue
            printed = _make_full(w.word)
            if line.is_bg and idx == 0:
                printed = f"（{printed}"
            if line.is_bg and idx == len(line.words) - 1:
                printed += "）"
            ws, we = int(w.start_time), int(w.end_time)
            buf.append(f"({ws},{we - ws},0){printed}")
        out.append(f"[{ls},{le - ls}]{''.join(buf)}")
    return "\n".join(out)
