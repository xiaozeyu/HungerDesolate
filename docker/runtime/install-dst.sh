#!/usr/bin/env bash
# Build-time only: anonymous SteamCMD bootstrap and validated DST download.
set -euo pipefail
export LC_ALL=C

steamcmd_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$steamcmd_dir"
dst_dir="$steamcmd_dir/dst"
phase=${1:-}
case "$phase" in
    bootstrap) deadline=600s; failure_code=STEAMCMD_BOOTSTRAP_FAILED ;;
    download) deadline=1800s; failure_code=STEAMCMD_DOWNLOAD_FAILED ;;
    *) echo 'Usage: install-dst.sh bootstrap|download' >&2; exit 2 ;;
esac

diagnostics() {
    # This isolated builder logs in anonymously and never receives instance credentials.
    for logs in "$steamcmd_dir/logs" "$steamcmd_dir/Steam/logs" "$steamcmd_dir/.steam/steam/logs" "$steamcmd_dir/.local/share/Steam/logs"; do
        for name in bootstrap_log.txt content_log.txt stderr.txt; do
            if [ -f "$logs/$name" ]; then
                printf '\n==> SteamCMD %s (%s)\n' "$name" "$logs"
                tail -n 40 -- "$logs/$name"
            fi
        done
    done
    df -Pk "$steamcmd_dir"
    df -Pi "$steamcmd_dir"
}

if [ "$phase" = bootstrap ]; then
    arguments=(+quit)
else
    arguments=(+@ShutdownOnFailedCommand 1 +@NoPromptForPassword 1
        +force_install_dir "$dst_dir" +login anonymous +app_update 343050 validate +quit)
fi

phase_log=$(mktemp "$steamcmd_dir/.steamcmd-$phase.XXXXXX")
trap 'rm -f -- "$phase_log"' EXIT
for attempt in 1 2 3; do
    printf '\n==> SteamCMD phase=%s attempt=%s/3\n' "$phase" "$attempt"
    set +e
    timeout --signal=TERM --kill-after=30s "$deadline" ./steamcmd.sh "${arguments[@]}" 2>&1 | tee "$phase_log"
    statuses=("${PIPESTATUS[@]}")
    set -e
    status=${statuses[0]}
    if [ "${statuses[1]}" -ne 0 ]; then
        echo 'STEAMCMD_LOG_WRITE_FAILED: cannot record build output' >&2
        exit 1
    fi
    if [ "$status" -eq 0 ]; then
        if [ "$phase" = download ]; then
            if ! grep -Eq "Success! App '343050' (fully installed|already up to date)\." "$phase_log" \
                || [ ! -x "$dst_dir/bin64/dontstarve_dedicated_server_nullrenderer_x64" ] \
                || [ ! -s "$dst_dir/version.txt" ] \
                || ! grep -Eq '^[0-9]+[[:space:]]?$' "$dst_dir/version.txt" \
                || [ ! -s "$dst_dir/data/databundles/scripts.zip" ]; then
                echo 'DST_ARTIFACT_INVALID: success marker, executable, version or game scripts missing' >&2
                diagnostics || printf 'SteamCMD diagnostics were incomplete\n' >&2
                exit 1
            fi
        fi
        printf '==> SteamCMD phase=%s complete\n' "$phase"
        exit 0
    fi
    printf '%s: phase=%s attempt=%s/3 exitCode=%s\n' "$failure_code" "$phase" "$attempt" "$status" >&2
    diagnostics || printf 'SteamCMD diagnostics were incomplete\n' >&2
    case "$status" in
        8|42|124) if [ "$attempt" -lt 3 ]; then sleep 5; continue; fi ;;
    esac
    exit "$status"
done
