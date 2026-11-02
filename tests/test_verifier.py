"""Harness contract: artifact parsing, status classification, resume, summary.

No docker here: the sweep itself is covered by the docker-marked smoke test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adb_solve import verifier

REWARD = {"reward": 1.0, "tests_total": 12, "tests_passed": 12, "partial": 1.0}
RUN_SUMMARY = {
    "ngspice_runs": 189,
    "workers": 1,
    "wall_clock_s": 66.9,
    "summed_run_time_s": 66.7,
    "slowest_run_time_s": 0.45,
    "failed_runs": [],
}
SUMMARY = {
    "tests_passed": 12,
    "tests_total": 12,
    "raw_score": 1.0,
    "reward": 1.0,
    "measurements": {"pvt_power_w": 1.14e-4},
    "ngspice_runs": 189,
    "wall_clock_s": 66.9,
}
CTRF = {
    "results": {
        "summary": {"tests": 2, "passed": 1, "failed": 1},
        "tests": [
            {"name": "signoff_complete", "status": "passed", "message": "ok"},
            {"name": "pvt_power", "status": "failed", "message": "power too high"},
        ],
    }
}


def write_logs(root: Path, *, reward: dict | None = REWARD, ctrf: dict | None = CTRF,
               run: dict | None = RUN_SUMMARY, summary: dict | None = SUMMARY,
               metrics_rows: int = 3) -> Path:
    logs = root / "logs"
    base = logs / verifier.ARTIFACT_DIR
    base.mkdir(parents=True, exist_ok=True)
    (logs / verifier.REPORT_DIR).mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("reward.json", reward),
        ("new-ctrf.json", ctrf),
    ):
        if payload is not None:
            (base / name).write_text(json.dumps(payload))
    # Reality: summary/run-summary live in the signoff report directory.
    for name, payload in (("run-summary.json", run), ("summary.json", summary)):
        if payload is not None:
            (logs / verifier.REPORT_DIR / name).write_text(json.dumps(payload))
    (logs / verifier.REPORT_DIR / "metrics.json").write_text(
        json.dumps([{"bench": "tb_op.spi", "group": "op"} for _ in range(metrics_rows)])
    )
    return logs


def test_parse_artifacts(tmp_path):
    artifacts = verifier.parse_artifacts(write_logs(tmp_path))
    assert artifacts is not None
    assert artifacts.reward == 1.0
    assert (artifacts.tests_passed, artifacts.tests_total) == (12, 12)
    assert artifacts.ngspice_runs == 189
    assert artifacts.wall_clock_s == pytest.approx(66.9)
    assert artifacts.metrics_rows == 3
    assert [gate.name for gate in artifacts.gates] == ["signoff_complete", "pvt_power"]
    assert artifacts.gates[1].status == "failed"
    assert artifacts.measurements["pvt_power_w"] == pytest.approx(1.14e-4)


def test_parse_artifacts_without_reward_is_none(tmp_path):
    logs = write_logs(tmp_path, reward=None)
    assert verifier.parse_artifacts(logs) is None


def test_classify_passed_failed_error_timeout(tmp_path):
    artifacts = verifier.parse_artifacts(write_logs(tmp_path))
    assert verifier.classify(0, artifacts, "")[0] == "passed"

    failing = verifier.VerifierArtifacts(
        reward=0.5, tests_passed=1, tests_total=2, partial=0.5,
        ngspice_runs=10, wall_clock_s=1.0, summed_run_time_s=1.0, workers=1,
        failed_runs=("tb_ac.spi/tt/1.8/27: no convergence",),
        gates=(verifier.GateResult("pvt_power", "failed", "too high"),),
        measurements={}, metrics_rows=2,
    )
    status, reason = verifier.classify(0, failing, "")
    assert status == "failed"
    assert "pvt_power" in reason and "failed runs: 1" in reason

    assert verifier.classify(0, None, "boom")[0] == "error"
    assert verifier.classify(124, None, "")[0] == "timeout"


def test_should_skip_only_when_resuming(tmp_path):
    path = tmp_path / "task.json"
    assert not verifier.should_skip(path, resume=True)  # file missing
    path.write_text("{}")
    assert verifier.should_skip(path, resume=True)
    assert not verifier.should_skip(path, resume=False)


def test_write_and_load_round_trip(tmp_path):
    artifacts = verifier.parse_artifacts(write_logs(tmp_path / "logs"))
    assert artifacts is not None
    run = verifier.VerifierRun(
        task_id="demo", status="passed", exit_code=0, wall_s=68.0,
        artifacts=artifacts, stdout_tail="log tail", image="adb-verifier:demo-abc",
    )
    path = verifier.result_path(tmp_path, "demo")
    verifier.write_result(path, run)
    loaded = verifier.load_result(path)
    assert loaded is not None
    assert (loaded.task_id, loaded.status, loaded.exit_code) == ("demo", "passed", 0)
    assert loaded.artifacts is not None
    assert loaded.artifacts.ngspice_runs == 189
    assert loaded.artifacts.gates[0].name == "signoff_complete"
    assert loaded.artifacts.measurements["pvt_power_w"] == pytest.approx(1.14e-4)


def test_summarize_aggregates(tmp_path):
    artifacts = verifier.parse_artifacts(write_logs(tmp_path))
    assert artifacts is not None
    results = [
        verifier.VerifierRun("a", "passed", 0, 70.0, artifacts=artifacts),
        verifier.VerifierRun("b", "failed", 0, 12.0, reason="reward=0.5", artifacts=artifacts),
        verifier.VerifierRun("c", "error", 2, 1.0, reason="no artifacts"),
    ]
    summary = verifier.summarize(results)
    assert summary["tasks"] == 3
    assert summary["status_counts"] == {"error": 1, "failed": 1, "passed": 1}
    assert summary["ngspice_runs"] == 378
    assert [row["task_id"] for row in summary["failing"]] == ["b", "c"]
    assert {row["gate"] for row in summary["failing_gates"]} == {"pvt_power"}


def test_context_digest_tracks_content(tmp_path):
    package = tmp_path / "task"
    (package / "tests").mkdir(parents=True)
    (package / "tests" / "Dockerfile").write_text("FROM scratch\n")
    first = verifier.context_digest(package / "tests")
    (package / "tests" / "benches").mkdir()
    (package / "tests" / "benches" / "tb.spi").write_text(".end\n")
    second = verifier.context_digest(package / "tests")
    assert first != second
    assert verifier.image_tag("task", second).endswith(second)


def test_discover_requires_packages(tmp_path):
    empty = tmp_path / "tasks"
    empty.mkdir()
    with pytest.raises(verifier.VerifierError):
        verifier.discover(empty)

    package = empty / "demo"
    (package / "tests").mkdir(parents=True)
    (package / "tests" / "Dockerfile").write_text("FROM scratch\n")
    (package / "tests" / "test.sh").write_text("#!/bin/sh\n")
    found = verifier.discover(empty)
    assert list(found) == ["demo"]
    found["demo"].require()


def test_artifacts_are_found_in_either_directory(tmp_path):
    """Some verifiers write run-summary next to reward.json instead."""
    logs = write_logs(tmp_path, run=None, summary=None)
    base = logs / verifier.ARTIFACT_DIR
    (base / "run-summary.json").write_text(json.dumps(RUN_SUMMARY))
    (base / "summary.json").write_text(json.dumps(SUMMARY))
    artifacts = verifier.parse_artifacts(logs)
    assert artifacts is not None
    assert artifacts.ngspice_runs == 189
    assert artifacts.measurements["pvt_power_w"] == pytest.approx(1.14e-4)


def test_docker_command_pins_threads_and_mounts_design_as_file(tmp_path):
    command = verifier.docker_command(
        name="adb-vf-demo-abc", image="img", design=tmp_path / "circuit.spi",
        logs=tmp_path / "logs", spiceinit=tmp_path / ".spiceinit",
        wrapper=tmp_path / "ngspice-count", cpus=2, memory="6g",
    )
    joined = " ".join(command)
    assert "OMP_NUM_THREADS=1" in joined
    assert f"{tmp_path}/.spiceinit:/app/.spiceinit:ro" in joined
    assert f"{tmp_path}/ngspice-count:/count/ngspice:ro" in joined
    assert "NGSPICE=/count/ngspice" in joined
    assert "PATH=/count:/opt/analog-arena:/opt/ngspice/bin:" in joined
    assert f"{tmp_path}/circuit.spi:/app/circuit.spi:ro" in joined
    assert "-v /app:/app" not in joined  # never shadow the image's /app
    assert command[-1] == "img"
    assert verifier.SPICEINIT.strip() == "set num_threads=1"


def make_package(root: Path) -> verifier.TaskPackage:
    tests = root / "demo" / "tests"
    tests.mkdir(parents=True)
    (tests / "Dockerfile").write_text("FROM scratch\nCOPY test.sh /tests/test.sh\n")
    (tests / "test.sh").write_text("#!/bin/sh\n")
    return verifier.discover(root / "demo" if False else root)["demo"]


def test_build_command_renders_the_sandbox_build(tmp_path):
    package = make_package(tmp_path)
    command = verifier.build_command(package, "adb-verifier:demo-abc")
    assert command[:4] == ["docker", "build", "--network", "none"]
    assert command[4:6] == ["-f", str(package.dockerfile)]
    assert command[-3:] == ["-t", "adb-verifier:demo-abc", str(package.tests_dir)]


def test_build_reuses_an_existing_image(tmp_path, monkeypatch):
    package = make_package(tmp_path)
    monkeypatch.setattr(verifier, "image_exists", lambda tag: True)

    def explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("docker build must not run for an existing image")

    monkeypatch.setattr(verifier.subprocess, "run", explode)
    tag = verifier.build(package)
    assert tag == verifier.image_tag("demo", verifier.context_digest(package.tests_dir))


def test_build_records_and_classifies_a_failed_build(tmp_path, monkeypatch):
    package = make_package(tmp_path)
    monkeypatch.setattr(verifier, "image_exists", lambda tag: False)
    seen: list[list[str]] = []

    class Result:
        returncode = 1
        stdout = ""
        stderr = "COPY failed: no such file"

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        seen.append(command)
        return Result()

    monkeypatch.setattr(verifier.subprocess, "run", fake_run)
    with pytest.raises(verifier.VerifierError, match="docker build failed.*COPY failed"):
        verifier.build(package, rebuild=True)
    assert seen == [verifier.build_command(package, verifier.image_tag(
        "demo", verifier.context_digest(package.tests_dir)))]


@pytest.mark.docker
def test_sandbox_container_runs_and_leaves_nothing_behind(tmp_path):
    """End-to-end: a real container, zero leftover containers/ngspice threads."""
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    probe = subprocess.run(
        ["docker", "image", "inspect", verifier.DEFAULT_IMAGE],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        pytest.skip("pinned Sky130 tools image is absent")
    if subprocess.run(
        ["docker", "image", "inspect", verifier.HELPER_IMAGE],
        capture_output=True, text=True,
    ).returncode != 0:
        pytest.skip("log-chown helper image is absent")

    root = tmp_path / "tasks"
    tests = root / "demo" / "tests"
    tests.mkdir(parents=True)
    (tests / "Dockerfile").write_text(
        f"FROM {verifier.DEFAULT_IMAGE}\n"
        "COPY test.sh /tests/test.sh\n"
        "RUN chmod +x /tests/test.sh\n"
    )
    (tests / "test.sh").write_text(
        "#!/bin/sh\n"
        "mkdir -p /logs/verifier\n"
        "printf '%s\\n' '{\"reward\":1.0,\"tests_total\":1,\"tests_passed\":1,\"partial\":1.0}'"
        " > /logs/verifier/reward.json\n"
        "printf '%s\\n' '{\"results\":{\"tests\":[{\"name\":\"g\",\"status\":\"passed\","
        "\"message\":\"ok\"}]}}' > /logs/verifier/new-ctrf.json\n"
        "echo ngspice_runs=0 wall_clock_s=0.01\n"
    )
    solution = tmp_path / "ref.spi"
    solution.write_text("title ref\n.end\n")
    package = verifier.discover(root)["demo"]

    before = set(verifier.leftover_containers())
    result = verifier.run(package, solution, workdir=tmp_path / "work", timeout=180)
    assert result.status == "passed", result.reason
    assert result.artifacts is not None
    assert (result.artifacts.tests_passed, result.artifacts.tests_total) == (1, 1)
    # Other sweeps may be running; this run must add no container.
    assert set(verifier.leftover_containers()) - before == set()


def test_wrapper_count_overrides_reported_runs(tmp_path):
    logs = write_logs(tmp_path)
    (logs / verifier.NGSPICE_COUNTER).write_text("one\ntwo\nthree\n")
    artifacts = verifier.parse_artifacts(logs)
    assert artifacts is not None
    assert artifacts.ngspice_runs == 3
    assert artifacts.runs_source == "wrapper-count"


def test_run_summary_runs_key_is_read(tmp_path):
    """Several verifiers write ``runs`` rather than ``ngspice_runs``."""
    logs = write_logs(
        tmp_path,
        run={"runs": 7, "workers": 1, "wall_clock_s": 12.5, "failed_runs": []},
    )
    artifacts = verifier.parse_artifacts(logs)
    assert artifacts is not None
    assert artifacts.ngspice_runs == 7
    assert artifacts.wall_clock_s == pytest.approx(12.5)


def test_wall_clock_uses_the_task_total_not_a_per_case_line(tmp_path):
    logs = write_logs(tmp_path, run=None, summary=None)
    output = "completed rf tt_mm/1.80V wall_clock_s=0.448\n" "INFO wall_clock_s=9.890\n"
    artifacts = verifier.parse_artifacts(logs, output)
    assert artifacts is not None
    assert artifacts.wall_clock_s == pytest.approx(9.89)
