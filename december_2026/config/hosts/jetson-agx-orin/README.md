# Jetson AGX Orin Developer Kit

Host tuning for an AGX Orin dev kit (L4T R36) that runs the Kinova arm driver on one NIC and the OAK cameras on the
other: flatbot's `snorlax`. Everything here was measured on that Jetson on 2026-10-22 with the 1 kHz arm loop running
under the full perception load (rammp-deployments#120).

```bash
sudo december_2026/config/hosts/jetson-agx-orin/tuning.sh install   # once per flash: persists and applies
december_2026/config/hosts/jetson-agx-orin/tuning.sh check           # any time: ok/FAIL per setting, with the fix
```

`install` copies the script to `/usr/local/sbin/rammp-host-tuning` and installs two triggers for `apply`:

- `rammp-host-tuning.service` runs at boot, once the arm NIC exists. It sets the multicast filter, which doesn't need
  the link up. The sysctl is in `/etc/sysctl.d` and needs neither.
- A NetworkManager dispatcher hook (`/etc/NetworkManager/dispatcher.d/60-rammp-host-tuning`) runs `apply` each time
  NetworkManager brings either NIC up. EEE and the camera IRQs can only be set on an open NIC, and every open resets
  them. At boot the NICs open after the unit has run, so the hook is what sets them. `nmcli con up` and
  `nmcli dev disconnect` don't close the NIC (snorlax, 2026-10-26), so they reset nothing; the hook runs anyway.

`apply` logs each step that fails and exits 1. The others still run. Its output is in
`journalctl -u rammp-host-tuning -u NetworkManager-dispatcher`. `undo` removes it all except the DDS buffer sysctl,
and EEE stays off until the next reboot: a live link refuses to turn it back on.
Default NICs follow flatbot's cabling (`config/robots/flatbot/network.yaml`); set `ARM_IF=` / `CAM_IF=` for another
robot.

| Setting                                                       | Why                                                                                                                                                                                                                                                                         | Measured                                                                                                                      |
| ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Drop IPv4 multicast leaving the arm NIC (tc clsact + flower)  | Cyclone DDS picks the arm NIC (`autodetermine`) and multicasts topic data on it. The NIC (lan743x) has one TX queue, so each camera frame delays the arm's packets. Local subscribers are unaffected. This drop is a backstop: the real fix is pinning Cyclone's interface. | 3.8 MB/s of DDS on the arm link: driver overruns ~0.05/s → ~14/s, 3–4 ms Kortex gaps. With the filter: 38 MB/s had no effect. |
| EEE off on the arm NIC                                        | Waking the link from low-power idle adds latency to every 1 kHz packet.                                                                                                                                                                                                     | Kortex round trip p50 372 → 355 µs.                                                                                           |
| Camera NIC (`eno1`, nvethernet) queue IRQs vm0–vm4 → CPUs 4–8 | The arm NIC's interrupts are fixed on CPU0 (they come through the PCIe controller's MSI and can't be moved), and `eno1`'s default to CPU0 too.                                                                                                                              | Kortex round trip max 1.28 → 1.16 ms.                                                                                         |
| `net.core.rmem_max` 32 MiB, `rmem_default` 16 MiB             | DDS drops large messages (images) with the 208 KB default buffers.                                                                                                                                                                                                          | 20–40% of large messages lost before.                                                                                         |
| Checked only: `isolcpus=11 rcu_nocbs=11`, MAXN                | The arm driver's RT loop runs on CPU 11 (`--cpu 11`). Edit `/boot/extlinux/extlinux.conf` by hand.                                                                                                                                                                          | —                                                                                                                             |

Not set here, on purpose:

- **The whole arm driver on CPU 11:** worse. Missed deadlines rose ~6× and the host crashed 10 minutes in. The arm
  driver keeps only its loop and Kortex's receive thread there (kinova-gen3-ros2 `fix/kortex-rx-thread-rt`).
- **`kernel.sched_rt_runtime_us`:** this kernel ships `-1` (RT throttling off), so a stuck FIFO thread can starve
  its CPU. The kernel default `950000` is safer; it hasn't been tested with the arm loop yet.
- **Arm traffic priority on the Jetson:** this kernel has no `prio`/`htb` qdiscs and no DSCP marking. The only lever
  is `SO_PRIORITY` on the Kortex socket, in the driver.
