#!/usr/bin/env bash
set -u
ROOT=/root/task
cd "$ROOT"
pip install -q -r requirements.txt

printf 'Checking local scaffold...\n'
python -m app selfcheck
if [ "$?" -ne 0 ]; then
    exit 2
fi

printf 'Running invariant tests...\n'
python -m pytest -q
rc=$?
if [ "$rc" -le 1 ]; then
    printf 'Repository is ready. Candidate invariants may fail before the task is complete.\n'
    exit 0
else
    exit "$rc"
fi
