from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import Any

    from flask import Flask
    from starlette.applications import Starlette
    from werkzeug.routing import Rule


@dataclass
class StaticFolder:
    """Represents a static folder that contains static assets for a website."""

    url: str | None
    folder: str
    rewrite: bool  # use nginx `rewrite {{url}}/(.*) /$1 break;``

    def with_prefix(self, prefix: str) -> StaticFolder:
        """Return a new StaticFolder instance with the specified prefix added to the URL."""
        url = prefix + (self.url or "")
        if url and self.folder.endswith(url):
            folder = self.folder[: -len(url)]
            return StaticFolder(url, folder, rewrite=False)
        return StaticFolder(url, self.folder, self.rewrite if not prefix else True)


def topath(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


STATIC_RULE = re.compile("^(.*)/<path:filename>$")


def is_starlette_app(app: Any) -> bool:  # noqa: ANN401
    try:
        from starlette.applications import Starlette

        return isinstance(app, Starlette)
    except ImportError:
        return False


def get_starlette_static_folders(app: Starlette) -> Iterator[StaticFolder]:
    from starlette.routing import BaseRoute, Mount, Router
    from starlette.staticfiles import StaticFiles

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
                    folder = str(topath(str(folder)))
                    path = prefix + r.path
                    rewrite = not folder.endswith(path)
                    yield StaticFolder(r.path, folder, rewrite)
                elif isinstance(r.app, Router):
                    yield from findstatic(r.app.routes, prefix + r.path)

    yield from findstatic(app.routes)


def get_starlette_route_prefixes(app: Starlette) -> Iterator[str]:
    from starlette.routing import BaseRoute, Mount, Router

    def findroute(
        routes: Sequence[BaseRoute],
        prefix: str = "",
    ) -> Iterator[str]:
        for r in routes:
            if isinstance(r, Mount):
                if isinstance(r.app, Router):
                    yield from findroute(r.app.routes, prefix + r.path)
                else:
                    yield re.escape(prefix + r.path)

    yield from findroute(app.routes)


def get_flask_static_folders(app: Flask) -> list[StaticFolder]:  # noqa: C901
    from os.path import isdir

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
                    print(  # noqa: T201
                        f"location: can't find static folder for endpoint: {r.endpoint}",
                        file=sys.stderr,
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

    if is_flask_app(app):  # only place we need flask
        return [s.with_prefix(prefix) for s in get_flask_static_folders(app)]
    if is_starlette_app(app):
        return [s.with_prefix(prefix) for s in get_starlette_static_folders(app)]
    msg = f"{app} is not a flask, quart, starlette or fastapi application!"
    raise ValueError(msg)


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

    if is_flask_app(app):  # only place we need flask
        urls = [prefix_from_rule(r.rule) for r in app.url_map.iter_rules()]
        urls = [u for u in urls if u and u != "/"]
        return list(set(urls))
    if is_starlette_app(app):
        return list(set(get_starlette_route_prefixes(app)))
    msg = f"{app} is not a flask, quart, starlette or fastapi application!"
    raise ValueError(msg)


def find_application(module: str, application_dir: str | None = None) -> Any:  # noqa: ANN401
    import sys
    from contextlib import redirect_stderr
    from importlib import import_module
    from io import StringIO

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
        print(  # noqa: T201
            f"trying to load application ({module}) using {sys.executable}: ",
            file=sys.stderr,
            end="",
        )
        sys.stderr.flush()
        with redirect_stderr(StringIO()) as stderr:
            m = import_module(module)
            app: Any = m
            for attr_str in attr.split("."):
                app = getattr(app, attr_str, None)
                if app is None:
                    msg = f"{attr_str} doesn't exist for module {module}"
                    raise ValueError(msg)
        v = stderr.getvalue()
        if v:
            print(f"got possible errors ...{v[-100:]}", file=sys.stderr)  # noqa: T201
        else:
            print("ok", file=sys.stderr)  # noqa: T201

    except (ImportError, AttributeError) as e:
        print("failed.", file=sys.stderr)  # noqa: T201
        msg = f"Can't load application from {application_dir}: {e}"
        print(msg, file=sys.stderr)  # noqa: T201
        raise SystemExit(1) from e
    finally:
        if remove:
            assert application_dir is not None  # noqa: S101
            sys.path.remove(application_dir)
    return app


def fix_path(s: str) -> str:
    return s.removeprefix("/")


def introspect(
    application_dir: Path, module: str, prefix: str = "", *, exclusive: bool
) -> tuple[list[StaticFolder], list[str]]:
    app = find_application(module, str(application_dir))
    folders = list(get_static_folders_for_app(app, prefix=prefix))
    routes: list[str] = []
    if exclusive:
        routes = sorted(get_route_prefixes(app))
        routes = [fix_path(r) for r in routes if r and r != "/"]
        if prefix:
            routes = [f"{prefix[1:]}/{r}" for r in routes]
    return folders, routes


def introspect_bg(
    python_executable: str | None,
    application_dir: Path,
    module: str,
    prefix: str = "",
    *,
    exclusive: bool,
    verbose: bool = False,
) -> tuple[list[StaticFolder], list[str]]:
    """Find package directories for given python. Guaranteed to return absolute paths.

    This runs a subprocess call, which generates a list of the directories in sys.path.
    """
    import ast
    import os
    import subprocess
    import sys

    if python_executable is None:
        python_executable = sys.executable

    if python_executable == sys.executable:
        return introspect(application_dir, module, prefix=prefix, exclusive=exclusive)

    env = {**dict(os.environ), "PYTHONSAFEPATH": "1"}
    args = []
    if verbose:
        args.append("--verbose")
    if exclusive:
        args.append("--exclusive")
    try:
        sd, routes = ast.literal_eval(
            subprocess.check_output(
                [
                    python_executable,
                    __file__,
                    "find-static",
                    f"--prefix={prefix}",
                    *args,
                    str(application_dir),
                    module,
                ],
                env=env,
                # stderr=subprocess.PIPE, # noqa: ERA001
            ).decode()
        )
        return [StaticFolder(**s) for s in sd], routes
    except subprocess.CalledProcessError as err:
        print(f"Error running: {' '.join(err.cmd)}", file=sys.stderr)  # noqa: T201
        raise SystemExit(1) from err

    except OSError as err:
        reason = os.strerror(err.errno) if err.errno is not None else "unknown error"
        msg = f"Invalid python executable '{python_executable}': {reason}"
        print(msg, file=sys.stderr)  # noqa: T201
        raise SystemExit(1) from err


def main() -> None:  # noqa: PLR0915
    """Introspect a Flask, Quart, Starlette or FastAPI application to find static folders and route prefixes."""
    import argparse
    import pprint

    parser = argparse.ArgumentParser(
        description="Introspect a Flask, Quart, Starlette or FastAPI"
        " application to find static folders and route prefixes."
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # find-static command
    find_static_parser = subparsers.add_parser(
        "find-static",
        help="Find static folders for an application",
    )
    find_static_parser.add_argument(
        "--prefix",
        default="",
        help="URL prefix for static folders",
    )
    find_static_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show traceback on error.",
    )
    find_static_parser.add_argument(
        "--exclusive",
        action="store_true",
        help="Whether to only show exclusive static folders and routes.",
    )
    find_static_parser.add_argument(
        "application_dir",
        type=Path,
        help="Path to the application directory",
    )
    find_static_parser.add_argument(
        "module",
        type=str,
        help="Module path to the application",
    )

    # introspect command
    introspect_parser = subparsers.add_parser(
        "introspect",
        help="Introspect an application using a Python executable",
    )
    introspect_parser.add_argument(
        "--python-executable",
        type=str,
        default=None,
        help="path to workspace's Python executable",
    )
    introspect_parser.add_argument(
        "--prefix",
        default="",
        help="URL prefix for static folders",
    )
    introspect_parser.add_argument(
        "--exclusive",
        action="store_true",
        help="Whether to only show exclusive static folders and routes.",
    )
    introspect_parser.add_argument(
        "application_dir",
        type=Path,
        help="Path to the application directory",
    )
    introspect_parser.add_argument(
        "module",
        type=str,
        help="Module path to the application",
    )

    args = parser.parse_args()

    if args.command == "find-static":
        if not args.application_dir.exists():
            print(f"Error: application_dir '{args.application_dir}' does not exist", file=sys.stderr)  # noqa: T201
            raise SystemExit(1)
        if not args.application_dir.is_dir():
            print(f"Error: application_dir '{args.application_dir}' is not a directory", file=sys.stderr)  # noqa: T201
            raise SystemExit(1)
        try:
            sd, routes = introspect(args.application_dir, args.module, prefix=args.prefix, exclusive=args.exclusive)
            sd2 = [asdict(s) for s in sd]

            pprint.pprint([sd2, routes])  # noqa: T203
        except Exception as e:
            if args.verbose:
                import traceback

                tb = traceback.format_exc()
                print(f"Traceback:\n{tb}", file=sys.stderr)  # noqa: T201
            else:
                print(f"Error introspecting {args.module}: {type(e).__name__}({e})", file=sys.stderr)  # noqa: T201
            raise SystemExit(1) from e

    elif args.command == "introspect":
        if not args.application_dir.exists():
            print(f"Error: application_dir '{args.application_dir}' does not exist", file=sys.stderr)  # noqa: T201
            raise SystemExit(1)
        if not args.application_dir.is_dir():
            print(f"Error: application_dir '{args.application_dir}' is not a directory", file=sys.stderr)  # noqa: T201
            raise SystemExit(1)
        try:
            sd, routes = introspect_bg(
                args.python_executable, args.application_dir, args.module, prefix=args.prefix, exclusive=args.exclusive
            )

            pprint.pprint([sd, routes])  # noqa: T203
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)  # noqa: T201
            raise SystemExit(1) from e

    else:
        parser.print_help()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
