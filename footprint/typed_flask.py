import collections
import typing as t
from collections import defaultdict
from dataclasses import MISSING, dataclass
from dataclasses import fields as dcfields
from dataclasses import make_dataclass, replace
from functools import wraps
from types import FunctionType

import click
from flask import Flask, Markup, jsonify, request
from marshmallow import Schema
from marshmallow.exceptions import ValidationError
from marshmallow.fields import Nested
from werkzeug.datastructures import CombinedMultiDict, MultiDict
from werkzeug.routing import Rule, parse_converter_args, parse_rule

from .cli import cli
from .typing import (
    DataClassJsonMixin,
    TSBuilder,
    TSClass,
    TSField,
    TSFunction,
    TSInterface,
    get_annotations,
    is_dataclass_type,
)

CMultiDict = t.Union[MultiDict, CombinedMultiDict]


def make_arg_dataclass(func: FunctionType) -> t.Type[DataClassJsonMixin]:
    from dataclasses import field

    items: t.List[
        t.Union[t.Tuple[str, t.Type[t.Any]], t.Tuple[str, t.Type[t.Any], t.Any]]
    ] = []
    for anno in get_annotations(func).values():
        if anno.name == "return":
            continue
        if anno.default != MISSING:
            items.append((anno.name, anno.type, field(default=anno.default)))
        else:
            items.append((anno.name, anno.type))

    return t.cast(
        t.Type[DataClassJsonMixin],
        make_dataclass(func.__name__.title(), items, bases=(DataClassJsonMixin,)),
    )


def update_dataclasses(schema: Schema, data: t.Dict[str, t.Any]) -> None:
    # because we have a flat request.form object
    # currently. We supply data to nested
    # schema from the top level data source
    for k, f in schema.fields.items():
        if isinstance(f, Nested):
            s = f.nested
            if isinstance(s, Schema):
                data[k] = s.load(data, unknown="exclude").to_dict()


StrOrList = t.Union[str, t.List[str]]


def request_fixer(
    datacls: t.Type[DataClassJsonMixin],
) -> t.Callable[[CMultiDict], t.Dict[str, StrOrList]]:
    def get(name):
        return lambda md: md.get(name)

    def getlist(name):
        return lambda md: md.getlist(name)

    def request_fixer_inner(
        datacls: t.Type[DataClassJsonMixin],
    ) -> t.Dict[str, t.Callable[[CMultiDict], StrOrList]]:
        getters = {}
        for f in dcfields(datacls):
            typ = f.type
            if is_dataclass_type(typ):
                for k, v in request_fixer_inner(
                    t.cast(t.Type[DataClassJsonMixin], typ)
                ).items():
                    getters[k] = v
                continue
            if hasattr(typ, "__origin__"):
                typ = typ.__origin__
            if issubclass(typ, collections.abc.Sequence) and not issubclass(
                typ, (str, bytes)
            ):
                getters[f.name + "[]"] = getlist(f.name + "[]")
                getters[f.name] = getlist(f.name)
            else:
                getters[f.name] = get(f.name)
        return getters

    getters = request_fixer_inner(datacls)

    def chop(name: str):
        if name.endswith("[]"):
            return name[:-2]
        return name

    def fix_request(md: CMultiDict) -> t.Dict[str, StrOrList]:
        ret = {}
        for k, getter in getters.items():
            if k not in md:
                continue
            ret[chop(k)] = getter(md)
        return ret

    return fix_request


def call_form(func: FunctionType) -> t.Callable[[CMultiDict], t.Any]:

    dc = make_arg_dataclass(func)
    assert issubclass(dc, DataClassJsonMixin)
    fixer = request_fixer(dc)
    schema = dc.schema()  # pylint: disable=no-member

    def call(md: CMultiDict, **kwargs):
        assert set(kwargs) <= set(schema.fields.keys())

        ret = fixer(md)
        update_dataclasses(schema, ret)
        ret.update(kwargs)

        dci = schema.load(ret, unknown="exclude")
        return func(**{f.name: getattr(dci, f.name) for f in dcfields(dci)})

    return call


