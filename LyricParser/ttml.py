"""TTML（Apple Music / AMLL 扩展）解析与生成。

支持：
- 标准 TTML 结构（``<tt>``/``<head>``/``<body>``/``<div>``/``<p>``）
- iTunes 扩展：``<iTunesMetadata>`` 下的翻译/罗马字/词曲作者
- AMLL 扩展：``<amll:meta>`` 元数据、``x-bg``/``x-translation``/``x-roman``
  角色、``amll:obscene``/``amll:empty-beat`` 属性、ruby 注音
"""

from itertools import groupby
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import Line, Lyric, MetadataEntry, Ruby, Word
from .options import ParserOptions, StringifyOptions
from .utils import _format_fraction, _validate_ms_digits, ensure_synced

# XML 命名空间
NS = {
    "TT": "http://www.w3.org/ns/ttml",
    "TTM": "http://www.w3.org/ns/ttml#metadata",
    "ITUNES": "http://music.apple.com/lyric-ttml-internal",
    "AMLL": "http://www.example.com/ns/amll",
    "XML": "http://www.w3.org/XML/1998/namespace",
    "TTS": "http://www.w3.org/ns/ttml#styling",
}

# 注册命名空间前缀（"" 表示默认命名空间）
ET.register_namespace("", NS["TT"])
ET.register_namespace("ttm", NS["TTM"])
ET.register_namespace("itunes", NS["ITUNES"])
ET.register_namespace("amll", NS["AMLL"])
ET.register_namespace("tts", NS["TTS"])

# 简化常量
TT, TTM, ITUNES, AMLL, XML, TTS = (NS["TT"], NS["TTM"], NS["ITUNES"], NS["AMLL"], NS["XML"], NS["TTS"])

# 时间字符串解析：支持 h:mm:ss.fff / mm:ss.fff / ss.fff / ss.fffs（秒单位）
TIME_REGEX = re.compile(r"^(?:(?:(?P<hours>\d+):)?(?P<minutes>\d+):)?(?P<seconds>\d+(?:\.\d+)?)$")
LEADING_SPACE = re.compile(r"^\s")
TRAILING_SPACE = re.compile(r"\s$")
MULTI_SPACE = re.compile(r"\s+")


class Values:
    """TTML 字符串常量（对应 XML 中的具体值）。"""

    Word = "Word"
    Line = "Line"
    MusicName = "musicName"
    Artists = "artists"
    Album = "album"
    ISRC = "isrc"
    TTMLAuthorGithub = "ttmlAuthorGithub"
    TTMLAuthorGithubLogin = "ttmlAuthorGithubLogin"
    NCMMusicId = "ncmMusicId"
    QQMusicId = "qqMusicId"
    SpotifyId = "spotifyId"
    AppleMusicId = "appleMusicId"
    RoleBg = "x-bg"
    RoleTranslation = "x-translation"
    RoleRoman = "x-roman"
    Group = "group"
    Person = "person"
    Other = "other"
    Full = "full"
    AgentGroup = "v1000"
    AgentDefault = "v1"
    AgentDefaultDuet = "v2"
    RubyContainer = "container"
    RubyBase = "base"
    RubyTextContainer = "textContainer"
    RubyText = "text"
    TRUE_ = "true"
    TimingMode = "timingMode"
    Language = "language"


# ============================== 内部数据结构 ==============================


@dataclass
class _TTMLAgent:
    """ttm:agent 元素对应的语音角色。

    Attributes:
        id: 角色 id（如 ``"v1"``）。
        name: 角色显示名。
        type: 角色类型（``person``/``group``/``other``）。
    """

    id: str
    name: str | None = None
    type: str = "person"


@dataclass
class _TTMLSubContent:
    """行内/侧栏中的一段子内容（翻译、罗马字等）。

    Attributes:
        text: 文本。
        language: 语言标签（xml:lang）。
        words: 逐字版本，无逐字时为 None。
    """

    text: str = ""
    language: str | None = None
    words: list[Word] | None = None


@dataclass
class _TTMLLine:
    """TTML 中间表示的一行歌词。

    Attributes:
        id: ``itunes:key`` 属性值。
        agent_id: ``ttm:agent`` 属性值。
        song_part: ``itunes:songPart`` 属性值。
        block_index: 该行所在的 ``<div>`` 索引（从 1 起）。
        start_time: 开始时间（毫秒）。
        end_time: 结束时间（毫秒）。
        text: 整行文本。
        words: 逐字音节列表；无逐字时为 None。
        translations: 翻译列表。
        romanizations: 罗马字列表。
        background_vocal: 背景人声行（若有）。
    """

    id: str = ""
    agent_id: str | None = None
    song_part: str | None = None
    block_index: int | None = None
    start_time: int = 0
    end_time: int = 0
    text: str = ""
    words: list[Word] | None = None
    translations: list[_TTMLSubContent] | None = None
    romanizations: list[_TTMLSubContent] | None = None
    background_vocal: "_TTMLLine | None" = None


@dataclass
class _TTMLMetadata:
    """TTML 头的中间表示。

    Attributes:
        title: 标题（可多条）。
        artist: 艺人。
        album: 专辑。
        isrc: ISRC。
        author_ids: 作者 GitHub 用户名。
        author_names: 作者显示名。
        songwriters: 词曲作者。
        agents: 语音角色 id 到角色的映射。
        platform_ids: 平台 id（``ncmMusicId`` 等）。
        raw_properties: 其它未归类的元数据。
        language: 歌词语言（xml:lang）。
        timing_mode: 时序模式（``"Word"`` 或 ``"Line"``）。
    """

    title: list[str] = field(default_factory=list)
    artist: list[str] = field(default_factory=list)
    album: list[str] = field(default_factory=list)
    isrc: list[str] = field(default_factory=list)
    author_ids: list[str] = field(default_factory=list)
    author_names: list[str] = field(default_factory=list)
    songwriters: list[str] = field(default_factory=list)
    agents: dict[str, _TTMLAgent] = field(default_factory=dict)
    platform_ids: dict[str, list[str]] = field(default_factory=dict)
    raw_properties: dict[str, list[str]] = field(default_factory=dict)
    language: str | None = None
    timing_mode: str | None = None


