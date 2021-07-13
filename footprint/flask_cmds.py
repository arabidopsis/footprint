import typing as t

import click
from flask.cli import pass_script_info

from footprint.typing import TSFunction

from .systemd import NGINX_HELP, config_options, nginx

if t.TYPE_CHECKING:
    # pylint: disable=unused-import ungrouped-imports
    from flask.cli import ScriptInfo


# commands installed to flask. See 'flask footprint --help'
@click.group(help=click.style("Footprint commands", fg="magenta"))
def footprint():
    pass


@footprint.command(name="nginx", help=NGINX_HELP)  # noqa: C901
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@click.option(
    "-d", "--application-dir", metavar="DIRECTORY", help="application directory"
)
@config_options
@click.argument("server_name")
@click.argument("params", nargs=-1)
@pass_script_info
def nginx_cmd(
    script_info: "ScriptInfo",
    server_name: str,
    application_dir: t.Optional[str],
    template: t.Optional[str],
    params: t.List[str],
    no_check: bool,
    output: t.Optional[str],
) -> None:
    """Generate nginx config file.

    PARAMS are key=value arguments for the template.
    """

    app = script_info.load_app()

    nginx(
        application_dir or ".",
        server_name,
        params,
        app=app,
        template_name=template,
        check=not no_check,
        output=output,
    )


@footprint.command(name="ts")
@click.argument("modules", nargs=-1)
@pass_script_info
def typescript_cmd(script_info: "ScriptInfo", modules: t.Tuple[str, ...]):
    """Generate a typescript file for a flask application

    Modules are a list of modules to import for name resolution. By default
    the names in the Flask package are imported
    """
    from collections import defaultdict
    from dataclasses import replace
    from importlib import import_module

    # from os.path import join
    import flask

    from .typed_flask import TSRule, process_rule
    from .typing import TSBuilder, TSClass, TSField, TSInterface

    ns = flask.__dict__.copy()

    app = script_info.load_app()
    for m in modules:
        mod = import_module(m)
        ns.update(mod.__dict__)

    builder = TSBuilder(ns=ns)

    class Restful(t.NamedTuple):
        function: TSFunction
        rule: TSRule

    # appfolder = join(app.root_path, app.template_folder)
    dd: t.Dict[str, t.Dict[str, Restful]] = defaultdict(dict)

    for rule in app.url_map.iter_rules():
        if rule.endpoint.endswith("static"):
            continue
        p = process_rule(rule)
        view_func = app.view_functions[p.endpoint]
        bp = p.endpoint.rpartition(".")[0]
        if bp in app.blueprints:
            blueprint = app.blueprints[bp]
        else:
            blueprint = app
        # if blueprint.template_folder:
        #     folder = join(blueprint.root_path, blueprint.template_folder)
        # else:
        #     folder = appfolder
        try:
            ts = builder.get_func_ts(view_func)
            args = p.ts_args()
            tsargs = {a.name: a.type for a in ts.args if a.name in args}
            if not args == tsargs:
                click.secho(
                    f"incompatible args {p.endpoint}: {args} {tsargs}",
                    fg="red",
                    err=True,
                )
            dd[blueprint.name][ts.name] = Restful(ts, p)
        except (NameError, TypeError) as e:
            err = click.style(f"{view_func.__name__}: {e}", fg="red")
            click.echo(err, err=True)
        # print(bp, p.endpoint, p.ts_args(), ts)

    def to_interface(name: str, fields: t.Iterable[Restful]) -> TSInterface:
        lfields = [
            TSField(
                name=r.function.name,
                type=r.function.to_promise(asjquery=True).anonymous(),
            )
            for r in fields
        ]
        return TSInterface(name=name, fields=lfields)

    def to_class(
        name: str, fields: t.Iterable[Restful], indent="   ", nl="\n"
    ) -> TSClass:
        funcs = []
        tab = f"{nl}{indent}"
        for r in fields:
            p2 = r.function.to_promise(asjquery=True)
            data = []
            for arg in p2.args:
                if arg.name in r.rule.url_arguments:
                    continue
                if arg.is_dataclass:
                    data.append(f"...{arg.name}")
                else:
                    data.append(arg.name)
            body = [f'const data = {{ {", ".join(data)} }}']
            body.append(f"return $.get(`{r.rule.url}`, data)")

            p2 = replace(
                p2,
                export=False,
                body=f"{indent}{tab.join(body)}",
            )

            funcs.append(TSField(name=r.function.name, type=p2.anonymous(), colon=""))

        app = TSClass(name=name, fields=funcs)
        return app

    for _, o in builder.process_seen():
        ot = builder(o)
        print(ot)
    for bp, d in dd.items():
        print(f"// {bp}")
        app = to_interface(bp.title(), d.values())
        print(app)
        cls = to_class(bp.title(), d.values())
        print(cls)
        print(f"export const app = new {cls.name}Class()")
