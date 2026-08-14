# 핵심 조회 API 통합 테스트 — 응답 코드와 최상위 스키마 형태를 고정한다.
# 전부 읽기 전용이므로 시연 시드에 영향이 없다.


def test_inventory_list_returns_array(client, master_headers):
    r = client.get("/api/v1/inventory", headers=master_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    if body:  # 시드가 있으면 행 스키마의 핵심 키를 고정
        assert "id" in body[0]


def test_orders_list_returns_array(client, master_headers):
    r = client.get("/api/v1/orders/", headers=master_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_notifications_list(client, master_headers):
    r = client.get("/api/v1/notifications", headers=master_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), (list, dict))


def test_dashboard_kpi_as_master(client, master_headers):
    r = client.get("/api/v1/dashboard/kpi", headers=master_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
