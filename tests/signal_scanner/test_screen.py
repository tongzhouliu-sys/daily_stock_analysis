from src.services.signal_scanner.core import digest


def test_screen_is_read_only_and_unknown_is_not_zero(client):
    with client.app.state.db.read() as cx:
        before = digest(list(cx.iterdump()))
    result = client.get("/api/screen")
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["environment"]["mode"] == "UNKNOWN" and data["risk"]["account_status"] == "UNKNOWN"
    assert data["spy"]["prices"] == [] and len(data["sources"]) == 3
    assert len(data["rules"]) == 12 and data["order_execution"] == "FORBIDDEN"
    with client.app.state.db.read() as cx:
        assert digest(list(cx.iterdump())) == before
