#!/bin/bash
# NTP server for the MIB: chrony on this host serves the MIB's network, so the MIB stamps its odometry in this host's
# clock. README.md next to this file has why, and how to see the MIB syncing.
#
#   sudo ./time_server.sh install   install chrony (it replaces systemd-timesyncd) and serve MIB_NET
#        ./time_server.sh check     report each piece; exit 1 if any is off
#   sudo ./time_server.sh undo      stop serving MIB_NET (chrony stays, as this host's NTP client)
#
# The MIB is the DHCP server at 192.168.4.1/24 and asks for <NTP_NAME>.local over mDNS, both set in RAMMP-mib-fw's
# MIBconfig.hpp (ethernet_ip, ntp_server). Override with MIB_NET= / NTP_NAME= if the firmware changes.
set -uo pipefail
MIB_NET=${MIB_NET:-192.168.4.0/24}
NTP_NAME=${NTP_NAME:-snorlax}
CONF=/etc/chrony/conf.d/rammp-mib.conf

# A line of the config chrony would run with (chronyd -p), conf.d included.
chrony_has() { chronyd -p 2>/dev/null | grep -qx "$1"; }
# The name avahi announces, not the hostname: after a name conflict avahi renames itself (snorlax-2.local) and keeps it.
avahi_name() { busctl call org.freedesktop.Avahi / org.freedesktop.Avahi.Server GetHostNameFqdn 2>/dev/null; }

check() {
  local bad=0
  ok() { if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1 -- fix: $3"; bad=1; fi; }
  ok "chrony running, and enabled at boot" "systemctl is-active -q chrony && systemctl is-enabled -q chrony" \
    "sudo $0 install"
  ok "systemd-timesyncd off (it can't serve)" "! systemctl is-active -q systemd-timesyncd" "sudo $0 install"
  ok "chrony serves $MIB_NET, from its own clock when offline" \
    "chrony_has 'allow $MIB_NET' && chrony_has 'local stratum 10'" "sudo $0 install"
  ok "chrony slews at most 500 ppm (CLOCK_MONOTONIC's rate)" "chrony_has 'maxslewrate 500'" "sudo $0 install"
  ok "avahi-daemon running (answers $NTP_NAME.local)" "systemctl is-active -q avahi-daemon" \
    "sudo systemctl enable --now avahi-daemon"
  ok "avahi announces $NTP_NAME.local (the name the MIB asks for)" "[ \"\$(avahi_name)\" = 's \"$NTP_NAME.local\"' ]" \
    "sudo hostnamectl set-hostname $NTP_NAME, or after a name conflict sudo systemctl restart avahi-daemon"
  return $bad
}

install_() {
  if ! command -v chronyd >/dev/null; then
    { apt-get update && apt-get install -y chrony; } || { echo "FAIL  installing chrony" >&2; return 1; }
  fi
  # The chrony package removes systemd-timesyncd on Ubuntu; this covers a host where it was left enabled anyway.
  systemctl disable --now systemd-timesyncd 2>/dev/null
  mkdir -p "$(dirname $CONF)"
  cat >"$CONF.new" <<EOF
# rammp: serve time to the MIB's network. Written by time_server.sh install (rammp-deployments
# config/hosts/jetson-agx-orin).
allow $MIB_NET
# Keep serving from this host's own clock when no upstream server is reachable: the robot is often offline, and
# what odometry needs is the MIB agreeing with this host, not with UTC.
local stratum 10
# Linux applies a slew to CLOCK_MONOTONIC's rate too. chrony's default cap (83333 ppm) would run the arm driver's
# 1 kHz loop up to 8% off while it corrects a large offset, e.g. after a reconnect; 500 ppm is the kernel PLL's cap
# under systemd-timesyncd.
maxslewrate 500
EOF
  # A restart resets makestep's count (step if > 1 s, first 3 updates), so restart only when the drop-in changed.
  if cmp -s "$CONF.new" "$CONF"; then
    rm "$CONF.new"
  else
    mv "$CONF.new" "$CONF"
    systemctl restart chrony
  fi
  systemctl enable --now chrony
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
