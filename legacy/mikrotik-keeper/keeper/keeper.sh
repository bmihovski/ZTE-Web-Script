#!/bin/sh
# zte-keeper - runs in a MikroTik container next to the ZTE MC889.
#  1. puts the antenna setup back after every ZTE reboot (the ZTE resets directional antennas to omni when it boots)
#  2. logs 5G cell switches, "no 5G" periods and rx dropouts (-140 dBm) around the clock -> CSV files in $DATA
# Settings (container env): ZTE_HOST, ZTE_PASS_HASH (or ZTE_PASS), ANT_AUTO (0 = auto-select off), ANT5 / ANT6 (1 omni, 4 directional),
#                           INTERVAL (s between reads), SAMPLE_EVERY (s between samples.csv lines), DATA (log folder),
#                           YIELD (s to stay logged out after someone else logs in on the router page; the ZTE allows one session)
set -u
ZTE=${ZTE_HOST:-192.168.6.3}
AUTO=${ANT_AUTO:-0}; ANT5=${ANT5:-1}; ANT6=${ANT6:-4}
INTERVAL=${INTERVAL:-10}; SAMPLE_EVERY=${SAMPLE_EVERY:-60}
DATA=${DATA:-/keeper/data}; YIELD=${YIELD:-900}
J=/tmp/zte.cookies
MAX_BYTES=2000000          # ponytail: each file (CSVs + keeper.log) keeps one old copy (.1) past ~2 MB -> at most ~12 MB in total
mkdir -p "$DATA"

log() { echo "$(date '+%F %T') $*"; rotate "$DATA/keeper.log"; echo "$(date '+%F %T'),$*" >> "$DATA/keeper.log"; }
sha() { printf %s "$1" | openssl dgst -sha256 | awk '{print toupper($NF)}'; }       # same as the router page: upper-case hex
# the ZTE answers flat JSON {"k":"v",...} - pull one value without jq (the fields read here never contain commas)
val() { printf %s "$1" | tr ',' '\n' | sed -n "s/^[{ ]*\"$2\": *\"\(.*\)\"[} ]*\$/\1/p" | head -n 1; }
get() { curl -s -m 8 -b "$J" -c "$J" -H "Referer: http://$ZTE/index.html" \
             "http://$ZTE/goform/goform_get_cmd_process?isTest=false&multi_data=1&cmd=$1&_=$(date +%s)"; }
post() {   # $1 = form body without AD
    v=$(get wa_inner_version,cr_version,RD)
    ad=$(sha "$(sha "$(val "$v" wa_inner_version)$(val "$v" cr_version)")$(val "$v" RD)")
    curl -s -m 8 -b "$J" -c "$J" -H "Referer: http://$ZTE/index.html" --data "isTest=false&$1&AD=$ad" \
         "http://$ZTE/goform/goform_set_cmd_process"
}
login() {
    h=${ZTE_PASS_HASH:-}
    [ -z "$h" ] && [ -n "${ZTE_PASS:-}" ] && h=$(sha "$ZTE_PASS")
    [ -z "$h" ] && { log "no ZTE_PASS_HASH or ZTE_PASS in the container env"; return 2; }
    h=$(printf %s "$h" | tr a-f A-F)
    ld=$(val "$(get LD)" LD)
    r=$(val "$(post "goformId=LOGIN&password=$(sha "$h$ld")")" result)
    [ "$r" = 0 ] && return 0
    [ "$r" = 3 ] && return 3     # wrong password
    return 1
}
apply() {
    ok=1
    [ "$(val "$(post "goformId=WAN_ANT_SWITCH_SET&ant_switch_enable=$AUTO")" result)" = success ] || ok=0
    if [ "$AUTO" = 0 ]; then
        [ "$(val "$(post "goformId=BSP_ANTENNA_STATE_SET&antenna_name=5&state=$ANT5")" result)" = success ] || ok=0
        [ "$(val "$(post "goformId=BSP_ANTENNA_STATE_SET&antenna_name=6&state=$ANT6")" result)" = success ] || ok=0
    fi
    [ $ok = 1 ]
}
rotate() {   # past MAX_BYTES: keep one old copy (.1); a CSV keeps its header line
    [ -f "$1" ] && [ "$(wc -c < "$1")" -gt $MAX_BYTES ] || return 0
    mv "$1" "$1.1"; case "$1" in *.csv) head -n 1 "$1.1" > "$1" ;; esac; return 0
}
num() { case "$1" in ''|-20.0|-3276.8) echo "" ;; *) echo "$1" ;; esac; }     # router "no value" markers -> empty

