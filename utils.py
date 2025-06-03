import yaml
import struct
import subprocess
import platform
from typing import Dict, Any

from constants import CONFIG_FILE


def load_config(path: str = CONFIG_FILE) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp)


def decode_packet(data: bytes, fmt: str, mapping: Dict[str, int]) -> Dict[str, float]:
    """Decode *data* (binary) into a dict using *fmt* and *mapping*."""
    values = struct.unpack(fmt, data)
    return {name: values[idx] for name, idx in mapping.items()}


def is_host_reachable(host: str) -> bool:
    """Return True if *host* responds to a single ping."""
    param = "-n" if platform.system().lower().startswith("win") else "-c"
    try:
        result = subprocess.run(
            ["ping", param, "1", host],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False
