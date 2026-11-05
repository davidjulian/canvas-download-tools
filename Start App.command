#!/bin/bash
cd -- "$(dirname -- "$0")" || exit 1
if [ ! -x .venv/bin/python3 ] || ! .venv/bin/python3 -c 'import playwright' 2>/dev/null; then
    printf 'Run Install.command once before starting the app.\nPress Return to close. '
    read -r answer
    exit 1
fi
printf 'The app will open in your browser. Leave this Terminal window open.\n'
printf 'Use Quit local app in the app when finished.\n'
.venv/bin/python3 local_app.py
result=$?
if [ "$result" -ne 0 ]; then
    printf '\nThe app closed with an error. Review the message above.\nPress Return to close. '
    read -r answer
fi
exit "$result"
