mypy:
	mypy -p footprint --show-error-codes --disable-error-code=import

# warning! a file called flask.py in the package will mean that flask is
# not considered a requirement!
# We don't want versioning since footprint has to be installed with any flask app
# virtual environment unfortunately
requirements:
	@pipreqs --print footprint | sed 's/==.*$$//' | sort | uniq > requirements.txt

# run pre-commit autoupdate to update versions in .pre-commit-config.yaml
pre-commit:
	pre-commit run --all-files

pylint:
	pylint footprint/

install:
	pip install --editable .
