sync:
	uv sync --extra=psutil --dev

mypy:
	mypy --python-executable=.venv/bin/python3 flask_nginx

.PHONY: mypy sync
