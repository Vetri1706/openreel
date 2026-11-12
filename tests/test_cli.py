from types import SimpleNamespace

import pytest

from openreel import cli


@pytest.mark.parametrize("command", ["studio", "mcp", "doctor", "demo", "worker"])
def test_cli_closes_service_when_command_fails(monkeypatch, command):
    closed = []

    def fail(*args, **kwargs):
        raise RuntimeError("command failed")

    service = SimpleNamespace(
        capabilities=fail,
        demo=fail,
        renderer=SimpleNamespace(run_worker=fail),
        close=lambda: closed.append(True),
    )
    monkeypatch.setenv("OPENREEL_RENDER_WORKER", "1")
    monkeypatch.setattr(cli, "Service", lambda *args, **kwargs: service)
    monkeypatch.setattr("sys.argv", ["openreel", command])
    monkeypatch.delenv("OPENREEL_RENDER_SERVER", raising=False)
    monkeypatch.setattr("openreel.mcp_server.build_mcp", lambda service: SimpleNamespace(run=fail))
    monkeypatch.setattr("openreel.web.create_app", lambda *args: object())
    monkeypatch.setattr("uvicorn.run", fail)
    with pytest.raises(RuntimeError, match="command failed"):
        cli.main()
    assert closed == [True]
    import os

    assert os.environ["OPENREEL_RENDER_WORKER"] == "1"
