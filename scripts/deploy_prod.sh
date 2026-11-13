#!/bin/bash
# Deploy the latest code to production.
# Must be run as the 'ripper' user from either host.
# Requires passwordless sudo for systemctl restart on the relevant units
# (configure /etc/sudoers.d/discripper on each host - see DEPLOYMENT.md).
#
# Usage:
#   ./scripts/deploy_prod.sh app     # app/DB machine (ripper): API + encoder
#   ./scripts/deploy_prod.sh ripper  # ripper machine (rimmer): ripper service

set -e

TARGET="${1:-}"
if [[ "$TARGET" != "app" && "$TARGET" != "ripper" ]]; then
    echo "Usage: $0 app|ripper"
    exit 1
fi

cd /projects/ripper

echo "=== git pull ==="
git pull

echo "=== pip install ==="
source venv/bin/activate
if [[ "$TARGET" == "app" ]]; then
    pip install -r requirements.txt
elif [[ "$TARGET" == "ripper" ]]; then
    pip install -r requirements-ripper.txt
fi

if [[ "$TARGET" == "app" ]]; then
    echo "=== alembic upgrade head ==="
    export DISCRIPPER_ENV=prod
    alembic upgrade head

    echo "=== npm ci + build ==="
    cd frontend
    npm ci
    npm run build
    cd ..

    echo "=== deploy frontend to /var/www/discripper ==="
    sudo rsync -a --delete frontend/dist/ /var/www/discripper/

    echo "=== restart api, encoder and mymovies ==="
    sudo systemctl restart discripper-api discripper-encoder discripper-mymovies

elif [[ "$TARGET" == "ripper" ]]; then
    echo "=== restart ripper ==="
    sudo systemctl restart discripper-ripper
fi

echo "=== deploy complete ==="
