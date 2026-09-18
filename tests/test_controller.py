"""End-to-end tests driving controller.main() over the stdin/stdout wire protocol."""

import io
import sys

import controller


def run_controller(input_text):
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(input_text)
    sys.stdout = io.StringIO()
    try:
        controller.main()
        return sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout


def test_empty_input_produces_no_output():
    assert run_controller("") == ""


def test_single_tick_admits_fitting_jobs():
    output = run_controller(
        'CONFIG {"capacity": 10, "max_ticks": 100}\n'
        "TICK 0\n"
        "ARRIVE j1 tenantA 0 5\n"
        "DONE\n"
    )
    assert output == "ADMIT j1\nEND\n"


def test_raw_json_config_line_is_accepted():
    output = run_controller(
        '{"config": {"capacity": 10, "max_ticks": 100}}\n'
        "TICK 0\n"
        "ARRIVE j1 tenantA 0 5\n"
        "DONE\n"
    )
    assert output == "ADMIT j1\nEND\n"


def test_capacity_overflow_defers_job_to_next_tick():
    output = run_controller(
        'CONFIG {"capacity": 5, "max_ticks": 100}\n'
        "TICK 0\n"
        "ARRIVE j1 tenantA 0 5\n"
        "ARRIVE j2 tenantA 0 5\n"
        "DONE\n"
        "TICK 1\n"
        "DONE\n"
    )
    assert output == "ADMIT j1\nEND\nADMIT j2\nEND\n"


def test_single_tenant_queue_drains_across_rounds_without_idling_capacity():
    # select_admissions rescans for a fitting job every round, so a single
    # tenant with multiple jobs still gets fully drained in round order
    # (head-of-queue first) as long as each job fits the capacity left over.
    output = run_controller(
        'CONFIG {"capacity": 10, "max_ticks": 100}\n'
        "TICK 0\n"
        "ARRIVE big tenantA 0 8\n"
        "ARRIVE small tenantA 0 2\n"
        "DONE\n"
    )
    assert output == "ADMIT big small\nEND\n"


def test_blank_line_between_ticks_ends_the_program():
    # A missing/blank TICK line (EOF or protocol end) terminates the loop
    # without emitting further output.
    output = run_controller(
        'CONFIG {"capacity": 10, "max_ticks": 100}\n'
        "TICK 0\n"
        "ARRIVE j1 tenantA 0 5\n"
        "DONE\n"
        "\n"
    )
    assert output == "ADMIT j1\nEND\n"
