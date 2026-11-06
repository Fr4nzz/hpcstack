"""Tests for skills/cluster-autoresearch/scripts/queue_worker.py with fake tasks (no cluster needed).

  uv run --with pytest python -m pytest tests/
"""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QW = os.path.join(REPO, "skills", "cluster-autoresearch", "scripts", "queue_worker.py")

# A fake task. Modes: quick (exit 0), train (runs until TERM, then writes a checkpoint and exits),
# resume (needs the checkpoint), stubborn (ignores TERM on its first run, finishes on its second).
FAKE_TASK = r'''
import os, signal, sys, time
name, mode = sys.argv[1], sys.argv[2]
with open("order.txt", "a") as fh:
    fh.write(f"{name}:{mode}\n")
if mode == "quick":
    sys.exit(0)
if mode == "train":
    def on_term(signum, frame):
        with open(f"{name}.ckpt", "w") as fh:
            fh.write("epoch=3")
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)
    open(f"{name}.running", "w").close()
    while True:
        time.sleep(0.05)
if mode == "resume":
    with open(f"{name}.ckpt") as fh:
        ck = fh.read()
    with open(f"{name}.resumed", "w") as fh:
        fh.write(ck)
    sys.exit(0)
if mode == "stubborn":
    if os.path.exists(f"{name}.running"):
        sys.exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    open(f"{name}.running", "w").close()
    while True:
        time.sleep(0.05)
'''


@pytest.fixture
def env(tmp_path):
    (tmp_path / "fake_task.py").write_text(FAKE_TASK)
    return tmp_path


def qw(env, *args, check=True):
    r = subprocess.run([sys.executable, QW, *args], cwd=env, capture_output=True, text=True, timeout=60)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def add(env, tid, mode, *opts, name=None):
    return qw(env, "add", "--queue", "q", "--id", tid, *opts, "--",
              sys.executable, "fake_task.py", name or tid, mode)


