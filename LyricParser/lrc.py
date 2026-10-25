"""LRC / ESLRC / Enhanced LRC / SPL 解析与生成。

这四种格式共享 LRC 的时间戳语义，仅在"逐字"或"辅助行"的表现上略有差异：

- **LRC（plain）**：``[mm:ss.xx]歌词``，不区分逐字
- **ESLRC**：``[mm:ss.xx]<mm:ss.xx>音节<mm:ss.xx>音节...``（enhanced LRC 标准）
- **Enhanced LRC**：与 ESLRC 相同，但行内时间戳用 ``<>``
- **SPL（Lyricify Syllable）**：也是 enhanced LRC 变体，语法兼容 ESLRC
"""

import dataclasses
import re
from dataclasses import dataclass

from .models import Line, Lyric, MetadataEntry, Word
from .options import ParserOptions, StringifyOptions
from .utils import (
    MAX_LRC_TIMESTAMP,
    clamp_timestamp,
    format_time,
    is_word_sync_lyric,
    normalize_timestamp,
    parse_timestamp_parts,
)

# 行内逐字时间戳（同时支持 [] 和 <>）
WORD_TIMESTAMP_REGEX = re.compile(r"(?:\[|<)(?P<min>\d{1,3}):(?P<sec>\d{1,2})(?:[:.](?P<ms>\d{1,6}))?(?:\]|>)")
# 元数据行：[key:value]
METADATA_REGEX = re.compile(r"^\[\s*(?P<key>[a-zA-Z]+)\s*:\s*(?P<value>.*?)\s*\]$")
# 注释前缀
COMMENT_PREFIXES: list[str] = ["#", "//"]
# 背景人声判定：全角/半角括号
BG_PAREN_BEGIN = re.compile(r"^[(（]")
BG_PAREN_END = re.compile(r"[）)]$")

# ============================== 解析 ==============================


@dataclass
class _Token:
    """词法单元：文本片段或时间戳。

    Attributes:
        type: ``"text"`` 或 ``"time"``。
        val: 文本内容（str）或毫秒时间（int）。
    """

    type: str
    val: str | int


@dataclass
class _LineFeatures:
    """一行歌词在词法分析后的特性。

    Attributes:
        first_text_index: 首个文本 token 的下标；-1 表示无文本。
        is_word_sync: 是否含内联时间戳（即 ``<t>...`` 或 ``[t]...`` 形式）。
        text: 整行拼接后的纯文本（已去除时间戳）。
    """

    first_text_index: int
    is_word_sync: bool
    text: str


def _parse_metadata_line(line: str) -> list[MetadataEntry] | None:
    """尝试将单行解析为元数据。

    Args:
        line: 已 strip 的原始行文本。

    Returns:
        成功时返回仅含一条 :class:`MetadataEntry` 的列表，失败返回 None。
    """
    m = METADATA_REGEX.match(line)
    if not m:
        return None
    key = m.group("key").lower()
    value = m.group("value").strip()
    if not value:
        return None
    return [MetadataEntry(key=key, values=[value])]


def _merge_metadata(target: list[MetadataEntry], source: list[MetadataEntry]) -> None:
    """将 source 合并到 target：同 key 追加值，不同 key 追加条目。"""
    for entry in source:
        for existing in target:
            if existing.key == entry.key:
                for v in entry.values:
                    if v not in existing.values:
                        existing.values.append(v)
                break
        else:
            target.append(MetadataEntry(key=entry.key, values=list(entry.values)))


def _is_bg_text(text: str) -> bool:
    """判断整行文本是否被圆括号包裹（背景人声的弱信号）。"""
    return len(text) > 2 and bool(BG_PAREN_BEGIN.match(text)) and bool(BG_PAREN_END.search(text))


