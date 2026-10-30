#!/bin/sh
# Deploy the keeper code to the router and restart the container. Never touches disk1/zte-keeper/keeper/data/.
#   ./deploy.sh                  tests first, then upload + restart (router: admin@192.168.6.1)
#   ./deploy.sh me@192.168.6.1   another SSH user / address
#   SKIP_TESTS=1 ./deploy.sh     skip pytest
# Needs SSH on the router (key login: README "Deploy"). Uses sftp + ssh only (both come with macOS).
set -eu
R=${1:-admin@192.168.6.1}
DST=disk1/zte-keeper/keeper
cd "$(dirname "$0")"
[ "${SKIP_TESTS:-}" = 1 ] || python3 -m pytest -q
B=$(mktemp)
trap 'rm -f "$B"' EXIT
{
    echo "-mkdir $DST/devices"         # "Unknown status" = the folder exists already (fine)
    echo "-mkdir $DST/devices/zte"
    for f in keeper/*.py keeper/start.sh keeper/keeper.sh; do echo "put $f $DST/"; done
    for f in keeper/devices/*.py; do echo "put $f $DST/devices/"; done
    for f in keeper/devices/zte/*.py; do echo "put $f $DST/devices/zte/"; done
} > "$B"
echo "uploading to $R:$DST ..."
sftp -q -b "$B" "$R"
echo "restarting the container ..."
ssh "$R" '/container stop [find name="zte-keeper"]; :delay 20s; /container start [find name="zte-keeper"]; :delay 5s; /log print where message~"zte-keeper" and message~"start \\(v2"'
echo "done - check http://192.168.6.250:7557/status"
