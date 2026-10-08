from vanta.resolve.broker_worker import handle


def test_broker_rejects_arbitrary_action_before_resolve_connection() -> None:
    response = handle({"action": "run_script", "params": {"code": "raise SystemExit"}})
    assert response["ok"] is False
    assert "broker allowlist" in response["error"]