@dataclass
class _TTMLResult:
    """TTML 解析的完整结果。

    Attributes:
        metadata: 元数据。
        lines: 行列表。
    """

    metadata: _TTMLMetadata = field(default_factory=_TTMLMetadata)
    lines: list[_TTMLLine] = field(default_factory=list)


# ============================== 工具函数 ==============================


def _q(ns: str, tag: str) -> str:
    """构造 ElementTree 的命名空间 tag：``{ns}tag``。"""
    return f"{{{ns}}}{tag}"


def _normalize_text(text: str | None, trim: bool = True) -> str:
    """归一化文本：合并连续空白，可选 strip。"""
    if not text:
        return ""
    normalized = MULTI_SPACE.sub(" ", text)
    return normalized.strip() if trim else normalized


def _parse_time(s: str | None) -> int:
    """解析 TTML 时间字符串为毫秒。

    支持：
    - ``"123"`` / ``"12.345"``（纯秒，可含小数）
    - ``"123s"``（秒单位显式标记）
    - ``"12:34"`` / ``"1:23:45.678"``（分:秒、时:分:秒）
    """
    if not (s := (s or "").strip()):
        return 0
    if s.endswith("s"):
        try:
            return round(float(s[:-1]) * 1000)
        except ValueError:
            return 0
    if m := TIME_REGEX.match(s):
        sec = float(m.group("seconds"))
        minute = int(m.group("minutes") or 0)
        hour = int(m.group("hours") or 0)
        return round((hour * 3600 + minute * 60 + sec) * 1000)
    return 0


def _format_time(ms: int, ms_digits: int = 3) -> str:
    """格式化毫秒为 TTML 时间字符串。

    - 有分钟时输出 ``m:ss.xxx``
    - 无分钟时输出 ``s.xxx``

    ``ms_digits`` 支持 1..6；``<=3`` 截断，``>3`` 补 0。
    """
    sec_total, milli = divmod(max(ms, 0), 1000)
    minute, sec = divmod(sec_total, 60)
    frac = _format_fraction(milli, ms_digits)
    return f"{minute}:{sec:02d}.{frac}" if minute else f"{sec}.{frac}"


def _get_attr(elem: ET.Element, ns: str | None, local: str, fallback: str | None = None) -> str | None:
    """读取 XML 属性，兼容命名空间属性、fallback 属性和 local-name 匹配。"""
    if ns is not None and (v := elem.get(_q(ns, local))):
        return v
    if fallback and (v := elem.get(fallback)):
        return v
    for attr_name, attr_val in elem.attrib.items():
        name = attr_name.split("}", 1)[-1] if attr_name.startswith("{") else attr_name
        if name.split(":", 1)[-1] == local:
            return attr_val
    return None


def _by_tag(elem: ET.Element, tag: str) -> list[ET.Element]:
    """按 local-name 匹配所有后代元素。"""
    return [e for e in elem.iter() if e.tag.split("}")[-1] == tag]


def _by_tag_ns(elem: ET.Element, ns: str, tag: str) -> list[ET.Element]:
    """按完整命名空间匹配所有后代元素。"""
    return list(elem.iter(_q(ns, tag)))


def _sort_platform_ids(ids: dict[str, list[str]]) -> dict[str, list[str]]:
    """按 AMLL 约定的优先级重排 platformIds。"""
    preferred = ["ncmMusicId", "qqMusicId", "spotifyId", "appleMusicId"]
    return {
        **{k: ids[k] for k in preferred if k in ids},
        **{k: v for k, v in ids.items() if k not in preferred},
    }


# ============================== 解析实现 ==============================


