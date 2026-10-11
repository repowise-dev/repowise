#!/bin/sh
# A script with no function definitions — must not crash the walker.

echo "starting"
for f in *.log; do
    rm -f "$f"
done
echo "done"
