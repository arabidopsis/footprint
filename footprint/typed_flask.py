import collections
import typing as t
from collections import defaultdict
from dataclasses import MISSING, dataclass
from dataclasses import fields as dcfields
from dataclasses import make_dataclass, replace
from functools import wraps

import click
from flask import Flask, Markup, jsonify, request
from marshmallow import Schema
from marshmallow.exceptions import ValidationError
from marshmallow.fields import Nested
from typing_extensions import Literal
from werkzeug.datastructures import CombinedMultiDict, MultiDict
from werkzeug.routing import Rule, parse_converter_args, parse_rule

from .cli import cli
from .typing import (
    BuildFunc,
    DataClassJsonMixin,
    TSBuilder,
    TSClass,
    TSField,
    TSFunction,
    TSInterface,
    TSThing,
    get_annotations,
    is_dataclass_type,
)

CMultiDict = t.Union[MultiDict, CombinedMultiDict]

# endpoint to default arguments
Defaults = t.Dict[str, t.Dict[str, t.Any]]


def make_arg_dataclass(func: t.Callable[..., t.Any]) -> t.Type[DataClassJsonMixin]:
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

    names_seen = set()

    def request_fixer_inner(
        datacls: t.Type[DataClassJsonMixin],
    ) -> t.Dict[str, t.Callable[[CMultiDict], StrOrList]]:
        getters = {}
        for f in dcfields(datacls):
            if f.name in names_seen:
                # TODO make this a logging statement
                click.secho(f"warning name overlap {f.name}", fg="yellow", err=True)
            names_seen.add(f.name)
            # TODO get mm_field.type
            # see typing:get_field_type
            # we want the type as it is on the inner side
            typ = f.type
            if is_dataclass_type(typ):
                for k, v in request_fixer_inner(
                    t.cast(t.Type[DataClassJsonMixin], typ)
                ).items():
                    getters[k] = v
                continue
            if hasattr(typ, "__args__"):
                # e.g. value: Union[str,int,MyBlah]
                raise TypeError(
                    f"Too Complex: can't do arguments for {f.name}: {typ.__args__}!"
                )
            if hasattr(typ, "__origin__"):
                typ = typ.__origin__
            # TODO: we can't to list of complex objects
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


F = t.TypeVar("F", bound=t.Callable[..., t.Any])


class Call:
    def __init__(self, func: F):
        self.func = func
        # turn arguments into a dataclass
        dc = make_arg_dataclass(func)
        assert issubclass(dc, DataClassJsonMixin)
        self.fixer = request_fixer(dc)
        self.schema = dc.schema()  # pylint: disable=no-member

    def call_form(self, md: CMultiDict, **kwargs) -> t.Any:
        assert set(kwargs) <= set(self.schema.fields.keys())

        ret = self.fixer(md)
        update_dataclasses(self.schema, ret)
        ret.update(kwargs)
        return self.from_data(ret)

    def call_json(self, json: t.Dict[str, t.Any], **kwargs) -> t.Any:
        # return $.ajax({
        #     url: `${url}`,
        #     _type: "POST",
        #     data: JSON.stringify($data),
        #     contentType: 'application/json; charset=utf-8'
        # });
        assert set(kwargs) <= set(self.schema.fields.keys())
        json.update(kwargs)
        return self.from_data(json)

    def from_data(self, data: t.Dict[str, t.Any]) -> t.Any:
        dci = self.schema.load(data, unknown="exclude")
        return self.func(**{f.name: getattr(dci, f.name) for f in dcfields(dci)})


@dataclass
class Error(DataClassJsonMixin):
    status: str
    msg: str
    errors: t.Dict[str, t.List[str]]
    kind: Literal["validation-error"] = "validation-error"


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


def api(func: F) -> F:

    caller = Call(func)

    @wraps(func)
    def api_func(*args, **kwargs):
        # kwargs are from url_defaults such as:
        # '/path/<project>/<int:page>'
        try:
            if request.is_json:
                ret = caller.call_json(request.json, **kwargs)
            else:
                ret = caller.call_form(request.values, **kwargs)
            if isinstance(ret, DataClassJsonMixin):
                return jsonify(ret.to_dict())
            return ret
        except ValidationError as e:
            # Api.func(...args).fail(xhr => {xhr.status == 400 && xhr.responseJSON as Error})
            ret = jsonify(
                Error(
                    status="FAILED",
                    msg="Validation error",
                    errors=flatten(e.normalized_messages()),
                ).to_dict()
            )
            ret.status = 400
            return ret

    return t.cast(F, api_func)


@dataclass
class URLFmt:
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


@dataclass
class ClassBuilder:

    view: "JSView"

    indent: str = "    "
    nl: str = "\n"
    as_ts: bool = True
    as_jquery: bool = True
    export: bool = False

    def build(self) -> TSClass:
        nl, indent = self.nl, self.indent
        funcs = []
        tab = f"{nl}{indent}"
        for r in self.view.code.values():
            rule = r.rule
            function = r.function.to_promise(as_jquery=self.as_jquery)
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

            body.extend(self.body(rule) if self.as_jquery else self.fetch_body(rule))

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

    def body(self, rule: TSRule) -> t.List[str]:
        methods = rule.methods
        method = "get" if "GET" in methods else ("post" if "POST" in methods else None)
        if method is None:
            raise ValueError(f"no get/post method for rule {rule}")
        return [f"return $.{method}(`{rule.url}`, $data)"]

    def fetch_body(self, rule: TSRule) -> t.List[str]:
        methods = rule.methods
        method = "GET" if "GET" in methods else ("POST" if "POST" in methods else None)
        if method is None:
            raise ValueError(f"no get/post method for rule {rule}")
        return [f"return jfetch(`{rule.url}`, $data)"]


