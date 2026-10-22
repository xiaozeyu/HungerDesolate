#!/bin/sh
set -eu
export LC_ALL=C

workshop_id=${1:-}
if ! printf '%s' "$workshop_id" | grep -Eq '^[0-9]{1,20}$'; then
    echo 'WORKSHOP_ID_INVALID' >&2
    exit 2
fi

work_root=/work
source_root=/opt/famine/steamcmd
steamcmd_root="$work_root/steamcmd"
home_root="$work_root/home"
output_root="$work_root/output"
destination="$output_root/$workshop_id"

if [ ! -x "$source_root/steamcmd.sh" ] || [ ! -x "$source_root/linux32/steamcmd" ]; then
    echo 'WORKSHOP_STEAMCMD_CAPABILITY_MISSING' >&2
    exit 3
fi
mkdir -p "$home_root" "$output_root"
cp -a "$source_root" "$steamcmd_root"
chmod -R u+rwX "$steamcmd_root"
cd "$steamcmd_root"

set +e
HOME="$home_root" timeout --signal=TERM --kill-after=15s 20m ./steamcmd.sh \
    +@ShutdownOnFailedCommand 1 \
    +@NoPromptForPassword 1 \
    +login anonymous \
    +workshop_download_item 322330 "$workshop_id" validate \
    +quit
status=$?
set -e
if [ "$status" -ne 0 ]; then
    echo 'WORKSHOP_DOWNLOAD_FAILED' >&2
    exit "$status"
fi

content=""
for base in \
    "$steamcmd_root/steamapps/workshop/content/322330/$workshop_id" \
    "$home_root/Steam/steamapps/workshop/content/322330/$workshop_id" \
    "$home_root/.steam/steamcmd/steamapps/workshop/content/322330/$workshop_id" \
    "$home_root/.steam/steam/steamapps/workshop/content/322330/$workshop_id" \
    "$home_root/.steam/steamapps/workshop/content/322330/$workshop_id" \
    "$home_root/.local/share/Steam/steamapps/workshop/content/322330/$workshop_id"
do
    if [ -d "$base" ]; then
        found=$(find "$base" -maxdepth 8 -type f -name modinfo.lua -print -quit)
        if [ -n "$found" ]; then
            content=$(dirname "$found")
            break
        fi
    fi
done
if [ -z "$content" ] || [ ! -s "$content/modinfo.lua" ]; then
    echo 'WORKSHOP_CONTENT_INVALID' >&2
    exit 4
fi
if [ -e "$destination" ]; then
    echo 'WORKSHOP_OUTPUT_EXISTS' >&2
    exit 5
fi
cp -a "$content" "$destination"
test -s "$destination/modinfo.lua"
printf 'WORKSHOP_PREPARED:%s\n' "$workshop_id"
