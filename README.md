# Admission Controller Challenge

## The scenario

CaseGuild is a multi-tenant system for legal teams. Each tenant is a firm. Each
tenant has many cases. Each case has multiple users working in it. Almost
everything users do turns into LLM calls.

There are three kinds of jobs:

1. **Upload** — a user drops documents into a case. Sometimes thirty documents,
   sometimes a hundred thousand. Nobody sits and watches an upload finish, but
   it must make steady, visible progress — a stalled upload is a support ticket.
2. **Investigation (question-answer)** — a user asks a question and is looking
   at a spinner right now. This work is small, and it is urgent, because a
   person is waiting on it.
3. **Deep research** — a user deliberately kicks off a long investigation and
   comes back later. Bigger than a question, more patient than a spinner, less
   patient than an upload.

We do not control how this work arrives. One firm may drop a hundred-thousand-
document upload at the same moment another firm's attorney asks a single
question. Both firms are paying customers. Both are owed an answer.

## The scarce resource

All of this work runs through LLM providers. We pay as we go, per million
tokens — idle time costs nothing. But every provider limits how *fast* we may
consume: **tokens per minute, capped per model**. That rate cap is the machine
everyone shares, and it has two properties that shape everything:

- **Under a backlog, unused cap is pure waiting time.** Tokens we could have
  spent this minute but didn't are gone — and every one of them is a customer
  waiting longer for no reason. The waste is latency, not money.
- **The cap cannot be grown on demand.** Workers can scale; the rate limit
  cannot. So the only real control point is the front door: deciding, moment
  by moment, whose work is admitted next.

## What your controller must guarantee

Maximize work through the cap while keeping four promises:

1. **Everything makes progress.** Every tenant keeps moving, no matter what
   anyone else submitted. Submitting more work does not buy a bigger share.
2. **A person waiting beats a batch running.** Question-answer work is small
   and someone is watching it. It goes first — but "first" must never mean
   uploads and research stop moving.
3. **Nothing waits past its limit.** Every job has a bound on how long it may
   sit in the queue. Different job types have different patience — the bound
   arrives with the job (see protocol). The bound is a contract, not a hope.
4. **When work is waiting, keep the machine full.** If admissible work exists
   and there is free room under the cap, that room must not sit idle. Every
   promise above will tempt you to idle; find the design that keeps the
   promises without paying that price.

Keeping the machine full is easy. Keeping the promises is easy. Doing both at
once is the job.

## The obvious answer is not enough

Whatever your first idea is — FIFO, round robin, strict priority — production
experience taught us it will not survive this problem. Part of your
deliverable: your plan must name the approach you started from, the failure
modes you found in it, and the mechanism that closes each one.

## How the harness models this

- A **tick** is one scheduling window; **capacity** per tick is the token cap
  for that window.
- A **job's size** is its token cost, in abstract units. Sizes never exceed
  capacity.
- **Jobs are atomic.** A job is admitted whole, in one tick. It cannot be
  split across ticks.
- **A large upload appears as many jobs** — its shards — often all arriving in
  the same tick from the same tenant.
- **Tenants** are firms. Cases and users are collapsed into tenants in this
  version — your plan should say how your design would extend to them.
- **Priority tiers** are the job types: tier 0 = question-answer,
  tier 1 = deep research, tier 2 = upload.
- **Bounds differ by patience.** The default wait bound is 200 ticks. Jobs
  whose patience differs carry their own bound as the final ARRIVE field —
  uploads are patient (large bound), questions are not.

Each promise has a scorecard metric that measures it:

| Promise | Scorecard metric |
|---|---|
| Everything makes progress | fairness index (Jain, against each tenant's fair entitlement) |
| A person waiting beats a batch running | priority violations (upload admitted while a fitting question waits) |
| Nothing waits past its limit | starvation count (jobs whose wait exceeded their bound) |
| When work is waiting, keep the machine full | utilization, plus the idle gate |

## Protocol

Your program talks to the harness over stdin/stdout, line by line. You cannot
see the future: each tick's arrivals are revealed only after you have responded
to the previous tick.

```
harness -> you:   CONFIG {"capacity": 100, "max_ticks": 3000}
harness -> you:   TICK 0
harness -> you:   ARRIVE <job_id> <tenant> <tier> <size> [<bound>]   (zero or more)
harness -> you:   DONE
you -> harness:   ADMIT <job_id> <job_id> ...                        (zero or more lines)
you -> harness:   END
                  ... repeats each tick ...
harness -> you:   FINISH
```

- `size` is token units. The sum of sizes you ADMIT in one tick must not exceed
  `capacity`.
- `bound` is the job's wait limit in ticks. It is present only when it differs
  from the default of 200. **Your parser must tolerate its presence and its
  absence.**
- You may admit any job that has arrived and is not yet admitted — including
  jobs from earlier ticks.
- Respond to every tick with at least `END` within 2 seconds. The limit is
  generous relative to the reference solution (which runs >100x under it on the
  largest workloads); you are only at risk if your per-tick work scales with
  total backlog size.
- Flush stdout after `END` or the harness will hang.

Hard correctness gates (instant fail): admitting a job before it arrives,
admitting a job twice, exceeding per-tick capacity, or leaving jobs unadmitted
at the end. Every generated workload is feasible: all jobs can be admitted
within their bounds by a sufficiently good controller, except where a workload
is explicitly documented as an overload-triage case.

## Running locally

```
python3 harness.py gen --scenario public --seed 42 -o public.jsonl
python3 harness.py run --workload public.jsonl -- python3 controller.py
```

`run` prints a scorecard: correctness gates, fairness index, starvation events,
priority violations, per-tenant waits, and utilization. Read `harness.py` —
including the scoring function — it is allowed and encouraged.

The public scenario is representative but not exhaustive. Hidden workloads are
considerably more adversarial and exercise cases the public one does not.
Design for the promises, not the sample.

## Deliverables

1. **plan.md** — one page, written *before* the implementation. The failure
   modes of the naive approach you are defending against, the mechanism that
   closes each one, and the trade-offs you accepted. This is how we build at
   CaseGuild: think first, then aim the AI.
2. **controller.py** (or any language available in this environment) — your
   controller.
3. **scorecard.txt** — the output of your final local `harness.py run` against
   the public workload, committed to the repo.

Use AI freely — any tool you like; we are interested in how you drive it and
what you catch it getting wrong. Budget 1-2 hours, and note your actual time
spent at the top of plan.md.

## Submitting

Reply to the email that sent you this challenge with either a link to a
private GitHub repo (invite the sender) or a zip of your working directory.
Commit as you go if you can — history is part of the submission.

Include your AI session alongside the code, in a `session/` directory:

- **Claude Code**: copy the session file(s) for this project from
  `~/.claude/projects/<this-project-dir>/*.jsonl`.
- **Cursor / ChatGPT / other**: your tool's chat export, or a share link in
  `session/LINK.md`.
- **If your tool cannot export**: write `session/SESSION-NOTES.md` — the key
  prompts you used and anything the AI got wrong that you had to catch.

Feel free to review the session files and redact anything personal (paths,
unrelated project names) before sending. We read the session to understand how
you worked.
