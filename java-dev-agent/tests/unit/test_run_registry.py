import threading
import time

import pytest

from java_coding_agent.application.run_registry import RunRegistry
from java_coding_agent.domain.models import RunStage
from tests.unit.fakes import FakeBuildRunner, InMemoryRuns, sequential_ids


@pytest.fixture
def registry(make_pipeline):
    gate = threading.Event()

    class GatedBuilder(FakeBuildRunner):
        def run_tests(self, workspace, *, test_filter=None):
            gate.wait(timeout=5)
            return super().run_tests(workspace, test_filter=test_filter)

    def _build(on_event):
        p = make_pipeline(builder=GatedBuilder([True, False, True]))
        p._on_event = on_event
        return p

    reg = RunRegistry(pipeline_factory=_build, run_repository=InMemoryRuns(), id_generator=sequential_ids("run"))
    reg.gate = gate  # type: ignore[attr-defined]
    yield reg
    reg.shutdown()


def test_start_returns_pending_run_and_persists(registry):
    run = registry.start("需求")
    assert run.id == "run-1" and not run.stage.is_terminal  # 背景執行緒可能已開始
    assert registry.get("run-1") is not None
    registry.gate.set()


def test_subscribe_streams_events_until_terminal(registry):
    run = registry.start("需求")
    events = []

    def consume():
        for e in registry.subscribe(run.id):
            events.append(e)

    t = threading.Thread(target=consume)
    t.start()
    time.sleep(0.05)
    registry.gate.set()
    t.join(timeout=10)
    assert not t.is_alive(), "subscriber should finish when run is terminal"
    assert events[-1].stage is RunStage.SUCCEEDED
    assert events[0].stage is RunStage.SCAFFOLD


def test_subscribe_after_completion_replays_history(registry):
    registry.gate.set()
    run = registry.start("需求")
    deadline = time.time() + 10
    while not registry.get(run.id).stage.is_terminal and time.time() < deadline:
        time.sleep(0.02)
    events = list(registry.subscribe(run.id))
    assert events[-1].stage is RunStage.SUCCEEDED
    assert len(events) == len(registry.get(run.id).events)


def test_unknown_run_raises(registry):
    with pytest.raises(KeyError):
        list(registry.subscribe("missing"))


def test_empty_requirement_rejected(registry):
    with pytest.raises(ValueError):
        registry.start("   ")
