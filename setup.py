from setuptools import find_packages, setup

req = [f.strip() for f in open("requirements.txt")]

setup(
    name="footprint",
    version="0.1",
    packages=find_packages(),
    include_package_data=True,
    install_requires=req,
    entry_points="""
        [console_scripts]
        footprint=footprint.__main__:cli
    """,
)
