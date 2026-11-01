"""背景執行 SDD Run，並提供事件串流給 inbound adapter（SSE）。"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from java_coding_agent.application.ports import TracingPort
from java_coding_agent.application.sdd_pipeline import SddPipeline
from java_coding_agent.domain.models import FeatureRun, RunEvent
from java_coding_agent.domain.ports import IdGenerator, RunRepository

PipelineFactory = Callable[[Callable[[FeatureRun], None]], SddPipeline]


class _NoTracing:
    def callbacks(self, *, trace_name: str, metadata: dict[str, Any] | None = None) -> list:
        return []

    def flush(self) -> None:
        return None


class RunRegistry:
    def __init__(
        self,
        *,
        pipeline_factory: PipelineFactory,
        run_repository: RunRepository,
        id_generator: IdGenerator,
        tracing: TracingPort | None = None,
        max_workers: int = 1,
    ) -> None:
        self._pipeline_factory = pipeline_factory
        self._runs = run_repository
        self._new_id = id_generator
        self._tracing = tracing or _NoTracing()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sdd-run")
        self._subscribers: dict[str, list[queue.Queue[RunEvent | None]]] = {}
        self._lock = threading.Lock()

    def start(self, requirement: str) -> FeatureRun:
        if not requirement.strip():
            raise ValueError("業務需求不可為空")
        run = FeatureRun(id=self._new_id(), requirement=requirement.strip())
        self._runs.save(run)
        self._executor.submit(self._execute, run)
        return run

    def get(self, run_id: str) -> FeatureRun | None:
        return self._runs.get(run_id)

    def list_recent(self, limit: int = 20):
        return self._runs.list_recent(limit)

    def subscribe(self, run_id: str) -> Iterator[RunEvent]:
        """先重播既有事件，再持續等待新事件直到 Run 進入終態。"""
        q: queue.Queue[RunEvent | None] = queue.Queue()
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            seen = len(run.events)
            self._subscribers.setdefault(run_id, []).append(q)
        try:
            yield from run.events[:seen]
            if run.stage.is_terminal:
                return
            while True:
                item = q.get()
                if item is None:
                    return
                yield item
        finally:
            with self._lock:
                subs = self._subscribers.get(run_id, [])
                if q in subs:
                    subs.remove(q)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ internal
    def _execute(self, run: FeatureRun) -> None:
        pipeline = self._pipeline_factory(self._on_event)
        callbacks = self._tracing.callbacks(trace_name=f"sdd-run-{run.id}", metadata={"run_id": run.id})
        try:
            pipeline.run(run, callbacks=callbacks)
        finally:
            self._tracing.flush()
            self._on_event(run)
            self._close(run.id)

    def _on_event(self, run: FeatureRun) -> None:
        self._runs.save(run)
        with self._lock:
            subs = list(self._subscribers.get(run.id, []))
            # 只推送最新事件；訂閱者在 subscribe() 時已重播歷史
            event = run.events[-1] if run.events else None
        if event is None:
            return
        for q in subs:
            q.put(event)

    def _close(self, run_id: str) -> None:
        with self._lock:
            subs = list(self._subscribers.get(run_id, []))
        for q in subs:
            q.put(None)
