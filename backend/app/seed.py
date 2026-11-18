"""Demo tenant seed. Re-importable without wiping human-review records.

Story (relative to today):
  days -14..-8  — stable rank, healthy conversion, prices in band
  days -7..-1   — competitors cut 12–18%, our list price stays put (price inversion),
                  rank and conversion drop; a 2-star review on day -3 mentions noise and cleanliness.
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import DATA_DIR, get_settings
from app.database import SessionLocal, get_engine, init_db, reset_engine
from app.services.kpis import pack_metrics
from app.wording import from_to_phrase, rank_from_to
from app.models import (
    Alert,
    Base,
    ChatMessage,
    CompetitorSnapshot,
    DailyMetric,
    Hotel,
    HotelMembership,
    Review,
    RoomType,
    SystemSetting,
    User,
)
from app.services.onboarding import ensure_demo_product_enabled
from app.services.tenancy import backfill_audit_hotel, ensure_membership, migrate_legacy_settings

COMPETITORS = ("西湖和禧酒店", "湖滨商务酒店", "湖畔丽舍酒店")

ROOM_SPECS = (
    {"name": "豪华大床房", "code": "DK", "inventory": 20, "capacity": 2, "base_price": 688, "min_price": 458, "max_price": 1288, "rank0": 9, "occ0": 0.76, "conv0": 0.032},
    {"name": "商务双床房", "code": "BT", "inventory": 16, "capacity": 2, "base_price": 628, "min_price": 428, "max_price": 1188, "rank0": 11, "occ0": 0.71, "conv0": 0.029},
    {"name": "家庭房", "code": "FAM", "inventory": 8, "capacity": 3, "base_price": 858, "min_price": 568, "max_price": 1588, "rank0": 16, "occ0": 0.54, "conv0": 0.022},
)


def _dow_factor(d: date) -> float:
    if d.weekday() >= 5:
        return 1.12
    if d.weekday() == 4:
        return 1.05
    return 1.0


def _price(value: float) -> int:
    raw = int(round(value / 10.0) * 10)
    return raw - 2 if raw % 10 == 0 and raw > 20 else max(1, raw)


def _rng(seed: int = 42) -> random.Random:
    return random.Random(seed)


DEMO_ACCOUNTS = (
    # username, password, display_name, role
    # The seeded admin password still comes from settings (default admin123).
    ("manager", "manager123", "收益经理", "revenue_manager"),
    ("approver", "approver123", "调价审批人", "approver"),
    ("analyst", "analyst123", "运营观察", "analyst"),
)

# Internal staff logins. Not hotel tenant accounts and not shown in hotel Settings.
# `ops` / `ops123` is the 管理员 account (replaces the legacy single `operator` role).
OPS_ACCOUNTS = (
    ("ops", "ops123", "内部管理员", "ops_admin"),
    ("ops_contract", "contract123", "合同专员", "ops_contract"),
    ("ops_sysops", "sysops123", "系统运维", "ops_sysops"),
    ("ops_operations", "operations123", "运营（兼 BD）", "ops_operations"),
    ("ops_support", "support123", "客服", "ops_support"),
)
OPS_ACCOUNT = OPS_ACCOUNTS[0]


def seed_ops_user(db: Session) -> User:
    """Ensure internal staff accounts exist, with no hotel membership."""
    admin = None
    for username, password, display_name, role in OPS_ACCOUNTS:
        user = db.query(User).filter(User.username == username).one_or_none()
        if user is None:
            user = User(
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
                role=role,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            if user.role != role:
                user.role = role
            if user.display_name in {"", None, "运维管理员"} and display_name:
                user.display_name = display_name
            if getattr(user, "disabled", False) and username == "ops":
                # The seeded admin must remain usable after migrate.
                user.disabled = False
                user.disabled_at = None
            db.commit()
            db.refresh(user)
        db.query(HotelMembership).filter(HotelMembership.user_id == user.id).delete(synchronize_session=False)
        db.commit()
        if username == "ops":
            admin = user
    return admin

def seed_users(db: Session) -> User:
    """Ensure the four demo accounts exist. Promote the historical admin role.

    Existing passwords are not reset on later startups, so a changed password
    survives reseed. The default admin is always given the `admin` role.
    """
    settings = get_settings()
    user = db.query(User).filter(User.username == settings.demo_username).one_or_none()
    if user is None:
        user = User(
            username=settings.demo_username,
            password_hash=hash_password(settings.demo_password),
            display_name="酒店管理员",
            role="admin",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if user.role != "admin":
            user.role = "admin"
        if user.display_name in {"收益经理", "", None}:
            user.display_name = "酒店管理员"
        if getattr(user, "disabled", False):
            # The seeded demo admin must remain usable after migrate.
            user.disabled = False
            user.disabled_at = None
        db.commit()
        db.refresh(user)

    for username, password, display_name, role in DEMO_ACCOUNTS:
        row = db.query(User).filter(User.username == username).one_or_none()
        if row is None:
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    display_name=display_name,
                    role=role,
                )
            )
    db.commit()
    return user


def seed_hotel(db: Session) -> Hotel:
    hotel = db.query(Hotel).filter(Hotel.tenant_key == "demo").one_or_none()
    if hotel is None:
        hotel = Hotel(
            name="杭州西湖精品酒店（演示）",
            city="杭州",
            address="杭州市西湖区湖滨路88号",
            positioning="湖滨精品商务酒店",
            tenant_key="demo",
            platform="ctrip",
        )
        db.add(hotel)
        db.commit()
        db.refresh(hotel)
    rooms = {r.code: r for r in db.query(RoomType).filter(RoomType.hotel_id == hotel.id).all()}
    for spec in ROOM_SPECS:
        if spec["code"] in rooms:
            continue
        db.add(
            RoomType(
                hotel_id=hotel.id,
                name=spec["name"],
                code=spec["code"],
                inventory=spec["inventory"],
                capacity=spec["capacity"],
                base_price=spec["base_price"],
                min_price=spec["min_price"],
                max_price=spec["max_price"],
            )
        )
    db.commit()
    return hotel


def _metric_row(hotel_id, room, spec, day: date, today: date, rng: random.Random) -> dict:
    offset = (day - today).days  # -14 .. 13
    # Today is the in-progress operating day (营业日), not a forecast day.
    is_future = offset > 0
    factor = _dow_factor(day)
    decline_age = max(0, offset - (-7) + 1) if offset >= -7 else 0  # 1.. at day -7
    if is_future:
        # Competitors stay low; our list price still hasn't moved.
        decline_age = 8 + offset

    our_list = _price(spec["base_price"] * factor)
    # Competitor starting prices sit slightly above us, then cut after day -7.
    cut = min(0.18, 0.04 * max(decline_age, 0)) if decline_age else 0.0
    # 湖畔丽舍 cuts less (higher positioning), 湖滨商务 cuts more.
    cuts = {
        "西湖和禧酒店": cut * 1.0,
        "湖滨商务酒店": cut * 1.15,
        "湖畔丽舍酒店": cut * 0.55,
    }
    comp_base = {
        "西湖和禧酒店": spec["base_price"] * 0.99,
        "湖滨商务酒店": spec["base_price"] * 0.95,
        "湖畔丽舍酒店": spec["base_price"] * 1.08,
    }

    rank = spec["rank0"] + (0 if decline_age <= 0 else int(2 * decline_age + rng.choice([-1, 0, 0, 1])))
    rank = max(3, min(40, rank + (1 if day.weekday() >= 5 else 0)))

    occ_drop = 0.035 * min(decline_age, 8) if decline_age else 0
    occ = max(0.18, min(0.95, spec["occ0"] * factor - occ_drop + rng.uniform(-0.02, 0.02)))
    if is_future:
        # Pickup: nearer dates have more already-sold rooms.
        pickup = max(0.05, 0.45 - offset * 0.03)
        occ = max(0.05, min(0.7, pickup * spec["occ0"] * factor))

    sold = int(round(spec["inventory"] * occ))
    sold = max(0, min(spec["inventory"], sold))
    occ = sold / spec["inventory"] if spec["inventory"] else 0
    adr = _price(our_list * (0.97 + rng.uniform(-0.01, 0.01)))
    revenue = 0 if is_future else int(adr * sold)

    conv0 = spec["conv0"]
    conv = max(0.008, conv0 * (1 - 0.06 * min(decline_age, 8)) + rng.uniform(-0.002, 0.002))
    impressions = int((820 if spec["code"] == "DK" else 640 if spec["code"] == "BT" else 380) * factor * (1 - 0.015 * min(decline_age, 8)))
    clicks = max(8, int(impressions * (0.045 - 0.002 * min(decline_age, 8))))
    bookings = 0 if is_future else max(sold, 1) if sold else 0
    # Keep conversion consistent with bookings / clicks when historical.
    if not is_future and clicks:
        conv = bookings / max(clicks, 1)
        # bookings roughly related to sold; keep in a sane band
        bookings = max(1, min(sold if sold else 1, int(clicks * max(0.01, spec["conv0"] * (1 - 0.06 * min(decline_age, 8))))))
        conv = bookings / clicks
        sold = max(sold, min(spec["inventory"], bookings))
        occ = sold / spec["inventory"]
        revenue = int(adr * sold)

    comps = []
    for name in COMPETITORS:
        price = _price(comp_base[name] * factor * (1 - cuts[name]) + rng.uniform(-6, 6))
        comps.append({"name": name, "price": price, "rank": max(3, rank + rng.randint(-4, 3) - (3 if cuts[name] > 0.1 else 0))})

    return {
        "metric": dict(
            hotel_id=hotel_id,
            room_type_id=room.id,
            date=day,
            platform="ctrip",
            is_future=is_future,
            available_rooms=spec["inventory"],
            sold_rooms=sold,
            occupancy=round(occ, 4),
            list_price=our_list,
            adr=adr if not is_future else our_list,
            revenue=revenue,
            search_rank=rank,
            impressions=impressions,
            clicks=clicks,
            bookings=bookings if not is_future else max(0, sold),
            conversion_rate=round(conv, 4),
        ),
        "competitors": comps,
    }


def seed_metrics(db: Session, hotel: Hotel, today: date) -> None:
    rng = _rng(42)
    rooms = {r.code: r for r in db.query(RoomType).filter(RoomType.hotel_id == hotel.id).all()}
    # Refresh the rolling window; do not touch other tables.
    window_start = today - timedelta(days=14)
    window_end = today + timedelta(days=13)
    db.query(DailyMetric).filter(
        DailyMetric.hotel_id == hotel.id,
        (DailyMetric.date < window_start) | (DailyMetric.date > window_end),
    ).delete(synchronize_session=False)
    db.query(CompetitorSnapshot).filter(
        CompetitorSnapshot.hotel_id == hotel.id,
        (CompetitorSnapshot.date < window_start) | (CompetitorSnapshot.date > window_end),
    ).delete(synchronize_session=False)
    db.commit()

    day = window_start
    while day <= window_end:
        for spec in ROOM_SPECS:
            room = rooms[spec["code"]]
            built = _metric_row(hotel.id, room, spec, day, today, rng)
            existing = (
                db.query(DailyMetric)
                .filter(
                    DailyMetric.hotel_id == hotel.id,
                    DailyMetric.room_type_id == room.id,
                    DailyMetric.date == day,
                    DailyMetric.platform == "ctrip",
                )
                .one_or_none()
            )
            if existing:
                for k, v in built["metric"].items():
                    if k in {"hotel_id", "room_type_id", "date", "platform"}:
                        continue
                    setattr(existing, k, v)
            else:
                db.add(DailyMetric(**built["metric"]))
            for c in built["competitors"]:
                row = (
                    db.query(CompetitorSnapshot)
                    .filter(
                        CompetitorSnapshot.hotel_id == hotel.id,
                        CompetitorSnapshot.room_type_id == room.id,
                        CompetitorSnapshot.date == day,
                        CompetitorSnapshot.competitor_name == c["name"],
                    )
                    .one_or_none()
                )
                if row:
                    row.price = c["price"]
                    row.rank = c["rank"]
                else:
                    db.add(
                        CompetitorSnapshot(
                            hotel_id=hotel.id,
                            room_type_id=room.id,
                            date=day,
                            competitor_name=c["name"],
                            price=c["price"],
                            rank=c["rank"],
                        )
                    )
        day += timedelta(days=1)
    db.commit()


def seed_reviews(db: Session, hotel: Hotel, today: date) -> None:
    samples = [
        (-13, 5, "周先生", "位置很好", "出门就是湖滨，步行到西湖很方便，房间安静，床品舒适，会再来。"),
        (-12, 4, "小林", "服务不错", "前台态度很好，办理入住很快。早餐种类一般，总体还过得去。"),
        (-10, 5, "陈女士", "商务出行方便", "出差选这里很合适，网络稳定，隔音也不错，晚上睡得很安稳。"),
        (-8, 4, "一家三口", "家庭房还能接受", "家庭房空间可以，带孩子住够用。卫生间偏小，整体干净。"),
        (-6, 3, "刘先生", "性价比一般", "位置没得说，但总感觉现在这个价格不如周边几家划算，犹豫了很久才订。"),
        (-3, 2, "王女士", "和图片差距较大", "房间临街噪音大，地毯有明显污渍，和图片差距较大，晚上几乎没睡好，不会再订。"),
        (-2, 4, "周女士", "位置很好", "风景和位置都好，只是房价比上周感觉贵了一些。"),
        (-1, 5, "赵先生", "服务还可以", "退房速度快，行李寄存也方便，整体体验不错。"),
    ]
    for offset, rating, guest, title, content in samples:
        ext = f"ctrip-demo-{offset}"
        day = today + timedelta(days=offset)
        existing = db.query(Review).filter(Review.external_id == ext).one_or_none()
        sentiment = "negative" if rating <= 2 else "neutral" if rating == 3 else "positive"
        if existing is None:
            db.add(
                Review(
                    hotel_id=hotel.id,
                    external_id=ext,
                    platform="ctrip",
                    guest_name=guest,
                    rating=rating,
                    title=title,
                    content=content,
                    review_date=day,
                    sentiment=sentiment,
                    reply_status="pending",
                )
            )
        else:
            # Do not overwrite drafts or human-review progress.
            if existing.reply_status in {"pending", "drafted"}:
                existing.review_date = day
                existing.rating = rating
                existing.content = content
                existing.title = title
                existing.guest_name = guest
                existing.sentiment = sentiment
    db.commit()


def seed_alerts(db: Session, hotel: Hotel, today: date) -> None:
    """Upsert story alerts. Preserve acknowledged status if the user already acted."""
    # Compute a few headline numbers from the just-seeded metrics.
    dk = db.query(RoomType).filter(RoomType.hotel_id == hotel.id, RoomType.code == "DK").one()
    hist = (
        db.query(DailyMetric)
        .filter(DailyMetric.hotel_id == hotel.id, DailyMetric.room_type_id == dk.id, DailyMetric.is_future.is_(False))
        .order_by(DailyMetric.date)
        .all()
    )
    early = [m for m in hist if m.date <= today - timedelta(days=8)]
    late = [m for m in hist if m.date >= today - timedelta(days=7)]
    r0 = int(sum(m.search_rank for m in early) / max(1, len(early))) if early else 9
    r1 = int(sum(m.search_rank for m in late[-3:]) / max(1, len(late[-3:]))) if late else 23
    prev7 = [m for m in hist if today - timedelta(days=13) <= m.date <= today - timedelta(days=7)]
    last7 = [m for m in hist if today - timedelta(days=6) <= m.date <= today]
    prev_p = pack_metrics(prev7)
    last_p = pack_metrics(last7)
    c0 = prev_p["conversion_rate"]
    c1 = last_p["conversion_rate"]
    conv_dropped = (c0 - c1) * 100 >= 0.6

    our_recent = sum(m.list_price for m in late) / max(1, len(late)) if late else 688
    comps = (
        db.query(CompetitorSnapshot)
        .filter(
            CompetitorSnapshot.hotel_id == hotel.id,
            CompetitorSnapshot.room_type_id == dk.id,
            CompetitorSnapshot.date >= today - timedelta(days=7),
        )
        .all()
    )
    mid = (sum(c.price for c in comps) / len(comps)) if comps else our_recent
    gap = (our_recent - mid) / mid * 100 if mid else 12

    defs = [
        {
            "stable_key": "rank_drop_dk",
            "alert_type": "rank_drop",
            "severity": "high",
            "title": f"豪华大床房搜索排名近7日{rank_from_to(r0, r1)}",
            "body": (
                "同期竞对降价后我方相对价格偏高，搜索排名后移。"
                + (
                    "点击与转化同步走弱，更像相对竞争力变化，而不是单纯的季节性波动。"
                    if conv_dropped
                    else "浏览量随排名后移下降，但按预订/浏览合计的转化率并未同步走弱。"
                )
            ),
            "metric_date": today - timedelta(days=1),
            "payload": {"room": "豪华大床房", "from": r0, "to": r1},
        },
        {
            "stable_key": "price_inversion_dk",
            "alert_type": "price_inversion",
            "severity": "high",
            "title": f"豪华大床房出现价格倒挂，挂牌价高于竞对中位约 {gap:.1f}%",
            "body": "西湖和禧酒店、湖滨商务酒店近一周持续下调售价，我方门市价基本未动，价格带已明显高于竞对中位。",
            "metric_date": today - timedelta(days=1),
            "payload": {"our_price": round(our_recent), "competitor_median": round(mid), "gap_pct": round(gap, 1)},
        },
        {
            "stable_key": "negative_review",
            "alert_type": "negative_review",
            "severity": "high",
            "title": "收到2分差评，提及临街噪音与卫生问题",
            "body": "客人反映房间临街噪音大、地毯有污渍、和图片差距较大。差评可能进一步拖累转化，建议尽快生成回复草稿并安排客房核查。",
            "metric_date": today - timedelta(days=3),
            "payload": {"rating": 2, "external_id": "ctrip-demo--3"},
        },
        {
            "stable_key": "competitor_cut",
            "alert_type": "competitor_cut",
            "severity": "high",
            "title": "主要竞对近一周下调售价 12%–18%",
            "body": "西湖和禧酒店与湖滨商务酒店为直接竞对，降价后列表位置前移；湖畔丽舍降幅较小。",
            "metric_date": today - timedelta(days=1),
            "payload": {"competitors": ["西湖和禧酒店", "湖滨商务酒店"]},
        },
    ]
    if conv_dropped:
        defs.append(
            {
                "stable_key": "conversion_drop",
                "alert_type": "conversion_drop",
                "severity": "medium",
                "title": f"转化率{from_to_phrase(f'{c0*100:.1f}', f'{c1*100:.1f}', '%')}",
                "body": "转化走弱与排名下滑、价格倒挂同一窗口出现，需与差评影响一并看待。",
                "metric_date": today - timedelta(days=1),
                "payload": {"from": round(c0 * 100, 2), "to": round(c1 * 100, 2)},
            }
        )
    else:
        stale = (
            db.query(Alert)
            .filter(Alert.hotel_id == hotel.id, Alert.stable_key == "conversion_drop")
            .one_or_none()
        )
        if stale and stale.status == "open":
            stale.status = "closed"
            stale.title = f"转化率{from_to_phrase(f'{c0*100:.1f}', f'{c1*100:.1f}', '%')}，未达到走弱预警"
            stale.body = "近 7 日转化率按预订合计除以浏览合计计算后并未下滑，已关闭此前的转化走弱预警。"
            stale.payload = {"from": round(c0 * 100, 2), "to": round(c1 * 100, 2), "closed_reason": "no_drop"}
            stale.metric_date = today - timedelta(days=1)

    for item in defs:
        row = (
            db.query(Alert)
            .filter(Alert.hotel_id == hotel.id, Alert.stable_key == item["stable_key"])
            .one_or_none()
        )
        if row is None:
            db.add(Alert(hotel_id=hotel.id, **item))
        else:
            # Update wording to stay current with rolling dates, keep status.
            row.title = item["title"]
            row.body = item["body"]
            row.severity = item["severity"]
            row.metric_date = item["metric_date"]
            row.payload = item["payload"]
            row.alert_type = item["alert_type"]
    db.commit()




HOTEL_B_TENANT = "demo_chengdu"
HOTEL_B_ADMIN = ("admin_b", "admin123", "成都店管理员", "admin")

B_ROOM_SPECS = (
    {"name": "高级大床房", "code": "ST", "inventory": 12, "capacity": 2, "base_price": 428, "min_price": 298, "max_price": 788, "rank0": 14, "occ0": 0.62, "conv0": 0.024},
    {"name": "商务双床房", "code": "TW", "inventory": 10, "capacity": 2, "base_price": 398, "min_price": 278, "max_price": 728, "rank0": 18, "occ0": 0.58, "conv0": 0.021},
)

B_COMPETITORS = ("春熙商旅酒店", "太古里精选酒店")


def seed_hotel_b(db: Session) -> Hotel:
    hotel = db.query(Hotel).filter(Hotel.tenant_key == HOTEL_B_TENANT).one_or_none()
    if hotel is None:
        hotel = Hotel(
            name="成都春熙商务酒店（演示）",
            city="成都",
            address="成都市锦江区春熙路12号",
            positioning="春熙路商务简约酒店",
            tenant_key=HOTEL_B_TENANT,
            platform="ctrip",
        )
        db.add(hotel)
        db.commit()
        db.refresh(hotel)
    rooms = {r.code: r for r in db.query(RoomType).filter(RoomType.hotel_id == hotel.id).all()}
    for spec in B_ROOM_SPECS:
        if spec["code"] in rooms:
            continue
        db.add(
            RoomType(
                hotel_id=hotel.id,
                name=spec["name"],
                code=spec["code"],
                inventory=spec["inventory"],
                capacity=spec["capacity"],
                base_price=spec["base_price"],
                min_price=spec["min_price"],
                max_price=spec["max_price"],
            )
        )
    db.commit()
    return hotel


def seed_users_b(db: Session, hotel: Hotel) -> User:
    username, password, display_name, role = HOTEL_B_ADMIN
    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None:
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            role=role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        if user.role != role:
            user.role = role
        if getattr(user, "disabled", False):
            user.disabled = False
            user.disabled_at = None
        db.commit()
        db.refresh(user)
    ensure_membership(db, user, hotel, role=role)
    db.commit()
    return user


def _metric_row_b(hotel_id, room, spec, day: date, today: date, rng: random.Random) -> dict:
    """A distinct Chengdu weekday-soft story. Not a copy of the Hangzhou price-war plot."""
    offset = (day - today).days
    is_future = offset > 0
    weekend = day.weekday() >= 5
    # Business hotel: weekends a bit busier, midweek softer. Prices stay put.
    factor = 1.10 if weekend else (1.03 if day.weekday() == 4 else 0.92)
    our_list = _price(spec["base_price"] * (1.06 if weekend else 1.0))
    occ = max(0.16, min(0.88, spec["occ0"] * factor + rng.uniform(-0.03, 0.03)))
    if is_future:
        pickup = max(0.06, 0.38 - offset * 0.025)
        occ = max(0.05, min(0.62, pickup * spec["occ0"] * factor))
    sold = max(0, min(spec["inventory"], int(round(spec["inventory"] * occ))))
    occ = sold / spec["inventory"] if spec["inventory"] else 0
    adr = _price(our_list * (0.96 + rng.uniform(-0.01, 0.01)))
    revenue = 0 if is_future else int(adr * sold)
    rank = max(6, min(28, spec["rank0"] + (0 if weekend else 2) + rng.choice([-1, 0, 0, 1])))
    impressions = int((420 if spec["code"] == "ST" else 340) * factor)
    clicks = max(6, int(impressions * 0.04))
    bookings = 0 if is_future else max(sold, 1) if sold else 0
    conv = spec["conv0"]
    if not is_future and clicks:
        bookings = max(1, min(sold if sold else 1, int(clicks * max(0.01, spec["conv0"]))))
        conv = bookings / clicks
        sold = max(sold, min(spec["inventory"], bookings))
        occ = sold / spec["inventory"]
        revenue = int(adr * sold)
    comps = []
    bases = {"春熙商旅酒店": spec["base_price"] * 0.97, "太古里精选酒店": spec["base_price"] * 1.06}
    for name in B_COMPETITORS:
        price = _price(bases[name] * (1.05 if weekend else 1.0) + rng.uniform(-8, 8))
        comps.append({"name": name, "price": price, "rank": max(5, rank + rng.randint(-3, 3))})
    return {
        "metric": dict(
            hotel_id=hotel_id,
            room_type_id=room.id,
            date=day,
            platform="ctrip",
            is_future=is_future,
            available_rooms=spec["inventory"],
            sold_rooms=sold,
            occupancy=round(occ, 4),
            list_price=our_list,
            adr=adr if not is_future else our_list,
            revenue=revenue,
            search_rank=rank,
            impressions=impressions,
            clicks=clicks,
            bookings=bookings if not is_future else max(0, sold),
            conversion_rate=round(conv, 4),
        ),
        "competitors": comps,
    }


def seed_metrics_b(db: Session, hotel: Hotel, today: date) -> None:
    rng = _rng(77)
    rooms = {r.code: r for r in db.query(RoomType).filter(RoomType.hotel_id == hotel.id).all()}
    window_start = today - timedelta(days=14)
    window_end = today + timedelta(days=13)
    db.query(DailyMetric).filter(
        DailyMetric.hotel_id == hotel.id,
        (DailyMetric.date < window_start) | (DailyMetric.date > window_end),
    ).delete(synchronize_session=False)
    db.query(CompetitorSnapshot).filter(
        CompetitorSnapshot.hotel_id == hotel.id,
        (CompetitorSnapshot.date < window_start) | (CompetitorSnapshot.date > window_end),
    ).delete(synchronize_session=False)
    db.commit()
    day = window_start
    while day <= window_end:
        for spec in B_ROOM_SPECS:
            room = rooms[spec["code"]]
            built = _metric_row_b(hotel.id, room, spec, day, today, rng)
            existing = (
                db.query(DailyMetric)
                .filter(
                    DailyMetric.hotel_id == hotel.id,
                    DailyMetric.room_type_id == room.id,
                    DailyMetric.date == day,
                    DailyMetric.platform == "ctrip",
                )
                .one_or_none()
            )
            if existing:
                for k, v in built["metric"].items():
                    if k in {"hotel_id", "room_type_id", "date", "platform"}:
                        continue
                    setattr(existing, k, v)
            else:
                db.add(DailyMetric(**built["metric"]))
            for c in built["competitors"]:
                row = (
                    db.query(CompetitorSnapshot)
                    .filter(
                        CompetitorSnapshot.hotel_id == hotel.id,
                        CompetitorSnapshot.room_type_id == room.id,
                        CompetitorSnapshot.date == day,
                        CompetitorSnapshot.competitor_name == c["name"],
                    )
                    .one_or_none()
                )
                if row:
                    row.price = c["price"]
                    row.rank = c["rank"]
                else:
                    db.add(
                        CompetitorSnapshot(
                            hotel_id=hotel.id,
                            room_type_id=room.id,
                            date=day,
                            competitor_name=c["name"],
                            price=c["price"],
                            rank=c["rank"],
                        )
                    )
        day += timedelta(days=1)
    db.commit()


def seed_reviews_b(db: Session, hotel: Hotel, today: date) -> None:
    samples = [
        (-12, 4, "何先生", "位置方便", "出门就是春熙路，走路到太古里很快，适合出差办事。"),
        (-9, 5, "小杨", "房间干净", "大床房比预想的宽敞，隔音一般但睡得还行，前台办事利落。"),
        (-5, 3, "刘女士", "停车不太方便", "酒店本身还行，地下停车场车位紧张，转了几圈才停进去。"),
        (-2, 4, "周先生", "出差够用", "商务双床房设施简单够用，早餐一般，整体符合这个价位。"),
    ]
    for offset, rating, guest, title, content in samples:
        ext = f"ctrip-demo-chengdu-{offset}"
        day = today + timedelta(days=offset)
        existing = db.query(Review).filter(Review.external_id == ext).one_or_none()
        sentiment = "negative" if rating <= 2 else "neutral" if rating == 3 else "positive"
        if existing is None:
            db.add(
                Review(
                    hotel_id=hotel.id,
                    external_id=ext,
                    platform="ctrip",
                    guest_name=guest,
                    rating=rating,
                    title=title,
                    content=content,
                    review_date=day,
                    sentiment=sentiment,
                    reply_status="pending",
                )
            )
        elif existing.reply_status in {"pending", "drafted"}:
            existing.review_date = day
            existing.rating = rating
            existing.content = content
            existing.title = title
            existing.guest_name = guest
            existing.sentiment = sentiment
            existing.hotel_id = hotel.id
    db.commit()


def seed_alerts_b(db: Session, hotel: Hotel, today: date) -> None:
    st = db.query(RoomType).filter(RoomType.hotel_id == hotel.id, RoomType.code == "ST").one()
    hist = (
        db.query(DailyMetric)
        .filter(DailyMetric.hotel_id == hotel.id, DailyMetric.room_type_id == st.id, DailyMetric.is_future.is_(False))
        .order_by(DailyMetric.date)
        .all()
    )
    last7 = [m for m in hist if today - timedelta(days=6) <= m.date <= today]
    weekdays = [m for m in last7 if m.date.weekday() < 5]
    weekends = [m for m in last7 if m.date.weekday() >= 5]
    wd_occ = sum(m.occupancy for m in weekdays) / max(1, len(weekdays)) if weekdays else 0
    we_occ = sum(m.occupancy for m in weekends) / max(1, len(weekends)) if weekends else wd_occ
    defs = [
        {
            "stable_key": "weekday_occ_soft",
            "alert_type": "occupancy_soft",
            "severity": "medium",
            "title": f"高级大床房近一周工作日出租率约 {wd_occ*100:.0f}%，弱于周末",
            "body": (
                f"近一周工作日平均出租率约 {wd_occ*100:.0f}%，周末约 {we_occ*100:.0f}%。"
                "这是春熙路商务客群的工作日偏软，不是竞对大幅降价后的倒挂。"
            ),
            "metric_date": today - timedelta(days=1),
            "payload": {"weekday_occupancy": round(wd_occ, 4), "weekend_occupancy": round(we_occ, 4), "room": "高级大床房"},
        },
        {
            "stable_key": "parking_review",
            "alert_type": "review_feedback",
            "severity": "medium",
            "title": "有客人提到地下停车场车位紧张",
            "body": "近几日有一条 3 分评价提到停车不便。评分不算差评，但停车体验可能影响商务客复购，可考虑回复说明周边停车建议。",
            "metric_date": today - timedelta(days=5),
            "payload": {"rating": 3, "external_id": "ctrip-demo-chengdu--5"},
        },
    ]
    for item in defs:
        row = (
            db.query(Alert)
            .filter(Alert.hotel_id == hotel.id, Alert.stable_key == item["stable_key"])
            .one_or_none()
        )
        if row is None:
            db.add(Alert(hotel_id=hotel.id, **item))
        else:
            row.title = item["title"]
            row.body = item["body"]
            row.severity = item["severity"]
            row.metric_date = item["metric_date"]
            row.payload = item["payload"]
            row.alert_type = item["alert_type"]
    db.commit()


def bind_hotel_a_memberships(db: Session, hotel: Hotel) -> None:
    settings = get_settings()
    wanted = [(settings.demo_username, "admin")] + [(username, role) for username, _pw, _dn, role in DEMO_ACCOUNTS]
    for username, role in wanted:
        user = db.query(User).filter(User.username == username).one_or_none()
        if user is None:
            continue
        ensure_membership(db, user, hotel, role=role)
    db.commit()


def seed_settings(db: Session) -> None:
    if db.get(SystemSetting, "overseas_enabled") is None:
        db.add(SystemSetting(key="overseas_enabled", value="false"))
        db.commit()


def seed_all(db: Session, today: date | None = None) -> Hotel:
    today = today or date.today()
    seed_users(db)
    seed_ops_user(db)
    hotel = seed_hotel(db)
    bind_hotel_a_memberships(db, hotel)
    migrate_legacy_settings(db, hotel.id)
    backfill_audit_hotel(db, hotel.id)
    seed_metrics(db, hotel, today)
    seed_reviews(db, hotel, today)
    seed_alerts(db, hotel, today)
    seed_settings(db)
    hotel_b = seed_hotel_b(db)
    seed_users_b(db, hotel_b)
    seed_metrics_b(db, hotel_b, today)
    seed_reviews_b(db, hotel_b, today)
    seed_alerts_b(db, hotel_b, today)
    ensure_demo_product_enabled(db, hotel)
    ensure_demo_product_enabled(db, hotel_b)
    return hotel


def reset_database() -> None:
    """Wipe the SQLite file / tables and reseed. Destroys review history on purpose."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    reset_engine()
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_all(db)
    finally:
        db.close()


def ensure_seeded() -> None:
    init_db()
    db = SessionLocal()
    try:
        seed_all(db)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="OIRO demo data seed")
    parser.add_argument("--reset", action="store_true", help="wipe the database and reseed")
    args = parser.parse_args()
    if args.reset:
        reset_database()
        print("database reset and reseeded")
    else:
        ensure_seeded()
        print("seed ensured (existing human-review records preserved)")


if __name__ == "__main__":
    main()
