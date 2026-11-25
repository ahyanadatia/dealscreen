"""In-process job runner with bounded concurrency and a replayable event log per job."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from dealscreen.models import ScreeningResult, TraceEvent


@dataclass
class Job:
    id: str
    label: str
    status: str = "queued"  # queued | running | done | error
    events: list[TraceEvent] = field(default_factory=list)
    result_id: str | None = None
    error: str | None = None
    cond: threading.Condition = field(default_factory=threading.Condition)

    def push(self, ev: TraceEvent) -> None:
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    def finish(self, status: str, result_id: str | None = None, error: str | None = None) -> None:
        with self.cond:
            self.status, self.result_id, self.error = status, result_id, error
            self.cond.notify_all()

    def wait_for(self, seen: int, timeout: float) -> None:
        with self.cond:
            if len(self.events) <= seen and self.status in {"queued", "running"}:
                self.cond.wait(timeout)


class JobManager:
    def __init__(self, max_workers: int = 2, max_queued: int = 25):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="screen")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.max_queued = max_queued

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status in {"queued", "running"})

    def submit(
        self, label: str, fn: Callable[[Callable[[TraceEvent], None]], ScreeningResult]
    ) -> Job:
        if self.active_count() >= self.max_queued:
            raise OverflowError("too many screenings in progress; try again shortly")
        job = Job(id=uuid.uuid4().hex[:12], label=label)
        with self._lock:
            self._jobs[job.id] = job

        def run() -> None:
            job.status = "running"
            try:
                result = fn(job.push)
            except Exception as exc:
                job.finish("error", error=str(exc) or exc.__class__.__name__)
            else:
                job.finish("done", result_id=result.id)

        self._pool.submit(run)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
