from re import Pattern, compile

from lxml.etree import _Element

from ttml.utils import qname  # 假设 NS_MAP 和 qname 定义在 ttml.py 或 common


class TTMLLine:
    brackets: Pattern[str] = compile(r'[(（]+(.+?)[）)]+')

    def __init__(self, element: _Element, object_lang: str, parent: "TTMLLine" = None):
        # 使用精确的命名空间获取属性
        # 对应 itunes:key
        self._key: str = element.get(qname('itunes', 'key'))

        self._is_duet: bool = False
        self._orig_line: str = ""

        self._bg_line: TTMLLine | None = None
        self._ts_line: dict[str, str] | str | None = dict[str, str]()

        self._is_bg: bool = parent is not None

        # 对应 ttm:agent
        agent: str = element.get(qname('ttm', 'agent'))
        self._is_duet = bool(agent and agent != 'v1') if parent is None else parent._is_duet

        # 1. 获取自身的 text (对应 minidom 第一个子节点前的文本)
        if element.text:
            self._orig_line += element.text

        # 2. 遍历子元素
        for child in element:
            # 对应 ttm:role
            role: str = child.get(qname('ttm', 'role'))

            match role:
                case "x-bg":
                    self._bg_line = TTMLLine(child, object_lang, self)
                case "x-translation":
                    # 对应 xml:lang
                    lang: str = child.get(qname('xml', 'lang'))
                    if not lang:
                        lang = "zh-Hans"
                    self._ts_line[lang] = child.text if child.text else ""
                case None:
                    if child.text:
                        self._orig_line += child.text
                case _:
                    pass

            # 3. 获取子元素后的 tail (对应 minidom 子节点后的文本)
            if child.tail:
                self._orig_line += child.tail

        if self._is_bg:
            if TTMLLine.brackets.match(self._orig_line.strip()):
                self._orig_line = TTMLLine.brackets.match(self._orig_line.strip()).group(1).strip()

    def to_text(self, have_duet: bool) -> str:
        text: list[str] = []
        head: str = ("[-:]" if self._is_duet else "[:-]") if have_duet else "[:-:]"

        if len(self._orig_line):
            text.append(f"{head}{self._orig_line}")
        head = head.replace('-', '_')
        if self._bg_line and self._bg_line._orig_line:
            text.append(f"{head}({self._bg_line._orig_line})")
        if self._ts_line:
            text.append(f"{head}{self._ts_line}")
        if self._bg_line and self._bg_line._ts_line:
            text.append(f"{head}({self._bg_line._ts_line})")

        return '\n'.join(text)

    def append_ts(self, text: str, lang: str) -> None:
        """
        添加翻译文本。
        如果有背景行 (bg_line) 且文本中包含括号，
        则将括号内的内容拆分给 bg_line，括号外的保留给自己。
        """
        # 定义提取用的正则 (兼容中文括号，非贪婪匹配内部内容)
        # 注意：这里我们定义一个适合“提取”的正则，
        # 原 TTML.brackets 是贪婪匹配整个字符串用于去壳，不适合从混合字符串中提取。
        pattern = compile(r'[(（]+(.+?)[）)]+')

        # 搜索文本中是否有匹配的括号内容
        match = pattern.search(text)

        if match and self._bg_line:
            # 1. 提取括号内的内容 (group 1)
            bg_content = match.group(1)

            # 2. 递归传递给 bg_line
            self._bg_line._ts_line[lang] = bg_content.strip()

            # 3. 移除原文本中的括号部分
            # 使用切片拼接的方式移除匹配到的部分，避免 replace 误伤重复词
            start, end = match.span()
            main_text = text[:start] + text[end:]

            self._ts_line[lang] = main_text.strip()
        else:
            # 如果没有括号或没有背景行，则全部作为主行翻译
            self._ts_line[lang] = text.strip()

    def filter_ts(self, lang: str) -> None:
        if lang in self._ts_line:
            self._ts_line = self._ts_line[lang]
        else:
            self._ts_line = None
        if self._bg_line:
            self._bg_line.filter_ts(lang)

    @property
    def key(self) -> str:
        return self._key

    @key.setter
    def key(self, key: str) -> None:
        self._key = key

    @property
    def is_duet(self) -> bool:
        return self._is_duet

    @property
    def ts_langs(self) -> list[str]:
        return list(self._ts_line.keys())
