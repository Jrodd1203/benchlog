"""Connect to the serial agent and run every command once.

    python agent/tools/try_client.py                  # list serial ports
    python agent/tools/try_client.py /dev/cu.usbserial-0001

Close `pio device monitor` first: only one program can hold the port.
"""

import sys
import time

from benchlog.serial.agent_client import AgentClient, AgentError
from benchlog.serial.service import available_ports


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: try_client.py PORT\n\nports:")
        for p in available_ports():
            print(f"  {p.device}  {p.description}")
        return 1

    port = sys.argv[1]
    print(f"connecting to {port} (the board reboots, give it a few seconds)...")
    try:
        with AgentClient(port) as agent:
            print(f"HELLO  {agent.info.model_dump_json()}")
            for name, call in [
                ("PING ", agent.ping),
                ("PROBE", agent.probe),
                ("PROBE", lambda: agent.probe([18, 21, 5])),
                ("I2C  ", agent.i2c),
                ("READ ", lambda: agent.read(34)),
                ("READ ", lambda: agent.read(18)),
            ]:
                start = time.monotonic()
                result = call()
                print(f"{name}  {result.model_dump_json()}  ({(time.monotonic() - start) * 1000:.0f} ms)")
            try:
                agent.read(5)
            except AgentError as e:
                print(f"READ   5 -> {e}  (expected: 5 is a strapping pin)")
    except AgentError as e:
        print(f"error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
