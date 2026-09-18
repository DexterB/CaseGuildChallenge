#!/usr/bin/env python3
"""
Admission controller for managing per-tenant job queues. The controller optimizes
for fair-share, SLO deadlines, and load-shedding or admission control behavior.

Protocol reminder: read stdin line by line, answer every DONE with
zero or more ADMIT lines followed by END, and flush stdout after END.
"""

import sys

from controller_lib.protocol import parse_config, read_arrivals, write_admissions
from controller_lib.scheduler import idle_fill, select_admissions
from controller_lib.tenant_queues import TenantQueues


def main():
    config_line = sys.stdin.readline().strip()
    if not config_line:
        return

    config = parse_config(config_line)
    capacity = config["capacity"]

    queues = TenantQueues()

    while True:
        line = sys.stdin.readline().strip()
        if not line or line == "DONE":
            break

        segments = line.split(" ", 1)
        if not segments:
            continue

        if segments[0] == "TICK":
            current_tick = int(segments[1])

        # 1. Ingest arriving jobs
        read_arrivals(sys.stdin, current_tick, queues)

        # 2. Scheduling & Admission Control
        admitted, remaining_capacity = select_admissions(queues, capacity, current_tick)

        # 3. Idle-Filler: fill any remaining capacity so no fitting work is left idle.
        if remaining_capacity > 0:
            filled, remaining_capacity = idle_fill(queues, remaining_capacity)
            admitted.extend(filled)

        # 4. Emit output protocol and flush.
        write_admissions(sys.stdout, admitted)


if __name__ == "__main__":
    main()
