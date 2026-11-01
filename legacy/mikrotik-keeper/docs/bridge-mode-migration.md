# RB450 bridge-mode migration — MC889 → bridge, NAT + SQM on the RB450

Goal: make ether5 a real routed WAN so SQM/CAKE runs on the WAN path (cleaner, less CPU),
and the MC889 becomes a pure modem with a dedicated management IP for TR-069/antennas/locks.

Do this at the **console or Winbox (MAC-telnet)**, NOT over SSH through the link you are rebuilding.
Window: ~30–60 min with the internet down in bursts. NOT before the summer trip.

## Current state (config-20261031-101955)
- RB450 LAN: bridge `192.168.6.1/24`; ports in bridge: ether1-4, ether5, agh, zte-keeper.
- Default route `0.0.0.0/0 → 192.168.6.3` (the MC889 = gateway/NAT today).
- AdGuard (agh) = DNS + DHCP for 192.168.6.0/24 (hands gateway 192.168.6.3 today).
- Firewall filter/nat/mangle: all **disabled** (defconf rules present).
- ether5 DHCP client "yettel": disabled, add-default-route=yes, distance 1.
- SQM: simple queue `sqm` target 192.168.6.0/24, queue cake/fqc, 30M/150M; SQM_IFACE=ether5.
- Keeper env ZTE_HOST=192.168.6.3; MC889 ACS URL → 192.168.6.250:7557.
- WireGuard Oreshak + DNS-check mangle: disabled (ignore for now; fix gateway later if re-enabled).

## Target
- ether5 = WAN: carrier IP via DHCP + static `192.168.5.2/24` for MC889 management.
- bridge = LAN 192.168.6.1/24 (ether1-3, agh, zte-keeper). ether4 (wan-bol-bg) stays as-is/unused.
- RB450 does NAT (masquerade on WAN) + firewall. Routes LAN ↔ management (no NAT between them).
- MC889 in bridge mode, management at 192.168.5.1 (CONFIRM — see below).
- Keeper stays at 192.168.6.250; ZTE_HOST → 192.168.5.1; ACS URL stays 192.168.6.250:7557
  if MC889 mgmt routes to LAN, else DST-NAT (see step 7).

## 0. CONFIRM ON THE MC889 FIRST (decides the addresses)
1. In the MC889 UI, switch to **bridge mode**. Note:
   - the management IP it exposes in bridge mode (expected 192.168.5.1 — may be 192.168.0.1; use whatever it shows),
   - that it still serves its web UI + TR-069 on that IP,
   - that it hands a WAN/carrier IP by DHCP to the Ethernet port.
2. If the mgmt subnet is not 192.168.5.0/24, substitute your real numbers everywhere below.
> Do NOT proceed until you know the MC889's bridge-mode mgmt IP.

## 1. Backup (console/Winbox)
    /system backup save name=pre-bridge
    /export file=pre-bridge

## 2. Interface lists
    /interface list add name=LAN
    /interface list add name=WAN
    /interface list member add list=LAN interface=bridge
    /interface list member add list=WAN interface=ether5
(If LAN/WAN already exist, just add the members; the defconf filter rules reference these lists.)

## 3. ether5 out of the bridge → WAN
    /interface bridge port remove [find interface=ether5]
    /ip dhcp-client enable [find interface=ether5]        ;# carrier IP + default route (dist 1)
    /ip address add address=192.168.5.2/24 interface=ether5 comment="MC889 mgmt"
    /ip route remove [find dst-address=0.0.0.0/0 gateway=192.168.6.3]   ;# drop the old static default

## 4. Firewall + NAT (re-enable defconf, keep fasttrack OFF for SQM)
    /ip firewall filter enable [find]        ;# re-enable all defconf filter rules
    /ip firewall filter disable [find action=fasttrack-connection]   ;# SQM needs fasttrack OFF
    /ip firewall nat enable [find]           ;# masquerade out WAN
Check the input chain keeps the router reachable from LAN only:
    /ip firewall filter print
(defconf rule "drop all not coming from LAN" on input protects the router from the WAN.)
Allow the keeper to reach the MC889 mgmt subnet (LAN → 192.168.5.0/24 is routed, not NAT'd — no rule needed
unless you later add a default-drop forward; with defconf's default-accept forward it just works).

## 5. AdGuard DHCP — point clients at the RB450
In AdGuard Home (DHCP settings): set the **gateway/router option to 192.168.6.1** (was 192.168.6.3),
DNS stays AdGuard (192.168.6.1 or the AGH address). Renew a client lease and confirm it gets .6.1 as gateway.

## 6. Keeper env
Change ZTE_HOST to the MC889's bridge-mode mgmt IP and redeploy:
    /container envs set [find list=zte key=ZTE_HOST] value=192.168.5.1
    # then restart the container (or ./deploy.sh restarts it)
(If SQM_URL used 192.168.6.1 that is still the RB450 — unchanged.)

## 7. TR-069 reachability (MC889 → keeper)
- If, from the MC889 mgmt (192.168.5.1), you can reach 192.168.6.250 (RB450 routes 192.168.5.0 ↔ 192.168.6.0):
  leave the MC889 ACS URL = http://192.168.6.250:7557/.
- If the MC889 mgmt only talks to its local subnet, add a shim and point the ACS URL at 192.168.5.2:
    /ip firewall nat add chain=dstnat dst-address=192.168.5.2 protocol=tcp dst-port=7557 \
        action=dst-nat to-addresses=192.168.6.250 to-ports=7557 comment="MC889 TR-069 to keeper"
  then set the MC889 ACS URL = http://192.168.5.2:7557/.

## 8. SQM
The simple queue `sqm` (target 192.168.6.0/24, cake/fqc) still matches LAN traffic on the routed path — keep it.
Now that ether5 is routed (not a bridge port), the old bridge shaping hacks are irrelevant:
you may re-enable hardware offload on the LAN ether ports. SQM_IFACE=ether5 is unchanged.

## Verify
    /ip address print                         ;# ether5 has a carrier IP + 192.168.5.2
    /ip route print                           ;# active default via ether5 (DHCP)
    /ip dhcp-client print detail              ;# ether5 bound
- A LAN device: gets gateway 192.168.6.1, has internet, DNS resolves (AdGuard).
- MC889 UI reachable at 192.168.5.1 from a LAN host.
- Keeper /status: MC889 informs (TR-069), antennas apply (ZTE_HOST), config.sqm shaping, no errors.
- Speedtest ~147/29 with low bufferbloat; POST /checks?name=all → Telegram.

## Rollback (if anything breaks)
Fastest: console
    /system backup load name=pre-bridge        ;# reboots to the pre-migration config
Manual:
    /ip firewall filter disable [find]; /ip firewall nat disable [find]
    /ip dhcp-client disable [find interface=ether5]
    /ip address remove [find interface=ether5]
    /interface bridge port add bridge=bridge interface=ether5
    /ip route add dst-address=0.0.0.0/0 gateway=192.168.6.3 distance=3
    # MC889 back to router mode (its UI); AdGuard DHCP gateway back to 192.168.6.3; ZTE_HOST back to 192.168.6.3
    # MC889 ACS URL back to http://192.168.6.250:7557/ ; remove the DST-NAT shim if added

## Unknowns to confirm during the window
- MC889 bridge-mode mgmt IP + that it keeps web/TR-069 there.
- Carrier IP actually handed to ether5 by DHCP (CGNAT is fine).
- Whether MC889 mgmt routes to the keeper (step 7 path A vs B).
- AdGuard DHCP gateway change applied.
- VLAN: not used (MC889 has none) — WAN + mgmt are two untagged L3 subnets on ether5.
