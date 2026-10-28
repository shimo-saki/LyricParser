"""序列化配置的 dataclass 定义。

所有 ``stringify_*`` 的 ``options`` 参数统一为 :class:`StringifyOptions`。
支持传入 ``StringifyOptions`` / ``dict`` / ``None``——由
:meth:`StringifyOptions.from_dict` 归一化。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class ParserOptions:
    """所有 ``parser`` 的统一配置。

    通过 :meth:`from_dict` 可从 ``dict`` / ``ParserOptions`` / ``None``
    构造。字段的合法性在 :meth:`__post_init__` 中统一校验。

    Attributes:
        mode: LRC 家族的解析模式：
            - ``"plain"``：丢弃逐字时间戳，只保留整行；
            - ``"enhanced"``：保留逐字（enhanced LRC 语义）；
        translation_language: TTML 解析时优先选用的翻译语言（``xml:lang``）。
            ``None`` 表示使用源文件中出现的第一条翻译。
        romanization_language: TTML 解析时优先选用的罗马字语言（``xml:lang``）。
            ``None`` 表示使用源文件中出现的第一条罗马字。
    """

    mode: Literal["plain", "enhanced"] = "enhanced"
    translation_language: str | None = None
    romanization_language: str | None = None
    parse_comments: bool = False

    def __post_init__(self) -> None:
        if self.mode not in ("plain", "enhanced"):
            raise ValueError(f"mode 必须是 'plain' 或 'enhanced'，实际为 {self.mode!r}")
        for name in ("translation_language", "romanization_language"):
            value = getattr(self, name)
            if not isinstance(value, (str, type(None))):
                raise TypeError(f"{name} 必须是 str 或 None，实际为 {type(value).__name__!r}")
        if not isinstance(self.parse_comments, bool):
            raise TypeError(f"parse_comments 必须是 bool，实际为 {type(self.parse_comments).__name__!r}")

    @classmethod
    def from_dict(cls, options: "ParserOptions" | dict[str, Any] | None) -> "ParserOptions":
        """归一化构造：接受 ``ParserOptions`` / ``dict`` / ``None``。

        Raises:
            TypeError: ``options`` 类型不支持，或字段校验失败。
            ValueError: 字段值超出允许范围。
        """
        if options is None:
            return cls()
        if isinstance(options, cls):
            return options
        if isinstance(options, dict):
            return cls(**options)
        raise TypeError(f"options 必须是 ParserOptions / dict / None，实际为 {type(options).__name__!r}")


@dataclass
class StringifyOptions:
    """所有 ``stringify`` 的统一配置。

    通过 :meth:`from_dict` 可从 ``dict`` / ``StringifyOptions`` / ``None``
    构造。字段的合法性在 :meth:`__post_init__` 中统一校验。

    Attributes:
        mode: LRC 输出模式：
            - ``"plain"``：整行同步；
            - ``"enhanced"``：逐字，含 enhanced LRC 特性；
        inline_bracket: 逐字内联时间戳的括号：
            - ``"square"``：``[t]``（ESLRC 惯例）；
            - ``"angle"``：``<t>``（Enhanced LRC / SPL 惯例）。
        ms_digits: 时间戳小数位数，支持 ``1..6``。
        include_metadata: 是否输出元数据。
        metadata_keys: 元数据白名单；``None`` 表示全部输出。
            LRC 家族内部按小写比较（大小写不敏感）；TTML 保留原样
            （camelCase 大小写敏感）。
        use_sidecar: TTML 专用——是否强制把翻译/罗马字写入侧栏。
    """

    # Stringify 可选项
    ms_digits: Literal[1, 2, 3, 4, 5, 6] = 3
    include_metadata: bool = True
    metadata_keys: Iterable[str] | frozenset[str] | None = None
    use_sidecar: bool = False

    # Stringify 内部选项
    mode: Literal["plain", "enhanced"] = "plain"
    inline_bracket: Literal["square", "angle"] = "angle"

    def __post_init__(self) -> None:
        if self.mode not in ("plain", "enhanced"):
            raise ValueError(f"mode 必须是 'plain' 或 'enhanced'，实际为 {self.mode!r}")
        if self.inline_bracket not in ("square", "angle"):
            raise ValueError(f"inline_bracket 必须是 'square' 或 'angle'，实际为 {self.inline_bracket!r}")
        if not isinstance(self.ms_digits, int) or not 1 <= self.ms_digits <= 6:
            raise ValueError(f"ms_digits 必须是 1..6 的整数，实际为 {self.ms_digits!r}")

        for name in ("include_metadata", "use_sidecar"):
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise TypeError(f"{name} 必须是 bool，实际为 {type(value).__name__!r}")

        if self.metadata_keys is not None:
            keys = self.metadata_keys
            if isinstance(keys, str):
                raise TypeError("metadata_keys 不能是字符串，必须传入字符串的可迭代对象（例如 {'ti', 'ar'}）")
            if not isinstance(keys, Iterable):
                raise TypeError(f"metadata_keys 必须是可迭代对象，实际为 {type(keys).__name__!r}")
            keys = frozenset(keys)
            if not all(isinstance(k, str) for k in keys):
                raise TypeError("metadata_keys 中的所有元素必须是字符串")
            self.metadata_keys = keys

    @classmethod
    def from_dict(cls, options: "StringifyOptions" | dict[str, Any] | None) -> "StringifyOptions":
        """归一化构造：接受 ``StringifyOptions`` / ``dict`` / ``None``。

        Raises:
            TypeError: ``options`` 类型不支持，或字段校验失败。
            ValueError: 字段值超出允许范围。
        """
        if options is None:
            return cls()
        if isinstance(options, cls):
            return options
        if isinstance(options, dict):
            return cls(**options)
        raise TypeError(f"options 必须是 StringifyOptions / dict / None，实际为 {type(options).__name__!r}")
