# Wrist camera through the Vision Puck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** flatbot's wrist OAK runs through the Kinova Vision Puck (static 10.20.0.50 behind
the arm), with the sensor-network setup kept beside it, commented, and a runbook to switch.

**Architecture:** Extend the two existing robot-config scripts (`network_config.py`
gains `routes:`, `oak_d_config.py` gains `reach_at:`), add a third
(`arm_config.py` + `arm.yaml`, Kortex API) for the arm's wrist Ethernet port, and make
the camera node accept `driver.i_ip` from the profile.

**Tech Stack:** uv inline-script Python, PyYAML 6.0.2, NetworkManager (`nmcli` 1.36 on
snorlax), depthai 3.10.0, Kinova `kortex_api` 2.8.0 on Python 3.9, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-wrist-camera-puck-design.md`

## Global Constraints

- Wrist port: enabled, `ETHERNET_SPEED_100M`, `ETHERNET_DUPLEX_FULL`, on the Interconnect's
  `ETHERNET_DEVICE_EXPANSION`.
- Camera through the Puck: static `10.20.0.50/24`, gateway `10.20.0.1`. Host route:
  `10.20.0.0/24` via `192.168.1.10` on `flatbot-arm` (`enp3s0`).
- Kortex: wheel
  `https://artifactory.kinovaapps.com/artifactory/generic-public/kortex/API/2.8.0/kortex_api-2.8.0.post5-py3-none-any.whl`,
  `requires-python = "==3.9.*"`, `protobuf==3.20.0` (the wheel requires `<=3.20`). Arm login
  from `KINOVA_USERNAME` / `KINOVA_PASSWORD`, default `admin` / `admin`.
- Every script keeps the house shape: `--validate` / `--dry-run`, `ConfigError` for the
  file, exit 0 ok / 1 a change failed / 2 invalid and nothing changed, no write when the
  target already matches, heavy imports (depthai, kortex) only inside apply.
- `--validate` must not import depthai or kortex_api.
- Style: ruff + ruff-format and mdformat via pre-commit. Comments explain *why*, in the
  existing voice.
- Unit tests touch no hardware and no `nmcli`/`sudo`: stub `_run`/`_get`/`subprocess`.

## Review Focus

1. A route whose `via` is outside the interface's subnet, or `routes:` on a `dhcp`
   interface: refused at `--validate` with exit 2, naming the field (Task 1 tests).
2. `nmcli -g ipv4.routes` reads back in a different spelling than we write (spacing,
   `, ` separators, a metric): a second run must still print `unchanged`, not re-apply
   and bounce the arm link (Task 1 normalization test).
3. Something answers at `reach_at` with a different device ID (a swapped camera): refuse,
   never flash it (Task 2 test).
4. The camera is found both by broadcast and at `reach_at`: use one, no duplicate
   handling, no error (Task 2 test).
5. The arm is unreachable or the login is wrong: one-line `FAILED` and exit 1, no Kortex
   traceback; `duplex: FULL` or `speed: 1G` refused at `--validate`, listing the allowed
   values (Task 3 tests).

---

### Task 1: `routes:` in network.yaml (and the test harness)

**Files:**

- Create: `tests/conftest.py`, `tests/test_network_config.py`
- Modify: `scripts/network_config.py` (`Interface`, `parse_ipv4`, `settings`, `plan`)
- Modify: `december_2026/config/robots/flatbot/network.yaml`
- Modify: `.github/workflows/validate.yml` (new step after "Validate every robot config")

**Interfaces:**

- Produces: `tests/conftest.py` fixture-free helper `load_script(name: str) -> ModuleType`
  that imports `scripts/<name>.py` by path with `importlib.util` (the scripts are not a
  package). Later tasks' tests use it.
- Produces: `Interface.routes: tuple[tuple[str, str], ...] = ()` (destination CIDR,
  gateway), parsed from
  ```yaml
  ipv4:
    method: static
    address: 192.168.1.11/24
    routes:
      - to: 10.20.0.0/24
        via: 192.168.1.10
  ```
- Produces: `settings()` includes `"ipv4.routes": "10.20.0.0/24 192.168.1.10"` (entries
  joined with `", "`; `""` when none).

- [ ] **Step 1: Write the failing tests** in `tests/test_network_config.py`:
  - `test_routes_parsed`: the yaml above parses to
    `routes == (("10.20.0.0/24", "192.168.1.10"),)`.
  - `test_route_via_outside_subnet_rejected`: `via: 10.20.0.1` raises `ConfigError` whose
    message contains `via` and `outside 192.168.1.0/24`.
  - `test_route_to_must_be_network`: `to: 10.20.0.5/24` raises `ConfigError` (host bits
    set).
  - `test_routes_on_dhcp_rejected`: `method: dhcp` with `routes:` raises `ConfigError`.
  - `test_settings_routes`: `settings(iface)["ipv4.routes"] == "10.20.0.0/24 192.168.1.10"`.
  - `test_plan_unchanged_when_nmcli_spells_routes_differently`: monkeypatch `_get` to
    return the wanted settings but `ipv4.routes` as `"10.20.0.0/24 192.168.1.10 0"` (with
    metric) and assert `plan(...)` returns no `con mod` command. Stub `_sys` to return
    `""`.
  - CI wiring: `uv run --with pytest==8.3.4 --with pyyaml==6.0.2 pytest tests -q`.

