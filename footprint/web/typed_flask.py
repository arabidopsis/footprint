import os
import typing as t
from collections import defaultdict
from dataclasses import dataclass, field, replace
from shutil import copy

import click
from flask import Flask, Markup
from werkzeug.routing import Rule, parse_converter_args, parse_rule

from ..cli import cli
from ..config import INDENT, NL

from ..templating import (
    Environment,
    Template,
    get_env,
    get_template,
    get_template_filename,
)
from ..utils import multiline_comment
from .flask_api import Defaults, Error
from .typing import (
    BuildFunc,
    TSBuilder,
    TSClass,
    TSField,
    TSFunction,
    TSInterface,
    TSThing,
)


@dataclass
class URLFmt:
    """Element of a flask url /a/<b>/c/"""

    converter: t.Optional[str]
    args: t.Optional[t.Tuple[t.Tuple, t.Dict[str, t.Any]]]  # args and kwargs
    variable: str

    @property
    def is_static(self) -> bool:
        return self.converter is None

    @property
    def ts_type(self) -> str:
        if self.args and self.converter == "any":
            return " | ".join(repr(s) for s in self.args[0])
        if self.converter is None:
            return "string"
        return {
            "default": "string",
            "int": "number",
            "float": "number",
            "any": "string",
            "path": "string",
        }.get(self.converter, self.converter)


@dataclass
class TSRule:
    endpoint: str
    rule: str
    methods: t.Tuple[str, ...]
    """original rule"""
    url_fmt_arguments: t.Tuple[URLFmt, ...]
    url: str
    """url string with expected arguments as js template values ${var}"""
    url_arguments: t.Tuple[str, ...]
    defaults: t.Mapping[str, t.Any]
    """default arguments"""

    def ts_args(self) -> t.Dict[str, str]:
        ret = {}
        for fmt in self.url_fmt_arguments:
            if fmt.is_static:
                continue
            ret[fmt.variable] = fmt.ts_type
        return ret

    def inject_url_defaults(self, app: Flask) -> "TSRule":
        values = dict(self.defaults)  # make copy
        # usually called by url_for
        app.inject_url_defaults(self.endpoint, values)
        if not values:
            return self
        return self.resolve_defaults(values)

    def resolve_defaults(self, values: t.Dict[str, t.Any]) -> "TSRule":
        v = {}
        url_arguments = list(self.url_arguments)
        for a in self.url_arguments:
            if a in values:
                v[a] = values[a]
                url_arguments.remove(a)
            else:
                v[a] = f"${{{a}}}"
        url = "".join(
            f.variable if f.is_static else "{%s}" % f.variable
            for f in self.url_fmt_arguments
        )
        url = url.format(**v)

        def update_fmt(fmt: URLFmt) -> URLFmt:
            if fmt.is_static or fmt.variable in url_arguments:
                return fmt
            # make static
            return replace(fmt, converter=None, args=None)

        url_fmt_arguments = tuple(update_fmt(fmt) for fmt in self.url_fmt_arguments)

        return replace(
            self,
            url=url,
            url_arguments=tuple(url_arguments),
            url_fmt_arguments=url_fmt_arguments,
        )


def process_rule(r: Rule) -> TSRule:
    url_fmt_arguments = [
        URLFmt(u[0], parse_converter_args(u[1]) if u[1] is not None else None, u[2])
        for u in parse_rule(r.rule)
    ]
    url = "".join(
        f.variable if f.is_static else "${%s}" % f.variable for f in url_fmt_arguments
    )
    url_arguments = [f.variable for f in url_fmt_arguments if not f.is_static]
    assert set(url_arguments) == r.arguments, r
    assert r.methods is not None, r
    return TSRule(
        endpoint=r.endpoint,
        rule=r.rule,
        methods=tuple(r.methods),
        url_fmt_arguments=tuple(url_fmt_arguments),
        url=url,
        url_arguments=tuple(url_arguments),
        defaults=r.defaults or {},
    )


# marries A function with the Flask Rule that is associated with it
@dataclass
class Restful:
    function: TSFunction
    rule: TSRule

    def inject_url_defaults(self, app: "Flask") -> "Restful":
        values: t.Dict[str, t.Any] = dict(self.rule.defaults)
        app.inject_url_defaults(self.rule.endpoint, values)
        if not values:
            return self
        return self.resolve_defaults(values)

    def resolve_defaults(self, values: t.Dict[str, t.Any]) -> "Restful":
        rule = self.rule.resolve_defaults(values)
        function = self.function.remove_args(*values)
        return replace(self, rule=rule, function=function)


def build_interface(
    name: str, fields: t.Iterable[Restful], *, as_jquery=True
) -> TSInterface:
    lfields = [
        TSField(
            name=r.function.name,
            type=r.function.build_promise(as_jquery=as_jquery).anonymous(),
        )
        for r in fields
    ]
    return TSInterface(name=name, fields=lfields)


