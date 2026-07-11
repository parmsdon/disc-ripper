#!/bin/bash
set -e

SESSION="discripper"

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Session '$SESSION' already exists — attaching."
    tmux attach-session -t "$SESSION"
    exit 0
fi

echo "Starting Disc Ripper (app/db)..."

tmux new-session -d -s "$SESSION" -n "claude"
tmux send-keys -t "$SESSION:claude" "claude --dangerously-skip-permissions --resume" Enter

tmux new-window -t "$SESSION" -n "psql"
tmux send-keys -t "$SESSION:psql" "export PGPASSWORD=a11iance; psql -h 192.168.0.22 -U discripper -d discripper_dev" Enter

tmux new-window -t "$SESSION" -n "api"
tmux send-keys -t "$SESSION:api" "cd /projects/ripperdev && source venv/bin/activate && export DISCRIPPER_ENV=dev && python3 api/app.py" Enter

tmux new-window -t "$SESSION" -n "frontend"
tmux send-keys -t "$SESSION:frontend" "cd /projects/ripperdev/frontend && npm run dev" Enter

tmux new-window -t "$SESSION" -n "encoder"
tmux send-keys -t "$SESSION:encoder" "cd /projects/ripperdev && source venv/bin/activate && export DISCRIPPER_ENV=dev && python3 -m encoder_service.main" Enter

tmux new-window -t "$SESSION" -n "shell"
tmux send-keys -t "$SESSION:shell" "cd /projects/ripperdev && source venv/bin/activate" Enter

tmux select-window -t "$SESSION:claude"

if [ -n "$TMUX" ] || [ -t 0 ]; then
    tmux attach-session -t "$SESSION"
else
    echo "Run: tmux attach-session -t $SESSION"
fi
