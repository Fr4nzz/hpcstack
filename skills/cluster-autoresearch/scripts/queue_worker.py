#!/usr/bin/env python3
"""queue_worker.py: a file-based task queue drained by batch worker jobs.

The agent adds tasks. One or more batch jobs run `queue_worker.py run`, claim tasks under a file lock,
run them, and exit when the queue has been empty for --idle-min minutes, when --max-hours is reached,
or when a STOP file appears. If the agent dies, the workers finish the queue and exit on their own.

  queue_worker.py add     --queue Q --id ID [--timeout-min N] [--priority high|normal]
                          [--resume-cmd "CMD ARGS"] [--est-min N] -- CMD [ARGS...]
  queue_worker.py run     --queue Q [--idle-min 25] [--max-hours 7.5] [--poll-s 30]
                          [--preempt-signal TERM] [--preempt-grace 120] [--preempt-poll-s 10]
  queue_worker.py preempt --queue Q --task ID --reason "why the new work is worth more"
  queue_worker.py status  --queue Q
  touch Q/STOP           workers finish their current task and exit

Layout of Q: queue.jsonl (tasks), claims.jsonl (who took what), results/<id>.json, logs/<id>.log,
preempt/<id>.json (open preempt requests), preempt.log (one line per preemption), .lock. A task's command
runs with cwd = the directory `add` was called from, in its own session, so the whole process group is
killed on timeout. Tasks are claimed once. Re-queue a crashed task with a new id.

Priority: `claim` takes high before normal, first in first out within each priority. Old queue lines
without a priority count as normal.

Repurposable slot (UNTESTED on a cluster, tested on synthetic tasks only): `preempt` writes a request
file and signals nothing. The worker that runs the task checks for it every --preempt-poll-s seconds,
sends the task's process group --preempt-signal so it can checkpoint and exit, waits --preempt-grace
seconds for every process in the group (not only the leader), then KILLs the group. The grace is capped
by the task's and the worker's remaining time. With no time left the run counts as a timeout. The task's
result is "preempted", one line goes to preempt.log, and the task goes back into the queue at its own
priority, ahead of the other tasks of that priority, running its --resume-cmd
(or the original command, which restarts from scratch). The worker then claims the next task, normally
the high-priority one that motivated the preemption. A task is preempted at most twice. Use this only on
your own queue and your own jobs. The idle exit stays on, so no allocation is held empty.
--resume-cmd is split like a shell line but not run by a shell. Wrap it in `bash -c '...'` for pipes.

Exit codes of `run`: 0 when the worker stopped normally (idle, max-hours, STOP), whatever the tasks'
own results (read results/<id>.json). 128+N when signal N stopped it (bkill, scancel, the walltime warning
sent by `bsub -wa USR2` or `sbatch --signal`): the running task's process group is stopped first and its
result is written as "interrupted", so `done(<job>)` dependents do not fire on a half-drained queue.
A signal to the worker always means "stop everything". It is never a preemption.
Exit codes of `preempt`: 0 request written, 1 refused (task not running, unknown, or preempted twice),
2 no or empty --reason.
"""
import argparse
import fcntl
import getpass
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

MAX_PREEMPTS = 2
PRIORITY_RANK = {"high": 0, "normal": 1}


def paths(q):
    return {k: os.path.join(q, v) for k, v in
            {"queue": "queue.jsonl", "claims": "claims.jsonl", "lock": ".lock", "stop": "STOP",
             "results": "results", "logs": "logs", "preempt": "preempt",
             "preempt_log": "preempt.log"}.items()}


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


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.tmp")
    with open(tmp, "w") as fh:
        json.dump(obj, fh, indent=1)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


class Interrupted(Exception):
    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum


STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR2)


def _on_signal(signum, frame):
    for sig in STOP_SIGNALS:  # the scheduler often follows up (INT, TERM, KILL): finish the cleanup once
        signal.signal(sig, signal.SIG_IGN)
    raise Interrupted(signum)


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_group(proc, sig=signal.SIGTERM, grace=30.0):
    """Send `sig` to the task's process group, wait up to `grace` s for the whole group to exit (a launcher
    that exits at once must not cut its children's checkpoint short), then KILL the group.
    Returns True when something was still running at the end of the grace period."""
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return False
    end = time.monotonic() + grace
    killed = False
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        killed = True
    while not killed and group_alive(proc.pid):
        if time.monotonic() >= end:
            killed = True
            break
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # the task's own children, or the task if it ignored the signal
    except ProcessLookupError:
        pass
    return killed


