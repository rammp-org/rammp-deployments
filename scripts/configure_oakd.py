#!/usr/bin/env python3
"""Configure a Luxonis OAK-D PoE camera for the RAMMP network."""

import argparse
import ipaddress
import sys


DEFAULT_MASK = "255.255.255.0"
DEFAULT_GATEWAY = "192.168.1.1"


def valid_ipv4(value: str) -> str:
    try:
        return str(ipaddress.IPv4Address(value))
    except ipaddress.AddressValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid IPv4 address: {value}") from exc


def validate_network(ip: str, mask: str, gateway: str) -> None:
    address = ipaddress.IPv4Address(ip)
    gateway_address = ipaddress.IPv4Address(gateway)

    if any(
        (
            address.is_loopback,
            address.is_multicast,
            address.is_link_local,
            address.is_unspecified,
            address.is_reserved,
        )
    ):
        raise ValueError(f"IP address {ip} is not usable for a camera")

    if any(
        (
            gateway_address.is_loopback,
            gateway_address.is_multicast,
            gateway_address.is_link_local,
            gateway_address.is_unspecified,
            gateway_address.is_reserved,
        )
    ):
        raise ValueError(f"gateway {gateway} is not usable")

    try:
        network = ipaddress.IPv4Network(f"{ip}/{mask}", strict=False)
    except ValueError as exc:
        raise ValueError(f"invalid netmask: {mask}") from exc

    if str(network.netmask) != mask:
        raise ValueError(f"netmask must be a valid contiguous netmask: {mask}")

    if address in (network.network_address, network.broadcast_address):
        raise ValueError(f"{ip} cannot be a network or broadcast address")

    if gateway_address == address:
        raise ValueError("gateway cannot be the same as the camera IP")

    if gateway_address not in network:
        raise ValueError(f"gateway {gateway} is outside {network}")

    if gateway_address in (network.network_address, network.broadcast_address):
        raise ValueError(f"gateway {gateway} cannot be a network or broadcast address")


def discover_devices(dai):
    devices = dai.DeviceBootloader.getAllAvailableDevices()

    if not devices:
        print(
            "No OAK-D bootloader device found.\n"
            "For PoE cameras, stop the oak_d_pro_docker driver container if it is running "
            "and make sure the camera is reachable over ethernet.",
            file=sys.stderr,
        )
        return []

    return devices


def select_device(devices, mxid: str | None, index: int | None):
    print("Available OAK-D devices:")

    for device_index, info in enumerate(devices):
        print(f"  [{device_index}] MXID: {info.mxid}, Name: {info.name}")

    if mxid is not None:
        for info in devices:
            if info.mxid == mxid:
                return info

        raise ValueError(f"No OAK-D device found with MXID {mxid}")

    if index is not None:
        if index < 0 or index >= len(devices):
            raise ValueError(f"device index must be between 0 and {len(devices) - 1}")

        return devices[index]

    if len(devices) == 1:
        return devices[0]

    try:
        answer = input("Select device index: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return None

    try:
        selected_index = int(answer)
    except ValueError as exc:
        raise ValueError("device index must be an integer") from exc

    if selected_index < 0 or selected_index >= len(devices):
        raise ValueError(f"device index must be between 0 and {len(devices) - 1}")

    return devices[selected_index]


def configure(
    ip: str,
    mask: str,
    gateway: str,
    confirm: bool,
    mxid: str | None,
    index: int | None,
) -> int:
    try:
        import depthai as dai
    except ImportError:
        print(
            "DepthAI is not installed. Install it with:\n"
            "  python -m pip install depthai",
            file=sys.stderr,
        )
        return 1

    devices = discover_devices(dai)

    if not devices:
        return 1

    try:
        info = select_device(devices, mxid, index)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if info is None:
        return 0

    print()
    print("Selected OAK-D:")
    print(f"  MXID       : {info.mxid}")
    print(f"  Name       : {info.name}")

    try:
        with dai.DeviceBootloader(info) as bootloader:
            config = bootloader.readConfig()

            print(f"  Current IP : {config.getIPv4()}")
            print(f"  Current mask : {config.getIPv4Mask()}")
            print(f"  Current gateway : {config.getIPv4Gateway()}")

            print()
            print("New OAK-D network configuration:")
            print(f"  Static IP : {ip}")
            print(f"  Netmask   : {mask}")
            print(f"  Gateway   : {gateway}")

            if not confirm:
                try:
                    answer = input(
                        "Flash this configuration to the selected OAK-D? [y/N] "
                    )
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    return 0

                if answer.strip().lower() != "y":
                    print("Aborted.")
                    return 0

            config.setStaticIPv4(ip, mask, gateway)

            success, error = bootloader.flashConfig(config)

    except Exception as exc:
        print(f"Failed to configure OAK-D: {exc}", file=sys.stderr)
        return 1

    if not success:
        print(f"Failed to configure OAK-D: {error}", file=sys.stderr)
        return 1

    print(f"Successfully configured OAK-D with static IP {ip}.")
    print("Reconnect the camera to the RAMMP network.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Configure an OAK-D PoE camera for the RAMMP network."
    )
    parser.add_argument(
        "--ip",
        required=True,
        type=valid_ipv4,
        help="Target static IPv4 address",
    )
    parser.add_argument(
        "--mask",
        type=valid_ipv4,
        default=DEFAULT_MASK,
        help=f"IPv4 netmask (default: {DEFAULT_MASK})",
    )
    parser.add_argument(
        "--gateway",
        type=valid_ipv4,
        default=DEFAULT_GATEWAY,
        help=f"IPv4 gateway (default: {DEFAULT_GATEWAY})",
    )
    parser.add_argument(
        "--mxid",
        help="Select the OAK-D by MXID",
    )
    parser.add_argument(
        "--index",
        type=int,
        help="Select the OAK-D by device index",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt",
    )

    args = parser.parse_args()

    try:
        validate_network(args.ip, args.mask, args.gateway)
    except ValueError as exc:
        parser.error(str(exc))

    if args.mxid is not None and args.index is not None:
        parser.error("--mxid and --index cannot be used together")

    return configure(
        args.ip,
        args.mask,
        args.gateway,
        args.yes,
        args.mxid,
        args.index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
