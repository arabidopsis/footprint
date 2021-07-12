import re

from setuptools import find_packages, setup

V = re.compile(r'^VERSION\s*=\s*"([^"]+)"\s*$', re.M)


def getversion():
    with open("footprint/config.py") as fp:
        return V.search(fp.read()).group(1)


req = [f.strip() for f in open("requirements.txt")]

setup(
    name="footprint",
    version=getversion(),
    packages=find_packages(),
    include_package_data=True,
    install_requires=req,
    entry_points="""
        [console_scripts]
        footprint=footprint.__main__:cli
        [flask.commands]
        footprint=footprint.flask_cmds:footprint
    """,
)
