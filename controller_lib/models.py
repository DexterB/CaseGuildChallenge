"""Job record and admission-control constants shared across the controller."""

from dataclasses import dataclass


DEFAULT_BOUND = 200
TIER_QUESTION, TIER_RESEARCH, TIER_UPLOAD = 0, 1, 2

# A job within this many ticks of its deadline is treated as urgent by the
# scheduler's candidate ordering.
URGENCY_WINDOW = 15


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
