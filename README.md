# rammp-deployments

What a RAMMP chair runs, and what its host has to be.

| | |
| --- | --- |
| `<deployment>/sheppy-manifest.yaml` | The nodes for one deployment. `cd` into the directory and run `sheppy`. |
| `scripts/` | Host setup for the Jetson: the real-time tunings every deployment needs. |

## scripts/

| Script | Run | What it does |
| --- | --- | --- |
| `rt_grant_once.sh [USER]` | once, with `sudo` | Creates the `realtime` group, grants it `rtprio`/`memlock` limits and a udev rule for `/dev/cpu_dma_latency`, so RT processes run without `sudo`. Log out and back in afterwards. |
| `rt_setup.sh [RT_CORE]` | every boot, with `sudo` | MAXN power model, locked clocks, `performance` governor, deep idle off, RT throttling off, timer migration off, transparent huge pages off, IRQs nudged off the RT core (default 11). |
| `rt-setup.service` | install once | systemd oneshot that runs `rt_setup.sh` at boot. Copy to `/etc/systemd/system/`, point `ExecStart` at your checkout, `sudo systemctl enable --now rt-setup.service`. |

Core isolation (`isolcpus=11 nohz_full=11 rcu_nocbs=11` on the kernel command
line) is a separate boot-time edit; the full walkthrough is
[Developer setup](https://rammp-org.github.io/setup) on the docs site, and the
reasoning behind each setting is the arm driver's
[real-time tuning guide](https://github.com/rammp-org/kinova-gen3-driver/blob/main/docs/rt-tuning.md).
