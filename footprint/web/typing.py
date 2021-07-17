# from __future__ import annotations
import collections
import decimal
import typing as t
from collections.abc import Mapping
from dataclasses import MISSING, Field, dataclass, fields, is_dataclass, replace
from importlib import import_module
from inspect import signature
from types import FunctionType

import click
from werkzeug.datastructures import FileStorage

from ..cli import cli
from ..config import INDENT, NL
from .datacls import ApiField, get_dc_defaults, is_dataclass_type

# def get_func_defaults(func: FunctionType) -> t.Dict[str, t.Any]:
#     if func.__defaults__ is None:
#         return {}
#     print(func.__code__.co_argcount)
#     args = func.__code__.co_varnames[:func.__code__.co_argcount]
#     return dict(zip(reversed(args), reversed(func.__defaults__)))

JDC = "dataclasses_json"


def get_field_type(f: Field) -> t.Type[t.Any]:
    if JDC in f.metadata:
        mm = f.metadata[JDC]["mm_field"]
        if isinstance(mm, ApiField):
            # e.g. if this is BytesField this will be int list
            return mm.type
    return f.type


class Annotation(t.NamedTuple):
    name: str
    type: t.Type[t.Any]
    default: t.Any

    @property
    def has_default(self) -> bool:
        return self.default is not MISSING

    @property
    def requires_post(self) -> bool:
        # TODO: typing.List[F]
        return isinstance(self.type, type) and issubclass(self.type, FileStorage)


def get_annotations(
    cls_or_func: "TSTypeable", ns: t.Optional[t.Any] = None
) -> t.Dict[str, Annotation]:
    """Return the anntotations for a dataclass or function.

    May throw a `NameError` if annotation is only imported when
    typing.TYPE_CHECKING is True.
    """
    if isinstance(cls_or_func, FunctionType):
        sig = signature(cls_or_func)
        defaults = {
            k: v.default for k, v in sig.parameters.items() if v.default is not v.empty
        }
        d_ = t.get_type_hints(cls_or_func, localns=ns)
        # add untyped parameters
        d = {k: d_.get(k, t.Any) for k in sig.parameters}
        if "return" in d_:
            d["return"] = d_["return"]
    else:
        defaults = get_dc_defaults(t.cast(t.Type[t.Any], cls_or_func))
        # we want the type of the field as it is on the
        # client (browser) side e.g. bytes -> number[]
        d = {f.name: get_field_type(f) for f in fields(cls_or_func)}

    return {k: Annotation(k, v, defaults.get(k, MISSING)) for k, v in d.items()}


@dataclass
class TSField:
    name: str
    type: str
    is_dataclass: bool = False
    requires_post: bool = False  # e.g. for FileStorage
    default: t.Optional[str] = None
    colon: str = ": "

    @property
    def is_list(self):
        self.type.endswith("[]")  # convention

    @property
    def nested_type(self):
        assert self.is_list, self
        return self.type[:-2]

    def make_default(self, as_comment: bool = True):
        if as_comment:
            fmt = " /* ={} */"
        else:
            fmt = " ={}"
        return "" if self.default is None else fmt.format(self.default)

    def to_ts(
        self,
        with_default: bool = True,
        with_optional: bool = False,
        as_comment: bool = True,
    ) -> str:
        if with_default:
            default = self.make_default(as_comment)
        else:
            default = ""
        q = "?" if with_optional and self.default is not None else ""
        return f"{self.name}{q}{self.colon}{self.type}{default}"

    def to_js(self, with_default: bool = True, as_comment: bool = True) -> str:
        if with_default:
            default = self.make_default(as_comment)
        else:
            default = ""
        return f"{self.name}{default}"

    def __str__(self) -> str:
        return self.to_ts()

    def is_typed(self) -> bool:
        return self.type != "any"

    def serializer(self, this: str = "this.") -> str:
        if self.is_dataclass:
            return f"{self.name}: {self.type}_serializer({this}{self.name})"
        if self.is_list:
            it = self.nested_type
            s = get_serializer(it)
            return f"{self.name}: {this}{self.name}.map(v => {s}(v))"

        s = get_serializer(self.type)
        if s is None:
            return f"{self.name}: {this}{self.name}"
        if s.endswith("[]"):
            s = s[:-2]
            return f"{self.name}: {this}{self.name}.map(v => {s}(v))"
        return f"{self.name}: {s}({this}{self.name})"


