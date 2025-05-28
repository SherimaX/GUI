#!/usr/bin/env python3
"""
One-shot UDP sender to test Simulink control signals.

This script packs four single-precision floats—`zero_signal`, `motor_signal`,
`assistance_signal`, and `fixed_k_signal`—into a 16-byte datagram and sends it
once to the IP/port defined under `udp.send_host` / `udp.send_port` in
`config.yaml` (default: 192.168.7.5:5432).

Run with custom values:

    python main.py --zero 0.0 --motor 1.2 --assist 0.8 --k 0.5

If no arguments are provided, default test values are used.
"""

import argparse
import asyncio
import struct
import yaml
import logging
from typing import Dict, Any

CONFIG_FILE = "config.yaml"
FMT = "<4f"  # four little-endian 32-bit floats (16 bytes)


def load_config(path: str = CONFIG_FILE) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


async def send_packet(payload: bytes, host: str, port: int) -> None:
    """Send *payload* to *(host, port)* via UDP once and close the socket."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol,
        remote_addr=(host, port),
    )
    logging.info("Sending %d bytes to %s:%d", len(payload), host, port)
    transport.sendto(payload)
    # Give the event loop a tick to flush the packet
    await asyncio.sleep(0.05)
    transport.close()


def build_payload(args) -> bytes:
    """Pack CLI args into binary payload."""
    values = [args.zero, args.motor, args.assist, args.k]
    return struct.pack(FMT, *values)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Send a test control packet to Simulink.")
    p.add_argument("--zero", type=float, default=0.0, help="zero_signal value (float)")
    p.add_argument("--motor", type=float, default=1.0, help="motor_signal value (float)")
    p.add_argument("--assist", type=float, default=0.5, help="assistance_signal value (float)")
    p.add_argument("--k", type=float, default=0.2, help="fixed_k_signal value (float)")
    return p.parse_args()


async def main_async() -> None:
    cfg = load_config()
    args = parse_args()
    payload = build_payload(args)

    await send_packet(payload, cfg["udp"]["send_host"], cfg["udp"]["send_port"])
    logging.info("Done.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    asyncio.run(main_async()) 