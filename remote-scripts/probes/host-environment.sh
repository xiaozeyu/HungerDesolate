#!/usr/bin/env bash
set -euo pipefail
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH LC_ALL=C
DOCKER_SOCKET=/var/run/docker.sock
MANAGEMENT_ROOT=/var/lib/famine-hub
PYTHON3=/usr/bin/python3
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH

# A fixed, read-only probe. No frontend values are interpolated into this script.
command -v base64 >/dev/null && command -v tr >/dev/null || exit 76
printf 'FHENV\t1\n'
emit() {
  printf '%s\t%s\t' "$1" "$2"
  printf '%s' "$3" | base64 | tr -d '\r\n'
  printf '\n'
}
run() {
  local key="$1" value
  shift
  if value=$("$@" 2>/dev/null); then emit "$key" ok "$value"; else emit "$key" failed ''; fi
}
run kernel uname -s
run architecture uname -m
run uid id -u
if [[ -f /etc/os-release && -r /etc/os-release ]]; then
  run osRelease head -c 16384 /etc/os-release
else emit osRelease missing ''; fi
if [[ -r /proc/meminfo ]]; then
  run meminfo head -c 16384 /proc/meminfo
else emit meminfo missing ''; fi

parent="$MANAGEMENT_ROOT"
while [[ ! -e "$parent" && "$parent" != / ]]; do parent="${parent%/*}"; parent="${parent:-/}"; done
emit diskPath ok "$parent"
run disk df -Pk "$parent"
if [[ ! -d "$parent" ]]; then emit managementAccess invalid ''
elif [[ -w "$parent" && -x "$parent" ]]; then emit managementAccess writable ''
else emit managementAccess permissionDenied ''; fi
if [[ -d "$MANAGEMENT_ROOT" ]]; then emit managementRoot present ''
elif [[ -e "$MANAGEMENT_ROOT" ]]; then emit managementRoot invalid ''
else emit managementRoot missing ''; fi

for tool in bash base64 tr head uname id df timeout tar gzip sha256sum flock jq; do
  if command -v "$tool" >/dev/null; then emit "tool.$tool" ok ''; else emit "tool.$tool" missing ''; fi
done
if [[ -x "$PYTHON3" ]]; then emit tool.python3 ok ''; else emit tool.python3 missing ''; fi
if ! command -v docker >/dev/null; then
  emit dockerClient missing ''; emit compose missing ''; emit dockerServer missing ''; emit dockerRoot missing ''; emit dockerDisk missing ''
elif ! command -v timeout >/dev/null; then
  emit dockerClient unavailable ''; emit compose unavailable ''; emit dockerServer unavailable ''; emit dockerRoot unavailable ''; emit dockerDisk unavailable ''
else
  run dockerClient timeout -k 1 4 docker --version
  run compose timeout -k 1 4 docker --host "unix://$DOCKER_SOCKET" compose version --short
  if [[ ! -S "$DOCKER_SOCKET" ]]; then
    emit dockerServer unavailable ''; emit dockerRoot unavailable ''; emit dockerDisk unavailable ''
  elif [[ ! -r "$DOCKER_SOCKET" || ! -w "$DOCKER_SOCKET" ]]; then
    emit dockerServer permissionDenied ''; emit dockerRoot permissionDenied ''; emit dockerDisk permissionDenied ''
  else
    run dockerServer timeout -k 1 4 docker --host "unix://$DOCKER_SOCKET" version --format '{{json .Server}}'
    if docker_root=$(timeout -k 1 4 docker --host "unix://$DOCKER_SOCKET" info --format '{{.DockerRootDir}}' 2>/dev/null) && [[ "$docker_root" == /* ]]; then
      emit dockerRoot ok "$docker_root"
      run dockerDisk df -Pk "$docker_root"
    else emit dockerRoot failed ''; emit dockerDisk failed ''; fi
  fi
fi
printf 'FHENV\tEND\n'
