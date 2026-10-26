#!/bin/zsh
set -eu
cd "${0:A:h:h}"
if [[ $# -ne 1 ]]; then
  print -u2 'Usage: Tools/package.sh CANONICAL_APP_ZIP'
  exit 2
fi
python="${FENNEC_BUILD_PYTHON:-python3}"
"$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "DMG packaging requires Python 3.10+; set FENNEC_BUILD_PYTHON to its executable.")'
venv="$PWD/work/dmg-venv"
if [[ ! -x "$venv/bin/python3" ]]; then
  "$python" -m venv "$venv"
fi
"$venv/bin/python3" -m pip --disable-pip-version-check --require-virtualenv install \
  --require-hashes --only-binary=:all: -r Tools/requirements-build.txt
"$venv/bin/python3" Tools/distribution.py package "$1"
