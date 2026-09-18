import dataclasses

import pytest

from controller_lib.models import DEFAULT_BOUND, Job


def test_deadline_is_arrival_plus_bound():
    job = Job(job_id="j1", tenant="A", tier=0, size=5, arrived_at=10, bound=20)
    assert job.deadline == 30


def test_default_bound_applied_when_omitted():
    job = Job(job_id="j1", tenant="A", tier=0, size=5, arrived_at=10)
    assert job.bound == DEFAULT_BOUND
    assert job.deadline == 10 + DEFAULT_BOUND


def test_job_is_immutable():
    job = Job(job_id="j1", tenant="A", tier=0, size=5, arrived_at=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.size = 10