@dataclass
class ClassBuilder:
    view: "JSView"
    indent: str = INDENT
    nl: str = NL
    as_ts: bool = True
    as_jquery: bool = True
    export: bool = False

    def build(self) -> TSClass:
        nl, indent = self.nl, self.indent
        funcs = []
        tab = f"{nl}{indent}"
        for r in self.view.restful:
            rule = r.rule
            function = r.function.build_promise(as_jquery=self.as_jquery)
            data = []
            for arg in function.args:
                if arg.name in rule.url_arguments:
                    continue
                if arg.is_dataclass:
                    data.append(f"...{arg.name}")
                else:
                    data.append(arg.name)

            # We user $data since this is a variable name not in python
            body = (
                [f'const $data = {{ {", ".join(data)} }}']
                if data
                else ["const $data = {}"]
            )

            body.extend(self.jquery_body(r) if self.as_jquery else self.fetch_body(r))

            function = replace(
                function,
                export=False,
                body=f"{indent}{tab.join(body)}",
            )

            funcs.append(
                TSField(
                    name=function.name,
                    type=function.anonymous(as_ts=self.as_ts),
                    colon="",
                )
            )

        app = TSClass(
            name=self.view.name.title(),
            fields=funcs,
            as_ts=self.as_ts,
            indent=indent,
            nl=nl,
            export=self.export,
        )
        return app

    def method(self, restful: Restful) -> str:
        methods = restful.rule.methods
        if restful.function.requires_post:
            assert "POST" in methods, restful
            return "post"
        method = "get" if "GET" in methods else ("post" if "POST" in methods else None)
        if method is None:
            raise ValueError(f"no get/post method for rule {restful.rule}")
        return method

    def jquery_body(self, restful: Restful) -> t.List[str]:
        method = self.method(restful)
        return [
            f"""return $.ajax({{
            url: `{restful.rule.url}`,
            data: $data,
            method: {method.upper()}
            }})"""
        ]

    def fetch_body(self, restful: Restful) -> t.List[str]:
        method = self.method(restful)
        return [f"return {method}(`{restful.rule.url}`, $data)"]


@dataclass
class JSView:
    name: str  # blueprint name
    restful: t.List[Restful]  # list of endpoints
    dependencies: t.List[TSThing]  # dependencies
    folder: str  # template folder
    interface: t.Optional[TSInterface] = None
    preamble_ts: str = "import {get, post} from './fetch-lib.js'"
    as_jquery: bool = False
    nl: str = NL

    def build_class(self, as_ts: bool = True):
        builder = ClassBuilder(
            self, as_ts=as_ts, export=as_ts, as_jquery=self.as_jquery
        )
        return builder.build()

    def build_interface(self):
        return build_interface(
            self.name.title(), self.restful, as_jquery=self.as_jquery
        )

    def inject_url_defaults(self, app: Flask) -> "JSView":

        restful = [r.inject_url_defaults(app) for r in self.restful]
        ret = replace(self, restful=restful)
        ret.interface = ret.build_interface()
        return ret

    def resolve_defaults(self, values: Defaults) -> "JSView":
        if not values:
            return self
        empty: t.Dict[str, t.Any] = {}
        restful = [
            r.resolve_defaults(values.get(r.rule.endpoint, empty)) for r in self.restful
        ]
        ret = replace(self, restful=restful)
        ret.interface = ret.build_interface()
        return ret

    def build_jsapi(
        self,
        global_name: t.Optional[str],  # supplied by user {{jsapi(name='...')}}
        template: Template,
        app: t.Optional["Flask"] = None,
    ) -> str:
        # dynamic generation of api
        if app is not None:
            v = self.inject_url_defaults(app)
        else:
            v = self

        cls = v.build_class(as_ts=False)

        # template = ctx.env.get_template("web/js_api.tjs")
        return template.render(
            interface=cls, global_name=global_name, jquery=self.as_jquery
        )

    def build_tsapi(
        self, with_class: bool = False, values: t.Optional[Defaults] = None
    ) -> str:
        if not values:
            v = self
        else:
            v = self.resolve_defaults(values)
        if not self.as_jquery:
            out = [self.preamble_ts]
        else:
            out = []
        used = set()
        if values:
            used = {
                k
                for r in self.restful
                if r.rule.endpoint in values
                for k in values[r.rule.endpoint]
            }
            if used:
                out.extend(multiline_comment(f"url_defaults={used}"))
        out.extend(str(o) for o in v.dependencies)
        if v.interface is not None:
            out.append(str(v.interface))
        if not used or with_class:
            # we only output the class if there are no url defaults
            out.append(str(v.build_class(as_ts=True)))
            out.append(f"export const app = new {v.name.title()}Class()")
        return self.nl.join(out)

    def finalize(self) -> None:
        self.interface = self.build_interface()

    def is_finalized(self) -> bool:
        return self.interface is not None


