#!/bin/zsh
# Shared macOS launcher; paths and arguments may contain spaces.
cd -- "${0:A:h}" || exit 1

finish() {
    local study_exit="$1"
    if (( study_exit == 2 )); then
        print -r -- "Prepared with notices. Check LAST_RUN.md and the coverage reports."
    elif (( study_exit != 0 )); then
        print -r -- "Stopped with an error. Read the message above."
    fi
    if [[ -t 0 && -t 1 && "${CANVAS_NO_PAUSE:-0}" != 1 ]]; then
        read "study_reply?Press Return to close. "
    fi
    exit "$study_exit"
}

study_action="${1:-}"
(( $# > 0 )) && shift
case "$study_action" in
    refresh|sync|portable|setup|window) ;;
    *) print -r -- "Usage: zsh RUN_STUDY.command {refresh|sync|portable|setup|window} [arguments]"; finish 1 ;;
esac

# Keep the Mac environment separate from a Windows .venv in the same archive.
python_candidates=("$PWD/.venv-macos/bin/python3")
if command -v python3 >/dev/null 2>&1; then
    path_python="$(command -v python3)"
    # Finder's small PATH often selects Apple's developer-tools stub first.
    if [[ "$path_python" != /usr/bin/python3 && "$path_python" != /bin/python3 ]]; then
        python_candidates+=("$path_python")
    fi
fi
python_candidates+=(/opt/anaconda3/bin/python3 "$HOME/anaconda3/bin/python3" "$HOME/miniconda3/bin/python3" /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" /usr/bin/python3)
canvas_python=""
for candidate in "${python_candidates[@]}"; do
    if [[ -x "$candidate" ]] && "$candidate" -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        canvas_python="$candidate"
        break
    fi
done
if [[ -z "$canvas_python" ]]; then
    print -r -- "Python 3.11 or newer is required. Install Python 3 from https://www.python.org/downloads/macos/, then run SETUP_THIS_PC.command."
    finish 1
fi

if [[ "$study_action" == window ]]; then
    "$canvas_python" -B -X utf8 resync_app.py --launch "$@"
    study_exit=$?
    (( study_exit == 0 )) || finish "$study_exit"
    exit 0
fi

if [[ "$study_action" == setup ]]; then
    if [[ "$canvas_python" != "$PWD/.venv-macos/bin/python3" ]]; then
        "$canvas_python" -B -X utf8 -m venv "$PWD/.venv-macos"
        study_exit=$?
        (( study_exit == 0 )) || finish "$study_exit"
        canvas_python="$PWD/.venv-macos/bin/python3"
    fi
    print -r -- "Installing the PDF reader and timezone data into the local Mac Python environment."
    "$canvas_python" -B -X utf8 -m pip install -r "$PWD/requirements-ai.txt" "$@"
    study_exit=$?
    (( study_exit == 0 )) || finish "$study_exit"
    "$canvas_python" -B -X utf8 check_dependencies.py
    study_exit=$?
    if (( study_exit == 0 )); then
        print -r -- "Setup complete. You can now run UPDATE_MATERIALS.command or REFRESH_CONTEXT.command."
    fi
    finish "$study_exit"
fi

if [[ "$study_action" == portable ]]; then
    "$canvas_python" -B -X utf8 ai_export.py --portable "$@"
    finish "$?"
fi

"$canvas_python" -B -X utf8 check_dependencies.py
study_exit=$?
(( study_exit == 0 )) || finish "$study_exit"
if [[ "$study_action" == sync ]]; then
    "$canvas_python" -B -X utf8 study_update.py --sync "$@"
else
    "$canvas_python" -B -X utf8 study_update.py "$@"
fi
finish "$?"
