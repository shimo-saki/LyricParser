"""QRC（QQ 音乐逐字歌词）解析与生成。

格式：``[start,dur]text(start,dur)text(start,dur)...``。
"""

import re

from .models import Line, Word
from .utils import ensure_synced

# 音节：文本(start,dur)
WORD_REGEX = re.compile(r"(.*?)\((\d+),(\d+)\)")
# 行首：[start,dur]
LINE_REGEX = re.compile(r"^\[(\d+),(\d+)\]")
# 背景人声括号（全角/半角）
BG_BEGIN = ("(", "（")
BG_END = (")", "）")


def parse_qrc(qrc: str) -> list[Line]:
    """解析 QRC 文本。

    Args:
        qrc: QRC 文本。

    Returns:
        行列表。
    """
    out: list[Line] = []
    for raw in re.split(r"\r?\n", qrc):
        line_str = raw.strip()
        if not line_str:
            continue
        m = LINE_REGEX.match(line_str)
        if not m:
            continue
        line_start, line_dur = int(m.group(1)), int(m.group(2))
        content = line_str[m.end() :].strip()
        if not content:
            continue
        words: list[Word] = []
        for wm in WORD_REGEX.finditer(content):
            text, ws, wd = wm.group(1), int(wm.group(2)), int(wm.group(3))
            words.append(Word(word=text, start_time=ws, end_time=ws + wd))
        is_bg = bool(words) and words[0].word.startswith(BG_BEGIN) and words[-1].word.endswith(BG_END)
        if is_bg:
            words[0].word = re.sub(r"^[(（]", "", words[0].word)
            words[-1].word = re.sub(r"[）)]$", "", words[-1].word)
        out.append(
            Line(
                start_time=line_start,
                end_time=line_start + line_dur,
                words=words,
                is_bg=is_bg,
            )
        )
    return out


def stringify_qrc(lines: list[Line]) -> str:
    """序列化为 QRC 文本。

    Args:
        lines: 歌词行列表。**必须全部带时间戳**；纯文本歌词会抛
            :class:`PlainTextNotSupportedError`。

    Returns:
        QRC 文本。

    Raises:
        PlainTextNotSupportedError: 存在无时间戳的行时。
    """
    ensure_synced(lines, "QRC")
    out: list[str] = []
    for line in lines:
        ls, le = int(line.start_time), int(line.end_time)
        ld = le - ls
        buf: list[str] = []
        for idx, w in enumerate(line.words):
            if not w.word.strip() and buf:
                # 无意义的空音节合并到前一格
                last = buf[-1]
                paren = last.rfind("(")
                buf[-1] = f"{last[:paren]}{w.word}{last[paren:]}"
                continue
            printed = w.word
            if line.is_bg:
                if idx == 0:
                    printed = f"（{printed}"
                if idx == len(line.words) - 1:
                    printed += "）"
            ws, we = int(w.start_time), int(w.end_time)
            buf.append(f"{printed}({ws},{we - ws})")
        out.append(f"[{ls},{ld}]{''.join(buf)}")
    return "\n".join(out)
