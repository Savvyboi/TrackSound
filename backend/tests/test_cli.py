"""The launcher: free-port selection and spotting an already-running TrackSound."""
import socket

from tracksound import cli


def test_pick_port_skips_busy_ports():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        busy = s.getsockname()[1]
        port, running = cli._pick_port("127.0.0.1", busy)
        assert port != busy and not running
    assert cli._pick_port("127.0.0.1", busy) == (busy, False)


def test_version_flag(capsys):
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "TrackSound" in capsys.readouterr().out
