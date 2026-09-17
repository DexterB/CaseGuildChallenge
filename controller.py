#!/usr/bin/env python3
"""
Admission controller for managing per-tenant job queues. The controller optimizes
for fair-shar, SLO deadlines, and load-shedding or admission control behavior

Protocol reminder: read stdin line by line, answer every DONE with
zero or more ADMIT lines followed by END, and flush stdout after END.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
import json
import sys


DEFAULT_BOUND = 200
TIER_QUESTION, TIER_RESEARCH, TIER_UPLOAD = 0, 1, 2


@dataclass(frozen=True, slots=True)
class Job:

    job_id: str
    tenant: str
    tier: int
    size: int
    arrived_at: int
    bound: int = DEFAULT_BOUND

    @property
    def deadline(self) -> int:
        return self.arrived_at + self.bound


def main():
    # Read the configuration line from stdin.
    config_line  = sys.stdin.readline().strip()
    if not config_line:
        return

    # Accommodate both "CONFIG {...} from harness and raw JSON from workload.json"
    if config_line.startswith("CONFIG "):
        config = json.loads(config_line.split(" ", 1)[1])
    else:
        raw_json = json.loads(config_line)
        config = raw_json.get("config", raw_json)

    capacity = config["capacity"]
    max_ticks = config["max_ticks"]

    # For now we are using per tenant FIFO queues. Each is a sequential run queue of
    # jobs for that tenant. We will use a defaultdict of deques to store the queues.
    # Track active tenants using a set.
    #
    # Invariant: if a tenant is inactive the runqueue for that tenant should be empty.
    tenant_runqueues = defaultdict(deque)
    active_tenants = set()

    # Main loop to read job events from stdin.
    while True:
        line = sys.stdin.readline().strip()
        if not line or line.startswith("FINISH"):
            break

        segments = line.strip().split(" ", 1)
        if not segments:
            continue

        if segments[0] == "TICK":
            current_tick = int(segments[1])

        # 1. Ingest arriving jobs
        while True:
            json_line = sys.stdin.readline().strip()
            if json_line == "DONE":
                break

            # ARRIVE <jid> <tenant> <tier> <size> <bound>
            tokens = json_line.split()
            jid = tokens[1]
            tenant = tokens[2]
            tier = int(tokens[3])
            size = int(tokens[4])
            bound = int(tokens[5]) if len(tokens) > 5 else DEFAULT_BOUND
            # Create a Job instance for the arriving job.
            job = Job(
                job_id=jid,
                tenant=tenant,
                tier=tier,
                size=size,
                arrived_at=current_tick,
                bound=bound,
            )
            tenant_runqueues[job.tenant].append(job)
            active_tenants.add(job.tenant)
        # 2. Scheduling & Admission Control
        admitted = []
        remaining_capacity = capacity

        while remaining_capacity > 0 and active_tenants:
            candidates = []

            # Collect one fitting candidate per active tenant
            for tenant in list(active_tenants):
                runqueue = tenant_runqueues[tenant]
                for job in runqueue:
                    if job.size <= remaining_capacity:
                        candidates.append(job)
                        break

            # If no tenant has any job that fits, exit the scheduling loop
            if not candidates:
                break

            # Sort across all candidate tenants:
            # 1. Urgent deadlines nearing starvation bounds
            # 2. Tier priority (TIER_QUESTION > TIER_RESEARCH > TIER_UPLOAD)
            # 3. Shortest deadline
            # 4. Smallest size
            candidates.sort(key=lambda job: (
                0 if job.deadline - current_tick < 15 else 1,
                job.tier,
                job.deadline,
                job.size
            ))

            # Admit the single best candidate from this round
            chosen = candidates[0]
            admitted.append(chosen.job_id)
            remaining_capacity -= chosen.size
            tenant_runqueues[chosen.tenant].remove(chosen)

            if not tenant_runqueues[chosen.tenant]:
                active_tenants.discard(chosen.tenant)

        # 3. Idle-Filler: Admit jobs to fill any remaining capacity, prioritizing smaller jobs to maximize utilization.
        #    If capacity remains seep all remining jobs to ensure not fitting work is idle.
        if remaining_capacity > 0:
            for tenant in list(active_tenants):
                runqueue = tenant_runqueues[tenant]
                to_remove = []
                for job in runqueue:
                    if job.size <= remaining_capacity:
                        # Prevent priority violation: don't admit tier 2 if tier 0 is waiting.
                        if job.tier == TIER_UPLOAD and any(
                            j.tier == TIER_QUESTION and j.size <= remaining_capacity
                            for other_tenant in active_tenants
                            for j in tenant_runqueues[other_tenant]
                        ):
                            continue

                        # Admit the job since it passed the priority check.
                        admitted.append(job.job_id)
                        remaining_capacity -= job.size
                        to_remove.append(job)
                        if remaining_capacity == 0:
                            break

                for job in to_remove:
                    runqueue.remove(job)
                if not runqueue:
                    active_tenants.discard(tenant)
                if remaining_capacity == 0:
                    break

        # 4. Emit output protocol and flush.
        if admitted:
            sys.stdout.write("ADMIT " + " ".join(map(str, admitted)) + "\n")
        sys.stdout.write("END\n")
        sys.stdout.flush()

if __name__ == "__main__":
    main()