def parse_signal(name):
    s = str(name).upper()
    if s.isdigit():
        return signal.Signals(int(s))
    if not s.startswith("SIG"):
        s = "SIG" + s
    try:
        return signal.Signals[s]
    except KeyError:
        raise argparse.ArgumentTypeError(f"unknown signal {name!r}")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def job_id():
    return os.environ.get("LSB_JOBID") or os.environ.get("SLURM_JOB_ID") or "local"


def worker_name():
    idx = os.environ.get("LSB_JOBINDEX") or os.environ.get("SLURM_ARRAY_TASK_ID")
    return f"{socket.gethostname()}:{job_id()}" + (f"[{idx}]" if idx and idx != "0" else "") + f":{os.getpid()}"


def attempt_of(entry):
    return int(entry.get("attempt", 0))


def queue_state(q):
    """Latest queue entry per task id (in first-seen order), and the set of claimed (id, attempt)."""
    p = paths(q)
    latest = {}
    for t in read_jsonl(p["queue"]):
        if t["id"] not in latest or attempt_of(t) >= attempt_of(latest[t["id"]]):
            latest[t["id"]] = t
    claims = {}
    for c in read_jsonl(p["claims"]):
        claims[(c["id"], attempt_of(c))] = c
    return latest, claims


def task_state(q, t, claims):
    """'queued', 'running' or 'finished' for the latest attempt of task t, plus its claim and result."""
    c = claims.get((t["id"], attempt_of(t)))
    if c is None:
        return "queued", None, None
    r = read_json(os.path.join(paths(q)["results"], f"{t['id']}.json"))
    if r is not None and attempt_of(r) == attempt_of(t):
        return "finished", c, r
    return "running", c, None


def cmd_add(a):
    p = paths(a.queue)
    os.makedirs(a.queue, exist_ok=True)
    if not a.cmd:
        sys.exit("add needs a command after --")
    entry = {"id": a.id, "cmd": a.cmd, "cwd": os.getcwd(), "timeout_min": a.timeout_min, "added": now(),
             "priority": a.priority}
    if a.resume_cmd:
        entry["resume_cmd"] = shlex.split(a.resume_cmd)
    if a.est_min is not None:
        entry["est_min"] = a.est_min
    with locked(a.queue):
        if any(t["id"] == a.id for t in read_jsonl(p["queue"])):
            sys.exit(f"task id {a.id} already queued. Use a new id")
        append_jsonl(p["queue"], entry)
    print(f"queued {a.id} ({a.priority}): {shlex.join(a.cmd)}")


def claim(q, me, deadline=None, grace=None):
    p = paths(q)
    with locked(q):
        latest, claims = queue_state(q)
        pending = [(PRIORITY_RANK.get(t.get("priority", "normal"), 1), 0 if t.get("front") else 1, i, t)
                   for i, t in enumerate(latest.values()) if (t["id"], attempt_of(t)) not in claims]
        if not pending:
            return None
        t = min(pending, key=lambda x: x[:3])[3]
        c = {"id": t["id"], "worker": me, "time": now(), "attempt": attempt_of(t)}
        if deadline is not None:
            c.update(deadline=round(deadline, 1), deadline_iso=iso(deadline), preempt_grace_s=grace)
        append_jsonl(p["claims"], c)
        return t


def requeue_entries(t, me, reason, runtime_s):
    """The queue line that puts a preempted task back at the front of its own priority, and its log line."""
    n = int(t.get("preempts", 0)) + 1
    resume = t.get("resume_cmd")
    new = dict(t, attempt=attempt_of(t) + 1, preempts=n, front=True, requeued=now(),
               cmd=resume or t["cmd"], prior_runtime_s=round(float(t.get("prior_runtime_s", 0)) + runtime_s, 1))
    logline = {"time": now(), "task": t["id"], "attempt": attempt_of(t), "worker": me, "job": job_id(),
               "host": socket.gethostname(), "reason": reason, "runtime_s": round(runtime_s, 1),
               "total_runtime_s": new["prior_runtime_s"], "preempt_count": n, "resume_cmd": bool(resume)}
    return new, logline


