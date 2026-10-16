"""User CLI exercised end to end with synthetic rows, a fake call and temp git."""

import json
from contextlib import asynccontextmanager

import pytest
from test_eval_human import synthetic
from test_eval_observation import observation
from test_eval_tcr import spec
from test_grounding_registration import commit, repository
from test_grounding_sheets import fill, sheet_dir

from govops import costs
from govops.eval.grading import TokenUsage
from govops.eval.observation import dump_observations
from govops.grounding import cli, registration
from govops.grounding.evidence import Evidence, EvidenceItem
from govops.grounding.spend import M8Spend

BODY = "SYNTHETIC_PRIVATE_DELIVERABLE"
EVIDENCE = "SYNTHETIC_PRIVATE_EVIDENCE"


def environment(root, monkeypatch, *, accurate=False, failure=None):
    repository(root)
    specs, observations = synthetic()
    observations = [
        o.model_copy(update={"answer": BODY + ".\n合成語句."}) for o in observations
    ]
    for i in range(20):
        task = f"T1-{i + 80:02}"
        specs.append(spec(task_id=task, split="dev"))
    dev = [
        observation(task_id=s.task_id, answer=BODY + ".\n合成語句.")
        for s in specs
        if s.split == "dev"
    ]
    for name, obs in [("test-run", observations), ("dev-run", dev)]:
        out = root / name
        out.mkdir()
        dump_observations(out / "observations.jsonl", obs)
    monkeypatch.setenv(
        "SYNTHETIC_DB", "postgresql://synthetic:synthetic-password@localhost/old"
    )
    calls = []

    async def call(prompt):
        calls.append(prompt)
        if failure == "call":
            raise RuntimeError(EVIDENCE + " synthetic-password")
        if failure == "parse":
            return EVIDENCE, TokenUsage(10, 10)
        value = json.loads(prompt.split("\n입력 JSON:\n")[1])
        return json.dumps(
            {
                "sentences": [
                    {
                        "sentence_id": s["sentence_id"],
                        "label": "supported"
                        if s["sentence_id"] == "s1"
                        or (accurate and s["text"] == "合成語句.")
                        else "unsupported",
                        "cited_ids": s["citations"],
                        "evidence_ids": ["E1"],
                        "citation_supports": [
                            {"doc_id": doc, "supports": True} for doc in s["citations"]
                        ],
                    }
                    for s in value["sentences"]
                ]
            }
        ), TokenUsage(10, 10)

    @asynccontextmanager
    async def saver(url):
        assert url.endswith("/govops_m8_evidence")
        yield object()

    async def evidence(saver, link, *, max_chars):
        assert max_chars == 80000
        return Evidence(
            "ok",
            (EvidenceItem("lookup_id", EVIDENCE, len(EVIDENCE)),),
            len(EVIDENCE),
            0,
        )

    costs.init_ledgers(root / "ledger")

    def run(command, *extra):
        return cli.main(
            [
                command,
                "--test-results",
                str(root / "test-run"),
                "--dev-results",
                str(root / "dev-run"),
                "--url-env",
                "SYNTHETIC_DB",
                "--dbname",
                "govops_m8_evidence",
                *extra,
            ],
            repo_root=root,
            specs=specs,
            call=call,
            ledger_factory=lambda: costs.CostLedger(root / "ledger"),
            saver_factory=saver,
            evidence_loader=evidence,
        )

    return run, calls


def label(root, split):
    out = sheet_dir(root, split)
    fill(out / "labels.csv", "label", ["supported", "unsupported"])
    fill(out / "citations.csv", "supports", ["지지"])


def test_end_to_end_guards_single_grade_full_report_and_no_bodies(
    tmp_path, monkeypatch, capsys
):
    run, calls = environment(tmp_path, monkeypatch)
    assert run("calibrate") == 2 and not calls
    assert run("sheet-calibration") == 0
    label(tmp_path, "calibration")
    assert run("register-labels", "--split", "calibration") == 0
    assert run("calibrate") == 2 and not calls
    commit(tmp_path)
    assert run("calibrate") == 0
    assert len(calls) == 30  # all rows are human-positive, so no eligible injections
    assert run("adopt") == 0
    assert run("draw-heldout") == 2
    commit(tmp_path)
    assert run("draw-heldout") == 0
    assert run("sheet-heldout") == 0
    label(tmp_path, "heldout")
    assert run("register-labels", "--split", "heldout") == 0
    before = len(calls)
    assert run("grade-heldout") == 2 and len(calls) == before
    commit(tmp_path)
    assert run("grade-heldout") == 0
    assert len(calls) == before + 25 * 3
    output = json.loads(
        (sheet_dir(tmp_path).parent / "heldout-result.json").read_text()
    )
    assert output["verdict"] == "채점기 v3 검증 미달"
    for key in (
        "kappa",
        "kappa_ci",
        "confusion_matrix",
        "precision",
        "recall",
        "row_kappa",
        "detection_rate",
        "false_positive_rate",
        "criteria",
        "excluded",
        "failures",
        "split_count_changed",
        "sentence_distribution",
        "type_distribution",
        "citation_agreement",
    ):
        assert key in output
    assert run("grade-heldout") == 2
    assert run("measure") == 2
    assert run("report") == 0
    printed = capsys.readouterr()
    assert (
        BODY not in printed.out + printed.err
        and EVIDENCE not in printed.out + printed.err
    )
    assert "synthetic-password" not in printed.out + printed.err
    for name in ("calibration-log.jsonl", "heldout-result.json"):
        text = (sheet_dir(tmp_path).parent / name).read_text()
        assert BODY not in text and EVIDENCE not in text and "task_ids" not in text


