"""公共数据模型。"""

import copy
from dataclasses import dataclass, field
from typing import Iterator, TypedDict

from .options import StringifyOptions


@dataclass
class Ruby:
    """单个注音片段（ruby / furigana）。"""

    word: str = ""
    start_time: int = 0
    end_time: int = 0


@dataclass
class Word:
    """
    单个音节。

    Attributes:
        word: 音节文本，可能包含尾随空格（表示与下一音节之间的空格）。
        start_time: 音节开始时间（毫秒）；纯文本歌词为 ``None``。
        end_time: 音节结束时间（毫秒）；纯文本歌词为 ``None``。
        roman_word: 该音节对应的罗马字（拼音/罗马音）。
        obscene: 是否为敏感词，None 表示未知/未设置。
        empty_beat: 空拍数量，None 表示无空拍。
        ruby: 该音节的注音列表；无注音时为 None。
    """

    word: str = ""
    start_time: int | None = None
    end_time: int | None = None
    roman_word: str = ""
    obscene: bool | None = None
    empty_beat: int | None = None
    ruby: list[Ruby] | None = None


@dataclass
class Line:
    """
    一行歌词。

    Attributes:
        words: 音节列表。整行同步时只有 1 个音节。
        translated_lyric: 翻译文本；多条翻译用 ``"\\n"`` 拼接。
        roman_lyric: 整行罗马字文本。
        is_bg: 是否为背景人声。
        is_duet: 是否为对唱。
        start_time: 行开始时间（毫秒）；纯文本歌词为 ``None``。
        end_time: 行结束时间（毫秒）；纯文本歌词为 ``None``。
    """

    words: list[Word] = field(default_factory=list)
    translated_lyric: str = ""
    roman_lyric: str = ""
    is_bg: bool = False
    is_duet: bool = False
    start_time: int | None = None
    end_time: int | None = None

    @property
    def text(self) -> str:
        """整行文本（所有音节的 ``word`` 直接拼接）。

        对应 ``"".join(w.word for w in self.words)``。注意 ``Word.word``
        可能包含尾随空格，因此结果与原始 LRC/ESLRC 文本保持一致。
        """
        return "".join(w.word for w in self.words)

    @property
    def duration(self) -> int | None:
        """本行时长（毫秒），即 ``end_time - start_time``。

        纯文本歌词（时间为 ``None``）返回 ``None``；其它情况返回整数。
        """
        if self.start_time is None or self.end_time is None:
            return None
        return self.end_time - self.start_time

    @property
    def is_word_sync(self) -> bool:
        """是否为逐字同步行。

        纯文本行（``start_time is None``）恒返回 ``False``。
        """
        if self.start_time is None:
            return False
        if not self.words:
            return False
        if len(self.words) > 1:
            return True
        return self.words[0].start_time != self.start_time


@dataclass
class MetadataEntry:
    """元数据条目。"""

    key: str
    values: list[str] = field(default_factory=list)


class Result(TypedDict):
    metadata: list[MetadataEntry]
    lines: list[Line]


# ============================== Lyric ==============================