- [ ] **Step 2: Run to verify they fail**

  Run: `uv run --with pytest==8.3.4 --with pyyaml==6.0.2 pytest tests -q`
  Expected: FAIL (`routes` unknown key / attribute missing).

- [ ] **Step 3: Implement.** `parse_ipv4` allows `routes` with `static` only; each entry
  takes exactly `to` (strict `IPv4Network`) and `via` (inside `address.network`), errors
  at `{where}.routes[i].to` / `.via`. `plan()` compares current vs wanted through a
  `_normalize_routes(text: str) -> set[tuple[str, str]]` that splits on `,`, takes the
  first two whitespace fields, and ignores a trailing metric. Other keys compare as today.

- [ ] **Step 4: Run tests to verify they pass.** Same command. Expected: all pass.

- [ ] **Step 5: Config + CI.** Add the route to `flatbot-arm` in `network.yaml`, with a
  comment: the Vision Puck's subnet, routed by the arm base; harmless when the wrist
  camera is on the sensor network. Change the entry's header comment so it no longer says
  the arm's network "goes nowhere else". Add a `Unit tests` step to `validate.yml` running
  the Step 1 command. Run
  `uv run scripts/network_config.py --validate december_2026/config/robots/flatbot`:
  expected `ok — 2 interfaces`. Run `pre-commit run --all-files`: expected pass.

- [ ] **Step 6: Commit** `network_config: routes per interface; flatbot routes the Vision Puck's subnet via the arm`.

### Task 2: `reach_at:` in oak_d.yaml; flatbot's wrist camera through the Puck

**Files:**

- Modify: `scripts/oak_d_config.py` (`Camera`, `parse`, `apply`, `_apply_one`,
  `_wait_for_restart`)
- Create: `tests/test_oak_d_config.py`
- Modify: `december_2026/config/robots/flatbot/oak_d.yaml`

**Interfaces:**

- Consumes: `load_script` (Task 1).
- Produces: `Camera.reach_at: str = ""` (dotted IPv4), optional key beside `device_id`.
- Produces: `_unicast_search(ip: str) -> list[tuple[str, str, str]]` returning
  `(deviceId, name, state.name)`. It runs
  `sys.executable -c <one-liner listing dai.XLinkConnection.getAllConnectedDevices()>`
  with `DEPTHAI_DEVICE_NAME_LIST=<ip>` in the child's env, and parses the result as JSON.
  **Why a child:** verified on snorlax 2026-11-17 with depthai 3.10, the variable makes
  discovery ask only that address, but it is read once per process; setting it
  in-process after a search has no effect.

- [ ] **Step 1: Write the failing tests** in `tests/test_oak_d_config.py`:
  - `test_reach_at_parsed`: `reach_at: 10.20.0.50` gives `Camera.reach_at == "10.20.0.50"`;
    `reach_at: 10.20.0` raises `ConfigError`.
  - `test_locate_prefers_broadcast`: with a fake dai whose `Device.getAllAvailableDevices`
    returns the camera, and `_unicast_search` patched to fail if called, `_locate` returns
    the broadcast `DeviceInfo`.
  - `test_locate_falls_back_to_reach_at`: broadcast empty, `_unicast_search` returns
    `[("194430107187F42E00", "10.20.0.50", "X_LINK_BOOTLOADER")]`: `_locate` returns an
    info with name `10.20.0.50`, state BOOTLOADER, protocol TCP_IP.
  - `test_locate_refuses_other_device_at_reach_at`: `_unicast_search` returns a different
    device ID: `_locate` raises `CameraError` whose message names both IDs.
  - `test_wait_static_uses_unicast`: for a static target, `_wait_for_restart` polls
    `_unicast_search("10.20.0.50")` (patched to report gone, then back) and returns
    `"10.20.0.50"`.

