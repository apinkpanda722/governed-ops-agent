"""Measurement preconditions follow the real artifact shape: three separate N2 runs.

Each official N2 directory holds the 40 test tasks with run_index 0 (counted from the
real run directories before this fixture was written). Order is the directory position.
"""

import pytest
from test_eval_human import synthetic

from govops.eval.observation import dump_observations
from govops.grounding import cli, registration


def make_dirs(tmp_path, *, run_index=lambda n: 0):
    specs, observations = synthetic()
    test = [s.task_id for s in specs if s.split == "test"]
    base = {o.task_id: o for o in observations}
    directories = []
    for n in range(3):
        directory = tmp_path / f"run-{n}"
        directory.mkdir()
        rows = [
            base[task].model_copy(
                update={"run_index": run_index(n), "answer": f"합성 답변 {n}"}
            )
            for task in test
            if task in base
        ]
        dump_observations(directory / "observations.jsonl", rows)
        directories.append(directory)
    return specs, directories, {s.task_id for s in specs if s.split == "test"}


def rewrite(directory, mutate):
    from govops.eval.observation import load_observations

    rows = mutate(load_observations(directory / "observations.jsonl"))
    dump_observations(directory / "observations.jsonl", rows)


def test_three_separate_runs_all_with_run_index_zero_are_accepted_in_position_order(
    tmp_path,
):
    specs, directories, tests = make_dirs(tmp_path)
    rows = cli.measurement_rows(directories, specs)
    assert [{r.spec.task_id for r in group} for group in rows] == [tests] * 3
    assert {r.observation.run_index for group in rows for r in group} == {0}
    assert [group[0].observation.answer for group in rows] == [
        "합성 답변 0",
        "합성 답변 1",
        "합성 답변 2",
    ]
    # The legacy 0, 1, 2 labelling is accepted too: only consistency inside a directory.
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    specs, directories, _ = make_dirs(legacy, run_index=lambda n: n)
    assert len(cli.measurement_rows(directories, specs)) == 3


@pytest.mark.parametrize("which", [0, 1, 2])
def test_mixed_run_index_inside_a_directory_is_a_fixed_code_failure(tmp_path, which):
    specs, directories, _ = make_dirs(tmp_path)
    rewrite(
        directories[which],
        lambda rows: [rows[0].model_copy(update={"run_index": 1}), *rows[1:]],
    )
    with pytest.raises(registration.RegistrationError) as error:
        cli.measurement_rows(directories, specs)
    assert str(error.value) == "measurement_order_mismatch"
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("mutation", ["missing_task", "duplicate_task", "extra_task"])
def test_a_different_task_set_is_the_same_code(tmp_path, mutation):
    specs, directories, _ = make_dirs(tmp_path)

    def mutate(rows):
        if mutation == "missing_task":
            return rows[1:]
        if mutation == "duplicate_task":
            return [*rows, rows[0]]
        return [*rows, rows[0].model_copy(update={"task_id": "T9-99"})]

    rewrite(directories[1], mutate)
    with pytest.raises(registration.RegistrationError) as error:
        cli.measurement_rows(directories, specs)
    assert str(error.value) == "measurement_order_mismatch"


def test_the_same_directory_twice_or_a_wrong_count_is_rejected(tmp_path):
    specs, directories, _ = make_dirs(tmp_path)
    for bad in (
        [directories[0], directories[1], directories[0]],
        [directories[0], directories[1], directories[1].parent / "run-1/../run-1"],
        directories[:2],
        [*directories, directories[0]],
    ):
        with pytest.raises(registration.RegistrationError) as error:
            cli.measurement_rows(bad, specs)
        assert str(error.value) == "measurement_order_mismatch"


def test_an_unreadable_directory_is_its_own_fixed_code(tmp_path):
    specs, directories, _ = make_dirs(tmp_path)
    (directories[2] / "observations.jsonl").unlink()
    with pytest.raises(registration.RegistrationError) as error:
        cli.measurement_rows(directories, specs)
    assert str(error.value) == "measurement_input_unreadable"


def test_positions_are_named_in_order():
    assert cli.N2_POSITIONS == ("n2_1", "n2_2", "n2_3")
    assert len(cli.N2_RUNS) == 3