@dataclass
class Errors(DataClassJsonMixin):
    status: str
    msg: str
    errors: t.Dict[str, t.List[str]]


def flatten(d: t.Dict[str, t.Any]) -> t.Dict[str, t.List[str]]:
    # error messges can be {'attr': {'0': [msg]} }
    # we flatten this to {'attr': [msg] }
    ret = {}
    for k, v in d.items():
        msgs = []
        if isinstance(v, dict):
            for m in flatten(v).values():  # pylint: disable=no-member
                msgs.extend(m)
        elif isinstance(v, list):
            msgs.extend(str(s) for s in v)
        else:
            msgs.append(str(v))
        ret[k] = msgs
    return ret


def decorator(func):

    caller = call_form(func)

    @wraps(func)
    def api(*args, **kwargs):
        # kwargs are from url_defaults such as:
        # '/path/<project>/<int:page>'
        try:
            ret = caller(request.values, **kwargs)
            if isinstance(ret, DataClassJsonMixin):
                return jsonify(ret.to_dict())
            return ret
        except ValidationError as e:
            ret = jsonify(
                Errors(
                    status="FAILED",
                    msg="Validation error",
                    errors=flatten(e.normalized_messages()),
                ).to_dict()
            )
            ret.status = 400
            return ret

    return api


@dataclass
class Fmt:
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
    url_fmt_arguments: t.Tuple[Fmt, ...]
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

        def update_fmt(fmt: Fmt) -> Fmt:
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
        Fmt(u[0], parse_converter_args(u[1]) if u[1] is not None else None, u[2])
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


class Restful(t.NamedTuple):
    function: TSFunction
    rule: TSRule


def to_interface(
    name: str, fields: t.Iterable[Restful], *, as_jquery=True
) -> TSInterface:
    lfields = [
        TSField(
            name=r.function.name,
            type=r.function.to_promise(as_jquery=as_jquery).anonymous(),
        )
        for r in fields
    ]
    return TSInterface(name=name, fields=lfields)


def to_class(
    name: str,
    fields: t.Iterable[Restful],
    *,
    indent="    ",
    nl="\n",
    as_ts: bool = True,
    as_jquery: bool = True,
    export: bool = False,
    defaults: t.Optional[t.Dict[str, t.Dict[str, t.Any]]] = None,
) -> TSClass:
    funcs = []
    if defaults is None:
        defaults = {}
    tab = f"{nl}{indent}"
    for r in fields:
        rule = r.rule
        function = r.function
        d = defaults.get(rule.endpoint)
        if d:
            rule = rule.resolve_defaults(d)
            function = function.remove_args(*d)
        function = function.to_promise(as_jquery=as_jquery)
        data = []
        for arg in function.args:
            if arg.name in rule.url_arguments:
                continue
            if arg.is_dataclass:
                data.append(f"...{arg.name}")
            else:
                data.append(arg.name)
        methods = rule.methods
        method = "get" if "GET" in methods else ("post" if "POST" in methods else None)
        if method is None:
            raise ValueError(f"no get/post method for rule {rule}")

        body = (
            [f'const $data = {{ {", ".join(data)} }}'] if data else ["const $data = {}"]
        )
        body.append(f"return $.{method}(`{rule.url}`, $data)")

        function = replace(
            function,
            export=False,
            body=f"{indent}{tab.join(body)}",
        )

        funcs.append(
            TSField(name=function.name, type=function.anonymous(as_ts=as_ts), colon="")
        )

    app = TSClass(
        name=name, fields=funcs, as_ts=as_ts, indent=indent, nl=nl, export=export
    )
    return app


@dataclass
class JSView:
    name: str
    code: t.Dict[str, Restful]
    extra: t.List[str]
    folder: str
    interface: t.Optional[TSInterface] = None

    def to_class(self, as_ts: bool = True):
        return to_class(
            self.name.title(), self.code.values(), as_ts=as_ts, export=as_ts
        )

    def create_interface(self):
        self.interface = to_interface(self.name.title(), self.code.values())
        return self.interface


