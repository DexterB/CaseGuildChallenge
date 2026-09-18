"""Admission-control policy: fair-share scheduling plus idle-capacity filling."""

from controller_lib.models import TIER_QUESTION, TIER_UPLOAD, URGENCY_WINDOW


def select_admissions(queues, capacity, current_tick):
    """Round-based fair-share admission across tenants.

    Each round collects one fitting candidate job per active tenant, then
    admits the single best candidate ranked by:
    1. Urgent deadlines nearing starvation bounds
    2. Tier priority (TIER_QUESTION > TIER_RESEARCH > TIER_UPLOAD)
    3. Shortest deadline
    4. Smallest size
    """
    admitted = []
    remaining_capacity = capacity

    while remaining_capacity > 0 and queues.active_tenants():
        candidates = []
        for tenant in queues.active_tenants():
            for job in queues.runqueue(tenant):
                if job.size <= remaining_capacity:
                    candidates.append(job)
                    break

        if not candidates:
            break

        candidates.sort(key=lambda job: (
            0 if job.deadline - current_tick < URGENCY_WINDOW else 1,
            job.tier,
            job.deadline,
            job.size,
        ))

        chosen = candidates[0]
        admitted.append(chosen.job_id)
        remaining_capacity -= chosen.size
        queues.runqueue(chosen.tenant).remove(chosen)
        queues.discard_tenant_if_empty(chosen.tenant)

    return admitted, remaining_capacity


def idle_fill(queues, remaining_capacity):
    """Admit jobs to fill any remaining capacity, maximizing utilization.

    Skips a tier-2 (upload) job if a tier-0 (question) job elsewhere would
    also fit, to avoid a priority violation.
    """
    admitted = []

    for tenant in queues.active_tenants():
        runqueue = queues.runqueue(tenant)
        to_remove = []
        for job in runqueue:
            if job.size <= remaining_capacity:
                if job.tier == TIER_UPLOAD and any(
                    j.tier == TIER_QUESTION and j.size <= remaining_capacity
                    for j in queues.iter_all_jobs()
                ):
                    continue

                admitted.append(job.job_id)
                remaining_capacity -= job.size
                to_remove.append(job)
                if remaining_capacity == 0:
                    break

        for job in to_remove:
            runqueue.remove(job)
        queues.discard_tenant_if_empty(tenant)
        if remaining_capacity == 0:
            break

    return admitted, remaining_capacity
