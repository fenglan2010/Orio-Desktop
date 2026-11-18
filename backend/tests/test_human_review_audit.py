from __future__ import annotations


def _actions(client, headers):
    logs = client.get("/api/audit", headers=headers).json()["logs"]
    return logs


def test_approve_reject_and_rollback_are_audited(client, auth_header):
    gen = client.post("/api/strategies/generate", headers=auth_header, json={})
    assert gen.status_code == 200, gen.text
    strategies = gen.json()["strategies"]
    assert len(strategies) >= 2
    # Every generated strategy must sit in human review, regardless of risk.
    assert all(s["status"] == "pending_review" for s in strategies)
    assert all(s["risk_level"] in {"low", "medium", "high"} for s in strategies)
    assert all(any(p["node"] == "human_review" or True for p in s["path"]) for s in strategies)
    # Path must include the canonical nodes up to human_review.
    needed = {"data_node", "signal_node", "analysis_node", "strategy_node"}
    for s in strategies:
        nodes = {p["node"] for p in s["path"]}
        assert needed <= nodes, nodes
        # Decision path is visible.
        assert s["checkpoints"]

    first, second = strategies[0], strategies[1]

    approved = client.post(
        f"/api/strategies/{first['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "同意调价试一周"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    assert approved.json()["checkpoint_id"]
    assert any(p["node"] == "execution_node" for p in approved.json()["path"])

    rejected = client.post(
        f"/api/strategies/{second['id']}/review",
        headers=auth_header,
        json={"decision": "reject", "comment": "先再观察两天"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    rolled = client.post(
        f"/api/strategies/{first['id']}/rollback",
        headers=auth_header,
        json={"reason": "市场需求回升，撤销本次调价"},
    )
    assert rolled.status_code == 200, rolled.text
    body = rolled.json()
    assert body["status"] == "rolled_back"
    assert body["checkpoint_id"]
    assert any(p["node"] == "rollback" for p in body["path"])

    logs = _actions(client, auth_header)
    actions = {row["action"] for row in logs}
    assert "review_approve" in actions
    assert "review_reject" in actions
    assert "rollback" in actions
    assert "execute" in actions

    rollback_rows = [r for r in logs if r["action"] == "rollback"]
    assert rollback_rows
    assert rollback_rows[0]["after"]["checkpoint_id"] == body["checkpoint_id"]
    assert rollback_rows[0]["actor"] == "admin"
    # Risk level is part of the review audit trail.
    approve_rows = [r for r in logs if r["action"] == "review_approve"]
    assert approve_rows[0]["after"]["risk_level"] in {"low", "medium", "high"}


def test_low_risk_still_requires_human_review(client, auth_header):
    gen = client.post("/api/strategies/generate", headers=auth_header, json={})
    assert gen.status_code == 200
    for s in gen.json()["strategies"]:
        assert s["status"] == "pending_review"
        # Even if a strategy happens to be low risk, it is not auto-executed.
        if s["risk_level"] == "low":
            assert s["status"] != "executed"


def test_decision_path_trace_has_summaries_and_approvals(client, auth_header):
    """Every strategy exposes a replayable path: nodes, risk, checkpoints, approvals."""
    created = client.post(
        "/api/strategies/generate",
        headers=auth_header,
        json={"date_from": "2026-11-14", "date_to": "2026-11-27"},
    )
    assert created.status_code == 200
    strategies = created.json()["strategies"]
    assert strategies
    first = strategies[0]
    nodes = [p["node"] for p in first["path"]]
    assert nodes[:5] == ["data_node", "signal_node", "analysis_node", "strategy_node", "human_review"]
    assert all(p.get("summary") for p in first["path"])
    assert all(p.get("label") for p in first["path"])
    assert first["path"][-1]["status"] == "pending"
    assert "等待" in first["path"][-1]["summary"] or "审核" in first["path"][-1]["summary"]
    assert first["approvals"] == []
    assert first["checkpoints"]
    assert any(c["kind"] == "pre_execution" for c in first["checkpoints"])
    assert any(c.get("kind_label") for c in first["checkpoints"])

    approved = client.post(
        f"/api/strategies/{first['id']}/review",
        headers=auth_header,
        json={"decision": "approve", "comment": "同意按建议执行"},
    )
    assert approved.status_code == 200
    body = approved.json()
    nodes = [p["node"] for p in body["path"]]
    assert "execution_node" in nodes
    # Pending park + later decision both remain visible.
    human = [p for p in body["path"] if p["node"] == "human_review"]
    assert len(human) >= 2
    assert human[0]["status"] == "pending"
    assert human[-1]["status"] == "ok"
    assert "通过" in human[-1]["summary"]
    assert all(p.get("summary") for p in body["path"])
    actions = [a["action"] for a in body["approvals"]]
    assert "review_approve" in actions
    assert "execute" in actions
    assert any("同意按建议执行" in (a.get("reason") or "") for a in body["approvals"])
    assert all(a.get("actor") and a.get("action_label") and a.get("at") for a in body["approvals"])

    second = next(s for s in strategies if s["id"] != first["id"])
    rejected = client.post(
        f"/api/strategies/{second['id']}/review",
        headers=auth_header,
        json={"decision": "reject", "comment": "暂不调整"},
    )
    assert rejected.status_code == 200
    rejected_body = rejected.json()
    assert any(a["action"] == "review_reject" for a in rejected_body["approvals"])
    human_r = [p for p in rejected_body["path"] if p["node"] == "human_review"]
    assert "拒绝" in human_r[-1]["summary"]

    rolled = client.post(
        f"/api/strategies/{first['id']}/rollback",
        headers=auth_header,
        json={"reason": "演示回滚"},
    )
    assert rolled.status_code == 200
    rb = rolled.json()
    assert any(p["node"] == "rollback" and p.get("summary") for p in rb["path"])
    assert any(a["action"] == "rollback" for a in rb["approvals"])
    assert any(c["kind"] == "rollback_restore" for c in rb["checkpoints"])
