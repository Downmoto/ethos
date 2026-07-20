#!/bin/sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
    command -v curl >/dev/null 2>&1 || {
        echo "curl is required to install uv" >&2
        exit 1
    }
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

uv tool install git+https://github.com/Downmoto/ethos.git

ETHOS="$(uv tool dir --bin)/ethos"
"$ETHOS" init
"$ETHOS" onboard
