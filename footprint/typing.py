# from __future__ import annotations
import collections
import decimal
from base64 import b64decode, b64encode

import typing as t
from types import FunctionType

from dataclasses import dataclass, is_dataclass, field, MISSING, Field
from dataclasses_json import (
    DataClassJsonMixin as BaseDataClassJsonMixin,
    config,
)
from dataclasses_json.api import SchemaType
from marshmallow import fields
from marshmallow.exceptions import ValidationError

from typing_extensions import Literal

O = t.TypeVar("O")
I = t.TypeVar("I")


class ApiField(t.Generic[O, I], fields.Field):
    type: t.Type[O]  # output type of field
    encoder: t.Callable[[I], O]
    decoder: t.Callable[[O], I]

    # pylint: disable=redefined-builtin
    @classmethod
    def field(
        cls: t.Type["ApiField"],
        *,
        default:t.Any=MISSING,
        default_factory:t.Callable[[],t.Any]=t.cast(t.Callable[[],t.Any],MISSING),
        repr:bool=True,
        hash:bool=True,
        init:bool=True,
        compare:bool=True,
        metadata: t.Optional[t.Dict[t.Any, t.Any]] = None,
    ) -> Field:
        """use: field: type = ApiField.field()"""
        m = config(mm_field=cls(), encoder=cls.encoder, decoder=cls.decoder)
        if metadata:
            metadata.update(m)
        else:
            metadata = m
        return field(
            default=default,
            default_factory=default_factory,
            repr=repr,
            hash=hash,
            init=init,
            compare=compare,
            metadata=metadata,
        )

    def _serialize(self, value: t.Optional[I], attr, obj, **kwargs) -> t.Optional[O]:
        if value is not None:
            return self.encoder(value)

        if not self.required:
            return None

        raise ValidationError(self.default_error_messages["required"])

    def _deserialize(self, value: t.Optional[O], attr, data, **kwargs) -> t.Optional[I]:
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
    dcfields = cls.__dataclass_fields__
    return {f.name: f.default for f in dcfields.values() if f.default is not MISSING}


def get_func_defaults(func: FunctionType) -> t.Dict[str, t.Any]:
    if func.__defaults__ is None:
        defaults = {}
    else:
        defaults = dict(
            zip(reversed(func.__code__.co_varnames), reversed(func.__defaults__))
        )
    return defaults


def get_field_type(f: Field) -> t.Type[t.Any]:
    if "dataclasses_json" in f.metadata:
        mm = f.metadata["dataclasses_json"]["mm_field"]
        if isinstance(mm, ApiField):
            return mm.type
    return f.type


def get_annotations(cls_or_func: t.Type[t.Any]) -> t.Dict[str, t.Tuple[t.Any, t.Any]]:
    if isinstance(cls_or_func, FunctionType):
        defaults = get_func_defaults(cls_or_func)
        d = t.get_type_hints(cls_or_func)
    else:
        defaults = get_dc_defaults(cls_or_func)
        dcfields = cls_or_func.__dataclass_fields__
        d = {f.name: get_field_type(f) for f in dcfields.values()}

    # return {
    #     k: (v, defaults.get(k, missing))
    #     for t in cls.mro()
    #     if hasattr(t, "__annotations__")
    #     for k, v in t.__annotations__.items()
    # }
    return {k: (v, defaults.get(k, MISSING)) for k, v in d.items()}


TS = {
    str: "string",
    int: "number",
    type(None): "null",
    bytes: "bytes",
    bool: "boolean",
    decimal.Decimal: "number",
}


def forward_ref(typ: str) -> str:
    g = globals()
    if typ in g:
        tt = g[typ]
        if not isinstance(tt, str):
            return type_to_str(tt)
    raise TypeError(f"unknown ForwardRef {typ}")


