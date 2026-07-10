#!/usr/bin/env bash
# Both function definition forms plus a nested call.

greet() {
    local name="$1"
    echo "hello $name"
}

function deploy {
    greet "world"
    build_step
}

build_step() {
    echo "building"
}
