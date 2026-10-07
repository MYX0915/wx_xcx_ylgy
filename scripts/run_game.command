#!/bin/zsh
set -eu
PROJECT_ROOT="${0:A:h:h}"
if [[ ! -x "$PROJECT_ROOT/scripts/game_window" || "$PROJECT_ROOT/scripts/game_window.swift" -nt "$PROJECT_ROOT/scripts/game_window" ]]; then
    swiftc "$PROJECT_ROOT/scripts/game_window.swift" -o "$PROJECT_ROOT/scripts/game_window"
fi
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/play_game.py" "$@"
