# from __future__ import annotations
import collections
import decimal
import typing as t
from base64 import b64decode, b64encode
from dataclasses import MISSING, Field, dataclass, field
from dataclasses import fields as dcfields
from dataclasses import is_dataclass
from types import FunctionType

import click
from dataclasses_json import DataClassJsonMixin as BaseDataClassJsonMixin
from dataclasses_json import config
from dataclasses_json.api import SchemaType
from marshmallow import fields
from marshmallow.exceptions import ValidationError

from .cli import cli

OUT = t.TypeVar("OUT")
IN = t.TypeVar("IN")
A = t.TypeVar("A", bound="ApiField")


class ApiField(t.Generic[OUT, IN], fields.Field):
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
        m = config(mm_field=cls(), encoder=cls.encoder, decoder=cls.decoder)
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
            return self.encoder(value)

        if not self.required:
            return None

        raise ValidationError(self.default_error_messages["required"])

    def _deserialize(
        self, value: t.Optional[OUT], attr, data, **kwargs
    ) -> t.Optional[IN]:
        if value is not None:
            return self.decoder(value)

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


# TYPES[bytes] = ByteField


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
            v = defaults[k]
            assert f.default == v, (f, v)
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


def is_dataclass_instance(obj):
    return is_dataclass(obj) and not isinstance(obj, type)


def is_dataclass_type(obj):
    return is_dataclass(obj) and isinstance(obj, type)


def get_dc_defaults(cls: t.Type[t.Any]) -> t.Dict[str, t.Any]:
    if not is_dataclass_type(cls):
        raise TypeError(f"{cls} is not a dataclass")
    dcf = dcfields(cls)
    return {f.name: f.default for f in dcf if f.default is not MISSING}


def get_func_defaults(func: FunctionType) -> t.Dict[str, t.Any]:
    if func.__defaults__ is None:
        return {}

    return dict(zip(reversed(func.__code__.co_varnames), reversed(func.__defaults__)))


def get_field_type(f: Field) -> t.Type[t.Any]:
    if "dataclasses_json" in f.metadata:
        mm = f.metadata["dataclasses_json"]["mm_field"]
        if isinstance(mm, ApiField):
            return mm.type
    return f.type


def get_annotations(
    cls_or_func: t.Union[t.Type[t.Any], t.Callable[..., t.Any]]
) -> t.Dict[str, t.Tuple[t.Any, t.Any]]:
    if isinstance(cls_or_func, FunctionType):
        defaults = get_func_defaults(cls_or_func)
        d = t.get_type_hints(cls_or_func)
    else:
        defaults = get_dc_defaults(t.cast(t.Type[t.Any], cls_or_func))
        dcf = dcfields(cls_or_func)
        d = {f.name: get_field_type(f) for f in dcf}

    # return {
    #     k: (v, defaults.get(k, missing))
    #     for t in cls.mro()
    #     if hasattr(t, "__annotations__")
    #     for k, v in t.__annotations__.items()
    # }
    return {k: (v, defaults.get(k, MISSING)) for k, v in d.items()}


@dataclass
class TSField:
    name: str
    type: str
    default: t.Optional[str] = None

    def to_ts(self) -> str:
        default = "" if self.default is None else f"={self.default}"
        return f"{self.name}: {self.type}{default}"

    def __str__(self):
        return self.to_ts()


@dataclass
class TSInterface:
    name: str
    fields: t.List[TSField]
    indent: str = "\t"
    export: bool = True
    lf: str = "\n"

    def to_ts(self) -> str:
        sfields = "\n".join(f"{self.indent}{f.to_ts()}" for f in self.fields)
        lf = self.lf
        export = "export " if self.export else ""
        return f"{export}interface {self.name} = {{{lf} {sfields}{lf} }}"

    def anonymous(self) -> str:
        sfields = ", ".join(f.to_ts() for f in self.fields)
        return f"{{ {sfields} }}"

    def __str__(self):
        return self.to_ts()


