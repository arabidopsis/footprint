from __future__ import annotations

import os
import re
from contextlib import redirect_stderr
from io import StringIO
from os.path import abspath
from os.path import expanduser
from os.path import isdir
from os.path import isfile
from os.path import normpath
from typing import Iterator
from typing import NamedTuple

from click import BadParameter
from click import secho
from click import style
from flask import Flask
from werkzeug.routing import Rule


# core ability


class StaticFolder(NamedTuple):
    url: str | None
    folder: str
    rewrite: bool  # use nginx `rewrite {{url}}/(.*) /$1 break;``


STATIC_RULE = re.compile("^(.*)/<path:filename>$")


def topath(path: str) -> str:
    return normpath(abspath(expanduser(path)))


def get_static_folders(app: Flask) -> list[StaticFolder]:  # noqa: C901

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
                    secho(
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
    app: Flask | None = None,
    prefix: str = "",
    entrypoint: str | None = None,
) -> list[StaticFolder]:
    def fixstatic(s: StaticFolder) -> StaticFolder:
        url = prefix + (s.url or "")
        if url and s.folder.endswith(url):
            path = s.folder[: -len(url)]
            return StaticFolder(url, path, False)
        return StaticFolder(url, s.folder, s.rewrite if not prefix else True)

    if app is None:
        app = find_application(
            application_dir,
            entrypoint or get_app_entrypoint(application_dir),
        )
    return [fixstatic(s) for s in get_static_folders(app)]


def find_application(application_dir: str, module: str) -> Flask:
    import sys
    from importlib import import_module

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
        secho(
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
            secho(f"got possible errors ...{style(v[-100:], fg='red')}", err=True)
        else:
            secho("ok", fg="green", err=True)
        if app is None:
            raise BadParameter(f"{attr} doesn't exist for module {module}")
        if not isinstance(app, Flask):
            raise BadParameter(f"{app} is not a flask application!")

        return app
    except (ImportError, AttributeError) as e:
        raise BadParameter(f"can't load application from {application_dir}: {e}") from e
    finally:
        if remove:
            sys.path.remove(application_dir)


def get_dot_env(fname: str) -> dict[str, str | None] | None:
    try:
        from dotenv import dotenv_values

        return dotenv_values(fname)
    except ImportError:
        import click

        click.secho(
            '".flaskenv" file detected but no python-dotenv module found',
            fg="yellow",
            bold=True,
            err=True,
        )
        return None


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
