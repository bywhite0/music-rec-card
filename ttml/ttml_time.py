from re import Pattern, compile


class TTMLTime:
    __pattern: Pattern = compile(r'\d+')

    def __init__(self, centi: str = ''):
        if centi == '': return
        # 获取匹配结果的数组
        match: list[str] = self.__pattern.findall(centi)

        count: int = 0
        for t in match[:-1]:
            count = count * 60 + int(t)
        count = count * 1000 + int(match[-1]) * (10 ** (3 - len(match[-1])))
        self._micros: int = count % 1000
        count //= 1000
        self._second: int = count % 60
        self._minute: int = count // 60

    def __str__(self) -> str:
        return f'{self._minute:02}:{self._second:02}.{self._micros:03}'

    def __int__(self) -> int:
        return (self._minute * 60 + self._second) * 1000 + self._micros

    def __gt__(self, other) -> bool:
        return int(self) > int(other)

    def __ge__(self, other) -> bool:
        return int(self) >= int(other)

    def __ne__(self, other) -> bool:
        return int(self) != int(other)

    def __sub__(self, other) -> int:
        return abs(int(self) - int(other))
