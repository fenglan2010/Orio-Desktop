"""Assemble a hotel-facing decision trace from node logs, checkpoints and audits."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, DecisionCheckpoint, Strategy, WorkflowNodeLog, WorkflowRun

FLAG_LABELS = {
    "rank_drop": "排名下跌",
    "conversion_drop": "转化走弱",
    "price_inversion": "价格倒挂",
    "competitor_cut": "竞对降价",
    "negative_review": "差评影响",
}

CLASS_LABELS = {
    "structural": "结构性问题",
    "short_term": "短期波动",
}

RISK_LABELS = {"low": "低", "medium": "中", "high": "高"}

NODE_LABELS = {
    "data_node": "数据采集",
    "signal_node": "信号识别",
    "analysis_node": "因果分析",
    "strategy_node": "策略生成",
    "human_review": "人工审核",
    "execution_node": "沙盒执行",
    "rollback": "检查点回滚",
    "memory_node": "经验沉淀",
}

KIND_LABELS = {
    "node": "节点快照",
    "pre_execution": "执行前检查点",
    "rollback_restore": "回滚还原点",
}

ACTION_LABELS = {
    "review_approve": "审批通过",
    "review_reject": "审批拒绝",
    "execute": "沙盒执行",
    "auto_execute": "自动执行",
    "execution_refused": "写入被拒绝",
    "update_execution_policy": "更新执行策略",
    "rollback": "回滚",
}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else dt


def _risk_text(risk: str | None) -> str:
    return RISK_LABELS.get(risk or "", risk or "—")


def summarize_node(
    node_name: str,
    state: dict | None,
    *,
    strategy: Strategy | None = None,
    log_output: dict | None = None,
) -> str:
    if log_output and log_output.get("summary"):
        return str(log_output["summary"])
    state = state or {}
    signals = state.get("signals") or {}
    analysis = state.get("analysis_result") or {}
    strat = state.get("strategy") or {}

    if node_name == "data_node":
        hotel = (state.get("hotel_profile") or {}).get("name") or "当前酒店"
        date_from = state.get("date_from") or ""
        date_to = state.get("date_to") or ""
        window = f"{date_from} 至 {date_to}" if date_from else "当日快照"
        return f"已采集{hotel}的酒店档案、渠道快照与竞对数据（携程 · 模拟数据），观察窗口 {window}。"

    if node_name == "signal_node":
        flags = [FLAG_LABELS.get(f, f) for f in (signals.get("flags") or [])]
        if flags:
            return "识别到经营信号：" + "、".join(flags) + "。"
        return "本轮未识别出需要处理的经营信号。"

    if node_name == "analysis_node":
        label = analysis.get("classification_label") or CLASS_LABELS.get(analysis.get("classification") or "", "")
        causes = [c.get("factor") for c in (analysis.get("primary_causes") or []) if c.get("factor")]
        if label and causes:
            return f"判定为{label}，主要原因：{'、'.join(causes[:3])}。"
        if label:
            return f"判定为{label}。"
        return "已完成因果拆解。"

    if node_name == "strategy_node":
        kind = (strategy.kind if strategy else None) or strat.get("kind") or state.get("kind") or ""
        risk = (strategy.risk_level if strategy else None) or state.get("risk_level") or ""
        risk_txt = _risk_text(risk)
        if kind in {"campaign", "campaign_advice"} or strat.get("campaign_advice"):
            advice = strat.get("campaign_advice") or (strategy.campaign_advice if strategy else None) or {}
            name = advice.get("campaign_name") or "渠道活动"
            rec = advice.get("recommendation_label") or (
                "建议参加" if advice.get("recommend_join") else "暂不建议全量参加"
            )
            scope = advice.get("scope") or ""
            limited = [
                o for o in (advice.get("options") or [])
                if o.get("recommendation") == "join_limited"
            ]
            extra = ""
            if limited:
                extra = "；另建议限范围试参加" + "、".join(o.get("campaign_name") or "" for o in limited)
            conf = strat.get("confidence")
            if conf is None and strategy is not None:
                conf = strategy.confidence
            conf_txt = f"，置信度 {round((conf or 0) * 100)}%" if conf else ""
            scope_txt = f"（{scope}）" if scope else ""
            return f"{rec}「{name}」{scope_txt}{extra}{conf_txt}，风险等级{risk_txt}。"
        price = strat.get("recommended_price")
        current = strat.get("current_price")
        if price is None and strategy is not None:
            price = strategy.recommended_price
        if current is None and strategy is not None:
            current = strategy.current_price
        conf = strat.get("confidence")
        if conf is None and strategy is not None:
            conf = strategy.confidence
        conf_txt = f"，置信度 {round((conf or 0) * 100)}%" if conf else ""
        if price is not None and current is not None:
            return (
                f"建议将挂牌价由约¥{round(current)} 调整至约¥{round(price)}{conf_txt}，"
                f"风险等级{risk_txt}。"
            )
        if price is not None:
            return f"给出推荐价约¥{round(price)}{conf_txt}，风险等级{risk_txt}。"
        return f"已生成策略建议，风险等级{risk_txt}。"

    if node_name == "human_review":
        approval = (state.get("approval_status") or "").lower()
        if strategy and approval in {"", "pending"}:
            if strategy.status == "rejected":
                approval = "rejected"
            elif strategy.status in {"approved", "executed", "rolled_back"}:
                approval = "approved"
        reviewer = state.get("actor") or (strategy.reviewer if strategy else "") or "审核人"
        comment = state.get("review_comment") or (strategy.review_comment if strategy else "") or ""
        if reviewer == "system:auto_execute":
            reviewer = "系统（自动执行）"
        if approval in {"approved", "executed", "rolled_back"}:
            base = f"{reviewer}已通过该策略"
            if comment:
                base += f"：{comment}"
            return base + "。"
        if approval == "rejected":
            base = f"{reviewer}已拒绝该策略"
            if comment:
                base += f"：{comment}"
            return base + "。"
        return "策略已送达人工审核，等待确认。未通过自动执行放行规则时将在此等待人审。"

    if node_name == "execution_node":
        result = state.get("execution_result") or (strategy.execution_payload if strategy else None) or {}
        msg = result.get("message") if isinstance(result, dict) else None
        return msg or "已按审核结果写入官方沙盒回执，未向真实 OTA 发送请求。"

    if node_name == "memory_node":
        status = (state.get("status") or state.get("approval_status") or "").lower()
        if status == "rejected":
            return "已将本店否决结论写入经验沉淀。结果影响尚未测算。"
        if status == "rolled_back":
            return "已将本店回滚结论写入经验沉淀。结果影响尚未测算。"
        if status in {"execution_failed", "execution_partial"}:
            return "已将本店执行结果写入经验沉淀。结果影响尚未测算。"
        return "已将本店决策写入经验沉淀。结果影响尚未测算，不会据此自动改价。"

    if node_name == "rollback":
        result = state.get("execution_result") or {}
        cid = ""
        if isinstance(result, dict):
            cid = str(result.get("restored_checkpoint_id") or state.get("checkpoint_id") or "")
        else:
            cid = str(state.get("checkpoint_id") or "")
        short = cid[:8] if cid else ""
        reason = (strategy.rollback_reason if strategy else "") or ""
        text = f"已回滚至执行前检查点 {short}" if short else "已按执行前检查点回退状态"
        if reason:
            text += f"：{reason}"
        return text + "（沙盒官方回执，未调用真实 OTA）。"

    return ""


def node_output_payload(node_name: str, state: dict) -> dict[str, Any]:
    return {
        "current_node": node_name,
        "approval_status": state.get("approval_status"),
        "risk_level": state.get("risk_level"),
        "checkpoint_id": state.get("checkpoint_id"),
        "summary": summarize_node(node_name, state),
    }


def assemble_trace(db: Session, run_id: int | None, *, strategy: Strategy | None = None) -> dict:
    empty = {
        "path": [],
        "checkpoints": [],
        "approvals": [],
        "current_node": None,
        "risk_level": strategy.risk_level if strategy else None,
        "checkpoint_id": strategy.checkpoint_id if strategy else None,
    }
    if not run_id:
        return empty

    run = db.get(WorkflowRun, run_id)
    logs = (
        db.query(WorkflowNodeLog)
        .filter(WorkflowNodeLog.run_id == run_id)
        .order_by(WorkflowNodeLog.id.asc())
        .all()
    )
    checkpoints = (
        db.query(DecisionCheckpoint)
        .filter(DecisionCheckpoint.run_id == run_id)
        .order_by(DecisionCheckpoint.id.asc())
        .all()
    )
    audits = (
        db.query(AuditLog)
        .filter(AuditLog.entity_type == "workflow_run", AuditLog.entity_id == str(run_id))
        .order_by(AuditLog.id.asc())
        .all()
    )
    payload = (run.payload or {}) if run else {}
    ck_queue = list(checkpoints)
    path: list[dict] = []
    for log in logs:
        matched: DecisionCheckpoint | None = None
        for i, ck in enumerate(ck_queue):
            if ck.node_name == log.node_name:
                matched = ck_queue.pop(i)
                break
        out = log.output_json or {}
        state = dict(matched.state or payload) if matched is not None else dict(payload)
        if log.node_name == "human_review" and log.status == "pending":
            state["approval_status"] = "pending"
            state["status"] = "pending_review"
        summary = out.get("summary") or summarize_node(log.node_name, state, strategy=strategy, log_output=out)
        path.append(
            {
                "node": log.node_name,
                "label": NODE_LABELS.get(log.node_name, log.node_name),
                "status": log.status,
                "started_at": _iso(log.started_at),
                "finished_at": _iso(log.finished_at),
                "error": log.error,
                "summary": summary,
                "checkpoint_id": out.get("checkpoint_id") or (matched.checkpoint_id if matched else None),
                "risk_level": out.get("risk_level") or state.get("risk_level") or (strategy.risk_level if strategy else None),
            }
        )

    ck_dump = [
        {
            "checkpoint_id": c.checkpoint_id,
            "node_name": c.node_name,
            "node_label": NODE_LABELS.get(c.node_name, c.node_name),
            "kind": c.kind,
            "kind_label": KIND_LABELS.get(c.kind, c.kind),
            "created_at": _iso(c.created_at),
        }
        for c in checkpoints
    ]

    approvals: list[dict] = []
    for row in audits:
        after = row.after or {}
        approvals.append(
            {
                "action": row.action,
                "action_label": ACTION_LABELS.get(row.action, row.action),
                "actor": row.actor,
                "at": _iso(row.created_at),
                "reason": row.reason or "",
                "risk_level": after.get("risk_level"),
                "checkpoint_id": after.get("checkpoint_id"),
            }
        )
    if strategy and strategy.reviewer and strategy.reviewed_at and not any(
        item["action"] in {"review_approve", "review_reject"} for item in approvals
    ):
        action = "review_reject" if strategy.status == "rejected" else "review_approve"
        approvals.insert(
            0,
            {
                "action": action,
                "action_label": ACTION_LABELS[action],
                "actor": strategy.reviewer,
                "at": _iso(strategy.reviewed_at),
                "reason": strategy.review_comment or "",
                "risk_level": strategy.risk_level,
                "checkpoint_id": strategy.checkpoint_id,
            },
        )

    return {
        "path": path,
        "checkpoints": ck_dump,
        "approvals": approvals,
        "current_node": run.current_node if run else None,
        "risk_level": (strategy.risk_level if strategy else None) or payload.get("risk_level"),
        "checkpoint_id": (strategy.checkpoint_id if strategy else None) or payload.get("checkpoint_id"),
    }
