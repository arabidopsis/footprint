import typing as t

import click
import flask
from flask.cli import pass_script_info

from .systemd import NGINX_HELP, config_options, nginx

if t.TYPE_CHECKING:
    # pylint: disable=unused-import
    from flask.cli import ScriptInfo


@click.command(name="nginx", help=NGINX_HELP)  # noqa: C901
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
