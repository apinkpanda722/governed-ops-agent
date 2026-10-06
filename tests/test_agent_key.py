"""가상 키 발급은 임시 파일·mock HTTP만 사용한다."""

import json
import logging
from pathlib import Path

import httpx
import pytest

from govops.gateway.agent_key import main

MASTER = "MASTER-SENTINEL"
ISSUED = "ISSUED-SENTINEL"


@pytest.fixture(autouse=True)
def environment(monkeypatch, caplog):
    monkeypatch.setenv("LITELLM_MASTER_KEY", MASTER)
    monkeypatch.delenv("LITELLM_AGENT_KEY", raising=False)
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "httpcore.http11"):
        caplog.set_level(logging.DEBUG, logger=name)


def safe_output(capsys, caplog, code):
    output = capsys.readouterr()
    safe = all(
        s not in output.out + output.err + caplog.text + str(code)
        for s in (MASTER, ISSUED)
    )
    assert safe, "secret appeared in diagnostic output"


@pytest.mark.parametrize(
    "original",
    [
        b"# comment\r\nOTHER=keep\r\n\r\nLITELLM_AGENT_KEY=\r\nTAIL=yes",
        b"# comment\nOTHER=keep\n\n",
        b"OTHER=keep",
        b"LITELLM_AGENT_KEY=\n",
    ],
)
def test_success(tmp_path, capsys, caplog, original):
    file = tmp_path / "temporary-env"
    file.write_bytes(original)
    file.chmod(0o640)
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == "/key/generate"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer " + MASTER, "wrong auth"
        assert json.loads(request.content) == {
            "models": [
                "agent-primary",
                "agent-primary-only",
                "agent-primary-down",
                "agent-fallback",
            ],
            "key_alias": "govops-agent",
        }
        logging.getLogger("httpcore.http11").debug("%s %s", MASTER, ISSUED)
        return httpx.Response(200, json={"key": ISSUED})

    code = main(["--env-file", str(file)], transport=httpx.MockTransport(handler))
    assert code == 0
    assert len(seen) == 1
    result = file.read_bytes()
    expected = original.replace(
        b"LITELLM_AGENT_KEY=", b"LITELLM_AGENT_KEY=" + ISSUED.encode()
    )
    if b"LITELLM_AGENT_KEY=" not in original:
        expected = (
            original
            + (b"" if not original or original.endswith(b"\n") else b"\n")
            + b"LITELLM_AGENT_KEY="
            + ISSUED.encode()
            + b"\n"
        )
    assert result == expected, "incorrect env bytes"
    assert file.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.iterdir()) == [file]
    safe_output(capsys, caplog, code)


@pytest.mark.parametrize(
    "failure", [401, 500, "connection", "missing", "empty", "json", "list", "nonstring"]
)
def test_failure(tmp_path, capsys, caplog, failure):
    file = tmp_path / "temporary-env"
    original = b"# keep\r\nLITELLM_AGENT_KEY=\r\n"
    file.write_bytes(original)

    def handler(request):
        if failure == "connection":
            raise httpx.ConnectError(MASTER + ISSUED, request=request)
        if isinstance(failure, int):
            return httpx.Response(failure, text=MASTER + ISSUED)
        if failure == "json":
            return httpx.Response(200, text=MASTER + ISSUED)
        body = {
            "missing": {},
            "empty": {"key": ""},
            "list": [],
            "nonstring": {"key": 123},
        }[failure]
        return httpx.Response(200, json=body)

    code = main(["--env-file", str(file)], transport=httpx.MockTransport(handler))
    assert code == 1
    assert file.read_bytes() == original
    safe_output(capsys, caplog, code)


@pytest.mark.parametrize(
    "condition", ["file", "environment", "master", "absent", "duplicate"]
)
def test_no_request(tmp_path, monkeypatch, capsys, caplog, condition):
    file = tmp_path / "temporary-env"
    original = b"LITELLM_AGENT_KEY=\n"
    if condition in {"file", "duplicate"}:
        original = (
            (original if condition == "duplicate" else b"")
            + b"LITELLM_AGENT_KEY="
            + ISSUED.encode()
            + b"\n"
        )
    if condition != "absent":
        file.write_bytes(original)
    if condition == "environment":
        monkeypatch.setenv("LITELLM_AGENT_KEY", ISSUED)
    if condition == "master":
        monkeypatch.delenv("LITELLM_MASTER_KEY")
    seen = []
    code = main(
        ["--env-file", str(file)],
        transport=httpx.MockTransport(lambda r: seen.append(r)),
    )
    assert code == 1
    assert not seen
    if condition == "absent":
        assert not file.exists()
    else:
        assert file.read_bytes() == original, "file changed on refusal"
    safe_output(capsys, caplog, code)


def test_master_reference_is_isolated():
    root = Path(__file__).resolve().parents[1] / "src/govops"
    matches = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if "LITELLM_MASTER_KEY" in p.read_text()
    ]
    assert matches == ["gateway/agent_key.py"]


