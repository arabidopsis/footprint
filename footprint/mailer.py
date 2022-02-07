import smtplib
import typing as t
from email.mime.text import MIMEText

import click

from .cli import cli
from .config import MAILHOST

# from email.mime.image import MIMEImage
# from email.mime.multipart import MIMEMultipart


def sendmail(
    html: str,
    you: str,
    me: str = "footprint@uwa.edu.au",
    mailhost: str = MAILHOST,
    subject: str = "footprint monitor",
) -> None:
    msg = MIMEText(html, "html")

    msg["Subject"] = subject
    msg["From"] = me
    msg["To"] = you

    with smtplib.SMTP() as s:
        s.connect(mailhost)
        s.sendmail(me, [you], msg.as_string())


@cli.command()
@click.option("--mailhost", default=MAILHOST)
@click.argument("email")
@click.argument("message", nargs=-1)
def email_test(email: str, message: t.List[str], mailhost: str = MAILHOST):
    """Test email from this host"""
    if not message:
        raise click.BadArgumentUsage("no message")

    sendmail(
        " ".join(message),
        you=email,
        mailhost=mailhost,
        subject="Message from footprint",
    )
    click.secho("message sent!", fg="green", bold=True)
