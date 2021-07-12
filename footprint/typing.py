# from __future__ import annotations
import collections
import decimal
import typing as t
from abc import ABC, abstractmethod
from base64 import b64decode, b64encode
from collections.abc import Mapping
from dataclasses import MISSING, Field, dataclass, field, fields, is_dataclass, replace
from importlib import import_module
from inspect import signature
from types import FunctionType

import click
from dataclasses_json import DataClassJsonMixin as BaseDataClassJsonMixin
from dataclasses_json import config
from dataclasses_json.api import SchemaType
from marshmallow import fields as mm_fields
from marshmallow.exceptions import ValidationError

from .cli import cli

OUT = t.TypeVar("OUT")
IN = t.TypeVar("IN")
A = t.TypeVar("A", bound="ApiField")


class ApiField(t.Generic[OUT, IN], mm_fields.Field):
    type: t.Type[OUT]  # output type of field
    encoder: t.Callable[[IN], OUT]
    decoder: t.Callable[[OUT], IN]

    # pylint: disable=redefined-builtin
    @classmethod
    def field(
        cls: t.Type[A],
        *,
        default: t.Any = MISSING,
        default_factory: t.Callable[[], t.Any] = t.cast(t.Callable[[], t.Any], MISSING),
        repr: bool = True,
        hash: bool = True,
        init: bool = True,
        compare: bool = True,
        metadata: t.Optional[t.Dict[t.Any, t.Any]] = None,
    ) -> Field:
        """use: field: type = ApiField.field()"""
        required = default is not MISSING or default_factory is not MISSING
        m = config(
            mm_field=cls(required=required), encoder=cls.encoder, decoder=cls.decoder
        )
        if metadata:
            metadata.update(m)
        else:
            metadata = m
        if default is MISSING:
            return field(
                default_factory=default_factory,
                repr=repr,
                hash=hash,
                init=init,
                compare=compare,
                metadata=metadata,
            )
        return field(
            default=default,
            repr=repr,
            hash=hash,
            init=init,
            compare=compare,
            metadata=metadata,
        )

    def _serialize(self, value: t.Optional[IN], attr, obj, **kwargs) -> t.Optional[OUT]:
        if value is not None:
            return self.encoder(value)  # type: ignore

        if not self.required:
            return None

        raise ValidationError(self.default_error_messages["required"])

    def _deserialize(
        self, value: t.Optional[OUT], attr, data, **kwargs
    ) -> t.Optional[IN]:
        if value is not None:
            return self.decoder(value)  # type: ignore

        if not self.required:
            return None
        raise ValidationError(self.default_error_messages["required"])


def bytes64encoder(value: bytes) -> str:
    return b64encode(value).decode("ascii")


def bytes64decoder(value: str) -> bytes:
    return b64decode(value)


class Bytes64Field(ApiField[str, bytes]):
    type = str
    encoder = bytes64encoder
    decoder = bytes64decoder


def bytesencoder(value: bytes) -> t.List[int]:
    return list(value)


def bytesdecoder(value: t.List[int]) -> bytes:
    return bytes(value)


class BytesField(ApiField[t.List[int], bytes]):
    type = t.List[int]
    encoder = bytesencoder
    decoder = bytesdecoder


class DataClassJsonMixin(BaseDataClassJsonMixin):
    @classmethod
    def schema(
        cls,
        *,
        infer_missing: bool = False,
        only=None,
        exclude=(),
        many: bool = False,
        context=None,
        load_only=(),
        dump_only=(),
        partial: bool = False,
        unknown=None,
    ) -> SchemaType:
        schema = super().schema(
            infer_missing=infer_missing,
            only=only,
            exclude=exclude,
            many=many,
            context=context,
            load_only=load_only,
            dump_only=dump_only,
            partial=partial,
            unknown=unknown,
        )
        return patch_schema(cls, schema)


def patch_schema(cls: t.Type[BaseDataClassJsonMixin], schema: SchemaType) -> SchemaType:
    # patch "required" field
    defaults = get_dc_defaults(cls)
    for k, f in schema.fields.items():
        if k in defaults:
            # f.default can be a function
            d = f.default() if callable(f.default) else f.default
            v = defaults[k]
            assert d == v, (f, v)
            f.required = False
        else:
            f.required = True

    return schema


