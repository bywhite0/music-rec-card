from lxml.etree import _Element, fromstring, XMLSyntaxError

from ttml.ttml_error import TTMLError
from ttml.ttml_line import TTMLLine
from ttml.utils import qname, NS_MAP


class Part:
    def __init__(self, count: int, name: str):
        self.count: int = count
        self.name: str = name


class TTML:
    def __init__(self, xml_content: str):
        try:
            # 1. 解析 XML 字符串
            # lxml 最佳实践：使用 .encode('utf-8') 将 python str 转为 bytes
            # 这样 lxml 可以正确处理 xml 头部声明的 encoding (如 <?xml ... encoding="gbk"?>)
            tt: _Element | None = fromstring(xml_content.encode('utf-8'))
        except (XMLSyntaxError, ValueError):
            # 2. 如果解析失败，抛出错误
            TTMLError.throw_xml_error()
            return  # 虽然 throw_xml_error 可能会 raise，但写上 return 保证静态分析逻辑通畅

        self._have_duet: bool = False
        self._lines: list[TTMLLine] = []
        self._parts: list[Part] = []

        # 获取 xml:lang
        self._lang = tt.get(qname('xml', 'lang'))
        if not self._lang:
            self._lang = "zh-Hans"

        # 使用 XPath 查找元素，传入 namespaces 字典
        # 注意：因为 xmlns="http://www.w3.org/ns/ttml" 是默认命名空间
        # 所以在 xpath 中必须使用我们定义的前缀 'tt' 来查找标准标签

        # 查找 tt -> body (通常 body 是 tt 的直接子元素，用 / 或 .// 均可，这里用 .// 更稳健)
        body_list = tt.xpath(".//tt:body", namespaces=NS_MAP)
        # head_list = tt.xpath(".//tt:head", namespaces=NS_MAP) # 虽然原逻辑获取了但未实际使用

        if body_list:
            # 查找 body -> div
            divs: list[_Element] = tt.xpath(".//*[local-name()='div']")

            # 使用 update 方法来更新集合 (原代码 union 不会修改原集合)
            langs: set[str] = set()

            index: int = 0
            for div in divs:
                # 获取 itunes:song-part 属性
                part_name = div.get(qname('itunes', 'song-part')) or div.get(qname('itunes', 'songPart'))
                self._parts.append(Part(0, part_name or ""))

                # 查找 div -> p
                p_elements: list[_Element] = div.xpath(".//*[local-name()='p']")

                for p in p_elements:
                    line: TTMLLine = TTMLLine(p, self._lang)
                    # 处理 key 为 None 的情况
                    if not line.key:
                        line.key = f"L{index + 1}"

                    self._lines.append(line)
                    self._have_duet |= line.is_duet
                    self._parts[-1].count += 1
                    langs.update(line.ts_langs)
                    index += 1

            # 获取 translation 标签
            translations: list[_Element] = tt.xpath(".//*[local-name()='translation']")

            # 【优化】构建 key 到 line 的映射字典，避免在循环中重复遍历 list
            # 这样查找复杂度从 O(N*M) 降低到 O(1)
            line_map = {line.key: line for line in self._lines}

            for translation in translations:
                # 1. 从 translation 标签获取语言
                lang: str = translation.get(qname('xml', 'lang'))
                if not lang:
                    lang = "zh-Hans"
                langs.add(lang)

                # 2. 遍历 translation 下的 text 子节点
                # 由于 iTunesMetadata 声明了默认命名空间，这里的 text 实际上是 itunes:text
                # 使用 local-name()='text' 可以稳健地获取到，无论它是否有前缀
                text_nodes: list[_Element] = translation.xpath("./*[local-name()='text']")

                for text_node in text_nodes:
                    # 3. 获取 innerText (歌词翻译内容)
                    text_content = "".join(text_node.itertext())

                    # 4. 获取 for 属性 (目标行的 key)
                    # 属性通常不继承默认命名空间，所以直接用 "for"
                    target_key = text_node.get("for")

                    # 以防万一，如果属性也带了命名空间
                    if not target_key:
                        target_key = text_node.get(qname('itunes', 'for'))

                    # 5. 查找对应的行并调用 append_ts
                    if target_key and target_key in line_map:
                        line_map[target_key].append_ts(text_content, lang)

            lang: str = select_translation_key(langs)
            for line in self._lines:
                line.filter_ts(lang)
        else:
            TTMLError.throw_dom_error()

    @property
    def text(self) -> str:
        text: list[str] = []
        index = 0
        for part in self._parts:
            count = part.count
            text.append(f"[-]{part.name}")
            while count > 0:
                text.append(self._lines[index].to_text(self._have_duet))
                count -= 1
                index += 1

        return '\n'.join(text)


def select_translation_key(keys: set[str]) -> str | None:
    if not keys:
        return None
    if 'zh-Hans' in keys:
        return 'zh-Hans'
    if 'zh-CN' in keys:
        return 'zh-CN'
    if 'zh-Hant' in keys:
        return 'zh-Hant'
    zh_key = next((key for key in keys if key.startswith('zh')), None)
    if zh_key is not None:
        return zh_key
    return next(iter(keys))
