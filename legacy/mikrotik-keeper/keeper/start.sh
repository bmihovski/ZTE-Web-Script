#!/bin/sh
# container entry point: install python3 once (the container root is kept, so later starts skip the download)
until command -v python3 >/dev/null; do
    apk add --no-cache python3 tzdata || { echo "apk add failed - retry in 30 s"; sleep 30; }
done
cd / && exec python3 -u -m keeper            # /keeper = this package (container mount)
# v1 (polls the ZTE web page every 10 s, no TR-069): exec sh /keeper/keeper.sh  - needs curl + openssl
