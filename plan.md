# Plan

## Failure modes of the naive approach

### Core Requirements

Recall the admission controller guarantees:

1. All jobs must make forward progress.
2. Interactive processing or queries beat any batch processing job.
3. Nothing waits past its limit (or _deadline_).
4. When work is waiting, machines should always be at maximum possible utilization.

### Resource Constraints

LLM providers limit how fast resources may be consumed, capping by model and tokens per minute.
The rate cap is a limited resource shared by all machines, with the following two properties shaping consumption:

- Under backlog, unused cap translates to pure waiting time.
- Caps are not elastic and cannot be grown on demand.


### Why do obvious schedulers fail?

The challenge correctly outlines that the obvious scheduling mechanisms, FIFO, round robin (RR), and strict
priority (PRI) are not enough to satisfy controller guarantees under the resource constraints described above.
The following outlines the failure modes for each class of scheduler.

#### FIFO

A pure FIFO scheduling fails in this system because it is inherently non-preemptive, blind to duration, and
agnostic to deadlines. Given the heterogeneous workload of varying ingestion jobs, interactive tasks, and batch
jobs, FIFO introduces the following algorithmic-structural failures:

1. The Convoy Effect Destroys Interactive Latency
Long-running batch jobs or large document ingestion tasks arriving ahead of a short-duration interactive query can force the interactive job to wait behind the larger jobs and drive it to expiry. Interactive response time becomes unpredictable and spikes from milliseconds to orders of magnitude longer, violating basic SLA and responsiveness requirements.

2. Deadline Agnosticism
FIFO orders jobs purely by arrival time, ignoring both remaining queue time and execution time. A long batch job submitted ahead of an ingestion job with a much earlier deadline can cause the ingestion job to miss its deadline simply because it was queued later.

3. Head-of-Queue Blocking within Ingestion
Ingestion workloads may consist of large and small numbers of documents of various sizes. Under FIFO, a single job ingesting, say, 1,000,000 documents will block hundreds of subsequent jobs ingesting single-page documents, even though the smaller jobs could complete in much smaller time slices with minimal overhead.

4. Failure to Guarantee Forward Progress
FIFO ensures the eventual completion of queued jobs, but it halts forward progress for all pending tasks while any single long-running or stalled job executes. If a long-running job blocks on an unpipelined external resource or performs heavy sequential processing without time-slicing, the entire system pipeline stalls behind it.

5. Inefficient Resource Utilization
Interactive and ingestion jobs frequently yield to wait on network or disk I/O, or database commits. In a non-preemptive FIFO model, when an active job blocks on I/O, CPU or LLM token consumption drops to zero, preventing other tasks from using both CPU and token resources.

#### Round Robin (RR)

Pure RR guarantees forward progress and prevents starvation, but it fails in multi-class systems with diverse service level objectives (SLOs) because it is agnostic to deadlines, heterogeneity, and costs.

1. Deadlines are Completely Ignored
RR operates strictly in FIFO queue order within a circular ring and inherits all the problems of FIFO.

2. Convoy Effect from Large Ingestion
Ingestion tasks may vary in computation and I/O density. If a large load or heavy ingestion jobs enter a run queue, the time required to compute one full cycle of the ring expands dramatically. Interactive jobs suffer high-tail latency directly proportional to the total number of active jobs, not the work they actually need done.

3. Quantum Size Conundrum
Small quanta give responsive interactive time, but increase context-switching overhead, TLB reconstruction, and cache thrashing. As a consequence, this overhead destroys throughput for long-running batch and bulk ingestion jobs—an inadvertent noisy-neighbor effect. On the other hand, large quantum sizes amortize switching costs but turn the system into an effective FIFO scheduler and all the problems that come with it.

4. I/O Asymmetry and Deadline Blindness
Pure RR treats all jobs as compute-bound threads. Interactive and ingestion jobs spend much of their time waiting on network and/or disk I/O. When an I/O-bound job yields before its quantum expires, plain RR advances to the next job without compensating the yielded job or considering the remaining deadline. Over time, compute-heavy batch jobs monopolize effective CPU cycles, penalizing I/O-heavy jobs.