@dataclass
class TSFunction:
    name: str
    args: t.List[TSField]
    returntype: str
    export: bool = True

    def to_ts(self) -> str:
        sargs = ", ".join(f.to_ts() for f in self.args)
        export = "export " if self.export else ""
        return f"{export} function {self.name}({sargs}): {self.returntype}"

    def __str__(self):
        return self.to_ts()

    def anonymous(self) -> str:
        sargs = ", ".join(f.to_ts() for f in self.args)
        return f"function({sargs}): {self.returntype}"


DEFAULTS: t.Dict[t.Type[t.Any], str] = {
    str: "string",
    int: "number",
    type(None): "null",
    bytes: "string",
    bool: "boolean",
    decimal.Decimal: "number",
}


class TSBuilder:
    TS = DEFAULTS.copy()

    def __init__(self):
        self.building = set()
        self.stack = []

    def forward_ref(self, typ: str) -> str:

        g = self.current_module()
        if typ in g:
            tt = g[typ]
            if not isinstance(tt, str):
                return self.type_to_str(tt)
        raise TypeError(f"unknown ForwardRef {typ}")

    def type_to_str(self, typ: t.Type[t.Any], is_arg=False) -> str:

        if is_dataclass_type(typ):
            if typ in self.building:  # recursive
                return typ.__name__  # just use name
            return self.get_type_ts(typ).anonymous()

        if hasattr(typ, "__origin__"):
            cls = typ.__origin__
        else:
            cls = typ  # list,str, etc.
            if isinstance(cls, t.ForwardRef):
                # FIXME find actual class
                return self.forward_ref(cls.__forward_arg__)

        is_type = isinstance(cls, type)
        if hasattr(typ, "__args__"):
            iargs = (str(self.type_to_str(s, is_arg=True)) for s in typ.__args__)
            if is_type and issubclass(cls, dict):
                k, v = iargs
                args = f"{{ [name: {k}]: {v} }}"
            else:
                args = " | ".join(set(iargs))
        else:
            if is_type:
                if cls not in self.TS:
                    raise TypeError(f"unknown type: {typ}")
                args = self.TS[cls]
            else:
                if isinstance(cls, str) and not is_arg:
                    return self.forward_ref(cls)
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
        self, cls: t.Union[t.Type[t.Any], t.Callable[..., t.Any]]
    ) -> t.Iterator[TSField]:
        a = get_annotations(cls)

        for name, (typ, default) in a.items():

            args = self.type_to_str(typ)
            yield TSField(
                name, args, self.ts_repr(default) if default != MISSING else None
            )

    def get_dc_ts(self, cls: t.Type[t.Any]) -> TSInterface:
        return TSInterface(cls.__name__, list(self.get_field_types(cls)))

    def get_func_ts(self, func: t.Callable[..., t.Any]) -> TSFunction:
        if not callable(func):
            raise TypeError(f"{func} is not a function")

        ft = list(self.get_field_types(func))
        args = [f for f in ft if f.name != "return"]
        returntype = [f for f in ft if f.name == "return"][0].type
        return TSFunction(func.__name__, args, returntype)

    def get_type_ts(
        self, o: t.Union[t.Type[t.Any], t.Callable[..., t.Any]]
    ) -> t.Union[TSFunction, TSInterface]:
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
        from importlib import import_module

        if self.stack:
            m = import_module(self.stack[-1].__module__)
            return m.__dict__
        return {}

    def __call__(
        self, o: t.Union[t.Type[t.Any], t.Callable[..., t.Any]]
    ) -> t.Union[TSFunction, TSInterface]:
        return self.get_type_ts(o)

    # pylint: disable=too-many-return-statements
    def ts_repr(self, value: t.Any) -> str:
        ts_repr = self.ts_repr
        if isinstance(value, FunctionType):  # field(default_factory=lambda:...)
            return ts_repr(value())
        if value is None:
            return "null"
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


def tots(dc: str) -> str:
    from importlib import import_module

    m, f = dc.rsplit(".", 1)
    mod = import_module(m)
    func = getattr(mod, f)
    return str(TSBuilder()(func))


@cli.command()
@click.argument("dataclasses", nargs=-1)
def typescript(dataclasses):
    """Generate typescript"""
    for dc in dataclasses:
        print(tots(dc))
