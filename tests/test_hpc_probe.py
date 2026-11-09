"""Tests for skills/cluster-ops/scripts/hpc-probe with fake bsub, srun, bjobs, bkill, squeue and scancel on a private
tmux socket. A bystander session stays open the whole time and must never receive input. Skipped without tmux."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[1] / "skills/cluster-ops/scripts/hpc-probe"
pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

FAKE_ALLOC = r"""#!/usr/bin/env bash
# fake bsub -Is / srun --pty: pends FAKE_PEND s, then starts an interactive bash on "fakenode", or is rejected.
dir=$FAKE_DIR; mode=${FAKE_MODE:-ok}
if [[ $(basename "$0") == bsub ]]; then id=4242; var=LSB_JOBID
  echo "Job <$id> is submitted to queue <normal>."; echo "<<Waiting for dispatch ...>>"
else id=777; var=SLURM_JOB_ID; echo "srun: job $id queued and waiting for resources"; fi
echo "$*" > "$dir/alloc-args.$id"
touch "$dir/running.$id"
sleep "${FAKE_PEND:-1}"
if [[ $mode == fail ]]; then echo "Request aborted by esub. Job not submitted."; rm -f "$dir/running.$id"; exit 255; fi
[[ $var == LSB_JOBID ]] && echo "<<Starting on fakenode>>" || echo "srun: job $id has been allocated resources"
host=fakenode; [[ $mode == samehost ]] && host=$(hostname)
printf 'hostname() { echo %s; }\nPS1="job$ "\n' "$host" > "$dir/rc.$id"
env "$var=$id" bash --rcfile "$dir/rc.$id" -i
s=$?
[[ $mode == sticky ]] || rm -f "$dir/running.$id"
exit $s
"""
FAKE_BJOBS = r"""#!/usr/bin/env bash
id=${!#}; if [[ -e $FAKE_DIR/running.$id ]]; then echo RUN; else echo "Job <$id> is not found" >&2; exit 255; fi
"""
FAKE_SQUEUE = r"""#!/usr/bin/env bash
id=$4; [[ -e $FAKE_DIR/running.$id ]] && echo RUNNING; exit 0
"""
FAKE_KILL = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$FAKE_DIR/kills.log"; rm -f "$FAKE_DIR/running.${!#}"; echo "Job <${!#}> is being terminated"
"""


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("bsub", FAKE_ALLOC), ("srun", FAKE_ALLOC), ("bjobs", FAKE_BJOBS), ("squeue", FAKE_SQUEUE),
                       ("bkill", FAKE_KILL), ("scancel", FAKE_KILL)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    sock = f"hpcprobe-{os.getpid()}-{tmp_path.name}"[:90]
    e = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", FAKE_DIR=str(tmp_path), HPC_PROBE_DIR=str(tmp_path / "st"),
             HPC_PROBE_TMUX=f"tmux -L {sock}", HPC_PROBE_POLL="0.2", HPC_PROBE_RESEND="1")
    e.pop("TMUX", None)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    tm = ["tmux", "-L", sock]
    # bystander: everything typed into it lands in bystander.txt
    subprocess.run(tm + ["new-session", "-d", "-s", "bystander", f"cat > {tmp_path}/bystander.txt"], env=e, check=True)
    yield {"env": e, "dir": tmp_path, "run": run_dir, "tm": tm}
    assert (tmp_path / "bystander.txt").read_text() == "", "the bystander session received input"
    subprocess.run(tm + ["kill-server"], capture_output=True)


def probe(env, *args, timeout=60, **extra):
    e = dict(env["env"], **extra)
    t0 = time.time()
    r = subprocess.run([sys.executable, str(PROBE), *args], cwd=env["run"], env=e, capture_output=True, text=True,
                       timeout=timeout)
    r.elapsed = time.time() - t0
    return r


LSF = ["--", "bsub", "-Is", "-q", "normal", "-W", "2:00", "/x/hpc-session"]
SLURM = ["--", "srun", "--time=02:00:00", "--pty", "/x/hpc-session-slurm"]


def test_refuses_bad_names_and_bare_allocations(env):
    for name in ("", ":", "=", "a:b", "a.b", "-x", "x" * 65):
        assert probe(env, "start", name, *LSF).returncode == 2, name
        assert probe(env, "run", name, "--", "true").returncode == 2, name
    assert probe(env, "run", "unknown", "--", "true").returncode == 2
    assert probe(env, "stop", "unknown").returncode == 2
    assert probe(env, "start", "p1", "--", "bsub", "-q", "normal", "-W", "1:00", "/x/hpc-session").returncode == 2  # no -Is
    assert probe(env, "start", "p1", "--", "bsub", "-Is", "/x/hpc-session").returncode == 2                       # no -W
    assert probe(env, "start", "p1", "--", "bsub", "-Is", "-W", "1:00", "bash").returncode == 2                   # no wrapper
    assert probe(env, "start", "p1", "--timeout", "541", *LSF).returncode == 2
    assert probe(env, "run", "p1", "--timeout", "600", "--", "true").returncode == 2


