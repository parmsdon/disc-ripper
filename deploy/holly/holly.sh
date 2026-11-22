#!/usr/bin/env bash
# Manage Samba and Plex containers on holly for one environment (dev or prod).
# Usage: holly.sh {up|down|restart|status|logs [samba|plex]|pull}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/holly.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: $ENV_FILE not found." >&2
    echo "  cp $SCRIPT_DIR/holly.env.example $ENV_FILE  # then edit it" >&2
    exit 1
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

: "${ENV:?ENV not set in holly.env}"
: "${LIBRARY_PATH:?LIBRARY_PATH not set in holly.env}"
: "${SAMBA_IP:?SAMBA_IP not set in holly.env}"
: "${PLEX_IP:?PLEX_IP not set in holly.env}"
: "${SAMBA_USER:?SAMBA_USER not set in holly.env}"
: "${SAMBA_PASS:?SAMBA_PASS not set in holly.env}"
: "${PLEX_CONFIG_PATH:?PLEX_CONFIG_PATH not set in holly.env}"
: "${PLEX_TRANSCODE_PATH:?PLEX_TRANSCODE_PATH not set in holly.env}"
: "${TZ:?TZ not set in holly.env}"

SAMBA_CONTAINER="discripper-samba-${ENV}"
PLEX_CONTAINER="discripper-plex-${ENV}"

if [[ "$ENV" == "prod" ]]; then
    RESTART_POLICY="unless-stopped"
else
    RESTART_POLICY="no"
fi

validate_paths() {
    if [[ ! -d "$LIBRARY_PATH" ]]; then
        echo "Error: LIBRARY_PATH '$LIBRARY_PATH' does not exist." >&2
        exit 1
    fi
    if [[ ! -d "$PLEX_CONFIG_PATH" ]]; then
        echo "Error: PLEX_CONFIG_PATH '$PLEX_CONFIG_PATH' does not exist." >&2
        echo "  mkdir -p $PLEX_CONFIG_PATH" >&2
        exit 1
    fi
    mkdir -p "$PLEX_TRANSCODE_PATH"
}

cmd_up() {
    validate_paths

    echo "Starting $SAMBA_CONTAINER ..."
    # dperson/samba flags:
    #   -u "user;pass"           create samba user + set password
    #   -s "name;path;browsable;readonly;guest;validusers"
    #   -g "smb.conf global option"
    #
    # Global options:
    #   server min protocol = NT1   -- SMB1 for older Sonos hardware (S1 app)
    #   ntlm auth = ntlmv1-permitted -- required by some Sonos firmware versions
    #   oplocks / strict locking     -- improve reliability for media shares
    docker run -d \
        --name "$SAMBA_CONTAINER" \
        --net=staticnet \
        --ip="$SAMBA_IP" \
        --restart="$RESTART_POLICY" \
        -e USERID=1001 \
        -e GROUPID=1001 \
        -v "${LIBRARY_PATH}:/mnt/library:ro" \
        dperson/samba \
        -u "${SAMBA_USER};${SAMBA_PASS}" \
        -s "flac;/mnt/library/cd/flac;yes;yes;no;${SAMBA_USER}" \
        -s "mp3;/mnt/library/cd/mp3;yes;yes;no;${SAMBA_USER}" \
        -s "iso;/mnt/library/dvd/iso;yes;yes;no;${SAMBA_USER}" \
        -s "iphone;/mnt/library/dvd/iphone;yes;yes;no;${SAMBA_USER}" \
        -s "plex;/mnt/library/dvd/plex;yes;yes;no;${SAMBA_USER}" \
        -g "server min protocol = NT1" \
        -g "ntlm auth = ntlmv1-permitted" \
        -g "oplocks = no" \
        -g "level2 oplocks = no" \
        -g "strict locking = no"

    echo "Starting $PLEX_CONTAINER ..."
    plex_claim_args=()
    if [[ -n "${PLEX_CLAIM:-}" ]]; then
        plex_claim_args=(-e "PLEX_CLAIM=${PLEX_CLAIM}")
    fi
    docker run -d \
        --name "$PLEX_CONTAINER" \
        --net=staticnet \
        --ip="$PLEX_IP" \
        --restart="$RESTART_POLICY" \
        -e TZ="$TZ" \
        -e PLEX_UID=1001 \
        -e PLEX_GID=1001 \
        "${plex_claim_args[@]}" \
        -v "${PLEX_CONFIG_PATH}:/config" \
        -v "${PLEX_TRANSCODE_PATH}:/transcode" \
        -v "${LIBRARY_PATH}/dvd/plex:/data/plex:ro" \
        plexinc/pms-docker

    echo
    echo "Samba: smb://${SAMBA_IP}/"
    echo "Plex:  http://${PLEX_IP}:32400/web"
}

cmd_down() {
    for name in "$SAMBA_CONTAINER" "$PLEX_CONTAINER"; do
        if docker inspect "$name" &>/dev/null; then
            echo "Removing $name ..."
            docker rm -f "$name"
        else
            echo "$name: not found, skipping."
        fi
    done
}

cmd_restart() {
    cmd_down
    cmd_up
}

cmd_status() {
    docker ps -a \
        --filter "name=^/${SAMBA_CONTAINER}$" \
        --filter "name=^/${PLEX_CONTAINER}$" \
        --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"
}

cmd_logs() {
    local svc="${2:-}"
    case "$svc" in
        samba)
            docker logs -f "$SAMBA_CONTAINER"
            ;;
        plex)
            docker logs -f "$PLEX_CONTAINER"
            ;;
        *)
            echo "=== $SAMBA_CONTAINER (last 50 lines) ==="
            docker logs --tail=50 "$SAMBA_CONTAINER" 2>&1 || echo "  (container not found)"
            echo
            echo "=== $PLEX_CONTAINER (last 50 lines) ==="
            docker logs --tail=50 "$PLEX_CONTAINER" 2>&1 || echo "  (container not found)"
            ;;
    esac
}

cmd_pull() {
    docker pull dperson/samba
    docker pull plexinc/pms-docker
}

CMD="${1:-help}"
case "$CMD" in
    up)       cmd_up ;;
    down)     cmd_down ;;
    restart)  cmd_restart ;;
    status)   cmd_status ;;
    logs)     cmd_logs "$@" ;;
    pull)     cmd_pull ;;
    *)
        echo "Usage: $(basename "$0") {up|down|restart|status|logs [samba|plex]|pull}" >&2
        exit 1
        ;;
esac
