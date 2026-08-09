from __future__ import annotations

import os
import re
from contextlib import redirect_stderr
from io import StringIO
from os.path import isdir
from typing import TYPE_CHECKING, Any

import click

from .utils import StaticFolder, get_dot_env, topath

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from flask import Flask
    from werkzeug.routing import Rule


# core ability


STATIC_RULE = re.compile("^(.*)/<path:filename>$")


def get_flask_static_folders(app: Flask) -> list[StaticFolder]:  # noqa: C901

    def get_static_folder(rule: Rule) -> str | None:
        bound_method = app.view_functions[rule.endpoint]
        if hasattr(bound_method, "static_folder"):
            return getattr(bound_method, "static_folder", None)
        # __self__ is the blueprint of send_static_file method
        if hasattr(bound_method, "__self__"):
            bp = getattr(bound_method, "__self__", None)
            if bp and getattr(bp, "has_static_folder", False):
                return getattr(bp, "static_folder", None)
        # now just a lambda :(
        return None

    def find_static(app: Flask) -> Iterator[StaticFolder]:  # noqa: C901
        has_static = False
        if app.has_static_folder:
            prefix, folder = app.static_url_path, app.static_folder
            if folder is not None and isdir(folder):  # noqa: PTH112
                yield StaticFolder(
                    prefix,
                    str(topath(folder)),
                    (not folder.endswith(prefix) if prefix else False),
                )
                has_static = True
        for r in app.url_map.iter_rules():
            if not r.endpoint.endswith("static"):
                continue
            if has_static and r.endpoint == "static":
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

            if not isdir(folder):  # noqa: PTH112
                continue
            yield StaticFolder(prefix, str(topath(folder)), rewrite)

    return list(find_static(app))


def is_flask_app(app: Any) -> bool:  # noqa: ANN401
    try:
        try:
            # flask and quart obey these
            from flask.sansio.app import App  # pyright: ignore[reportMissingImports]

            return isinstance(app, App)
        except ModuleNotFoundError:
            from flask import Flask

            return isinstance(app, Flask)
    except ImportError:
        return False


def get_static_folders_for_app(app: Any, *, prefix: str = "") -> list[StaticFolder]:  # noqa: ANN401
    from .asgi import get_starlette_static_folders, is_starlette_app

    if is_flask_app(app):  # only place we need flask
        return [s.with_prefix(prefix) for s in get_flask_static_folders(app)]
    if is_starlette_app(app):
        return [s.with_prefix(prefix) for s in get_starlette_static_folders(app)]
    msg = f"{app} is not a flask, quart, starlette or fastapi application!"
    raise click.BadParameter(
        msg,
    )


def prefix_from_rule(rule: str) -> str:
    def replace(match: re.Match[str]) -> str:
        match_str = match.group(1)
        if match_str.startswith("path:"):
            return ".+"
        return "[^/]+"

    rule = re.escape(rule)
    return re.sub(r"<([^>]+)>", replace, rule)


def prefix_from_rule2(rule: str) -> str:
    if "<" not in rule:
        return rule
    return rule.split("<", 1)[0]


def get_route_prefixes(app: Any) -> list[str]:  # noqa: ANN401
    from .asgi import get_starlette_route_prefixes, is_starlette_app

    if is_flask_app(app):  # only place we need flask
        urls = [prefix_from_rule(r.rule) for r in app.url_map.iter_rules()]
        urls = [u for u in urls if u and u != "/"]
        return list(set(urls))
    if is_starlette_app(app):
        return list(set(get_starlette_route_prefixes(app)))
    msg = f"{app} is not a flask, quart, starlette or fastapi application!"
    raise click.BadParameter(
        msg,
    )


def find_application(module: str, application_dir: str | None = None) -> Any:  # noqa: ANN401
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
        # We really want to run this
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
                    msg = f"{attr_str} doesn't exist for module {module}"
                    raise click.BadParameter(
                        msg,
                    )
        v = stderr.getvalue()
        if v:
            click.secho(f"got possible errors ...{style(v[-100:], fg='red')}", err=True)
        else:
            click.secho("ok", fg="green", err=True)

    except (ImportError, AttributeError) as e:
        msg = f"can't load application from {application_dir}: {e}"
        raise click.BadParameter(
            msg,
        ) from e
    finally:
        if remove:
            assert application_dir is not None  # noqa: S101
            sys.path.remove(application_dir)
    return app


def get_app_entrypoint(  # noqa: C901
    application_dir: Path,
    *,
    asgi: bool,
    default: str = "app.app:application",
) -> str:
    if asgi:
        envs = ["QUART_APP", "FASTAPI_APP", "UVICORN_APP"]
        dotenvs = [".quartenv", ".fastapienv", ".env"]
    else:
        envs = ["FLASK_APP"]
        dotenvs = [".flaskenv", ".env"]
    for e in envs:
        app = os.environ.get(e)
        if app is not None:
            if asgi and ":" not in app:
                app += ":application"
            return app
    for dotenv in dotenvs:
        dot = application_dir / dotenv
        if dot.is_file():
            cfg = get_dot_env(dot)
            if cfg is None:
                continue
            for e in envs:
                app = cfg.get(e)
                if app is not None:
                    if asgi and ":" not in app:
                        app += ":application"
                    return app
    return default
