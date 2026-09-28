#!/bin/sh
# Генерация HTML-документации кода (pdoc) в docs/api
cd "$(dirname "$0")/.." || exit 1
rm -rf docs/api
python3 -m pdoc lcc $(find lcc -mindepth 2 -name "*.py" ! -name "__init__.py" | sed 's#/#.#g; s#\.py$##' | sort) \
    -o docs/api --docformat google --no-show-source
