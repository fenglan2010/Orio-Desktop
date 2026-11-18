"""Deterministic mock model used for offline demos and as the last fallback."""

from __future__ import annotations

from app.wording import from_to_phrase, rank_from_to
import json
import re
from typing import Any

from app.gateway.adapters.base import BaseAdapter
from app.gateway.protocol import LLMRequest, LLMResponse


def _extract_facts(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull a JSON facts blob out of the prompt if the caller provided one."""
    text = "\n".join(str(m.get("content") or "") for m in messages)
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not match:
        match = re.search(r"FACTS_JSON:\s*(\{.*\})", text, re.S)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}
    return {}


def _analysis_text(facts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    hotel = facts.get("hotel_name") or "杭州西湖精品酒店"
    rank = facts.get("rank_trend") or {}
    start = rank.get("from", 9)
    end = rank.get("to", 23)
    room = facts.get("focus_room") or "豪华大床房"
    comp_cut = facts.get("competitor_cut_pct") or 15
    conv_from = facts.get("conversion_from")
    conv_to = facts.get("conversion_to")
    review = facts.get("negative_review") or "近14日暂无2分及以下评价"
    classification = facts.get("classification") or "structural"
    flags = facts.get("flags") or []
    conv_dropped = "conversion_drop" in flags

    label = "结构性问题" if classification == "structural" else "短期波动"
    conv_clause = ""
    if conv_from is not None and conv_to is not None:
        conv_clause = f"酒店合计转化率{from_to_phrase(conv_from, conv_to, '%')}。"
        if not conv_dropped:
            conv_clause += "按预订/浏览合计看，转化率并未同步走弱。"
    if conv_dropped:
        not_cause = "单纯周末需求波动不足以解释连续多日的排名与转化同步下滑"
        conclusion = f"周末需求波动不足以解释连续多日的排名与转化同步下滑，因此判定为{label}："
        pressure = "差评对转化形成叠加压力。" if "negative_review" in flags else ""
    else:
        not_cause = "单纯周末需求波动不足以解释连续多日的排名下滑与价格倒挂"
        conclusion = f"周末需求波动不足以解释连续多日的排名下滑与价格倒挂，因此判定为{label}："
        pressure = "差评需要单独处理，但不宜写成转化已经走弱。" if "negative_review" in flags else ""
    text = (
        f"{hotel}近一周在携程的搜索表现明显走弱。以{room}为例，搜索排名{rank_from_to(start, end)}。"
        f"{conv_clause}同期主要竞对累计下调售价约{comp_cut}%，而我方门市价基本保持原位，价格带已偏出竞对中位。{review}。"
        f"{conclusion}主因是竞对降价后我方相对价格失去竞争力。{pressure}"
        "建议优先用可回滚的自主调价把价格带拉回竞对区间，并尽快对差评给出诚恳回复，不宜立刻叠加高佣金促销。"
    )
    structured = {
        "classification": classification,
        "summary": text,
        "primary_causes": [
            {"factor": "竞对降价", "contribution": "high", "detail": f"竞对近一周下调约{comp_cut}%"},
            {"factor": "我方价格未跟进", "contribution": "high", "detail": "门市价基本持平，相对价格偏高"},
        ],
        "not_causes": [not_cause],
    }
    if "negative_review" in flags:
        structured["primary_causes"].append({"factor": "差评", "contribution": "medium", "detail": review})
    if conv_dropped:
        structured["primary_causes"].append(
            {"factor": "转化率走弱", "contribution": "medium", "detail": f"转化率{from_to_phrase(conv_from, conv_to, '%')}"}
        )
    return text, structured


def _pricing_text(facts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    room = facts.get("room_name") or "豪华大床房"
    current = facts.get("current_price") or 688
    rec = facts.get("recommended_price") or 598
    lo = facts.get("min_price") or 548
    hi = facts.get("max_price") or 648
    conf = facts.get("confidence") or 0.81
    impact = facts.get("expected_impact") or {}
    rev = impact.get("revenue_delta", 0)
    occ = impact.get("occupancy_delta_pct", 0)
    sign = "增加" if (rev or 0) >= 0 or True else "减少"
    text = (
        f"针对{room}未来定价窗口，建议将挂牌价从约¥{int(current)}调整至¥{int(rec)}，"
        f"允许波动区间¥{int(lo)}–¥{int(hi)}。该区间已校验最低价、库存与竞对价格带，不会击穿底价。"
        f"调价目的是把相对价格拉回竞对中位附近，换回曝光与转化，而不是跟进到底价战。"
        f"按启发式弹性测算，出租率有望回升约{abs(occ):.1f}个百分点，窗口期内营收预计{sign}"
        f"约¥{abs(int(rev))}。置信度{conf:.0%}。本建议不是最终执行价，需收益经理人审后才能下发。"
    )
    return text, {
        "recommended_price": rec,
        "min_price": lo,
        "max_price": hi,
        "confidence": conf,
        "rationale": text,
        "expected_impact": impact,
    }


def _campaign_text(facts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    advice = facts.get("campaign_advice") or {}
    join = advice.get("recommend_join", False)
    name = advice.get("campaign_name") or "携程天天特价"
    reason = advice.get("reason") or "当前已有竞对降价，再叠加平台活动会进一步压缩利润。"
    risk = advice.get("risk") or "不参与可能使曝光继续落后于参与活动的竞对。"
    alt = advice.get("alternative") or "仅对出租率偏低的家庭房在周中小范围观察。"
    decision = "暂不建议全量参加" if not join else "可有限度参加"
    text = (
        f"关于是否参加「{name}」：{decision}。{reason}风险在于：{risk}"
        f"更稳妥的替代方案是先完成自主调价，{alt}活动参与可在观察一周后再评估。"
    )
    return text, {**advice, "rationale": text}


def _reply_text(facts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    rating = int(facts.get("rating") or 2)
    guest = facts.get("guest_name") or "客人"
    content = facts.get("content") or ""
    if rating <= 2:
        draft = (
            f"尊敬的{guest}，非常抱歉这次入住没有达到您的预期。"
            "您提到的噪音和卫生问题我们已立即转交客房与工程核查，临街房源我们会优先安排更换或加强隔音说明，"
            "地毯与客卫清洁标准也会重新培训检查。欢迎您通过订单私信留下联系方式，我们安排专人跟进并提供补偿方案。"
            "再次为给您带来的不便道歉，也期待有机会弥补这次体验。"
        )
    elif rating == 3:
        draft = (
            f"感谢{guest}的反馈。我们已记录您提到的性价比感受，近期会结合周边价格重新审视报价，"
            "也欢迎您下次入住前与我们沟通需求，我们会尽量安排更合适的房型与礼遇。"
        )
    else:
        draft = (
            f"感谢{guest}的入住与点评。您提到的位置和服务我们会继续保持，"
            "也欢迎再次光临杭州西湖精品酒店。"
        )
    if "早餐" in content:
        draft += "关于早餐，我们已向餐饮部同步您的意见。"
    return draft, {"draft": draft, "tone": "apologetic" if rating <= 2 else "grateful"}


def _question(messages: list[dict[str, Any]]) -> str:
    user = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user = str(m.get("content") or "")
            break
    return user.split("FACTS_JSON")[0].strip()


def _yen(n) -> str:
    try:
        return f"¥{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "—"


def _pct(n, digits=1) -> str:
    try:
        return f"{float(n) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _chat_text(messages: list[dict[str, Any]], facts: dict[str, Any]) -> str:
    user = _question(messages)
    kpis = facts.get("kpis") or {}
    today = kpis.get("today") or {}
    last7 = kpis.get("last7") or {}
    today_delta = kpis.get("today_delta") or {}
    last7_delta = kpis.get("last7_delta") or {}
    as_of = facts.get("as_of") or ""
    flags = facts.get("flags") or []
    hotel = facts.get("hotel_name") or "演示酒店"
    alerts = facts.get("alerts") or []
    pending = facts.get("pending_strategies") or []
    rt = facts.get("rank_trend") or {}
    inversions = facts.get("price_inversions") or []

    if any(k in user for k in ("营收", "出租", "转化", "看板", "今天", "今日", "业绩", "RevPAR", "revpar", "OCC", "adr", "ADR", "浏览")):
        bits = [
            f"{hotel}截至 {as_of}：今日营收 {_yen(today.get('revenue'))}，出租率 {_pct(today.get('occupancy'))}，"
            f"转化率 {_pct(today.get('conversion_rate'), 2)}，搜索排名 {today.get('search_rank') or '—'}。"
        ]
        if today_delta:
            bits.append(
                f"较上周同日，营收 {today_delta.get('revenue', 0):+.1f}%，出租率 {today_delta.get('occupancy_pp', 0):+.1f} 个百分点。"
            )
        bits.append(
            f"近 7 日营收 {_yen(last7.get('revenue'))}，出租率 {_pct(last7.get('occupancy'))}，"
            f"转化率 {_pct(last7.get('conversion_rate'), 2)}（预订 {last7.get('orders', 0)} 单 / 浏览 {last7.get('pageviews', 0)} 次）。"
        )
        if last7_delta:
            bits.append(f"近 7 日转化率较前 7 日 {last7_delta.get('conversion_pp', 0):+.2f} 个百分点。")
        if "conversion_drop" not in flags and last7:
            bits.append("按预订/浏览合计看，转化率并未同步走弱。")
        return "".join(bits)

    if any(k in user for k in ("预警", "告警", "警报")):
        if not alerts:
            return f"截至 {as_of}，当前没有未关闭的预警。"
        titles = "；".join(a.get("title") or "" for a in alerts)
        return f"当前有 {len(alerts)} 条未关闭预警：{titles}。可以在监控大屏打开其中一条做因果诊断，或到分析页按日期拆解。"

    if any(k in user for k in ("排名", "下滑", "掉了", "搜索")):
        room = rt.get("room") or "豪华大床房"
        trend = ""
        if rt.get("from") is not None and rt.get("to") is not None:
            from app.wording import rank_from_to
            trend = f"{room}搜索排名{rank_from_to(rt.get('from'), rt.get('to'))}。"
        else:
            trend = f"{room}近7日搜索排名明显后移。"
        extra = ""
        if inversions:
            inv = inversions[0]
            extra = f"同时 {inv.get('room')} 挂牌价约¥{inv.get('our_price')}，高于竞对中位约 {inv.get('gap_pct')}%。"
        conv = "转化也一并走弱。" if "conversion_drop" in flags else "浏览量随排名后移下降，但转化率并未同步走弱。"
        return (
            f"{trend}主因是竞对降价后我方价格基本没动，相对价格偏高。{extra}{conv}"
            "这更像结构性错位，而不是周末波动。可以到分析页生成一版可人审的调价方案。"
        )

    if any(k in user for k in ("差评", "评价", "回复", "噪音", "卫生")):
        return (
            "最近有一条2分评价，客人提到临街噪音和地毯污渍，和图片差距较大。"
            "建议先出一版诚恳的回复草稿，人审后再记为已执行，同时让客房和工程核查。"
            "您可以在执行中心的评价页签里一键生成草稿。"
        )

    if any(k in user for k in ("策略", "定价", "调价", "价格", "活动")):
        if pending:
            listed = "；".join(f"#{s.get('id')} {s.get('title')}" for s in pending)
            queue = f"当前待人审策略有：{listed}。策略必须经人审后才会记入模拟执行，本阶段不会自动改价或对外报名。"
        else:
            queue = "当前没有待人审策略，可到分析页或执行中心生成定价或活动参与建议。"
        return (
            "定价上不要跟进到底价战。启发式会给出落在竞对价格带内、且不低于酒店价格约束的推荐区间，"
            "大模型只写理由，最终执行价必须人审。"
            + queue
        )

    if facts.get("briefing"):
        return str(facts["briefing"])
    return (
        f"我是 OIRO 收益助手，可以帮你查今日营收、解释预警，或追问某条待人审策略。"
        f"当前演示酒店是{hotel}（模拟数据）。你可以直接问「今天营收怎么样」或「为什么排名掉了」。"
    )


class MockModelAdapter(BaseAdapter):
    vendor = "mock"
    outbound = False

    def available(self) -> bool:
        return True

    def complete(self, request: LLMRequest) -> LLMResponse:
        facts = _extract_facts(request.messages)
        task = request.task_type
        structured: dict[str, Any] | None = None
        if task == "analysis":
            content, structured = _analysis_text(facts)
        elif task == "pricing_strategy":
            if facts.get("kind") == "campaign":
                content, structured = _campaign_text(facts)
            else:
                content, structured = _pricing_text(facts)
        elif task == "review_reply":
            content, structured = _reply_text(facts)
        elif task == "vision_or_longdoc":
            content = "当前为本地模拟结果：尚未接入真实多模态模型，无法解析上传图片，请改用经营数据与评价文本。"
        else:
            content = _chat_text(request.messages, facts)

        if request.response_schema and structured is None:
            structured = {"text": content}

        return LLMResponse(
            vendor="mock",
            model="mock-deterministic",
            content=content,
            structured=structured,
            usage={"prompt_tokens": 0, "completion_tokens": max(1, len(content) // 4)},
            degraded=True,
            outbound=False,
            status="ok",
            degrade_notice="当前为降级结果（本地模拟模型）",
        )
