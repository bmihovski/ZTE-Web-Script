#!/bin/sh
# Per-core CPU load of the router, once a second, while you test (Mac -> SSH, key login as for deploy.sh).
#   ./cpu.sh                 120 s, prints and writes cpu-<date-time>.log here
#   ./cpu.sh 60 me@192.168.6.1
set -eu
N=${1:-120}; R=${2:-admin@192.168.6.1}
cd "$(dirname "$0")"
LOG="cpu-$(date +%Y%m%d-%H%M%S).log"
ssh "$R" ":for i from=1 to=$N do={ :local s \"\"; :foreach c in=[/system resource cpu find] do={ :set s (\$s . [/system resource cpu get \$c load] . \"% \") }; :put ([/system clock get time] . \"  \" . \$s . \" total \" . [/system resource get cpu-load] . \"%\"); :delay 1s }" | tee "$LOG"
echo "saved $LOG"
