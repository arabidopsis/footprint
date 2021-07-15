import collections
from functools import wraps
from dataclasses import dataclass, MISSING, make_dataclass, fields as dcfields
import typing as t
import click
from flask import request, jsonify
from werkzeug.datastructures import CombinedMultiDict, MultiDict
from marshmallow import Schema
from marshmallow.exceptions import ValidationError
from marshmallow.fields import Nested
from typing_extensions import Literal
from .typing import DataClassJsonMixin, get_annotations, is_dataclass_type

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
            if hasattr(typ, "__args__") and len(typ.__args__) > 1:
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
    ret: t.Dict[str, t.List[str]] = {}
    for k, v in d.items():
        msgs = []
        if isinstance(v, dict):
            for k1, m1 in flatten(v).items():  # pylint: disable=no-member
                if k1 in ret:
                    ret[k1].extend(m1)
                elif k1 == k or k1.isdigit():
                    msgs.extend(m1)
                else:
                    ret[k1] = m1
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
