"""LyricParser — 歌词工具库。

提供以下歌词格式的解析与生成：

- **LRC 家族**：LRC / Enhanced LRC / ESLRC / SPL
- **QRC**：QQ 音乐逐字
- **YRC**：网易云音乐逐字
- **TTML**：Apple Music / AMLL
- **ASS**：字幕

公共数据结构见 :mod:`LyricParser.models`（``Word``/``Line``/``Lyric`` 等）。

用法示例::

    import LyricParser as lp

    lines = lp.parse_lrc("[00:01.00]Hello")
    print(lp.stringify_lrc(lines))

    ttml = lp.stringify_ttml(lp.Lyric(
        lines=[lp.Line(
            words=[lp.Word(word="Hi", start_time=0, end_time=1000)],
            start_time=0, end_time=1000,
        )],
    ))
"""

from .ass import stringify_ass
from .lrc import (
    LrcGenerator,
    LrcParser,
    parse_enhanced_lrc,
    parse_eslrc,
    parse_lrc,
    parse_lrc_like,
    parse_spl,
    stringify_enhanced_lrc,
    stringify_eslrc,
    stringify_lrc,
    stringify_lrc_like,
    stringify_spl,
)
from .models import Line, Lyric, MetadataEntry, Ruby, Word
from .options import ParserOptions, StringifyOptions
from .qrc import parse_qrc, stringify_qrc
from .qrc_crypto import decrypt_qrc_hex, encrypt_qrc_hex
from .ttml import (
    TTMLGenerator,
    TTMLParser,
)
from .ttml import Values as TTMLValues
from .ttml import (
    parse_ttml,
    stringify_ttml,
)
from .utils import PlainTextNotSupportedError
from .yrc import parse_yrc, stringify_yrc

__all__ = [
    # 数据模型
    "Word",
    "Line",
    "Lyric",
    "Ruby",
    "MetadataEntry",
    # LRC 家族
    "parse_lrc",
    "parse_eslrc",
    "parse_enhanced_lrc",
    "parse_spl",
    "parse_lrc_like",
    "stringify_lrc",
    "stringify_eslrc",
    "stringify_enhanced_lrc",
    "stringify_spl",
    "stringify_lrc_like",
    "LrcParser",
    "LrcGenerator",
    # QRC
    "parse_qrc",
    "stringify_qrc",
    # YRC
    "parse_yrc",
    "stringify_yrc",
    # TTML
    "parse_ttml",
    "stringify_ttml",
    "TTMLParser",
    "TTMLGenerator",
    "TTMLValues",
    # ASS
    "stringify_ass",
    # 加解密
    "decrypt_qrc_hex",
    "encrypt_qrc_hex",
    # 配置项
    "ParserOptions",
    "StringifyOptions",
    # 错误
    "PlainTextNotSupportedError",
]
