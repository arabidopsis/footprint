mypy:
	mypy -p footprint --show-error-codes --disable-error-code=import

# run pre-commit autoupdate to update versions in .pre-commit-config.yaml
pre-commit:
	pre-commit run --all-files

pylint:
	pylint footprint/

install:
	pip install --editable .

.PHONY: install pylint pre-commit mypy