class _TTMLParser:
    """TTML 内部解析器（面向 _TTMLResult）。"""

    @dataclass
    class _NodeState:
        """``_parse_common_content`` 内的可变状态。"""

        full_text: str = ""
        words: list[Word] = field(default_factory=list)
        translations: list[_TTMLSubContent] = field(default_factory=list)
        romanizations: list[_TTMLSubContent] = field(default_factory=list)
        bg_translations: list[_TTMLSubContent] = field(default_factory=list)
        bg_romanizations: list[_TTMLSubContent] = field(default_factory=list)
        background_vocal: _TTMLLine | None = None

    def __init__(self, options: StringifyOptions | dict | None = None) -> None:
        self.options: StringifyOptions = StringifyOptions.from_dict(options)
        self.ms_digits: int = self.options.ms_digits
        self.include_metadata: bool = self.options.include_metadata
        # TTML 的 metadata_keys 大小写敏感，保留原样
        self.metadata_keys: set[str] | None = (
            set(self.options.metadata_keys) if self.options.metadata_keys is not None else None
        )
        self.use_sidecar: bool = self.options.use_sidecar

    def parse(self, xml: str) -> _TTMLResult:
        """解析 TTML XML 文本。

        Args:
            xml: TTML 字符串。

        Returns:
            ``_TTMLResult`` 对象。

        Raises:
            ValueError: 输入为空或不是字符串。
        """
        if not xml or not isinstance(xml, str):
            raise ValueError("TTMLParser：输入必须是合法的 XML 字符串。")
        doc = ET.fromstring(xml)
        metadata, sidecar = self._parse_head(doc)
        lines: list[_TTMLLine] = []

        if lang := _get_attr(doc, XML, "lang"):
            metadata.language = lang
        if (timing := _get_attr(doc, ITUNES, "timing")) in (Values.Word, Values.Line):
            metadata.timing_mode = timing

        self._parse_body(doc, lines, sidecar)
        metadata.timing_mode = metadata.timing_mode or self._infer_timing(lines)
        if metadata.platform_ids:
            metadata.platform_ids = _sort_platform_ids(metadata.platform_ids)
        return _TTMLResult(metadata=metadata, lines=lines)

    def _infer_timing(self, lines: list[_TTMLLine]) -> str:
        """未显式声明时，根据是否有逐字推断 timingMode。"""
        for line in lines:
            bg = line.background_vocal
            if len(line.words) > 1 or (bg is not None and len(bg.words) > 1):
                return "Word"
        return "Line"

    def _parse_head(self, doc: ET.Element) -> tuple[_TTMLMetadata, dict[str, dict[str, list[_TTMLSubContent]]]]:
        """解析 head，返回 (metadata, sidecar)。

        sidecar 是 ``行 id -> {translations/romanizations: [...]}`` 的映射，
        用于把 iTunesMetadata 中的翻译/罗马字挂回主行。
        """
        meta = _TTMLMetadata()
        sidecar: dict[str, dict[str, list[_TTMLSubContent]]] = {}
        head = next((e for e in doc.iter() if e.tag == _q(TT, "head")), None)
        if head is None:
            return meta, sidecar

        for title in _by_tag_ns(head, TTM, "title"):
            if title.text:
                meta.title.append(title.text.strip())
        for agent in _by_tag_ns(head, TTM, "agent"):
            if not (aid := _get_attr(agent, XML, "id")):
                continue
            a = _TTMLAgent(id=aid, type=_get_attr(agent, TTM, "type") or "person")
            if (names := _by_tag_ns(agent, TTM, "name")) and (nm := (names[0].text or "").strip()):
                a.name = nm
            meta.agents[aid] = a

        for el in _by_tag_ns(head, AMLL, "meta"):
            key = _get_attr(el, AMLL, "key")
            val = _get_attr(el, AMLL, "value").strip()
            if not key or not val:
                continue
            if key == Values.MusicName:
                meta.title.append(val)
            elif key == Values.Artists:
                meta.artist.append(val)
            elif key == Values.Album:
                meta.album.append(val)
            elif key == Values.ISRC:
                meta.isrc.append(val)
            elif key == Values.TTMLAuthorGithub:
                meta.author_ids.append(val)
            elif key == Values.TTMLAuthorGithubLogin:
                meta.author_names.append(val)
            elif key in (Values.NCMMusicId, Values.QQMusicId, Values.SpotifyId, Values.AppleMusicId):
                meta.platform_ids.setdefault(key, []).append(val)
            else:
                meta.raw_properties.setdefault(key, []).append(val)

        self._parse_itunes(head, meta, sidecar)

        for name in ("title", "artist", "album", "isrc", "author_ids", "author_names", "songwriters"):
            setattr(meta, name, list(dict.fromkeys(getattr(meta, name))))
        for mapping in (meta.platform_ids, meta.raw_properties):
            for k, v in mapping.items():
                mapping[k] = list(dict.fromkeys(v))

        return meta, sidecar

    def _parse_itunes(
        self, head: ET.Element, meta: _TTMLMetadata, sidecar: dict[str, dict[str, list[_TTMLSubContent]]]
    ) -> None:
        """解析 ``<iTunesMetadata>`` 下的扩展元素。"""
        for itunes in _by_tag(head, "iTunesMetadata"):
            if sw := _by_tag(itunes, "songwriters"):
                for w in _by_tag(sw[0], "songwriter"):
                    if w.text:
                        meta.songwriters.append(w.text.strip())

            for container_tag, item_tag, key, bg_key in (
                ("translations", "translation", "translations", "bgTranslations"),
                ("transliterations", "transliteration", "romanizations", "bgRomanizations"),
            ):
                if not (container := _by_tag(itunes, container_tag)):
                    continue
                for item in _by_tag(container[0], item_tag):
                    lang = _get_attr(item, XML, "lang")
                    for text_node in _by_tag(item, "text"):
                        if not (fid := text_node.get("for")):
                            continue
                        sub = self._extract_sub_content(self._parse_common_content(text_node), lang, False)
                        entry = sidecar.setdefault(fid, {})
                        if sub.get("main"):
                            entry.setdefault(key, []).append(sub["main"])
                        if sub.get("bg"):
                            entry.setdefault(bg_key, []).append(sub["bg"])

    def _extract_sub_content(
        self, base: _TTMLLine, lang: str | None, ignore_words: bool = False
    ) -> dict[str, _TTMLSubContent]:
        """从行对象提取主/背景子内容，用于 sidecar 展开。"""
        result: dict[str, _TTMLSubContent] = {}
        for key, src in (("main", base), ("bg", base.background_vocal)):
            if src is None:
                continue
            text = _normalize_text(src.text)
            words = src.words if not ignore_words else None
            if not (text or words):
                continue
            sub = _TTMLSubContent(text=text, language=lang)
            if words and not (len(words) == 1 and words[0].start_time == words[0].end_time == 0):
                sub.words = words
            result[key] = sub
        return result

    def _parse_body(
        self, doc: ET.Element, lines: list[_TTMLLine], sidecar: dict[str, dict[str, list[_TTMLSubContent]]]
    ) -> None:
        """解析 body 中的 div/p 结构。"""
        if (body := next((e for e in doc.iter() if e.tag == _q(TT, "body")), None)) is None:
            return
        block_index = 0
        for el in body:
            tag = el.tag.split("}")[-1]
            if tag not in ("div", "p"):
                continue
            block_index += 1
            if tag == "div":
                song_part = _get_attr(el, ITUNES, "song-part") or _get_attr(el, ITUNES, "songPart")
                nodes = _by_tag_ns(el, TT, "p") or _by_tag(el, "p")
            else:
                song_part, nodes = None, [el]
            for p in nodes:
                self._process_line(p, lines, sidecar, song_part, block_index)

    def _process_line(
        self,
        p: ET.Element,
        lines: list[_TTMLLine],
        sidecar: dict[str, dict[str, list[_TTMLSubContent]]],
        song_part: str | None,
        block_index: int,
    ) -> None:
        """处理单个 ``<p>``：解析内容并挂载 sidecar 中的翻译/罗马字。"""
        if not (lid := _get_attr(p, ITUNES, "key")):
            return
        content = self._parse_common_content(p)
        content.id = lid
        content.song_part = song_part or content.song_part
        content.block_index = block_index
        content.agent_id = _get_attr(p, TTM, "agent") or content.agent_id
        if ext := sidecar.get(lid):
            if vals := ext.get("translations"):
                content.translations = (content.translations or []) + vals
            if vals := ext.get("romanizations"):
                content.romanizations = (content.romanizations or []) + vals
            if bg := content.background_vocal:
                if vals := ext.get("bgTranslations"):
                    bg.translations = (bg.translations or []) + vals
                if vals := ext.get("bgRomanizations"):
                    bg.romanizations = (bg.romanizations or []) + vals
        lines.append(content)

    def _parse_common_content(self, element: ET.Element) -> _TTMLLine:
        """解析元素主体（文本 + 音节 + 角色标记）。"""
        begin = _get_attr(element, XML, "begin") or element.get("begin")
        end = _get_attr(element, XML, "end") or element.get("end")
        orig_start = _parse_time(begin)
        orig_end = _parse_time(end)

        state = self._extract_node_state(element)
        if bg := state.background_vocal:
            if state.bg_translations:
                bg.translations = (bg.translations or []) + state.bg_translations
            if state.bg_romanizations:
                bg.romanizations = (bg.romanizations or []) + state.bg_romanizations
        _finalize_words(state.words)

        start, end_time = self._calc_time(orig_start, orig_end, state.words, state.background_vocal)
        clean = _normalize_text(state.full_text)
        has_attrs = begin is not None or end is not None
        self._apply_fallback(state.words, clean, has_attrs, orig_start, orig_end, start, end_time)

        return _TTMLLine(
            start_time=start,
            end_time=end_time,
            text=clean,
            words=state.words or None,
            translations=state.translations or None,
            romanizations=state.romanizations or None,
            background_vocal=state.background_vocal,
        )

    def _extract_node_state(self, element: ET.Element) -> "_TTMLParser._NodeState":
        """遍历元素（含文本节点）收集文本、音节、角色内容。"""
        state = self._NodeState()
        if element.text:
            self._process_text(state, element.text)
        for child in list(element):
            self._process_element(state, child)
            if child.tail:
                self._process_text(state, child.tail)
        return state

    def _process_text(self, state: "_TTMLParser._NodeState", raw_text: str) -> None:
        """处理纯文本节点（仅累加至 fullText）。"""
        if "\n" in raw_text and not raw_text.strip():
            return
        state.full_text += _normalize_text(raw_text, trim=False)

    def _process_element(self, state: "_TTMLParser._NodeState", el: ET.Element) -> None:
        """根据 role/ruby 属性分发元素处理。"""
        if _get_attr(el, TTS, "ruby") == Values.RubyContainer:
            self._process_ruby(state, el)
            return
        role = _get_attr(el, TTM, "role")
        if role == Values.RoleBg:
            state.background_vocal = self._parse_bg(el)
            return
        for role_val, main_list, bg_list in (
            (Values.RoleTranslation, state.translations, state.bg_translations),
            (Values.RoleRoman, state.romanizations, state.bg_romanizations),
        ):
            if role == role_val:
                if sub := self._parse_inline_sub(el):
                    if sub.get("main"):
                        main_list.append(sub["main"])
                    if sub.get("bg"):
                        bg_list.append(sub["bg"])
                return
        self._process_word(state, el)

    def _process_ruby(self, state: "_TTMLParser._NodeState", container: ET.Element) -> None:
        """解析 ruby 容器（base + 一组带时间的 rt）。"""
        is_obscene = _get_attr(container, AMLL, "obscene") == "true"
        eb = _get_attr(container, AMLL, "empty-beat")
        empty_beat = int(eb) if eb and eb.isdigit() else None

        base_text = ""
        ruby_tags: list[Ruby] = []
        for child in container:
            ruby_attr = _get_attr(child, TTS, "ruby")
            if ruby_attr == Values.RubyBase:
                base_text = _normalize_text(child.text, trim=False)
            elif ruby_attr == Values.RubyTextContainer:
                for rt in child:
                    if _get_attr(rt, TTS, "ruby") != Values.RubyText:
                        continue
                    b = _get_attr(rt, XML, "begin")
                    e = _get_attr(rt, XML, "end")
                    txt = _normalize_text(rt.text, trim=False).strip()
                    if txt and b and e:
                        ruby_tags.append(Ruby(word=txt, start_time=_parse_time(b), end_time=_parse_time(e)))

        if not base_text:
            return
        state.full_text += base_text
        if clean := base_text.strip():
            state.words.append(
                Word(
                    word=clean,
                    start_time=min((r.start_time for r in ruby_tags), default=0),
                    end_time=max((r.end_time for r in ruby_tags), default=0),
                    ruby=ruby_tags or None,
                    obscene=is_obscene or None,
                    empty_beat=empty_beat,
                )
            )

    def _process_word(self, state: "_TTMLParser._NodeState", el: ET.Element) -> None:
        """处理普通 span 音节（含 begin/end 时视为音节）。"""
        wb = _get_attr(el, XML, "begin")
        we = _get_attr(el, XML, "end")
        is_obscene = _get_attr(el, AMLL, "obscene") == "true"
        eb = _get_attr(el, AMLL, "empty-beat")
        empty_beat = int(eb) if eb and eb.isdigit() else None
        norm = _normalize_text(el.text, trim=False)
        state.full_text += norm
        if wb and we and (clean := norm.strip()):
            state.words.append(
                Word(
                    word=clean,
                    start_time=_parse_time(wb),
                    end_time=_parse_time(we),
                    obscene=is_obscene or None,
                    empty_beat=empty_beat,
                )
            )
        for child in el:
            self._process_element(state, child)
            if child.tail:
                self._process_text(state, child.tail)

    def _parse_bg(self, el: ET.Element) -> _TTMLLine:
        """解析背景人声元素，去掉首尾括号。"""
        content = self._parse_common_content(el)
        content.background_vocal = None
        content.text = re.sub(r"^[(（]+|[)）]+$", "", content.text)
        if words := content.words:
            words[0].word = re.sub(r"^[(（]+", "", words[0].word).lstrip()
            words[-1].word = re.sub(r"[)）]+$", "", words[-1].word).rstrip()
        return content

    def _parse_inline_sub(self, el: ET.Element) -> dict[str, _TTMLSubContent] | None:
        """解析行内 translation/roman 角色。"""
        lang = _get_attr(el, XML, "lang")
        content = self._extract_sub_content(self._parse_common_content(el), lang, True)
        return content or None

    def _calc_time(self, orig_start: int, orig_end: int, words: list[Word], bg: _TTMLLine | None) -> tuple[int, int]:
        """当行无显式时间时，根据子节点时间推导行时间。"""
        timed = [*words, bg] if bg else words
        if not timed:
            return orig_start, orig_end
        min_start = min(t.start_time for t in timed)
        max_end = max(t.end_time for t in timed)
        start = min_start if orig_start == 0 or 0 < min_start < orig_start else orig_start
        return start, max(orig_end, max_end)

    def _apply_fallback(
        self,
        words: list[Word],
        clean_text: str,
        has_attrs: bool,
        orig_start: int,
        orig_end: int,
        calc_start: int,
        calc_end: int,
    ) -> None:
        """当一行没有任何 span 但有文本和时间属性时，构造一个整行音节。"""
        if not words and clean_text and has_attrs:
            words.append(
                Word(
                    word=clean_text,
                    start_time=orig_start if orig_start > 0 else calc_start,
                    end_time=orig_end if orig_end > 0 else calc_end,
                )
            )


