def test_create_production_log(client):
    payload = {
        "equipment_id": "EQ-001",
        "qty_good": 10,
        "qty_defect": 2,
        "cycle_time_sec": 5.0,
        "planned_time_sec": 60.0,
    }
    res = client.post("/production-logs", json=payload)
    assert res.status_code == 201

    body = res.json()
    assert body["equipment_id"] == "EQ-001"
    assert body["qty_good"] == 10
    assert body["qty_defect"] == 2
    assert "log_id" in body


def test_create_production_log_unknown_equipment(client):
    res = client.post(
        "/production-logs",
        json={"equipment_id": "EQ-NOPE", "qty_good": 1, "qty_defect": 0},
    )
    assert res.status_code == 404


def test_oee_endpoint_range(client):
    for _ in range(3):
        client.post(
            "/production-logs",
            json={
                "equipment_id": "EQ-001",
                "qty_good": 8,
                "qty_defect": 2,
                "cycle_time_sec": 5.0,
                "planned_time_sec": 60.0,
            },
        )

    res = client.get("/equipment/EQ-001/oee?hours=24")
    assert res.status_code == 200

    body = res.json()
    assert 0.0 <= body["availability"] <= 1.0
    assert 0.0 <= body["quality_rate"] <= 1.0
    assert 0.0 <= body["oee"] <= 1.0