def publish_result(q, t, res, requeue=None):
    """Under the queue lock (the one `preempt` takes): write the attempt's result first, then re-queue it
    if it was preempted, then remove its preempt request, so no request outlives the attempt and no
    stale result overwrites the next attempt's."""
    p = paths(q)
    rpath = os.path.join(p["preempt"], f"{t['id']}.json")
    with locked(q):
        write_json(os.path.join(p["results"], f"{t['id']}.json"), res)
        if requeue is not None:
            append_jsonl(p["queue"], requeue[0])
            append_jsonl(p["preempt_log"], requeue[1])
        late = os.path.exists(rpath)
        if late:
            os.remove(rpath)
    if late and res["status"] != "preempted":
        print(f"{now()} preempt request for {t['id']} arrived after it ended, removed", flush=True)


def preempt_request(q, t):
    """The open preempt request for this attempt of task t, or None. Stale requests are removed."""
    path = os.path.join(paths(q)["preempt"], f"{t['id']}.json")
    if not os.path.exists(path):
        return None
    req = read_json(path) or {}
    if attempt_of(req) != attempt_of(t) or int(t.get("preempts", 0)) >= MAX_PREEMPTS:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return None
    return req


def run_task(q, t, me, deadline, opts=None):
    p = paths(q)
    opts = opts or {}
    psig = opts.get("preempt_signal", signal.SIGTERM)
    pgrace = opts.get("preempt_grace", 120.0)
    ppoll = max(0.05, opts.get("preempt_poll_s", 10.0))
    os.makedirs(p["results"], exist_ok=True)
    os.makedirs(p["logs"], exist_ok=True)
    timeout = t.get("timeout_min")
    limit = max(1.0, deadline - time.time())
    if timeout:
        limit = min(limit, timeout * 60)
    start = time.time()
    end_by = start + limit
    status, rc, interrupted = "done", None, None
    extra, requeue = {}, None
    attempt = attempt_of(t)
    with open(os.path.join(p["logs"], f"{t['id']}.log"), "a" if attempt else "w") as log:
        log.write(f"# {now()} {me}" + (f" attempt {attempt} (after {t.get('preempts', 0)} preemption(s))"
                                         if attempt else "") + f"\n# {shlex.join(t['cmd'])}\n")
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
                while True:
                    left = end_by - time.time()
                    if left <= 0:
                        raise subprocess.TimeoutExpired(t["cmd"], limit)
                    try:
                        rc = proc.wait(timeout=min(left, ppoll))
                        status = "done" if rc == 0 else "failed"
                        break
                    except subprocess.TimeoutExpired:
                        req = preempt_request(q, t)
                        if req is None:
                            continue
                    reason = req.get("reason", "")
                    budget = min(end_by, deadline) - time.time()
                    log.write(f"# {now()} preempt requested by {req.get('user')}@{req.get('host')}: {reason}\n")
                    if budget <= 0:  # no time left to checkpoint in: this run is a timeout
                        log.write(f"# {now()} no time left for a grace period, recorded as timeout\n")
                        raise subprocess.TimeoutExpired(t["cmd"], limit)
                    grace = max(1.0, min(pgrace, budget))
                    log.write(f"# sending {psig.name}, grace {grace:g} s\n")
                    log.flush()
                    killed = stop_group(proc, psig, grace)
                    rc = proc.wait()
                    status = "preempted"
                    log.write(f"# {now()} task {'killed after the grace period' if killed else 'exited'}"
                              f" rc={rc}\n")
                    requeue = requeue_entries(t, me, reason, time.time() - start)
                    if not t.get("resume_cmd"):
                        msg = f"WARNING no resume command: {t['id']} restarts from scratch"
                        log.write(f"# {now()} {msg}\n")
                        print(f"{now()} {msg}", flush=True)
                    extra = {"reason": reason, "preempt_exit": "killed" if killed else "exited",
                             "preempt_count": requeue[1]["preempt_count"], "requested": req.get("time"),
                             "grace_s": round(grace, 1)}
                    break
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
    res = {"id": t["id"], "status": status, "returncode": rc, "worker": me, "start": iso(start), "end": now(),
           "runtime_s": round(time.time() - start, 1), "attempt": attempt,
           "preempts": int(t.get("preempts", 0)) + (1 if status == "preempted" else 0)}
    if attempt:
        res["prior_runtime_s"] = t.get("prior_runtime_s", 0)
    res.update(extra)
    publish_result(q, t, res, requeue)
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
    print(f"{now()} worker {me} started: idle-min {a.idle_min}, max-hours {a.max_hours}, "
          f"preempt {a.preempt_signal.name} grace {a.preempt_grace:g} s", flush=True)
    for sig in STOP_SIGNALS:
        signal.signal(sig, _on_signal)
    try:
        return _drain(a, p, me, deadline, last_work)
    except Interrupted as e:
        print(f"{now()} worker exits: signal {e.signum}", flush=True)
        return 128 + e.signum


