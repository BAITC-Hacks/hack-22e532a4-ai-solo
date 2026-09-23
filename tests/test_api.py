from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_reports_truthful_model_mode():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["model_mode"] in {"offline", "live"}
    assert "docx" in response.json()["supported_formats"]


def test_public_probe_detects_transfer_candidate():
    response = client.post(
        "/api/evaluate/probe",
        json={
            "before_text": "взаимодействует с субъектами СВК и оценивает результаты",
            "after_text": "осуществляет взаимодействие с субъектами СВК, включая оценку результатов",
        },
    )
    assert response.status_code == 200
    assert response.json()["related"] is True


def test_public_probe_rejects_common_words_across_scopes():
    response = client.post(
        "/api/evaluate/probe",
        json={
            "before_text": "организует аудит ИТ-систем и данных",
            "after_text": "организует аудит операционных и поддерживающих процессов",
            "before_scope": "ИТ, данные",
            "after_scope": "операционные, поддерживающие",
        },
    )
    assert response.status_code == 200
    assert response.json()["scope_conflict"] is True
    assert response.json()["strong_overlap"] is False
