# zte-keeper - MikroTik RouterOS 7.25 container next to the ZTE MC889 (same container setup as AdGuard "agh").
# Before pasting: upload keeper.sh and start.sh into  disk1/zte-keeper/keeper/  (WinBox/WebFig -> Files).
# Edit keeperIp (a FREE address in the same subnet as the agh container), then paste this whole block into the terminal.
# Afterwards add the login env yourself (README step 4) and start the container.
# Syntax per the 7.25 manual: envs/mounts use list=, the container uses mountlists= and envlist(s)= - "envlist=" is
# written here because RouterOS accepts unambiguous prefixes, so it works whether the property is envlist or envlists.
{
:local keeperIp "192.168.6.250/24"
:local aghVeth "agh"
:local gw [/interface/veth get [find name=$aghVeth] gateway]
/interface/veth add name=zte-keeper address=$keeperIp gateway=$gw comment="zte-keeper"
# same network as agh: join its bridge if agh is a bridge port, otherwise masquerade the keeper towards the ZTE
:local p [/interface/bridge/port find interface=$aghVeth]
:if ([:len $p] > 0) do={
    /interface/bridge/port add bridge=[/interface/bridge/port get $p bridge] interface=zte-keeper comment="zte-keeper"
} else={
    /ip/firewall/nat add chain=srcnat src-address=[:pick $keeperIp 0 [:find $keeperIp "/"]] dst-address=192.168.6.3 action=masquerade comment="zte-keeper"
}
/container/envs add list=zte key=TZ value="Europe/Sofia"
/container/envs add list=zte key=ZTE_HOST value="192.168.6.3"
/container/envs add list=zte key=ANT_AUTO value="0"
/container/envs add list=zte key=ANT5 value="1"
/container/envs add list=zte key=ANT6 value="4"
/container/envs add list=zte key=INTERVAL value="10"
/container/envs add list=zte key=SAMPLE_EVERY value="60"
/container/envs add list=zte key=YIELD value="900"
/container/mounts add list=zte-keeper src=disk1/zte-keeper/keeper dst=/keeper
# alpine:3.20 comes from Docker Hub - the registry AdGuard was pulled from (default registry-url in 7.25 is lscr.io)
:if ([/container/config get registry-url] != "https://registry-1.docker.io") do={
    :put ("NOTE: registry-url is " . [/container/config get registry-url] . " - alpine needs https://registry-1.docker.io")
}
/container add name=zte-keeper remote-image=alpine:3.20 interface=zte-keeper root-dir=disk1/zte-keeper/root \
    mountlists=zte-keeper envlist=zte cmd="/bin/sh /keeper/start.sh" start-on-boot=yes logging=yes comment="zte-keeper"
:put "zte-keeper created - wait until /container print shows it 'stopped' (image extracted), add the login env, then start it"
}