- [ ] **Step 2: Run to verify they fail.** Run the Task 1 test command. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - `_locate(dai, cam: Camera, found: dict) -> DeviceInfo | None`: broadcast hit first;
    otherwise, if `reach_at`, `_unicast_search(reach_at)`; on an ID match build
    `dai.DeviceInfo(reach_at)` with state and protocol set as Kinova's `device_manager.py`
    does (`X_LINK_BOOTLOADER` taken from the search, `X_LINK_TCP_IP`); a different ID is a
    `CameraError`. `apply()` uses `_locate` in place of `found.get`. Update the "not found"
    message to mention `reach_at`.
  - `_wait_for_restart(dai, cam: Camera, timeout=60.0)`: for `static`, wait for it to drop
    off and come back with `_unicast_search(<new ip>)`; for `dhcp`, broadcast as today.
    The new address may be off every local subnet (flashing onto the Puck's subnet from
    `eno1`): with static, if it does not come back, the error says so and that this is
    expected until the cable is in the Puck and the route and wrist port are up. That case
    is exit 1 with the config already written. Say that plainly.
  - Module docstring: describe `reach_at` and the child-process search.

- [ ] **Step 4: Run tests to verify they pass.** Expected: all pass.

- [ ] **Step 5: Config.** In flatbot's `oak_d.yaml`, `wrist_camera` gets
  `reach_at: 10.20.0.50` (always present: where to look when broadcast cannot reach it),
  then the live Puck block (`static`, `10.20.0.50/24`, `gateway: 10.20.0.1`) and the
  sensor-network block (`method: dhcp`) commented beneath it. Each block gets one comment
  line on when it applies. Replace the old "becomes static on 192.168.1.x" comment. Run
  `uv run scripts/oak_d_config.py --validate december_2026/config/robots/flatbot`:
  expected `ok — 2 cameras`. Run pre-commit.

- [ ] **Step 6: Commit** `oak_d_config: reach a camera behind a router by IP; flatbot's wrist camera through the Vision Puck`.

### Task 3: `arm_config.py` and `arm.yaml` (the wrist Ethernet port)

**Files:**

- Create: `scripts/arm_config.py`, `december_2026/config/robots/flatbot/arm.yaml`,
  `tests/test_arm_config.py`
- Modify: `.github/workflows/validate.yml` (robot-config loop: `arm.yaml` → `--validate`)

**Interfaces:**

- Consumes: `load_script` (Task 1).
- Produces: `arm.yaml`:
  ```yaml
  address: 192.168.1.10
  wrist_ethernet:
    enabled: true
    speed: 100M   # 10M | 100M
    duplex: full  # half | full
  ```
- Produces: `WristEthernet(enabled: bool, speed: str, duplex: str)`,
  `Arm(address: str, wrist_ethernet: WristEthernet)`, `load(robot_dir: Path) -> Arm`,
  `main(argv) -> int`. PEP 723 header: `requires-python = "==3.9.*"`, deps
  `pyyaml==6.0.2`, `protobuf==3.20.0`, the Kortex wheel URL (Global Constraints).
  Code must also import under the test runner's newer Python
  (`from __future__ import annotations`, kortex imported only in `apply`).

- [ ] **Step 1: Write the failing tests** in `tests/test_arm_config.py`:
  - `test_parse_flatbot`: the yaml above gives
    `Arm("192.168.1.10", WristEthernet(True, "100M", "full"))`.
  - `test_bad_duplex_rejected`: `duplex: FULL` gives a `ConfigError` containing
    `half, full`; `speed: 1G` gives one containing `10M, 100M`.
  - `test_unknown_key_rejected`: `wrist_ethernet.mtu` gives `ConfigError`.
  - `test_apply_skips_write_when_matching`: a fake Kortex layer (patch
    `_session(address)` as a context manager yielding a stub with `read_wrist()` /
    `write_wrist(cfg)`) whose current config already matches: `apply` returns 0 and
    `write_wrist` is never called.
  - `test_apply_unreachable_is_one_line`: `_session` raises `ConnectionError`: exit 1, and
    stderr has one line starting `arm (192.168.1.10): FAILED:`.

- [ ] **Step 2: Run to verify they fail.** Expected: FAIL (no module).

- [ ] **Step 3: Implement.** Parsing in the house `_keys`/`_string` style. `_session` is a
  context manager wrapping Kinova's sequence from `VisionPkConf.py`: `TCPTransport`
  connected on port 10000, `RouterClient`, `SessionManager.CreateSession` with
  `KINOVA_USERNAME`/`KINOVA_PASSWORD` (default `admin`), timeouts 60000/2000 ms as Kinova
  uses, and `CloseSession` + `disconnect` on exit. Inside it: `DeviceManagerClient.ReadAllDevices()`,
  pick the first `device_type == Common_pb2.INTERCONNECT` (none: `ArmError`),
  `InterconnectConfigClient.GetEthernetConfiguration(EthernetDeviceIdentification(device=ETHERNET_DEVICE_EXPANSION), interconnect_id)`.
  Compare `enabled/speed/duplex` with the file. On mismatch with `--dry-run`, print
  `would change … -> …`. Otherwise set the three fields, `SetEthernetConfiguration`,
  read back, and fail if the read-back differs. Print values as `enabled 100M full`.
  Catch `ArmError`, `ConnectionError`, `OSError` and `KServerException` (from
  `kortex_api.Exceptions.KServerException`) into the one-line FAILED. Module docstring:
  what the port is, that Kinova's procedure is where this comes from, and that whether
  the setting survives an arm power cycle is not yet known (ask Kinova; re-run after a
  power cycle until then).