def _strip_parens(words: list[Word]) -> None:
    """就地剥除 words 首尾的括号字符，并清理变空的音节。

    处理 ``[t]<t>（<t>何<t>よ<t>て）<t2>`` 这类形式时，``（`` 会被
    当成独立音节（start==end），剥除后变空需要移除。

    Args:
        words: 音节列表（就地修改）。
    """
    if not words:
        return
    first = words[0].word
    if first and first[0] in "(（":
        words[0].word = first[1:]
    last = words[-1].word
    if last and last[-1] in ")）":
        words[-1].word = last[:-1]
    words[:] = [w for w in words if w.word]


class LrcParser:
    """LRC 家族解析器。

    Args:
        options: :class:`ParserOptions` / ``dict`` / ``None``。
    """

    def __init__(self, options: ParserOptions | dict | None = None) -> None:
        self.options: ParserOptions = ParserOptions.from_dict(options)
        self.mode: str = self.options.mode

    @property
    def _word_level(self) -> bool:
        """是否保留逐字时间戳。``plain`` 之外的任何模式都保留。"""
        return self.mode != "plain"

    def parse(self, text: str) -> Lyric:
        """解析 LRC 文本。

        - **纯文本歌词**（无任何时间戳）：直接按行拆分，时间设为 ``None``；
        - **同步歌词**（含至少一个时间戳）：走完整解析流程。

        Args:
            text: LRC / ESLRC / Enhanced LRC / SPL 文本，或纯文本歌词。

        Returns:
            包含行列表与元数据的 :class:`Lyric` 对象。
        """

        # 纯文本检测：任意位置都没有时间戳 → 直接走纯文本路径
        if WORD_TIMESTAMP_REGEX.search(text) is None:
            return self._parse_plain_text(text)

        metadata: list[MetadataEntry] = []
        all_lines: list[Line] = []

        for raw in re.split(r"\r?\n", text):
            line = raw.strip()
            if not line:
                continue
            if any(line.startswith(p) for p in COMMENT_PREFIXES):
                continue

            parsed_meta = _parse_metadata_line(line)
            if parsed_meta:
                _merge_metadata(metadata, parsed_meta)
                continue

            tokens = self._tokenize(line)
            if not tokens:
                continue

            feats = self._line_features(tokens)
            if feats.text.strip() == "":
                # 显式结束时间行（`[00:05.00]` 空内容）
                self._append_explicit_end_time(tokens, all_lines)
                continue

            new_lines = self._parse_word_sync(tokens, feats) if feats.is_word_sync else self._parse_plain(tokens, feats)
            all_lines.extend(new_lines)

        # 后处理：按 start_time 分组绑定翻译 + 判定 bg
        all_lines = self._bind_translations_and_bg(all_lines)
        all_lines = self._finalize(all_lines)

        return Lyric(lines=all_lines, metadata=metadata)

    def _parse_plain_text(self, text: str) -> Lyric:
        """解析无时间戳的纯文本歌词。

        每行独立成为一条 :class:`Line`：

        - ``start_time`` / ``end_time`` = ``None``
        - 单个音节，``Word.start_time`` / ``end_time`` 也是 ``None``
        - ``[ti:xxx]`` 等元数据行被识别为元数据，**不**成为歌词行

        Args:
            text: 纯文本歌词。

        Returns:
            ``is_synced=False``、``is_plain_text=True`` 的 :class:`Lyric`。
        """
        metadata: list[MetadataEntry] = []
        lines: list[Line] = []

        for raw in re.split(r"\r?\n", text):
            line = raw.strip()
            if not line:
                continue
            if any(line.startswith(p) for p in COMMENT_PREFIXES):
                continue

            parsed_meta = _parse_metadata_line(line)
            if parsed_meta:
                _merge_metadata(metadata, parsed_meta)
                continue

            lines.append(Line(start_time=None, end_time=None, words=[Word(word=line, start_time=None, end_time=None)]))

        return Lyric(lines=lines, metadata=metadata)

    def _tokenize(self, line: str) -> list[_Token]:
        """将一行文本拆分为文本/时间 token 序列。"""
        tokens: list[_Token] = []
        last = 0
        for m in WORD_TIMESTAMP_REGEX.finditer(line):
            before = line[last : m.start()]
            if before:
                tokens.append(_Token(type="text", val=before))
            t = parse_timestamp_parts(m.group("min"), m.group("sec"), m.group("ms") or "")
            tokens.append(_Token(type="time", val=t))
            last = m.end()
        rest = line[last:]
        if rest:
            tokens.append(_Token(type="text", val=rest))
        return tokens

    def _line_features(self, tokens: list[_Token]) -> _LineFeatures:
        """分析 token 序列的整行特性。"""
        first_text = next((i for i, t in enumerate(tokens) if t.type == "text"), -1)
        is_word_sync = False
        if first_text > 0:
            for i in range(first_text + 1, len(tokens)):
                if tokens[i].type == "time":
                    is_word_sync = True
                    break
        text = "".join(str(t.val) for t in tokens if t.type == "text")
        return _LineFeatures(first_text_index=first_text, is_word_sync=is_word_sync, text=text)

    def _append_explicit_end_time(self, tokens: list[_Token], all_lines: list[Line]) -> None:
        """处理"只含时间戳"的行，作为上一行的显式结束时间。"""
        last_time = next((int(t.val) for t in reversed(tokens) if t.type == "time"), None)
        last_line = all_lines[-1] if all_lines else None
        if last_time is None or last_line is None:
            return
        if last_line.end_time != -1 or last_time <= last_line.start_time:
            return
        last_line.end_time = last_time

    def _parse_plain(self, tokens: list[_Token], feats: _LineFeatures) -> list[Line]:
        """解析整行同步行（``[t1][t2]text`` 或 ``[t]翻译``）。

        不区分"歌词 vs 翻译"——绑定由 :meth:`_bind_translations_and_bg`
        在解析完成后统一处理。

        Args:
            tokens: 词法 token 序列。
            feats: 行特性。

        Returns:
            每行一个 ``Line``（多时间戳时一行展开为多条）。
        """
        base_times: list[int] = []
        end = feats.first_text_index if feats.first_text_index != -1 else len(tokens)
        for i in range(end):
            tk = tokens[i]
            if tk.type == "time" and int(tk.val) not in base_times:
                base_times.append(int(tk.val))
        return [
            Line(
                start_time=t,
                end_time=-1,
                words=[Word(word=feats.text, start_time=t, end_time=-1)],
            )
            for t in base_times
        ]

    def _parse_word_sync(self, tokens: list[_Token], feats: _LineFeatures) -> list[Line]:
        """解析带内联时间戳的行（真逐字或整行同步）。

        - **多 text token** → 真逐字，直接作为歌词
        - **单 text token**（``<t1>text<t2>``）→ 整行同步，直接作为歌词

        Args:
            tokens: 词法 token 序列。
            feats: 行特性。

        Returns:
            一条 ``Line``（或空列表表示无法解析）。
        """
        if not tokens or tokens[0].type != "time":
            return []
        if feats.first_text_index < 1 or tokens[feats.first_text_index - 1].type != "time":
            return []

        base_time = int(tokens[0].val)
        syllable_start = int(tokens[feats.first_text_index - 1].val)
        syllables: list[Word] = []
        current_text = ""
        explicit_end: int | None = None
        for i in range(feats.first_text_index, len(tokens)):
            tk = tokens[i]
            if tk.type == "text":
                current_text += str(tk.val)
            else:
                nt = int(tk.val)
                if current_text:
                    syllables.append(Word(word=current_text, start_time=syllable_start, end_time=nt))
                    current_text = ""
                syllable_start = nt
                if i == len(tokens) - 1:
                    explicit_end = nt
        if current_text:
            syllables.append(Word(word=current_text, start_time=syllable_start, end_time=-1))

        end_time = explicit_end if explicit_end is not None else -1
        if is_word_sync_lyric(syllables, base_time):
            return [Line(start_time=base_time, end_time=end_time, words=syllables)]
        return [
            Line(
                start_time=base_time,
                end_time=end_time,
                words=[Word(word=feats.text, start_time=base_time, end_time=-1)],
            )
        ]

    def _finalize(self, all_lines: list[Line]) -> list[Line]:
        """按时间排序、补全结束时间，并根据模式决定是否剥离逐字。"""
        all_lines.sort(key=lambda l: l.start_time)
        self._resolve_end_times(all_lines)
        if not self._word_level:
            self._strip_word_timings(all_lines)
        return all_lines

    def _resolve_end_times(self, all_lines: list[Line]) -> None:
        """为所有 ``end_time == -1`` 的行/音节推导结束时间。

        规则：非末行取下一行开始时间；末行（整行同步）取
        ``MAX_LRC_TIMESTAMP``；末行（逐字）最后一个音节补 1 秒。
        """
        for i, line in enumerate(all_lines):
            is_last = i == len(all_lines) - 1
            if line.end_time == -1:
                if is_last and not is_word_sync_lyric(line.words, line.start_time):
                    line.end_time = MAX_LRC_TIMESTAMP
                elif not is_last:
                    line.end_time = all_lines[i + 1].start_time
            for j, w in enumerate(line.words):
                if w.end_time != -1:
                    continue
                if j + 1 < len(line.words):
                    w.end_time = line.words[j + 1].start_time
                elif is_last and line.end_time == -1:
                    w.end_time = w.start_time + 1000
                    line.end_time = w.end_time
                else:
                    w.end_time = line.end_time

    def _strip_word_timings(self, all_lines: list[Line]) -> None:
        """在 plain 模式下丢弃逐字，仅保留整行单音节。"""
        for line in all_lines:
            if len(line.words) <= 1:
                continue
            line.words = [Word(line.text, line.start_time, line.end_time)]

    def _bind_translations_and_bg(self, all_lines: list[Line]) -> list[Line]:
        """按 ``start_time`` 分组绑定翻译并判定 bg 行。

        分组规则：

        - 同一 ``start_time`` 的行按出现顺序，第 1 行是**歌词行**，
        其余行是**翻译**。
        - 歌词行整行被 ``(...)`` 或 ``（...）`` 包裹时 → ``is_bg=True``，
        剥除首尾括号。
        - 翻译行整行被括号包裹时 → 剥除括号后作为翻译文本

        Args:
            all_lines: 解析阶段产生的全部行（含翻译行）。

        Returns:
            过滤后的行列表（翻译行已并入 ``translated_lyric``，不再独立存在）。
        """
        groups: dict[int, list[Line]] = {}
        order: list[int] = []
        for line in all_lines:
            t = int(line.start_time)
            if t not in groups:
                groups[t] = []
                order.append(t)
            groups[t].append(line)

        result: list[Line] = []
        for t in order:
            group = groups[t]
            main_line = group[0]

            if _is_bg_text(main_line.text):
                main_line.is_bg = True
                _strip_parens(main_line.words)

            translations: list[str] = []
            for trans_line in group[1:]:
                trans_text = trans_line.text
                if len(trans_text) > 2 and trans_text[0] in "(（" and trans_text[-1] in ")）":
                    trans_text = trans_text[1:-1]
                if trans_text.strip():
                    translations.append(trans_text)
            if translations:
                main_line.translated_lyric = "\n".join(translations)

            result.append(main_line)
        return result


