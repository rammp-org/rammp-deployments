#!/bin/bash
# Host tuning for a Jetson AGX Orin Developer Kit (L4T R36) that runs the arm driver on one NIC and the cameras on
# another. Why each setting, and what it measured, is in README.md next to this file.
#
#   sudo ./tuning.sh install   persist everything (systemd unit + sysctl + modules) and apply it now
#   sudo ./tuning.sh apply     apply the runtime settings once (what the unit runs at boot)
#        ./tuning.sh check     report each setting; exit 1 if any is off
#   sudo ./tuning.sh undo      remove the unit and put the runtime settings back to the defaults
#
# NICs default to flatbot's cabling (config/robots/flatbot/network.yaml); override with ARM_IF= / CAM_IF=.
set -uo pipefail
ARM_IF=${ARM_IF:-enp3s0}  # Kinova Gen3 base, direct cable
CAM_IF=${CAM_IF:-eno1}    # camera network (nvethernet)
UNIT=/etc/systemd/system/rammp-host-tuning.service
BIN=/usr/local/sbin/rammp-host-tuning
SYSCTL=/etc/sysctl.d/60-ros-dds-buffers.conf
MODULES=/etc/modules-load.d/rammp-host-tuning.conf

arm_driver_up() { pgrep -f "kinova_gen3_node --ip" >/dev/null; }
# "<irq> <name>" for the camera NIC's queue interrupts vm0..vm4
cam_irqs() { grep -E " $CAM_IF\.vm[0-4]\$" /proc/interrupts | awk '{print $1, $NF}' | tr -d ':'; }
irqs_spread() {
  local irq name found=1
  while read -r irq name; do
    found=0
    [ "$(cat "/proc/irq/$irq/smp_affinity_list")" = $((4 + ${name##*vm})) ] || return 1
  done < <(cam_irqs)
  return $found
}

apply() {
  # 1. No multicast out of the arm NIC: DDS discovery and data stay off the arm link.
  tc qdisc del dev "$ARM_IF" clsact 2>/dev/null
  tc qdisc add dev "$ARM_IF" clsact &&
    tc filter add dev "$ARM_IF" egress prio 10 protocol ip flower dst_ip 224.0.0.0/4 action drop
  # 2. EEE off on the arm link. Changing it renegotiates the link (~3 s down), so never under a running driver.
  if ethtool --show-eee "$ARM_IF" | grep -q "EEE status: enabled"; then
    if arm_driver_up; then echo "EEE: arm driver running, not touching the link; stop it and re-run" >&2
    else ethtool --set-eee "$ARM_IF" eee off; fi
  fi
  # 3. Camera NIC queue interrupts vmN -> CPU 4+N, leaving CPU0 to the arm NIC (whose interrupts can't move).
  cam_irqs | while read -r irq name; do echo $((4 + ${name##*vm})) >"/proc/irq/$irq/smp_affinity_list"; done
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
  ok "applied at boot" "systemctl is-enabled -q rammp-host-tuning 2>/dev/null" "sudo $0 install"
  return $bad
}

install_() {
  install -m 0755 "$0" "$BIN"
  [ -f "$SYSCTL" ] || printf 'net.core.rmem_max=33554432\nnet.core.rmem_default=16777216\n' >"$SYSCTL"
  sysctl -q -p "$SYSCTL"
  printf 'sch_ingress\ncls_flower\nact_gact\n' >"$MODULES"
  modprobe -a sch_ingress cls_flower act_gact
  cat >"$UNIT" <<EOF
[Unit]
Description=RAMMP host tuning (arm link and camera NIC), see rammp-deployments config/hosts/jetson-agx-orin
Requires=sys-subsystem-net-devices-$ARM_IF.device sys-subsystem-net-devices-$CAM_IF.device
After=sys-subsystem-net-devices-$ARM_IF.device sys-subsystem-net-devices-$CAM_IF.device

[Service]
Type=oneshot
RemainAfterExit=yes
Environment=ARM_IF=$ARM_IF CAM_IF=$CAM_IF
ExecStart=$BIN apply

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now rammp-host-tuning.service
  check
}

undo() {
  systemctl disable --now rammp-host-tuning.service 2>/dev/null
  rm -f "$UNIT" "$BIN" "$MODULES"
  systemctl daemon-reload
  tc qdisc del dev "$ARM_IF" clsact 2>/dev/null
  cam_irqs | while read -r irq _; do echo 0 >"/proc/irq/$irq/smp_affinity_list"; done
  if arm_driver_up; then echo "EEE left off: arm driver running" >&2; else ethtool --set-eee "$ARM_IF" eee on; fi
  echo "left in place: $SYSCTL (DDS buffers)"
}

case "${1:-}" in
  install) install_ ;;
  apply) apply ;;
  check) check ;;
  undo) undo ;;
  *) sed -n 2,10p "$0"; exit 2 ;;
esac
