# 定义命名空间映射
NS_MAP = {
    'tt': 'http://www.w3.org/ns/ttml',
    'itunes': 'http://music.apple.com/lyric-ttml-internal',
    'ttm': 'http://www.w3.org/ns/ttml#metadata',
    'amll': 'http://www.example.com/ns/amll',
    'xml': 'http://www.w3.org/XML/1998/namespace'
}


# 方便生成属性全名的辅助函数
def qname(prefix, tag):
    return f"{{{NS_MAP[prefix]}}}{tag}"
