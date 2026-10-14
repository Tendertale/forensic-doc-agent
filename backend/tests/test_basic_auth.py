"""访问控制：可选 HTTP Basic Auth（部署到服务器时的最小防护）。

依据：`docs/DEPLOY.md`（应用自身无登录，公网暴露前必须加一层访问控制）；
`AGENTS.md` 硬性约束第 9 条同类思路——密钥/凭据只走请求头，不落库、不落日志。

覆盖：默认关闭、配置后必须带凭据、凭据错误 401、`/api/health` 免校验、配置格式非法时忽略。
"""

from __future__ import annotations

import pytest

ENV = "FORENSIC_BASIC_AUTH"


def test_disabled_by_default(client, monkeypatch):
    """未设置 FORENSIC_BASIC_AUTH 时不启用鉴权（本机与隧道场景）。"""
    monkeypatch.delenv(ENV, raising=False)
    assert client.get("/api/config").status_code == 200


def test_credentials_required_when_configured(client, monkeypatch):
    """配置凭据后：无凭据/错凭据 → 401（带 WWW-Authenticate）；正确凭据 → 200。"""
    monkeypatch.setenv(ENV, "forensic:Test-Pw-123")

    anonymous = client.get("/api/config")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]
    assert anonymous.headers["www-authenticate"].startswith("Basic ")

    assert client.get("/api/config", auth=("forensic", "wrong")).status_code == 401
    assert client.get("/api/config", auth=("someone", "Test-Pw-123")).status_code == 401
    assert client.get("/api/config", auth=("forensic", "Test-Pw-123")).status_code == 200
    assert client.get("/", auth=("forensic", "Test-Pw-123")).status_code == 200


def test_health_endpoint_exempt(client, monkeypatch):
    """`/api/health` 免校验：便于反代/监控探活，且不泄露任何案件数据。"""
    monkeypatch.setenv(ENV, "forensic:Test-Pw-123")
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_invalid_configuration_is_ignored(client, monkeypatch):
    """配置缺少用户名/口令或没有冒号时视为未配置，而不是把所有人挡在门外。"""
    for value in ("no-colon", ":only-password", "only-user:"):
        monkeypatch.setenv(ENV, value)
        assert client.get("/api/config").status_code == 200, value


def test_write_endpoints_also_protected(client, seeded_case, monkeypatch):
    """写操作同样受保护（未认证不得建案）。"""
    monkeypatch.setenv(ENV, "forensic:Test-Pw-123")
    payload = {"case_no": "", "commission_unit": "未认证单位"}
    assert client.post("/api/cases", json=payload).status_code == 401
    assert client.post("/api/cases", json=payload,
                       auth=("forensic", "Test-Pw-123")).status_code in (200, 201)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    yield