- [ ] **Step 4: Run tests to verify they pass.** Expected: all pass.

- [ ] **Step 5: Config + CI.** Write flatbot's `arm.yaml` with a header comment
  (applied by, what it is for, *full* duplex chosen; Kinova's sample set half against its
  own comment, and full is being confirmed with them). Add the `arm.yaml` branch to the CI
  loop. Run `uv run scripts/arm_config.py --validate december_2026/config/robots/flatbot`:
  expected `ok`. First run downloads Python 3.9 and the wheel. Run pre-commit.

- [ ] **Step 6: Commit** `arm_config: the arm's wrist Ethernet port from arm.yaml; flatbot enables it at 100M full`.

### Task 4: camera node by IP, profiles, and the runbook

**Files:**

- Modify: `december_2026/sheppy-manifest.yaml` (`oak_d_pro_docker` params, ~line 246)
- Modify: `december_2026/profiles/flatbot.yaml`, `december_2026/profiles/flatbot_empty_world.yaml`
- Modify: `december_2026/config/robots/flatbot/README.md`, `CONTRIBUTING.md` (robot-config
  table gets the `arm.yaml` row)

- [ ] **Step 1:** In `oak_d_pro_docker` params, add `driver.i_ip: ""` after
  `driver.i_device_id`, with a comment: empty finds the camera by device ID (broadcast,
  same subnet); an IP connects to it directly, as a camera behind the arm's Vision Puck
  needs. Update the device-ID comment that says the camera must be on a host subnet.
- [ ] **Step 2:** In both flatbot profiles, under `overrides:`, add
  `wrist_camera: {driver.i_ip: 10.20.0.50}` with the sensor-network alternative as a
  comment (`# driver.i_ip: ""`). Comments point at the README section.
- [ ] **Step 3:** Verify. Run `uv tool run --from "sheppy @ git+https://github.com/rammp-org/sheppy@dev" python scripts/validate_manifest.py`
  (or `pip install` sheppy@dev into a venv, as CI does). Expected: every manifest and
  profile ok, with no "overrides a key not in params" error.
- [ ] **Step 4:** README. Cabling table: wrist camera row via the Puck. Replace the
  "When the wrist camera is cabled through the arm's wrist Ethernet…" paragraph and the
  temporary-address passage with **Switching the wrist camera**. Each direction is an
  ordered list:
  - **Sensor network → Puck:** set the Puck block live in `oak_d.yaml`; run
    `oak_d_config.py` while the camera is still on `eno1` (stop `wrist_camera` first; it
    reports the camera did not come back, which is expected); move the cable to the Puck;
    run `network_config.py` and `arm_config.py`; `ping 10.20.0.50`; set
    `driver.i_ip: 10.20.0.50` in the profile; `sheppy restart wrist_camera`.
  - **Puck → sensor network:** set the DHCP block live; run `oak_d_config.py` (it finds
    the camera at `reach_at`); move the cable to the PoE switch; set `driver.i_ip: ""`;
    restart.

  Add `arm_config.py` to "After a reflash". Note the 100 Mbit/s link and the measured
  63-83 Mbit/s. Run pre-commit (mdformat).
- [ ] **Step 5: Commit** `flatbot: wrist camera by IP through the Vision Puck; switching runbook`.

### Task 5: On snorlax (attended, with Swapnil)

Not delegated: it moves a cable, restarts the running stack, and talks to the arm. Do
it only with the user present and agreeing to each step.

- [ ] `git fetch && git switch feature/wrist-camera-puck` in `~/rammp-deployments` on
  snorlax.
- [ ] `network_config.py --dry-run`, then apply. Check `ip route get 10.20.0.50` shows
  `via 192.168.1.10 dev enp3s0`. Re-run: `unchanged` (Review Focus 2 on real nmcli).
- [ ] `arm_config.py --dry-run`, then apply. Expected read-back `enabled 100M full`.
  Re-run: `unchanged`.
- [ ] `sheppy stop wrist_camera`; `oak_d_config.py` (camera on `eno1`) writes static
  10.20.0.50.
- [ ] Move the cable into the Puck. `ping 10.20.0.50`. `sheppy restart wrist_camera`, or
  `sheppy up flatbot`. `/wrist_camera/color/image_raw` and depth publish; check rates and
  link load.
- [ ] Re-run `oak_d_config.py`: `unchanged at 10.20.0.50` (found through `reach_at`).
