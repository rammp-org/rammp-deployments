#!/bin/bash
# NTP server for the MIB: chrony on this host serves time to the MIB's network, so the MIB's SNTP client stamps its
# odometry in this host's clock. Why, and how to see the MIB syncing, is in README.md next to this file.
#
#   sudo ./time_server.sh install   install chrony (it replaces systemd-timesyncd), serve MIB_NET, restart chrony
#        ./time_server.sh check     report each piece; exit 1 if any is off
#   sudo ./time_server.sh undo      stop serving MIB_NET (chrony stays, as this host's NTP client)
#
# The MIB is the DHCP server at 192.168.4.1/24 and asks for <NTP_NAME>.local over mDNS: both are RAMMP-mib-fw
# MIBconfig.hpp (ethernet_ip, ntp_server). Override with MIB_NET= / NTP_NAME= if the firmware changes.
set -uo pipefail
MIB_NET=${MIB_NET:-192.168.4.0/24}
NTP_NAME=${NTP_NAME:-snorlax}
CONF=/etc/chrony/conf.d/rammp-mib.conf

check() {
  local bad=0
  ok() { if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1 -- fix: $3"; bad=1; fi; }
  ok "chrony running" "systemctl is-active -q chrony" "sudo $0 install"
  ok "systemd-timesyncd off (it can't serve)" "! systemctl is-active -q systemd-timesyncd" "sudo $0 install"
  # chronyd -p prints the config chrony would run with, conf.d included.
  ok "chrony serves $MIB_NET, from its own clock when offline" \
    "chronyd -p 2>/dev/null | grep -qx 'allow $MIB_NET' && chronyd -p 2>/dev/null | grep -qx 'local stratum 10'" "sudo $0 install"
  ok "avahi-daemon running (answers $NTP_NAME.local)" "systemctl is-active -q avahi-daemon" \
    "sudo systemctl enable --now avahi-daemon"
  ok "hostname is $NTP_NAME (the MIB asks for $NTP_NAME.local)" "[ \"\$(hostname)\" = $NTP_NAME ]" \
    "sudo hostnamectl set-hostname $NTP_NAME, or change ntp_server in RAMMP-mib-fw MIBconfig.hpp"
  return $bad
}

install_() {
  if ! command -v chronyd >/dev/null; then
    { apt-get update && apt-get install -y chrony; } || { echo "FAIL  installing chrony" >&2; return 1; }
  fi
  # The chrony package removes systemd-timesyncd on Ubuntu; this covers a host where it was left enabled anyway.
  systemctl disable --now systemd-timesyncd 2>/dev/null
  mkdir -p "$(dirname $CONF)"
  cat >"$CONF" <<EOF
# rammp: serve time to the MIB's network. Written by time_server.sh install (rammp-deployments
# config/hosts/jetson-agx-orin).
allow $MIB_NET
# Keep serving from this host's own clock when no upstream server is reachable: the robot is often offline, and
# what odometry needs is the MIB agreeing with this host, not with UTC.
local stratum 10
EOF
  systemctl enable chrony
  systemctl restart chrony
  check
}

undo() {
  rm -f "$CONF"
  systemctl restart chrony
  echo "left in place: chrony (this host's NTP client)"
}

case "${1:-}" in
  install) install_ ;;
  check) check ;;
  undo) undo ;;
  *) sed -n 2,10p "$0"; exit 2 ;;
esac
