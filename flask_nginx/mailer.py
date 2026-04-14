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
    username = password = None
    if "@" in mailhost:
        uw, mailhost = mailhost.split("@", maxsplit=1)
        username, password = uw.split(":", maxsplit=1)

    port = 0
    if ":" in mailhost:
        mailhost, p = mailhost.split(":", maxsplit=1)
        port = int(p)

    if username is not None and password is not None:

        with smtplib.SMTP(mailhost, port, timeout=timeout) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(username, password)
            s.sendmail(me, [you], msg.as_string())
        return

    with smtplib.SMTP(mailhost, port, timeout=timeout) as s:
        s.sendmail(me, [you], msg.as_string())


@cli.command()
@click.option("-m", "--mailhost", help="mail host to use [default from config]")
@click.option(
    "-f",
    "--from",
    "me",
    help="sender",
    default="footprint@uwa.edu.au",
    show_default=True,
)
@click.option("-t", "--timeout", default=20.0, help="timeout to wait for connection")
@click.argument("email")
@click.argument("message", nargs=-1)
def email_test(
    email: str,
    me: str,
    message: list[str],
    mailhost: str | None,
    timeout: float,
) -> None:
    """Test email setup from this host"""
    import platform

    if not message:
        raise click.BadArgumentUsage("no message")
    mh = str(mailhost)
    if mailhost is not None and "@" in mailhost:
        _, mh = mailhost.split("@", maxsplit=1)

    message = [*message, f" (Sent via {mh})"]

    sendmail(
        " ".join(message),
        you=email,
        mailhost=mailhost,
        subject=f"Message from footprint on {platform.node()}",
        me=me,
        timeout=timeout,
    )
    click.secho("message sent!", fg="green", bold=True)
