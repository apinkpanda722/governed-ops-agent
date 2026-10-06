"""Internal mock CLI wrapper tests; every command uses an injected fake runner."""

import json
import subprocess
import traceback

import pytest

from govops.evalrun.mock_admin import MockAdmin, MockAdminError, subprocess_runner


class FakeRunner:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    def __call__(self, command, *, cwd):
        self.calls.append((command, cwd))
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def output(value, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(value), "private-stderr")


@pytest.mark.parametrize(
    ("method", "options", "suffix", "expected"),
    [
        ("reset", {}, ["reset"], None),
        ("state", {}, ["state"], {"jira": {"comments": []}, "slack": {"channels": []}}),
        (
            "state",
            {"bodies": True},
            ["state", "--bodies"],
            {"jira": {"comments": []}, "slack": {"channels": []}},
        ),
        ("faults_clear", {}, ["faults", "clear"], None),
        (
            "faults_stats",
            {},
            ["faults", "stats"],
            {"jira": {"comments": []}, "slack": {"channels": []}},
        ),
    ],
)
def test_commands_and_combined_results(tmp_path, method, options, suffix, expected):
    runner = FakeRunner(output({"comments": []}), output({"channels": []}))
    admin = MockAdmin(tmp_path, runner=runner)
    assert getattr(admin, method)(**options) == expected
    assert runner.calls == [
        (
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/docker-compose.yml",
                "exec",
                "-T",
                service,
                "python",
                "-m",
                "govops.mocks.admin",
                *suffix,
            ],
            tmp_path,
        )
        for service in ("mock-jira", "mock-slack")
    ]


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (output({"private-body": True}, 1), "command_failed"),
        (
            subprocess.CompletedProcess([], 0, "private-stdout", "private-stderr"),
            "invalid_output",
        ),
        (output([]), "invalid_output"),
        (OSError("private-exception"), "command_failed"),
        (
            subprocess.TimeoutExpired("private-command", 1, output="private-stdout"),
            "command_failed",
        ),
    ],
)
def test_errors_do_not_expose_output_or_exception(tmp_path, result, code):
    with pytest.raises(MockAdminError) as caught:
        MockAdmin(tmp_path, runner=FakeRunner(result)).state()
    assert caught.value.code == code
    assert str(caught.value) == code
    rendered = "".join(traceback.format_exception(caught.value))
    assert "private-" not in rendered


@pytest.mark.parametrize(
    "failed",
    [
        output({}, 1),
        output([]),
        OSError("private-exception"),
        output({"status": "down"}),
    ],
)
def test_health_partial_failure(tmp_path, failed):
    runner = FakeRunner(failed, output({"status": "ok"}))
    assert MockAdmin(tmp_path, runner=runner).health() == {
        "mock-jira": False,
        "mock-slack": True,
    }
    assert len(runner.calls) == 2
    assert all(command[-1] == "health" for command, _ in runner.calls)


def test_health_success(tmp_path):
    runner = FakeRunner(output({"status": "ok"}), output({"status": "ok"}))
    assert MockAdmin(tmp_path, runner=runner).health() == {
        "mock-jira": True,
        "mock-slack": True,
    }


def test_subprocess_runner_captures_without_raising(tmp_path, monkeypatch):
    calls = []
    result = output({"status": "ok"})

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return result

    monkeypatch.setattr("govops.evalrun.mock_admin.subprocess.run", run)
    assert subprocess_runner(["fake-command"], cwd=tmp_path) is result
    assert calls == [
        (
            ["fake-command"],
            {
                "cwd": tmp_path,
                "capture_output": True,
                "text": True,
                "check": False,
            },
        )
    ]
