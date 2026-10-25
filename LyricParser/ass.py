"""ASS 字幕生成。

ASS 使用 ``h:mm:ss.cc`` 时间格式与 ``\\k`` 卡拉 OK 标签；本库用默认样式
（Default）与 ``v1``/``v2`` 说话人名称区分主唱/对唱/背景人声。
"""

from .models import Line
from .utils import ensure_synced, write_ass_timestamp


def _speaker(line: Line) -> str:
    """根据行特性返回 ASS 说话人名称。

    - 主唱：``v1``
    - 对唱：``v2``
    - 背景人声：后缀 ``-bg``
    """
    name = "v2" if line.is_duet else "v1"
    return name + ("-bg" if line.is_bg else "")


def _dialog(out: list[str], start: float, end: float, name: str, text: str) -> None:
    """向输出列表追加一条 Dialogue 行。"""
    out.append(
        f"Dialogue: 0,{write_ass_timestamp(start)}, " f"{write_ass_timestamp(end)}, Default, {name},0,0,0,,{text}"
    )


def stringify_ass(lines: list[Line]) -> str:
    """序列化为 ASS 字幕文本（仅 Events 部分）。

    Args:
        lines: 歌词行列表。**必须全部带时间戳**；纯文本歌词会抛
            :class:`PlainTextNotSupportedError`。

    Returns:
        ASS 文本。

    Raises:
        PlainTextNotSupportedError: 存在无时间戳的行时。
    """
    ensure_synced(lines, "ASS")
    out: list[str] = [
        "[Script Info]",
        "[Events]",
        "Formats: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for line in lines:
        timed = [w for w in line.words if int(w.end_time) > int(w.start_time)]
        if not timed:
            continue
        start = min(w.start_time for w in timed)
        end = max(w.end_time for w in timed)
        text = ""
        prev_end = start
        for w in line.words:
            ws, we = int(w.start_time), int(w.end_time)
            if ws >= we:
                text += w.word
                continue
            if ws > prev_end:
                gap = (ws - prev_end + 5) // 10
                if gap > 0:
                    text += f"{{\\k{gap}}}"
            dur = (we - ws + 5) // 10
            if dur > 0:
                text += f"{{\\k{dur}}}"
            text += w.word
            prev_end = we
        speaker = _speaker(line)
        _dialog(out, start, end, speaker, text)
        if line.translated_lyric:
            _dialog(out, start, end, f"{speaker}-trans", line.translated_lyric)
        if line.roman_lyric:
            _dialog(out, start, end, f"{speaker}-roman", line.roman_lyric)
    return "\n".join(out) + "\n"
