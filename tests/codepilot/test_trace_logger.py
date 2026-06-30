import json

from codepilot.trace.events import TraceEvent
from codepilot.trace.logger import TraceLogger, make_run_id


def test_make_run_id_uses_prefix() -> None:
    run_id = make_run_id(prefix="test")

    assert run_id.startswith("test-")
    assert len(run_id) == len("test-") + 12


def test_trace_logger_writes_jsonl(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")
    event = TraceEvent(run_id="run-test", step=logger.next_step, event_type="run_start")

    logger.record(event)

    assert logger.trace_path.exists()
    lines = logger.trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event_type"] == "run_start"


def test_trace_logger_creates_run_directory(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")

    assert logger.run_dir.exists()
    assert logger.run_dir.is_dir()
    assert logger.trace_path == tmp_path / "run-test" / "trace.jsonl"


def test_trace_logger_continues_existing_steps(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")
    logger.record(TraceEvent(run_id="run-test", step=logger.next_step, event_type="run_start"))

    reopened = TraceLogger(runs_dir=tmp_path, run_id="run-test")
    reopened.record(TraceEvent(run_id="run-test", step=reopened.next_step, event_type="run_end"))

    lines = reopened.trace_path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["step"] == 1
    assert json.loads(lines[1])["step"] == 2


def test_trace_logger_skips_bad_json_lines(tmp_path) -> None:
    trace_path = tmp_path / "run-test" / "trace.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"step": 2}\n{bad json}\n{"step": 5}\n', encoding="utf-8")

    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")

    assert logger.next_step == 6


def test_trace_logger_record_run_start_and_end(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")

    logger.record_run_start(task="demo task", metadata={"source": "test"})
    logger.record_run_end(success=True, summary="done", metadata={"checked": True})

    lines = logger.trace_path.read_text(encoding="utf-8").splitlines()
    start_event = json.loads(lines[0])
    end_event = json.loads(lines[1])

    assert start_event["event_type"] == "run_start"
    assert start_event["metadata"]["task"] == "demo task"
    assert start_event["metadata"]["source"] == "test"
    assert end_event["event_type"] == "run_end"
    assert end_event["success"] is True
    assert end_event["output_summary"] == "done"
    assert end_event["metadata"]["checked"] is True
