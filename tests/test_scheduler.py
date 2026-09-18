"""Scheduler tests.

Note: TenantQueues.active_tenants() iterates a Python set, so when two
candidates from *different* tenants tie on every sort key, or when
idle_fill's un-sorted per-tenant scan touches more than one tenant, admit
order is not guaranteed. Test cases are built with distinct sizes/deadlines
across tenants (so select_admissions's sort fully determines order) and
single-tenant setups for idle_fill's priority-guard checks, so results are
deterministic regardless of set iteration order.
"""

from controller_lib.models import Job, TIER_QUESTION, TIER_RESEARCH, TIER_UPLOAD
from controller_lib.scheduler import idle_fill, select_admissions
from controller_lib.tenant_queues import TenantQueues


def make_job(job_id, tenant, tier=0, size=1, arrived_at=0, bound=200):
    return Job(job_id=job_id, tenant=tenant, tier=tier, size=size,
               arrived_at=arrived_at, bound=bound)


def test_select_admissions_picks_one_best_candidate_per_round():
    queues = TenantQueues()
    # tenant A has two queued jobs; only the head should ever be considered
    # per round, and tier priority beats intra-tenant queue order.
    queues.add(make_job("a1", "A", tier=TIER_RESEARCH, size=4))
    queues.add(make_job("a2", "A", tier=TIER_RESEARCH, size=4))
    queues.add(make_job("b1", "B", tier=TIER_QUESTION, size=4))

    admitted, remaining = select_admissions(queues, capacity=8, current_tick=0)

    assert admitted == ["b1", "a1"]
    assert remaining == 0
    # a2 was never a round candidate (behind a1 in A's queue) so it's deferred.
    assert [j.job_id for j in queues.runqueue("A")] == ["a2"]


def test_select_admissions_stops_when_nothing_fits():
    queues = TenantQueues()
    queues.add(make_job("a1", "A", size=50))

    admitted, remaining = select_admissions(queues, capacity=10, current_tick=0)

    assert admitted == []
    assert remaining == 10
    assert [j.job_id for j in queues.runqueue("A")] == ["a1"]


def test_select_admissions_tie_break_by_size():
    queues = TenantQueues()
    queues.add(make_job("a1", "A", tier=0, size=5, bound=200))
    queues.add(make_job("b1", "B", tier=0, size=3, bound=200))

    admitted, remaining = select_admissions(queues, capacity=10, current_tick=0)

    # Same urgency bucket, same tier, same deadline -> smaller size wins first.
    assert admitted == ["b1", "a1"]
    assert remaining == 2


def test_select_admissions_tie_break_by_deadline():
    queues = TenantQueues()
    queues.add(make_job("a1", "A", tier=0, size=5, arrived_at=0, bound=300))
    queues.add(make_job("b1", "B", tier=0, size=5, arrived_at=0, bound=200))

    admitted, remaining = select_admissions(queues, capacity=10, current_tick=0)

    # Same urgency bucket, same tier, same size -> shorter deadline wins first.
    assert admitted == ["b1", "a1"]
    assert remaining == 0


def test_select_admissions_urgency_overrides_tier():
    queues = TenantQueues()
    # Upload (low priority tier) but deadline is within the urgency window.
    queues.add(make_job("urgent", "A", tier=TIER_UPLOAD, size=5, arrived_at=0, bound=10))
    # Question (high priority tier) but far from its deadline.
    queues.add(make_job("normal", "B", tier=TIER_QUESTION, size=5, arrived_at=0, bound=200))

    admitted, remaining = select_admissions(queues, capacity=10, current_tick=0)

    assert admitted == ["urgent", "normal"]
    assert remaining == 0


def test_select_admissions_drains_tenant_and_deactivates_it():
    queues = TenantQueues()
    queues.add(make_job("a1", "A", size=5))

    select_admissions(queues, capacity=10, current_tick=0)

    assert queues.active_tenants() == []
    assert not bool(queues)


def test_idle_fill_admits_smaller_jobs_in_fifo_order_until_capacity_exhausted():
    queues = TenantQueues()
    queues.add(make_job("j1", "A", tier=TIER_RESEARCH, size=3))
    queues.add(make_job("j2", "A", tier=TIER_RESEARCH, size=3))
    queues.add(make_job("j3", "A", tier=TIER_RESEARCH, size=3))

    admitted, remaining = idle_fill(queues, remaining_capacity=7)

    assert admitted == ["j1", "j2"]
    assert remaining == 1
    assert [j.job_id for j in queues.runqueue("A")] == ["j3"]


def test_idle_fill_skips_upload_when_question_job_would_also_fit():
    queues = TenantQueues()
    queues.add(make_job("up1", "A", tier=TIER_UPLOAD, size=5))
    queues.add(make_job("q1", "A", tier=TIER_QUESTION, size=3))

    admitted, remaining = idle_fill(queues, remaining_capacity=10)

    assert admitted == ["q1"]
    assert remaining == 7
    assert [j.job_id for j in queues.runqueue("A")] == ["up1"]


def test_idle_fill_admits_upload_when_no_question_job_fits():
    queues = TenantQueues()
    queues.add(make_job("up1", "A", tier=TIER_UPLOAD, size=5))

    admitted, remaining = idle_fill(queues, remaining_capacity=10)

    assert admitted == ["up1"]
    assert remaining == 5
    assert queues.active_tenants() == []


def test_idle_fill_stops_exactly_at_zero_remaining_capacity():
    queues = TenantQueues()
    queues.add(make_job("j1", "A", size=4))
    queues.add(make_job("j2", "A", size=4))
    queues.add(make_job("j3", "A", size=4))

    admitted, remaining = idle_fill(queues, remaining_capacity=8)

    assert admitted == ["j1", "j2"]
    assert remaining == 0