def _finalize_words(words: list[Word]) -> None:
    """去掉首尾音节的空白字符。"""
    if not words:
        return
    words[0].word = words[0].word.lstrip()
    words[-1].word = words[-1].word.rstrip()


# ============================== 生成实现 ==============================


class _TTMLGenerator:
    """TTML 内部生成器（面向 _TTMLResult）。"""

    def __init__(self, options: StringifyOptions | dict | None = None) -> None:
        self.options = StringifyOptions.from_dict(options)
        self.ms_digits = self.options.ms_digits
        self.include_metadata = self.options.include_metadata
        self.metadata_keys = self.options.metadata_keys

    def _fmt(self, ms: int) -> str:
        """以当前 ms_digits 格式化时间。"""
        return _format_time(ms, self.ms_digits)

    def generate(self, result: _TTMLResult) -> str:
        """序列化为 TTML 字符串。"""
        meta = result.metadata
        lines = result.lines

        if not all(line.id for line in lines):
            for i, line in enumerate(lines, 1):
                line.id = f"L{i}"
        for line in lines:
            line.agent_id = line.agent_id or Values.AgentDefault

        root = ET.Element(_q(TT, "tt"))
        if meta.language:
            root.set(_q(XML, "lang"), meta.language)
        root.set(_q(ITUNES, "timing"), meta.timing_mode or "Word")

        if self.include_metadata:
            root.append(self._build_head(result))
        root.append(self._build_body(result))

        return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode", xml_declaration=False)

    def _build_head(self, result: _TTMLResult) -> ET.Element:
        """构建 ``<head>``：agents + iTunesMetadata + amll:meta。"""
        meta = result.metadata
        head = ET.Element(_q(TT, "head"))
        metadata = ET.SubElement(head, _q(TTM, "metadata"))

        agents = meta.agents or {
            aid: _TTMLAgent(
                id=aid,
                type=Values.Group if aid == Values.AgentGroup else Values.Person,
            )
            for aid in {line.agent_id for line in result.lines if line.agent_id}
        }
        for aid, agent in agents.items():
            el = ET.SubElement(metadata, _q(TTM, "agent"))
            el.set("type", agent.type)
            el.set(_q(XML, "id"), aid)
            if agent.name:
                name_el = ET.SubElement(el, _q(TTM, "name"))
                name_el.set("type", Values.Full)
                name_el.text = agent.name

        self._build_itunes_metadata(metadata, result)

        for key, values in (
            (Values.MusicName, meta.title),
            (Values.Artists, meta.artist),
            (Values.Album, meta.album),
            (Values.ISRC, meta.isrc),
            (Values.TTMLAuthorGithub, meta.author_ids),
            (Values.TTMLAuthorGithubLogin, meta.author_names),
            *meta.platform_ids.items(),
            *meta.raw_properties.items(),
        ):
            for value in values:
                if not (stripped := value.strip()):
                    continue
                if key.lower() == "offset" and stripped == "0":
                    continue
                if self.metadata_keys is not None and key not in self.metadata_keys:
                    continue
                el = ET.SubElement(metadata, _q(AMLL, "meta"))
                el.set("key", key)
                el.set("value", value)
        return head

    def _should_sidecar(self, content: _TTMLSubContent) -> bool:
        """判断子内容是否走侧栏（有逐字或用户强制）。"""
        return bool(content.words) or self.use_sidecar

    def _build_itunes_metadata(self, metadata_el: ET.Element, result: _TTMLResult) -> None:
        """构建 ``<iTunesMetadata>``：翻译、罗马字、词曲作者。"""
        trans_map: dict[str | None, list[dict[str, Any]]] = {}
        roman_map: dict[str | None, list[dict[str, Any]]] = {}
        for attr, out in (("translations", trans_map), ("romanizations", roman_map)):
            for line in result.lines:
                bg = line.background_vocal
                pairs = self._pair_sub_contents(getattr(line, attr), getattr(bg, attr) if bg else None)
                for pair in pairs:
                    if (pair.get("main") and self._should_sidecar(pair["main"])) or (
                        pair.get("bg") and self._should_sidecar(pair["bg"])
                    ):
                        out.setdefault(pair["lang"], []).append(
                            {"id": line.id, "main": pair.get("main"), "bg": pair.get("bg")}
                        )

        songwriters = result.metadata.songwriters
        if not (trans_map or roman_map or songwriters):
            return

        itunes = ET.SubElement(metadata_el, _q(ITUNES, "iTunesMetadata"))
        itunes.set("xmlns", NS["ITUNES"])

        for tag, container_tag, mapping in (
            ("translation", "translations", trans_map),
            ("transliteration", "transliterations", roman_map),
        ):
            if not mapping:
                continue
            container = ET.SubElement(itunes, container_tag)
            for lang, items in mapping.items():
                el = ET.SubElement(container, tag)
                if lang:
                    el.set(_q(XML, "lang"), lang)
                for item in items:
                    text_el = ET.SubElement(el, "text")
                    text_el.set("for", item["id"])
                    if item.get("main"):
                        self._append_sub_content(text_el, item["main"])
                    if item.get("bg"):
                        self._append_bg(text_el, item["bg"])

        if songwriters:
            container = ET.SubElement(itunes, "songwriters")
            for name in songwriters:
                ET.SubElement(container, "songwriter").text = name

    def _pair_sub_contents(
        self, main_list: list[_TTMLSubContent] | None, bg_list: list[_TTMLSubContent] | None
    ) -> list[dict[str, Any]]:
        """按语言把主/背景翻译配对。"""
        out: dict[str | None, dict[str, Any]] = {}
        for key, items in (("main", main_list), ("bg", bg_list)):
            for item in items or ():
                out.setdefault(item.language, {"lang": item.language})[key] = item
        return list(out.values())

    def _build_body(self, result: _TTMLResult) -> ET.Element:
        """构建 ``<body>``，按 song_part/block_index 分组。"""
        lines = result.lines
        body = ET.Element(_q(TT, "body"), dur=self._fmt(max((l.end_time for l in lines), default=0)))

        for (part, _), group in groupby(lines, key=lambda l: (l.song_part, l.block_index)):
            items = list(group)
            div = ET.SubElement(body, _q(TT, "div"))
            div.set("begin", self._fmt(items[0].start_time))
            if (section_end := max(l.end_time for l in items)) > 0:
                div.set("end", self._fmt(section_end))
            if part:
                div.set(_q(ITUNES, "songPart"), part)
            for line in items:
                p = ET.SubElement(div, _q(TT, "p"))
                p.set("begin", self._fmt(line.start_time))
                p.set("end", self._fmt(line.end_time))
                p.set(_q(ITUNES, "key"), line.id)
                if line.agent_id:
                    p.set(_q(TTM, "agent"), line.agent_id)
                self._append_line_content(p, line)
        return body

    def _append_line_content(self, element: ET.Element, content: _TTMLLine) -> None:
        """向 ``<p>`` 或嵌套 span 追加文本、音节、翻译、罗马字、bg。"""
        self._append_sub_content(element, content)

        for items, role in (
            (content.translations, Values.RoleTranslation),
            (content.romanizations, Values.RoleRoman),
        ):
            for sub in items or ():
                if self._should_sidecar(sub):
                    continue
                span = ET.SubElement(element, _q(TT, "span"))
                span.set(_q(TTM, "role"), role)
                if sub.language:
                    span.set(_q(XML, "lang"), sub.language)
                self._append_sub_content(span, sub)

        if content.background_vocal:
            self._append_bg(element, content.background_vocal)

    def _append_sub_content(self, element: ET.Element, content: _TTMLSubContent) -> None:
        """追加一段子内容"""
        if content.words:
            self._append_words(element, content.words, False)
        else:
            element.text = (element.text or "") + (content.text or "")

    def _append_bg(self, element: ET.Element, bg: _TTMLLine) -> None:
        """追加一段背景人声 span"""
        span = ET.SubElement(element, _q(TT, "span"))
        span.set(_q(TTM, "role"), Values.RoleBg)
        if bg.start_time > 0 and bg.end_time > 0:
            span.set("begin", self._fmt(bg.start_time))
            span.set("end", self._fmt(bg.end_time))
        self._append_line_content(span, bg)

    def _append_words(self, element: ET.Element, words: list[Word], is_bg: bool) -> None:
        """追加音节序列（含 ruby 支持）"""
        last = len(words) - 1
        for i, syl in enumerate(words):
            text = syl.word
            if is_bg and i == 0:
                text = f"({text}"
            if is_bg and i == last:
                text = f"{text})"

            outer = ET.SubElement(element, _q(TT, "span"))
            if syl.obscene:
                outer.set(_q(AMLL, "obscene"), Values.TRUE_)
            if syl.empty_beat is not None:
                outer.set(_q(AMLL, "empty-beat"), str(syl.empty_beat))

            if not syl.ruby:
                outer.set("begin", self._fmt(syl.start_time))
                outer.set("end", self._fmt(syl.end_time))
                outer.text = text
                continue

            outer.set(_q(TTS, "ruby"), Values.RubyContainer)
            base = ET.SubElement(outer, _q(TT, "span"))
            base.set(_q(TTS, "ruby"), Values.RubyBase)
            base.text = text
            tc = ET.SubElement(outer, _q(TT, "span"))
            tc.set(_q(TTS, "ruby"), Values.RubyTextContainer)
            for r in syl.ruby:
                rt = ET.SubElement(tc, _q(TT, "span"))
                rt.set(_q(TTS, "ruby"), Values.RubyText)
                rt.set("begin", self._fmt(r.start_time))
                rt.set("end", self._fmt(r.end_time))
                rt.text = r.word


