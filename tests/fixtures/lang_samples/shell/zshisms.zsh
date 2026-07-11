#!/usr/bin/env zsh
# Mild zsh-isms: tree-sitter-bash parses the bash/POSIX subset. A parse error
# on a zsh-only construct must degrade per-file, never crash — the plain
# function below must still be extracted.

autoload -Uz compinit

typeset -A colors
colors[red]="31"

setup() {
    echo "configured"
}
