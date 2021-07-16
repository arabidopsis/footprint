from dataclasses import dataclass
from types import TracebackType

from werkzeug.local import LocalProxy, LocalStack

from ..config import INDENT, NL


@dataclass
class Builder:

    preamble_ts: str = "import {get, post} from './fetch-lib.js'"
    preamble_js: str = "const {get, post} = require('./fetch-lib.js');"
    as_jquery: bool = False
    as_ts: bool = True
    nl: str = NL
    indent: str = INDENT
    export: bool = False


class BuildContext:
    def __init__(self, builder: Builder):
        self.builder = builder

    def __enter__(self) -> "BuildContext":
        self.push()
        return self

    def __exit__(
        self, exc_type: type, exc_value: BaseException, tb: TracebackType
    ) -> None:
        self.pop()

    def push(self):
        _build_ctx.push(self)

    def pop(self):
        _build_ctx.pop()


def _find_builder() -> Builder:
    top = _build_ctx.top
    if top is None:
        raise RuntimeError("no build context")
    return top.builder


_build_ctx = LocalStack()
build_context: Builder = LocalProxy(_find_builder)  # type: ignore
