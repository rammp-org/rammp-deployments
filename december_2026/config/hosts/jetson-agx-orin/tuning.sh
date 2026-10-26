#!/bin/bash
# Host tuning for a Jetson AGX Orin Developer Kit (L4T R36) that runs the arm driver on one NIC and the cameras on
# another. Why each setting, and what it measured, is in README.md next to this file.
#
#   sudo ./tuning.sh install   persist everything (boot unit + NetworkManager hook + sysctl + modules), apply it now
#   sudo ./tuning.sh apply     apply the runtime settings once (what the unit and the hook run)
#        ./tuning.sh check     report each setting; exit 1 if any is off
#   sudo ./tuning.sh undo      remove the unit and the hook, and put the runtime settings back to the defaults
#
# NICs default to flatbot's cabling (config/robots/flatbot/network.yaml); override with ARM_IF= / CAM_IF=.
set -uo pipefail
ARM_IF=${ARM_IF:-enp3s0}  # Kinova Gen3 base, direct cable
CAM_IF=${CAM_IF:-eno1}    # camera network (nvethernet)
UNIT=/etc/systemd/system/rammp-host-tuning.service
HOOK=/etc/NetworkManager/dispatcher.d/60-rammp-host-tuning
BIN=/usr/local/sbin/rammp-host-tuning
SYSCTL=/etc/sysctl.d/60-ros-dds-buffers.conf
MODULES=/etc/modules-load.d/rammp-host-tuning.conf