# TODO: needs work here
def get_serializer(typ: str) -> t.Optional[str]:
    if typ in {"string", "number"}:
        return None
    if typ.endswith("[]"):
        s = get_serializer(typ[:-2])
        if s is None:
            return None
        return s + "[]"

    return f"{typ}_serializer"


@dataclass
class TSInterface:
    name: str
    fields: t.List[TSField]
    indent: str = INDENT
    export: bool = True
    nl: str = NL
    with_defaults: bool = True

    def to_ts(self) -> str:
        export = "export " if self.export else ""
        nl = self.nl
        return f"{export}interface {self.name} {{{nl}{self.ts_fields()}{nl}}}"

    def ts_fields(self):
        nl = self.nl
        return nl.join(
            f"{self.indent}{f.to_ts(with_default=self.with_defaults, with_optional=True)}"
            for f in self.fields
        )

    def anonymous(self) -> str:
        sfields = ", ".join(
            f.to_ts(with_default=self.with_defaults) for f in self.fields
        )
        return f"{{ {sfields} }}"

    def is_typed(self) -> bool:
        return all(f.is_typed() for f in self.fields)

    def __str__(self) -> str:
        return self.to_ts()

    def serializer(self) -> "TSFunction":
        kv = [f.serializer("input.") for f in self.fields]
        nl = self.nl
        sep = f"{nl}{self.indent}"
        csep = f",{sep}"
        body = f"return {{{sep}{csep.join(kv)}{nl}}};"

        return TSFunction(
            name=f"{self.name}_serializer",
            args=[TSField("input", type=self.name, is_dataclass=True)],
            returntype="{ [key: string]: any }",
            export=True,
            body=body,
        )


@dataclass
class TSClass(TSInterface):
    as_ts: bool = True

    def to_ts(self) -> str:
        nl = self.nl
        export = "export " if self.export else ""
        implements = f" implements {self.name}" if self.as_ts else ""
        return (
            f"{export}class {self.name}Class{implements} {{{nl}{self.ts_fields()}{nl}}}"
        )


@dataclass
class TSFunction:
    name: str
    args: t.List[TSField]
    returntype: str
    export: bool = True
    with_defaults: bool = True
    body: t.Optional[str] = None
    nl: str = NL
    indent: str = INDENT

    @property
    def requires_post(self) -> bool:
        return any(f.requires_post for f in self.args)

    def remove_args(self, *args: str) -> "TSFunction":
        a = [f for f in self.args if f.name not in set(args)]
        return replace(self, args=a)

    def to_ts(self) -> str:
        sargs = self.ts_args()
        export = "export " if self.export else ""
        if self.body is None:
            return f"{export}type {self.name} = ({sargs}) => {self.returntype}"

        return f"{export}const {self.name} = ({sargs}): {self.returntype} =>{self.ts_body()}"

    def to_js(self) -> str:
        sargs = self.js_args()
        export = "export " if self.export else ""
        if self.body is None:
            return f"{export}type {self.name} = ({sargs})"

        return f"{export}{self.name} = ({sargs}){self.ts_body()}"

    def ts_args(self) -> str:
        return ", ".join(
            f.to_ts(
                with_default=self.with_defaults,
                with_optional=True,
                as_comment=self.body is None,
            )
            for f in self.args
        )

    def js_args(self) -> str:
        return ", ".join(
            f.to_js(
                with_default=self.with_defaults,
                as_comment=self.body is None,
            )
            for f in self.args
        )

    def ts_body(self) -> str:
        if self.body is None:
            return ""
        nl = self.nl
        tab = f"{nl}{self.indent}"
        body = tab.join(self.body.splitlines())
        return f" {{{tab}{body}{nl}}}"

    def __str__(self) -> str:
        return self.to_ts()

    def anonymous(self, as_ts=True) -> str:
        assert as_ts or self.body is not None
        sargs = self.ts_args() if as_ts else self.js_args()
        if as_ts:
            arrow = " =>" if self.body is None else ":"
            return f"({sargs}){arrow} {self.returntype}{self.ts_body()}"
        return f"({sargs}){self.ts_body()}"

    def is_typed(self) -> bool:
        return not all(f.is_typed() for f in self.args) or self.returntype != "any"

    def build_promise(self, as_jquery=False) -> "TSFunction":
        promise = "JQuery.jqXHR" if as_jquery else "Promise"
        return replace(self, returntype=f"{promise}<{self.returntype}>")


DEFAULTS: t.Dict[t.Type[t.Any], str] = {
    str: "string",
    int: "number",
    float: "number",
    type(None): "null",
    bytes: "string",  # TODO see if this works
    bool: "boolean",
    decimal.Decimal: "number",
    FileStorage: "File",
}