@dataclass
class BuildContext:
    as_js: bool = False  # generate javascript instead of typescrit
    stdout: bool = False  # generate to stdout
    with_class: bool = False  # generate class implementation even if url_defaults exist
    global_name: str = "app"  # global name to add to window
    jsapi: str = "jsapi"  # name in template
    env: Environment = field(default_factory=get_env)


@dataclass
class FlaskApi:
    app: Flask
    views: t.Dict[str, JSView]
    defaults: t.Optional[Defaults] = None
    errors: t.Optional[t.List[str]] = None
    as_jquery: bool = True
    with_class: bool = False

    def jsviews(self) -> t.Iterable[JSView]:
        return self.views.values()

    def finalize(self):
        if not self.is_finalized():
            for view in self.jsviews():
                view.finalize()

    def is_finalized(self):
        return all(v.is_finalized() for v in self.jsviews())

    def context(self, blueprint: str) -> t.Dict[str, t.Callable[..., Markup]]:

        view = self.views[blueprint]
        template = get_template("web/js_api.tjs")

        def jsapi(view: JSView, name: t.Optional[str]) -> Markup:
            return Markup(view.build_jsapi(name or "app", template, self.app))

        def tsapi(view: JSView) -> Markup:
            return Markup(
                view.build_tsapi(with_class=self.with_class, values=self.defaults)
            )

        def jsapi_(
            as_ts: bool = False,
            name: t.Optional[str] = None,
            blueprint: t.Optional[str] = None,
        ) -> Markup:
            if blueprint is not None and blueprint in self.views:
                v = self.views[blueprint]
            else:
                v = view
            if as_ts:
                return tsapi(v)
            return jsapi(v, name)

        return dict(jsapi=jsapi_)

    def generate_view(self, view: JSView, ctx: BuildContext) -> None:
        as_ts = not ctx.as_js
        ext = "ts" if as_ts else "js"
        if not os.path.isdir(view.folder):
            os.makedirs(view.folder, exist_ok=True)

        output = f"{view.folder}/{view.name}_api.{ext}"

        def copyif(name: str) -> None:
            if not os.path.isdir(view.folder):
                return
            if os.path.isfile(os.path.join(view.folder, name)):
                return
            click.secho(f"copying {name} to {view.folder}", fg="blue")
            copy(get_template_filename(name), view.folder)

        # copyif("require.tjs")
        if not self.as_jquery:
            copyif("web/fetch-lib.ts")

        template = get_template("web/js_api.tjs")

        def do(fp: t.Optional[t.TextIO]) -> None:
            print(f"// {view.name}: {output}", file=fp)
            if as_ts:
                print(
                    view.build_tsapi(with_class=ctx.with_class, values=self.defaults),
                    file=fp,
                )
            else:
                print(view.build_jsapi(ctx.global_name, template, self.app), file=fp)

        if ctx.stdout:
            do(None)
        else:
            click.secho(f"writing to: {output}", fg="yellow")
            with open(output, "w") as fp:
                do(fp)

    def generate_api(self, ctx: BuildContext) -> None:
        if not self.is_finalized():
            self.finalize()
        for view in self.jsviews():
            self.generate_view(view, ctx)

    def add_processors(self) -> None:
        from flask import request

        # we need to copy the template to the
        # jinja environment so that {% include "./file.js" %} works
        with open(get_template_filename("web/require.tjs")) as fp:
            template = self.app.jinja_env.from_string(fp.read())

        requireall = getattr(template.module, "requireall")

        @self.app.context_processor
        def require():  # pylint: disable=unused-variable
            return dict(requireall=requireall)

        @self.app.context_processor
        def jsapi():  # pylint: disable=unused-variable
            return self.context(request.blueprint or self.app.name)


def is_api(func: t.Callable[..., t.Any]) -> bool:
    while True:
        if hasattr(func, "api_"):
            return True
        if hasattr(func, "__wrapped__"):
            func = func.__wrapped__  # type: ignore
            continue
        return False


