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
) -> None:
    from .config import MAILHOST

    if mailhost is None:
        mailhost = MAILHOST
    msg = MIMEText(html, "html")

    msg["Subject"] = subject
    msg["From"] = me
    msg["To"] = you

    with smtplib.SMTP() as s:
        s.connect(mailhost)
        s.sendmail(me, [you], msg.as_string())


@cli.command()
@click.option("-m", "--mailhost", help="mail host to use [default from config]")
@click.argument("email")
@click.argument("message", nargs=-1)
def email_test(email: str, message: list[str], mailhost: str):
    """Test email from this host"""
    import platform

    if not message:
        raise click.BadArgumentUsage("no message")

    sendmail(
        " ".join(message),
        you=email,
        mailhost=mailhost,
        subject=f"Message from footprint on {platform.node()}",
    )
    click.secho("message sent!", fg="green", bold=True)