# ============================== 生成 ==============================


class LrcGenerator:
    """LRC 家族生成器。

    Args:
        options: :class:`StringifyOptions` / ``dict`` / ``None``。
    """

    def __init__(self, options: StringifyOptions | dict | None = None) -> None:
        self.options: StringifyOptions = StringifyOptions.from_dict(options)

        self.mode: str = self.options.mode
        self.inline_bracket: str = self.options.inline_bracket
        self.ms_digits: int = self.options.ms_digits
        self.include_metadata: bool = self.options.include_metadata

        # LRC 家族的 metadata_keys 按小写比较
        self.metadata_keys: set[str] | None = (
            {k.lower() for k in self.options.metadata_keys} if self.options.metadata_keys is not None else None
        )

    @property
    def _word_level(self) -> bool:
        """是否输出逐字格式。``plain`` 之外都输出。"""
        return self.mode != "plain"

    @property
    def uses_first_word_start(self) -> bool:
        """是否用首个音节的开始时间作为行首时间戳。

        使用 ``[]`` 时（ESLRC 惯例）行首时间戳省略，改用首个音节的
        开始时间；使用 ``<>`` 时用行本身的开始时间。
        """
        return self.inline_bracket == "square"

    @staticmethod
    def _is_metadata_visible(entry: MetadataEntry) -> bool:
        """判断一条元数据是否应输出。

        过滤规则：

        - 键为空
        - 所有值都为空白
        - ``offset`` 键且所有值为 ``0``（视为无意义）
        """
        if not entry.key.strip():
            return False
        vals = [v for v in entry.values if v.strip()]
        if not vals:
            return False
        if entry.key.lower() == "offset" and all(v.strip() == "0" for v in vals):
            return False
        return True

    @staticmethod
    def _wrap_bg_text(text: str) -> str:
        """为背景行的翻译/罗马字包裹半角括号。

        生成统一使用半角 ``(...)``；检测同时兼容全角 ``（...）``，
        避免用户已手动包裹时重复加括号。

        Args:
            text: 翻译/罗马字文本。

        Returns:
            已包裹半角括号的文本；空字符串原样返回。
        """
        s = text.strip()
        if not s:
            return s
        if s.startswith(("(", "（")) and s.endswith((")", "）")):
            return s
        return f"({s})"

    def generate(self, source: Lyric | list[Line]) -> str:
        """将歌词序列化为 LRC 家族文本。

        支持三种形态：

        - **纯文本 Lyric**（所有行 ``start_time=None``）：逐行输出文本，不加时间戳；
        - **同步 Lyric**（所有行带时间戳）：标准 LRC 家族输出；
        - **混合 Lyric**（部分行有时间戳、部分为 ``None``）：逐行分流——
          有时间戳的行按标准逻辑，``None`` 的行原样输出文本。

        Args:
            source: 可以是 :class:`Lyric` 或 ``list[Line]``。

        Returns:
            序列化后的文本。
        """
        if isinstance(source, Lyric):
            metadata = source.metadata
            source_lines = source.lines
        else:
            metadata = []
            source_lines = source

        # 元数据
        meta_lines: list[str] = []
        if self.include_metadata:
            for entry in metadata:
                if self.metadata_keys is not None and entry.key.lower() not in self.metadata_keys:
                    continue
                if not self._is_metadata_visible(entry):
                    continue
                vals = [v for v in entry.values if v.strip()]
                meta_lines.append(f"[{entry.key}:{'/'.join(vals)}]")

        # 全纯文本快速路径
        if source_lines and all(l.start_time is None for l in source_lines):
            lyric_lines: list[str] = [text for line in source_lines if (text := line.text).strip()]
            return self._join_output(meta_lines, lyric_lines)

        # 同步 / 混合：每行独立输出
        normalized = [self._normalize(l) for l in source_lines if l.text.strip()]

        lyric_lines: list[str] = []
        for line in normalized:
            self._process_line(line, lyric_lines)

        return self._join_output(meta_lines, lyric_lines)

    @staticmethod
    def _join_output(meta_lines: list[str], lyric_lines: list[str]) -> str:
        """拼接元数据与歌词行（元数据与歌词间空一行）。"""
        if meta_lines and lyric_lines:
            return "\n".join(meta_lines) + "\n\n" + "\n".join(lyric_lines)
        if meta_lines:
            return "\n".join(meta_lines)
        return "\n".join(lyric_lines)

    @staticmethod
    def _normalize(line: Line) -> Line:
        """归一化行内所有时间字段。"""
        line.start_time = normalize_timestamp(line.start_time)  # type: ignore
        line.end_time = normalize_timestamp(line.end_time)  # type: ignore
        for w in line.words:
            w.start_time = normalize_timestamp(w.start_time)  # type: ignore
            w.end_time = normalize_timestamp(w.end_time)  # type: ignore
        return line

    def _process_line(self, line: Line, out: list[str]) -> None:
        """处理并输出一行（主行 / bg 行 / 纯文本行统一处理）。

        输出顺序：

        1. 行主体（bg 行用括号包裹）
        2. 行的翻译（bg 行的翻译也用括号包裹，``\\n`` 拆多行）
        3. 行的罗马字（同上）

        Args:
            line: 待输出的歌词行。
            out: 输出行列表（就地追加）。
        """
        # 纯文本行：直接输出文本，无时间戳
        if line.start_time is None:
            text = line.text
            if not text.strip():
                return
            if line.is_bg:
                text = f"({text})"
            out.append(text)
            self._append_aux_lines(line, None, out)
            return

        # 有时间戳的行
        line_time = self._tag(line.start_time, "square")
        out.append(self._render_base(line, line.text, line.is_bg))
        self._append_aux_lines(line, line_time, out)

    def _append_aux_lines(self, line: Line, line_time: str | None, out: list[str]) -> None:
        """输出行的翻译 / 罗马字行。

        Args:
            line: 原歌词行（取 ``translated_lyric`` / ``roman_lyric``）。
            line_time: 行时间戳前缀（纯文本行为 ``None``）。
            out: 输出行列表（就地追加）。
        """
        prefix = line_time or ""
        if line.translated_lyric:
            for sub in line.translated_lyric.split("\n"):
                if not sub:
                    continue
                text = self._wrap_bg_text(sub) if line.is_bg else sub
                out.append(f"{prefix}{text}")
        if line.roman_lyric:
            for sub in line.roman_lyric.split("\n"):
                if not sub:
                    continue
                text = self._wrap_bg_text(sub) if line.is_bg else sub
                out.append(f"{prefix}{text}")

    def _render_base(self, item: Line, line_text: str, is_bgv: bool) -> str:
        """渲染一行主体（不含翻译/罗马字辅助行）。

        逐字模式（``word_level=True``）下，**所有行**——包括只有单个音节的
        行——都用 ``[line_t]<w_t>text<w_t>...`` 格式输出。这符合 ESLRC /
        Enhanced LRC / SPL 的规范：句内时间戳一律用 inner bracket 表示。

        整行模式（``word_level=False``）下只输出 ``[line_t]text``。

        Args:
            item: 待渲染的歌词行。
            line_text: 整行文本（已含空格），仅整行模式使用。
            is_bgv: 是否为背景人声，True 时用括号包裹。

        Returns:
            渲染后的行文本。
        """

        # 过滤因剥括号而变空的音节，避免 "(" 成为独立符号
        words = [w for w in item.words if w.word] or item.words

        if self._word_level and words:
            lead = words[0].start_time if self.uses_first_word_start else item.start_time
            buf = self._tag(lead, "square")
            for i, w in enumerate(words):
                if i > 0 or not self.uses_first_word_start:
                    buf += self._tag(w.start_time, self.inline_bracket)
                text = w.word
                if is_bgv:
                    if i == 0:
                        text = f"({text}"
                    if i == len(words) - 1:
                        text = f"{text})"
                buf += text
                if i == len(words) - 1:
                    buf += self._tag(w.end_time, self.inline_bracket)
            return buf

        result = f"{self._tag(item.start_time, 'square')}{'(' + line_text + ')' if is_bgv else line_text}"
        return result

    def _tag(self, ms: int, bracket: str = "square") -> str:
        """将毫秒渲染为时间戳字符串。

        Args:
            ms: 毫秒时间戳。
            bracket: ``"square"`` 输出 ``[t]``，``"angle"`` 输出 ``<t>``。
        """
        content = format_time(int(clamp_timestamp(ms)), self.ms_digits)
        return f"<{content}>" if bracket == "angle" else f"[{content}]"