def test_adoption_order_and_revision_limits():
    rows = [
        {"version": "v3.2", "kappa": 0.6, "detection_rate": 0.8},
        {"version": "v3.1", "kappa": 0.6, "detection_rate": 0.9},
        {"version": "v3.0", "kappa": 0.6, "detection_rate": 0.9},
    ]
    assert cli.choose_adopted(rows)["version"] == "v3.0"
    assert cli.choose_adopted(rows[:2])["version"] == "v3.1"
    with pytest.raises(ValueError, match="revision_limit"):
        cli.check_revision("v3.4", [])
    with pytest.raises(ValueError):
        cli.check_revision(
            "v3.1",
            [
                {
                    "version": "v3.0",
                    "kappa": 0.6,
                    "detection_rate": 0.9,
                    "false_positive_rate": 0.1,
                }
            ],
        )
    with pytest.raises(ValueError):
        cli.check_revision(
            "v3.2",
            [
                {"version": "v3.0", "kappa": 0.2, "detection_rate": 0.5},
                {"version": "v3.1", "kappa": 0.2, "detection_rate": 0.4},
            ],
        )


def test_probe_is_synthetic_costs_count_towards_calibration_cap(
    tmp_path, monkeypatch, capsys
):
    run, calls = environment(tmp_path, monkeypatch)
    assert run("cost-probe") == 0
    assert 3 <= len(calls) <= 5
    assert all(BODY not in value and EVIDENCE not in value for value in calls)
    spend = M8Spend(sheet_dir(tmp_path).parent / "spend.jsonl")
    assert spend.total_usd("calibration") > 0
    reservation = spend.reserve("calibration", 0)
    spend.settle(reservation, 0.8)
    before = len(calls)
    assert run("cost-probe") == 1 and len(calls) == before
    assert "m8_stage_cap_exceeded" in capsys.readouterr().out


def test_parser_accepts_dbname_and_does_not_echo_invalid_input(tmp_path, capsys):
    for command in cli.COMMANDS:
        parsed = cli.parser().parse_args([command, "--dbname", "govops_m8_evidence"])
        assert parsed.dbname == "govops_m8_evidence"
    assert cli.main(["unknown-private-token"], repo_root=tmp_path) == 2
    output = capsys.readouterr()
    assert "unknown-private-token" not in output.out + output.err


def test_fake_call_failure_keeps_diagnostics_and_indeterminate_zero_exit(
    tmp_path, monkeypatch, capsys
):
    run, calls = environment(tmp_path, monkeypatch)
    assert run("sheet-calibration") == 0
    fill(sheet_dir(tmp_path) / "labels.csv", "label", ["supported"])
    fill(sheet_dir(tmp_path) / "citations.csv", "supports", ["지지"])
    assert run("register-labels") == 0
    commit(tmp_path)
    assert run("calibrate") == 0
    assert len(calls) > 30  # includes synthetic injection calls
    assert run("adopt") == 0
    commit(tmp_path)
    assert run("draw-heldout") == 0
    assert run("sheet-heldout") == 0
    fill(sheet_dir(tmp_path, "heldout") / "labels.csv", "label", ["supported"])
    fill(sheet_dir(tmp_path, "heldout") / "citations.csv", "supports", ["지지"])
    assert run("register-labels", "--split", "heldout") == 0
    commit(tmp_path)
    assert run("grade-heldout") == 0
    assert "판정 불가(양성 부족)" in capsys.readouterr().out
    current = registration.read_registration(tmp_path / registration.REGISTRATION_PATH)
    assert current["splitter_rules_version"] == "sent-v1"


