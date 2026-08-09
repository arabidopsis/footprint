# mypy: disable-error-code="import-not-found"
from __future__ import annotations

from pathlib import Path

import click

from .cli import cli


def img2ico(png: Path, out: Path) -> None:
    from PIL import Image

    with png.open("rb") as fp:
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
@click.option("-o", "--output", help="output filename", type=click.Path(dir_okay=False, writable=True, path_type=Path))
@click.argument(
    "image",
    nargs=1,
    type=click.Path(exists=True, dir_okay=False, file_okay=True, path_type=Path),
)
def img_to_ico(image: Path, output: Path | None) -> None:
    """Convert a image file to an .ico file [**requires Pillow**]."""
    from .utils import require_mod

    require_mod("PIL", "Pillow")

    # see https://anaconda.org/conda-forge/svg2png
    if output is None:
        output = Path(image).with_suffix(".ico")

    img2ico(image, output)
