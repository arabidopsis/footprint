requirements:
	@pipreqs --print footprint | sed 's/==/>=/' | sed 's/bio>/biopython>/' | sort | uniq > requirements.txt

pre-commit:
	pre-commit run --all-files

install:
	pip install --editable .