def test_atomic_write_failure_preserves_file(tmp_path, monkeypatch, capsys, caplog):
    from govops.gateway import agent_key

    file = tmp_path / "temporary-env"
    original = b"LITELLM_AGENT_KEY=\n"
    file.write_bytes(original)

    real_mkstemp = agent_key.tempfile.mkstemp

    def mkstemp(**kwargs):
        fd, name = real_mkstemp(**kwargs)
        assert Path(name).stat().st_mode & 0o777 == 0o600
        return fd, name

    monkeypatch.setattr(agent_key.tempfile, "mkstemp", mkstemp)

    def replace(source, destination):
        temporary = Path(source)
        assert temporary.parent == file.parent
        raise OSError(MASTER + ISSUED)

    monkeypatch.setattr(agent_key.os, "replace", replace)
    code = main(
        ["--env-file", str(file)],
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"key": ISSUED})
        ),
    )
    assert code == 1
    assert file.read_bytes() == original
    assert list(tmp_path.iterdir()) == [file]
    safe_output(capsys, caplog, code)


@pytest.mark.parametrize("override", [False, True])
def test_base_url(tmp_path, monkeypatch, override, capsys, caplog):
    file = tmp_path / "temporary-env"
    file.write_bytes(b"LITELLM_AGENT_KEY=\n")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://environment.invalid:4000")
    args = ["--env-file", str(file)]
    if override:
        args += ["--base-url", "http://override.invalid:4000/"]

    def handler(request):
        assert request.url.host == (
            "override.invalid" if override else "environment.invalid"
        )
        return httpx.Response(200, json={"key": ISSUED})

    code = main(args, transport=httpx.MockTransport(handler))
    assert code == 0
    safe_output(capsys, caplog, code)


@pytest.mark.parametrize("config", ["config.yaml", "config.obs.yaml"])
def test_agent_key_models_match_config(config):
    import yaml

    from govops.gateway.agent_key import AGENT_KEY_MODELS

    root = Path(__file__).resolve().parents[1]
    settings = yaml.safe_load((root / "infra/litellm" / config).read_text())
    assert set(AGENT_KEY_MODELS) == {
        row["model_name"] for row in settings["model_list"]
    }


@pytest.mark.parametrize("failure", [None, 401, 500, "connection"])
def test_update(tmp_path, monkeypatch, capsys, caplog, failure):
    from govops.gateway.agent_key import AGENT_KEY_MODELS

    file = tmp_path / "temporary-env"
    original = b"# keep\r\nLITELLM_AGENT_KEY=" + ISSUED.encode() + b"\r\n"
    file.write_bytes(original)
    file.chmod(0o640)
    monkeypatch.setenv("LITELLM_AGENT_KEY", ISSUED)
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert str(request.url) == "http://override.invalid:4000/key/update"
        assert request.headers["authorization"] == "Bearer " + MASTER, "wrong auth"
        assert json.loads(request.content) == {
            "key": ISSUED,
            "models": list(AGENT_KEY_MODELS),
        }, "wrong update body"
        logging.getLogger("httpcore.http11").debug("%s %s", MASTER, ISSUED)
        if failure == "connection":
            raise httpx.ConnectError(MASTER + ISSUED, request=request)
        return httpx.Response(failure or 200, text=MASTER + ISSUED)

    code = main(
        [
            "--update",
            "--env-file",
            str(file),
            "--base-url",
            "http://override.invalid:4000/",
        ],
        transport=httpx.MockTransport(handler),
    )
    assert code == (0 if failure is None else 1)
    assert len(seen) == 1
    assert file.read_bytes() == original, "update changed env bytes"
    assert file.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.iterdir()) == [file]
    output = capsys.readouterr()
    expected = (
        "PASS agent-key-update models=" + ",".join(AGENT_KEY_MODELS)
        if failure is None
        else "FAIL " + ("connection" if failure == "connection" else f"http_{failure}")
    )
    assert output.out == expected + "\n"
    assert not output.err
    assert all(secret not in output.out + caplog.text for secret in (MASTER, ISSUED))


@pytest.mark.parametrize("missing", ["agent", "blank_agent", "master"])
def test_update_missing_key(tmp_path, monkeypatch, capsys, caplog, missing):
    file = tmp_path / "temporary-env"
    original = b"LITELLM_AGENT_KEY=\n"
    file.write_bytes(original)
    if missing == "blank_agent":
        monkeypatch.setenv("LITELLM_AGENT_KEY", "   ")
    if missing == "master":
        monkeypatch.setenv("LITELLM_AGENT_KEY", ISSUED)
        monkeypatch.delenv("LITELLM_MASTER_KEY")
    seen = []
    code = main(
        ["--update", "--env-file", str(file)],
        transport=httpx.MockTransport(lambda request: seen.append(request)),
    )
    assert code == 1
    assert not seen
    assert file.read_bytes() == original
    output = capsys.readouterr()
    assert (
        output.out
        == "FAIL missing_"
        + ("master_key" if missing == "master" else "agent_key")
        + "\n"
    )
    assert not output.err
    assert all(secret not in output.out + caplog.text for secret in (MASTER, ISSUED))