def flask_api(  # noqa: C901
    app: "Flask",
    modules: t.Optional[t.Sequence[str]] = None,
    defaults: t.Optional[Defaults] = None,
    verbose: bool = True,
    add_error: bool = False,
    as_jquery: bool = False,
    with_class: bool = False,
    Base: t.Type[FlaskApi] = FlaskApi,
) -> FlaskApi:

    from importlib import import_module
    from os.path import join

    import flask
    from flask.scaffold import Scaffold

    assert app.template_folder is not None

    # 1. build namespace for typing.get_type_hints
    ns = flask.__dict__.copy()
    if modules is not None:
        for m in modules:
            mod = import_module(m)
            ns.update(mod.__dict__)

    # 2. default folder to generate *_api.ts
    appfolder = join(app.root_path, app.template_folder)

    views: t.Dict[str, JSView] = {}
    seen: t.Dict[str, t.Dict[str, str]] = defaultdict(dict)
    blueprint: Scaffold

    errors = []

    def try_build(
        name: str, view_func: t.Callable[..., t.Any]
    ) -> t.Optional[TSFunction]:
        try:
            builder = TSBuilder(ns=ns)
            ret = builder.get_func_ts(view_func)
            seen[name].update(builder.seen)
            return ret

        except (NameError, TypeError) as e:
            show_err(f"Error: {view_func.__name__} {e}")
            return None

    def try_call(func: BuildFunc) -> t.Optional[TSThing]:
        try:
            return func()
        except (NameError, TypeError) as e:
            show_err(f"Error {func.module}:{func.name}: {e}")
            return None

    def show_err(msg: str) -> None:
        if verbose:
            click.secho(msg, fg="red", err=True)
        errors.append(msg)

    def tojsname(name: str) -> str:
        if name.isidentifier():
            return name
        return name.replace("-", "_")

    # 3. loop url_rules
    for rule in app.url_map.iter_rules():
        # ignore static
        if rule.endpoint.endswith("static"):
            continue
        # ignore no GET or POST
        if rule.methods is None or (
            "GET" not in rule.methods and "POST" not in rule.methods
        ):
            continue
        # no view function
        if rule.endpoint not in app.view_functions:
            continue
        view_func = app.view_functions[rule.endpoint]

        # tagged by @FlaskAPI.api
        if not is_api(view_func):
            continue

        tsrule = process_rule(rule)
        bp = rule.endpoint.rpartition(".")[0]

        # find app or blueprint for this endpoint
        if bp in app.blueprints:
            blueprint = app.blueprints[bp]
        else:
            blueprint = app
        if blueprint.template_folder:
            folder = join(blueprint.root_path, blueprint.template_folder)
        else:
            folder = appfolder

        ts = try_build(blueprint.name, view_func)
        if ts is None:
            continue

        # check args match with url_rule
        args = tsrule.ts_args()
        tsargs = {a.name: a.type for a in ts.args if a.name in args}
        if not args == tsargs:
            show_err(f"incompatible args {tsrule.endpoint}: {args} {tsargs}")

        if ts.requires_post and "POST" not in rule.methods:
            show_err(f"function {tsrule.endpoint}: requires POST methods")

        # get or create a JSView for this endpoint
        if blueprint.name not in views:
            views[blueprint.name] = JSView(
                name=tojsname(blueprint.name),
                restful=[],
                dependencies=[],
                folder=folder,
                as_jquery=as_jquery,
            )
        jsview = views[blueprint.name]
        # add endpoint info
        jsview.restful.append(Restful(function=ts, rule=tsrule))

    # 4. generate dependencies
    for view in views.values():
        todo = seen[view.name]
        if add_error:
            # FIXME: the current @api returns an Error object on ValidationError
            todo[Error.__name__] = Error.__module__
        if todo:
            builder = TSBuilder(ns=ns)
            view.dependencies = [
                res
                for func in builder.process_seen(todo)
                for res in [try_call(func)]
                if res is not None
            ]

    ret = Base(
        app=app,
        views=views,
        defaults=defaults,
        as_jquery=as_jquery,
        with_class=with_class,
        errors=errors,
    )
    return ret


@cli.command()
@click.option(
    "-d",
    "--dir",
    "directory",
    metavar="DIRECTORY",
    default=".",
    help="directory to install typescript [default: current directory]",
    type=click.Path(exists=True, file_okay=False),
)
@click.option("-y", "--yes", is_flag=True, help="Answer yes to all questions")
@click.argument("packages", nargs=-1)
def typescript_install(packages: t.Sequence[str], directory: str, yes: bool) -> None:
    """Install typescript in current directory

    Installs jquery and toastr types by default.
    """
    from invoke import Context

    pgks = set(packages)
    pgks.update(["jquery", "toastr"])
    c = Context()
    run = c.run
    y = "-y" if yes else ""
    err = lambda msg: click.secho(msg, fg="red", bold=True)
    r = run("which npm", warn=True, hide=True)
    if r.failed:
        err("No npm in PATH!")
        raise click.Abort()

    with c.cd(directory):
        run(f"npm init {y}", pty=True)  # create package.json
        run("npm install --save-dev typescript")
        for package in pgks:
            r = run(f"npm install --save-dev @types/{package}", pty=True, warn=True)
            if r.failed:
                err(f"failed to install {package}")
        run("npx tsc --init", pty=True)  # create tsconfig.json
