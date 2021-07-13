import typing as t

import click
from flask.cli import pass_script_info

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
@click.option("--js", "as_js", is_flag=True, help="render as javascript")
@click.argument("modules", nargs=-1)
@pass_script_info
def typescript_cmd(script_info: "ScriptInfo", modules: t.Tuple[str, ...], as_js: bool):
    """Generate a typescript file for a flask application

    Modules are a list of modules to import for name resolution. By default
    the names in the Flask package are imported
    """
    from .typed_flask import flask_api

    app = script_info.load_app()

    flask_api(app, modules, as_js=as_js)
