from __future__ import annotations

from typing import Any
from typing import Iterator
from typing import TYPE_CHECKING

from .core import StaticFolder
from .utils import topath

if TYPE_CHECKING:
    from typing import Sequence
    from starlette.applications import Starlette


def is_starlette_app(app: Any) -> bool:
    try:
        from starlette.applications import Starlette  # type: ignore

        return isinstance(app, Starlette)
    except ImportError:
        return False


def get_starlette_static_folders(app: Starlette) -> Iterator[StaticFolder]:
    from starlette.staticfiles import StaticFiles
    from starlette.routing import Mount, Router, BaseRoute

    def findstatic(
        routes: Sequence[BaseRoute],
        prefix: str = "",
    ) -> Iterator[StaticFolder]:
        for r in routes:
            if isinstance(r, Mount):
                if isinstance(r.app, StaticFiles):
                    folder = r.app.directory
                    if not folder:
                        continue
                    folder = topath(str(folder))
                    path = prefix + r.path
                    rewrite = not folder.endswith(path)
                    yield StaticFolder(r.path, folder, rewrite)
                elif isinstance(r.app, Router):
                    yield from findstatic(r.app.routes, prefix + r.path)

    yield from findstatic(app.routes)


def get_starlette_route_prefixes(app: Starlette) -> Iterator[str]:
    from starlette.routing import Mount, Router, BaseRoute

    def findroute(
        routes: Sequence[BaseRoute],
        prefix: str = "",
    ) -> Iterator[str]:
        for r in routes:
            if isinstance(r, Mount):
                if isinstance(r.app, Router):
                    yield from findroute(r.app.routes, prefix + r.path)
                else:
                    yield prefix + r.path

    yield from findroute(app.routes)
