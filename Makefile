typescript:
	npx tsc footprint/templates/web/*.ts --target es2015 --module es2015

mypy:
	mypy -p footprint --show-error-codes --disable-error-code=import

# warning! a file called flask.py in the package will mean that flask is
# not considered a requirement!
requirements:
	@pipreqs --print footprint | sed 's/==/>=/' | sed 's/bio>/biopython>/' | sort | uniq > requirements.txt

# run pre-commit autoupdate to update versions in .pre-commit-config.yaml
pre-commit:
	pre-commit run --all-files

pylint:
	pylint footprint/

install:
	pip install --editable .
