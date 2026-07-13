import json

from codepilot.trace.events import TraceEvent
from codepilot.trace.logger import MAX_TRACE_PREVIEW_CHARS, TraceLogger, _preview_text, make_run_id


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


def test_trace_logger_notifies_record_hook_with_written_event(tmp_path) -> None:
    received: list[TraceEvent] = []
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test", record_hook=received.append)
    event = TraceEvent(run_id="run-test", step=logger.next_step, event_type="run_start")

    assert logger.record(event) is event
    assert received == [event]
    assert logger.last_record_hook_error is None


def test_trace_logger_exposes_hook_failure_after_trace_is_written(tmp_path) -> None:
    errors: list[tuple[Exception, TraceEvent]] = []

    def broken_hook(_event: TraceEvent) -> None:
        raise RuntimeError("event bridge failed")

    logger = TraceLogger(
        runs_dir=tmp_path,
        run_id="run-test",
        record_hook=broken_hook,
        record_hook_error=lambda error, event: errors.append((error, event)),
    )
    event = TraceEvent(run_id="run-test", step=logger.next_step, event_type="run_start")

    assert logger.record(event) is event
    assert json.loads(logger.trace_path.read_text(encoding="utf-8"))["event_type"] == "run_start"
    assert logger.last_record_hook_error is not None
    assert logger.last_record_hook_error[0] is errors[0][0]
    assert logger.last_record_hook_error[1] is event
    assert errors[0][1] is event


def test_trace_logger_error_callback_failure_does_not_escape(tmp_path) -> None:
    def broken_hook(_event: TraceEvent) -> None:
        raise RuntimeError("event bridge failed")

    def broken_error_hook(_error: Exception, _event: TraceEvent) -> None:
        raise RuntimeError("diagnostic bridge failed")

    logger = TraceLogger(
        runs_dir=tmp_path,
        run_id="run-test",
        record_hook=broken_hook,
        record_hook_error=broken_error_hook,
    )
    event = TraceEvent(run_id="run-test", step=logger.next_step, event_type="run_start")

    assert logger.record(event) is event
    assert json.loads(logger.trace_path.read_text(encoding="utf-8"))["event_type"] == "run_start"
    assert logger.last_record_hook_error is not None
    assert str(logger.last_record_hook_error[0]) == "event bridge failed"
    assert logger.last_record_hook_error_callback_error is not None
    assert str(logger.last_record_hook_error_callback_error[0]) == "diagnostic bridge failed"
    assert logger.last_record_hook_error_callback_error[1] is event


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

    assert logger.terminal_recorded is False
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
    assert logger.terminal_recorded is True


def test_trace_logger_restores_terminal_state_from_existing_trace(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")
    logger.record_run_cancelled(metadata={"source": "test"})

    reopened = TraceLogger(runs_dir=tmp_path, run_id="run-test")

    assert reopened.terminal_recorded is True


def test_trace_logger_record_policy_decision(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")

    logger.record_policy_decision(
        tool_name="run_shell",
        decision="deny",
        reason="blocked",
        rule="command.deny_substrings.rm -rf",
        mode="build",
        metadata={"checked": True},
    )
    logger.record_policy_decision(
        tool_name="read_file",
        decision="allow",
        reason="ok",
        mode="read_only",
    )

    lines = logger.trace_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    second = json.loads(lines[1])

    assert first["step"] == 1
    assert first["event_type"] == "policy_decision"
    assert first["policy_decision"] == "deny"
    assert first["metadata"]["checked"] is True
    assert second["step"] == 2
    assert second["event_type"] == "policy_decision"
    assert second["policy_decision"] == "allow"


def test_preview_text_truncates_long_text() -> None:
    preview, truncated = _preview_text("x" * (MAX_TRACE_PREVIEW_CHARS + 10))

    assert truncated is True
    assert len(preview) == MAX_TRACE_PREVIEW_CHARS
    assert preview.endswith("... truncated")


def test_trace_logger_record_llm_call(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")

    logger.record_llm_call(
        model="fake-model",
        message_count=3,
        response_text="hello",
        usage={"total_tokens": 10},
        metadata={"source": "test"},
    )

    event = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["event_type"] == "llm_call"
    assert event["success"] is True
    assert event["output_preview"] == "hello"
    assert event["metadata"]["model"] == "fake-model"
    assert event["metadata"]["message_count"] == 3
    assert event["metadata"]["response_chars"] == 5
    assert event["metadata"]["response_preview_truncated"] is False
    assert event["metadata"]["usage"] == {"total_tokens": 10}
    assert event["metadata"]["source"] == "test"


def test_trace_logger_record_agent_action_and_parse_failure(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")

    logger.record_agent_action(
        action_type="tool_call",
        tool_name="read_file",
        input={"tool_name": "read_file"},
        success=False,
        error="bad json",
        metadata={"parse_success": False},
    )

    event = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["event_type"] == "agent_action"
    assert event["tool_name"] == "read_file"
    assert event["success"] is False
    assert event["error"] == "bad json"
    assert event["metadata"]["action_type"] == "tool_call"
    assert event["metadata"]["parse_success"] is False


def test_trace_logger_record_agent_observation_truncates_output(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")
    observation = "first line\n" + ("x" * (MAX_TRACE_PREVIEW_CHARS + 50))

    logger.record_agent_observation(tool_name="run_tests", observation=observation, metadata={"source": "test"})

    event = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["event_type"] == "agent_observation"
    assert event["tool_name"] == "run_tests"
    assert event["output_summary"] == "first line"
    assert event["metadata"]["observation_chars"] == len(observation)
    assert event["metadata"]["observation_preview_truncated"] is True
    assert event["metadata"]["source"] == "test"


def test_trace_logger_record_agent_finish(tmp_path) -> None:
    logger = TraceLogger(runs_dir=tmp_path, run_id="run-test")

    logger.record_agent_finish(status="partial", summary="done", metadata={"steps": 2})

    event = json.loads(logger.trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["event_type"] == "agent_finish"
    assert event["success"] is False
    assert event["output_summary"] == "done"
    assert event["metadata"]["status"] == "partial"
    assert event["metadata"]["steps"] == 2
