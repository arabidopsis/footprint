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

    return list(find_static(app))


def is_flask_app(app: Any) -> bool:
    try:
        try:
            # flask and quart obey these
            from flask.sansio.app import App  # type: ignore

            return isinstance(app, App)
        except ModuleNotFoundError:
            from flask import Flask

            return isinstance(app, Flask)
    except ImportError:
        return False


def get_static_folders_for_app(
    application_dir: str,
    entrypoint: str,
    *,
    prefix: str = "",
) -> list[StaticFolder]:
    from .asgi import get_starlette_static_folders
    from .asgi import is_starlette_app

    app = find_application(
        entrypoint,
        application_dir,
    )

    if is_flask_app(app):  # only place we need flask
        return [fixstatic(s, prefix) for s in get_flask_static_folders(app)]
    elif is_starlette_app(app):
        return [fixstatic(s, prefix) for s in get_starlette_static_folders(app)]
    raise click.BadParameter(
        f"{app} is not a flask, quart, starlette or fastapi application!",
    )


def find_application(module: str, application_dir: str | None = None) -> Any:
    import sys
    from importlib import import_module
    from click import style

    remove = False

    if ":" in module:
        module, attr = module.split(":", maxsplit=1)
    else:
        attr = "application"
    if application_dir and application_dir not in sys.path:
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
            app: Any = m
            for attr_str in attr.split("."):
                app = getattr(app, attr_str, None)
                if app is None:
                    raise click.BadParameter(
                        f"{attr} doesn't exist for module {module}",
                    )
        v = stderr.getvalue()
        if v:
            click.secho(f"got possible errors ...{style(v[-100:], fg='red')}", err=True)
        else:
            click.secho("ok", fg="green", err=True)

        return app
    except (ImportError, AttributeError) as e:
        raise click.BadParameter(
            f"can't load application from {application_dir}: {e}",
        ) from e
    finally:
        if remove:
            assert application_dir is not None
            sys.path.remove(application_dir)


def get_app_entrypoint(
    application_dir: str,
    *,
    asgi: bool,
    default: str = "app.app:application",
) -> str:
    if asgi:
        ENVS = ["QUART_APP", "FASTAPI_APP", "UVICORN_APP"]
        dotenvs = [".quartenv", ".fastapienv", ".env"]
    else:
        ENVS = ["FLASK_APP"]
        dotenvs = [".flaskenv", ".env"]
    for e in ENVS:
        app = os.environ.get(e)
        if app is not None:
            return app
    for dotenv in dotenvs:
        dot = os.path.join(application_dir, dotenv)
        if isfile(dot):
            cfg = get_dot_env(dot)
            if cfg is None:
                continue
            for e in ENVS:
                app = cfg.get(e)
                if app is not None:
                    return app
    return default
