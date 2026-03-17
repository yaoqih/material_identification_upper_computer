from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.comm.protocol import FrameType, build_a1
from app.comm.serial_port import FakeSerialPort
from app.comm.session import SerialSession
from app.storage.config import ConfigRepo


def _make_session(monkeypatch) -> SerialSession:
    monkeypatch.setattr(
        "app.comm.session.ConfigRepo.load",
        lambda self: {
            "comm": {
                "enable_heartbeat": False,
                "retry": {"enabled": False},
            },
            "logging": {"hex": {"capture": False}},
        },
    )
    monkeypatch.setattr(SerialSession, "_start_tx_worker", lambda self: None)
    return SerialSession(
        port=FakeSerialPort(),
        request_handler=lambda: ([], None),
    )


def test_serial_session_init_does_not_expose_chunking_args() -> None:
    params = inspect.signature(SerialSession.__init__).parameters

    assert "bytes_per_frame" not in params
    assert "inter_frame_gap_ms" not in params


def test_config_repo_does_not_add_chunking_defaults() -> None:
    cfg = {"comm": {}}

    ConfigRepo()._apply_defaults(cfg)

    assert "bytes_per_frame" not in cfg["comm"]
    assert "inter_frame_gap_ms" not in cfg["comm"]


def test_send_a1_payload_uses_single_frame_for_multiple_indices(monkeypatch) -> None:
    session = _make_session(monkeypatch)
    sent_frames = []

    def _fake_send_and_wait_ack(frame, timeout_ms=None, single_try=False):
        sent_frames.append((frame, timeout_ms, single_try))
        return True

    monkeypatch.setattr(session, "send_and_wait_ack", _fake_send_and_wait_ack)

    session._send_a1_payload(indices=[1, 2, 3], attrs=[0, 1, 0], colors=[0, 1, 2])

    assert len(sent_frames) == 1
    frame, timeout_ms, single_try = sent_frames[0]
    assert frame.type == FrameType.A1
    assert frame == build_a1(indices=[1, 2, 3], seq=0, attrs=[0, 1, 0], colors=[0, 1, 2])
    assert timeout_ms == session.cmd_timeout_ms
    assert single_try is False


def test_send_a1_payload_sends_one_empty_frame_for_empty_indices(monkeypatch) -> None:
    session = _make_session(monkeypatch)
    sent_frames = []

    def _fake_send_and_wait_ack(frame, timeout_ms=None, single_try=False):
        sent_frames.append((frame, timeout_ms, single_try))
        return True

    monkeypatch.setattr(session, "send_and_wait_ack", _fake_send_and_wait_ack)

    session._send_a1_payload(indices=[], attrs=None, colors=None)

    assert len(sent_frames) == 1
    frame, timeout_ms, single_try = sent_frames[0]
    assert frame == build_a1(indices=[], seq=0, attrs=None, colors=None)
    assert timeout_ms == session.cmd_timeout_ms
    assert single_try is False
