from __future__ import annotations

import os
import re
from contextlib import redirect_stderr
from io import StringIO
from os.path import isdir
from os.path import isfile
from typing import Any
from typing import Iterator
from typing import TYPE_CHECKING

import click

from .utils import fixstatic
from .utils import get_dot_env
from .utils import StaticFolder
from .utils import topath


if TYPE_CHECKING:
    from flask import Flask
    from werkzeug.routing import Rule


# core ability


STATIC_RULE = re.compile("^(.*)/<path:filename>$")


def get_flask_static_folders(app: Flask) -> list[StaticFolder]:  # noqa: C901

    def get_static_folder(rule: Rule) -> str | None:
        bound_method = app.view_functions[rule.endpoint]
        if hasattr(bound_method, "static_folder"):
            return getattr(bound_method, "static_folder")
        # __self__ is the blueprint of send_static_file method
        if hasattr(bound_method, "__self__"):
            bp = getattr(bound_method, "__self__")
            if bp.has_static_folder:
                return bp.static_folder
        # now just a lambda :(
        return None

    def find_static(app: Flask) -> Iterator[StaticFolder]:
        if app.has_static_folder:
            prefix, folder = app.static_url_path, app.static_folder
            if folder is not None and isdir(folder):
                yield StaticFolder(
                    prefix,
                    topath(folder),
                    (not folder.endswith(prefix) if prefix else False),
                )
        for r in app.url_map.iter_rules():
            if not r.endpoint.endswith("static"):
                continue
            m = STATIC_RULE.match(r.rule)
            if not m:
                continue
            rewrite = False
            prefix = m.group(1)
            folder = get_static_folder(r)
            if folder is None:
                if r.endpoint != "static":
                    # static view_func for app is now
                    # just a lambda.
                    click.secho(
                        f"location: can't find static folder for endpoint: {r.endpoint}",
                        fg="red",
                        err=True,
                    )
                continue
            if not folder.endswith(prefix):
                rewrite = True

            if not isdir(folder):
                continue
            yield StaticFolder(prefix, topath(folder), rewrite)

    return list(set(find_static(app)))


def get_static_folders_for_app(
    application_dir: str,
    prefix: str = "",
    entrypoint: str | None = None,
) -> list[StaticFolder]:
    from flask import Flask

    app = find_application(
        application_dir,
        entrypoint or get_app_entrypoint(application_dir),
    )
    if isinstance(app, Flask):  # only place we need flask
        return [fixstatic(s, prefix) for s in get_flask_static_folders(app)]
    raise click.BadParameter(f"{app} is not a flask application!")


def find_application(application_dir: str, module: str) -> Any:
    import sys
    from importlib import import_module
    from click import style

    remove = False

    if ":" in module:
        module, attr = module.split(":", maxsplit=1)
    else:
        attr = "application"
    if application_dir not in sys.path:
        sys.path.append(application_dir)
        remove = True
    try:
        # FIXME: we really want to run this
        # under the virtual environment that this pertains too
        venv = sys.prefix
        click.secho(
            f"trying to load application ({module}) using {venv}: ",
            fg="yellow",
            nl=False,
            err=True,
        )
        with redirect_stderr(StringIO()) as stderr:
            m = import_module(module)
            app = getattr(m, attr, None)
        v = stderr.getvalue()
        if v:
            click.secho(f"got possible errors ...{style(v[-100:], fg='red')}", err=True)
        else:
            click.secho("ok", fg="green", err=True)
        if app is None:
            raise click.BadParameter(f"{attr} doesn't exist for module {module}")

        return app
    except (ImportError, AttributeError) as e:
        raise click.BadParameter(
            f"can't load application from {application_dir}: {e}",
        ) from e
    finally:
        if remove:
            sys.path.remove(application_dir)


def get_app_entrypoint(application_dir: str, default: str = "app.app") -> str:
    app = os.environ.get("FLASK_APP")
    if app is not None:
        return app
    dot = os.path.join(application_dir, ".flaskenv")
    if isfile(dot):
        cfg = get_dot_env(dot)
        if cfg is None:
            return default
        app = cfg.get("FLASK_APP")
        if app is not None:
            return app
    return default