TSTypeable = t.Union[t.Type[t.Any], t.Callable[..., t.Any]]

TSThing = t.Union[TSFunction, TSInterface]


class BuildFunc:
    def __init__(self, builder: t.Callable[[], TSThing], name: str, module: str):
        self.name = name
        self.module = module
        self.builder = builder

    def __call__(self) -> TSThing:
        return self.builder()


class TSBuilder:
    TS = DEFAULTS.copy()

    def __init__(
        self,
        variables: t.Optional[t.Sequence[str]] = None,
        ns: t.Optional[t.Any] = None,
    ):
        self.build_stack: t.List[TSTypeable] = []
        self.seen: t.Dict[str, str] = {}
        self.variables: t.Optional[t.Set[str]] = set(variables) if variables else None
        self.ns = ns

    def process_seen(
        self, seen: t.Optional[t.Dict[str, str]] = None
    ) -> t.Iterator[BuildFunc]:

        if seen is None:
            seen = {}
        seen.update(self.seen)
        self.seen = {}

        for name, module in seen.items():
            yield self.build(name, module)

    def build(self, name: str, module: str) -> BuildFunc:
        def build_func():
            m = import_module(module)
            return self.get_type_ts(getattr(m, name))

        return BuildFunc(build_func, name, module)

    def __call__(self, o: TSTypeable) -> TSThing:
        return self.get_type_ts(o)

    def forward_ref(self, type_name: str) -> str:
        if type_name in self.seen:
            return type_name
        g = self.current_module()
        if type_name in g:
            typ = g[type_name]
            if not isinstance(typ, str):
                return self.type_to_str(typ)
        raise TypeError(f"unknown ForwardRef {type_name}")

    # pylint: disable=too-many-return-statements
    def type_to_str(self, typ: t.Type[t.Any], is_arg: bool = False) -> str:

        if is_dataclass_type(typ):
            if (
                self.is_being_built(typ) or is_arg or typ.__name__ in self.seen
            ):  # recursive
                if is_arg:
                    self.seen[typ.__name__] = typ.__module__
                return typ.__name__  # just use name
            return self.get_type_ts(typ).anonymous()

        if isinstance(typ, t.ForwardRef):
            return self.forward_ref(typ.__forward_arg__)

        if hasattr(typ, "__origin__"):
            cls = typ.__origin__
        else:
            cls = typ  # list, str, etc.

        is_type = isinstance(cls, type)
        if hasattr(typ, "__args__"):
            iargs = (
                self.type_to_str(s, is_arg=True)
                for s in typ.__args__
                if s is not Ellipsis  # e.g. t.Tuple[int,...]
            )

            if is_type and issubclass(cls, Mapping):
                k, v = iargs
                args = f"{{ [name: {k}]: {v} }}"
            else:
                # Union
                args = " | ".join(set(iargs))
        else:
            if is_type:
                if cls not in self.TS:
                    self.seen[cls.__name__] = cls.__module__
                    return cls.__name__
                    # raise TypeError(
                    #     f"unknown type: {typ.__qualname__} from {cls.__module__}"
                    # )
                args = self.TS[cls]
            else:
                if isinstance(cls, str) and not is_arg:
                    return self.forward_ref(cls)
                if typ == t.Any:
                    return "any"
                args = self.ts_repr(cls)  # Literal

        if (
            is_type
            and issubclass(cls, collections.abc.Sequence)
            and not issubclass(
                cls, (str, bytes)
            )  # these are both sequences but not arrays
        ):
            args = f"({args})[]" if "|" in args else f"{args}[]"
        return args

    def get_field_types(
        self, cls: TSTypeable, is_arg: bool = False
    ) -> t.Iterator[TSField]:
        a = get_annotations(cls, self.ns)

        for name, annotation in a.items():

            ts_type_as_str = self.type_to_str(annotation.type, is_arg=is_arg)
            yield TSField(
                name=name,
                type=ts_type_as_str,
                is_dataclass=is_dataclass(annotation.type),
                requires_post=annotation.requires_post,
                default=self.ts_repr(annotation.default)
                if annotation.has_default
                else None,
            )

    def get_dc_ts(self, typ: t.Type[t.Any]) -> TSInterface:
        return TSInterface(name=typ.__name__, fields=list(self.get_field_types(typ)))

    def get_func_ts(self, func: t.Callable[..., t.Any]) -> TSFunction:
        if not callable(func):
            raise TypeError(f"{func} is not a function")

        ft = list(self.get_field_types(func, is_arg=True))
        args = [f for f in ft if f.name != "return"]
        rt = [f for f in ft if f.name == "return"]
        if self.variables is not None:
            # pylint: disable=unsupported-membership-test
            args = [f for f in args if f.name not in self.variables]
        if rt:
            returntype = rt[0].type
            if returntype == "null":  # type(None) for a return type should mean void
                returntype = "void"
        else:
            returntype = "any"

        return TSFunction(name=func.__name__, args=args, returntype=returntype)

    def is_being_built(self, o: TSTypeable) -> bool:
        return any(o == s for s in self.build_stack)

    def get_type_ts(self, o: TSTypeable) -> TSThing:
        # main entrypoint
        self.build_stack.append(o)
        try:
            if isinstance(o, FunctionType):
                return self.get_func_ts(t.cast(t.Callable[..., t.Any], o))
            return self.get_dc_ts(t.cast(t.Type[t.Any], o))
        finally:
            self.build_stack.pop()

    def current_module(self) -> t.Dict[str, t.Any]:
        if self.build_stack:
            m = import_module(self.build_stack[-1].__module__)
            return m.__dict__
        return {}

    # pylint: disable=too-many-return-statements
    def ts_repr(self, value: t.Any) -> str:
        ts_repr = self.ts_repr
        if value is None:
            return "null"
        if isinstance(value, FunctionType):  # field(default_factory=lambda:...)
            return ts_repr(value())
        if isinstance(value, decimal.Decimal):
            return repr(float(value))
        if isinstance(value, str):  # WARNING: *before* test for Sequence!
            return repr(value)
        if isinstance(value, bytes):  # WARNING: *before* test for Sequence!
            return repr(value)[1:]  # chop b'xxx' off
        if isinstance(value, collections.abc.Sequence):
            args = ", ".join(ts_repr(v) for v in value)
            return f"[{args}]"
        if isinstance(value, collections.abc.Mapping):
            args = ", ".join(f"{str(k)}: {ts_repr(v)}" for k, v in value.items())
            return f"{{{args}}}"
        if isinstance(value, bool):
            return repr(value).lower()
        # if isinstance(value, (float, int)):
        #     return s
        return repr(value)


