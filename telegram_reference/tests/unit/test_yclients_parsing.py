from app.services.booking_reminders import _extract_dict


def test_extract_dict_from_data_payload():
    payload = {"data": {"id": 1, "client": {"name": "A", "birthday": "1990-01-01"}, "staff": {"name": "M"}, "services": [{"title": "S", "category": "C"}], "company": {"address": "Addr"}}}
    d = _extract_dict(payload)
    assert d["client"]["birthday"] == "1990-01-01"
    assert d["staff"]["name"] == "M"
    assert d["services"][0]["title"] == "S"
    assert d["company"]["address"] == "Addr"