# ============================== 转换 ==============================


def _to_lyrics(result: _TTMLResult, options: ParserOptions | dict | None = None) -> Lyric:
    """将 ``_TTMLResult`` 转换为公共 ``Lyric`` 模型。

    Args:
        result: 内部解析结果。
        options: :class:`ParserOptions` / ``dict`` / ``None``。
            ``translation_language`` / ``romanization_language``
            指定优选的翻译/罗马字语言。

    Returns:
        公共 :class:`Lyric` 对象。
    """

    opts = ParserOptions.from_dict(options)

    def convert(source: _TTMLLine, is_bg: bool, is_duet: bool) -> Line:
        if source.words:
            words = [
                Word(
                    word=w.word,
                    start_time=w.start_time,
                    end_time=w.end_time,
                    roman_word="",
                    obscene=w.obscene,
                    empty_beat=w.empty_beat,
                    ruby=w.ruby,
                )
                for w in source.words
            ]
        else:
            words = [Word(word=source.text, start_time=source.start_time, end_time=source.end_time)]

        trans_text = ""
        if source.translations:
            trans_text = next(
                (t for t in source.translations if t.language == opts.translation_language),
                source.translations[0],
            ).text

        roman_text = ""
        roman_words: list[Word] | None = None
        if source.romanizations:
            target = next(
                (r for r in source.romanizations if r.language == opts.romanization_language), source.romanizations[0]
            )
            roman_words = target.words
            if not roman_words:
                roman_text = target.text
        if roman_words and words:
            _align_romanization(words, roman_words)

        return Line(
            words=words,
            translated_lyric=trans_text,
            roman_lyric=roman_text,
            is_bg=is_bg,
            is_duet=is_duet,
            start_time=source.start_time,
            end_time=source.end_time,
        )

    agents = result.metadata.agents
    lines: list[Line] = []
    last_person: str | None = None
    last_person_duet = False
    for line in result.lines:
        aid = line.agent_id or Values.AgentDefault
        agent = agents.get(aid)
        agent_type = agent.type if agent else None

        current_duet = False
        if agent_type != Values.Group:
            if last_person is None:
                current_duet = last_person_duet = agent_type == Values.Other
                last_person = aid
            elif last_person != aid:
                current_duet = last_person_duet = not last_person_duet
                last_person = aid
            else:
                current_duet = last_person_duet

        lines.append(convert(line, False, current_duet))
        if line.background_vocal:
            lines.append(convert(line.background_vocal, True, current_duet))

    meta = result.metadata
    metadata = [
        MetadataEntry(key, list(values))
        for key, values in (
            (Values.MusicName, meta.title),
            (Values.Artists, meta.artist),
            (Values.Album, meta.album),
            (Values.ISRC, meta.isrc),
            (Values.TTMLAuthorGithub, meta.author_ids),
            (Values.TTMLAuthorGithubLogin, meta.author_names),
            (Values.Language, [meta.language] if meta.language else None),
            (Values.TimingMode, [meta.timing_mode] if meta.timing_mode else None),
            ("songwriters", meta.songwriters),
            *(
                (k, meta.platform_ids[k])
                for k in ("ncmMusicId", "qqMusicId", "spotifyId", "appleMusicId")
                if meta.platform_ids.get(k)
            ),
            *meta.raw_properties.items(),
        )
        if values
    ]

    return Lyric(metadata=metadata, lines=lines)


