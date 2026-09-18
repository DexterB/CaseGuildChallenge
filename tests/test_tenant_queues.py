from controller_lib.models import Job
from controller_lib.tenant_queues import TenantQueues


def make_job(job_id, tenant, tier=0, size=1, arrived_at=0, bound=200):
    return Job(job_id=job_id, tenant=tenant, tier=tier, size=size,
               arrived_at=arrived_at, bound=bound)


def test_add_appends_to_tenant_queue_and_marks_active():
    queues = TenantQueues()
    job = make_job("j1", "A")

    queues.add(job)

    assert list(queues.runqueue("A")) == [job]
    assert queues.active_tenants() == ["A"]
    assert bool(queues)


def test_runqueue_preserves_fifo_order():
    queues = TenantQueues()
    j1, j2, j3 = make_job("j1", "A"), make_job("j2", "A"), make_job("j3", "A")

    queues.add(j1)
    queues.add(j2)
    queues.add(j3)

    assert list(queues.runqueue("A")) == [j1, j2, j3]


def test_empty_tenant_has_no_active_entry():
    queues = TenantQueues()
    assert queues.active_tenants() == []
    assert not bool(queues)
    assert list(queues.runqueue("nobody")) == []


def test_discard_tenant_if_empty_removes_only_when_queue_drained():
    queues = TenantQueues()
    job = make_job("j1", "A")
    queues.add(job)

    queues.discard_tenant_if_empty("A")
    assert "A" in queues.active_tenants()

    queues.runqueue("A").remove(job)
    queues.discard_tenant_if_empty("A")
    assert queues.active_tenants() == []
    assert not bool(queues)


def test_active_tenants_is_a_safe_snapshot():
    queues = TenantQueues()
    queues.add(make_job("j1", "A"))
    queues.add(make_job("j2", "B"))

    snapshot = queues.active_tenants()
    # Mutating the live active set after taking a snapshot must not affect it
    # or raise (this is what makes "for tenant in queues.active_tenants()"
    # safe to iterate while draining tenants).
    queues.runqueue("A").pop()
    queues.discard_tenant_if_empty("A")

    assert sorted(snapshot) == ["A", "B"]
    assert queues.active_tenants() == ["B"]


def test_iter_all_jobs_covers_only_active_tenants():
    queues = TenantQueues()
    j1 = make_job("j1", "A")
    j2 = make_job("j2", "B")
    queues.add(j1)
    queues.add(j2)

    assert sorted(job.job_id for job in queues.iter_all_jobs()) == ["j1", "j2"]

    queues.runqueue("A").remove(j1)
    queues.discard_tenant_if_empty("A")

    assert [job.job_id for job in queues.iter_all_jobs()] == ["j2"]