arm_driver_up() { pgrep -f "kinova_gen3_node --ip" >/dev/null; }
is_up() { [ -n "$(ip link show dev "$1" up 2>/dev/null)" ]; }
# "<irq> <name>" for the camera NIC's queue interrupts vm0..vm4
cam_irqs() { awk -v p="$CAM_IF.vm" 'index($NF, p) == 1 && substr($NF, length(p) + 1) ~ /^[0-4]$/ { print $1 + 0, $NF }' /proc/interrupts; }
irqs_spread() {
  local irq name found=1
  while read -r irq name; do
    found=0
    [ "$(cat "/proc/irq/$irq/smp_affinity_list")" = $((4 + ${name##*vm})) ] || return 1
  done < <(cam_irqs)
  return $found
}

# Each step runs regardless of the others; one that fails says so, and apply exits 1. Steps 2 and 3 only stick
# once the NIC is open (lan743x attaches its PHY there, nvethernet requests its queue IRQs there), and both are reset
# on every open, so on a NIC that isn't up yet they wait for the NetworkManager hook.
apply() {
  local rc=0 irq name found=
  fail() { echo "FAIL  $*" >&2; rc=1; }
  # 1. No multicast out of the arm NIC: DDS discovery and data stay off the arm link. Doesn't need the link up.
  tc qdisc del dev "$ARM_IF" clsact 2>/dev/null
  { tc qdisc add dev "$ARM_IF" clsact &&
    tc filter add dev "$ARM_IF" egress prio 10 protocol ip flower dst_ip 224.0.0.0/4 action drop; } ||
    fail "multicast filter on $ARM_IF"
  # 2. EEE off on the arm link. Changing it renegotiates the link (~3 s down), so never under a running driver.
  if ! is_up "$ARM_IF"; then echo "EEE: $ARM_IF not up yet, left to the NetworkManager hook"
  elif ethtool --show-eee "$ARM_IF" | grep -q "EEE status: disabled"; then :
  elif arm_driver_up; then fail "EEE on $ARM_IF: arm driver running, not touching the link; stop it and re-run"
  else ethtool --set-eee "$ARM_IF" eee off || fail "EEE off on $ARM_IF"; fi
  # 3. Camera NIC queue interrupts vmN -> CPU 4+N, leaving CPU0 to the arm NIC (whose interrupts can't move).
  if ! is_up "$CAM_IF"; then echo "IRQs: $CAM_IF not up yet, left to the NetworkManager hook"
  else
    while read -r irq name; do
      found=1
      echo $((4 + ${name##*vm})) >"/proc/irq/$irq/smp_affinity_list" || fail "pinning $name (IRQ $irq)"
    done < <(cam_irqs)
    [ -n "$found" ] || fail "no $CAM_IF.vm0-4 IRQs in /proc/interrupts"
  fi
  return $rc
}

check() {
  local bad=0
  ok() { if eval "$2"; then echo "ok    $1"; else echo "FAIL  $1 -- fix: $3"; bad=1; fi; }
  ok "multicast dropped at $ARM_IF egress" "tc filter show dev $ARM_IF egress | grep -q 'dst_ip 224.0.0.0/4'" \
    "sudo $0 apply"
  ok "EEE off on $ARM_IF" "ethtool --show-eee $ARM_IF | grep -q 'EEE status: disabled'" "sudo $0 apply (arm driver stopped)"
  ok "$CAM_IF queue IRQs on CPUs 4-8" irqs_spread "sudo $0 apply"
  ok "rmem_max >= 32 MiB (DDS)" "[ \$(sysctl -n net.core.rmem_max) -ge 33554432 ]" "sudo $0 install"
  ok "CPU 11 isolated" "grep -qw isolcpus=11 /proc/cmdline && grep -qw rcu_nocbs=11 /proc/cmdline" \
    "add 'isolcpus=11 rcu_nocbs=11' to the default APPEND line of /boot/extlinux/extlinux.conf, reboot"
  ok "power mode MAXN" "nvpmodel -q 2>/dev/null | grep -q MAXN" "sudo nvpmodel -m 0"
  ok "applied at boot and on NIC up" "systemctl is-enabled -q rammp-host-tuning 2>/dev/null && [ -x $HOOK ]" \
    "sudo $0 install"
  return $bad
}

install_() {
  [ "$0" -ef "$BIN" ] || install -m 0755 "$0" "$BIN"
  [ -f "$SYSCTL" ] || printf 'net.core.rmem_max=33554432\nnet.core.rmem_default=16777216\n' >"$SYSCTL"
  sysctl -q -p "$SYSCTL"
  printf 'sch_ingress\ncls_flower\nact_gact\n' >"$MODULES"
  modprobe -a sch_ingress cls_flower act_gact
  # At boot: the multicast filter, before DDS starts. Wants=, not Requires=: a missing arm NIC delays the unit, it
  # doesn't skip it.
  cat >"$UNIT" <<EOF
[Unit]
Description=RAMMP host tuning (arm link and camera NIC), see rammp-deployments config/hosts/jetson-agx-orin
Wants=sys-subsystem-net-devices-$ARM_IF.device
After=sys-subsystem-net-devices-$ARM_IF.device

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=ARM_IF=$ARM_IF CAM_IF=$CAM_IF
ExecStart=$BIN apply

[Install]
WantedBy=multi-user.target
EOF
  # Every time NetworkManager brings either NIC up: EEE and the IRQs, which each open of the NIC resets.
  # NetworkManager logs the output, and a non-zero exit, to the journal.
  cat >"$HOOK" <<EOF
#!/bin/sh
# rammp-host-tuning: re-apply when the arm or camera NIC comes up. Installed by $BIN install.
case "\$1 \$2" in "$ARM_IF up"|"$CAM_IF up") ARM_IF=$ARM_IF CAM_IF=$CAM_IF exec $BIN apply ;; esac
EOF
  chmod 0755 "$HOOK"
  systemctl daemon-reload
  systemctl enable rammp-host-tuning.service
  systemctl restart rammp-host-tuning.service
  check
}

undo() {
  # The NICs the unit was installed with, not this shell's defaults.
  local ifs
  ifs=$(sed -n 's/^Environment=ARM_IF=\(\S*\) CAM_IF=\(\S*\)$/\1 \2/p' "$UNIT" 2>/dev/null)
  [ -n "$ifs" ] && read -r ARM_IF CAM_IF <<<"$ifs"
  systemctl disable --now rammp-host-tuning.service 2>/dev/null
  rm -f "$UNIT" "$HOOK" "$BIN" "$MODULES"
  systemctl daemon-reload
  tc qdisc del dev "$ARM_IF" clsact 2>/dev/null
  cam_irqs | while read -r irq _; do echo 0 >"/proc/irq/$irq/smp_affinity_list"; done
  # A live link can't take EEE back (lan743x: "EEE initialization failed"); a reboot restores it.
  echo "EEE left off on $ARM_IF until the next reboot"
  echo "left in place: $SYSCTL (DDS buffers)"
}

case "${1:-}" in
  install) install_ ;;
  apply) apply ;;
  check) check ;;
  undo) undo ;;
  *) sed -n 2,10p "$0"; exit 2 ;;
esac
