#!/bin/bash
set -e

SESSION="discripper"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' already exists — attaching."
    tmux attach-session -t "$SESSION"
    exit 0
fi

echo "Starting Disc Ripper (ripper)..."

tmux new-session -d -s "$SESSION" -n "ripper"
tmux send-keys -t "$SESSION:ripper" "cd /projects/ripperdev && source venv/bin/activate && export DISCRIPPER_ENV=dev && python3 -m ripper_service.main" Enter

tmux new-window -t "$SESSION" -n "shell"
tmux send-keys -t "$SESSION:shell" "cd /projects/ripperdev && source venv/bin/activate" Enter

tmux select-window -t "$SESSION:ripper"

if [ -n "$TMUX" ] || [ -t 0 ]; then
    tmux attach-session -t "$SESSION"
else
    echo "Run: tmux attach-session -t $SESSION"
fi
