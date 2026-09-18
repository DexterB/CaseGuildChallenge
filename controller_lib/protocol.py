"""stdin/stdout wire protocol: config parsing, job arrivals, admission output."""

import json

from controller_lib.models import DEFAULT_BOUND, Job


def parse_config(line):
    """Accept both "CONFIG {...}" from the harness and raw JSON from a workload file."""
    if line.startswith("CONFIG "):
        return json.loads(line.split(" ", 1)[1])
    raw = json.loads(line)
    return raw.get("config", raw)


def parse_arrive(tokens, current_tick):
    """Parse an "ARRIVE <jid> <tenant> <tier> <size> <bound>" token list."""
    jid = tokens[1]
    tenant = tokens[2]
    tier = int(tokens[3])
    size = int(tokens[4])
    bound = int(tokens[5]) if len(tokens) > 5 else DEFAULT_BOUND
    return Job(
        job_id=jid,
        tenant=tenant,
        tier=tier,
        size=size,
        arrived_at=current_tick,
        bound=bound,
    )


def read_arrivals(stdin, current_tick, queues):
    """Ingest ARRIVE lines into queues until a DONE line is read."""
    while True:
        line = stdin.readline().strip()
        if line == "DONE":
            break

        tokens = line.split()
        if not tokens or tokens[0] != "ARRIVE":
            continue

        queues.add(parse_arrive(tokens, current_tick))


def write_admissions(stdout, admitted):
    """Emit the ADMIT/END response for a tick and flush."""
    if admitted:
        stdout.write("ADMIT " + " ".join(map(str, admitted)) + "\n")
    stdout.write("END\n")
    stdout.flush()
