import io

from controller_lib.models import DEFAULT_BOUND
from controller_lib.protocol import parse_arrive, parse_config, read_arrivals, write_admissions
from controller_lib.tenant_queues import TenantQueues


def test_parse_config_handles_harness_prefixed_line():
    config = parse_config('CONFIG {"capacity": 100, "max_ticks": 3000}')
    assert config == {"capacity": 100, "max_ticks": 3000}


def test_parse_config_handles_workload_wrapper():
    config = parse_config('{"config": {"capacity": 100, "max_ticks": 3000}}')
    assert config == {"capacity": 100, "max_ticks": 3000}


def test_parse_config_handles_raw_flat_json():
    config = parse_config('{"capacity": 100, "max_ticks": 3000}')
    assert config == {"capacity": 100, "max_ticks": 3000}


def test_parse_arrive_with_explicit_bound():
    job = parse_arrive(["ARRIVE", "j1", "tenantA", "1", "5", "50"], current_tick=7)
    assert job.job_id == "j1"
    assert job.tenant == "tenantA"
    assert job.tier == 1
    assert job.size == 5
    assert job.arrived_at == 7
    assert job.bound == 50


def test_parse_arrive_defaults_bound():
    job = parse_arrive(["ARRIVE", "j1", "tenantA", "0", "5"], current_tick=0)
    assert job.bound == DEFAULT_BOUND


def test_read_arrivals_ingests_until_done():
    stdin = io.StringIO(
        "ARRIVE j1 tenantA 0 5\n"
        "ARRIVE j2 tenantB 1 3 40\n"
        "DONE\n"
    )
    queues = TenantQueues()

    read_arrivals(stdin, current_tick=3, queues=queues)

    assert [j.job_id for j in queues.runqueue("tenantA")] == ["j1"]
    assert [j.job_id for j in queues.runqueue("tenantB")] == ["j2"]
    assert queues.runqueue("tenantB")[0].bound == 40


def test_read_arrivals_skips_blank_and_unrecognized_lines():
    stdin = io.StringIO(
        "\n"
        "NOISE something\n"
        "ARRIVE j1 tenantA 0 5\n"
        "DONE\n"
    )
    queues = TenantQueues()

    read_arrivals(stdin, current_tick=0, queues=queues)

    assert queues.active_tenants() == ["tenantA"]


def test_write_admissions_with_jobs():
    stdout = io.StringIO()

    write_admissions(stdout, ["j1", "j2"])

    assert stdout.getvalue() == "ADMIT j1 j2\nEND\n"


def test_write_admissions_without_jobs():
    stdout = io.StringIO()

    write_admissions(stdout, [])

    assert stdout.getvalue() == "END\n"