def type_to_str(typ: t.Type[t.Any], is_arg: bool = False) -> str:
    # return the ts type that will go after the colon e.g. name: number
    if is_dataclass_type(typ):
        return f'{{ {", ".join(get_type(typ))} }}'
    if hasattr(typ, "__origin__"):
        cls = typ.__origin__
    else:
        cls = typ  # list,str, etc.
        if isinstance(cls, t.ForwardRef):
            # FIXME find actual class
            return forward_ref(cls.__forward_arg__)

    is_type = isinstance(cls, type)
    if hasattr(typ, "__args__"):
        iargs = (str(type_to_str(s, is_arg=True)) for s in typ.__args__)
        if is_type and issubclass(cls, dict):
            k, v = iargs
            args = f"{{ [name: {k}]: {v} }}"
        else:
            args = " | ".join(iargs)
    else:
        if is_type:
            if cls not in TS:
                raise TypeError(f"unknown type {typ}")
            args = TS[cls]
        else:
            args = ts_repr(cls)  # Literal

    if (
        is_type
        and issubclass(cls, collections.abc.Sequence)
        and cls not in {str, bytes}
    ):
        args = f"({args})[]" if "|" in args else f"{args}[]"
    return args


# pylint: disable=too-many-return-statements
def ts_repr(value: t.Any) -> str:
    if isinstance(value, FunctionType):  # field(default=lambda:...)
        return ts_repr(value())
    if value is None:
        return "null"
    if isinstance(value, decimal.Decimal):
        return repr(float(value))
    if isinstance(value, (str, bytes)):  # WARNING: before test for Sequence!
        return repr(value)
    if isinstance(value, collections.abc.Sequence):
        args = ", ".join(ts_repr(v) for v in value)
        return f"[{args}]"
    if isinstance(value, collections.abc.Mapping):
        args = ", ".join(f"{str(k)}: {ts_repr(v)}" for k, v in value.items())
        return f"{{{args}}}"
    s = repr(value)
    if isinstance(value, bool):
        return s.lower()
    # if isinstance(value, (float, int)):
    #     return s

    return s


def get_type(cls: t.Type[t.Any]) -> t.Iterator[str]:
    a = get_annotations(cls)

    for name, (typ, default) in a.items():

        args = type_to_str(typ)
        comment = "" if default is MISSING else f"  // default={ts_repr(default)}"
        yield f'{name}{ "" if default is MISSING else "?"}: {args}{comment}'
        # yield name, cls, args, default


def get_type_ts(cls: t.Type[t.Any]) -> str:
    s = "\n\t" + "\n\t".join(get_type(cls)) + "\n"
    return f"interface {cls.__name__} {{ {s} }}"


def get_func_type(func: t.Type[t.Callable[..., t.Any]]) -> t.Iterator[str]:
    a = get_annotations(func)
    rv, _ = a.pop("return")
    yield f"function {func.__name__}("
    for name, (typ, default) in a.items():
        args = type_to_str(typ)
        # comment = "" if default is missing else f"  // default={ts_repr(default)}"
        yield f'{name}{ "" if default is MISSING else "?"}: {args},'
    yield f"): {type_to_str(rv)}"


def get_function_ts(func: t.Type[t.Callable[..., t.Any]]) -> str:
    return " ".join(get_func_type(func))


@dataclass
class Model(DataClassJsonMixin):
    value: int
    v2: bytes = Bytes64Field.field()


T = t.TypeVar("T", bound=Model)


class S(t.Generic[T]):
    def put(self, model: T) -> T:
        print(model.value)
        return model


class Database(S[T]):  # need T
    def add(self, m: T) -> T:
        return self.put(m)


@dataclass
class EModel(Model):
    evalue: str = "ss"


@dataclass
class EModel2(DataClassJsonMixin):
    # evalue: t.Optional[str]
    this_or_that: t.List[t.Union[str, bytes]]
    more: t.Sequence[str]
    a: t.Dict[str, str]
    bbb: bytes  # = field(metadata=Bytes)
    emodel: EModel
    qemodel: "EModel"
    number: decimal.Decimal
    kind: Literal["a", "b", "c"]
    dd: t.Dict[int, int] = field(default_factory=lambda: {2: 3, 4: 5})
    value: int = 1
    v: str = "s"
    none: t.Optional[str] = None
    istrue: bool = False


@dataclass
class EModel3(DataClassJsonMixin):

    a: t.Dict[str, int]


@dataclass
class Model4(DataClassJsonMixin):

    a: bytes = Bytes64Field.field()


def func2(arg1: int, arg2: EModel) -> "Model":
    return Model(value=arg2.value + arg1)


@dataclass
class Nested:
    value: int
    extra: t.List["Nested"]