def start_worker(env, grace="3"):
    return subprocess.Popen([sys.executable, QW, "run", "--queue", "q", "--idle-min", "0.03", "--poll-s", "0.2",
                             "--preempt-poll-s", "0.2", "--preempt-grace", grace],
                            cwd=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_for(path, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            return
        time.sleep(0.05)
    raise AssertionError(f"{path} did not appear")


def finish(worker, timeout=60):
    out, _ = worker.communicate(timeout=timeout)
    return worker.returncode, out


def order(env):
    return (env / "order.txt").read_text().split()


def result(env, tid):
    return json.loads((env / "q" / "results" / f"{tid}.json").read_text())


def test_priority_order(env):
    for tid, prio in [("n1", "normal"), ("n2", "normal"), ("h1", "high"), ("n3", "normal"), ("h2", "high")]:
        add(env, tid, "quick", "--priority", prio)
    rc, out = finish(start_worker(env))
    assert rc == 0, out
    assert order(env) == ["h1:quick", "h2:quick", "n1:quick", "n2:quick", "n3:quick"]


def test_preempt_running_task_resumes_after_high_task(env):
    add(env, "long", "train", "--resume-cmd", f"{sys.executable} fake_task.py long resume")
    w = start_worker(env)
    wait_for(env / "long.running")
    add(env, "urgent", "quick", "--priority", "high", "--est-min", "1")
    add(env, "later", "quick")
    r = qw(env, "preempt", "--queue", "q", "--task", "long", "--reason", "urgent ablation decides the next wave")
    assert "preempt requested for long" in r.stdout
    assert "worker time left" in r.stdout and "unknown" not in r.stdout
    assert (env / "q" / "preempt" / "long.json").exists()
    st = qw(env, "status", "--queue", "q").stdout
    assert "open preempt request: long" in st
    rc, out = finish(w)
    assert rc == 0, out
    # same worker: the checkpoint is written, the high task runs next, then the resume, then the normal task
    assert (env / "long.ckpt").read_text() == "epoch=3"
    assert order(env) == ["long:train", "urgent:quick", "long:resume", "later:quick"]
    assert (env / "long.resumed").read_text() == "epoch=3"
    res = result(env, "long")
    assert res["status"] == "done" and res["attempt"] == 1 and res["preempts"] == 1
    assert not (env / "q" / "preempt" / "long.json").exists()
    claims = [json.loads(x) for x in (env / "q" / "claims.jsonl").read_text().splitlines()]
    assert len({c["worker"] for c in claims}) == 1
    # the log line
    lines = (env / "q" / "preempt.log").read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["task"] == "long" and entry["reason"] == "urgent ablation decides the next wave"
    assert entry["preempt_count"] == 1 and entry["resume_cmd"] is True
    for k in ("time", "worker", "job", "host", "runtime_s"):
        assert k in entry
    st = qw(env, "status", "--queue", "q").stdout
    assert any(line.startswith("long\tnormal\tpreempts=1\tdone") for line in st.splitlines()), st
    assert "open preempt request" not in st
    log = (env / "q" / "logs" / "long.log").read_text()
    assert "preempt requested" in log and "attempt 1" in log


def test_grace_then_kill_without_resume_cmd(env):
    add(env, "stub", "stubborn")
    add(env, "hi", "quick", "--priority", "high")
    # hi is claimed first, so stub starts second
    w = start_worker(env, grace="1")
    wait_for(env / "stub.running")
    r = qw(env, "preempt", "--queue", "q", "--task", "stub", "--reason", "test grace")
    assert "restarts from scratch" in r.stdout
    assert "no high-priority task is queued" in r.stdout
    rc, out = finish(w)
    assert rc == 0, out
    entry = json.loads((env / "q" / "preempt.log").read_text().splitlines()[0])
    assert entry["resume_cmd"] is False and entry["runtime_s"] >= 1.0
    log = (env / "q" / "logs" / "stub.log").read_text()
    assert "killed after the grace period" in log
    assert "no resume command: stub restarts from scratch" in log
    assert "no resume command" in out
    # re-queued with the original command, which finishes on its second run
    assert order(env) == ["hi:quick", "stub:stubborn", "stub:stubborn"]
    assert result(env, "stub")["status"] == "done"


def write_running_state(env, preempts, deadline_s=600, high_est=None):
    q = env / "q"
    q.mkdir(exist_ok=True)
    t = {"id": "x", "cmd": ["true"], "cwd": str(env), "timeout_min": None, "added": "t", "priority": "normal",
         "attempt": preempts, "preempts": preempts}
    lines = [json.dumps(t)]
    if high_est is not None:
        lines.append(json.dumps({"id": "big", "cmd": ["true"], "cwd": str(env), "priority": "high",
                                 "est_min": high_est}))
    (q / "queue.jsonl").write_text("\n".join(lines) + "\n")
    c = {"id": "x", "worker": "host:123:4", "time": "t", "attempt": preempts,
         "deadline": time.time() + deadline_s, "deadline_iso": "d", "preempt_grace_s": 0}
    (q / "claims.jsonl").write_text(json.dumps(c) + "\n")


def test_third_preempt_refused(env):
    write_running_state(env, preempts=2)
    r = qw(env, "preempt", "--queue", "q", "--task", "x", "--reason", "again", check=False)
    assert r.returncode == 1
    assert "already preempted 2 times" in r.stdout
    assert not (env / "q" / "preempt" / "x.json").exists()


def test_fit_warning_is_not_a_refusal(env):
    write_running_state(env, preempts=0, deadline_s=600, high_est=30)
    r = qw(env, "preempt", "--queue", "q", "--task", "x", "--reason", "big job")
    assert "big needs about 30 min" in r.stdout
    assert (env / "q" / "preempt" / "x.json").exists()
    st = qw(env, "status", "--queue", "q").stdout
    assert "x\tnormal\tpreempts=0\trunning on host:123:4" in st
    assert "big\thigh\tpreempts=0\tqueued" in st
    assert "open preempt request: x" in st


def test_preempt_non_running_task(env):
    add(env, "waiting", "quick")
    r = qw(env, "preempt", "--queue", "q", "--task", "waiting", "--reason", "r", check=False)
    assert r.returncode == 1 and "still queued" in r.stdout
    r = qw(env, "preempt", "--queue", "q", "--task", "ghost", "--reason", "r", check=False)
    assert r.returncode == 1 and "no task ghost" in r.stdout
    assert not (env / "q" / "preempt").exists()
    rc, out = finish(start_worker(env))
    assert rc == 0, out
    r = qw(env, "preempt", "--queue", "q", "--task", "waiting", "--reason", "r", check=False)
    assert r.returncode == 1 and "already ended" in r.stdout
    assert not (env / "q" / "preempt").exists()


def test_reason_required(env):
    write_running_state(env, preempts=0)
    r = qw(env, "preempt", "--queue", "q", "--task", "x", check=False)
    assert r.returncode == 2 and "--reason" in r.stderr
    r = qw(env, "preempt", "--queue", "q", "--task", "x", "--reason", "   ", check=False)
    assert r.returncode == 2 and "non-empty --reason" in r.stderr
    assert not (env / "q" / "preempt").exists()


def test_old_queue_without_priority_fields(env):
    q = env / "q"
    (q / "results").mkdir(parents=True)
    old = [{"id": f"o{i}", "cmd": [sys.executable, "fake_task.py", f"o{i}", "quick"], "cwd": str(env),
            "timeout_min": None, "added": "2026-01-01T00:00:00"} for i in range(3)]
    (q / "queue.jsonl").write_text("".join(json.dumps(t) + "\n" for t in old))
    # o0 was claimed and finished by an old worker, without attempt fields
    (q / "claims.jsonl").write_text(json.dumps({"id": "o0", "worker": "old:1:2", "time": "t"}) + "\n")
    (q / "results" / "o0.json").write_text(json.dumps({"id": "o0", "status": "done", "returncode": 0,
                                                       "worker": "old:1:2", "runtime_s": 1.0}))
    st = qw(env, "status", "--queue", "q").stdout
    assert "o0\tnormal\tpreempts=0\tdone" in st and "o1\tnormal\tpreempts=0\tqueued" in st
    add(env, "new_high", "quick", "--priority", "high")
    rc, out = finish(start_worker(env))
    assert rc == 0, out
    assert order(env) == ["new_high:quick", "o1:quick", "o2:quick"]


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGUSR2])
def test_worker_signal_still_stops_everything(env, sig):
    add(env, "long", "train", "--resume-cmd", f"{sys.executable} fake_task.py long resume")
    add(env, "next", "quick")
    w = start_worker(env)
    wait_for(env / "long.running")
    w.send_signal(sig)
    rc, out = finish(w)
    assert rc == 128 + sig, out
    assert result(env, "long")["status"] == "interrupted"
    assert not (env / "q" / "preempt.log").exists()
    assert order(env) == ["long:train"]  # not re-queued, nothing else claimed
    st = qw(env, "status", "--queue", "q").stdout
    assert "next\tnormal\tpreempts=0\tqueued" in st