def _drain(a, p, me, deadline, last_work):
    stop_reason = None
    opts = {"preempt_signal": a.preempt_signal, "preempt_grace": a.preempt_grace,
            "preempt_poll_s": a.preempt_poll_s}
    while True:
        if os.path.exists(p["stop"]):
            stop_reason = "STOP file"
            break
        if time.time() > deadline - 60:
            stop_reason = "max-hours reached"
            break
        t = claim(a.queue, me, deadline, a.preempt_grace)
        if t is None:
            idle = (time.time() - last_work) / 60
            if idle >= a.idle_min:
                stop_reason = f"idle for {idle:.1f} min"
                break
            time.sleep(min(a.poll_s, max(1.0, a.idle_min * 60 - idle * 60)))
            continue
        print(f"{now()} start {t['id']}" + (f" attempt {attempt_of(t)}" if attempt_of(t) else ""), flush=True)
        try:
            r = run_task(a.queue, t, me, deadline, opts)
        except Interrupted:  # signal before the task started: still leave a result, not a ghost claim
            rpath = os.path.join(p["results"], f"{t['id']}.json")
            old = read_json(rpath)
            if old is None or attempt_of(old) != attempt_of(t):
                write_json(rpath, {"id": t["id"], "status": "interrupted", "returncode": None, "worker": me,
                                   "runtime_s": 0.0, "end": now(), "attempt": attempt_of(t)})
            raise
        print(f"{now()} end   {t['id']} {r['status']} rc={r['returncode']} {r['runtime_s']}s", flush=True)
        last_work = time.time()
    print(f"{now()} worker exits: {stop_reason}", flush=True)
    return 0


def user():
    try:
        return getpass.getuser()
    except (OSError, KeyError):
        return "unknown"


def fmt_min(s):
    return f"{s / 60:.0f} min"


def cmd_preempt(a):
    p = paths(a.queue)
    reason = (a.reason or "").strip()
    if not reason:
        print("preempt needs a non-empty --reason: say why the new work is worth more", file=sys.stderr)
        return 2
    if not os.path.isdir(a.queue):
        print(f"no queue at {a.queue}", file=sys.stderr)
        return 1
    with locked(a.queue):
        latest, claims = queue_state(a.queue)
        t = latest.get(a.task)
        if t is None:
            print(f"refused: no task {a.task} in {a.queue}. Nothing was signalled")
            return 1
        state, c, r = task_state(a.queue, t, claims)
        if state != "running":
            what = "is still queued" if state == "queued" else f"has already ended ({r['status']})"
            print(f"refused: {a.task} {what}, not running. Nothing was signalled")
            return 1
        n = int(t.get("preempts", 0))
        if n >= MAX_PREEMPTS:
            print(f"refused: {a.task} was already preempted {n} times (limit {MAX_PREEMPTS}), so it would starve. "
                  "Let it finish or give the new work its own job")
            return 1
        rpath = os.path.join(p["preempt"], f"{a.task}.json")
        old = read_json(rpath) if os.path.exists(rpath) else None
        if old is not None and attempt_of(old) == attempt_of(t):
            print(f"a preempt request for {a.task} is already open: {rpath}")
            return 0
        write_json(rpath, {"task": a.task, "attempt": attempt_of(t), "reason": reason, "time": now(),
                           "user": user(), "host": socket.gethostname(), "worker": c.get("worker")})
        high = [x for x in latest.values()
                if x.get("priority") == "high" and (x["id"], attempt_of(x)) not in claims]
    print(f"preempt requested for {a.task} on {c.get('worker')} (preemption {n + 1} of {MAX_PREEMPTS}): {reason}")
    if not t.get("resume_cmd"):
        print(f"warning: {a.task} has no --resume-cmd, so it restarts from scratch")
    left = None
    if c.get("deadline"):
        left = float(c["deadline"]) - time.time() - float(c.get("preempt_grace_s") or 0)
        print(f"worker time left after the grace period: about {fmt_min(max(0.0, left))} "
              f"(its deadline {c.get('deadline_iso')})")
    else:
        print("worker time left: unknown (its claim records no deadline)")
    if not high:
        print("warning: no high-priority task is queued, so the worker will resume this same task next")
    for x in high:
        if x.get("est_min") is None:
            print(f"note: high-priority {x['id']} has no --est-min, fit not checked")
        elif left is not None and x["est_min"] * 60 > left:
            print(f"warning: high-priority {x['id']} needs about {x['est_min']:g} min, "
                  f"more than the {fmt_min(max(0.0, left))} left")
    print("the worker acts on it within its --preempt-poll-s (default 10 s)")
    return 0