def get_schema(cls: t.Type[t.Any]) -> SchemaType:
    if not issubclass(cls, BaseDataClassJsonMixin):
        raise TypeError(f"{cls} is not a JSON dataclass")
    schema = cls.schema()
    if issubclass(cls, DataClassJsonMixin):
        return schema
    return patch_schema(cls, schema)


def is_dataclass_instance(obj: t.Any) -> bool:
    return is_dataclass(obj) and not isinstance(obj, type)


def is_dataclass_type(obj: t.Any) -> bool:
    return is_dataclass(obj) and isinstance(obj, type)


def get_dc_defaults(cls: t.Type[t.Any]) -> t.Dict[str, t.Any]:
    if not is_dataclass_type(cls):
        raise TypeError(
            f"{cls} is not a dataclass type instance={is_dataclass_instance(cls)}"
        )

    def get_default(f: Field) -> t.Any:
        if f.default is not MISSING:
            return f.default
        if f.default_factory is not MISSING:  # type: ignore
            return f.default_factory()  # type: ignore
        return MISSING

    return {
        f.name: d for f in fields(cls) for d in [get_default(f)] if d is not MISSING
    }


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
            return mm.type
    return f.type


class Annotation(t.NamedTuple):
    name: str
    type: t.Type[t.Any]
    default: t.Any

    @property
    def has_default(self):
        return self.default is not MISSING


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
        d = {f.name: get_field_type(f) for f in fields(cls_or_func)}

    return {k: Annotation(k, v, defaults.get(k, MISSING)) for k, v in d.items()}


class TSClass(ABC):
    name: str

    @abstractmethod
    def to_ts(self) -> str:
        raise NotImplementedError("to_ts")

    def __str__(self) -> str:
        return self.to_ts()

    def is_typed(self) -> bool:
        return False

    @abstractmethod
    def anonymous(self) -> str:
        raise NotImplementedError("anonymous")


@dataclass
class TSField:
    name: str
    type: str
    default: t.Optional[str] = None

    def make_default(self, as_comment: bool = True):
        if as_comment:
            fmt = "/* ={} */"
        else:
            fmt = " ={}"
        return "" if self.default is None else fmt.format(self.default)

    def to_ts(
        self, with_default=True, with_optional: bool = False, as_comment: bool = True
    ) -> str:
        if with_default:
            default = self.make_default(as_comment)
        else:
            default = ""
        q = "?" if with_optional and self.default is not None else ""
        return f"{self.name}{q}: {self.type}{default}"

    def to_js(self, with_default=True, as_comment: bool = True) -> str:
        if with_default:
            default = self.make_default(as_comment)
        else:
            default = ""
        return f"{self.name}{default}"

    def __str__(self) -> str:
        return self.to_ts()

    def is_typed(self) -> bool:
        return self.type != "any"


@dataclass
class TSInterface:
    name: str
    fields: t.List[TSField]
    indent: str = "    "
    export: bool = True
    nl: str = "\n"
    with_defaults: bool = False

    def to_ts(self) -> str:
        nl = self.nl
        sfields = nl.join(
            f"{self.indent}{f.to_ts(with_default=self.with_defaults)}"
            for f in self.fields
        )
        export = "export " if self.export else ""
        return f"{export}interface {self.name} {{{nl}{sfields}{nl}}}"

    def anonymous(self) -> str:
        sfields = ", ".join(
            f.to_ts(with_default=self.with_defaults) for f in self.fields
        )
        return f"{{ {sfields} }}"

    def is_typed(self) -> bool:
        return not all(f.type == "any" for f in self.fields)

    def __str__(self) -> str:
        return self.to_ts()


@dataclass
class TSFunction:
    name: str
    args: t.List[TSField]
    returntype: str
    export: bool = True
    with_defaults: bool = True
    body: t.Optional[str] = None

    def remove_args(self, *args: str) -> "TSFunction":
        a = [f for f in self.args if f.name not in set(args)]
        return replace(self, args=a)

    def to_ts(self) -> str:
        sargs = ", ".join(
            f.to_ts(
                with_default=self.with_defaults,
                with_optional=True,
                as_comment=self.body is None,
            )
            for f in self.args
        )
        export = "export " if self.export else ""
        if self.body is None:
            return f"{export}type {self.name} = ({sargs}) => {self.returntype}"
        return f"{export}{self.name} = ({sargs}) : {self.returntype} {{ {self.body }}}"

    def __str__(self) -> str:
        return self.to_ts()

    def anonymous(self) -> str:
        sargs = ", ".join(
            f.to_ts(with_default=self.with_defaults, with_optional=True)
            for f in self.args
        )
        return f"({sargs}) => {self.returntype}"

    def is_typed(self) -> bool:
        return not all(f.type == "any" for f in self.args) or self.returntype != "any"


