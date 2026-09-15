#!/usr/bin/env python3
"""Admission controller challenge harness: workload generation, replay, scoring.

Reading this file, including the scoring function, is allowed and encouraged.
"""
import argparse
import json
import random
import subprocess
import sys
import time
from collections import defaultdict

DEFAULT_BOUND = 200
TICK_TIMEOUT_SECONDS = 2.0
JUDGED_TENANT_MAX_SHARE = 0.05   # tenants offering <5% of total load are "judged"
JUDGED_P99_TARGET = 30           # judged tenants should see p99 waits at/below this
JAIN_WINDOW_TICKS = 50


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def gen_public(seed):
    """One firm drops a huge upload while small firms ask questions and one
    runs research. The README's opening sentence, made literal."""
    rng = random.Random(seed)
    jobs = []  # (tick, id, tenant, tier, size, bound-or-None)

    # Whale upload: 5000 shards land at tick 100. Patient (bound 2000).
    for i in range(5000):
        jobs.append((100, f"up{i}", "firm-whale", 2, rng.randint(10, 25), 2000))

    # Five small firms ask questions: tiny tier-0 jobs, steady trickle.
    for m in range(5):
        t = 0
        while t < 1500:
            t += rng.randint(1, 5)
            for _ in range(rng.randint(1, 2)):
                jobs.append((t, f"q{m}_{t}_{rng.randint(0, 9999)}",
                             f"firm-{m}", 0, rng.randint(1, 5), None))

    # One firm runs three research batches: medium tier-1, patient-ish (500).
    for b, start in enumerate((200, 600, 1000)):
        for i in range(30):
            jobs.append((start, f"rs{b}_{i}", "firm-research", 1,
                         rng.randint(10, 40), 500))

    return {"capacity": 100, "max_ticks": 3000}, jobs


SCENARIOS = {"public": gen_public}


def cmd_gen(args):
    config, jobs = SCENARIOS[args.scenario](args.seed)
    jobs.sort(key=lambda j: j[0])
    with open(args.output, "w") as f:
        f.write(json.dumps({"config": config}) + "\n")
        for tick, jid, tenant, tier, size, bound in jobs:
            rec = {"t": tick, "id": jid, "tn": tenant, "tier": tier, "size": size}
            if bound is not None and bound != DEFAULT_BOUND:
                rec["bound"] = bound
            f.write(json.dumps(rec) + "\n")
    total = sum(j[4] for j in jobs)
    print(f"wrote {len(jobs)} jobs, {total} units, capacity "
          f"{config['capacity']} x {config['max_ticks']} ticks -> {args.output}")


# --------------------------------------------------------------------------
# Replay + scoring
# --------------------------------------------------------------------------

def load_workload(path):
    with open(path) as f:
        header = json.loads(f.readline())
        config = header["config"]
        by_tick = defaultdict(list)
        for line in f:
            r = json.loads(line)
            by_tick[r["t"]].append(
                (r["id"], r["tn"], r["tier"], r["size"], r.get("bound", DEFAULT_BOUND)))
    return config, by_tick


def cmd_run(args):
    config, by_tick = load_workload(args.workload)
    capacity, max_ticks = config["capacity"], config["max_ticks"]

    proc = subprocess.Popen(args.command, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, bufsize=1)
    w, r = proc.stdin, proc.stdout
    w.write("CONFIG " + json.dumps(config) + "\n")

    jobs = {}
    admit_tick = {}
    gate_failures = []
    waiting = set()
    waiting_units = 0
    waiting_units_by_tenant = defaultdict(int)
    used_total = 0
    demand_total = 0
    max_tick_seconds = 0.0
    prio_violations = 0
    jain_samples = []
    window = []

    controller_died = False
    for t in range(max_ticks):
        try:
            w.write(f"TICK {t}\n")
            for jid, tenant, tier, size, bound in by_tick.get(t, []):
                jobs[jid] = (tenant, tier, size, t, bound)
                waiting.add(jid)
                waiting_units += size
                waiting_units_by_tenant[tenant] += size
                extra = f" {bound}" if bound != DEFAULT_BOUND else ""
                w.write(f"ARRIVE {jid} {tenant} {tier} {size}{extra}\n")
            started = time.perf_counter()
            w.write("DONE\n")
            w.flush()
        except BrokenPipeError:
            gate_failures.append(f"tick {t}: controller exited (broken pipe)")
            controller_died = True
            break
        contending = {jobs[j][0] for j in waiting}

        picked = []
        while True:
            line = r.readline()
            if not line:
                gate_failures.append(f"tick {t}: controller exited mid-run"
                                     " (did you implement the protocol and flush stdout?)")
                controller_died = True
                break
            line = line.strip()
            if line == "END":
                break
            if line.startswith("ADMIT"):
                picked.extend(line.split()[1:])
        elapsed = time.perf_counter() - started
        max_tick_seconds = max(max_tick_seconds, elapsed)
        if elapsed > TICK_TIMEOUT_SECONDS:
            gate_failures.append(f"tick {t}: response took {elapsed:.2f}s (> {TICK_TIMEOUT_SECONDS}s)")
        if controller_died:
            break

        used = 0
        tier2_admitted = False
        for jid in picked:
            if jid not in jobs:
                gate_failures.append(f"tick {t}: admitted unknown job {jid}")
                continue
            if jid in admit_tick:
                gate_failures.append(f"tick {t}: admitted {jid} twice")
                continue
            if jobs[jid][3] > t:
                gate_failures.append(f"tick {t}: admitted {jid} before arrival")
            admit_tick[jid] = t
            waiting.discard(jid)
            waiting_units -= jobs[jid][2]
            waiting_units_by_tenant[jobs[jid][0]] -= jobs[jid][2]
            used += jobs[jid][2]
            if jobs[jid][1] == 2:
                tier2_admitted = True
        if used > capacity:
            gate_failures.append(f"tick {t}: admitted {used} > capacity {capacity}")
        used_total += used
        demand_total += min(capacity, waiting_units + used)

        if tier2_admitted:
            for jid in waiting:
                if jobs[jid][1] == 0 and jobs[jid][2] <= capacity - used:
                    prio_violations += 1
                    break

        free = capacity - used
        if waiting and free > 0 and any(jobs[j][2] <= free for j in waiting):
            gate_failures.append(
                f"tick {t}: idled {free} units while fitting work waited")

        for jid in picked:
            if jid in jobs:
                window.append((t, jobs[jid][0], jobs[jid][2]))
        window = [x for x in window if x[0] > t - JAIN_WINDOW_TICKS]
        if len(contending) > 1:
            # Fairness is against entitlement, not raw share: a tenant's fair
            # slice is min(its demand, capacity/n). Tenants given everything
            # they asked for are fully served even if their share was small.
            per_tenant = defaultdict(int)
            for _, tn, u in window:
                per_tenant[tn] += u
            window_cap = capacity * JAIN_WINDOW_TICKS
            n = len(contending)
            xs = []
            for tn in contending:
                got = per_tenant.get(tn, 0)
                demand = got + waiting_units_by_tenant.get(tn, 0)
                entitlement = min(demand, window_cap / n)
                if entitlement > 0:
                    xs.append(min(1.5, got / entitlement))
            if xs and sum(xs) > 0:
                jain_samples.append(sum(xs) ** 2 / (len(xs) * sum(x * x for x in xs)))

    try:
        w.write("FINISH\n")
        w.flush()
        proc.wait(timeout=15)
    except Exception:
        proc.kill()

    scorecard(jobs, admit_tick, gate_failures, prio_violations, jain_samples,
              used_total, demand_total, max_tick_seconds)