def _align_romanization(words: list[Word], roman_words: list[Word]) -> None:
    """把罗马字按时间对齐到主音节。

    先用精确时间快速匹配，失败则用区间 IoU 做最佳匹配。
    """
    MIN_IOU = 0.1
    FAST_TOL = 2
    search_start = 0
    for w in words:
        w_start, w_end = w.start_time, w.end_time
        best_iou = 0.0
        best_idx = -1
        fast = False
        for j in range(search_start, len(roman_words)):
            sub = roman_words[j]
            sub_start = sub.start_time
            if abs(w_start - sub_start) <= FAST_TOL:
                w.roman_word = sub.word
                search_start = j + 1
                fast = True
                break
            overlap_start = max(w_start, sub_start)
            overlap_end = min(w_end, sub.end_time)
            if overlap_start < overlap_end:
                union_start = min(w_start, sub_start)
                union_end = max(w_end, sub.end_time)
                iou = (overlap_end - overlap_start) / max(1, union_end - union_start)
                if iou > best_iou:
                    best_iou = iou
                    best_idx = j
            if sub_start >= w_end:
                break
        if not fast and best_idx != -1 and best_iou >= MIN_IOU:
            w.roman_word = roman_words[best_idx].word
            search_start = best_idx + 1


def _to_ttml_result(lyric: Lyric, options: ParserOptions | dict | None = None) -> _TTMLResult:
    """将公共 ``Lyric`` 转换为 ``_TTMLResult``。

    Args:
        lyric: 公共歌词对象。
        options: 支持 ``translation_language`` / ``romanization_language``。

    Returns:
        ``_TTMLResult`` 对象。
    """
    opts = ParserOptions.from_dict(options or {"translation_language": "zh-Hans"})

    metadata = _TTMLMetadata()
    for aid in (Values.AgentDefault, Values.AgentDefaultDuet):
        metadata.agents[aid] = _TTMLAgent(id=aid, type=Values.Person)

    for entry in lyric.metadata:
        if not entry.values:
            continue
        key = entry.key
        if key == Values.MusicName:
            metadata.title = list(entry.values)
        elif key == Values.Artists:
            metadata.artist = list(entry.values)
        elif key == Values.Album:
            metadata.album = list(entry.values)
        elif key == Values.ISRC:
            metadata.isrc = list(entry.values)
        elif key == Values.TTMLAuthorGithub:
            metadata.author_ids = list(entry.values)
        elif key == Values.TTMLAuthorGithubLogin:
            metadata.author_names = list(entry.values)
        elif key in (Values.NCMMusicId, Values.QQMusicId, Values.SpotifyId, Values.AppleMusicId):
            metadata.platform_ids[key] = list(entry.values)
        elif key == "songwriters":
            metadata.songwriters = list(entry.values)
        elif key == Values.Language:
            metadata.language = entry.values[0]
        elif key == Values.TimingMode:
            metadata.timing_mode = entry.values[0]
        else:
            metadata.raw_properties[key] = list(entry.values)

    result_lines: list[_TTMLLine] = []
    current_main: _TTMLLine | None = None

    for line in lyric.lines:
        main_words, roman_words, full_text, roman_text = _convert_words(line)
        ttml_line = _TTMLLine(
            start_time=line.start_time,
            end_time=line.end_time,
            text=full_text,
            words=main_words,
            translations=(
                [_TTMLSubContent(text=line.translated_lyric, language=opts.translation_language)]
                if line.translated_lyric
                else None
            ),
            romanizations=(
                [
                    _TTMLSubContent(
                        text=line.roman_lyric or roman_text,
                        language=opts.romanization_language,
                        words=roman_words or None,
                    )
                ]
                if (line.roman_lyric or roman_words)
                else None
            ),
        )

        if line.is_bg:
            if current_main is not None and not current_main.background_vocal:
                current_main.background_vocal = ttml_line
                continue
            ttml_line.agent_id = current_main.agent_id if current_main else Values.AgentDefault
        else:
            ttml_line.agent_id = Values.AgentDefaultDuet if line.is_duet else Values.AgentDefault
            current_main = ttml_line
        result_lines.append(ttml_line)

    return _TTMLResult(metadata=metadata, lines=result_lines)


