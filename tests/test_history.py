import json


def test_history_without_existing_file(tmp_path, monkeypatch):
    from backend.config import settings
    from backend.services.history_service import HistoryService

    history_file = tmp_path / "history.json"
    monkeypatch.setattr(settings, "history_file", history_file)
    service = HistoryService()
    assert not history_file.exists()
    assert service.get_history() == []
    assert not history_file.exists()

    item = service.add_entry("text", "Offline request", "Offline response")

    saved = json.loads(history_file.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["id"] == item.id
    assert saved[0]["request_summary"] == "Offline request"
    assert saved[0]["response_summary"] == "Offline response"
    assert service.get_history() == [item]
