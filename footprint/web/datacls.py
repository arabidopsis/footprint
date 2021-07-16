import typing as t
from base64 import b64decode, b64encode
from dataclasses import MISSING, Field, is_dataclass, field, fields

from dataclasses_json import DataClassJsonMixin as BaseDataClassJsonMixin
from dataclasses_json import config
from dataclasses_json.api import SchemaType
from marshmallow import fields as mm_fields
from marshmallow.exceptions import ValidationError

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