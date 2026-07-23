"""Generate a gateway config that both the host and sandboxes can reach."""

from __future__ import annotations

import socket
from pathlib import Path


def main() -> None:
    example_directory = Path(__file__).parent
    template = (example_directory / "gateway.toml").read_text()
    placeholder = "REPLACE_WITH_HOST_IP"
    if template.count(placeholder) != 1:
        raise RuntimeError("gateway.toml must contain exactly one host placeholder")

    host_ip = _discover_host_ipv4()
    output_path = example_directory / "gateway.local.toml"
    output_path.write_text(template.replace(placeholder, host_ip))
    print(f"Wrote {output_path.name} with middleware host {host_ip}")


def _discover_host_ipv4() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 80))
        return str(probe.getsockname()[0])


if __name__ == "__main__":
    main()