def tsok(o: t.Any) -> bool:
    return is_dataclass_type(o) or isinstance(o, FunctionType)


def tots(dc: str) -> t.Iterator[TSTypeable]:

    m = dc.rsplit(":", 1)
    mod = import_module(m[0])
    if len(m) > 1:
        func = getattr(mod, m[1])
        if tsok(func):
            yield func
    else:
        for o in mod.__dict__.values():
            if tsok(o):
                yield o


@cli.command()
@click.option("-v", "--variables", help="url_default variables")
@click.option("-r", "--raise", "raise_exc", help="raise any exceptions")
@click.option("-e", "--no-errors", is_flag=True)
@click.argument("modules", nargs=-1)
def typescript(
    modules: t.List[str], no_errors: bool, variables: t.Optional[str], raise_exc: bool
) -> None:
    """Generate typescript from functions and dataclasses"""
    import sys

    vars_: t.Optional[t.List[str]] = None
    if variables is not None:
        vars_ = [v.strip() for v in variables.split(",")]

    def build(build_func: t.Callable[[], TSThing]) -> None:
        try:
            ot = build_func()
            if ot.is_typed():
                if isinstance(ot, TSFunction):
                    # convert to anonymous function
                    app.append(TSField(name=ot.name, type=ot.anonymous()))
                else:
                    click.echo(str(ot))
                    click.echo(str(ot.serializer()))
        except Exception as e:  # pylint: disable=broad-except
            msg = "// " + "// ".join(f"error for: {e}".splitlines())
            if not no_errors:
                click.echo(msg)
            else:
                click.secho(msg, fg="red", err=True)
            if raise_exc:
                raise

    if "." not in sys.path:
        sys.path.append(".")

    builder = TSBuilder(vars_)

    def buildit(o: TSTypeable) -> t.Callable[[], TSThing]:
        return lambda: builder(o)

    for mod in modules:
        app: t.List[TSField] = []
        click.echo(f"// Module: {mod}")
        for o in tots(mod):
            if o.__name__ in builder.seen:
                continue
            build(buildit(o))

        if app:
            click.echo(str(TSInterface(name="App", fields=app)))

    for build_func in builder.process_seen():
        build(build_func)
