#!/usr/bin/env bash
# Manage Samba and Plex containers on holly for one environment (dev or prod).
# Usage: holly.sh {up|down|restart|status|logs [samba|plex]|pull}
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/holly.env"
SECRETS_FILE="${SCRIPT_DIR}/holly.secrets"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Error: $ENV_FILE not found." >&2
    echo "  cp ${SCRIPT_DIR}/holly.env.example $ENV_FILE  # then edit it" >&2
    exit 1
fi

# shellcheck source=/dev/null
source "$ENV_FILE"

: "${ENV:?ENV not set in holly.env}"
: "${LIBRARY_PATH:?LIBRARY_PATH not set in holly.env}"
: "${SAMBA_IP:?SAMBA_IP not set in holly.env}"
: "${PLEX_IP:?PLEX_IP not set in holly.env}"
: "${SAMBA_USER:?SAMBA_USER not set in holly.env}"
: "${SAMBA_UID:?SAMBA_UID not set in holly.env}"
: "${PLEX_CONFIG_PATH:?PLEX_CONFIG_PATH not set in holly.env}"
: "${PLEX_TRANSCODE_PATH:?PLEX_TRANSCODE_PATH not set in holly.env}"
: "${PLEX_ALLOWED_NETWORKS:?PLEX_ALLOWED_NETWORKS not set in holly.env}"
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
    if [[ ! -f "$SECRETS_FILE" ]]; then
        echo "Error: $SECRETS_FILE not found." >&2
        echo "  cp ${SCRIPT_DIR}/holly.secrets.example $SECRETS_FILE" >&2
        echo "  # Edit it, set the real password, then:" >&2
        echo "  chmod 600 $SECRETS_FILE" >&2
        exit 1
    fi
    mkdir -p "$PLEX_TRANSCODE_PATH"
}

cmd_up() {
    validate_paths

    # Build protocol args based on SAMBA_ALLOW_SMB1 (default: false = SMB2+, modern auth).
    # SAMBA_GLOBAL_CONFIG_<NAME>=<VALUE> → writes " NAME = VALUE" to [global] in smb.conf.
    # Spaces in NAME are encoded as _SPACE_. VALUE is the value only, not "key = value".
    samba_protocol_args=()
    if [[ "${SAMBA_ALLOW_SMB1:-false}" == "true" ]]; then
        samba_protocol_args=(
            -e "SAMBA_GLOBAL_CONFIG_server_SPACE_min_SPACE_protocol=NT1"
            -e "SAMBA_GLOBAL_CONFIG_ntlm_SPACE_auth=ntlmv1-permitted"
        )
    else
        samba_protocol_args=(
            -e "SAMBA_GLOBAL_CONFIG_server_SPACE_min_SPACE_protocol=SMB2"
        )
    fi

    echo "Starting $SAMBA_CONTAINER ..."
    # Samba image: ghcr.io/servercontainers/samba (last changelog: 2026-07-11)
    # Configuration is entirely via environment variables; no separate config file needed.
    # ACCOUNT_* (password) is passed via --env-file so it does not appear in process
    # listings or docker inspect's command/args fields.
    docker run -d \
        --name "$SAMBA_CONTAINER" \
        --net=staticnet \
        --ip="$SAMBA_IP" \
        --restart="$RESTART_POLICY" \
        --env-file "$SECRETS_FILE" \
        -e "UID_${SAMBA_USER}=${SAMBA_UID}" \
        -e "SAMBA_VOLUME_CONFIG_flac=[flac]; path=/mnt/library/cd/flac; valid users = ${SAMBA_USER}; guest ok = no; read only = yes; browseable = yes" \
        -e "SAMBA_VOLUME_CONFIG_mp3=[mp3]; path=/mnt/library/cd/mp3; valid users = ${SAMBA_USER}; guest ok = no; read only = yes; browseable = yes" \
        -e "SAMBA_VOLUME_CONFIG_iso=[iso]; path=/mnt/library/dvd/iso; valid users = ${SAMBA_USER}; guest ok = no; read only = yes; browseable = yes" \
        -e "SAMBA_VOLUME_CONFIG_iphone=[iphone]; path=/mnt/library/dvd/iphone; valid users = ${SAMBA_USER}; guest ok = no; read only = yes; browseable = yes" \
        -e "SAMBA_VOLUME_CONFIG_plex=[plex]; path=/mnt/library/dvd/plex; valid users = ${SAMBA_USER}; guest ok = no; read only = yes; browseable = yes" \
        "${samba_protocol_args[@]}" \
        -v "${LIBRARY_PATH}:/mnt/library:ro" \
        ghcr.io/servercontainers/samba:a3.24.4-s4.23.8-r0

    # Verify smb.conf loaded cleanly (wait up to 15s for entrypoint to write it)
    echo "Verifying Samba configuration ..."
    samba_ok=0
    for i in $(seq 1 15); do
        if docker exec "$SAMBA_CONTAINER" test -f /etc/samba/smb.conf 2>/dev/null; then
            samba_ok=1
            break
        fi
        sleep 1
    done
    if [[ $samba_ok -eq 0 ]]; then
        echo "Error: smb.conf was not written after 15s." >&2
        docker logs --tail=30 "$SAMBA_CONTAINER" >&2
        docker rm -f "$SAMBA_CONTAINER"
        exit 1
    fi
    if ! docker exec "$SAMBA_CONTAINER" testparm -s >/dev/null; then
        echo "Error: Samba configuration failed testparm validation." >&2
        docker logs --tail=30 "$SAMBA_CONTAINER" >&2
        docker rm -f "$SAMBA_CONTAINER"
        exit 1
    fi
    echo "Samba config OK."

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
        -e "ADVERTISE_IP=http://${PLEX_IP}:32400" \
        -e "ALLOWED_NETWORKS=${PLEX_ALLOWED_NETWORKS}" \
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
    docker pull ghcr.io/servercontainers/samba:a3.24.4-s4.23.8-r0
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