# ============================== 顶层 API ==============================


def parse_lrc_like(text: str, options: ParserOptions | dict | None = None) -> Lyric:
    """解析 LRC 家族文本（保留元数据）。

    Args:
        text: 输入文本。
        options: 解析选项，``mode`` 键控制逐字保留策略。

    Returns:
        :class:`Lyric` 对象。
    """
    return LrcParser(options).parse(text)


def stringify_lrc_like(source: Lyric | list[Line], options: StringifyOptions | dict | None = None) -> str:
    """序列化为 LRC 家族文本（原样调用 :class:`LrcGenerator`）。

    Args:
        source: :class:`Lyric` 或 ``list[Line]``。
        options: :class:`StringifyOptions` / ``dict`` / ``None``。
            不做任何字段强制，完全以 ``options`` 为准。

    Returns:
        序列化后的文本。
    """

    return LrcGenerator(options).generate(source)


def parse_lrc(text: str, options: ParserOptions | dict | None = None) -> list[Line]:
    """解析普通 LRC 文本，返回行列表。"""
    return parse_lrc_like(text, options).lines


def parse_eslrc(text: str, options: ParserOptions | dict | None = None) -> list[Line]:
    """
    解析 Enhanced LRC 文本，返回行列表（保留逐字）。
    eslrc 使用方括号 `[mm:ss.xx]` 作为逐字内联时间戳。
    """

    return parse_lrc_like(text, options).lines


