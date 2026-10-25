"""通用工具函数：时间解析/格式化、归一化、字符串拼接等。"""

import math

from .models import Line, Word

#: 时间戳类型（int 或 float）
Number = int | float

#: LRC 时间戳可表示的最大值（999:59.999）
MAX_LRC_TIMESTAMP: int = 999 * 60000 + 59 * 1000 + 999


class PlainTextNotSupportedError(ValueError):
    """格式不支持无时间戳歌词时抛出。

    某些格式（QRC / YRC / ASS / TTML）要求每一行都带
    时间戳；如果待序列化的 :class:`Lyric` 含无时间戳的行，则抛出此异常。
    LRC 家族（LRC / ESLRC / Enhanced LRC / SPL）支持纯文本歌词，不会触发。
    """


def _validate_ms_digits(n: int) -> int:
    """校验 ``ms_digits`` 取值，仅允许 1..6 的整数。

    Args:
        n: 待校验的值。

    Returns:
        合法的整数值。

    Raises:
        ValueError: 当取值不是 1..6 的整数时。
    """
    if not isinstance(n, int) or n < 1 or n > 6:
        raise ValueError(f"ms_digits 必须是 1..6 的整数，实际为 {n!r}")
    return n


def ensure_synced(lines: list[Line], format_name: str) -> None:
    """检查所有行都带时间戳，否则抛 :class:`PlainTextNotSupportedError`。

    纯文本行或混合 Lyric 中的无时间戳行都会触发。空列表不报错（生成空
    输出是无害的）。

    Args:
        lines: 待检查的歌词行列表。
        format_name: 目标格式名称，用于错误提示（如 ``"LYS"``）。

    Raises:
        PlainTextNotSupportedError: 存在 ``start_time is None`` 的行。
    """
    for i, line in enumerate(lines):
        if line.start_time is None:
            raise PlainTextNotSupportedError(
                f"{format_name} 格式要求每一行都带时间戳；"
                f"第 {i} 行 ({line.text!r}) 没有 start_time。"
                f"纯文本歌词请使用 LRC 系列格式。"
            )


def normalize_timestamp(ms: int | float | None) -> int | float | None:
    """将任意值归一化为合法的时间戳（毫秒）。

    ``None`` 原样返回 ``None``（表示"无时间戳"，纯文本歌词场景）；
    NaN / 无穷大 / 无法解析 → 0；负数 → 0。
    """
    if ms is None:
        return None
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return 0
    if math.isnan(v) or math.isinf(v):
        return 0
    return 0 if v < 0 else v


def _format_fraction(ms: int, ms_digits: int) -> str:
    """把毫秒部分（0..999）格式化为指定小数位数的字符串。

    ``ms_digits <= 3`` 时截断（不四舍五入，避免跨秒进位）；
    ``ms_digits > 3`` 时在末尾补 0（内部无更细精度）。

    Args:
        ms: 0..999 的毫秒部分。
        ms_digits: 小数位数（1..6）。

    Returns:
        指定位数的数字字符串。
    """
    factor = 10 ** abs(ms_digits - 3)
    value = ms // factor if ms_digits < 3 else ms * factor
    return f"{value:0{ms_digits}d}"


def normalize_duration(ms: int | float | None) -> Number:
    """将任意值归一化为合法的时长（毫秒）。

    时长与时间戳语义相近，负数视为 0。

    Args:
        ms: 原始值。

    Returns:
        非负的时长数值。
    """
    return normalize_timestamp(ms)


def clamp_timestamp(ms: Number) -> Number:
    """将时间戳限制在 LRC 格式可表示的范围内（不超过 999:59.999）。

    Args:
        ms: 原始时间戳。

    Returns:
        取值范围为 ``[0, MAX_LRC_TIMESTAMP]`` 的时间戳。
    """
    return min(MAX_LRC_TIMESTAMP, normalize_timestamp(ms))


def parse_timestamp_parts(min_str: str, sec_str: str, ms_str: str = "") -> int:
    """将 ``分、秒、毫秒`` 三段字符串解析为毫秒时间戳。

    毫秒段会自动补零或截断到 3 位；例如 ``"5"`` 被视为 500ms，
    ``"12345"`` 被截断为 123ms。

    Args:
        min_str: 分钟字符串。
        sec_str: 秒字符串。
        ms_str: 毫秒字符串，可为空。

    Returns:
        毫秒时间戳。
    """
    ms_padded = (ms_str + "000")[:3]
    return int(min_str) * 60000 + int(sec_str) * 1000 + int(ms_padded)


def format_time(ms: int, ms_digits: int = 3) -> str:
    """格式化毫秒为 ``mm:ss.xxx`` 字符串。

    Args:
        ms: 毫秒时间戳；负数会被截断为 0。
        ms_digits: 小数位数，1..6。

    Returns:
        形如 ``"01:23.456"`` 的字符串。

    Raises:
        ValueError: 当 ``ms_digits`` 不是 1..6 的整数时。
    """
    _validate_ms_digits(ms_digits)
    ms = int(ms)
    if ms < 0:
        ms = 0
    minute = ms // 60000
    sec = (ms % 60000) // 1000
    milli = ms % 1000
    return f"{minute:02d}:{sec:02d}.{_format_fraction(milli, ms_digits)}"


def _round_half_up(x: float) -> int:
    """四舍五入取整（避免 Python 内置 round 的银行家舍入）。"""
    return int(math.floor(x + 0.5))


def write_ass_timestamp(ms: Number) -> str:
    """将毫秒格式化为 ASS 规范的时间戳 ``h:mm:ss.cc``。

    ASS 的时间精度为 **centiseconds**（10ms），因此毫秒会四舍五入到最近的
    centisecond。

    Args:
        ms: 毫秒时间戳。

    Returns:
        形如 ``"0:01:23.45"`` 的字符串。
    """
    v = normalize_timestamp(ms)
    total_ms = _round_half_up(v)
    milli = total_ms % 1000
    sec_total = total_ms // 1000
    sec = sec_total % 60
    min_total = sec_total // 60
    return f"{min_total // 60}:{min_total % 60:02d}:{sec:02d}.{milli // 10:02d}"


def is_word_sync_lyric(words: list[Word], start_time: int) -> bool:
    """判断该行是否为"逐字同步"歌词。

    判定规则：
    - 无音节：False
    - 音节数 > 1：True
    - 音节数 == 1：仅当音节起始时间与行起始时间不同时为 True

    Args:
        words: 音节列表。
        start_time: 行的起始时间。

    Returns:
        是否为逐字歌词。
    """
    if not words:
        return False
    if len(words) > 1:
        return True
    return words[0].start_time != start_time


def normalize_line(line: Line) -> Line:
    """就地将行的所有时间字段归一化。

    纯文本行（``start_time is None``）原样保留，不会被错误地转成 0。
    """
    if line.start_time is not None:
        line.start_time = normalize_timestamp(line.start_time)
    if line.end_time is not None:
        line.end_time = normalize_timestamp(line.end_time)
    for w in line.words:
        if w.start_time is not None:
            w.start_time = normalize_timestamp(w.start_time)
        if w.end_time is not None:
            w.end_time = normalize_timestamp(w.end_time)
    return line