# --- in-process tests of single behaviours (round 1 review fixes) ---

def load_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("queue_worker", QW)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def qwm():
    return load_module()


def claimed_task(qwm, q, tid="x", cmd=("sleep", "30"), **fields):
    entry = {"id": tid, "cmd": list(cmd), "cwd": str(q.parent), "timeout_min": None, "added": "t",
             "priority": "normal", **fields}
    q.mkdir(exist_ok=True)
    qwm.append_jsonl(str(q / "queue.jsonl"), entry)
    return qwm.claim(str(q), "w", time.time() + 3600, 1)


def test_grace_covers_whole_process_group(qwm, tmp_path):
    # the leader exits at once on TERM, its child needs 2 s to write the checkpoint
    (tmp_path / "child.py").write_text(
        "import signal, sys, time\n"
        "def h(s, f):\n    time.sleep(2)\n    open('ckpt', 'w').write('ok')\n    sys.exit(0)\n"
        "signal.signal(signal.SIGTERM, h)\nopen('ready', 'w').close()\n"
        "while True:\n    time.sleep(0.05)\n")
    proc = subprocess.Popen(["bash", "-c", f"trap 'exit 0' TERM; {sys.executable} child.py & wait"],
                            cwd=tmp_path, start_new_session=True)
    wait_for(tmp_path / "ready")
    killed = qwm.stop_group(proc, signal.SIGTERM, 10)
    assert proc.returncode == 0
    assert killed is False
    assert (tmp_path / "ckpt").read_text() == "ok"


def test_preempted_result_written_before_requeue_under_lock(qwm, tmp_path, monkeypatch):
    q = tmp_path / "q"
    t = claimed_task(qwm, q, resume_cmd=["true"])
    events = []

    real_locked, real_write, real_append = qwm.locked, qwm.write_json, qwm.append_jsonl

    from contextlib import contextmanager

    @contextmanager
    def rec_locked(qq):
        with real_locked(qq):
            events.append("lock")
            yield
            events.append("unlock")

    def rec_write(path, obj):
        events.append("result" if "/results/" in path else "write")
        real_write(path, obj)

    def rec_append(path, obj):
        events.append(os.path.basename(path))
        real_append(path, obj)

    monkeypatch.setattr(qwm, "locked", rec_locked)
    monkeypatch.setattr(qwm, "write_json", rec_write)
    monkeypatch.setattr(qwm, "append_jsonl", rec_append)
    new, line = qwm.requeue_entries(t, "w", "r", 1.0)
    qwm.publish_result(str(q), t, {"id": "x", "status": "preempted", "attempt": 0}, (new, line))
    assert events == ["lock", "result", "queue.jsonl", "preempt.log", "unlock"]


