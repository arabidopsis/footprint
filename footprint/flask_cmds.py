import typing as t

import click
from flask.cli import ScriptInfo, pass_script_info

from .systemd import NGINX_HELP, config_options, nginx


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
    script_info: ScriptInfo,
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
@click.option("--js", "as_js", is_flag=True, help="render as javascript")
@click.option("-s", "--stdout", is_flag=True, help="print to stdout")
@click.option("-f", "--fetch", is_flag=True, help="use fetch")
@click.option("-c", "--ensure-class", is_flag=True, help="ensure class is created")
@click.option("-d", "--defaults", help="defaults file")
@click.argument("modules", nargs=-1)
@pass_script_info
def typescript_cmd(
    script_info: ScriptInfo,
    modules: t.Tuple[str, ...],
    as_js: bool,
    stdout: bool,
    defaults: t.Optional[str],
    fetch: bool,
    ensure_class: bool,
) -> None:
    """Generate a typescript file for a flask application

    Modules are a list of modules to import for name resolution. By default
    the names in the Flask package are imported
    """
    from .typed_flask import BuildContext, flask_api
    from .utils import flatten_toml

    if defaults is not None:
        import toml

        with open(defaults) as fp:
            d = toml.load(fp)
            d = flatten_toml(d)
            print(d)

    else:
        d = None

    app = script_info.load_app()

    flaskapi = flask_api(app, modules, defaults=d, as_jquery=not fetch)
    if not flaskapi.errors:
        ctx = BuildContext(as_js=as_js, stdout=stdout, with_class=ensure_class)
        flaskapi.generate_api(ctx)
