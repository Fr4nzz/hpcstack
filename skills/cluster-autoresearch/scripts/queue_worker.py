#!/usr/bin/env python3
"""queue_worker.py: a file-based task queue drained by batch worker jobs.

The agent adds tasks. One or more batch jobs run `queue_worker.py run`, claim tasks under a file lock,
run them, and exit when the queue has been empty for --idle-min minutes, when --max-hours is reached,
or when a STOP file appears. If the agent dies, the workers finish the queue and exit on their own.

  queue_worker.py add    --queue Q --id ID [--timeout-min N] -- CMD [ARGS...]
  queue_worker.py run    --queue Q [--idle-min 25] [--max-hours 7.5] [--poll-s 30]
  queue_worker.py status --queue Q
  touch Q/STOP          workers finish their current task and exit

Layout of Q: queue.jsonl (tasks), claims.jsonl (who took what), results/<id>.json, logs/<id>.log,
.lock. A task's command runs with cwd = the directory `add` was called from, in its own session, so
the whole process group is killed on timeout. Tasks are claimed once. Re-queue a crashed task with a
new id.

Exit codes of `run`: 0 when the worker stopped normally (idle, max-hours, STOP), whatever the tasks'
own results (read results/<id>.json). 128+N when signal N stopped it (bkill, scancel, the walltime warning
sent by `bsub -wa USR2` or `sbatch --signal`): the running task's process group is stopped first and its
result is written as "interrupted", so `done(<job>)` dependents do not fire on a half-drained queue.
"""
import argparse
import fcntl
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager


def paths(q):
    return {k: os.path.join(q, v) for k, v in
            {"queue": "queue.jsonl", "claims": "claims.jsonl", "lock": ".lock", "stop": "STOP",
             "results": "results", "logs": "logs"}.items()}