5. Admission Blindness
Pure RR, like PRI below, does not assess a job's feasibility. Every enqueued job continues to receive time slices, but jobs may still miss their deadlines because resources are diluted equally rather than allocated strategically to meet contracted completion deadlines.

##### Strict Priority (PRI)

Priority scheduling solves RR's issue of treating every job identically by scheduling the most important job first. However, using priority scheduling alone fails for multi-class, deadline-driven systems due to the following flaws:

1. Starvation of Lower Tiers
Lower priority queues run only when higher-priority queues are empty. If interactive traffic surges or a continuous stream of ingestion jobs arrives, the batch class receives zero compute resources. Priority queues need auxiliary mechanisms like dynamic aging to prevent batch jobs from freezing indefinitely and prevent violations of the core guarantee that all jobs must make forward progress.

2. Deadlines are not Priorities
Static priority ranks importance but not job urgency. A high-priority interactive task with a loose deadline could always preempt a medium- or low-priority ingestion or batch job whose deadline expires in milliseconds. With static priority only, we cannot reason about temporal constraints or lead times, leading to deadline blindness and misses across all classes. 

3. Priority Inversion Across Shared Resources
All job classes contend for shared resources such as CPU, GPU, storage I/O, network bandwidth, shared memory buffers, and database locks. If a low-priority batch job acquires a lock, saturates the storage controller's queue depth, or saturates PCIe lanes, higher-priority interactive jobs can block on the same resource and stall behind a lower-priority workload—a priority inversion. This implies a scheduler capable of priority inheritance or a capping protocol to reverse the inversion.

4. FIFO Trap
When multiple jobs in the same class share the same priority level, the priority scheduler schedules them FIFO and inherits all the FIFO problems mentioned above. Even if the scheduler uses a simple time-slice among jobs in the same class, it inherits the RR quantum size conundrum and ignores internal deadlines.

5. Admission Blindness
PRI lacks an admission control model to determine a job's resource feasibility. A sudden burst of high-priority interactive jobs can saturate compute, bandwidth, memory, and database resources when the scheduler tries to run them all. Overcommitted capacity spikes context switches, collapses throughput, and may force all interactive tasks to miss their target deadlines. Admission control, or load shedding, could ameliorate these effects.


## Design

Because all jobs must make progress regardless of the job mix, time slices become indispensable.
Time slices are the only mechanism that allows multiple job classes to interleave on finite resources
(cores, memory, I/O bandwidth, etc.) without starving slower or longer jobs. However, as shown in
the previous section, _static time slices_ are not enough, and the system will require load shedding with a dynamic time-slicing
scheme parameterized by weights, deadlines, and workload profiles.

## Trade-offs

## Trade-offs
Given the design outlined above and the limitations of the _obvious_ FIRO, RR, and PRI mechanisms, the following outlines the trade-offs incurred by a multi-class time-sliced scheduler. Without time-slicing, this multi-class scheduling problem reduces to a variant of the bin packing problem and becomes NP-hard. However, time-slicing has some of its own practical issues.

| Trade-Off | Benefit Gained | Cost, Penalties & Bottlenecks |
| :--- | :--- | :--- |
| **Context Switching & Locality** | Guarantees bounded latency for interactive tasks and continuous forward progress across all jobs. | Degrades bulk ingestion and batch throughput due to frequent preemption; bottlenecks arise from CPU L1/L2 cache invalidations, TLB flushes, and kernel context-switch CPU overhead. |
| **Algorithmic Dispatch Complexity** | Eliminates queue starvation and dynamically adapts execution budgets to workload deadlines and weights. | Eliminates $O(1)$ fast-path scheduling by consuming cycles inside the critical loop; bottlenecks include $O(\log N)$ runqueue tree/heap rebalancing, lock contention, and cross-thread cache line ping-pong. |
| **Workload Chunking & Interruption** | Prevents head-of-line blocking by splitting large document ingestion tasks into preemptible slices. | Demands application-level checkpointing and intermediate state management; incurs serialization overhead, metadata tracking storage, and potential priority inversion if tasks yield while holding lock primitives. |
| **Non-Compute (CPU & GPU) Resource Contention** | Interleaves multiple workloads fairly across CPU and GPU compute cores. | Fails preempt non-compute subsystems cleanly; risks buffer pool cache eviction, memory bus saturation, cascaded page faults, and storage controller queue depth exhaustion from concurrent batch tasks. |
| **Load Shedding & Admission Control** | Protects system stability and guarantees admitted jobs meet committed SLAs without cascading deadline collapses. | Breaks client-side fire-and-forget submission patterns with non-deterministic rejections; requires upstream backoff queues, retry logic, and complex triage policies on fallbacks and deciding which job class to drop. |

