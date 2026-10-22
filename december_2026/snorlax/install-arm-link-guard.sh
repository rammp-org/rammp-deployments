#!/bin/bash
# Install and start arm-link-guard.service (see the unit for why). Idempotent. Run with sudo on snorlax.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
modprobe sch_ingress cls_flower act_gact
install -m 0644 "$here/arm-link-guard.service" /etc/systemd/system/arm-link-guard.service
printf 'sch_ingress\ncls_flower\nact_gact\n' > /etc/modules-load.d/arm-link-guard.conf
systemctl daemon-reload
systemctl enable --now arm-link-guard.service
tc -s filter show dev enp3s0 egress
