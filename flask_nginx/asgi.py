from __future__ import annotations

from typing import Any
from typing import Iterator
from typing import TYPE_CHECKING

from .core import StaticFolder
from .utils import topath

if TYPE_CHECKING:
    from starlette.applications import Starlette


def is_starlette_app(app: Any) -> bool:
    try:
        from starlette.applications import Starlette  # type: ignore

        return isinstance(app, Starlette)
    except ImportError:
        return False


def get_starlette_static_folders(app: Starlette) -> Iterator[StaticFolder]:
    from starlette.staticfiles import StaticFiles
    from starlette.routing import Mount

    for r in app.routes:
        if isinstance(r, Mount) and isinstance(r.app, StaticFiles):
            folder = r.app.directory
            if not folder:
                continue
            folder = topath(str(folder))
            rewrite = not folder.endswith(r.path)
            yield StaticFolder(r.path, folder, rewrite)