## Extending to a Multi-tenant Design
The design outlined above is a single-tenant design for the following reasons:

1. Workload-Centric rather than Tenant-Centric
Parameterization focuses entirely on job attributes: weights, deadlines, and workload characteristics rather than tenant identity, larger macro-contractual SLAs, or organizational isolation boundaries.

2. Global Resource Interleaving
Multiple job classes are interleaved across finite system resources to avoid starving slower or longer jobs. In a multi-tenant system, starvation prevention must be enforced first across tenants, not just job classes.

3. 1-Level Load Shedding Mechanics
Load shedding is based simply on global weights and deadlines and operates on a single shared budget. A multi-tenant model would require a two-level load-shedding policy—shedding within the offending tenant's quota first, rather than rejecting jobs across a shared global admission pool.

To promote the current design to a multi-tenant design, the dynamic time-slicing and admission scheme would need to be tweaked as follows.

1. Hierarchical Scheduling
As a simple first pass, introduce a root-level scheduler that statically partitions finite resource capacity (CPU, GPU, I/O, memory bandwidth, buffer pools) among tenants using quotas or a weighting scheme. A second level would apply the class-based dynamic scheduling introduced here within each tenant's partition.

2. Tenant-Scoped Admission (Load Shedding)
Admission rejections enforce per-tenant capacity caps, isolating one tenant's load surge from other tenants' jobs. Effectively, there are no _noisy neighbors_ across tenants.

3. Hard Non-Compute Subsystem Isolation
Tenant quotas on I/O bandwidth, memory bandwidth, buffer pool memory, and database connection pools to prevent _noisy neighbor_ effects across tenant boundaries.

                                       [ Root Scheduler ]
                                      /                  \
                                     /                    \
                                    /                      \
                                   /                        \
                       [ Tenant A: 75% ]                [ Tenant B: 25% ]
                        /     |     \                    /     |     \
                       /      |      \                  /      |      \
                      /       |       \                /       |       \
                     /        |        \              /        |        \
            [Interactive] [Ingestion] [Batch] [Interactive] [Ingestion] [Batch]


## Beyond one process (Scaling the Controller to Many Processes on Many Machines)

The key to extending the design to many processes running on an unlimited number of machines is to externalize all in-memory state to separate non-volatile state or consensus-based services. For example, scheduler state consisting of active runqueues, Red-black trees, and virtual-runtime clock accumulators would need to be maintained by separate reliable databases. The same holds for execution-coordination metadata, including worker leasing and admission accounting. The stateless scheduler will operate purely as a set of idempotent, compute-only execution engines acting on state retrieved via persistent tokens or fetched from a low-latency data plane.

The following diagram drafts a possible architectural separation into configurable layers with K8S orchestration.

```text
+-------------------------------------------------------------+
|                     Clients / Ingestion                     |
+-------------------------------------------------------------+
                              │
                              ▼
+-------------------------------------------------------------+
|                  Stateless Scheduler Nodes                  |
|         (Horizontal scaling, no local memory state)         |
+-------------------------------------------------------------+
               │                               │
               ▼                               ▼
+-----------------------------+ +-----------------------------+
|         State Store         | |       Task / Queue Log      |
|    (Redis / KV / Memory)    | |    (Sorted Sets / Leases)   |
+-----------------------------+ +-----------------------------+
               ▲                               ▲
               │                               │
               └──────────────┬────────────────┘
                              │
                              ▼
+-------------------------------------------------------------+
|                     Worker Pool / Cores                     |
|        (Pulls leases, executes quanta, reports yield)       |
+-------------------------------------------------------------+

