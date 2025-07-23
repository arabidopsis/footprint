from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

import click

from .cli import cli

# from email.mime.image import MIMEImage
# from email.mime.multipart import MIMEMultipart


def sendmail(
    html: str,
    you: str,
    me: str = "footprint@uwa.edu.au",
    mailhost: str | None = None,
    subject: str = "footprint monitor",
    timeout: float = 20.0,
) -> None:
    from .config import get_config

    if mailhost is None:
        mailhost = get_config().mailhost
    msg = MIMEText(html, "html")

    msg["Subject"] = subject
    msg["From"] = me
    msg["To"] = you

    with smtplib.SMTP(timeout=timeout) as s:
        s.connect(mailhost)
        s.sendmail(me, [you], msg.as_string())


@cli.command()
@click.option("-m", "--mailhost", help="mail host to use [default from config]")
@click.option("-t", "--timeout", default=20.0, help="timeout to wait for connection")
@click.argument("email")
@click.argument("message", nargs=-1)
def email_test(
    email: str,
    message: list[str],
    mailhost: str | None,
    timeout: float,
) -> None:
    """Test email setup from this host"""
    import platform

    if not message:
        raise click.BadArgumentUsage("no message")

    message = [*message, f" (Sent via {mailhost})"]

    sendmail(
        " ".join(message),
        you=email,
        mailhost=mailhost,
        subject=f"Message from footprint on {platform.node()}",
        timeout=timeout,
    )
    click.secho("message sent!", fg="green", bold=True)