@dataclass
class Built:
    views: t.Dict[str, JSView]

    def jsviews(self) -> t.Iterable[JSView]:
        return self.views.values()

    def context(
        self, blueprint: str, global_name="app"
    ) -> t.Dict[str, t.Callable[[], Markup]]:

        view = self.views[blueprint]

        def jsapi():
            cls = view.to_class(as_ts=False)
            s = "\n".join(
                [
                    "(function() {",
                    str(cls),
                    f"window.{global_name} = new {cls.name}Class()",
                    "})();",
                ]
            )
            return Markup(s)

        def tsapi():
            s = list(view.extra)
            if view.interface is not None:
                s.append(str(view.interface))
            s.append(str(view.to_class()))
            return Markup("\n".join(s))

        return dict(jsapi=jsapi, tsapi=tsapi)


def flask_api(app: "Flask", modules: t.Sequence[str]) -> Built:

    from importlib import import_module
    from os.path import join

    import flask
    from flask.scaffold import Scaffold

    ns = flask.__dict__.copy()

    for m in modules:
        mod = import_module(m)
        ns.update(mod.__dict__)
    assert app.template_folder is not None
    appfolder = join(app.root_path, app.template_folder)
    views: t.Dict[str, JSView] = {}
    seen: t.Dict[str, t.Dict[str, str]] = defaultdict(dict)
    blueprint: Scaffold
    for rule in app.url_map.iter_rules():
        if rule.endpoint.endswith("static"):
            continue
        if rule.methods is None or (
            "GET" not in rule.methods and "POST" not in rule.methods
        ):
            continue
        tsrule = process_rule(rule)
        view_func = app.view_functions[rule.endpoint]
        bp = rule.endpoint.rpartition(".")[0]
        if bp in app.blueprints:
            blueprint = app.blueprints[bp]
        else:
            blueprint = app
        if blueprint.template_folder:
            folder = join(blueprint.root_path, blueprint.template_folder)
        else:
            folder = appfolder

        try:
            builder = TSBuilder(ns=ns)
            ts = builder.get_func_ts(view_func)
            args = tsrule.ts_args()
            tsargs = {a.name: a.type for a in ts.args if a.name in args}
            if not args == tsargs:
                click.secho(
                    f"incompatible args {tsrule.endpoint}: {args} {tsargs}",
                    fg="red",
                    err=True,
                )
            if blueprint.name not in views:
                views[blueprint.name] = JSView(blueprint.name, {}, [], folder)
            jsview = views[blueprint.name]
            jsview.code[ts.name] = Restful(function=ts, rule=tsrule)

            seen[blueprint.name].update(builder.seen)
        except (NameError, TypeError) as e:
            err = click.style(f"{view_func.__name__}: {e}", fg="red")
            click.echo(err, err=True)
        # print(bp, p.endpoint, p.ts_args(), ts)
    for view in views.values():
        todo = seen[view.name]
        if todo:
            builder = TSBuilder(ns=ns)
            view.extra = [str(func()) for func in builder.process_seen(todo)]

        view.create_interface()
    return Built(views)


def show_view(view: JSView, as_js: bool = False) -> None:
    as_ts = not as_js
    print(f"// {view.name}: {view.folder}")
    if as_ts:
        # need extra type structures
        for o in view.extra:
            print(o)
        print(view.interface)

    cls = view.to_class(as_ts=as_ts)
    print(cls)
    if as_ts:
        print(f"export const app = new {cls.name}Class()")
    else:
        print(f"window.app = new {cls.name}Class()")


def show_api(built: Built, as_js: bool = False) -> None:
    for view in built.jsviews():
        show_view(view, as_js=as_js)


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
        err("No npm!")
        raise click.Abort()

    with c.cd(directory):
        run(f"npm init {y}", pty=True)  # create package.json
        run("npm install --save-dev typescript")
        for package in pgks:
            r = run(f"npm install --save-dev @types/{package}", pty=True, warn=True)
            if r.failed:
                err(f"failed to install {package}")
        run("npx tsc --init", pty=True)  # create tsconfig.json
