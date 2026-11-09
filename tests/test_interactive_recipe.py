"""Runs the code block in references/interactive.md ("Driving the session from an agent") call by call, on a
private tmux socket, with a fake `bsub -Is` that pends and then starts a job shell, or is rejected. Skipped
without tmux. It checks the documented commands, not LSF itself."""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

MD = Path(__file__).resolve().parents[1] / "skills/cluster-ops/references/interactive.md"
SOCK = f"hpcstack-test-{os.getpid()}"

FAKE_BSUB = """#!/usr/bin/env bash
echo "Job <4242> is submitted to queue <normal>."
echo "<<Waiting for dispatch ...>>"
sleep 2
if [[ $1 == fail ]]; then echo "Request aborted by esub. Job not submitted."; exit 255; fi
echo "<<Starting on fakehost>>"
LSB_JOBID=4242 exec env PS1='$ ' bash --norc -i
"""
FAKE_CHECK = "#!/usr/bin/env bash\nsleep 3; echo checked; exit 3\n"

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")


def steps():
    block = re.search(r"## Driving the session from an agent.*?```bash\n(.*?)```", MD.read_text(), re.S).group(1)
    return re.split(r"\n(?=# \d\.)", block.strip())


def run(cmd, d, mode, name=None):
    cmd = cmd.replace("tmux ", f"tmux -L {SOCK} ")
    cmd = cmd.replace("bsub -Is ... hpc-session", f"{d}/fakebsub {mode}").replace("python check.py", f"{d}/fakecheck")
    if name:
        cmd = re.sub(r"^S=probe-\d{4}-\d{6}$", f"S={name}", cmd, flags=re.M)
    return subprocess.run(["bash", "-c", cmd], cwd=d, capture_output=True, text=True, timeout=120)


@pytest.fixture
def d(tmp_path):
    for name, body in (("fakebsub", FAKE_BSUB), ("fakecheck", FAKE_CHECK)):
        (tmp_path / name).write_text(body)
        (tmp_path / name).chmod(0o755)
    yield tmp_path
    subprocess.run(["tmux", "-L", SOCK, "kill-server"], capture_output=True)


def start(d, mode):
    s1 = steps()[0]
    name = run(s1, d, mode).stdout.split()[0]
    again = run(s1.replace('S=probe-$(date +%m%d-%H%M%S); echo "$S"', f"S={name}"), d, mode)
    assert again.returncode != 0 and "duplicate session" in again.stderr   # a used name is refused
    return name


def test_job_starts_and_markers_are_per_command(d):
    name = start(d, "ok")
    ready = run(steps()[1], d, "ok", name).stdout.split()
    assert ready[-1].startswith("__READY_4242_")
    s3 = steps()[2]
    assert run(s3, d, "ok", name).stdout.split()[:2] == ["__END_3_3", "checked"]
    # command 4 while __END_3_3 is still on screen: the wait must last for the 3 s command
    s4 = s3.replace("__END_3_", "__END_4_").replace("probe-3.log", "probe-4.log")
    t0 = time.time()
    out = run(s4, d, "ok", name).stdout.split()
    assert out[:2] == ["__END_4_3", "checked"] and time.time() - t0 >= 3


def test_rejected_submission_runs_nothing_on_the_login_node(d):
    name = start(d, "fail")
    subprocess.run(["tmux", "-L", SOCK, "send-keys", "-t", f"={name}:", f"touch {d}/typed-ahead", "Enter"])
    assert run(steps()[1], d, "fail", name).stdout.split()[-1] == "__ALLOC_ENDED_255"
    time.sleep(1)
    assert not (d / "typed-ahead").exists()