def test_late_request_removed_with_result(qwm, tmp_path, monkeypatch, capsys):
    q = tmp_path / "q"
    t = claimed_task(qwm, q, cmd=["true"])
    real_publish = qwm.publish_result

    def request_then_publish(qq, tt, res, requeue=None):
        # a request lands after the task ended but before its result is published
        assert qwm.main(["preempt", "--queue", qq, "--task", "x", "--reason", "late"]) == 0
        assert (q / "preempt" / "x.json").exists()
        real_publish(qq, tt, res, requeue)

    monkeypatch.setattr(qwm, "publish_result", request_then_publish)
    r = qwm.run_task(str(q), t, "w", time.time() + 3600, {"preempt_poll_s": 0.05})
    assert r["status"] == "done"
    assert not (q / "preempt" / "x.json").exists()
    assert "arrived after it ended" in capsys.readouterr().out


def write_request(qwm, q, attempt=0):
    qwm.write_json(str(q / "preempt" / "x.json"), {"task": "x", "attempt": attempt, "reason": "r", "time": "t"})


def test_grace_capped_by_task_budget(qwm, tmp_path, monkeypatch):
    q = tmp_path / "q"
    t = claimed_task(qwm, q, timeout_min=0.05)  # 3 s budget
    write_request(qwm, q)
    seen = []

    def fake_stop(proc, sig=signal.SIGTERM, grace=30.0):
        seen.append(grace)
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        return False

    monkeypatch.setattr(qwm, "stop_group", fake_stop)
    r = qwm.run_task(str(q), t, "w", time.time() + 3600, {"preempt_grace": 120.0, "preempt_poll_s": 0.05})
    assert r["status"] == "preempted"
    assert 1.0 <= seen[0] <= 3.0
    assert r["grace_s"] == round(seen[0], 1)


def test_no_budget_left_is_a_timeout(qwm, tmp_path):
    q = tmp_path / "q"
    t = claimed_task(qwm, q)
    write_request(qwm, q)
    r = qwm.run_task(str(q), t, "w", time.time() - 10, {"preempt_grace": 120.0, "preempt_poll_s": 0.05})
    assert r["status"] == "timeout"
    assert not (q / "preempt.log").exists()
    assert not (q / "preempt" / "x.json").exists()
    assert [e.get("attempt", 0) for e in qwm.read_jsonl(str(q / "queue.jsonl"))] == [0]


def test_stale_request_from_older_attempt_is_overwritten(env):
    write_running_state(env, preempts=1)
    (env / "q" / "preempt").mkdir()
    (env / "q" / "preempt" / "x.json").write_text(json.dumps({"task": "x", "attempt": 0, "reason": "old"}))
    r = qw(env, "preempt", "--queue", "q", "--task", "x", "--reason", "new")
    assert "already open" not in r.stdout
    req = json.loads((env / "q" / "preempt" / "x.json").read_text())
    assert req["attempt"] == 1 and req["reason"] == "new"


def test_preempted_high_task_queues_behind_waiting_high_task(qwm, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    q = str(tmp_path / "q")
    for tid, prio in [("h_old", "high"), ("n1", "normal")]:
        qwm.main(["add", "--queue", q, "--id", tid, "--priority", prio, "--", "true"])
    t = qwm.claim(q, "w")
    assert t["id"] == "h_old"
    # the new high task is added before preempting h_old for it
    qwm.main(["add", "--queue", q, "--id", "h_new", "--priority", "high", "--", "true"])
    new, line = qwm.requeue_entries(t, "w", "r", 1.0)
    assert new["priority"] == "high" and new["front"] is False
    qwm.publish_result(q, t, {"id": "h_old", "status": "preempted", "attempt": 0}, (new, line))
    got = [qwm.claim(q, "w") for _ in range(3)]
    assert [(x["id"], x.get("attempt", 0)) for x in got] == [("h_new", 0), ("h_old", 1), ("n1", 0)]


def test_stale_request_cleanup_does_not_delete_fresh_request(qwm, tmp_path, monkeypatch):
    # interleaving: the worker reads a stale attempt-0 request, `preempt` then writes the attempt-1
    # request and reports success, then the worker cleans up. The fresh request must survive.
    q = tmp_path / "q"
    t = claimed_task(qwm, q, attempt=1, preempts=1)
    write_request(qwm, q, attempt=0)
    real_read = qwm.read_json
    state = {"done": False}

    def read_then_preempt(path):
        out = real_read(path)
        if path.endswith(os.path.join("preempt", "x.json")) and not state["done"]:
            state["done"] = True
            assert qwm.main(["preempt", "--queue", str(q), "--task", "x", "--reason", "fresh"]) == 0
        return out

    monkeypatch.setattr(qwm, "read_json", read_then_preempt)
    req = qwm.preempt_request(str(q), t)
    assert state["done"]
    assert req is not None and req["attempt"] == 1 and req["reason"] == "fresh"
    assert json.loads((q / "preempt" / "x.json").read_text())["attempt"] == 1
