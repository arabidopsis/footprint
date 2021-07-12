import typing as t

import click
from flask.cli import pass_script_info

from .systemd import NGINX_HELP, config_options, nginx

if t.TYPE_CHECKING:
    # pylint: disable=unused-import
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
    from collections import defaultdict
    from importlib import import_module

    # from os.path import join
    import flask

    from .typed_flask import process_rule
    from .typing import TSBuilder

    ns = flask.__dict__.copy()
    for m in modules:
        mod = import_module(m)
        ns.update(mod.__dict__)

    builder = TSBuilder(ns=ns)
    app = script_info.load_app()
    # appfolder = join(app.root_path, app.template_folder)
    dd = defaultdict(dict)

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
                    f"incompatible args {p.endpoint}: {args} {tsargs}", fg="red"
                )
            dd[blueprint.name][ts.name] = (ts, p)
        except (NameError, TypeError) as e:
            ts = click.style(f"{view_func.__name__}: {e}", fg="red")
        # print(bp, p.endpoint, p.ts_args(), ts)

    for bp, d in dd.items():
        print(f"// {bp}")
        for ts, p in d.values():
            print(ts)

    for _, o in builder.process_seen():
        ot = builder(o)
        print(ot)
