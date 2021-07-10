import collections
from functools import wraps
import typing as t
from dataclasses import MISSING, make_dataclass, fields
from types import FunctionType
from marshmallow.exceptions import ValidationError
from werkzeug.datastructures import MultiDict, CombinedMultiDict
from marshmallow import Schema
from marshmallow.fields import Nested
from dataclasses_json import Undefined
from .typing import get_annotations, DataClassJsonMixin, is_dataclass_type

CMultiDict = t.Union[MultiDict, CombinedMultiDict]


def make_arg_dataclass(func: FunctionType) -> DataClassJsonMixin:

    items: t.List[
        t.Union[t.Tuple[str, t.Type[t.Any]], t.Tuple[str, t.Type[t.Any], t.Any]]
    ] = []
    for anno in get_annotations(func).values():
        if anno.name == "return":
            continue
        if anno.default != MISSING:
            items.append((anno.name, anno.type, anno.default))
        else:
            items.append((anno.name, anno.type))

    return t.cast(
        DataClassJsonMixin,
        make_dataclass(func.__name__.title(), items, bases=(DataClassJsonMixin,)),
    )


def update_dataclasses(schema: Schema, data: t.Dict[str, t.Any]) -> None:
    for k, f in schema.fields.items():
        if isinstance(f, Nested):
            s = f.nested
            if isinstance(s, Schema):
                data[k] = s.load(data, unknown="exclude").to_dict()


StrOrList = t.Union[str, t.List[str]]


def request_fixer(
    datacls: DataClassJsonMixin,
) -> t.Callable[[CMultiDict], t.Dict[str, StrOrList]]:
    def get(name):
        return lambda md: md.get(name)

    def getlist(name):
        return lambda md: md.getlist(name)

    def request_fixer_inner(
        DC: DataClassJsonMixin,
    ) -> t.Dict[str, t.Callable[[CMultiDict], StrOrList]]:
        getters = {}
        for f in fields(DC):
            typ = f.type
            if is_dataclass_type(typ):
                for k, v in request_fixer_inner(
                    t.cast(DataClassJsonMixin, typ)
                ).items():
                    getters[k] = v
                continue
            if hasattr(typ, "__origin__"):
                typ = typ.__origin__
            if issubclass(typ, collections.abc.Sequence) and not issubclass(
                typ, (str, bytes)
            ):
                getters[f.name] = getlist(f.name)
            else:
                getters[f.name] = get(f.name)
        return getters

    getters = request_fixer_inner(datacls)

    def fix_request(md: CMultiDict) -> t.Dict[str, StrOrList]:
        ret = {}
        for k, getter in getters.items():
            if k not in md:
                continue
            ret[k] = getter(md)
        return ret

    return fix_request


def call_form(func: FunctionType) -> t.Callable[[CMultiDict], t.Any]:

    dc = make_arg_dataclass(func)
    assert isinstance(dc, DataClassJsonMixin)
    fixer = request_fixer(dc)
    schema = dc.schema()  # pylint: disable=no-member

    @wraps(func)
    def call(md, **kwargs):

        ret = fixer(md)
        update_dataclasses(schema, ret)
        ret.update(kwargs)

        dci = schema.load(ret, unknown=Undefined.EXCLUDE)
        return func(**{f.name: getattr(dci, f.name) for f in fields(dci)})

    return call


def decorator(func):
    from flask import request, jsonify

    caller = call_form(func)

    @wraps(func)
    def api(*args, **kwargs):
        try:
            ret = caller(request.values, **kwargs)
            if isinstance(ret, DataClassJsonMixin):
                return jsonify(ret.to_dict())
            return ret
        except KeyError as e:
            name = e.args[0]

            ret = jsonify(
                dict(
                    status="FAILED",
                    msg=f"missing name {name}",
                    errors=[(name, ["missing"])],
                )
            )
            ret.status = 400
            return ret
        except ValidationError as e:
            ret = jsonify(
                dict(
                    status="FAILED",
                    msg="validation error",
                    errors=e.normalized_messages(),
                )
            )
            ret.status = 400

    return api
