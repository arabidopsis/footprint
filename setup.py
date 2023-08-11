from __future__ import annotations

import re

from setuptools import find_packages
from setuptools import setup

V = re.compile(r'^VERSION\s*=\s*"([^"]+)"\s*$', re.M)


def getversion():
    with open("footprint/config.py", encoding="utf-8") as fp:
        return V.search(fp.read()).group(1)


def getreq():
    with open("requirements.txt", encoding="utf-8") as fp:
        return [f.strip() for f in fp]


setup(
    name="footprint",
    version=getversion(),
    packages=find_packages(),
    include_package_data=True,
    install_requires=getreq(),
    entry_points="""
        [console_scripts]
        footprint=footprint.__main__:cli
    """,
)
