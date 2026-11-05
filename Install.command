#!/bin/bash
# Install only into this extracted software folder. No administrator access.
cd -- "$(dirname -- "$0")" || exit 1
pause_exit() {
    printf '\nPress Return to close this window. '
    read -r answer
    exit "$1"
}
printf 'Canvas Download Tools setup for macOS\n'
printf 'Requires macOS 14 or newer, Python 3.10+, and Google Chrome or Microsoft Edge.\n\n'
if [ "$(uname -s)" != "Darwin" ]; then
    printf 'Use Install.cmd on Windows.\n'
    pause_exit 1
fi
mac_version=$(sw_vers -productVersion)
if [ "${mac_version%%.*}" -lt 14 ]; then
    printf 'This version requires macOS 14 or newer.\n'
    pause_exit 1
fi
if [ ! -x .venv/bin/python3 ]; then
    python_bin=''
    path_python=$(command -v python3 2>/dev/null || true)
    for candidate in /usr/local/bin/python3 /opt/homebrew/bin/python3 "$path_python"; do
        # Avoid invoking Apple's development-tools placeholder.
        [ -n "$candidate" ] && [ "$candidate" != /usr/bin/python3 ] && [ -x "$candidate" ] || continue
        if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
            python_bin=$candidate
            break
        fi
    done
    if [ -z "$python_bin" ]; then
        printf 'Python 3.10 or newer was not found. Install an institution-approved Python\n'
        printf 'or use the macOS installer at https://www.python.org/downloads/macos/\n'
        printf 'Then run Install.command again.\n'
        pause_exit 1
    fi
    if ! "$python_bin" -m venv .venv; then
        printf 'Could not create the local Python environment. Check folder permissions.\n'
        pause_exit 1
    fi
fi
if ! .venv/bin/python3 -m pip install -r requirements.txt; then
    printf '\nSetup did not finish. Review the installation error above.\n'
    printf 'For a certificate error with python.org Python, run its Install Certificates.command.\n'
    pause_exit 1
fi
printf '\nSetup complete. Open Start App.command to use the app.\n'
pause_exit 0