def _convert_words(line: Line) -> tuple[list[Word], list[Word], str, str]:
    """将公共 Line 的音节转为 TTML 音节（保留尾随空格信息）。

    Returns:
        ``(主音节, 罗马音节, 整行文本, 罗马文本)``
    """
    main_words: list[Word] = []
    roman_words: list[Word] = []
    for w in line.words:
        trimmed = w.word.rstrip()
        main_words.append(
            Word(
                word=trimmed + (" " if w.word != trimmed else ""),
                start_time=w.start_time,
                end_time=w.end_time,
                obscene=w.obscene,
                empty_beat=w.empty_beat,
                ruby=w.ruby,
            )
        )
        if w.roman_word:
            roman_words.append(Word(word=w.roman_word.strip(), start_time=w.start_time, end_time=w.end_time))
    return (main_words, roman_words, "".join(w.word for w in line.words), "".join(s.word for s in roman_words))


# ============================== 公共 API ==============================


class TTMLParser:
    """TTML 解析器的公共入口。

    Args:
        options: :class:`ParserOptions` / ``dict`` / ``None``。
    """

    def __init__(self, options: ParserOptions | dict | None = None) -> None:
        self.options: ParserOptions = ParserOptions.from_dict(options)

    def parse(self, xml: str) -> Lyric:
        """解析 TTML 字符串为 ``Lyric``。

        Args:
            xml: TTML 字符串。

        Returns:
            :class:`Lyric` 对象。

        Raises:
            ValueError: 输入为空、不是字符串或不是合法的 XML。
        """
        if not xml or not isinstance(xml, str):
            raise ValueError("TTMLParser：输入必须是合法的 XML 字符串。")
        try:
            return _to_lyrics(_TTMLParser().parse(xml), self.options)
        except ET.ParseError as e:
            raise ValueError(f"TTMLParser：XML 解析失败 ({e})") from e

    @staticmethod
    def parse_static(xml: str, options: ParserOptions | dict | None = None) -> Lyric:
        """静态方法版本的解析入口。"""
        return TTMLParser(options).parse(xml)