def parse_enhanced_lrc(text: str, options: ParserOptions | dict | None = None) -> list[Line]:
    """
    解析 Enhanced LRC 文本，返回行列表（保留逐字）。
    Enhanced LRC 使用尖括号 `<mm:ss.xx>` 作为逐字内联时间戳。
    """

    return parse_lrc_like(text, options).lines


def parse_spl(text: str, options: ParserOptions | dict | None = None) -> list[Line]:
    """解析 SPL 文本，返回行列表（保留逐字）。"""
    return parse_lrc_like(text, options).lines


def stringify_lrc(source: Lyric | list[Line], options: StringifyOptions | dict | None = None) -> str:
    """序列化为普通 LRC 文本。

    Args:
        source: 歌词数据。
        ms_digits: 时间戳毫秒位数（2 或 3）。
        include_metadata: 是否输出元数据。
        metadata_keys: 元数据白名单（大小写不敏感）。

    Returns:
        LRC 文本。
    """
    opts = dataclasses.replace(StringifyOptions.from_dict(options), mode="plain")
    return stringify_lrc_like(source, opts)


def stringify_eslrc(source: Lyric | list[Line], options: StringifyOptions | dict | None = None) -> str:
    """序列化为 Enhanced LRC 文本（逐字使用方括号内联时间戳）"""
    opts = dataclasses.replace(StringifyOptions.from_dict(options), mode="enhanced", inline_bracket="square")
    return stringify_lrc_like(source, opts)


def stringify_enhanced_lrc(source: Lyric | list[Line], options: StringifyOptions | dict | None = None) -> str:
    """序列化为 Enhanced LRC 文本（逐字使用尖括号内联时间戳）"""
    opts = dataclasses.replace(StringifyOptions.from_dict(options), mode="enhanced", inline_bracket="angle")
    return stringify_lrc_like(source, opts)


def stringify_spl(source: Lyric | list[Line], options: StringifyOptions | dict | None = None) -> str:
    """序列化为 SPL 文本"""
    opts = dataclasses.replace(StringifyOptions.from_dict(options), mode="enhanced", inline_bracket="angle")
    return stringify_lrc_like(source, opts)
