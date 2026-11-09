"""Tests for skills/cluster-ops/scripts/hpc-probe with fake bsub, srun, bjobs, bkill, squeue and scancel on a private
tmux socket. The tmux server is started first with other settings (another HPC_PROBE_DIR, a bare PATH), as a
long-lived server would be, and a bystander session stays open the whole time and must never receive input.
Skipped without tmux."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "skills/cluster-ops/scripts/hpc-probe"
WRAP = str(ROOT / "skills/cluster-ops/scripts/hpc-session")
WRAP_SLURM = str(ROOT / "skills/cluster-ops/scripts/hpc-session-slurm")
pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

# Fake scheduler. Jobs live in $FAKE_DIR/job.<id> as "id name user state". With FAKE_GATE set, a job pends until
# the test creates $FAKE_DIR/release, so "typed while pending" is really typed while pending.
FAKE_ALLOC = r"""#!/usr/bin/env bash
dir=$FAKE_DIR; mode=${FAKE_MODE:-ok}; me=$(basename "$0"); jname=; prev=
for a in "$@"; do [[ $prev == -J ]] && jname=$a; [[ $a == --job-name=* ]] && jname=${a#--job-name=}; prev=$a; done
echo "$*" >> "$dir/alloc-args"
c=$(cat "$dir/count.$me" 2>/dev/null || echo 0); echo $((c + 1)) > "$dir/count.$me"
if [[ $me == bsub ]]; then id=$((4242 + c)); var=LSB_JOBID; P=PEND; R=RUN; D=DONE; X=EXIT
  echo "Job <$id> is submitted to queue <normal>."; echo "<<Waiting for dispatch ...>>"
else id=$((777 + c)); var=SLURM_JOB_ID; P=PENDING; R=RUNNING; D=COMPLETED; X=FAILED
  echo "srun: job $id queued and waiting for resources"; fi
setst() { echo "$id $jname $USER $1" > "$dir/job.$id"; }
setst $P
if [[ -n ${FAKE_GATE:-} ]]; then until [[ -e $dir/release ]]; do sleep 0.1; done; else sleep "${FAKE_PEND:-1}"; fi
if [[ $mode == fail ]]; then echo "Request aborted by esub. Job not submitted."; setst $X; exit 255; fi
[[ $me == bsub ]] && echo "<<Starting on fakenode>>" || echo "srun: job $id has been allocated resources"
setst $R
host=fakenode; [[ $mode == samehost ]] && host=$(hostname)
printf 'hostname() { echo %s; }\nPS1="job$ "\n' "$host" > "$dir/rc.$id"
env "$var=$id" bash --rcfile "$dir/rc.$id" -i
s=$?
[[ $mode == sticky ]] || setst $D
exit $s
"""
FAKE_BJOBS = r"""#!/usr/bin/env bash
# bjobs -a -u USER -J NAME -noheader -o "..."
while (( $# )); do case $1 in -J) n=$2; shift ;; -u) u=$2; shift ;; esac; shift; done
hit=$(cat "$FAKE_DIR"/job.* 2>/dev/null | awk -v n="$n" -v u="$u" '$2 == n && $3 == u')
[[ -n $hit ]] && echo "$hit" || { echo "Job <$n> is not found" >&2; exit 255; }
"""
FAKE_SQUEUE = r"""#!/usr/bin/env bash
# squeue -h -u USER -n NAME -o "..."
while (( $# )); do case $1 in -n) n=$2; shift ;; -u) u=$2; shift ;; esac; shift; done
cat "$FAKE_DIR"/job.* 2>/dev/null | awk -v n="$n" -v u="$u" '$2 == n && $3 == u'
exit 0
"""
FAKE_KILL = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$FAKE_DIR/kills.log"
f=$FAKE_DIR/job.$1
if [[ -e $f ]]; then read -r id n u s < "$f"; [[ $0 == *bkill ]] && s=EXIT || s=CANCELLED; echo "$id $n $u $s" > "$f"; fi
echo "Job <$1> is being terminated"
"""
FAKE_TMUX_FAILING_NEW = r"""#!/usr/bin/env bash
# real tmux, except that new-session records the saved environment file and then fails
if [[ " $* " == *" new-session "* ]]; then
  f=$(ls "$HPC_PROBE_DIR"/*.env); stat -c %a "$f" > "$FAKE_DIR/env-mode"; cp "$f" "$FAKE_DIR/env-copy"; exit 1
fi
exec tmux -L "$REAL_SOCK" "$@"
"""


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("bsub", FAKE_ALLOC), ("srun", FAKE_ALLOC), ("bjobs", FAKE_BJOBS), ("squeue", FAKE_SQUEUE),
                       ("bkill", FAKE_KILL), ("scancel", FAKE_KILL), ("tmux-failing-new", FAKE_TMUX_FAILING_NEW)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    sock = f"hpcprobe-{os.getpid()}-{tmp_path.name}"[:90]
    tm = ["tmux", "-L", sock]
    e = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", FAKE_DIR=str(tmp_path), HPC_PROBE_DIR=str(tmp_path / "st"),
             HPC_PROBE_TMUX=" ".join(tm), HPC_PROBE_POLL="0.2", HPC_PROBE_RESEND="1", REAL_SOCK=sock)
    e.pop("TMUX", None)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # a server that was started elsewhere, with other settings; its first session is the bystander
    server_env = dict(e, HPC_PROBE_DIR=str(tmp_path / "other-dir"), PATH="/usr/bin:/bin")
    subprocess.run(tm + ["new-session", "-d", "-s", "bystander", f"cat > {tmp_path}/bystander.txt"], env=server_env,
                   check=True)
    received = None
    try:
        yield {"env": e, "dir": tmp_path, "run": run_dir, "tm": tm}
    finally:
        received = (tmp_path / "bystander.txt").read_text()
        subprocess.run(tm + ["kill-server"], capture_output=True)
    assert received == "", "the bystander session received input"


def probe(env, *args, timeout=90, **extra):
    e = dict(env["env"], **extra)
    t0 = time.time()
    r = subprocess.run([sys.executable, str(PROBE), *args], cwd=env["run"], env=e, capture_output=True, text=True,
                       timeout=timeout)
    r.elapsed = time.time() - t0
    return r


def tmux(env, *args, **kw):
    return subprocess.run(env["tm"] + list(args), capture_output=True, text=True, **kw)


def pane_text(env, target):
    return tmux(env, "capture-pane", "-p", "-J", "-t", target, "-S", "-200").stdout


def kills(env):
    f = env["dir"] / "kills.log"
    return f.read_text() if f.exists() else ""


def wait_for(cond, timeout=20):
    t_end = time.time() + timeout
    while time.time() < t_end:
        if cond():
            return True
        time.sleep(0.1)
    return False


LSF = ["--", "bsub", "-Is", "-q", "normal", "-W", "2:00", WRAP]
SLURM = ["--", "srun", "--time=02:00:00", "--pty", WRAP_SLURM]


def test_refuses_bad_names_timeouts_and_allocations(env):
    for name in ("", ":", "=", "a:b", "a.b", "-x", "x" * 65):
        assert probe(env, "start", name, *LSF).returncode == 2, name
        assert probe(env, "run", name, "--", "true").returncode == 2, name
    assert probe(env, "run", "unknown", "--", "true").returncode == 2
    assert probe(env, "stop", "unknown").returncode == 2
    for t in ("nan", "inf", "-inf", "0", "541", "x"):
        assert probe(env, "start", "p1", "--timeout", t, *LSF).returncode == 2, t
        assert probe(env, "run", "p1", "--timeout", t, "--", "true").returncode == 2, t
    bad = [["bsub", "-q", "normal", "-W", "1:00", WRAP],                          # no -Is
           ["bsub", "-Is", WRAP],                                                # no walltime
           ["bsub", "-Is", "-W", "1:00", "-J", "hpc-session-x", "bash"],         # decoy job name, bare bash
           ["bsub", "-Is", "-W", "1:00", "-q", "hpc-session", "bash"],           # decoy option value
           ["bsub", "-Is", "-W", "1:00", "-q", "normal", "bash", WRAP],          # wrapper only an argument of bash
           ["bsub", "-Is", "-W", "1:00", WRAP + "-old"],                         # wrong wrapper name
           ["bsub", "-Is", "-W", "1:00", "/nonexistent/hpc-session"],            # not an executable file
           ["bsub", "-Is", "-W", "1:00", WRAP_SLURM],                            # SLURM wrapper under LSF
           ["srun", "--time=1:00:00", "--pty", "bash", WRAP_SLURM],             # wrapper only an argument
           ["srun", "--time=1:00:00", "--pty", "--job-name=x", WRAP_SLURM],     # own job name
           ["sbatch", "--time=1:00:00", WRAP_SLURM]]
    for argv in bad:
        r = probe(env, "start", "p1", "--", *argv)
        assert r.returncode == 2, (argv, r.stdout)
    assert not (env["dir"] / "alloc-args").exists()                          # nothing was submitted


def test_lsf_markers_commands_timeout_and_stop(env):
    r = probe(env, "start", "p1", *LSF)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "job=4242 host=fakenode sched=lsf job_name=hpc-probe-p1-" in r.stdout
    assert "-J hpc-probe-p1-" in (env["dir"] / "alloc-args").read_text()
    assert probe(env, "start", "p1", *LSF).returncode == 2                   # name in use
    r = probe(env, "run", "p1", "--", "echo hello # a comment")
    assert r.returncode == 0 and "hello" in r.stdout, r.stdout
    r = probe(env, "run", "p1", "--", "echo it's")                            # unbalanced quote
    assert r.returncode not in (0, 124) and r.elapsed < 15, r.stdout
    r = probe(env, "run", "p1", "--", "export X=5; sh -c 'exit 3'")
    assert r.returncode == 3, r.stdout
    t0 = time.time()
    r = probe(env, "run", "p1", "--", "sleep 2; echo $X")                     # old markers must not end this wait
    assert r.returncode == 0 and "5" in r.stdout and time.time() - t0 >= 2
    r = probe(env, "run", "p1", "--timeout", "1", "--", "sleep 8; echo late")
    assert r.returncode == 124 and r.elapsed < 6 and "still running" in r.stdout
    assert probe(env, "run", "p1", "--", "true").returncode == 2             # previous command not ended
    r = probe(env, "wait", "p1")
    assert r.returncode == 0 and "late" in r.stdout
    assert (env["run"] / "hpc-probe-logs/p1/5.log").read_text() == "late\n"
    assert "attach -r -t '$" in probe(env, "attach", "p1").stdout          # the session id, not the name
    r = probe(env, "stop", "p1")
    assert r.returncode == 0, r.stdout
    assert tmux(env, "has-session", "-t", "=p1").returncode != 0
    assert kills(env) == ""                                                  # exit was enough, no kill


def test_slurm_readiness_and_completed_is_ended(env):
    r = probe(env, "start", "s1", *SLURM)
    assert r.returncode == 0 and "job=777 host=fakenode sched=slurm" in r.stdout, r.stdout + r.stderr
    assert "--job-name=hpc-probe-s1-" in (env["dir"] / "alloc-args").read_text()
    assert probe(env, "run", "s1", "--", "echo $SLURM_JOB_ID").stdout.startswith("777")
    r = probe(env, "stop", "s1")                     # squeue still lists the job as COMPLETED: that is ended
    assert r.returncode == 0 and r.elapsed < 15, r.stdout
    assert kills(env) == ""


def test_type_ahead_while_pending_and_after_rejection_never_runs(env):
    p = subprocess.Popen([sys.executable, str(PROBE), "start", "r1", *LSF], cwd=env["run"],
                         env=dict(env["env"], FAKE_MODE="fail", FAKE_GATE="1"), stdout=subprocess.PIPE, text=True)
    try:
        assert wait_for(lambda: "Waiting for dispatch" in pane_text(env, "=r1:"))   # the job is pending
        tmux(env, "send-keys", "-t", "=r1:", f"touch {env['dir']}/typed-ahead", "Enter", check=True)
        assert wait_for(lambda: "typed-ahead" in pane_text(env, "=r1:"))          # the keys arrived
        (env["dir"] / "release").touch()
        out, _ = p.communicate(timeout=60)
    finally:
        p.kill()
    assert p.returncode == 125 and "exited with 255" in out, out
    tmux(env, "send-keys", "-t", "=r1:", f"touch {env['dir']}/typed-after", "Enter", check=True)
    assert wait_for(lambda: "typed-after" in pane_text(env, "=r1:"))
    time.sleep(1)
    assert not (env["dir"] / "typed-ahead").exists() and not (env["dir"] / "typed-after").exists()
    assert probe(env, "run", "r1", "--", "true").returncode == 125


def test_login_host_is_not_ready(env):
    r = probe(env, "start", "h1", *LSF, FAKE_MODE="samehost")
    assert r.returncode == 2 and "login node" in r.stdout
    assert probe(env, "run", "h1", "--", "true").returncode == 2
    assert probe(env, "stop", "h1").returncode == 0
    assert kills(env) == "bkill 4242\n"


def test_closed_session_and_ended_allocation_return_at_once(env):
    # "by" is a prefix of "bystander": an inexact target would hit the bystander
    assert probe(env, "start", "by", *LSF).returncode == 0
    tmux(env, "kill-session", "-t", "=by", check=True)
    r = probe(env, "run", "by", "--", "echo should-not-arrive")
    assert r.returncode == 125 and r.elapsed < 10
    assert probe(env, "start", "c2", *LSF).returncode == 0
    tmux(env, "send-keys", "-t", "=c2:", "exit", "Enter", check=True)        # watchdog release
    time.sleep(1)
    r = probe(env, "run", "c2", "--", "sleep 30")
    assert r.returncode == 125 and r.elapsed < 10


def test_replacement_session_with_same_name_gets_nothing(env):
    assert probe(env, "start", "p1", *LSF, FAKE_MODE="sticky").returncode == 0   # the job outlives its client
    tmux(env, "kill-session", "-t", "=p1", check=True)
    tmux(env, "new-session", "-d", "-s", "p1", f"cat > {env['dir']}/replacement.txt", check=True)
    r = probe(env, "run", "p1", "--", f"touch {env['dir']}/ran")
    assert r.returncode == 3 and "Sent nothing" in r.stdout, r.stdout
    assert probe(env, "run", "p1", "--", "true").returncode == 3
    r = probe(env, "stop", "p1")
    assert r.returncode == 0 and "left alone" in r.stdout, r.stdout
    assert tmux(env, "has-session", "-t", "=p1").returncode == 0               # the replacement still runs
    assert kills(env) == "bkill 4242\n"                    # the job outlived its killed client: that exact id
    time.sleep(0.5)
    assert (env["dir"] / "replacement.txt").read_text() == "" and not (env["dir"] / "ran").exists()


def test_added_active_pane_gets_nothing(env):
    assert probe(env, "start", "p2", *LSF).returncode == 0
    tmux(env, "split-window", "-t", "=p2:", f"cat > {env['dir']}/active.txt", check=True)   # now the active pane
    r = probe(env, "run", "p2", "--", "echo mine")
    assert r.returncode == 0 and "mine" in r.stdout
    assert probe(env, "stop", "p2").returncode == 0
    assert tmux(env, "list-panes", "-t", "=p2:", "-F", "#{pane_id}").stdout.count("%") == 1   # only that pane left
    time.sleep(0.5)
    assert (env["dir"] / "active.txt").read_text() == ""


def test_reused_job_id_is_never_killed(env):
    assert probe(env, "start", "p3", *LSF).returncode == 0
    assert probe(env, "run", "p3", "--", "exit").returncode == 125           # the job ends with its shell
    # the scheduler gives id 4242 to another job (another probe of the same user), now running
    (env["dir"] / "job.4242").write_text(f"4242 hpc-probe-other-abcd {os.environ.get('USER', '')} RUN\n")
    r = probe(env, "stop", "p3")
    assert r.returncode == 0, r.stdout
    assert kills(env) == ""


def test_pending_start_times_out_then_wait(env):
    r = probe(env, "start", "w1", "--timeout", "1", *LSF, FAKE_GATE="1")
    assert r.returncode == 124 and "hpc-probe wait w1" in r.stdout
    assert probe(env, "run", "w1", "--", "true").returncode == 2             # not ready
    (env["dir"] / "release").touch()
    assert probe(env, "wait", "w1").returncode == 0
    assert probe(env, "run", "w1", "--", "true").returncode == 0
    assert probe(env, "stop", "w1").returncode == 0


def test_stop_kills_exact_id_when_job_outlives_its_shell(env):
    assert probe(env, "start", "k1", *LSF, FAKE_MODE="sticky").returncode == 0
    r = probe(env, "stop", "k1", timeout=120, HPC_PROBE_POLL="1")
    assert r.returncode == 0, r.stdout
    assert kills(env) == "bkill 4242\n"


def test_environment_file_secret_filter_and_cleanup(env):
    secrets = {"HF_TOKEN": "s1", "MY_PASS": "s2", "OPENAI_KEY": "s3", "MYSQL_PWD": "s4", "KEEP_ME": "kept"}
    assert probe(env, "start", "e1", *LSF, **secrets).returncode == 0
    assert not list((env["dir"] / "st").glob("*.env"))                       # removed once the launcher read it
    r = probe(env, "run", "e1", "--", "echo ${HF_TOKEN:-unset} ${MY_PASS:-unset} ${OPENAI_KEY:-unset} "
                                     "${MYSQL_PWD:-unset} ${KEEP_ME:-unset}")
    assert r.stdout.splitlines()[0] == "unset unset unset unset kept", r.stdout
    assert probe(env, "stop", "e1").returncode == 0
    # a failing tmux new-session: the file was 0600 without the secrets, and it is gone afterwards
    r = probe(env, "start", "e2", *LSF, HPC_PROBE_TMUX=str(env["dir"] / "bin/tmux-failing-new"), **secrets)
    assert r.returncode == 2 and "new-session failed" in r.stdout, r.stdout
    assert (env["dir"] / "env-mode").read_text().strip() == "600"
    saved = (env["dir"] / "env-copy").read_text()
    assert "KEEP_ME" in saved and not any(k in saved for k in ("HF_TOKEN", "MY_PASS", "OPENAI_KEY", "MYSQL_PWD"))
    assert not list((env["dir"] / "st").glob("*.env"))
    assert probe(env, "start", "e2", *LSF).returncode == 0                   # the name is free again
    assert probe(env, "stop", "e2").returncode == 0