class TTMLGenerator:
    """TTML 生成器的公共入口。

    Args:
        options: 支持 ``ms_digits`` / ``include_metadata`` / ``metadata_keys``。
    """

    def __init__(self, options: StringifyOptions | dict | None = None) -> None:
        self.options = options or {}

    def generate(self, lyric: Lyric) -> str:
        """将 ``Lyric`` 序列化为 TTML 字符串。"""
        return _TTMLGenerator(self.options).generate(_to_ttml_result(lyric))

    @staticmethod
    def generate_static(lyric: Lyric, options: StringifyOptions | dict | None = None) -> str:
        """静态方法版本的生成入口。"""
        return TTMLGenerator(options).generate(lyric)


def parse_ttml(text: str, options: ParserOptions | dict | None = None) -> Lyric:
    """解析 TTML 字符串为 ``Lyric``。

    Args:
        text: TTML 字符串。
        options: 解析选项。

    Returns:
        :class:`Lyric` 对象。
    """
    return TTMLParser(options).parse(text)


def stringify_ttml(lyric: Lyric, options: StringifyOptions | dict | None = None) -> str:
    """序列化 ``Lyric`` 为 TTML 字符串。

    Args:
        lyric: 歌词数据。**所有行必须带时间戳**；纯文本歌词会抛
            :class:`PlainTextNotSupportedError`。
        options: 生成选项。

    Returns:
        TTML 字符串。

    Raises:
        PlainTextNotSupportedError: 存在无时间戳的行时。
    """
    ensure_synced(lyric.lines, "TTML")
    return TTMLGenerator(options).generate(lyric)
