import typing as t
import click
from flask.cli import pass_script_info

from .systemd import NGINX_HELP, config_options, nginx

if t.TYPE_CHECKING:
    # pylint: disable=unused-import
    from flask.cli import ScriptInfo


@click.command(name="nginx", help=NGINX_HELP)  # noqa: C901
@click.option("-t", "--template", metavar="TEMPLATE_FILE", help="template file")
@config_options
@click.argument("server_name")
@click.argument("params", nargs=-1)
@pass_script_info
def nginx_cmd(
    script_info: "ScriptInfo",
    server_name: str,
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
        app,
        server_name,
        params,
        template,
        check=not no_check,
        output=output,
    )
