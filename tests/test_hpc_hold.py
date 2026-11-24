"""Tests for skills/cluster-ops/scripts/hpc-hold."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HOLD = Path(__file__).resolve().parent.parent / "skills" / "cluster-ops" / "scripts" / "hpc-hold"


def run_cli(*args, cwd=None, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(HOLD), *args], capture_output=True, text=True, cwd=cwd, env=e,
                          timeout=15)


def wait_beacon(ctrl, status, timeout=5.0):
    end = time.monotonic() + timeout
    p = ctrl / "beacon.json"
    while time.monotonic() < end:
        if p.exists():
            try:
                d = json.loads(p.read_text())
                if d.get("status") == status:
                    return d
            except json.JSONDecodeError:
                pass
        time.sleep(0.05)
    raise AssertionError(f"beacon did not reach {status}; last={p.read_text() if p.exists() else None}")


def test_success_exits_immediately(tmp_path):
    ctrl = tmp_path / "hold"
    t0 = time.monotonic()
    p = run_cli("run", "--dir", str(ctrl), "--fail-hold-min", "5", "--poll-s", "0.05",
                "--", sys.executable, "-c", "print('ok')", env={"LSB_JOBID": "9001"})
    dt = time.monotonic() - t0
    assert p.returncode == 0
    assert dt < 3.0
    assert "ok" in p.stdout
    b = json.loads((ctrl / "beacon.json").read_text())
    assert b["status"] == "DONE" and b["rc"] == 0 and b["job_id"] == "9001" and b["attempt"] == 1


def test_failure_holds_and_expires_with_original_rc(tmp_path):
    ctrl = tmp_path / "hold"
    p = run_cli("run", "--dir", str(ctrl), "--fail-hold-min", "0.006", "--max-hold-min", "0.02",
                "--poll-s", "0.05", "--", sys.executable, "-c",
                "import sys; print('boom detail', file=sys.stderr); sys.exit(7)")
    assert p.returncode == 7
    b = json.loads((ctrl / "beacon.json").read_text())
    assert b["status"] == "EXPIRED" and b["rc"] == 7 and "boom detail" in b["tail"]


def test_retry_rescues_in_same_allocation_and_keeps_cwd(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    ctrl = tmp_path / "hold"
    proc = subprocess.Popen(
        [sys.executable, str(HOLD), "run", "--dir", str(ctrl), "--fail-hold-min", "1",
         "--poll-s", "0.05", "--", sys.executable, "-c", "import sys; print('bad typo'); sys.exit(2)"],
        cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        b = wait_beacon(ctrl, "HOLD_ON_FAIL")
        assert b["rc"] == 2 and b["attempt"] == 1 and "bad typo" in b["tail"]
        st = run_cli("status", "--dir", str(ctrl), "--json")
        assert st.returncode == 0 and json.loads(st.stdout)["status"] == "HOLD_ON_FAIL"
        # Call retry from a different directory; job should still run in original `work` cwd.
        r = run_cli("retry", "--dir", str(ctrl), "--", sys.executable, "-c",
                    "import os; open('rescued.txt', 'w').write(os.getcwd())", cwd=tmp_path)
        assert r.returncode == 0, r.stderr
        out, err = proc.communicate(timeout=10)
        assert proc.returncode == 0, err
        assert (work / "rescued.txt").read_text() == str(work)
        b2 = json.loads((ctrl / "beacon.json").read_text())
        assert b2["status"] == "DONE" and b2["attempt"] == 2 and b2["rc"] == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_release_exits_immediately_with_original_rc(tmp_path):
    ctrl = tmp_path / "hold"
    proc = subprocess.Popen(
        [sys.executable, str(HOLD), "run", "--dir", str(ctrl), "--fail-hold-min", "5",
         "--poll-s", "0.05", "--", sys.executable, "-c", "import sys; sys.exit(42)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        wait_beacon(ctrl, "HOLD_ON_FAIL")
        t0 = time.monotonic()
        r = run_cli("release", "--dir", str(ctrl), "--reason", "needs more gmem")
        assert r.returncode == 0, r.stderr
        proc.communicate(timeout=5)
        assert proc.returncode == 42 and time.monotonic() - t0 < 2.0
        b = json.loads((ctrl / "beacon.json").read_text())
        assert b["status"] == "RELEASED" and b["rc"] == 42 and b["reason"] == "needs more gmem"
        # Commands after exit are refused cleanly.
        assert run_cli("release", "--dir", str(ctrl)).returncode == 1
    finally:
        if proc.poll() is None:
            proc.kill()


def test_extend_once_and_hard_ceiling(tmp_path):
    ctrl = tmp_path / "hold"
    proc = subprocess.Popen(
        [sys.executable, str(HOLD), "run", "--dir", str(ctrl), "--fail-hold-min", "0.02",
         "--max-hold-min", "0.03", "--poll-s", "0.05", "--", sys.executable, "-c", "import sys; sys.exit(3)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        b1 = wait_beacon(ctrl, "HOLD_ON_FAIL")
        r1 = run_cli("extend", "--dir", str(ctrl), "--min", "5")
        assert r1.returncode == 0, r1.stderr
        # Wait until beacon reflects extensions_used == 1 and capped at hard_deadline.
        end = time.monotonic() + 3.0
        b2 = b1
        while time.monotonic() < end:
            b2 = json.loads((ctrl / "beacon.json").read_text())
            if b2.get("extensions_used") == 1:
                break
            time.sleep(0.05)
        assert b2["extensions_used"] == 1
        assert b2["hold_until"] == b2["hard_deadline"]
        # Second extend is refused.
        r2 = run_cli("extend", "--dir", str(ctrl), "--min", "1")
        assert r2.returncode == 1 and "already extended" in r2.stderr
        proc.communicate(timeout=5)
        assert proc.returncode == 3
    finally:
        if proc.poll() is None:
            proc.kill()


def test_signal_during_hold_exits_immediately(tmp_path):
    ctrl = tmp_path / "hold"
    proc = subprocess.Popen(
        [sys.executable, str(HOLD), "run", "--dir", str(ctrl), "--fail-hold-min", "5",
         "--poll-s", "0.5", "--", sys.executable, "-c", "import sys; sys.exit(1)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        wait_beacon(ctrl, "HOLD_ON_FAIL")
        t0 = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=5)
        assert proc.returncode == 128 + signal.SIGTERM
        assert time.monotonic() - t0 < 2.0
        b = json.loads((ctrl / "beacon.json").read_text())
        assert b["status"] == "INTERRUPTED" and b["signal"] == signal.SIGTERM
    finally:
        if proc.poll() is None:
            proc.kill()


def test_validation_and_stale_cmd_ignored(tmp_path):
    ctrl = tmp_path / "hold"
    ctrl.mkdir()
    (ctrl / "cmd.json").write_text(json.dumps({"action": "release", "attempt": 1}))
    assert run_cli("run", "--dir", str(ctrl), "--fail-hold-min", "20", "--max-hold-min", "10", "--", "true").returncode == 2
    assert run_cli("run", "--dir", str(ctrl), "--max-hold-min", "120", "--", "true").returncode == 2
    # Stale cmd.json is removed on startup so a successful run completes cleanly.
    p = run_cli("run", "--dir", str(ctrl), "--fail-hold-min", "0.1", "--", sys.executable, "-c", "print('fresh')")
    assert p.returncode == 0 and not (ctrl / "cmd.json").exists()
