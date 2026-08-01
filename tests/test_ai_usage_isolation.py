"""Faz 6 — AI + usage tenant-aware kanıtları.

  * log_usage kaydı aktif tenant'la damgalanır (usage_logs.tenant_id).
  * usage_logs tenant'lar arası izole.
  * Dashboard AI Usage sayfası yalnız AKTİF tenant'ın verisini gösterir
    (istekler, tokenlar, maliyet, en yoğun müşteriler).
"""

from Services import usage_logger, dashboard_service
from Services.db import get_session, tenant_scope
from Services.models import UsageLog
from conftest import TENANT_A, TENANT_B


def test_usage_logs_tenant_stamped_and_isolated(env):
    with tenant_scope(TENANT_A):
        usage_logger.log_usage("cust_a", "gpt-4.1-mini", 10, 5, 15, 0.05, 0.4)
        usage_logger.log_usage("cust_a", "gpt-4.1-mini", 20, 5, 25, 0.09, 0.5)
    with tenant_scope(TENANT_B):
        usage_logger.log_usage("cust_b", "gpt-4o", 100, 50, 150, 0.5, 1.0)

    with tenant_scope(TENANT_A):
        with get_session() as s:
            rows = s.query(UsageLog).all()
            assert len(rows) == 2
            assert all(r.tenant_id == TENANT_A for r in rows)

    with tenant_scope(TENANT_B):
        with get_session() as s:
            rows = s.query(UsageLog).all()
            assert len(rows) == 1
            assert rows[0].tenant_id == TENANT_B
            assert rows[0].sender == "cust_b"


def test_ai_usage_dashboard_isolated(env, monkeypatch):
    monkeypatch.setattr(dashboard_service, "get_usd_try_rate", lambda: 40.0)

    with tenant_scope(TENANT_A):
        usage_logger.log_usage("cust_a", "m", 10, 5, 15, 0.05, 0.4)
    with tenant_scope(TENANT_B):
        usage_logger.log_usage("cust_b", "m", 999, 999, 1998, 0.99, 9.9)

    with tenant_scope(TENANT_A):
        data = dashboard_service.get_ai_usage_detail()

    assert data["summary"]["total_requests"] == 1
    assert round(data["summary"]["total_cost_usd"], 2) == 0.05
    # B'nin yüksek maliyeti/müşterisi A'nın panelinde GÖRÜNMEZ
    senders = {c["sender"] for c in data["top_customers_by_cost"]}
    assert senders == {"cust_a"}


def test_dashboard_summary_isolated(env, monkeypatch):
    monkeypatch.setattr(dashboard_service, "get_usd_try_rate", lambda: 40.0)

    with tenant_scope(TENANT_A):
        usage_logger.log_usage("cust_a", "m", 10, 5, 15, 0.05, 0.4)
        usage_logger.log_usage("cust_a2", "m", 10, 5, 15, 0.05, 0.4)
    with tenant_scope(TENANT_B):
        usage_logger.log_usage("cust_b", "m", 10, 5, 15, 0.05, 0.4)

    with tenant_scope(TENANT_A):
        data = dashboard_service.get_dashboard_data()

    assert data["business"]["total_requests"] == 2
    assert data["business"]["unique_customers"] == 2
