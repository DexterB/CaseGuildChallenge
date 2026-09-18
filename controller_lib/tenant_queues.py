"""Per-tenant FIFO run queues.

Invariant: if a tenant is inactive, its run queue is empty.
"""

from collections import defaultdict, deque


class TenantQueues:

    def __init__(self):
        self._runqueues = defaultdict(deque)
        self._active = set()

    def add(self, job):
        self._runqueues[job.tenant].append(job)
        self._active.add(job.tenant)

    def runqueue(self, tenant):
        return self._runqueues[tenant]

    def active_tenants(self):
        """Snapshot of currently active tenants, safe to iterate while mutating."""
        return list(self._active)

    def discard_tenant_if_empty(self, tenant):
        if not self._runqueues[tenant]:
            self._active.discard(tenant)

    def iter_all_jobs(self):
        """Jobs across all active tenants' queues, live w.r.t. tenant activity."""
        for tenant in self._active:
            yield from self._runqueues[tenant]

    def __bool__(self):
        return bool(self._active)
