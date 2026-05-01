from fastapi.testclient import TestClient
from app.main import app


def test_project_secretary_smoke() -> None:
    client = TestClient(app)
    response = client.post("/agents/project-secretary/run", headers={"X-API-Key": "local-dev-key"}, json={"project_id": "demo-rag", "trigger_type": "手动"})
    assert response.status_code == 200
    assert response.json()["agent_name"] == "ProjectSecretaryAgent"


def test_demo_full_chain_smoke() -> None:
    client = TestClient(app)
    response = client.post("/demo/run-full-chain", headers={"X-API-Key": "local-dev-key"}, json={"project_id": "demo-rag", "trigger_type": "手动"})
    assert response.status_code == 200
    assert len(response.json()["results"]) == 4
