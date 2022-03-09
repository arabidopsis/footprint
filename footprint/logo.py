import os
import typing as t

import click

from .cli import cli


def img2ico(png, out):
    from PIL import Image

    with open(png, "rb") as fp:
        im = Image.open(fp)

        im.thumbnail((128, 128), Image.ANTIALIAS)

        size_tuples = [  # (256, 256),
            (128, 128),
            (64, 64),
            (48, 48),
            (32, 32),
            (24, 24),
            (16, 16),
        ]

        im.save(out, sizes=size_tuples)


@cli.command()
@click.option("-o", "--output", help="output filename")
@click.argument(
    "image", nargs=1, type=click.Path(exists=True, dir_okay=False, file_okay=True)
)
def img_to_ico(image: str, output: t.Optional[str]):
    "Convert a image file to an .ico file."

    # see https://anaconda.org/conda-forge/svg2png
    if output is None:
        out, _ = os.path.splitext(image)
        output = out + ".ico"
    img2ico(image, output)
