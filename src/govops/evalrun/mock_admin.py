"""Invoke internal mock administration through compose, without exposing output."""

import json
import subprocess
from pathlib import Path
from typing import Literal, Protocol


class Runner(Protocol):
    def __call__(
        self, command: list[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]: ...


def subprocess_runner(
    command: list[str], *, cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


class MockAdminError(RuntimeError):
    def __init__(self, code: Literal["command_failed", "invalid_output"]):
        self.code = code
        super().__init__(code)


class MockAdmin:
    def __init__(self, root: Path, *, runner: Runner = subprocess_runner):
        self.root = root
        self.runner = runner

    def _command(self, service: str, *arguments: str) -> dict:
        command = [
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
            *arguments,
        ]
        # Raise outside the handler so even exception context cannot carry output.
        failed = False
        try:
            result = self.runner(command, cwd=self.root)
        except (OSError, subprocess.SubprocessError):
            failed = True
        if failed:
            raise MockAdminError("command_failed")
        if result.returncode != 0:
            raise MockAdminError("command_failed")
        invalid = False
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError):
            invalid = True
        if invalid or not isinstance(value, dict):
            raise MockAdminError("invalid_output")
        return value

    def _both(self, *arguments: str) -> dict:
        return {
            name: self._command(f"mock-{name}", *arguments)
            for name in ("jira", "slack")
        }

    def reset(self) -> None:
        self._both("reset")

    def state(self, *, bodies: bool = False) -> dict:
        return self._both("state", *(["--bodies"] if bodies else []))

    def faults_clear(self) -> None:
        self._both("faults", "clear")

    def faults_stats(self) -> dict:
        return self._both("faults", "stats")

    def health(self) -> dict[str, bool]:
        health = {}
        for service in ("mock-jira", "mock-slack"):
            try:
                health[service] = self._command(service, "health").get("status") == "ok"
            except MockAdminError:
                health[service] = False
        return health
