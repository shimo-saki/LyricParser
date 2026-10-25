"""TTML（Apple Music / AMLL 扩展）解析与生成。

支持：
- 标准 TTML 结构（``<tt>``/``<head>``/``<body>``/``<div>``/``<p>``）
- iTunes 扩展：``<iTunesMetadata>`` 下的翻译/罗马字/词曲作者
- AMLL 扩展：``<amll:meta>`` 元数据、``x-bg``/``x-translation``/``x-roman``
  角色、``amll:obscene``/``amll:empty-beat`` 属性、ruby 注音
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

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
    t = MULTI_SPACE.sub(" ", text)
    return t.strip() if trim else t


def _parse_time(s: str | None) -> int:
    """解析 TTML 时间字符串为毫秒。

    支持：
    - ``"123"`` / ``"12.345"``（纯秒，可含小数）
    - ``"123s"``（秒单位显式标记）
    - ``"12:34"`` / ``"1:23:45.678"``（分:秒、时:分:秒）
    """
    if not s:
        return 0
    s = s.strip()
    if not s:
        return 0
    if s.endswith("s"):
        try:
            return round(float(s[:-1]) * 1000)
        except ValueError:
            return 0
    m = TIME_REGEX.match(s)
    if m:
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
    if ms < 0:
        ms = 0
    sec_total = ms // 1000
    milli = ms % 1000
    minute = sec_total // 60
    sec = sec_total % 60
    frac = _format_fraction(milli, ms_digits)
    if minute > 0:
        return f"{minute}:{sec:02d}.{frac}"
    return f"{sec}.{frac}"


def _get_attr(elem: ET.Element, ns: str | None, local: str, fallback: str | None = None) -> str | None:
    """读取 XML 属性，兼容命名空间属性、fallback 属性和 local-name 匹配。"""
    if ns is not None:
        v = elem.get(_q(ns, local))
        if v:
            return v
    if fallback:
        v = elem.get(fallback)
        if v:
            return v
    for attr_name, attr_val in elem.attrib.items():
        if attr_name.startswith("{"):
            _, local_name = attr_name[1:].split("}", 1)
        else:
            local_name = attr_name
        if ":" in local_name:
            local_name = local_name.split(":", 1)[1]
        if local_name == local:
            return attr_val
    return None


def _iter_all(elem: ET.Element):
    """深度优先遍历元素树。"""
    yield elem
    for child in elem:
        yield from _iter_all(child)


def _by_tag(elem: ET.Element, tag: str) -> list[ET.Element]:
    """按 local-name 匹配所有后代元素。"""
    return [e for e in _iter_all(elem) if e.tag.split("}")[-1] == tag]


def _by_tag_ns(elem: ET.Element, ns: str, tag: str) -> list[ET.Element]:
    """按完整命名空间匹配所有后代元素。"""
    return [e for e in _iter_all(elem) if e.tag == _q(ns, tag)]


def _sort_platform_ids(ids: dict[str, list[str]]) -> dict[str, list[str]]:
    """按 AMLL 约定的优先级重排 platformIds。"""
    preferred = ["ncmMusicId", "qqMusicId", "spotifyId", "appleMusicId"]
    ordered: dict[str, list[str]] = {}
    for k in preferred:
        if k in ids:
            ordered[k] = ids[k]
    for k, v in ids.items():
        if k not in ordered:
            ordered[k] = v
    return ordered


# ============================== 解析实现 ==============================


class _TTMLParser:
    """TTML 内部解析器（面向 _TTMLResult）。"""

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

        lang = _get_attr(doc, XML, "lang")
        if lang:
            metadata.language = lang
        timing = _get_attr(doc, ITUNES, "timing")
        if timing in (Values.Word, Values.Line):
            metadata.timing_mode = timing

        self._parse_body(doc, lines, sidecar)
        if not metadata.timing_mode:
            metadata.timing_mode = self._infer_timing(lines)
        if metadata.platform_ids:
            metadata.platform_ids = _sort_platform_ids(metadata.platform_ids)
        return _TTMLResult(metadata=metadata, lines=lines)

    def _infer_timing(self, lines: list[_TTMLLine]) -> str:
        """未显式声明时，根据是否有逐字推断 timingMode。"""
        for line in lines:
            if line.words and len(line.words) > 1:
                return "Word"
            if line.background_vocal and line.background_vocal.words and len(line.background_vocal.words) > 1:
                return "Word"
        return "Line"

    def _parse_head(self, doc: ET.Element) -> tuple[_TTMLMetadata, dict[str, dict[str, list[_TTMLSubContent]]]]:
        """解析 head，返回 (metadata, sidecar)。

        sidecar 是 ``行 id -> {translations/romanizations: [...]}`` 的映射，
        用于把 iTunesMetadata 中的翻译/罗马字挂回主行。
        """
        meta = _TTMLMetadata()
        sidecar: dict[str, dict[str, list[_TTMLSubContent]]] = {}
        head = next((e for e in _iter_all(doc) if e.tag == _q(TT, "head")), None)
        if head is None:
            return meta, sidecar

        for title in _by_tag_ns(head, TTM, "title"):
            if title.text:
                meta.title.append(title.text.strip())
        for agent in _by_tag_ns(head, TTM, "agent"):
            aid = _get_attr(agent, XML, "id")
            if not aid:
                continue
            atype = _get_attr(agent, TTM, "type") or "person"
            names = _by_tag_ns(agent, TTM, "name")
            a = _TTMLAgent(id=aid, type=atype)
            if names and names[0].text:
                nm = names[0].text.strip()
                if nm:
                    a.name = nm
            meta.agents[aid] = a

        for el in _by_tag_ns(head, AMLL, "meta"):
            key = _get_attr(el, AMLL, "key")
            val = _get_attr(el, AMLL, "value")
            if not key or not val:
                continue
            val = val.strip()
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

        meta.title = list(dict.fromkeys(meta.title))
        meta.artist = list(dict.fromkeys(meta.artist))
        meta.album = list(dict.fromkeys(meta.album))
        meta.isrc = list(dict.fromkeys(meta.isrc))
        meta.author_ids = list(dict.fromkeys(meta.author_ids))
        meta.author_names = list(dict.fromkeys(meta.author_names))
        meta.songwriters = list(dict.fromkeys(meta.songwriters))
        for k, v in list(meta.platform_ids.items()):
            meta.platform_ids[k] = list(dict.fromkeys(v))
        for k, v in list(meta.raw_properties.items()):
            meta.raw_properties[k] = list(dict.fromkeys(v))

        return meta, sidecar

    def _parse_itunes(
        self, head: ET.Element, meta: _TTMLMetadata, sidecar: dict[str, dict[str, list[_TTMLSubContent]]]
    ) -> None:
        """解析 ``<iTunesMetadata>`` 下的扩展元素。"""
        itunes_metas = _by_tag(head, "iTunesMetadata")
        if not itunes_metas:
            return
        for itunes in itunes_metas:
            sw = _by_tag(itunes, "songwriters")
            if sw:
                for w in _by_tag(sw[0], "songwriter"):
                    if w.text:
                        meta.songwriters.append(w.text.strip())

            def process(container_tag: str, item_tag: str, key: str) -> None:
                container = _by_tag(itunes, container_tag)
                if not container:
                    return
                for item in _by_tag(container[0], item_tag):
                    lang = _get_attr(item, XML, "lang")
                    for text_node in _by_tag(item, "text"):
                        fid = text_node.get("for")
                        parsed = self._parse_common_content(text_node)
                        if not fid:
                            continue
                        sub = self._extract_sub_content(parsed, lang, False)
                        sidecar.setdefault(fid, {})
                        if sub.get("main"):
                            sidecar[fid].setdefault(key, []).append(sub["main"])
                        if sub.get("bg"):
                            bg_key = "bgTranslations" if key == "translations" else "bgRomanizations"
                            sidecar[fid].setdefault(bg_key, []).append(sub["bg"])

            process("translations", "translation", "translations")
            process("transliterations", "transliteration", "romanizations")

    def _extract_sub_content(
        self, base: _TTMLLine, lang: str | None, ignore_words: bool = False
    ) -> dict[str, _TTMLSubContent]:
        """从行对象提取主/背景子内容，用于 sidecar 展开。"""
        result: dict[str, _TTMLSubContent] = {}
        main_text = _normalize_text(base.text)
        has_words = (not ignore_words) and base.words and len(base.words) > 0
        if main_text or has_words:
            main = _TTMLSubContent(text=main_text, language=lang)
            if has_words:
                w = base.words
                if not (len(w) == 1 and w[0].start_time == 0 and w[0].end_time == 0):
                    main.words = w
            result["main"] = main
        bg = base.background_vocal
        if bg:
            bg_text = _normalize_text(bg.text)
            has_bg_words = (not ignore_words) and bg.words
            if bg_text or has_bg_words:
                b = _TTMLSubContent(text=bg_text, language=lang)
                if has_bg_words:
                    if not (len(bg.words) == 1 and bg.words[0].start_time == 0 and bg.words[0].end_time == 0):
                        b.words = bg.words
                result["bg"] = b
        return result

    def _parse_body(
        self, doc: ET.Element, lines: list[_TTMLLine], sidecar: dict[str, dict[str, list[_TTMLSubContent]]]
    ) -> None:
        """解析 body 中的 div/p 结构。"""
        body = next((e for e in _iter_all(doc) if e.tag == _q(TT, "body")), None)
        if body is None:
            return
        block_index = 0
        for el in list(body):
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "div":
                block_index += 1
                song_part = _get_attr(el, ITUNES, "song-part") or _get_attr(el, ITUNES, "songPart")
                p_nodes = _by_tag_ns(el, TT, "p") or _by_tag(el, "p")
                for p in p_nodes:
                    self._process_line(p, lines, sidecar, song_part, block_index)
            elif tag == "p":
                block_index += 1
                self._process_line(el, lines, sidecar, None, block_index)

    def _process_line(
        self,
        p: ET.Element,
        lines: list[_TTMLLine],
        sidecar: dict[str, dict[str, list[_TTMLSubContent]]],
        song_part: str | None,
        block_index: int,
    ) -> None:
        """处理单个 ``<p>``：解析内容并挂载 sidecar 中的翻译/罗马字。"""
        lid = _get_attr(p, ITUNES, "key")
        if not lid:
            return
        content = self._parse_common_content(p)
        content.id = lid
        if song_part:
            content.song_part = song_part
        content.block_index = block_index
        agent = _get_attr(p, TTM, "agent")
        if agent:
            content.agent_id = agent
        ext = sidecar.get(lid)
        if ext:
            if ext.get("translations"):
                content.translations = (content.translations or []) + ext["translations"]
            if ext.get("romanizations"):
                content.romanizations = (content.romanizations or []) + ext["romanizations"]
            bg = content.background_vocal
            if ext.get("bgTranslations") and bg:
                bg.translations = (bg.translations or []) + ext["bgTranslations"]
            if ext.get("bgRomanizations") and bg:
                bg.romanizations = (bg.romanizations or []) + ext["bgRomanizations"]
        lines.append(content)

    def _parse_common_content(self, element: ET.Element) -> _TTMLLine:
        """解析元素主体（文本 + 音节 + 角色标记）。"""
        begin = _get_attr(element, XML, "begin") or element.get("begin")
        end = _get_attr(element, XML, "end") or element.get("end")
        orig_start = _parse_time(begin) if begin else 0
        orig_end = _parse_time(end) if end else 0

        state = self._extract_node_state(element)
        if state.background_vocal:
            if state.bg_translations:
                state.background_vocal.translations = (
                    state.background_vocal.translations or []
                ) + state.bg_translations
            if state.bg_romanizations:
                state.background_vocal.romanizations = (
                    state.background_vocal.romanizations or []
                ) + state.bg_romanizations
        _finalize_words(state.words)

        start, end_time = self._calc_time(orig_start, orig_end, state.words, state.background_vocal)
        clean = _normalize_text(state.full_text)
        has_attrs = begin is not None or end is not None
        self._apply_fallback(state.words, clean, has_attrs, orig_start, orig_end, start, end_time)

        return _TTMLLine(
            start_time=start,
            end_time=end_time,
            text=clean,
            words=state.words if state.words else None,
            translations=state.translations if state.translations else None,
            romanizations=state.romanizations if state.romanizations else None,
            background_vocal=state.background_vocal,
        )

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
        is_fmt = "\n" in raw_text
        if is_fmt and not raw_text.strip():
            return
        norm = _normalize_text(raw_text, trim=False)
        state.full_text += norm

    def _process_element(self, state: "_TTMLParser._NodeState", el: ET.Element) -> None:
        """根据 role/ruby 属性分发元素处理。"""
        role = _get_attr(el, TTM, "role")
        ruby_attr = _get_attr(el, TTS, "ruby")
        if ruby_attr == Values.RubyContainer:
            self._process_ruby(state, el)
            return
        if role == Values.RoleBg:
            state.background_vocal = self._parse_bg(el)
        elif role == Values.RoleTranslation:
            trans = self._parse_inline_sub(el)
            if trans:
                if trans.get("main"):
                    state.translations.append(trans["main"])
                if trans.get("bg"):
                    state.bg_translations.append(trans["bg"])
        elif role == Values.RoleRoman:
            roman = self._parse_inline_sub(el)
            if roman:
                if roman.get("main"):
                    state.romanizations.append(roman["main"])
                if roman.get("bg"):
                    state.bg_romanizations.append(roman["bg"])
        else:
            self._process_word(state, el)

    def _process_ruby(self, state: "_TTMLParser._NodeState", container: ET.Element) -> None:
        """解析 ruby 容器（base + 一组带时间的 rt）。"""
        is_obscene = _get_attr(container, AMLL, "obscene") == "true"
        empty_beat_attr = _get_attr(container, AMLL, "empty-beat")
        empty_beat = int(empty_beat_attr) if empty_beat_attr and empty_beat_attr.isdigit() else None

        base_text = ""
        ruby_tags: list[Ruby] = []
        for child in list(container):
            ruby_attr = _get_attr(child, TTS, "ruby")
            if ruby_attr == Values.RubyBase:
                base_text = _normalize_text(child.text or "", trim=False)
            elif ruby_attr == Values.RubyTextContainer:
                for rt in list(child):
                    if _get_attr(rt, TTS, "ruby") != Values.RubyText:
                        continue
                    b = _get_attr(rt, XML, "begin")
                    e = _get_attr(rt, XML, "end")
                    txt = _normalize_text(rt.text or "", trim=False).strip()
                    if txt and b and e:
                        ruby_tags.append(Ruby(word=txt, start_time=_parse_time(b), end_time=_parse_time(e)))

        if not base_text:
            return
        state.full_text += base_text
        start = min((r.start_time for r in ruby_tags), default=0)
        end = max((r.end_time for r in ruby_tags), default=0)
        clean = base_text.strip()
        if clean:
            state.words.append(
                Word(
                    word=clean,
                    start_time=start,
                    end_time=end,
                    ruby=ruby_tags if ruby_tags else None,
                    obscene=True if is_obscene else None,
                    empty_beat=empty_beat,
                )
            )

    def _process_word(self, state: "_TTMLParser._NodeState", el: ET.Element) -> None:
        """处理普通 span 音节（含 begin/end 时视为音节）。"""
        wb = _get_attr(el, XML, "begin")
        we = _get_attr(el, XML, "end")
        is_obscene = _get_attr(el, AMLL, "obscene") == "true"
        empty_beat_attr = _get_attr(el, AMLL, "empty-beat")
        empty_beat = int(empty_beat_attr) if empty_beat_attr and empty_beat_attr.isdigit() else None
        raw = el.text or ""
        norm = _normalize_text(raw, trim=False)
        state.full_text += norm
        if wb and we:
            clean = norm.strip()
            if clean:
                state.words.append(
                    Word(
                        word=clean,
                        start_time=_parse_time(wb),
                        end_time=_parse_time(we),
                        obscene=True if is_obscene else None,
                        empty_beat=empty_beat,
                    )
                )
        for child in list(el):
            self._process_element(state, child)
            if child.tail:
                self._process_text(state, child.tail)

    def _parse_bg(self, el: ET.Element) -> _TTMLLine:
        """解析背景人声元素，去掉首尾括号。"""
        content = self._parse_common_content(el)
        content.background_vocal = None
        content.text = re.sub(r"^[(（]+", "", content.text)
        content.text = re.sub(r"[)）]+$", "", content.text)
        words = content.words
        if words:
            words[0].word = re.sub(r"^[(（]+", "", words[0].word).lstrip()
            words[-1].word = re.sub(r"[)）]+$", "", words[-1].word).rstrip()
        return content

    def _parse_inline_sub(self, el: ET.Element) -> dict[str, _TTMLSubContent] | None:
        """解析行内 translation/roman 角色。"""
        lang = _get_attr(el, XML, "lang")
        parsed = self._parse_common_content(el)
        content = self._extract_sub_content(parsed, lang, True)
        return content if (content.get("main") or content.get("bg")) else None

    def _calc_time(self, orig_start: int, orig_end: int, words: list[Word], bg: _TTMLLine | None) -> tuple[int, int]:
        """当行无显式时间时，根据子节点时间推导行时间。"""
        start, end = orig_start, orig_end
        timed = list(words)
        if bg:
            timed.append(bg)
        if timed:
            min_start = min((t.start_time for t in timed), default=float("inf"))
            max_end = max((t.end_time for t in timed), default=0)
            if start == 0 or (min_start > 0 and min_start < start):
                start = 0 if min_start == float("inf") else min_start
            if end == 0 or max_end > end:
                end = max_end
        return start, end

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
        self.options: StringifyOptions = StringifyOptions.from_dict(options)
        self.ms_digits: int = self.options.ms_digits
        self.include_metadata: bool = self.options.include_metadata
        self.metadata_keys: frozenset[str] | None  = self.options.metadata_keys

    def _fmt(self, ms: int) -> str:
        """以当前 ms_digits 格式化时间。"""
        return _format_time(ms, self.ms_digits)

    def generate(self, result: _TTMLResult) -> str:
        """序列化为 TTML 字符串。"""
        meta = result.metadata
        lines = result.lines

        all_have_id = all(line.id for line in lines)
        for i, line in enumerate(lines):
            if not all_have_id:
                line.id = f"L{i + 1}"
            if not line.agent_id:
                line.agent_id = Values.AgentDefault

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

        agents = meta.agents
        if not agents:
            unique = {line.agent_id for line in result.lines if line.agent_id}
            agents = {
                aid: _TTMLAgent(
                    id=aid,
                    type=Values.Group if aid == Values.AgentGroup else Values.Person,
                )
                for aid in unique
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

        def add_meta(key: str, value: str) -> None:
            stripped = value.strip()
            if not stripped:
                return
            if key.lower() == "offset" and stripped == "0":
                return
            if self.metadata_keys is not None and key not in self.metadata_keys:
                return
            el = ET.SubElement(metadata, _q(AMLL, "meta"))
            el.set("key", key)
            el.set("value", value)

        for v in meta.title:
            add_meta(Values.MusicName, v)
        for v in meta.artist:
            add_meta(Values.Artists, v)
        for v in meta.album:
            add_meta(Values.Album, v)
        for k, vals in meta.platform_ids.items():
            for v in vals:
                add_meta(k, v)
        for v in meta.isrc:
            add_meta(Values.ISRC, v)
        for v in meta.author_ids:
            add_meta(Values.TTMLAuthorGithub, v)
        for v in meta.author_names:
            add_meta(Values.TTMLAuthorGithubLogin, v)
        for k, vals in meta.raw_properties.items():
            for v in vals:
                add_meta(k, v)
        return head

    def _should_sidecar(self, content: _TTMLSubContent) -> bool:
        """判断子内容是否走侧栏（有逐字或用户强制）。"""
        return bool(content.words) or self.use_sidecar

    def _build_itunes_metadata(self, metadata_el: ET.Element, result: _TTMLResult) -> None:
        """构建 ``<iTunesMetadata>``：翻译、罗马字、词曲作者。"""
        itunes = ET.SubElement(metadata_el, _q(ITUNES, "iTunesMetadata"))
        itunes.set("xmlns", NS["ITUNES"])
        has_content = False

        trans_map: dict[str | None, list[dict[str, Any]]] = {}
        roman_map: dict[str | None, list[dict[str, Any]]] = {}
        for line in result.lines:
            for pair in self._pair_sub_contents(
                line.translations, (line.background_vocal.translations if line.background_vocal else None)
            ):
                if (pair.get("main") and self._should_sidecar(pair["main"])) or (
                    pair.get("bg") and self._should_sidecar(pair["bg"])
                ):
                    trans_map.setdefault(pair["lang"], []).append(
                        {"id": line.id, "main": pair.get("main"), "bg": pair.get("bg")}
                    )
            for pair in self._pair_sub_contents(
                line.romanizations, (line.background_vocal.romanizations if line.background_vocal else None)
            ):
                if (pair.get("main") and self._should_sidecar(pair["main"])) or (
                    pair.get("bg") and self._should_sidecar(pair["bg"])
                ):
                    roman_map.setdefault(pair["lang"], []).append(
                        {"id": line.id, "main": pair.get("main"), "bg": pair.get("bg")}
                    )

        if trans_map:
            container = ET.SubElement(itunes, "translations")
            for lang, items in trans_map.items():
                trans_el = ET.SubElement(container, "translation")
                if lang:
                    trans_el.set(_q(XML, "lang"), lang)
                for item in items:
                    text_el = ET.SubElement(trans_el, "text")
                    text_el.set("for", item["id"])
                    if item.get("main"):
                        self._append_content(text_el, item["main"])
                    if item.get("bg"):
                        self._append_bg(text_el, item["bg"])
            has_content = True

        if roman_map:
            container = ET.SubElement(itunes, "transliterations")
            for lang, items in roman_map.items():
                trans_el = ET.SubElement(container, "transliteration")
                if lang:
                    trans_el.set(_q(XML, "lang"), lang)
                for item in items:
                    text_el = ET.SubElement(trans_el, "text")
                    text_el.set("for", item["id"])
                    if item.get("main"):
                        self._append_content(text_el, item["main"])
                    if item.get("bg"):
                        self._append_bg(text_el, item["bg"])
            has_content = True

        if result.metadata.songwriters:
            container = ET.SubElement(itunes, "songwriters")
            for name in result.metadata.songwriters:
                sw = ET.SubElement(container, "songwriter")
                sw.text = name
            has_content = True

        if not has_content:
            metadata_el.remove(itunes)

    def _pair_sub_contents(
        self, main_list: list[_TTMLSubContent] | None, bg_list: list[_TTMLSubContent] | None
    ) -> list[dict[str, Any]]:
        """按语言把主/背景翻译配对。"""
        out: dict[str | None, dict[str, Any]] = {}
        for item in main_list or []:
            entry = out.setdefault(item.language, {"lang": item.language})
            entry["main"] = item
        for item in bg_list or []:
            entry = out.setdefault(item.language, {"lang": item.language})
            entry["bg"] = item
        return list(out.values())

    def _build_body(self, result: _TTMLResult) -> ET.Element:
        """构建 ``<body>``，按 song_part/block_index 分组。"""
        body = ET.Element(_q(TT, "body"))
        lines = result.lines
        last_time = max((l.end_time for l in lines), default=0)
        body.set("dur", self._fmt(last_time))

        current_div: ET.Element | None = None
        current_part: str | None = None
        current_block: int | None = None
        section_end = 0

        def finalize() -> None:
            nonlocal current_div, section_end, current_part
            if current_div is not None and section_end > 0:
                current_div.set("end", self._fmt(section_end))
                if current_part:
                    current_div.set(_q(ITUNES, "songPart"), current_part)

        for line in lines:
            if line.song_part != current_part or line.block_index != current_block or current_div is None:
                finalize()
                current_part = line.song_part
                current_block = line.block_index
                section_end = 0
                current_div = ET.SubElement(body, _q(TT, "div"))
                current_div.set("begin", self._fmt(line.start_time))
            if line.end_time > section_end:
                section_end = line.end_time
            p = ET.SubElement(current_div, _q(TT, "p"))
            p.set("begin", self._fmt(line.start_time))
            p.set("end", self._fmt(line.end_time))
            p.set(_q(ITUNES, "key"), line.id)
            if line.agent_id:
                p.set(_q(TTM, "agent"), line.agent_id)
            self._append_line_content(p, line)
        finalize()
        return body

    def _append_line_content(self, element: ET.Element, content: _TTMLLine) -> None:
        """向 ``<p>`` 或嵌套 span 追加文本、音节、翻译、罗马字、bg。"""
        if content.words:
            self._append_words(element, content.words, False)
        else:
            element.text = (element.text or "") + (content.text or "")

        for trans in content.translations or []:
            if not self._should_sidecar(trans):
                span = ET.SubElement(element, _q(TT, "span"))
                span.set(_q(TTM, "role"), Values.RoleTranslation)
                if trans.language:
                    span.set(_q(XML, "lang"), trans.language)
                self._append_sub_content(span, trans)

        for roman in content.romanizations or []:
            if not self._should_sidecar(roman):
                span = ET.SubElement(element, _q(TT, "span"))
                span.set(_q(TTM, "role"), Values.RoleRoman)
                if roman.language:
                    span.set(_q(XML, "lang"), roman.language)
                self._append_sub_content(span, roman)

        if content.background_vocal:
            self._append_bg(element, content.background_vocal)

    def _append_sub_content(self, element: ET.Element, content: _TTMLSubContent) -> None:
        """追加一段子内容（逐字或纯文本）。"""
        if content.words:
            self._append_words(element, content.words, False)
        else:
            element.text = (element.text or "") + (content.text or "")

    def _append_bg(self, element: ET.Element, bg: _TTMLLine) -> None:
        """追加一段背景人声 span。"""
        span = ET.SubElement(element, _q(TT, "span"))
        span.set(_q(TTM, "role"), Values.RoleBg)
        if bg.start_time > 0 and bg.end_time > 0:
            span.set("begin", self._fmt(bg.start_time))
            span.set("end", self._fmt(bg.end_time))
        self._append_line_content(span, bg)

    def _append_content(self, element: ET.Element, content: _TTMLSubContent) -> None:
        """追加侧栏内容（仅文本或音节）。"""
        if content.words:
            self._append_words(element, content.words, False)
        else:
            text = content.text or ""
            element.text = (element.text or "") + text

    def _append_words(self, element: ET.Element, words: list[Word], is_bg: bool) -> None:
        """追加音节序列（含 ruby 支持）。"""
        for i, syl in enumerate(words):
            text = syl.word
            if is_bg:
                if i == 0:
                    text = f"({text}"
                if i == len(words) - 1:
                    text = f"{text})"
            if syl.ruby:
                container = ET.SubElement(element, _q(TT, "span"))
                container.set(_q(TTS, "ruby"), Values.RubyContainer)
                if syl.obscene:
                    container.set(_q(AMLL, "obscene"), Values.TRUE_)
                if syl.empty_beat is not None:
                    container.set(_q(AMLL, "empty-beat"), str(syl.empty_beat))
                base = ET.SubElement(container, _q(TT, "span"))
                base.set(_q(TTS, "ruby"), Values.RubyBase)
                base.text = text
                tc = ET.SubElement(container, _q(TT, "span"))
                tc.set(_q(TTS, "ruby"), Values.RubyTextContainer)
                for r in syl.ruby:
                    rt = ET.SubElement(tc, _q(TT, "span"))
                    rt.set(_q(TTS, "ruby"), Values.RubyText)
                    rt.set("begin", self._fmt(r.start_time))
                    rt.set("end", self._fmt(r.end_time))
                    rt.text = r.word
            else:
                span = ET.SubElement(element, _q(TT, "span"))
                span.set("begin", self._fmt(syl.start_time))
                span.set("end", self._fmt(syl.end_time))
                if syl.obscene:
                    span.set(_q(AMLL, "obscene"), Values.TRUE_)
                if syl.empty_beat is not None:
                    span.set(_q(AMLL, "empty-beat"), str(syl.empty_beat))
                span.text = text


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

    lines: list[Line] = []
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
            selected = next(
                (t for t in source.translations if t.language == opts.translation_language), source.translations[0]
            )
            trans_text = selected.text

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
    last_person: str | None = None
    last_person_duet = False
    for line in result.lines:
        aid = line.agent_id or Values.AgentDefault
        agent = agents.get(aid)
        is_group = agent.type == Values.Group if agent else False
        is_other = agent.type == Values.Other if agent else False

        current_duet = False
        if is_group:
            current_duet = False
        elif last_person is None:
            current_duet = is_other
            last_person = aid
            last_person_duet = current_duet
        elif last_person == aid:
            current_duet = last_person_duet
        else:
            current_duet = not last_person_duet
            last_person = aid
            last_person_duet = current_duet

        lines.append(convert(line, False, current_duet))
        if line.background_vocal:
            lines.append(convert(line.background_vocal, True, current_duet))

    metadata: list[MetadataEntry] = []
    meta = result.metadata
    if meta.title:
        metadata.append(MetadataEntry(Values.MusicName, list(meta.title)))
    if meta.artist:
        metadata.append(MetadataEntry(Values.Artists, list(meta.artist)))
    if meta.album:
        metadata.append(MetadataEntry(Values.Album, list(meta.album)))
    if meta.isrc:
        metadata.append(MetadataEntry(Values.ISRC, list(meta.isrc)))
    if meta.author_ids:
        metadata.append(MetadataEntry(Values.TTMLAuthorGithub, list(meta.author_ids)))
    if meta.author_names:
        metadata.append(MetadataEntry(Values.TTMLAuthorGithubLogin, list(meta.author_names)))
    if meta.language:
        metadata.append(MetadataEntry(Values.Language, [meta.language]))
    if meta.timing_mode:
        metadata.append(MetadataEntry(Values.TimingMode, [meta.timing_mode]))
    if meta.songwriters:
        metadata.append(MetadataEntry("songwriters", list(meta.songwriters)))
    for key in ("ncmMusicId", "qqMusicId", "spotifyId", "appleMusicId"):
        if meta.platform_ids.get(key):
            metadata.append(MetadataEntry(key, list(meta.platform_ids[key])))
    for k, v in meta.raw_properties.items():
        metadata.append(MetadataEntry(k, list(v)))

    return Lyric(lines=lines, metadata=metadata)


def _align_romanization(words: list[Word], roman_words: list[Word]) -> None:
    """把罗马字按时间对齐到主音节。

    先用精确时间快速匹配，失败则用区间 IoU 做最佳匹配。
    """
    search_start = 0
    MIN_IOU = 0.1
    FAST_TOL = 2
    for w in words:
        best_iou = 0.0
        best_idx = -1
        fast = False
        j = search_start
        while j < len(roman_words):
            sub = roman_words[j]
            if abs(w.start_time - sub.start_time) <= FAST_TOL:
                w.roman_word = sub.word
                search_start = j + 1
                fast = True
                break
            overlap_start = max(w.start_time, sub.start_time)
            inter = max(0, min(w.end_time, sub.end_time) - overlap_start)
            if inter > 0:
                union_start = min(w.start_time, sub.start_time)
                iou = inter / max(1, max(w.end_time, sub.end_time) - union_start)
                if iou > best_iou:
                    best_iou = iou
                    best_idx = j
            if sub.start_time >= w.end_time:
                break
            j += 1
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
    metadata.agents[Values.AgentDefault] = _TTMLAgent(id=Values.AgentDefault, type=Values.Person)
    metadata.agents[Values.AgentDefaultDuet] = _TTMLAgent(id=Values.AgentDefaultDuet, type=Values.Person)

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
        trans_content: list[_TTMLSubContent] | None = None
        if line.translated_lyric:
            trans_content = [_TTMLSubContent(text=line.translated_lyric, language=opts.translation_language)]
        roman_content: list[_TTMLSubContent] | None = None
        if line.roman_lyric or roman_words:
            roman_content = [
                _TTMLSubContent(
                    text=line.roman_lyric or roman_text,
                    language=opts.romanization_language,
                    words=roman_words if roman_words else None,
                )
            ]

        ttml_line = _TTMLLine(
            start_time=line.start_time,
            end_time=line.end_time,
            text=full_text,
            words=main_words,
            translations=trans_content,
            romanizations=roman_content,
        )

        if line.is_bg:
            if current_main is not None and not current_main.background_vocal:
                current_main.background_vocal = ttml_line
            else:
                ttml_line.agent_id = current_main.agent_id if current_main else Values.AgentDefault
                result_lines.append(ttml_line)
        else:
            ttml_line.agent_id = Values.AgentDefaultDuet if line.is_duet else Values.AgentDefault
            result_lines.append(ttml_line)
            current_main = ttml_line

    return _TTMLResult(metadata=metadata, lines=result_lines)


def _convert_words(line: Line) -> tuple[list[Word], list[Word], str, str]:
    """将公共 Line 的音节转为 TTML 音节（保留尾随空格信息）。

    Returns:
        ``(主音节, 罗马音节, 整行文本, 罗马文本)``
    """
    main_words: list[Word] = []
    roman_words: list[Word] = []
    for w in line.words:
        raw = w.word
        trimmed = raw.rstrip()
        has_space = raw != trimmed
        syl = Word(
            word=trimmed + (" " if has_space else ""),
            start_time=w.start_time,
            end_time=w.end_time,
            obscene=w.obscene,
            empty_beat=w.empty_beat,
            ruby=w.ruby,
        )
        main_words.append(syl)
        if w.roman_word:
            roman_words.append(Word(word=w.roman_word.strip(), start_time=w.start_time, end_time=w.end_time))
    full_text = "".join(w.word for w in line.words)
    roman_text = "".join(s.word for s in roman_words) if roman_words else ""
    return main_words, roman_words, full_text, roman_text


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
