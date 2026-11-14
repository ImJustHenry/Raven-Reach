#!/usr/bin/env bash
# One-time setup: Python 3.12 virtual env + Raven Framework + dependencies.
set -euo pipefail
cd "$(dirname "$0")"
UV="$(command -v uv || echo "$HOME/Library/Python/3.9/bin/uv")"
if [ ! -x "$UV" ]; then
  echo "Installing uv (Python manager)…"; curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi
[ -d ../raven-framework ] || git clone https://github.com/RavenResonance/raven-framework.git ../raven-framework
[ -d .venv ] || "$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python -e ../raven-framework -r requirements.txt pytest pytest-qt
mkdir -p ~/.config/raven-reach
[ -f ~/.config/raven-reach/config.json ] || cp config.example.json ~/.config/raven-reach/config.json
.venv/bin/python -m pytest -q tests
echo; echo "Done. Edit ~/.config/raven-reach/config.json, then run: ./run.sh"