@contextmanager
def locked(q):
    with open(paths(q)["lock"], "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # a half-written line from a crashed writer is ignored
    return out


def append_jsonl(path, obj):
    with open(path, "a") as fh:
        fh.write(json.dumps(obj) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


class Interrupted(Exception):
    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR2)


def _on_signal(signum, frame):
    for sig in STOP_SIGNALS:  # the scheduler often follows up (INT, TERM, KILL): finish the cleanup once
        signal.signal(sig, signal.SIG_IGN)
    raise Interrupted(signum)


def stop_group(proc):
    """TERM the task's process group, wait up to 30 s for the task, then KILL the group."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # the task's own children, or the task if it ignored TERM
    except ProcessLookupError:
        pass


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def worker_name():
    job = os.environ.get("LSB_JOBID") or os.environ.get("SLURM_JOB_ID") or "local"
    idx = os.environ.get("LSB_JOBINDEX") or os.environ.get("SLURM_ARRAY_TASK_ID")
    return f"{socket.gethostname()}:{job}" + (f"[{idx}]" if idx and idx != "0" else "") + f":{os.getpid()}"


def cmd_add(a):
    p = paths(a.queue)
    os.makedirs(a.queue, exist_ok=True)
    if not a.cmd:
        sys.exit("add needs a command after --")
    with locked(a.queue):
        if any(t["id"] == a.id for t in read_jsonl(p["queue"])):
            sys.exit(f"task id {a.id} already queued. Use a new id")
        append_jsonl(p["queue"], {"id": a.id, "cmd": a.cmd, "cwd": os.getcwd(),
                                  "timeout_min": a.timeout_min, "added": now()})
    print(f"queued {a.id}: {shlex.join(a.cmd)}")


def claim(q, me):
    p = paths(q)
    with locked(q):
        taken = {c["id"] for c in read_jsonl(p["claims"])}
        for t in read_jsonl(p["queue"]):
            if t["id"] not in taken:
                append_jsonl(p["claims"], {"id": t["id"], "worker": me, "time": now()})
                return t
    return None


def run_task(q, t, me, deadline):
    p = paths(q)
    os.makedirs(p["results"], exist_ok=True)
    os.makedirs(p["logs"], exist_ok=True)
    timeout = t.get("timeout_min")
    limit = max(1.0, deadline - time.time())
    if timeout:
        limit = min(limit, timeout * 60)
    start = time.time()
    status, rc, interrupted = "done", None, None
    with open(os.path.join(p["logs"], f"{t['id']}.log"), "w") as log:
        log.write(f"# {now()} {me}\n# {shlex.join(t['cmd'])}\n")
        log.flush()
        try:
            proc = subprocess.Popen(t["cmd"], cwd=t.get("cwd") or None, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        except OSError as e:
            status, rc = "crash", None
            log.write(f"# could not start: {e}\n")
            proc = None
        if proc is not None:
            try:
                rc = proc.wait(timeout=limit)
                status = "done" if rc == 0 else "failed"
            except subprocess.TimeoutExpired:
                status = "timeout"
                stop_group(proc)
                rc = proc.wait()
            except Interrupted as e:  # the worker itself got a signal: stop the task, record it, re-raise
                interrupted = e
                status = "interrupted"
                stop_group(proc)
                rc = proc.wait()
                log.write(f"# {now()} worker got signal {e.signum}, task stopped\n")
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # orphans left in the task's session
            except ProcessLookupError:
                pass
    res = {"id": t["id"], "status": status, "returncode": rc, "worker": me,
           "start": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start)), "end": now(),
           "runtime_s": round(time.time() - start, 1)}
    tmp = os.path.join(p["results"], f".{t['id']}.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(res, fh, indent=1)
    os.replace(tmp, os.path.join(p["results"], f"{t['id']}.json"))
    if interrupted is not None:
        raise interrupted
    return res


def cmd_run(a):
    p = paths(a.queue)
    os.makedirs(a.queue, exist_ok=True)
    me = worker_name()
    start = time.time()
    deadline = start + a.max_hours * 3600
    last_work = time.time()
    print(f"{now()} worker {me} started: idle-min {a.idle_min}, max-hours {a.max_hours}", flush=True)
    for sig in STOP_SIGNALS:
        signal.signal(sig, _on_signal)
    try:
        return _drain(a, p, me, deadline, last_work)
    except Interrupted as e:
        print(f"{now()} worker exits: signal {e.signum}", flush=True)
        return 128 + e.signum


def _drain(a, p, me, deadline, last_work):
    stop_reason = None
    while True:
        if os.path.exists(p["stop"]):
            stop_reason = "STOP file"
            break
        if time.time() > deadline - 60:
            stop_reason = "max-hours reached"
            break
        t = claim(a.queue, me)
        if t is None:
            idle = (time.time() - last_work) / 60
            if idle >= a.idle_min:
                stop_reason = f"idle for {idle:.1f} min"
                break
            time.sleep(min(a.poll_s, max(1.0, a.idle_min * 60 - idle * 60)))
            continue
        print(f"{now()} start {t['id']}", flush=True)
        try:
            r = run_task(a.queue, t, me, deadline)
        except Interrupted:  # signal before the task started: still leave a result, not a ghost claim
            rpath = os.path.join(p["results"], f"{t['id']}.json")
            if not os.path.exists(rpath):
                os.makedirs(p["results"], exist_ok=True)
                with open(rpath, "w") as fh:
                    json.dump({"id": t["id"], "status": "interrupted", "returncode": None, "worker": me, "runtime_s": 0.0,
                               "end": now()}, fh, indent=1)
            raise
        print(f"{now()} end   {t['id']} {r['status']} rc={r['returncode']} {r['runtime_s']}s", flush=True)
        last_work = time.time()
    print(f"{now()} worker exits: {stop_reason}", flush=True)
    return 0


def cmd_status(a):
    p = paths(a.queue)
    tasks = read_jsonl(p["queue"])
    claims = {c["id"]: c for c in read_jsonl(p["claims"])}
    for t in tasks:
        rpath = os.path.join(p["results"], f"{t['id']}.json")
        if os.path.exists(rpath):
            with open(rpath) as fh:
                r = json.load(fh)
            state = f"{r['status']} rc={r['returncode']} {r['runtime_s']}s"
        elif t["id"] in claims:
            state = f"running on {claims[t['id']]['worker']} since {claims[t['id']]['time']}"
        else:
            state = "queued"
        print(f"{t['id']}\t{state}")
    if os.path.exists(p["stop"]):
        print("STOP file present")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="sub", required=True)
    s = sub.add_parser("add")
    s.add_argument("--queue", required=True)
    s.add_argument("--id", required=True)
    s.add_argument("--timeout-min", type=float)
    s.add_argument("cmd", nargs=argparse.REMAINDER)
    s = sub.add_parser("run")
    s.add_argument("--queue", required=True)
    s.add_argument("--idle-min", type=float, default=25.0)
    s.add_argument("--max-hours", type=float, default=7.5,
                   help="stop claiming tasks and end a running task at this point. Keep it below the job walltime")
    s.add_argument("--poll-s", type=float, default=30.0)
    s = sub.add_parser("status")
    s.add_argument("--queue", required=True)
    a = p.parse_args(argv)
    if a.sub == "add" and a.cmd and a.cmd[0] == "--":
        a.cmd = a.cmd[1:]
    return {"add": cmd_add, "run": cmd_run, "status": cmd_status}[a.sub](a)


if __name__ == "__main__":
    sys.exit(main())
