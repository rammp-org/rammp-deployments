# Seeed reComputer Robotics J5012

Host tuning for a Seeed Studio reComputer Robotics J5012 with GMSL: a Jetson AGX Orin 64GB module (L4T R36) on
Seeed's carrier. It runs the Kinova arm driver on one NIC and the OAK cameras on the other: flatbot's `snorlax`.
Everything here was measured on that Jetson on 2026-10-22 with the 1 kHz arm loop running under the full perception
load (rammp-deployments#120).

The arm NIC is one of the carrier's three Microchip LAN7430 ports (`lan743x`, `enp3s0`-`enp5s0`); the camera NIC is
the module's own `eno1` (`nvethernet`). The device tree still reports "NVIDIA Jetson AGX Orin Developer Kit"; the
carrier shows in `/etc/nv_boot_control.conf` (`recomputer-robo-agx-orin-j501x`).

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

| Setting                                                        | Why                                                                                                                                                                                                                                                                                                                                                             | Measured                                                                                                                                                                                                                 |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Drop IPv4 multicast leaving the arm NIC (tc clsact + flower)   | Cyclone DDS picks the arm NIC (`autodetermine`) and multicasts topic data on it. The NIC (lan743x) has one TX queue, so each camera frame delays the arm's packets. Local subscribers are unaffected. This drop is a backstop: the real fix is pinning Cyclone's interface.                                                                                     | 3.8 MB/s of DDS on the arm link: driver overruns ~0.05/s → ~14/s, 3–4 ms Kortex gaps. With the filter: 38 MB/s had no effect.                                                                                            |
| EEE off on the arm NIC                                         | Waking the link from low-power idle adds latency to every 1 kHz packet.                                                                                                                                                                                                                                                                                         | Kortex round trip p50 372 → 355 µs.                                                                                                                                                                                      |
| Camera NIC (`eno1`, nvethernet) queue IRQs vm0–vm4 → CPUs 4–8  | The arm NIC's interrupts are fixed on CPU0 (they come through the PCIe controller's MSI and can't be moved), and `eno1`'s default to CPU0 too.                                                                                                                                                                                                                  | Kortex round trip max 1.28 → 1.16 ms.                                                                                                                                                                                    |
| Threaded NAPI on the arm NIC, its threads on CPU 11 at FIFO 78 | The arm NIC's interrupts stay on CPU0, so its receive processing (softirq) ran there too, and under load in `ksoftirqd/0` at normal priority, behind everything else on CPU0. Threaded NAPI moves it into `napi/enp3s0-*` kthreads, pinned next to the RT loop (80) and Kortex's UDP receive thread (79). Tied to the driver's `--cpu 11` / `--rt-priority 80`. | 2026-10-26, 30 min, CPUs 0–10 ~90%, GPU ~64% (SAM3 bursts, RANSAC, cuRobo): max Kortex request gap 5.49 → 2.38 ms, reply max 4.4 → 1.28 ms; overruns 51 → 42 (kinova-gen3-ros2#62).                                      |
| `net.core.rmem_max` 32 MiB                                     | Cyclone asks for larger socket receive buffers than the stock 208 KiB cap allows. The kernel silently caps the request, Cyclone logs nothing, and DDS drops images. `rmem_default` stays stock: raising it changes every socket on the host, the arm driver's included, and DDS doesn't need it.                                                                | 2026-10-27, full flatbot stack, 5 min per setting: stock cap → 1.4% of UDP datagrams dropped (boxer 10.2k, RF-DETR 2.9k), cameras 12.3–13.8 Hz instead of 15. 32 MiB cap → 0 drops, with `rmem_default` 16 MiB or stock. |
| Checked only: `isolcpus=11 rcu_nocbs=11`, MAXN                 | The arm driver's RT loop runs on CPU 11 (`--cpu 11`). Edit `/boot/extlinux/extlinux.conf` by hand.                                                                                                                                                                                                                                                              | —                                                                                                                                                                                                                        |

Not set here, on purpose:

- **The whole arm driver on CPU 11:** worse. Missed deadlines rose ~6× and the host crashed 10 minutes in. The arm
  driver keeps only its loop and Kortex's receive thread there (kinova-gen3-ros2 `fix/kortex-rx-thread-rt`).
- **`kernel.sched_rt_runtime_us`:** this kernel ships `-1` (RT throttling off), so a stuck FIFO thread can starve
  its CPU. The kernel default `950000` is safer; it hasn't been tested with the arm loop yet.
- **Separating arm traffic from other traffic on the arm NIC:** lan743x has fixed-off ntuple filters and receive
  hashing (everything lands in RX queue 0), so the NAPI thread above processes every packet on the arm link. Today
  that's only Kortex. If camera data ever shares the arm link (e.g. a wrist camera downlinked through the arm), it
  would land on CPU 11 too: re-measure before keeping this setting.
- **Arm traffic priority on the Jetson:** this kernel has no `prio`/`htb` qdiscs and no DSCP marking. The only lever
  is `SO_PRIORITY` on the Kortex socket, in the driver.

## Time server for the MIB

The MIB (RAMMP-mib-fw) stamps `/odom` and `/tf` from its own clock, which counts from boot until something sets it.
Its SNTP client syncs that clock to this host every 15 s. It finds this host by name, `snorlax.local` over mDNS: the
MIB is the DHCP server on the sensor network (`eno1`, 192.168.4.0/24), so this host's address there can change.
`time_server.sh` makes this host answer:

```bash
sudo december_2026/config/hosts/jetson-agx-orin/time_server.sh install   # once per flash
december_2026/config/hosts/jetson-agx-orin/time_server.sh check           # any time: ok/FAIL per piece, with the fix
```

- chrony replaces systemd-timesyncd, which can sync this host's clock but can't serve time.
- `/etc/chrony/conf.d/rammp-mib.conf` allows the MIB's network and sets `local stratum 10`: when no upstream server is
  reachable, which on the robot is most of the time, chrony serves from this host's own clock. ROS stamps need the
  MIB to agree with this host, not with UTC.
- The same file caps chrony's slew rate at 500 ppm (`maxslewrate`; chrony's default is 83333). Linux applies a slew to
  `CLOCK_MONOTONIC`'s rate too, so at the default a large correction, e.g. after a reconnect, would run the arm
  driver's 1 kHz loop up to 8% off. 500 ppm is the kernel PLL's cap under timesyncd.
- chrony still steps the clock (`makestep 1 3`) when it is off by more than 1 s in its first three updates after it
  starts: the first upstream sync after an offline boot. A step moves `CLOCK_REALTIME` only, so ROS stamps and TF jump;
  the MIB follows within 15 s. `install` restarts chrony only when the drop-in changes, so a re-run doesn't open that
  window again.
- `check` also checks what makes `snorlax.local` resolve: avahi-daemon running, and the name it announces. That is the
  hostname unless avahi lost a name conflict and renamed itself (`snorlax-2.local`). Another robot's Jetson needs
  `NTP_NAME=` here and `ntp_server` changed in the firmware's `MIBconfig.hpp`.

To see the MIB syncing: `sudo chronyc clients` lists 192.168.4.1, and its NTP count grows every 15 s. The MIB logs
`System clock synced to snorlax.local` once, at its first sync.
