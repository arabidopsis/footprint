import collections
import typing as t
from dataclasses import MISSING, dataclass, fields, make_dataclass, replace
from functools import wraps
from types import FunctionType

from flask import Flask, jsonify, request
from marshmallow import Schema
from marshmallow.exceptions import ValidationError
from marshmallow.fields import Nested
from werkzeug.datastructures import CombinedMultiDict, MultiDict
from werkzeug.routing import Rule, parse_converter_args, parse_rule

from .typing import DataClassJsonMixin, get_annotations, is_dataclass_type

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
        DC: t.Type[DataClassJsonMixin],
    ) -> t.Dict[str, t.Callable[[CMultiDict], StrOrList]]:
        getters = {}
        for f in fields(DC):
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
    assert issubclass(dc, DataClassJsonMixin)
    fixer = request_fixer(dc)
    schema = dc.schema()  # pylint: disable=no-member

    @wraps(func)
    def call(md, **kwargs):
        assert set(kwargs) <= set(schema.fields.keys())

        ret = fixer(md)
        update_dataclasses(schema, ret)
        ret.update(kwargs)

        dci = schema.load(ret, unknown="exclude")
        return func(**{f.name: getattr(dci, f.name) for f in fields(dci)})

    return call


@dataclass
class Errors:
    status: str
    msg: str
    errors: t.Dict[str, t.List[str]]


def decorator(func):

    caller = call_form(func)

    @wraps(func)
    def api(*args, **kwargs):
        try:
            ret = caller(request.values, **kwargs)
            if isinstance(ret, DataClassJsonMixin):
                return jsonify(ret.to_dict())
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
            return ret

    return api


class Fmt(t.NamedTuple):
    converter: t.Optional[str]
    args: t.Optional[t.Tuple[t.Tuple, t.Dict[str, t.Any]]]  # args and kwargs
    variable: str

    @property
    def is_static(self):
        return self.converter is None

    @property
    def ts_type(self):
        if self.args and self.converter == "any":
            return " | ".join(repr(s) for s in self.args[0])
        return {
            "default": "string",
            "int": "number",
            "float": "number",
            "any": "string",
        }.get(self.converter, self.converter)


@dataclass
class TSRule:
    endpoint: str
    rule: str
    url_fmt_arguments: t.Tuple[Fmt, ...]
    url: str
    url_arguments: t.Tuple[str, ...]
    defaults: t.Mapping[str, t.Any]

    def resolve_defaults(self, app: Flask) -> "TSRule":
        values = dict(self.defaults)
        app.inject_url_defaults(self.endpoint, values)
        if not values:
            return self

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

        return replace(self, url=url, url_arguments=tuple(url_arguments))


def process_rule(r: Rule) -> TSRule:
    url_fmt_arguments = [
        Fmt(u[0], parse_converter_args(u[1]) if u[1] is not None else None, u[2])
        for u in parse_rule(r.rule)
    ]
    url = "".join(
        f.variable if f.is_static else "${%s}" % f.variable for f in url_fmt_arguments
    )
    url_arguments = [f.variable for f in url_fmt_arguments if not f.is_static]
    return TSRule(
        r.endpoint,
        r.rule,
        tuple(url_fmt_arguments),
        url,
        tuple(url_arguments),
        r.defaults or {},
    )