@dataclass
class Lyric:
    """带元数据的完整歌词集合。

    Attributes:
        lines: 歌词行列表。
        metadata: 元数据条目列表。
    """

    lines: list[Line] = field(default_factory=list)
    metadata: list[MetadataEntry] = field(default_factory=list)

    def __iter__(self) -> Iterator[Line]:
        """遍历所有歌词行。

        支持 ``for line in lyric: ...``；直接返回内部 ``lines`` 的
        迭代器，零拷贝。
        """
        return iter(self.lines)

    def __len__(self) -> int:
        """返回歌词行数量。

        支持 ``len(lyric)``。
        """
        return len(self.lines)

    def __getitem__(self, index: int | slice) -> Line | list[Line]:
        """按索引或切片访问歌词行。

        - **整数索引**（含负数）：返回 :class:`Line`，越界抛 ``IndexError``。
        - **切片**：返回 ``list[Line]``，与 ``list`` 行为一致。

        Args:
            index: 整数下标或 ``slice`` 对象。

        Returns:
            单个 :class:`Line` 或 ``list[Line]``。
        """
        return self.lines[index]

    def __contains__(self, item: object) -> bool:
        """成员判断，支持 ``if line in lyric: ...``。

        由于 :class:`Line` 是 dataclass，比较基于字段值而非对象身份，
        因此下面成立::

            line = lyric[0]
            assert line in lyric       # True
            assert Line(...) in lyric  # 字段相等即为 True
        """
        return item in self.lines

    def __reversed__(self) -> Iterator[Line]:
        """反向遍历，支持 ``for line in reversed(lyric): ...``。"""
        return reversed(self.lines)

    @property
    def is_synced(self) -> bool:
        """是否为"同步歌词"（至少有一行带时间戳）。

        - 空 Lyric → ``False``
        - 纯文本歌词 → ``False``
        - 有时间戳的歌词 → ``True``
        """
        return any(l.start_time is not None for l in self.lines)

    @property
    def is_plain_text(self) -> bool:
        """是否为纯文本歌词（所有行都没有时间戳）。

        - 空 Lyric → ``False``
        - 纯文本歌词 → ``True``
        - 有时间戳（哪怕只有一行）→ ``False``
        """
        return bool(self.lines) and not self.is_synced

    def sort_lines(self) -> "Lyric":
        """按 ``start_time`` 稳定排序；``None`` 排最后。"""
        self.lines.sort(key=lambda l: l.start_time if l.start_time is not None else float("inf"))
        return self

    def to_dict(self) -> dict:
        """转换为普通字典（便于 JSON 序列化等场景）。"""
        return {"lines": self.lines, "metadata": self.metadata}

    def append(self, line: Line, *, sort: bool = True) -> None:
        """在末尾追加一条歌词行，默认自动按 ``start_time`` 排序。

        Args:
            line: 待追加的歌词行。
            sort: 是否自动排序。批量构建时可传 ``False`` 避免每次
                O(n log n) 开销，最后统一调用 :meth:`sort_lines`。
        """
        self.lines.append(line)
        if sort:
            self.sort_lines()

    def extend(self, lines: list[Line], *, sort: bool = True) -> None:
        """在末尾追加多条歌词行，默认自动按 ``start_time`` 排序。

        Args:
            lines: 待追加的歌词行列表。
            sort: 是否自动排序。批量构建时可传 ``False``。
        """
        self.lines.extend(lines)
        if sort:
            self.sort_lines()

    def combine(
        self, other: "Lyric", *, overwrite: bool = False, only_main: bool = True, other_as_refline_only: bool = True
    ) -> "Lyric":
        """把另一个 ``Lyric`` 的文本合并到本歌词的翻译中。

        按 ``other`` 的类型分流：

        - **纯文本歌词**（``other.is_plain_text=True``）：按索引顺序逐行
          绑定翻译；``other`` 行数多于 ``self`` 时，多出的行以纯文本
          ``Line``（``start_time=None``）追加到 ``self`` 末尾。
        - **同步歌词**：按 ``start_time`` 匹配（原逻辑）。

        Args:
            other: 提供翻译文本的 :class:`Lyric`。
            overwrite: 已存在翻译时是否覆盖（默认追加）。
            only_main: 同步 other 时是否跳过 bg 行（纯文本 other 不区分）。
            other_as_refline_only: 同步 other 时是否丢弃无法匹配的孤儿行
                （纯文本 other 的分支不受此参数影响，多出的行总是保留）。

        Returns:
            self（便于链式调用）。

        Raises:
            TypeError: 当 ``other`` 不是 :class:`Lyric` 实例时。
        """
        if not isinstance(other, Lyric):
            raise TypeError(
                f"combine() 的 other 参数必须是 Lyric 对象，"
                f"实际为 {type(other).__name__!r}。"
                f"请先用 Lyric(lines=[...]) 包装原始行列表。"
            )

        # other 是纯文本歌词 → 按索引顺序合并
        if other.is_plain_text:
            return self._combine_plain_text(other, overwrite=overwrite)

        # other 是同步歌词 → 按时序匹配
        grouped: dict[int | float, list[Line]] = {}
        for line in other.lines:
            if only_main and line.is_bg:
                continue
            if line.start_time is None:
                continue
            if not line.text.strip():
                continue
            grouped.setdefault(line.start_time, []).append(line)

        if not grouped:
            return self

        self_times = {line.start_time for line in self.lines if line.start_time is not None}

        for line in self.lines:
            if line.start_time is None:
                continue
            group = grouped.get(line.start_time)
            if not group:
                continue
            combined = "\n".join(l.text for l in group)
            if overwrite or not line.translated_lyric:
                line.translated_lyric = combined
            else:
                line.translated_lyric += "\n" + combined

        if not other_as_refline_only:
            new_lines: list[Line] = []
            for t, group in grouped.items():
                if t in self_times:
                    continue
                new_lines.extend(copy.deepcopy(group))
            if new_lines:
                self.lines.extend(new_lines)
                self.sort_lines()

        return self

    def _combine_plain_text(self, other: "Lyric", *, overwrite: bool = False) -> "Lyric":
        """``other`` 为纯文本歌词时按索引顺序合并。

        规则：

        - 逐行对应：``self.lines[i]`` 的翻译取自 ``other.lines[i].text``；
        - 空文本行（``.strip()==''``）跳过，不覆盖已有翻译；
        - ``other`` 行数多于 ``self`` 时，多出的行 deepcopy 后追加到
          ``self.lines`` 末尾，``start_time`` / ``end_time`` 保持 ``None``。

        Args:
            other: 纯文本歌词（``is_plain_text=True``）。
            overwrite: 已存在翻译时是否覆盖。

        Returns:
            self。
        """
        n_self = len(self.lines)
        n_other = len(other.lines)
        common = min(n_self, n_other)

        for i in range(common):
            text = other.lines[i].text
            if not text.strip():
                continue
            target = self.lines[i]
            if overwrite or not target.translated_lyric:
                target.translated_lyric = text
            else:
                target.translated_lyric += "\n" + text

        if n_other > n_self:
            for i in range(n_self, n_other):
                self.lines.append(copy.deepcopy(other.lines[i]))

        return self

    def __add__(self, other: object) -> "Lyric":
        """``lyric + other``：返回合并了 ``other`` 的**新对象**。

        使用 :meth:`combine` 的默认参数（``overwrite=False``、
        ``only_main=True``、``other_as_refline_only=True``）。原对象不被
        修改。

        Args:
            other: 另一个 :class:`Lyric`。非 :class:`Lyric` 类型返回
                ``NotImplemented``，由 Python 抛出 :class:`TypeError`。

        Returns:
            合并后的新 :class:`Lyric`。

        Example::

            a = parse_lrc_like("[00:01.00]Hello")
            b = parse_lrc_like("[00:01.00]你好")
            c = a + b
            # c[0].translated_lyric == "你好"
            # a[0].translated_lyric == ""
        """
        if not isinstance(other, Lyric):
            return NotImplemented
        new = copy.deepcopy(self)
        new.combine(other)
        return new

    def __radd__(self, other: object) -> "Lyric":
        """支持 ``0 + lyric`` 和 ``sum([lyric1, lyric2, ...])``。

        - ``other == 0``：返回本对象的深拷贝（``sum`` 内部从 0 开始累加）；
        - ``other`` 是 :class:`Lyric`：等价于 ``other + self``；
        - 其它类型返回 ``NotImplemented``。

        Args:
            other: 整数 ``0`` 或另一个 :class:`Lyric`。

        Returns:
            副本或合并后的新 :class:`Lyric`。
        """
        if other == 0:
            return copy.deepcopy(self)
        if isinstance(other, Lyric):
            return other.__add__(self)
        return NotImplemented

    def __iadd__(self, other: object) -> "Lyric":
        """``lyric += other``：就地合并 ``other`` 并返回 self。

        等价于 ``lyric.combine(other)``，使用 :meth:`combine` 的默认参数。

        Args:
            other: 另一个 :class:`Lyric`。非 :class:`Lyric` 类型返回
                ``NotImplemented``，由 Python 抛出 :class:`TypeError`。

        Returns:
            self（就地修改后的对象）。

        Example::

            lyric = parse_lrc_like("[00:01.00]Hello")
            lyric += parse_lrc_like("[00:01.00]你好")
            # lyric[0].translated_lyric == "你好"
        """
        if not isinstance(other, Lyric):
            return NotImplemented
        self.combine(other)
        return self

    # ------------------------------------------------------------------
    # offset 元数据读写
    # ------------------------------------------------------------------

    @property
    def offset(self) -> int:
        """当前 ``offset`` 元数据值（毫秒）。

        读取 :attr:`metadata` 中 ``offset`` 键的整数值；无该键或无法解析
        时返回 0。

        该属性**不影响**行时间戳；若要实际平移时间，请使用
        :meth:`shift` 或 ``<<`` / ``>>`` 运算符。
        """
        for entry in self.metadata:
            if entry.key.lower() == "offset":
                if not entry.values:
                    return 0
                try:
                    return int(entry.values[0])
                except (ValueError, IndexError):
                    return 0
        return 0

    @offset.setter
    def offset(self, value: int) -> None:
        """设置 ``offset`` 元数据（不改变行时间戳）。

        值为 0 时删除该元数据条目，避免序列化时的冗余。

        Args:
            value: 新的 offset 值（毫秒）。
        """
        value = int(value)
        for i, entry in enumerate(self.metadata):
            if entry.key.lower() == "offset":
                if value == 0:
                    del self.metadata[i]
                else:
                    entry.values = [str(value)]
                return
        if value != 0:
            self.metadata.append(MetadataEntry("offset", [str(value)]))

    # ------------------------------------------------------------------
    # 时间平移
    # ------------------------------------------------------------------

    def shift(self, delta_ms: int) -> "Lyric":
        """就地平移所有时间戳；``None`` 保持 ``None``。"""
        if delta_ms == 0:
            return self
        for line in self.lines:
            if line.start_time is not None:
                line.start_time += delta_ms
            if line.end_time is not None:
                line.end_time += delta_ms
            for w in line.words:
                if w.start_time is not None:
                    w.start_time += delta_ms
                if w.end_time is not None:
                    w.end_time += delta_ms
        self.offset = 0
        return self

    def apply_delta(self, delta_ms: int) -> "Lyric":
        """调整整体时间偏移（就地）。

        :meth:`shift` 的别名，命名更贴近用户术语。

        Args:
            delta_ms: 偏移量。正数延后，负数提前。

        Returns:
            self（便于链式调用）。
        """
        return self.shift(delta_ms)

    def __lshift__(self, ms: int) -> "Lyric":
        """``lyric << ms``：返回时间戳提前 ``ms`` 的新对象。

        Args:
            ms: 提前量（毫秒），必须非负。

        Returns:
            平移后的新 :class:`Lyric` 对象。

        Raises:
            ValueError: 当 ``ms`` 不为``int``，或为负时。
        """
        if not isinstance(ms, int) or ms < 0:
            raise ValueError(f"左移量必须是非负数，实际为 {ms!r}")
        new = copy.deepcopy(self)
        new.shift(-ms)
        return new

    def __rshift__(self, ms: int) -> "Lyric":
        """``lyric >> ms``：返回时间戳延后 ``ms`` 的新对象。

        Args:
            ms: 延后量（毫秒），必须非负。

        Returns:
            平移后的新 :class:`Lyric` 对象。

        Raises:
            ValueError: 当 ``ms`` 不为``int``，或为负时。
        """

        if not isinstance(ms, int) or ms < 0:
            raise ValueError(f"右移量必须是非负数，实际为 {ms!r}")
        new = copy.deepcopy(self)
        new.shift(ms)
        return new

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def stringify_lrc(self, options: StringifyOptions | dict | None = None) -> str:
        """序列化为普通 LRC 文本。

        Args:
            ms_digits: 时间戳毫秒位数（2 或 3）。
            include_metadata: 是否输出元数据。
            metadata_keys: 元数据白名单（大小写不敏感）。

        Returns:
            LRC 文本。
        """
        from .lrc import stringify_lrc as _impl

        return _impl(self, options)

    def stringify_eslrc(self, options: StringifyOptions | dict | None = None) -> str:
        """序列化为 Enhanced LRC 文本（逐字使用方括号内联时间戳）。"""
        from .lrc import stringify_eslrc as _impl

        return _impl(self, options)

    def stringify_enhanced_lrc(self, options: StringifyOptions | dict | None = None) -> str:
        """序列化为 Enhanced LRC 文本（逐字使用尖括号内联时间戳）。"""
        from .lrc import stringify_enhanced_lrc as _impl

        return _impl(self, options)

    def stringify_spl(self, options: StringifyOptions | dict | None = None) -> str:
        """序列化为 SPL 文本。"""
        from .lrc import stringify_spl as _impl

        return _impl(self, options)

    def stringify_lrc_like(self, options: StringifyOptions | dict | None = None) -> str:
        """序列化为 LRC 家族文本。"""
        from .lrc import stringify_lrc_like as _impl

        return _impl(self, options)

    def stringify_qrc(self) -> str:
        """序列化为 QRC 文本。

        Raises:
            PlainTextNotSupportedError: 存在无时间戳的行。
        """
        from .qrc import stringify_qrc as _impl

        return _impl(self.lines)

    def stringify_yrc(self) -> str:
        """序列化为 YRC 文本。

        Raises:
            PlainTextNotSupportedError: 存在无时间戳的行。
        """
        from .yrc import stringify_yrc as _impl

        return _impl(self.lines)

    def stringify_ass(self) -> str:
        """序列化为 ASS 字幕文本。

        Raises:
            PlainTextNotSupportedError: 存在无时间戳的行。
        """
        from .ass import stringify_ass as _impl

        return _impl(self.lines)

    def stringify_ttml(self, options: StringifyOptions | dict | None = None) -> str:
        """序列化为 TTML 文本。

        Args:
            options: 生成选项，支持 ``ms_digits``、``include_metadata``、
                ``metadata_keys``。

        Raises:
            PlainTextNotSupportedError: 存在无时间戳的行。
        """
        from .ttml import stringify_ttml as _impl

        return _impl(self, options)
