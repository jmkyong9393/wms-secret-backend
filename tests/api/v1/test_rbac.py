# RBAC 인가 테스트 — admin 전용 API가 역할별로 올바르게 차단되는지.
# 대상: /api/v1/admin/hitl/pending (RoleChecker[MASTER, ADMIN])
ADMIN_EP = "/api/v1/admin/hitl/pending"


def test_admin_endpoint_without_token_returns_401(client):
    r = client.get(ADMIN_EP)
    assert r.status_code == 401


def test_admin_endpoint_as_worker_returns_403(client, worker_headers):
    r = client.get(ADMIN_EP, headers=worker_headers)
    assert r.status_code == 403


def test_admin_endpoint_as_master_returns_200(client, master_headers):
    r = client.get(ADMIN_EP, headers=master_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_as_worker_returns_403(client, worker_headers):
    # dashboard 라우터는 라우터 수준 dependencies로 MASTER/ADMIN을 강제한다.
    r = client.get("/api/v1/dashboard/kpi", headers=worker_headers)
    assert r.status_code == 403