@pytest.mark.parametrize(
    "failure,code",
    [("call", "grading_call_failure"), ("parse", "grading_response_failure")],
)
def test_grader_failures_are_counted_without_exposing_text(
    tmp_path, monkeypatch, capsys, failure, code
):
    run, calls = environment(tmp_path, monkeypatch, failure=failure)
    assert run("sheet-calibration") == 0
    label(tmp_path, "calibration")
    assert run("register-labels") == 0
    commit(tmp_path)
    assert run("calibrate") == (1 if failure == "call" else 0)
    result = json.loads(
        (sheet_dir(tmp_path).parent / "calibration-log.jsonl")
        .read_text()
        .splitlines()[-1]
    )
    assert result["failures"] == {code: len(calls)}
    assert result["n_grader_failures"] == len(calls)
    assert result["n_ungraded_rows"] == 30
    if failure == "call":
        assert 0 < len(calls) < 30
        assert result["stop_code"] == "m8_stage_cap_exceeded"
        assert 0 < result["cost_usd"] <= 0.8
    else:
        assert len(calls) == 30
    assert not result["complete"]
    output = capsys.readouterr()
    assert (
        EVIDENCE not in output.out + output.err
        and "synthetic-password" not in output.out + output.err
    )


def test_validated_measurement_uses_three_n2_runs_in_order(tmp_path, monkeypatch):
    run, _ = environment(tmp_path, monkeypatch, accurate=True)
    for name in ("test-run", "dev-run"):
        path = tmp_path / name / "observations.jsonl"
        observations = [json.loads(line) for line in path.read_text().splitlines()]
        for i, obs in enumerate(observations):
            if i % 2 == 0:
                obs["answer"] = BODY + ".\nSYNTHETIC_POSITIVE."
        path.write_text("".join(json.dumps(o) + "\n" for o in observations))

    def labels_for(split):
        path = sheet_dir(tmp_path, split) / "labels.csv"
        import csv

        with path.open(newline="") as source:
            reader = csv.DictReader(source)
            fields, rows = reader.fieldnames, list(reader)
        for row in rows:
            row["label"] = (
                "unsupported"
                if row["sentence"] == "SYNTHETIC_POSITIVE."
                else "supported"
            )
        with path.open("w", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        fill(path.with_name("citations.csv"), "supports", ["지지"])

    assert run("sheet-calibration") == 0
    labels_for("calibration")
    assert run("register-labels") == 0
    commit(tmp_path)
    assert run("calibrate") == 0
    assert run("adopt") == 0
    commit(tmp_path)
    assert run("draw-heldout") == 0
    assert run("sheet-heldout") == 0
    labels_for("heldout")
    assert run("register-labels", "--split", "heldout") == 0
    commit(tmp_path)
    assert run("grade-heldout") == 0
    result = json.loads(
        (sheet_dir(tmp_path).parent / "heldout-result.json").read_text()
    )
    assert result["verdict"] == "채점기 v3 검증 통과"
    paths = [tmp_path / "test-run"]
    for n in (1, 2):
        path = tmp_path / f"test-run-{n}"
        path.mkdir()
        rows = [
            json.loads(line)
            for line in (paths[0] / "observations.jsonl").read_text().splitlines()
        ]
        for row in rows:
            row["run_index"] = n
        (path / "observations.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows)
        )
        paths.append(path)
    assert run("measure", "--results", *map(str, paths)) == 0
    measurement = json.loads(
        (sheet_dir(tmp_path).parent / "measurement-result.json").read_text()
    )
    assert [r["run_index"] for r in measurement["runs"]] == [0, 1, 2]
    assert measurement["n_graded_rows"] == measurement["n_input_rows"] == 108
    assert measurement["n_ungraded_rows"] == 0
    assert (
        measurement["n_evidence_missing"]
        == measurement["n_oversize"]
        == measurement["n_grader_failures"]
        == 0
    )
    assert measurement["unsupported_sentence_rate"] == 0.25
    assert measurement["unsupported_row_rate"] == 0.5
    assert len(measurement["repeat_counts"]) == 3
    assert all(r["n_claims"] == 216 for r in measurement["repeat_counts"])
    assert run("measure", "--results", *map(str, paths)) == 2


def test_modified_adopted_prompt_is_rejected_before_evidence_loading(
    tmp_path, monkeypatch
):
    run, _ = environment(tmp_path, monkeypatch)
    assert run("sheet-calibration") == 0
    label(tmp_path, "calibration")
    assert run("register-labels") == 0
    commit(tmp_path)
    assert run("calibrate") == 0
    assert run("adopt") == 0
    commit(tmp_path)
    monkeypatch.setattr(cli.prompt, "prompt_sha256", lambda version: "b" * 64)

    async def forbidden(*args, **kwargs):
        pytest.fail("changed adopted prompt must be rejected before held-out access")

    monkeypatch.setattr(cli, "_rows", forbidden)
    assert run("draw-heldout") == 2