def cmd_status(a):
    p = paths(a.queue)
    latest, claims = queue_state(a.queue)
    for t in latest.values():
        state, c, r = task_state(a.queue, t, claims)
        if state == "finished":
            s = f"{r['status']} rc={r['returncode']} {r['runtime_s']}s"
        elif state == "running":
            s = f"running on {c['worker']} since {c['time']}"
            if c.get("deadline"):
                s += f", worker deadline in {fmt_min(max(0.0, float(c['deadline']) - time.time()))}"
        else:
            s = "queued" + (" (resume)" if t.get("front") and t.get("resume_cmd") else
                            " (restart)" if t.get("front") else "")
        print(f"{t['id']}\t{t.get('priority', 'normal')}\tpreempts={t.get('preempts', 0)}\t{s}")
    if os.path.isdir(p["preempt"]):
        for f in sorted(os.listdir(p["preempt"])):
            if f.endswith(".json") and not f.startswith("."):
                req = read_json(os.path.join(p["preempt"], f)) or {}
                print(f"open preempt request: {f[:-5]} since {req.get('time')}: {req.get('reason')}")
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
    s.add_argument("--priority", choices=sorted(PRIORITY_RANK), default="normal",
                   help="high tasks are claimed before normal ones")
    s.add_argument("--resume-cmd", help="command to run instead of CMD after a preemption, e.g. "
                   "\"python train.py resume=last.pt\"")
    s.add_argument("--est-min", type=float, help="expected runtime in minutes, used by preempt's fit check")
    s.add_argument("cmd", nargs=argparse.REMAINDER)
    s = sub.add_parser("run")
    s.add_argument("--queue", required=True)
    s.add_argument("--idle-min", type=float, default=25.0)
    s.add_argument("--max-hours", type=float, default=7.5,
                   help="stop claiming tasks and end a running task at this point. Keep it below the job walltime")
    s.add_argument("--poll-s", type=float, default=30.0)
    s.add_argument("--preempt-signal", type=parse_signal, default=signal.SIGTERM,
                   help="signal sent to a preempted task's process group (default TERM)")
    s.add_argument("--preempt-grace", type=float, default=120.0,
                   help="seconds a preempted task gets to checkpoint and exit before KILL (default 120)")
    s.add_argument("--preempt-poll-s", type=float, default=10.0,
                   help="how often a running task's preempt request is checked (default 10)")
    s = sub.add_parser("preempt", help="ask the worker running TASK to stop it and run higher-priority work")
    s.add_argument("--queue", required=True)
    s.add_argument("--task", required=True)
    s.add_argument("--reason", required=True, help="why the new work is worth more (recorded)")
    s = sub.add_parser("status")
    s.add_argument("--queue", required=True)
    a = p.parse_args(argv)
    if a.sub == "add" and a.cmd and a.cmd[0] == "--":
        a.cmd = a.cmd[1:]
    return {"add": cmd_add, "run": cmd_run, "preempt": cmd_preempt, "status": cmd_status}[a.sub](a)


if __name__ == "__main__":
    sys.exit(main())