DEFAULTS: t.Dict[t.Type[t.Any], str] = {
    str: "string",
    int: "number",
    float: "number",
    type(None): "null",
    bytes: "string",  # TODO see if this works
    bool: "boolean",
    decimal.Decimal: "number",
}

TSTypeable = t.Union[t.Type[t.Any], t.Callable[..., t.Any]]


class TSUnknown(t.NamedTuple):
    name: str
    module: str


class TSBuilder:
    TS = DEFAULTS.copy()

    def __init__(
        self,
        variables: t.Optional[t.Sequence[str]] = None,
        ns: t.Optional[t.Any] = None,
    ):
        self.building: t.Set[TSTypeable] = set()
        self.stack: t.List[TSTypeable] = []
        self.seen: t.Dict[str, str] = {}
        self.unknown: t.Set[TSUnknown] = set()
        self.variables: t.Optional[t.Set[str]] = set(variables) if variables else None
        self.ns = ns

    def process_unknowns(self):
        unknown = list(self.unknown)
        self.unknown = set()
        for tsu in unknown:
            m = import_module(tsu.module)
            yield tsu.module, getattr(m, tsu.name)

    def __call__(self, o: TSTypeable) -> t.Union[TSFunction, TSInterface]:
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
            if typ in self.building or is_arg or typ.__name__ in self.seen:  # recursive
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
                    self.unknown.add(TSUnknown(cls.__name__, cls.__module__))
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
                name,
                ts_type_as_str,
                self.ts_repr(annotation.default) if annotation.has_default else None,
            )

    def get_dc_ts(self, typ: t.Type[t.Any]) -> TSInterface:
        return TSInterface(typ.__name__, list(self.get_field_types(typ)))

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
        else:
            returntype = "any"
        return TSFunction(func.__name__, args, returntype)

    def get_type_ts(self, o: TSTypeable) -> t.Union[TSFunction, TSInterface]:
        self.building.add(o)
        self.stack.append(o)
        try:
            if isinstance(o, FunctionType):
                return self.get_func_ts(t.cast(t.Callable[..., t.Any], o))
            return self.get_dc_ts(t.cast(t.Type[t.Any], o))
        finally:
            self.building.remove(o)
            self.stack.pop()

    def current_module(self) -> t.Dict[str, t.Any]:
        if self.stack:
            m = import_module(self.stack[-1].__module__)
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
@click.option("-e", "--no-errors", is_flag=True)
@click.argument("dataclasses", nargs=-1)
def typescript(
    dataclasses: t.List[str], no_errors: bool, variables: t.Optional[str]
) -> None:
    """Generate typescript from functions and dataclasses"""
    import sys

    vars_: t.Optional[t.List[str]] = None
    if variables is not None:
        vars_ = [v.strip() for v in variables.split(",")]

    # t.TYPE_CHECKING = False
    # import mypy.typeshed.stdlib as s
    # stdlibpath = s.__path__._path[0]
    # print(stdlibpath)
    # sys.path.append(stdlibpath)
    def build(o):
        try:
            ot = builder(o)
            if ot.is_typed():
                if isinstance(ot, TSFunction):
                    app.append(TSField(ot.name, ot.anonymous()))
                else:
                    click.echo(str(ot))
        except Exception as e:  # pylint: disable=broad-except
            msg = "// " + "// ".join(f"error for {o}: {e}".splitlines())
            if not no_errors:
                click.echo(msg)
            else:
                click.secho(msg, fg="red", err=True)

    if "." not in sys.path:
        sys.path.append(".")
    # EXCLUDE = (type(None), str)
    builder = TSBuilder(vars_)
    for dc in dataclasses:
        app: t.List[TSField] = []
        click.echo(f"// Module: {dc}")
        for o in tots(dc):
            if o.__name__ in builder.seen:
                continue
            build(o)

        if app:
            click.echo(str(TSInterface("App", app)))

        for mod, u in builder.process_unknowns():
            click.echo(f"// Module: {mod}")
            build(u)