def test_lsf_session_markers_timeout_and_stop(env):
    r = probe(env, "start", "p1", *LSF)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "job=4242 host=fakenode sched=lsf" in r.stdout
    assert probe(env, "start", "p1", *LSF).returncode == 2               # name in use
    r = probe(env, "run", "p1", "--", "echo hello")
    assert r.returncode == 0 and "hello" in r.stdout
    r = probe(env, "run", "p1", "--", "export X=5; sh -c 'exit 3'")
    assert r.returncode == 3, r.stdout
    t0 = time.time()
    r = probe(env, "run", "p1", "--", "sleep 2; echo $X")                 # old markers must not satisfy this wait
    assert r.returncode == 0 and "5" in r.stdout and time.time() - t0 >= 2
    r = probe(env, "run", "p1", "--timeout", "1", "--", "sleep 8; echo late")
    assert r.returncode == 124 and r.elapsed < 6 and "still running" in r.stdout
    assert probe(env, "run", "p1", "--", "true").returncode == 2         # previous command not ended
    r = probe(env, "wait", "p1")
    assert r.returncode == 0 and "late" in r.stdout
    assert (env["run"] / "hpc-probe-logs/p1/4.log").read_text() == "late\n"
    r = probe(env, "stop", "p1")
    assert r.returncode == 0, r.stdout
    assert subprocess.run(env["tm"] + ["has-session", "-t", "=p1"], capture_output=True).returncode != 0
    assert not (env["dir"] / "kills.log").exists()                     # exit was enough, no kill


def test_slurm_readiness(env):
    r = probe(env, "start", "s1", *SLURM)
    assert r.returncode == 0 and "job=777 host=fakenode sched=slurm" in r.stdout, r.stdout + r.stderr
    assert probe(env, "run", "s1", "--", "echo $SLURM_JOB_ID").stdout.startswith("777")
    assert probe(env, "stop", "s1").returncode == 0


def test_rejection_and_type_ahead(env):
    # keys typed while the job pends, and after the rejection, must never run on the login node
    p = subprocess.Popen([sys.executable, str(PROBE), "start", "r1", *LSF], cwd=env["run"],
                         env=dict(env["env"], FAKE_MODE="fail", FAKE_PEND="2"), stdout=subprocess.PIPE, text=True)
    time.sleep(1)
    subprocess.run(env["tm"] + ["send-keys", "-t", "=r1:", f"touch {env['dir']}/typed-ahead", "Enter"])
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 125 and "exited with 255" in out, out
    subprocess.run(env["tm"] + ["send-keys", "-t", "=r1:", f"touch {env['dir']}/typed-after", "Enter"])
    time.sleep(1)
    assert not (env["dir"] / "typed-ahead").exists() and not (env["dir"] / "typed-after").exists()
    assert probe(env, "run", "r1", "--", "true").returncode == 125


def test_login_host_is_not_ready(env):
    r = probe(env, "start", "h1", *LSF, FAKE_MODE="samehost")
    assert r.returncode == 2 and "login node" in r.stdout
    assert probe(env, "run", "h1", "--", "true").returncode == 2
    assert probe(env, "stop", "h1").returncode == 0
    assert "bkill 4242" in (env["dir"] / "kills.log").read_text()


def test_closed_session_and_ended_allocation_return_at_once(env):
    assert probe(env, "start", "c1", *LSF).returncode == 0
    subprocess.run(env["tm"] + ["kill-session", "-t", "=c1"], check=True)
    r = probe(env, "run", "c1", "--", "true")
    assert r.returncode == 125 and r.elapsed < 10   # at once, not after the 540 s default
    assert probe(env, "start", "c2", *LSF).returncode == 0
    subprocess.run(env["tm"] + ["send-keys", "-t", "=c2:", "exit", "Enter"], check=True)   # watchdog release
    time.sleep(1)
    r = probe(env, "run", "c2", "--", "sleep 30")
    assert r.returncode == 125 and r.elapsed < 10


def test_pending_start_times_out_then_wait(env):
    r = probe(env, "start", "w1", "--timeout", "1", *LSF, FAKE_PEND="3")
    assert r.returncode == 124 and "hpc-probe wait w1" in r.stdout
    assert probe(env, "run", "w1", "--", "true").returncode == 2         # not ready
    assert probe(env, "wait", "w1").returncode == 0
    assert probe(env, "run", "w1", "--", "true").returncode == 0
    assert probe(env, "stop", "w1").returncode == 0


def test_stop_kills_exact_id_when_job_outlives_its_shell(env):
    assert probe(env, "start", "k1", *LSF, FAKE_MODE="sticky").returncode == 0
    r = probe(env, "stop", "k1", timeout=120, HPC_PROBE_POLL="1")
    assert r.returncode == 0, r.stdout
    assert (env["dir"] / "kills.log").read_text() == "bkill 4242\n"


def test_attach_prints_read_only_command(env):
    r = probe(env, "attach", "p9")
    assert r.returncode == 0 and "attach -r -t =p9" in r.stdout