[ -f "$DATA/switches.csv" ] || echo "time,uptime_s,event,from,to,sinr_before,lte_cell,lte_sinr" > "$DATA/switches.csv"
[ -f "$DATA/samples.csv" ]  || echo "time,uptime_s,cell,sinr,rsrp,rssi,rx0,rx1,lte_cell,lte_sinr,lte_rssi,network,nr_lock" > "$DATA/samples.csv"
log "start: ZTE $ZTE, auto=$AUTO ant5=$ANT5 ant6=$ANT6, every ${INTERVAL}s"

F=loginfo,ppp_status,system_uptime,network_type,nr5g_pci,nr5g_action_channel,Z5g_SINR,Z5g_rsrp,Z5g_rssi,5g_rx0_rsrp,5g_rx1_rsrp,lte_pci,wan_active_channel,lte_snr,lte_rssi,nr5g_cell_lock
prev_up=0; applied=0; prev_cell=""; prev_sinr=""; dead=""; last_sample=0; next_login=0; had=0; yield_until=0
while :; do
    now=$(date +%s); s=$(get "$F")
    if [ "$(val "$s" loginfo)" != ok ]; then
        # ppp_status is readable without a session: empty reply or not connected = ZTE down / booting -> nobody else holds the session
        if [ -z "$s" ] || [ "$(val "$s" ppp_status)" != ppp_connected ]; then yield_until=0
        # lost the session with the setup already applied = someone logged in on the router page -> leave them alone for YIELD s
        elif [ $had = 1 ] && [ $applied = 1 ]; then yield_until=$((now + YIELD)); log "session taken by the router web page - pausing ${YIELD}s"
        fi
        had=0
        if [ "$now" -ge "$next_login" ] && [ "$now" -ge "$yield_until" ]; then
            login; rc=$?
            case $rc in
                0) log "logged in" ;;
                3) log "login refused: wrong password hash - waiting 10 min"; next_login=$((now + 600)) ;;
                2) next_login=$((now + 600)) ;;
                *) log "login failed (router busy or booting)"; next_login=$((now + 60)) ;;
            esac
        fi
        sleep "$INTERVAL"; continue
    fi
    had=1

    up=$(val "$s" system_uptime); case "$up" in ''|*[!0-9]*) up=0 ;; esac
    if [ "$up" -lt "$prev_up" ]; then applied=0; log "ZTE reboot detected (uptime ${up}s)"; fi
    prev_up=$up
    # first run or after a reboot: wait until the ZTE has been up 60 s, then put the antenna setup back
    if [ $applied = 0 ] && [ "$up" -ge 60 ]; then
        if apply; then applied=1; log "applied: auto=$AUTO ant5=$ANT5 ant6=$ANT6"; else log "apply failed - retrying"; fi
    fi

    nt=$(val "$s" network_type); pci=$(val "$s" nr5g_pci); ch=$(val "$s" nr5g_action_channel)
    cell=no5G
    case "$nt" in *ENDC*|*5G*|*NR*) [ -n "$pci" ] && [ -n "$ch" ] && cell="$ch/$(printf %d "0x$pci" 2>/dev/null)" ;; esac
    sinr=$(num "$(val "$s" Z5g_SINR)"); [ "$cell" = no5G ] && sinr=""
    lpci=$(val "$s" lte_pci); lte_cell=""; [ -n "$lpci" ] && lte_cell="$(val "$s" wan_active_channel)/$(printf %d "0x$lpci" 2>/dev/null)"
    lte_sinr=$(val "$s" lte_snr); t=$(date '+%F %T')

    if [ -n "$prev_cell" ] && [ "$cell" != "$prev_cell" ]; then
        rotate "$DATA/switches.csv"
        echo "$t,$up,switch,$prev_cell,$cell,$prev_sinr,$lte_cell,$lte_sinr" >> "$DATA/switches.csv"
        log "5G $prev_cell -> $cell"
    fi
    rx0=$(val "$s" 5g_rx0_rsrp); rx1=$(val "$s" 5g_rx1_rsrp); d=""
    if [ "$cell" != no5G ]; then [ "$rx0" = -140 ] && d=rx0; [ "$rx1" = -140 ] && d="${d:+$d+}rx1"; fi
    if [ -n "$d" ] && [ -z "$dead" ]; then echo "$t,$up,rx_dropout,$cell,$d,$sinr,$lte_cell,$lte_sinr" >> "$DATA/switches.csv"; fi
    dead=$d
    if [ $((now - last_sample)) -ge "$SAMPLE_EVERY" ]; then
        rotate "$DATA/samples.csv"
        echo "$t,$up,$cell,$sinr,$(num "$(val "$s" Z5g_rsrp)"),$(val "$s" Z5g_rssi),$rx0,$rx1,$lte_cell,$lte_sinr,$(val "$s" lte_rssi),$nt,$(val "$s" nr5g_cell_lock)" >> "$DATA/samples.csv"
        last_sample=$now
    fi
    prev_cell=$cell; [ -n "$sinr" ] && prev_sinr=$sinr
    sleep "$INTERVAL"
done