@dataclass
class JSView:
    name: str
    code: t.Dict[str, Restful]
    extra_structs: t.List[str]
    folder: str
    interface: t.Optional[TSInterface] = None
    preamble: str = "import {jfetch} from './fetch.js'"
    as_jquery: bool = False

    def to_class(self, as_ts: bool = True):
        builder = ClassBuilder(
            self, as_ts=as_ts, export=as_ts, as_jquery=self.as_jquery
        )
        return builder.build()

    def create_interface(self):
        self.interface = to_interface(
            self.name.title(), self.code.values(), as_jquery=self.as_jquery
        )
        return self.interface

    def inject_url_defaults(self, app: Flask) -> "JSView":

        code = {k: r.inject_url_defaults(app) for k, r in self.code.items()}
        ret = replace(self, code=code)
        ret.create_interface()
        return ret

    def resolve_defaults(self, values: Defaults) -> "JSView":
        if not values:
            return self
        empty: t.Dict[str, t.Any] = {}
        code = {
            k: r.resolve_defaults(values.get(r.rule.endpoint, empty))
            for k, r in self.code.items()
        }
        ret = replace(self, code=code)
        ret.create_interface()
        return ret

    def jsapi(self, global_name="app", app: t.Optional["Flask"] = None) -> str:
        if app is not None:
            v = self.inject_url_defaults(app)
        else:
            v = self
        cls = v.to_class(as_ts=False)
        s = "\n".join(
            [
                "(function() {",
                str(cls),
                f"window.{global_name} = new {cls.name}Class()",
                "})();",
            ]
        )
        return s

    def tsapi(self, values: t.Optional[Defaults] = None) -> str:
        if not values:
            v = self
        else:
            v = self.resolve_defaults(values)
        if not self.as_jquery:
            out = [self.preamble]
        else:
            out = []
        out.extend(v.extra_structs)
        if v.interface is not None:
            out.append(str(v.interface))
        out.append(str(v.to_class()))
        out.append(f"export const app = new {v.name.title()}Class()")
        return "\n".join(out)


@dataclass
class FlaskApi:
    app: Flask
    views: t.Dict[str, JSView]
    defaults: t.Optional[Defaults] = None
    errors: t.Optional[t.List[str]] = None

    def jsviews(self) -> t.Iterable[JSView]:
        return self.views.values()

    def context(
        self, blueprint: str, global_name="app"
    ) -> t.Dict[str, t.Callable[[], Markup]]:

        view = self.views[blueprint]

        def jsapi_(name):

            return Markup(view.jsapi(name, self.app))

        def tsapi():
            return Markup(view.tsapi(self.defaults))

        def jsapi(as_ts: bool = False, name="app"):
            if as_ts:
                return tsapi()
            return jsapi_(name)

        return dict(jsapi=jsapi)

    def generate_view(
        self, view: JSView, as_js: bool = False, stdout: bool = False
    ) -> None:
        as_ts = not as_js
        ext = "ts" if as_ts else "js"
        output = f"{view.folder}/{view.name}_api.{ext}"

        def do(fp):
            print(f"// {view.name}: {output}", file=fp)
            if as_ts:
                print(view.tsapi(self.defaults), file=fp)
            else:
                print(view.jsapi("app", self.app), file=fp)

        if stdout:
            do(None)
        else:
            click.secho(f"writing to: {output}", fg="yellow")
            with open(output, "w") as fp:
                do(fp)

    def generate_api(self, as_js: bool = False, stdout: bool = False) -> None:
        for view in self.jsviews():
            self.generate_view(view, as_js=as_js, stdout=stdout)


def flask_api(  # noqa: C901
    app: "Flask",
    modules: t.Optional[t.Sequence[str]] = None,
    defaults: t.Optional[Defaults] = None,
    verbose: bool = True,
    add_error: bool = False,
    as_jquery: bool = False,
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
            err = click.style(f"Error: {view_func.__name__} {e}", fg="red")
            if verbose:
                click.echo(err, err=True)
            errors.append(err)
            return None

    def try_call(func: BuildFunc) -> t.Optional[TSThing]:
        try:
            return func()
        except (NameError, TypeError) as e:
            err = click.style(f"Error: {e}", fg="red")
            if verbose:
                click.echo(err, err=True)
            errors.append(err)
            return None

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
        tsrule = process_rule(rule)
        view_func = app.view_functions[rule.endpoint]
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
            click.secho(
                f"incompatible args {tsrule.endpoint}: {args} {tsargs}",
                fg="red",
                err=True,
            )
        # get or create a JSView for this endpoint
        if blueprint.name not in views:
            views[blueprint.name] = JSView(
                name=blueprint.name,
                code={},
                extra_structs=[],
                folder=folder,
                as_jquery=as_jquery,
            )
        jsview = views[blueprint.name]
        # add endpoint info
        jsview.code[ts.name] = Restful(function=ts, rule=tsrule)
        # add dependencies

    # 4. generate dependencies
    for view in views.values():
        todo = seen[view.name]
        if add_error:
            # FIXME: the current @api returns an Error object on ValidationError
            todo[Error.__name__] = Error.__module__
        if todo:
            builder = TSBuilder(ns=ns)
            view.extra_structs = [
                str(res)
                for func in builder.process_seen(todo)
                for res in [try_call(func)]
                if res is not None
            ]

        view.create_interface()

    return FlaskApi(app=app, views=views, defaults=defaults, errors=errors)


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
