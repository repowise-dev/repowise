#!/usr/bin/env bash
# A control-flow-heavy function for the complexity walker: if/elif, for,
# c-style for, while, until, case, and && / || command lists.

process() {
    local mode="$1"
    if [[ -z "$mode" ]]; then
        echo "no mode" || exit 1
    elif [[ "$mode" == "fast" ]]; then
        echo "fast"
    else
        echo "slow"
    fi

    for f in *.txt; do
        cat "$f" && echo "ok"
    done

    for (( i = 0; i < 10; i++ )); do
        echo "$i"
    done

    while read -r line; do
        echo "$line"
    done < input.txt

    until check_done; do
        step
    done

    case "$mode" in
        fast) echo A ;;
        slow) echo B ;;
        *) echo C ;;
    esac
}

trivial() {
    echo "nothing to see"
}
