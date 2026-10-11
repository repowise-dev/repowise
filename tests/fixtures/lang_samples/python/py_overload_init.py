import typing
from typing import overload

import typing_extensions


class Client:
    @overload  # a comment inside the decorator node
    def __init__(self, url: str) -> None: ...

    @typing.overload
    def __init__(self, url: str, timeout: float) -> None: ...

    def __init__(self, url, timeout=None):
        self.url = url
        self.timeout = timeout
        self.connect()

    def connect(self):
        return self.url

    @staticmethod
    def build(url):
        return Client(url)


@typing_extensions.overload
def parse(raw: str) -> str: ...


@overload
def parse(raw: bytes) -> bytes: ...


def parse(raw):
    return raw
