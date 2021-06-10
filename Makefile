requirements:
	@pipreqs --print footprint | sed 's/==/>=/' | sed 's/bio>/biopython>/' | sort | uniq > requirements.txt

# run pre-commit autoupdate to update versions in .pre-commit-config.yaml
pre-commit:
	pre-commit run --all-files

pylint:
	pylint footprint/

install:
	pip install --editable .
