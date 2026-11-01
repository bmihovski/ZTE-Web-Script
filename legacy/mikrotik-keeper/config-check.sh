#!/bin/sh
# Print the router config the keeper / SQM depend on. No secrets: env values with PASS / TOKEN / HASH / SECRET print as <hidden>
# (never use `/container print detail` here - its env-current shows every env value).
#   ./config-check.sh [admin@192.168.6.1]      -> prints and writes config-<date-time>.txt here
set -eu
R=${1:-admin@192.168.6.1}
cd "$(dirname "$0")"
OUT="config-$(date +%Y%m%d-%H%M%S).txt"
ssh "$R" '
:put "== system"; /system resource print
:put "== cpu cores"; /system resource cpu print
:put "== cpu frequency setting"; /system routerboard settings print
:put "== bridge settings"; /interface bridge settings print
:put "== bridge ports"; /interface bridge port print
:put "== ip addresses"; /ip address print
:put "== ip routes"; /ip route print
:put "== dhcp client"; /ip dhcp-client print detail
:put "== dhcp server"; /ip dhcp-server print; /ip dhcp-server network print
:put "== firewall filter"; /ip firewall filter print
:put "== firewall nat"; /ip firewall nat print
:put "== firewall mangle"; /ip firewall mangle print
:put "== firewall raw"; /ip firewall raw print
:put "== interfaces"; /interface print
:put "== fasttrack rules"; /ip firewall filter print where action=fasttrack-connection
:put "== queue type cake"; /queue type print detail where name=cake
:put "== simple queues (order = match order)"; /queue simple print detail
:put "== simple queue stats"; /queue simple print stats
:put "== interface queues"; /queue interface print
:put "== sqm user"; /user print detail where name=keeper-sqm
:put "== sqm group"; /user group print detail where name=sqm
:put "== web services"; /ip service print where name~"www"
:put "== container"; /container print proplist=name,tag,envlists,mountlists,cmd,memory-current,cpu-usage where name=zte-keeper
:put "== container env keys"; /container envs print proplist=list,key
:put "== container env values (secrets hidden)"
:foreach e in=[/container envs find] do={ :local k [/container envs get $e key]; :local l [/container envs get $e list]; :if ($k~"PASS|TOKEN|HASH|SECRET") do={ :put ($l . " " . $k . "=<hidden>") } else={ :put ($l . " " . $k . "=" . [/container envs get $e value]) } }
' | tee "$OUT"
echo "saved $OUT"
