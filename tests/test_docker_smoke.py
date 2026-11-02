"""End-to-end smoke test: real ngspice in the pinned image.

Skipped unless docker and the pinned Sky130 tools image are available.  This is
the only test that proves the mount paths (``/app/circuit.spi``), the deck
rendering and the measure parsing fit together on a real netlist.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from adb_solve import bench, evaluate
from adb_solve.docker_backend import DEFAULT_IMAGE

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "data" / "tasks" / "sky130-lc-vco-2ghz-pvt.json"

pytestmark = pytest.mark.docker


def _have_image() -> bool:
    if shutil.which("docker") is None or not TASK.exists():
        return False
    completed = subprocess.run(
        ["docker", "image", "inspect", DEFAULT_IMAGE],
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0


requires_docker = pytest.mark.skipif(
    not _have_image(), reason="docker or the pinned Sky130 image is unavailable"
)


@requires_docker
def test_lc_vco_nominal_operating_point(tmp_path: Path) -> None:
    payload = json.loads(TASK.read_text())
    netlist = payload["files"]["solution"][0]["content"]
    deck_text = next(
        item["content"] for item in payload["files"]["tests"]
        if item["path"].endswith("tb_tune.spi")
    )
    report = evaluate.evaluate(
        netlist,
        [bench.Deck("tb_tune.spi", deck_text)],
        evaluate.corner_matrix(
            processes=["tt"],
            values={"supply": 1.8, "temperature": 27.0},
        ),
        evaluate.EvalOptions(
            jobs=1,
            timeout=300.0,
            cache_root=tmp_path / "cache",
            work_root=tmp_path / "work",
        ),
    )

    assert report["status_counts"] == {"ok": 1}, report["results"][0].get("log_tail")
    measures = report["results"][0]["measures"]
    assert measures["p1_freq"] == pytest.approx(2.1e9, rel=0.1)
    assert {"p1_freq", "p4_freq", "power_avg", "startup_time"} <= set(measures)
