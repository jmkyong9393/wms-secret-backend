# 에러 응답 계약 테스트 — 404가 JSON으로 내려오고 프론트가 읽는 키가 존재하는지.
import uuid


def test_inventory_detail_unknown_id_returns_404(client, master_headers):
    r = client.get(f"/api/v1/inventory/{uuid.uuid4()}", headers=master_headers)
    assert r.status_code == 404
    body = r.json()
    # 전역 핸들러 규격({status,code,message,...}) 또는 FastAPI 기본({detail}) 중 하나여야 한다.
    assert ("message" in body) or ("detail" in body)


def test_unknown_route_returns_404(client):
    r = client.get("/api/v1/definitely-not-a-real-route")
    assert r.status_code == 404