def scorecard(jobs, admit_tick, gate_failures, prio_violations, jain_samples,
              used_total, demand_total, max_tick_seconds):
    unadmitted = [j for j in jobs if j not in admit_tick]
    if unadmitted:
        gate_failures.append(f"{len(unadmitted)} jobs never admitted")

    waits = {j: admit_tick[j] - jobs[j][3] for j in admit_tick}
    starved = [j for j, wt in waits.items() if wt > jobs[j][4]]

    offered = defaultdict(int)
    for tn, _, size, _, _ in jobs.values():
        offered[tn] += size
    total_offered = sum(offered.values()) or 1
    judged = {tn for tn, u in offered.items()
              if u / total_offered <= JUDGED_TENANT_MAX_SHARE}

    per_tenant_waits = defaultdict(list)
    for j, wt in waits.items():
        per_tenant_waits[jobs[j][0]].append(wt)

    def p99(vals):
        if not vals:
            return 0
        vals = sorted(vals)
        return vals[min(len(vals) - 1, int(len(vals) * 0.99))]

    judged_p99 = max((p99(per_tenant_waits[tn]) for tn in judged
                      if tn in per_tenant_waits), default=0)
    global_p99 = p99(list(waits.values()))
    jain = sum(jain_samples) / len(jain_samples) if jain_samples else 1.0
    utilization = used_total / max(1, demand_total)

    score = 0.0
    if not gate_failures:
        score = 100.0
        score -= min(30.0, 0.5 * len(starved))
        score -= min(20.0, 2.0 * prio_violations)
        score -= (1.0 - jain) * 20.0
        score -= min(20.0, 0.5 * max(0, judged_p99 - JUDGED_P99_TARGET))
        score -= (1.0 - utilization) * 10.0
        score = max(0.0, round(score, 1))

    print("=== SCORECARD ===")
    if gate_failures:
        print(f"  GATES: FAIL ({gate_failures[0]}"
              + (f" +{len(gate_failures)-1} more)" if len(gate_failures) > 1 else ")"))
    else:
        print("  GATES: PASS")
    print(f"  score:             {score}")
    print(f"  jobs:              {len(jobs)} admitted={len(admit_tick)}")
    print(f"  starved:           {len(starved)} (wait > per-job bound)")
    print(f"  priority violations: {prio_violations}")
    print(f"  fairness (jain, {JAIN_WINDOW_TICKS}-tick windows): {jain:.4f}")
    print(f"  judged-tenant p99: {judged_p99} (target <= {JUDGED_P99_TARGET}; "
          f"judged = {sorted(judged)})")
    print(f"  global p99 wait:   {global_p99}")
    print(f"  utilization:       {100.0 * utilization:.1f}% of demand-capped capacity")
    print(f"  slowest tick:      {max_tick_seconds * 1000:.1f}ms "
          f"(limit {int(TICK_TIMEOUT_SECONDS * 1000)}ms)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--scenario", choices=sorted(SCENARIOS), default="public")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("-o", "--output", required=True)
    g.set_defaults(fn=cmd_gen)
    rn = sub.add_parser("run")
    rn.add_argument("--workload", required=True)
    rn.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- your-program [args]")
    rn.set_defaults(fn=cmd_run)
    args = ap.parse_args()
    if args.cmd == "run":
        args.command = [c for c in args.command if c != "--"]
        if not args.command:
            ap.error("run requires -- <your-program>")
    args.fn(args)


if __name__ == "__main__":
    main()