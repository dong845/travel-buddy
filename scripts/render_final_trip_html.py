#!/usr/bin/env python3
"""Render a safe, self-contained Travel Buddy final itinerary from JSON.

Usage: python render_final_trip_html.py <plan.json|-> [<output.html|->]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from travel_workspace import find_sensitive_values


REQUIRED_STAY_SEARCH_FIELDS = {"destination", "check_in", "check_out", "guests", "rooms"}
REQUIRED_FLIGHT_SEARCH_FIELDS = {"origin", "destination", "outbound_date", "return_date", "travellers"}
REQUIRED_RENTAL_SEARCH_FIELDS = {"pickup_location", "dropoff_location", "pickup_time", "dropoff_time"}
BOOKING_ACCESS_CATEGORIES = {"flight", "accommodation", "attraction_ticket", "rental_car", "rail_or_ground"}
BOOKING_ACCESS_STATUSES = {"available", "limited", "unknown"}
PRICE_STATUSES = {"researched_current", "estimate", "user_confirmed"}
ROUTE_MAP_SCOPES = {"multi_stop", "primary_leg"}
DISALLOWED_URL_QUERY_KEYS = {
    "aff",
    "affiliate",
    "aid",
    "api_key",
    "auth",
    "authorization",
    "cart",
    "key",
    "password",
    "payment",
    "ref",
    "referral",
    "referrer",
    "secret",
    "session",
    "sid",
    "token",
}
DISALLOWED_URL_PATH_PARTS = {"account", "cart", "checkout", "login", "payment", "signin"}

FINAL_PAGE_DESIGN = r"""
:root{--ink:#132238;--muted:#637186;--paper:#f6f1e9;--card:#fffdfa;--accent:#0f766e;--accent-deep:#075c58;--accent-soft:#dff3ee;--line:#d9d9d1;--warm:#ee8d4a;--warm-soft:#fff0e3;--shadow:0 20px 54px rgb(26 42 57/.10);--radius:22px}
body{background:radial-gradient(circle at 8% -8%,#d5ede8 0 13%,transparent 32%),radial-gradient(circle at 100% 5%,#f9dfc3 0 12%,transparent 29%),var(--paper)}
main{max-width:1180px;padding:42px 22px 74px}h1,h2,h3,h4{color:var(--ink)}h1{max-width:780px;color:#fff}h2{letter-spacing:-.025em}
.hero{position:relative;isolation:isolate;overflow:hidden;padding:clamp(30px,5vw,54px);border:0;border-radius:30px;color:#e3f5f1;background:linear-gradient(135deg,#102c43 0%,#105f63 57%,#0d776e 100%);box-shadow:0 24px 60px rgb(10 41 55/.22)}
.hero::before{content:"";position:absolute;z-index:-1;width:440px;height:440px;right:-150px;top:-250px;border:1px solid rgb(255 255 255/.22);border-radius:50%;box-shadow:0 0 0 36px rgb(255 255 255/.06),0 0 0 98px rgb(255 255 255/.04)}
.hero::after{content:"TRIP";position:absolute;z-index:-1;right:28px;bottom:-22px;color:rgb(255 255 255/.09);font:800 clamp(5rem,16vw,12rem)/1 ui-serif,Georgia,serif;letter-spacing:-.1em}
.hero p{max-width:760px;color:#e3f5f1}.hero .meta{color:#c7e7e2}.hero .eyebrow{color:#b6e9df}
.panel,.day-card{border:1px solid rgb(19 34 56/.10);border-radius:var(--radius);background:rgb(255 253 250/.94);box-shadow:var(--shadow)}.panel{padding:clamp(22px,3vw,30px)}.grid{gap:16px}.fact,.option{border:1px solid #dce4e0;border-radius:16px;background:linear-gradient(145deg,#fff,#fbfaf5)}
.fact{position:relative;overflow:hidden;min-height:116px;padding:17px}.fact::after{content:"";position:absolute;width:54px;height:54px;right:-19px;top:-19px;border-radius:50%;background:var(--accent-soft)}.fact strong{position:relative;z-index:1;font-size:1.22rem}
.option{padding:18px;transition:transform .18s ease,box-shadow .18s ease}.option:hover{transform:translateY(-3px);box-shadow:0 16px 28px rgb(26 42 57/.12)}.pill{padding:5px 9px;background:var(--accent-soft);color:var(--accent-deep);letter-spacing:.04em}
.day-card{position:relative;overflow:hidden;padding:clamp(22px,3vw,30px)}.day-card::before{content:"";position:absolute;inset:0 auto 0 0;width:5px;background:linear-gradient(var(--warm),#f6c06e)}.day-top{align-items:flex-start;padding-bottom:18px;border-bottom:1px solid #e2e5e0}.day-number{min-width:54px;height:54px;border:1px solid rgb(255 255 255/.2);border-radius:16px;background:linear-gradient(135deg,#102c43,#17676b);box-shadow:0 10px 20px rgb(15 78 83/.20)}
.day-card section{padding:18px 0 0}.day-card h3{display:flex;align-items:center;gap:8px}.day-card h3::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--warm);box-shadow:0 0 0 4px var(--warm-soft)}.timeline li{padding:15px 0}.timeline time{color:var(--accent-deep)}
.route-map{border:1px solid #d8e6e3;border-radius:17px;background:linear-gradient(135deg,#edf7f5,#f7fbfa)}.route-map figcaption{color:var(--muted)}.route-segment{padding:14px 0}
.booking-link,.map-link{border-radius:12px;padding:10px 14px;background:linear-gradient(135deg,var(--accent),#138b80);box-shadow:0 8px 15px rgb(15 118 110/.18);transition:transform .18s ease,filter .18s ease}.map-link{background:linear-gradient(135deg,#17324a,#254e69);box-shadow:0 8px 15px rgb(23 50 74/.18)}.booking-link:hover,.map-link:hover{color:#fff;filter:brightness(1.04);transform:translateY(-1px)}
.warning{border:1px solid #f2cf9d;border-left:4px solid var(--warm);border-radius:14px;background:linear-gradient(100deg,#fff7ed,#fff0e2);color:#744016}details{padding:14px 0 0}summary{color:var(--accent-deep)}.source-item{margin:10px 0}
@media(max-width:600px){main{padding:22px 13px 48px}.hero{border-radius:24px;padding:30px 23px 38px}.panel,.day-card{padding:20px}.day-number{min-width:46px;height:46px}}
@media print{body{background:#fff}.hero{color:var(--ink);background:#fff;border:1px solid #d9d9d1}.hero h1{color:var(--ink)}.hero p,.hero .meta,.hero .eyebrow{color:var(--muted)}.hero::before,.hero::after{display:none}.panel,.day-card{box-shadow:none}.booking-link,.map-link{box-shadow:none}}
"""


def labels_for(language: object, custom_labels: object = None) -> dict[str, str]:
    """Return presentation labels for the supported final-plan interface languages."""
    normalized = str(language).casefold()
    if normalized.startswith("zh") or "chinese" in normalized or "中文" in normalized:
        return {
            "review_option": "查看选项",
            "round_trip": "搜索往返行程",
            "compare_booking": "在 Booking.com 比较（已带入日期和住客）",
            "compare_platform_prefix": "在 ",
            "compare_platform_suffix": " 比较（已带入日期和住客）",
            "segment_map": "在地图中打开此路段",
            "segment_map_provider": "在 {provider} 中打开此路段",
            "ticket_prefix": "查看门票：",
            "researched_itinerary": "已调研行程 · ",
            "arrival": "抵达方式：",
            "pace": "节奏：",
            "currency": "货币：",
            "last_checked": "调研最后核验：",
            "price_recheck": "价格与库存均须在购买前再次核验。",
            "budget": "预算概览",
            "total": "人均可比较预算",
            "included": "包含的假设",
            "ground": "地面交通方案",
            "browse": "浏览选项 — 尚未购买",
            "no_reservation": "仅展示当前调研选项；打开链接不会创建预订。",
            "booking_access_checks": "预订可访问性核验",
            "access_flight": "机票",
            "access_accommodation": "住宿",
            "access_attraction_ticket": "景点门票",
            "access_rental_car": "租车",
            "access_rail_or_ground": "铁路/地面交通",
            "access_available": "可用",
            "access_limited": "受限",
            "access_unknown": "待核验",
            "overall_transport": "全程交通",
            "overall_route": "打开全程路线",
            "overall_route_provider": "在 {provider} 中打开全程路线",
            "overall_route_overview": "打开交通概览（请查看每日分段）",
            "overall_route_overview_provider": "在 {provider} 中打开交通概览（请查看每日分段）",
            "sources": "来源、可信度与购买前复核清单",
            "sources_used": "使用的来源",
            "recheck_before": "购买前复核",
            "no_purchase_options": "本行程未请求购买选项。",
            "price_unverified": "价格暂未核验",
            "time_unverified": "时间暂未核验",
            "up_to": "最高",
            "hour": "小时",
            "minute": "分钟",
            "conditions_recheck": "条款须复核",
            "price_per_person": "人均往返价格：",
            "price_per_room_night": "每间每晚价格：",
            "price_trip_total": "本次住宿总价：",
            "price_ticket": "每人门票价格：",
            "price_car_day": "每车每日价格：",
            "availability": "库存状态：",
            "price_checked": "价格核验：",
            "price_status": "价格状态：",
            "price_researched_current": "已调研现价",
            "price_estimate": "估算",
            "price_user_confirmed": "用户确认",
            "outbound": "去程：",
            "return": "返程：",
            "hotel_location": "位置与通勤：",
            "hotel_fit": "适配原因：",
            "provider": "供应商：",
            "compared": "比较平台：",
            "checked": "核验时间：",
            "source": "来源：",
            "source_link": "来源",
            "to": "至",
            "travellers": "位旅行者",
            "guest": "位住客",
            "room": "间客房",
            "arranged_independently": "自行安排住宿",
            "departure_checkout": "退房 / 当晚无住宿",
            "day": "第",
            "day_suffix": "天",
            "stay": "住宿",
            "plan": "行程",
            "dining": "用餐建议",
            "dining_map": "在 {provider} 中查看餐厅",
            "dining_backup": "备选：",
            "free_time": "自由活动",
            "route": "路线与交通",
            "schematic": "示意图 — 不用于导航。按游览顺序展示站点；请使用实时地图获取路线。",
            "full_day_route": "打开当日完整路线",
            "full_day_route_provider": "在 {provider} 中打开当日完整路线",
            "full_day_route_overview": "打开路线概览（请查看下方分段）",
            "full_day_route_overview_provider": "在 {provider} 中打开路线概览（请查看下方分段）",
            "alternative_map_provider": "在 {provider} 中打开备选路线",
            "route_by_segment": "分段路线",
            "operating_recheck": "出发前请再次核验运营情况。",
            "tickets": "门票与复核",
            "no_ticket": "列出的活动没有需要单独购买或已核验的门票。",
            "contingency": "请为突发情况预留灵活备选方案。",
            "plan_evidence": "行程依据",
            "destination_essentials": "目的地体验重点",
            "budget_breakdown": "人均预算明细",
            "default_recheck": "购买前请再次核验价格、可订性、入境要求和运营情况。",
        }
    if isinstance(custom_labels, dict) and all(
        isinstance(value, str) and (value.strip() or key == "day_suffix")
        for key, value in custom_labels.items()
    ) and REQUIRED_UI_LABEL_KEYS.issubset(custom_labels):
        return {str(key): html.escape(value, quote=True) for key, value in custom_labels.items()}
    return {}


REQUIRED_UI_LABEL_KEYS = frozenset(labels_for("zh-CN"))


def has_builtin_interface_language(language: object) -> bool:
    normalized = str(language).casefold()
    return normalized.startswith(("zh", "en")) or "chinese" in normalized or "english" in normalized or "中文" in normalized


def localize_static_page(page: str, language: object, custom_labels: object = None) -> str:
    """Localize standard renderer copy; user content remains escaped throughout rendering."""
    labels = labels_for(language, custom_labels)
    if not labels:
        return page

    replacements = {
        ">Review option<": f">{labels['review_option']}<",
        ">Open this segment in maps<": f">{labels['segment_map']}<",
        ">Budget at a glance<": f">{labels['budget']}<",
        ">Comparable cost per person<": f">{labels['total']}<",
        ">Included assumptions<": f">{labels['included']}<",
        ">Ground-mobility plan<": f">{labels['ground']}<",
        ">Browse options — no purchase made<": f">{labels['browse']}<",
        ">Booking access checks<": f">{labels['booking_access_checks']}<",
        ">Source<": f">{labels['source_link']}<",
        ">Overall transport<": f">{labels['overall_transport']}<",
        ">Open overall route<": f">{labels['overall_route']}<",
        ">Open transport overview — see daily segments<": f">{labels['overall_route_overview']}<",
        ">Sources, confidence, and recheck list<": f">{labels['sources']}<",
        ">Sources used<": f">{labels['sources_used']}<",
        ">Recheck before purchase<": f">{labels['recheck_before']}<",
        ">No purchase options were requested for this plan.<": f">{labels['no_purchase_options']}<",
        '<span class="pill">flight<': '<span class="pill">机票<',
        '<span class="pill">hotel<': '<span class="pill">酒店<',
        '<span class="pill">ticket<': '<span class="pill">门票<',
        '<span class="pill">car<': '<span class="pill">租车<',
        'aria-label="Schematic route in visit order"': 'aria-label="按游览顺序的路线示意图"',
        '>Start</text>': '>起点</text>',
        '>End</text>': '>终点</text>',
        "Price not currently verified": labels["price_unverified"],
        "Time not currently verified": labels["time_unverified"],
        "Up to ": labels["up_to"] + " ",
        "Conditions require recheck": labels["conditions_recheck"],
        "Price per person, round trip: ": labels["price_per_person"],
        "Price per room/night: ": labels["price_per_room_night"],
        "Trip total for stay: ": labels["price_trip_total"],
        "Ticket price per person: ": labels["price_ticket"],
        "Vehicle price per day: ": labels["price_car_day"],
        "Availability: ": labels["availability"],
        "Price checked: ": labels["price_checked"],
        "Price status: ": labels["price_status"],
        "Outbound: ": labels["outbound"],
        "Return: ": labels["return"],
        "Location and access: ": labels["hotel_location"],
        "Why it fits: ": labels["hotel_fit"],
        "Destination essentials": labels["destination_essentials"],
        "Budget breakdown": labels["budget_breakdown"],
        "Backup: ": labels["dining_backup"],
        "Arranged independently": labels["arranged_independently"],
        "Checkout / no overnight stay": labels["departure_checkout"],
        "Schematic — not for navigation. Stops are shown in visit order; use the live map for directions.": labels["schematic"],
        "Recheck operating conditions before departure.": labels["operating_recheck"],
        "No verified ticket is required for the listed activities.": labels["no_ticket"],
        "Keep a flexible alternative for disruptions.": labels["contingency"],
        "Plan evidence": labels["plan_evidence"],
        "Recheck price, availability, entry requirements, and operating conditions before purchase.": labels["default_recheck"],
        "Prices and availability require recheck before purchase.": labels["price_recheck"],
        "Current researched options only. Opening a link never creates a reservation.": labels["no_reservation"],
        "Researched itinerary · ": labels["researched_itinerary"],
        "Arrival: ": labels["arrival"],
        " · Pace: ": f" · {labels['pace']}",
        " · Currency: ": f" · {labels['currency']}",
        " · Research last checked: ": f" · {labels['last_checked']}",
        "Provider: ": labels["provider"],
        " · Compared via: ": f" · {labels['compared']}",
        " · Checked: ": f" · {labels['checked']}",
        " · Source: ": f" · {labels['source']}",
        " guest(s) · ": f" {labels['guest']} · ",
        " room(s)": f" {labels['room']}",
    }
    for source, target in replacements.items():
        page = page.replace(source, target)

    page = re.sub(
        r'(data-booking-purpose="round-trip-search"[^>]*>)Search round trip — ([^<]+)(</a>)',
        lambda match: f"{match.group(1)}{labels['round_trip']} — {match.group(2)}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(data-booking-purpose="comparison-search"[^>]*>)Compare on Booking\.com — dates and guests included(</a>)',
        lambda match: f"{match.group(1)}{labels['compare_booking']}{match.group(2)}",
        page,
    )
    page = re.sub(
        r'(data-booking-purpose="comparison-search"[^>]*>)Compare on ([^<]+) — dates and guests included(</a>)',
        lambda match: f"{match.group(1)}{labels['compare_platform_prefix']}{match.group(2)}{labels['compare_platform_suffix']}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(data-booking-type="ticket"[^>]*>)Review ticket: ([^<]+)(</a>)',
        lambda match: f"{match.group(1)}{labels['ticket_prefix']}{match.group(2)}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(\d+)h (\d+)m',
        lambda match: f"{match.group(1)}{labels['hour']}{match.group(2)}{labels['minute']}",
        page,
    )
    page = re.sub(
        r'(\d+) min',
        lambda match: f"{match.group(1)}{labels['minute']}",
        page,
    )
    page = re.sub(
        r'<p class="eyebrow">Day ([^<]+) · ([^<]+)</p>',
        lambda match: f'<p class="eyebrow">{labels["day"]}{match.group(1)}{labels["day_suffix"]} · {match.group(2)}</p>',
        page,
    )
    page = re.sub(
        r'aria-label="Day (\d+)"',
        lambda match: f'aria-label="{labels["day"]}{match.group(1)}{labels["day_suffix"]}"',
        page,
    )
    page = re.sub(
        r'(<header id="trip-summary" class="hero">.*?</h1><p>)([^<]+) → ([^<]+) · (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2}) · ([^<]+) traveller\(s\)(</p>)',
        lambda match: f'{match.group(1)}{match.group(2)} → {match.group(3)} · {match.group(4)}{labels["to"]}{match.group(5)} · {match.group(6)}{labels["travellers"]}{match.group(7)}',
        page,
        flags=re.DOTALL,
    )
    page = page.replace('<h3>Stay</h3>', f'<h3>{labels["stay"]}</h3>')
    page = page.replace('<h3>Plan</h3>', f'<h3>{labels["plan"]}</h3>')
    page = page.replace('<h3>Dining suggestions</h3>', f'<h3>{labels["dining"]}</h3>')
    page = page.replace('<h3>Route and mobility</h3>', f'<h3>{labels["route"]}</h3>')
    page = page.replace('>Open full-day route<', f'>{labels["full_day_route"]}<')
    page = page.replace('>Open route overview — see segments below<', f'>{labels["full_day_route_overview"]}<')
    page = re.sub(
        r'>Open full-day route in ([^<]+)<',
        lambda match: ">" + labels["full_day_route_provider"].replace("{provider}", match.group(1)) + "<",
        page,
    )
    page = re.sub(
        r'>Open route overview in ([^<]+) — see segments below<',
        lambda match: ">" + labels["full_day_route_overview_provider"].replace("{provider}", match.group(1)) + "<",
        page,
    )
    page = re.sub(
        r'>Open this segment in ([^<]+)<',
        lambda match: ">" + labels["segment_map_provider"].replace("{provider}", match.group(1)) + "<",
        page,
    )
    page = re.sub(
        r'>Open overall route in ([^<]+)<',
        lambda match: ">" + labels["overall_route_provider"].replace("{provider}", match.group(1)) + "<",
        page,
    )
    page = re.sub(
        r'>Open transport overview in ([^<]+) — see daily segments<',
        lambda match: ">" + labels["overall_route_overview_provider"].replace("{provider}", match.group(1)) + "<",
        page,
    )
    page = re.sub(
        r'>Open alternative route in ([^<]+)<',
        lambda match: ">" + labels["alternative_map_provider"].replace("{provider}", match.group(1)) + "<",
        page,
    )
    page = re.sub(
        r'>View restaurant in ([^<]+)<',
        lambda match: ">" + labels["dining_map"].replace("{provider}", match.group(1)) + "<",
        page,
    )
    access_categories = {
        "flight": labels["access_flight"],
        "accommodation": labels["access_accommodation"],
        "attraction_ticket": labels["access_attraction_ticket"],
        "rental_car": labels["access_rental_car"],
        "rail_or_ground": labels["access_rail_or_ground"],
    }
    access_statuses = {
        "available": labels["access_available"],
        "limited": labels["access_limited"],
        "unknown": labels["access_unknown"],
    }
    page = re.sub(
        r'(<strong>)(flight|accommodation|attraction_ticket|rental_car|rail_or_ground) · (available|limited|unknown)(</strong>)',
        lambda match: f"{match.group(1)}{access_categories[match.group(2)]} · {access_statuses[match.group(3)]}{match.group(4)}",
        page,
    )
    page = page.replace('<h4>Route by segment</h4>', f'<h4>{labels["route_by_segment"]}</h4>')
    page = page.replace('<h3>Tickets and recheck</h3>', f'<h3>{labels["tickets"]}</h3>')
    page = page.replace('<time>Flexible</time><div><strong>Free time</strong>', f'<time>灵活安排</time><div><strong>{labels["free_time"]}</strong>')
    # Price provenance is decision-critical: do not leave machine enum values in an
    # otherwise localized checkout-facing page. These replacements only affect
    # renderer-owned visible text, never URLs or data attributes.
    for price_status in PRICE_STATUSES:
        status_label = labels.get(f"price_{price_status}", price_status)
        page = page.replace(f"</strong>{price_status}", f"</strong>{status_label}")
        page = page.replace(f">{price_status} ·", f">{status_label} ·")
    return page


def as_text(value: object, fallback: str = "Not supplied") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def esc(value: object, fallback: str = "Not supplied") -> str:
    return html.escape(as_text(value, fallback), quote=True)


def attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def is_https(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        return False
    path_parts = {part.casefold() for part in parsed.path.split("/") if part}
    if DISALLOWED_URL_PATH_PARTS & path_parts:
        return False
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.casefold()
        if normalized in DISALLOWED_URL_QUERY_KEYS or normalized.startswith(("utm_", "ref_", "session_")):
            return False
    return True


def is_google_map(provider: object, url: object) -> bool:
    provider_text = str(provider or "").casefold()
    host = urlparse(str(url or "")).hostname or ""
    normalized_host = host.casefold()
    return "google" in provider_text or normalized_host.endswith(("google.com", ".goo.gl")) or ".google." in normalized_host


def is_amap(provider: object, url: object) -> bool:
    provider_text = str(provider or "").casefold()
    host = urlparse(str(url or "")).hostname or ""
    return "amap" in provider_text or "高德" in str(provider or "") or host.casefold().endswith("amap.com")


def is_amap_directions_url(url: object) -> bool:
    """Validate the public Amap URI route format, not a destination/POI page.

    Amap's public URI documentation requires `https://uri.amap.com/navigation`
    plus `from`, `to`, and a transport `mode`.  Requiring those fields prevents
    a visually plausible `ditu.amap.com/place/...` link from being presented as
    a route that has actually been planned.
    """
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() != "uri.amap.com" or parsed.path.rstrip("/") != "/navigation":
        return False
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return bool(query.get("from") and query.get("to") and query.get("mode"))


def is_directions_url(provider: object, url: object) -> bool:
    """Return whether the provider URL is suitable for a live directions button.

    Provider-specific URI syntaxes differ.  Mainland-China Amap links are
    checked strictly because its documented public URI format is known; other
    verified regional providers are checked by the research workflow and by the
    explicit `map_link_kind` contract instead of guessing undocumented URL
    syntax.
    """
    if not is_https(url):
        return False
    if is_amap(provider, url):
        return is_amap_directions_url(url)
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if is_google_map(provider, url):
        return bool(("/maps/dir" in path and (query.get("origin") and query.get("destination"))) or (query.get("api") == "1" and query.get("origin") and query.get("destination")))
    if host == "maps.apple.com":
        return bool(query.get("saddr") and query.get("daddr"))
    if host.endswith("openstreetmap.org"):
        return bool(query.get("route"))
    place_parts = {part.casefold() for part in parsed.path.split("/") if part}
    return not bool(place_parts & {"place", "poi", "detail", "details", "location", "search"})


def is_ambiguous_route_mode(value: object) -> bool:
    """Detect a choice-list masquerading as one researched primary route."""
    text = str(value or "").casefold()
    return bool(re.search(r"\b(?:or|either|choose|tbd)\b|(?:或|择一|待核验|待确认|/|／)", text))


def map_link_allowed(provider: object, url: object, regional_context: dict) -> bool:
    market = str(regional_context.get("destination_service_market") or "").casefold()
    google_access = regional_context.get("google_services_access")
    return not ((market == "mainland_china" or google_access == "unavailable") and is_google_map(provider, url))


def validate_alternative_map_links(value: object, label: str, regional_context: dict, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        errors.append(f"{label}.alternative_map_links must be a list when present.")
        return
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict) or not all(item.get(key) for key in ("provider", "url", "checked_at", "map_link_kind")):
            errors.append(f"{label}.alternative_map_links[{number}] needs provider, URL, check time, and map_link_kind.")
            continue
        if item["map_link_kind"] != "directions":
            errors.append(f"{label}.alternative_map_links[{number}].map_link_kind must be directions.")
        if not is_https(item["url"]):
            errors.append(f"{label}.alternative_map_links[{number}].url must be HTTPS.")
        elif not is_directions_url(item["provider"], item["url"]):
            errors.append(f"{label}.alternative_map_links[{number}] must be an actual directions URL, not a place page.")
        if not map_link_allowed(item["provider"], item["url"], regional_context):
            errors.append(f"{label}.alternative_map_links[{number}] uses Google Maps despite the regional-access rule.")


def validate_booking_access_checks(value: object, errors: list[str]) -> set[str]:
    if not isinstance(value, list) or not value:
        errors.append("regional_service_context.booking_access_checks must be a non-empty list.")
        return set()
    sensitive_values = find_sensitive_values(value, "regional_service_context.booking_access_checks")
    if sensitive_values:
        errors.append("booking_access_checks appears to contain prohibited sensitive values at: " + ", ".join(sensitive_values) + ".")
    categories: set[str] = set()
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict) or not all(
            item.get(key) for key in ("category", "access_status", "provider_or_channel", "requirements_note", "source_url", "checked_at")
        ):
            errors.append(f"booking_access_checks[{number}] needs category, access_status, provider_or_channel, requirements_note, source_url, and checked_at.")
            continue
        category = item["category"]
        if category not in BOOKING_ACCESS_CATEGORIES:
            errors.append(f"booking_access_checks[{number}].category is invalid.")
        else:
            if category in categories:
                errors.append(f"booking_access_checks has duplicate category: {category}.")
            categories.add(category)
        if item["access_status"] not in BOOKING_ACCESS_STATUSES:
            errors.append(f"booking_access_checks[{number}].access_status must be available, limited, or unknown.")
        if not is_https(item["source_url"]):
            errors.append(f"booking_access_checks[{number}].source_url must be a safe HTTPS browse URL.")
        if not is_iso_datestamp(item["checked_at"]):
            errors.append(f"booking_access_checks[{number}].checked_at must be an ISO date or date-time.")
    return categories


def parse_iso_date(value: object, label: str, errors: list[str]) -> date | None:
    if not isinstance(value, str):
        errors.append(f"{label} must be an ISO date (YYYY-MM-DD).")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.append(f"{label} must be an ISO date (YYYY-MM-DD).")
        return None


def is_iso_datestamp(value: object) -> bool:
    """Accept an ISO calendar date or an ISO date-time for source freshness fields."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def is_nonnegative_money_range(low: object, high: object) -> bool:
    """Keep displayed costs comparable and prevent inverted or text-only ranges."""
    numeric = (int, float)
    if any(not isinstance(value, numeric) or isinstance(value, bool) for value in (low, high)):
        return False
    return low >= 0 and high >= low


def is_nonnegative_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def money(low: object, high: object, currency: object) -> str:
    unit = as_text(currency, "")
    if low is None and high is None:
        return "Price not currently verified"
    if low == high or high is None:
        return f"{unit} {as_text(low)}".strip()
    if low is None:
        return f"Up to {unit} {as_text(high)}".strip()
    return f"{unit} {as_text(low)}–{as_text(high)}".strip()


def minutes(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "Time not currently verified"
    hours, mins = divmod(round(value), 60)
    return f"{hours}h {mins}m" if hours else f"{mins} min"


def read_json(path: str) -> dict:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("The plan must be a JSON object.")
    return data


def booking_title(kind: str, item: dict) -> str:
    if kind == "flight":
        return f"{as_text(item.get('provider'))}: {as_text(item.get('origin_airport'))} → {as_text(item.get('destination_airport'))}"
    if kind == "hotel":
        return f"{as_text(item.get('property_name'))} · {as_text(item.get('stay_location'))}"
    if kind == "ticket":
        return as_text(item.get("attraction_name"))
    return f"{as_text(item.get('provider'))} · {as_text(item.get('vehicle_class'))}"


def booking_details(kind: str, item: dict) -> str:
    if kind == "flight":
        return " · ".join(
            part
            for part in (
                as_text(item.get("outbound_date"), ""),
                as_text(item.get("return_date"), ""),
                as_text(item.get("cabin"), ""),
                as_text(item.get("baggage_assumption"), ""),
                as_text(item.get("connection_summary"), ""),
            )
            if part
        )
    if kind == "hotel":
        return " · ".join(
            part
            for part in (
                " → ".join(part for part in (as_text(item.get("check_in"), ""), as_text(item.get("check_out"), "")) if part),
                f"{as_text(item.get('guest_count'), '')} guest(s) · {as_text(item.get('room_count'), '')} room(s)" if item.get("guest_count") and item.get("room_count") else "",
                as_text(item.get("room_basis"), ""),
                as_text(item.get("taxes_and_fees_status"), ""),
                as_text(item.get("cancellation_terms"), ""),
                as_text(item.get("accessibility_or_location_note"), ""),
            )
            if part
        )
    if kind == "ticket":
        return " · ".join(
            part
            for part in (
                as_text(item.get("timed_entry_or_reservation"), ""),
                as_text(item.get("ticket_status"), ""),
            )
            if part
        )
    return " · ".join(
        part
        for part in (
            as_text(item.get("pickup_location"), ""),
            as_text(item.get("dropoff_location"), ""),
            as_text(item.get("transmission"), ""),
            as_text(item.get("fuel_policy"), ""),
            as_text(item.get("mileage_policy"), ""),
        )
        if part
    )


def flight_leg_summary(leg: object) -> str:
    if not isinstance(leg, dict):
        return ""
    return " · ".join(
        part
        for part in (
            as_text(leg.get("service_identifier"), ""),
            " → ".join(part for part in (as_text(leg.get("departure_local"), ""), as_text(leg.get("arrival_local"), "")) if part),
            minutes(leg.get("duration_minutes")),
            f"{as_text(leg.get('stops'))} stop(s)" if leg.get("stops") is not None else "",
            as_text(leg.get("connection_or_terminal_note"), ""),
        )
        if part
    )


def option_detail_list(kind: str, item: dict) -> str:
    rows: list[str] = []
    if kind == "flight":
        rows.extend(
            (
                f'<li><strong>Outbound: </strong>{esc(flight_leg_summary(item.get("outbound_itinerary")), "Not supplied")}</li>',
                f'<li><strong>Return: </strong>{esc(flight_leg_summary(item.get("return_itinerary")), "Not supplied")}</li>',
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{esc(item.get("price_checked_at"))}</li>',
                f'<li>{esc(item.get("airport_transfer_note"))}</li>',
            )
        )
    elif kind == "hotel":
        rows.extend(
            (
                f'<li><strong>Location and access: </strong>{esc(item.get("neighborhood"))} · {esc(item.get("address_or_location_reference"))} · {esc(item.get("arrival_access_note"))} · {esc(item.get("key_area_access_note"))}</li>',
                f'<li><strong>Why it fits: </strong>{esc(item.get("selection_rationale"))}</li>',
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{esc(item.get("price_checked_at"))}</li>',
            )
        )
    elif kind == "ticket":
        rows.extend(
            (
                f'<li>{esc(item.get("timed_entry_or_reservation"))}</li>',
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{esc(item.get("price_checked_at"))}</li>',
            )
        )
    elif kind == "car":
        rows.extend(
            (
                f'<li>{esc(item.get("pickup_location"))} → {esc(item.get("dropoff_location"))} · {esc(item.get("pickup_time"))} → {esc(item.get("dropoff_time"))}</li>',
                f'<li>{esc(item.get("transmission"))} · {esc(item.get("capacity_note"))} · {esc(item.get("insurance_excess"))}</li>',
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{esc(item.get("price_checked_at"))}</li>',
            )
        )
    return f'<ul class="option-details">{"".join(rows)}</ul>' if rows else ""


def budget_breakdown_cards(value: object, currency: object) -> str:
    if not isinstance(value, list):
        return ""
    cards = []
    for item in value:
        if not isinstance(item, dict):
            continue
        cards.append(
            f'<article class="fact budget-item" data-budget-category="{attr(item.get("category"))}">'
            f'<strong>{esc(item.get("category"))}: {esc(money(item.get("per_person_low"), item.get("per_person_high"), item.get("currency") or currency))}</strong>'
            f'<span>{esc(item.get("description"))}</span>'
            f'<p class="meta">{esc(item.get("price_status"))} · {esc(item.get("checked_at"))} · {esc(item.get("note"))}</p></article>'
        )
    return f'<section id="budget-breakdown" class="panel"><h2>Budget breakdown</h2><div class="grid">{"".join(cards)}</div></section>' if cards else ""


def booking_link(kind: str, provider: object, checked_at: object, url: object, label: str, purpose: str | None = None, prefilled_fields: list[object] | None = None) -> str:
    purpose_attr = f' data-booking-purpose="{attr(purpose)}"' if purpose else ""
    prefilled_attr = f' data-prefilled-fields="{attr(",".join(str(field) for field in prefilled_fields))}"' if prefilled_fields else ""
    return f'<a class="booking-link" data-booking-type="{attr(kind)}" data-provider="{attr(provider)}" data-verified-at="{attr(checked_at)}"{purpose_attr}{prefilled_attr} href="{attr(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def hotel_comparison_links(item: dict) -> str:
    links = []
    for search in item.get("comparison_searches", []):
        if not isinstance(search, dict):
            continue
        platform = as_text(search.get("platform"), "Comparison platform")
        label = "Compare on Booking.com — dates and guests included" if platform.casefold() in {"booking", "booking.com"} else f"Compare on {platform} — dates and guests included"
        links.append(booking_link("hotel", platform, search.get("checked_at"), search.get("search_url"), label, "comparison-search", search.get("prefilled_fields") if isinstance(search.get("prefilled_fields"), list) else None))
    return "".join(links)


def option_card(kind: str, item: dict) -> str:
    provider = as_text(item.get("provider"), item.get("official_or_authorised_provider") or "Provider")
    checked_at = as_text(item.get("checked_at"))
    comparison_platform = as_text(item.get("comparison_platform"), "Direct provider")
    url = item.get("review_url")
    price = money(item.get("fare_low", item.get("nightly_cost_low", item.get("price_low"))), item.get("fare_high", item.get("nightly_cost_high", item.get("price_high"))), item.get("fare_currency", item.get("currency")))
    price_label = (
        "Price per person, round trip: " if kind == "flight" else
        "Price per room/night: " if kind == "hotel" else
        "Ticket price per person: " if kind == "ticket" else
        "Vehicle price per day: " if kind == "car" else ""
    )
    stay_total = ""
    if kind == "hotel":
        stay_total = f'<p class="meta"><strong>Trip total for stay: </strong>{esc(money(item.get("trip_cost_low"), item.get("trip_cost_high"), item.get("currency")))}</p>'
    actions = [booking_link(kind, provider, checked_at, url, "Review option")]
    direct_url = item.get("direct_review_url")
    if direct_url and direct_url != url:
        actions.append(booking_link(kind, item.get("direct_provider") or "Direct provider", item.get("comparison_checked_at") or checked_at, direct_url, "Review direct provider", "direct-provider"))
    if kind == "flight":
        actions.insert(0, booking_link("flight", item.get("round_trip_search_provider") or comparison_platform or provider, item.get("round_trip_search_checked_at") or checked_at, item.get("round_trip_search_url"), f"Search round trip — {as_text(item.get('outbound_date'))} to {as_text(item.get('return_date'))}", "round-trip-search", item.get("round_trip_prefilled_fields") if isinstance(item.get("round_trip_prefilled_fields"), list) else None))
    if kind == "hotel":
        actions.extend(hotel_comparison_links(item))
    if kind == "car":
        actions[0] = booking_link("car", provider, checked_at, url, "Review option", "rental-search", item.get("rental_search_prefilled_fields") if isinstance(item.get("rental_search_prefilled_fields"), list) else None)
    return f'''<article class="option"><span class="pill">{attr(kind)}</span><h3>{esc(booking_title(kind, item))}</h3><p><strong>{price_label}</strong>{esc(price)}</p>{stay_total}<p>{esc(booking_details(kind, item), "Conditions require recheck")}</p>{option_detail_list(kind, item)}<p class="meta">Provider: {esc(provider)} · Compared via: {esc(comparison_platform)} · Checked: {esc(checked_at)} · Source: {esc(item.get("source_type"))}</p>{"".join(actions)}</article>'''


def booking_access_details(value: object) -> str:
    if not isinstance(value, list) or not value:
        return ""
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rows.append(
            f'<li class="booking-access-item" data-booking-access-category="{attr(item.get("category"))}" '
            f'data-booking-access-status="{attr(item.get("access_status"))}" '
            f'data-accessed-at="{attr(item.get("checked_at"))}" data-source-url="{attr(item.get("source_url"))}">'
            f'<strong>{esc(item.get("category"))} · {esc(item.get("access_status"))}</strong> — '
            f'{esc(item.get("provider_or_channel"))}: {esc(item.get("requirements_note"))} '
            f'<a class="booking-access-source-link" href="{attr(item.get("source_url"))}" target="_blank" rel="noopener noreferrer">Source</a></li>'
        )
    return f'<details class="booking-access" open><summary>Booking access checks</summary><ul>{"".join(rows)}</ul></details>'


def route_svg(stops: list[object]) -> str:
    labels = [as_text(stop) for stop in stops if stop not in (None, "")]
    if len(labels) < 2:
        labels = ["Start", "End"]
    width, height = 720, 126
    step = (width - 80) / (len(labels) - 1)
    points = " ".join(f"{40 + step * i:.0f},56" for i in range(len(labels)))
    nodes = []
    for index, label in enumerate(labels):
        x = 40 + step * index
        nodes.append(f'<circle cx="{x:.0f}" cy="56" r="8" fill="#0b6e69"/><text x="{x:.0f}" y="94" text-anchor="middle" font-size="12" fill="#162235">{html.escape(label)}</text>')
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Schematic route in visit order"><polyline points="{points}" fill="none" stroke="#0b6e69" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>{"".join(nodes)}</svg>'


def map_link(
    provider: object,
    checked_at: object,
    url: object,
    label: str,
    *,
    segment_number: int | None = None,
    alternative: bool = False,
    link_kind: str = "directions",
    map_scope: str | None = None,
) -> str:
    classes = "map-link map-link-alternative" if alternative else "map-link"
    if segment_number is not None and not alternative:
        classes += " segment-map-link"
    segment_attr = f' data-route-segment="{segment_number}"' if segment_number is not None else ""
    scope_attr = f' data-map-scope="{attr(map_scope)}"' if map_scope else ""
    role = "alternative" if alternative else "primary"
    return f'<a class="{classes}" data-map-provider="{attr(provider)}" data-map-role="{role}" data-map-kind="{attr(link_kind)}"{scope_attr}{segment_attr} data-verified-at="{attr(checked_at)}" href="{attr(url)}" target="_blank" rel="noopener noreferrer">{esc(label)}</a>'


def alternative_map_links(value: object) -> str:
    if not isinstance(value, list):
        return ""
    links = []
    for item in value:
        if not isinstance(item, dict):
            continue
        provider = as_text(item.get("provider"), "Map provider")
        links.append(map_link(provider, item.get("checked_at"), item.get("url"), f"Open alternative route in {provider}", alternative=True, link_kind=as_text(item.get("map_link_kind"), "directions")))
    return "".join(links)


def decorate_primary_map_links(page: str, plan: dict) -> str:
    """Replace the two legacy primary-map anchors with provider-labelled, attributed links."""
    regional = plan.get("regional_service_context") if isinstance(plan.get("regional_service_context"), dict) else {}
    page = page.replace(
        '<main id="trip-plan" data-trip-plan>',
        f'<main id="trip-plan" data-trip-plan data-service-market="{attr(regional.get("destination_service_market"))}" data-google-services-access="{attr(regional.get("google_services_access"))}" data-primary-map-provider="{attr(regional.get("primary_map_provider"))}" data-primary-map-exception="{attr(regional.get("primary_map_exception_reason") or "")}" data-transport-mode="{attr((plan.get("transport_preference") or {}).get("mode"))}">',
        1,
    )
    booking_access = booking_access_details(regional.get("booking_access_checks"))
    booking_intro = '<p class="meta">Current researched options only. Opening a link never creates a reservation.</p>'
    page = page.replace(booking_intro, booking_intro + booking_access, 1)
    for day in plan.get("days", []):
        if not isinstance(day, dict) or not isinstance(day.get("route"), dict):
            continue
        route = day["route"]
        route_scope = route.get("route_map_scope")
        base_label = "Open full-day route" if route_scope == "multi_stop" else "Open route overview — see segments below"
        old = f'<a class="map-link" data-map-scope="{attr(route_scope)}" data-verified-at="{attr(route.get("map_checked_at"))}" href="{attr(route.get("verified_map_url"))}" target="_blank" rel="noopener noreferrer">{base_label}</a>'
        provider = as_text(route.get("map_provider"), "Map provider")
        provider_label = f"Open full-day route in {provider}" if route_scope == "multi_stop" else f"Open route overview in {provider} — see segments below"
        new = map_link(provider, route.get("map_checked_at"), route.get("verified_map_url"), provider_label, link_kind=as_text(route.get("map_link_kind"), "directions"), map_scope=as_text(route_scope)) + alternative_map_links(route.get("alternative_map_links"))
        page = page.replace(old, new, 1)
    overview = plan.get("transport_overview") if isinstance(plan.get("transport_overview"), dict) else {}
    overview_scope = overview.get("overall_map_scope")
    overview_label = "Open overall route" if overview_scope == "multi_stop" else "Open transport overview — see daily segments"
    old_overview = f'<a class="map-link" data-map-scope="{attr(overview_scope)}" data-verified-at="{attr(overview.get("overall_map_checked_at"))}" href="{attr(overview.get("overall_route_map_url"))}" target="_blank" rel="noopener noreferrer">{overview_label}</a>'
    provider = as_text(overview.get("overall_map_provider"), "Map provider")
    overview_provider_label = f"Open overall route in {provider}" if overview_scope == "multi_stop" else f"Open transport overview in {provider} — see daily segments"
    new_overview = map_link(provider, overview.get("overall_map_checked_at"), overview.get("overall_route_map_url"), overview_provider_label, link_kind=as_text(overview.get("map_link_kind"), "directions"), map_scope=as_text(overview_scope)) + alternative_map_links(overview.get("overall_alternative_map_links"))
    return page.replace(old_overview, new_overview, 1)


def route_segment_links(route: dict, currency: object) -> str:
    rows = []
    for index, segment in enumerate(route.get("segments", []), 1):
        if not isinstance(segment, dict):
            continue
        details = " · ".join(
            part
            for part in (
                as_text(segment.get("mode"), ""),
                as_text(segment.get("service_or_line"), ""),
                minutes(segment.get("duration_minutes")),
                f"{as_text(segment.get('distance_km'))} km" if segment.get("distance_km") is not None else "",
                f"Walk {as_text(segment.get('walking_minutes'))} min" if segment.get("walking_minutes") is not None else "",
                f"{as_text(segment.get('transfer_count'))} transfer(s)" if segment.get("transfer_count") is not None else "",
                money(segment.get("cost_low"), segment.get("cost_high"), segment.get("currency", currency)),
                as_text(segment.get("journey_instruction"), ""),
                as_text(segment.get("arrival_instruction"), ""),
                as_text(segment.get("fallback_note"), ""),
            )
            if part
        )
        primary = map_link(segment.get("map_provider"), segment.get("map_checked_at"), segment.get("verified_map_url"), f"Open this segment in {as_text(segment.get('map_provider'))}", segment_number=index, link_kind=as_text(segment.get("map_link_kind"), "directions"))
        rows.append(f'''<li class="route-segment" data-route-segment="{index}"><div><strong>{esc(segment.get("from"))} → {esc(segment.get("to"))}</strong><p class="meta">{esc(details)}</p></div>{primary}{alternative_map_links(segment.get("alternative_map_links"))}</li>''')
    return f'<ol class="segment-list">{"".join(rows)}</ol>'


def dining_cards(value: object) -> str:
    if not isinstance(value, list) or not value:
        return '<p class="meta">No meal recommendation was researched.</p>'
    cards = []
    for item in value:
        if not isinstance(item, dict):
            continue
        provider = as_text(item.get("map_provider"), "Map provider")
        backup = ""
        if item.get("backup_venue_name"):
            backup = f'<p class="meta">Backup: {esc(item.get("backup_venue_name"))} — {esc(item.get("backup_note"), "Check current opening hours before switching.")}</p>'
        reservation = ""
        if item.get("reservation_url"):
            reservation = f'<a class="dining-reservation-link" data-dining-provider="{attr(item.get("reservation_provider") or provider)}" data-verified-at="{attr(item.get("checked_at"))}" href="{attr(item.get("reservation_url"))}" target="_blank" rel="noopener noreferrer">Review reservation</a>'
        cards.append(
            f'<article class="dining-stop" data-meal="{attr(item.get("meal"))}">'
            f'<p class="eyebrow">{esc(item.get("meal"))} · {esc(item.get("time_window"))}</p>'
            f'<h4>{esc(item.get("venue_name"))}</h4>'
            f'<p>{esc(item.get("cuisine_or_style"))} · {esc(item.get("neighborhood"))}</p>'
            f'<p>{esc(item.get("why_this_stop"))}</p>'
            f'<p class="meta">{esc(money(item.get("price_per_person_low"), item.get("price_per_person_high"), item.get("currency")))} per person · {esc(item.get("reservation_or_queue_note"))}</p>'
            f'<a class="dining-link" data-dining-provider="{attr(provider)}" data-verified-at="{attr(item.get("checked_at"))}" href="{attr(item.get("venue_url"))}" target="_blank" rel="noopener noreferrer">View restaurant in {esc(provider)}</a>'
            f'{reservation}{backup}</article>'
        )
    return f'<div class="dining-grid">{"".join(cards)}</div>'


def destination_anchor_cards(value: object) -> str:
    if not isinstance(value, list):
        return ""
    items = []
    for anchor in value:
        if not isinstance(anchor, dict):
            continue
        items.append(
            f'<article class="anchor"><span class="pill">Day {esc(anchor.get("planned_day"))}</span><h3>{esc(anchor.get("name"))}</h3>'
            f'<p>{esc(anchor.get("category"))} · {esc(anchor.get("neighborhood_or_area"))}</p>'
            f'<p>{esc(anchor.get("why_it_matters"))}</p>'
            f'<a class="anchor-link" data-verified-at="{attr(anchor.get("checked_at"))}" href="{attr(anchor.get("source_url"))}" target="_blank" rel="noopener noreferrer">View source</a></article>'
        )
    return f'<section id="destination-essentials" class="panel"><h2>Destination essentials</h2><div class="grid">{"".join(items)}</div></section>' if items else ""


def link_for_ticket(ticket: dict | None) -> str:
    if not ticket:
        return "No separate ticket is required or it was not verified."
    return f'<a class="booking-link" data-booking-type="ticket" data-provider="{attr(as_text(ticket.get("official_or_authorised_provider"), "Provider"))}" data-verified-at="{attr(as_text(ticket.get("checked_at")))}" href="{attr(ticket.get("review_url"))}" target="_blank" rel="noopener noreferrer">Review ticket: {esc(ticket.get("attraction_name"))}</a>'


def validate_plan(plan: dict) -> list[str]:
    errors: list[str] = []
    if not is_iso_datestamp(plan.get("generated_at")):
        errors.append("generated_at must be an ISO date or date-time.")
    trip = plan.get("trip") if isinstance(plan.get("trip"), dict) else {}
    for field in ("title", "language", "currency", "origin", "destination", "destination_type", "start_date", "end_date", "traveler_count", "pace", "budget_basis", "arrival_transport_mode"):
        if not trip.get(field):
            errors.append(f"trip.{field} is required.")
    if trip.get("language") and not has_builtin_interface_language(trip["language"]):
        custom_labels = plan.get("ui_labels")
        if not isinstance(custom_labels, dict):
            errors.append("A non-English/Chinese plan needs a complete ui_labels object for renderer-owned interface text.")
        else:
            missing = REQUIRED_UI_LABEL_KEYS - set(custom_labels)
            invalid = [
                key
                for key in REQUIRED_UI_LABEL_KEYS
                if not isinstance(custom_labels.get(key), str) or (not custom_labels[key].strip() and key != "day_suffix")
            ]
            if missing or invalid:
                errors.append("ui_labels must provide every renderer label for a non-English/Chinese plan: " + ", ".join(sorted(missing | set(invalid))) + ".")
    if trip.get("arrival_transport_mode") not in {"flight", "rail", "road", "other"}:
        errors.append("trip.arrival_transport_mode must be flight, rail, road, or other.")
    if trip.get("destination_type") not in {"city", "region", "multi-stop", "other"}:
        errors.append("trip.destination_type must be city, region, multi-stop, or other.")
    trip_start = parse_iso_date(trip.get("start_date"), "trip.start_date", errors)
    trip_end = parse_iso_date(trip.get("end_date"), "trip.end_date", errors)
    if trip_start and trip_end and trip_end < trip_start:
        errors.append("trip.end_date cannot be before trip.start_date.")
    budget = plan.get("budget") if isinstance(plan.get("budget"), dict) else {}
    if budget.get("calculation_basis") != "per_person":
        errors.append("budget.calculation_basis must be per_person.")
    if budget.get("estimated_per_person_low") is None or budget.get("estimated_per_person_high") is None:
        errors.append("budget needs estimated_per_person_low and estimated_per_person_high.")
    elif not is_nonnegative_money_range(budget["estimated_per_person_low"], budget["estimated_per_person_high"]):
        errors.append("budget estimated_per_person_low/high must be non-negative numbers with low less than or equal to high.")
    if not isinstance(budget.get("included_categories"), list) or not budget.get("included_categories"):
        errors.append("budget.included_categories must name the categories included in the per-person total.")
    if not isinstance(budget.get("unverified_categories"), list):
        errors.append("budget.unverified_categories must be a list.")
    breakdown = budget.get("breakdown")
    if not isinstance(breakdown, list) or not breakdown:
        errors.append("budget.breakdown must contain a transparent per-person cost breakdown.")
    else:
        breakdown_categories: set[str] = set()
        for number, item in enumerate(breakdown, 1):
            required_breakdown_fields = ("category", "description", "per_person_low", "per_person_high", "currency", "price_status", "checked_at", "note")
            if not isinstance(item, dict) or not all(item.get(field) is not None and item.get(field) != "" for field in required_breakdown_fields):
                errors.append(f"budget.breakdown[{number}] needs category, per-person range, currency, price status, check time, and note.")
                continue
            if item["currency"] != trip.get("currency"):
                errors.append(f"budget.breakdown[{number}].currency must match trip.currency.")
            if not is_nonnegative_money_range(item["per_person_low"], item["per_person_high"]):
                errors.append(f"budget.breakdown[{number}] per-person range must be non-negative numbers with low less than or equal to high.")
            if item["price_status"] not in PRICE_STATUSES:
                errors.append(f"budget.breakdown[{number}].price_status must be researched_current, estimate, or user_confirmed.")
            if not is_iso_datestamp(item["checked_at"]):
                errors.append(f"budget.breakdown[{number}].checked_at must be an ISO date or date-time.")
            if item["category"] in breakdown_categories:
                errors.append(f"budget.breakdown has duplicate category: {item['category']}.")
            breakdown_categories.add(item["category"])
        if isinstance(budget.get("included_categories"), list):
            missing_breakdown = {str(category) for category in budget["included_categories"]} - breakdown_categories
            if missing_breakdown:
                errors.append("budget.breakdown is missing included categories: " + ", ".join(sorted(missing_breakdown)) + ".")
    transport = plan.get("transport_preference") if isinstance(plan.get("transport_preference"), dict) else {}
    mode = transport.get("mode")
    if mode not in {"self-drive", "public-transit"}:
        errors.append("transport_preference.mode must be self-drive or public-transit.")
    regional_context = plan.get("regional_service_context") if isinstance(plan.get("regional_service_context"), dict) else {}
    for field in ("destination_service_market", "selection_basis", "primary_map_provider"):
        if not regional_context.get(field):
            errors.append(f"regional_service_context.{field} is required.")
    if regional_context.get("google_services_access") not in {"available", "unavailable", "unknown"}:
        errors.append("regional_service_context.google_services_access must be available, unavailable, or unknown.")
    for field in ("alternative_map_providers", "local_transport_sources"):
        if not isinstance(regional_context.get(field), list):
            errors.append(f"regional_service_context.{field} must be a list.")
    market = str(regional_context.get("destination_service_market") or "").casefold()
    primary_provider = str(regional_context.get("primary_map_provider") or "")
    mainland_primary_exception = regional_context.get("primary_map_exception_reason")
    if market == "mainland_china" and not any(token in primary_provider.casefold() for token in ("amap", "高德")) and not mainland_primary_exception:
        errors.append("mainland_china plans require Amap/高德地图 as the primary map provider unless a user-confirmed primary_map_exception_reason is recorded.")
    if market == "mainland_china" and is_google_map(primary_provider, ""):
        errors.append("Google Maps cannot be the mainland-China primary provider.")
    booking_access_categories = validate_booking_access_checks(regional_context.get("booking_access_checks"), errors)
    days = plan.get("days") if isinstance(plan.get("days"), list) else []
    if not days:
        errors.append("At least one day is required.")
    day_dates: dict[int, date] = {}
    for expected, day in enumerate(days, 1):
        if not isinstance(day, dict) or day.get("number") != expected:
            errors.append("days must be objects numbered consecutively from 1.")
            continue
        if day.get("day_type") not in {"arrival", "departure", "full", "transfer"}:
            errors.append(f"day {expected}.day_type must be arrival, departure, full, or transfer.")
        activities = day.get("activities")
        if not isinstance(activities, list) or not activities:
            errors.append(f"day {expected} needs at least one timed activity.")
        elif day.get("day_type") == "full" and len(activities) < 2:
            errors.append(f"day {expected} is a full day and needs at least two meaningful activities, or must be marked as a deep single-site day with its reason.")
        day_date = parse_iso_date(day.get("date"), f"day {expected} date", errors)
        if day_date:
            day_dates[expected] = day_date
            if trip_start and trip_end and not trip_start <= day_date <= trip_end:
                errors.append(f"day {expected} date must fall within the trip date range.")
        route = day.get("route") if isinstance(day.get("route"), dict) else {}
        for field in ("start", "end", "mode", "route_logic", "fallback_plan", "route_map_scope", "map_link_kind", "verified_map_url", "map_checked_at", "map_provider"):
            if not route.get(field):
                errors.append(f"day {expected} route.{field} is required.")
        if route.get("route_map_scope") and route.get("route_map_scope") not in ROUTE_MAP_SCOPES:
            errors.append(f"day {expected} route.route_map_scope must be multi_stop or primary_leg.")
        if route.get("map_link_kind") and route.get("map_link_kind") != "directions":
            errors.append(f"day {expected} route.map_link_kind must be directions.")
        if route.get("mode") and is_ambiguous_route_mode(route.get("mode")):
            errors.append(f"day {expected} route.mode must name one researched primary mode; put alternatives in fallback_plan.")
        if route.get("verified_map_url") and not is_https(route["verified_map_url"]):
            errors.append(f"day {expected} route.verified_map_url must be HTTPS.")
        elif route.get("verified_map_url") and route.get("map_provider") and not is_directions_url(route["map_provider"], route["verified_map_url"]):
            errors.append(f"day {expected} route.verified_map_url must be an actual directions URL, not a place/POI page.")
        if route.get("map_provider") and route.get("verified_map_url") and not map_link_allowed(route["map_provider"], route["verified_map_url"], regional_context):
            errors.append(f"day {expected} route uses Google Maps despite the regional-access rule.")
        if market == "mainland_china" and not mainland_primary_exception and route.get("map_provider") and route.get("verified_map_url") and not is_amap(route["map_provider"], route["verified_map_url"]):
            errors.append(f"day {expected} mainland-China primary route must use Amap/高德地图.")
        validate_alternative_map_links(route.get("alternative_map_links"), f"day {expected} route", regional_context, errors)
        segments = route.get("segments")
        stops = route.get("stops_in_order")
        if not isinstance(stops, list) or len(stops) < 2 or not all(isinstance(stop, str) and stop.strip() for stop in stops):
            errors.append(f"day {expected} route.stops_in_order needs at least two named stops.")
        elif route.get("start") != stops[0] or route.get("end") != stops[-1]:
            errors.append(f"day {expected} route start/end must match the first/last stop in stops_in_order.")
        if not isinstance(segments, list) or not segments:
            errors.append(f"day {expected} route.segments needs at least one mapped segment.")
        else:
            if isinstance(stops, list) and len(stops) >= 2 and len(segments) != len(stops) - 1:
                errors.append(f"day {expected} route needs one exact segment for every consecutive pair in stops_in_order.")
            for segment_number, segment in enumerate(segments, 1):
                required_segment_fields = (
                    "from", "to", "mode", "duration_minutes", "walking_minutes", "transfer_count",
                    "service_or_line", "journey_instruction", "arrival_instruction", "fare_basis",
                    "fallback_note", "map_link_kind", "verified_map_url", "map_checked_at", "map_provider",
                )
                if not isinstance(segment, dict) or not all(segment.get(key) is not None and segment.get(key) != "" for key in required_segment_fields):
                    errors.append(f"day {expected} route segment {segment_number} needs endpoints, one primary mode, service/instructions, walk/transfers, fare basis, fallback, and a mapped directions URL.")
                    continue
                if segment.get("map_link_kind") != "directions":
                    errors.append(f"day {expected} route segment {segment_number}.map_link_kind must be directions.")
                if is_ambiguous_route_mode(segment.get("mode")):
                    errors.append(f"day {expected} route segment {segment_number}.mode must be one primary mode; alternatives belong in fallback_note.")
                if not is_nonnegative_number(segment.get("duration_minutes")) or not is_nonnegative_number(segment.get("walking_minutes")):
                    errors.append(f"day {expected} route segment {segment_number} duration_minutes and walking_minutes must be non-negative numbers.")
                if not isinstance(segment.get("transfer_count"), int) or isinstance(segment.get("transfer_count"), bool) or segment["transfer_count"] < 0:
                    errors.append(f"day {expected} route segment {segment_number}.transfer_count must be a non-negative integer.")
                if isinstance(stops, list) and segment_number < len(stops) and (segment.get("from") != stops[segment_number - 1] or segment.get("to") != stops[segment_number]):
                    errors.append(f"day {expected} route segment {segment_number} must match consecutive stops: {stops[segment_number - 1]} → {stops[segment_number]}.")
                if not is_https(segment["verified_map_url"]):
                    errors.append(f"day {expected} route segment {segment_number} map URL must be HTTPS.")
                elif not is_directions_url(segment["map_provider"], segment["verified_map_url"]):
                    errors.append(f"day {expected} route segment {segment_number} map URL must be a directions URL, not a place/POI page.")
                if not map_link_allowed(segment["map_provider"], segment["verified_map_url"], regional_context):
                    errors.append(f"day {expected} route segment {segment_number} uses Google Maps despite the regional-access rule.")
                if market == "mainland_china" and not mainland_primary_exception and not is_amap(segment["map_provider"], segment["verified_map_url"]):
                    errors.append(f"day {expected} mainland-China route segment {segment_number} must use Amap/高德地图.")
                validate_alternative_map_links(segment.get("alternative_map_links"), f"day {expected} route segment {segment_number}", regional_context, errors)
        if mode == "public-transit" and route.get("cost_low") is None and route.get("cost_high") is None:
            errors.append(f"day {expected} public-transit route needs a researched fare or range.")
        elif mode == "public-transit" and not is_nonnegative_money_range(route.get("cost_low"), route.get("cost_high")):
            errors.append(f"day {expected} public-transit route cost range must be non-negative numbers with low less than or equal to high.")
        if mode == "self-drive" and (
            route.get("duration_minutes") is None or route.get("distance_km") is None
        ):
            errors.append(f"day {expected} self-drive route needs duration_minutes and distance_km.")
        elif mode == "self-drive" and (not is_nonnegative_number(route.get("duration_minutes")) or not is_nonnegative_number(route.get("distance_km"))):
            errors.append(f"day {expected} self-drive route duration_minutes and distance_km must be non-negative numbers.")
        dining = day.get("dining")
        if not isinstance(dining, list):
            errors.append(f"day {expected}.dining must be a list.")
        else:
            meal_types = {item.get("meal") for item in dining if isinstance(item, dict)}
            required_meals = {"lunch", "dinner"} if day.get("day_type") == "full" else {"dinner"} if day.get("day_type") == "arrival" else {"breakfast"} if day.get("day_type") == "departure" else set()
            missing_meals = required_meals - meal_types
            if missing_meals:
                errors.append(f"day {expected} dining needs: {', '.join(sorted(missing_meals))}.")
            for dining_number, item in enumerate(dining, 1):
                required_dining_fields = ("meal", "time_window", "venue_name", "cuisine_or_style", "neighborhood", "why_this_stop", "price_per_person_low", "price_per_person_high", "currency", "reservation_or_queue_note", "venue_url", "map_provider", "checked_at")
                if not isinstance(item, dict) or not all(item.get(key) is not None and item.get(key) != "" for key in required_dining_fields):
                    errors.append(f"day {expected} dining {dining_number} needs meal, concrete venue, price, queue note, source URL, map provider, and check time.")
                    continue
                if item.get("meal") not in {"breakfast", "lunch", "dinner", "snack"}:
                    errors.append(f"day {expected} dining {dining_number}.meal must be breakfast, lunch, dinner, or snack.")
                if not is_nonnegative_money_range(item.get("price_per_person_low"), item.get("price_per_person_high")):
                    errors.append(f"day {expected} dining {dining_number} price range must be non-negative numbers with low less than or equal to high.")
                if not is_https(item.get("venue_url")):
                    errors.append(f"day {expected} dining {dining_number}.venue_url must be HTTPS.")
                if item.get("reservation_url") and not is_https(item.get("reservation_url")):
                    errors.append(f"day {expected} dining {dining_number}.reservation_url must be a safe HTTPS browse URL when present.")
    if trip_start and trip_end and len(day_dates) == len(days):
        expected_dates = [trip_start + timedelta(days=offset) for offset in range((trip_end - trip_start).days + 1)]
        actual_dates = [day_dates[number] for number in sorted(day_dates)]
        if actual_dates != expected_dates:
            errors.append("days must cover every trip date consecutively from trip.start_date through trip.end_date.")
    anchors = plan.get("destination_experience_anchors")
    if not isinstance(anchors, list):
        errors.append("destination_experience_anchors must be a list.")
    else:
        minimum_anchors = 3 if trip.get("destination_type") == "city" and len(days) >= 3 else 1
        if len(anchors) < minimum_anchors:
            errors.append(f"This trip needs at least {minimum_anchors} destination-specific experience anchor(s), so a city itinerary does not collapse into isolated headline attractions.")
        planned_anchor_days: set[int] = set()
        for number, anchor in enumerate(anchors, 1):
            required_anchor_fields = ("name", "category", "neighborhood_or_area", "planned_day", "why_it_matters", "source_url", "checked_at")
            if not isinstance(anchor, dict) or not all(anchor.get(field) for field in required_anchor_fields):
                errors.append(f"destination_experience_anchors[{number}] needs name, category, area, planned day, rationale, source URL, and check time.")
                continue
            if anchor["planned_day"] not in day_dates:
                errors.append(f"destination_experience_anchors[{number}].planned_day must name a real trip day.")
            else:
                planned_anchor_days.add(anchor["planned_day"])
            if not is_https(anchor["source_url"]):
                errors.append(f"destination_experience_anchors[{number}].source_url must be HTTPS.")
        if trip.get("destination_type") == "city" and len(days) >= 3 and len(planned_anchor_days) < 2:
            errors.append("A city itinerary needs destination-specific anchors across at least two planned days.")
    sources = plan.get("sources") if isinstance(plan.get("sources"), list) else []
    if not sources:
        errors.append("At least one source is required.")
    for source in sources:
        if not isinstance(source, dict) or not all(source.get(key) for key in ("name", "url", "source_type", "accessed_at")) or not is_https(source.get("url")):
            errors.append("Every source needs name, HTTPS url, source_type, and accessed_at.")
        elif not is_iso_datestamp(source["accessed_at"]):
            errors.append("Every source.accessed_at must be an ISO date or date-time.")
    options = plan.get("booking_options") if isinstance(plan.get("booking_options"), dict) else {}
    required = {"flights": "flight", "accommodations": "hotel", "attraction_tickets": "ticket", "rental_cars": "car"}
    for field, kind in required.items():
        items = options.get(field, [])
        if not isinstance(items, list):
            errors.append(f"booking_options.{field} must be a list.")
            continue
        for item in items:
            if not isinstance(item, dict) or not all(item.get(key) for key in ("provider" if kind != "ticket" else "official_or_authorised_provider", "checked_at", "review_url")) or not is_https(item.get("review_url")):
                errors.append(f"Every {kind} option needs provider, checked_at, and an HTTPS review_url.")
                continue
            if kind in {"flight", "hotel"} and not item.get("id"):
                errors.append(f"Every {kind} option needs a stable, non-empty id for day assignments and comparison.")
            if item.get("comparison_platform") and not item.get("comparison_checked_at"):
                errors.append(f"Every compared {kind} option needs comparison_checked_at.")
            if not is_iso_datestamp(item.get("checked_at")):
                errors.append(f"Every {kind} option.checked_at must be an ISO date or date-time.")
            if item.get("comparison_checked_at") and not is_iso_datestamp(item.get("comparison_checked_at")):
                errors.append(f"Every compared {kind} option.comparison_checked_at must be an ISO date or date-time.")
            if item.get("direct_review_url") and not is_https(item.get("direct_review_url")):
                errors.append(f"Every direct {kind} cross-check URL must be HTTPS.")
            if kind == "flight":
                required_flight_fields = (
                    "origin_airport", "destination_airport", "outbound_date", "return_date",
                    "outbound_itinerary", "return_itinerary", "baggage_assumption", "material_conditions",
                    "availability_status", "price_checked_at", "airport_transfer_note",
                    "round_trip_search_url", "round_trip_search_provider", "round_trip_search_checked_at", "round_trip_prefilled_fields",
                )
                if not all(item.get(key) for key in required_flight_fields):
                    errors.append("Every flight option needs route, dates, concrete outbound/return itineraries, fare conditions, current-price status, airport transfer note, and a verified dated round-trip search URL.")
                elif not is_https(item["round_trip_search_url"]):
                    errors.append("Every flight round-trip search URL must be HTTPS.")
                search_fields = item.get("round_trip_prefilled_fields")
                if not isinstance(search_fields, list) or not REQUIRED_FLIGHT_SEARCH_FIELDS.issubset(set(search_fields)):
                    errors.append("Every flight round-trip search must prefill origin, destination, outbound date, return date, and travellers.")
                if not is_iso_datestamp(item.get("round_trip_search_checked_at")):
                    errors.append("flight.round_trip_search_checked_at must be an ISO date or date-time.")
                for leg_name in ("outbound_itinerary", "return_itinerary"):
                    leg = item.get(leg_name)
                    if not isinstance(leg, dict) or not all(leg.get(key) is not None and leg.get(key) != "" for key in ("service_identifier", "departure_local", "arrival_local", "duration_minutes", "stops", "connection_or_terminal_note")):
                        errors.append(f"flight.{leg_name} needs carrier/flight or service identifier, local times, duration, stops, and connection/terminal note.")
                if item.get("availability_status") not in {"available", "limited", "unknown"}:
                    errors.append("flight.availability_status must be available, limited, or unknown.")
                if item.get("price_basis") != "per_person_round_trip":
                    errors.append("flight.price_basis must be per_person_round_trip.")
                if item.get("price_status") not in PRICE_STATUSES:
                    errors.append("flight.price_status must be researched_current, estimate, or user_confirmed.")
                if item.get("fare_low") is None or item.get("fare_high") is None or not item.get("fare_currency"):
                    errors.append("Every flight option needs a checked per-person fare range and currency.")
                elif not is_nonnegative_money_range(item.get("fare_low"), item.get("fare_high")):
                    errors.append("Every flight fare range must be non-negative numbers with low less than or equal to high.")
                if not is_iso_datestamp(item.get("price_checked_at")):
                    errors.append("flight.price_checked_at must be an ISO date or date-time.")
                outbound = parse_iso_date(item.get("outbound_date"), "flight.outbound_date", errors)
                inbound = parse_iso_date(item.get("return_date"), "flight.return_date", errors)
                if outbound and inbound and inbound < outbound:
                    errors.append("flight.return_date cannot be before flight.outbound_date.")
                if outbound and trip_start and outbound != trip_start:
                    errors.append("flight.outbound_date must match trip.start_date; include travel days in the trip window.")
                if inbound and trip_end and inbound != trip_end:
                    errors.append("flight.return_date must match trip.end_date; include travel days in the trip window.")
            if kind == "hotel":
                required_hotel_fields = (
                    "stay_location", "neighborhood", "address_or_location_reference", "check_in", "check_out",
                    "guest_count", "room_count", "room_basis", "nightly_cost_low", "nightly_cost_high",
                    "trip_cost_low", "trip_cost_high", "currency", "price_checked_at", "availability_status",
                    "arrival_access_note", "key_area_access_note", "selection_rationale",
                )
                if not all(item.get(key) is not None and item.get(key) != "" for key in required_hotel_fields):
                    errors.append("Every hotel option needs an exact stay area, access rationale, room and current price details, availability status, and check-in/out dates.")
                if item.get("availability_status") not in {"available", "limited", "unknown"}:
                    errors.append("hotel.availability_status must be available, limited, or unknown.")
                if not item.get("stay_group_id"):
                    errors.append("Every hotel option needs a stay_group_id so comparable options cannot be split by different neighborhood labels.")
                if item.get("price_basis") != "per_room_per_night":
                    errors.append("hotel.price_basis must be per_room_per_night.")
                if item.get("price_status") not in PRICE_STATUSES:
                    errors.append("hotel.price_status must be researched_current, estimate, or user_confirmed.")
                if not is_iso_datestamp(item.get("price_checked_at")):
                    errors.append("hotel.price_checked_at must be an ISO date or date-time.")
                for cost_label, low_key, high_key in (
                    ("nightly", "nightly_cost_low", "nightly_cost_high"),
                    ("trip", "trip_cost_low", "trip_cost_high"),
                ):
                    if not is_nonnegative_money_range(item.get(low_key), item.get(high_key)):
                        errors.append(f"hotel {cost_label} price range must be non-negative numbers with low less than or equal to high.")
                searches = item.get("comparison_searches")
                if not isinstance(searches, list) or not searches:
                    errors.append("Every hotel option needs at least one dated comparison-platform search.")
                else:
                    for search in searches:
                        if not isinstance(search, dict) or not all(search.get(key) for key in ("platform", "search_url", "checked_at")) or not is_https(search.get("search_url")):
                            errors.append("Every hotel comparison search needs platform, checked_at, and an HTTPS search URL.")
                            continue
                        if not is_iso_datestamp(search["checked_at"]):
                            errors.append("Every hotel comparison search.checked_at must be an ISO date or date-time.")
                        fields = search.get("prefilled_fields")
                        if not isinstance(fields, list) or not REQUIRED_STAY_SEARCH_FIELDS.issubset(set(fields)):
                            errors.append("Every hotel comparison search must prefill destination, check-in/out, guests, and rooms.")
                check_in = parse_iso_date(item.get("check_in"), "hotel.check_in", errors)
                check_out = parse_iso_date(item.get("check_out"), "hotel.check_out", errors)
                if check_in and check_out and check_out <= check_in:
                    errors.append("hotel.check_out must be after hotel.check_in.")
                for field_name in ("guest_count", "room_count"):
                    if not isinstance(item.get(field_name), int) or isinstance(item.get(field_name), bool) or item[field_name] < 1:
                        errors.append(f"hotel.{field_name} must be a positive integer.")
            if kind == "ticket":
                required_ticket_fields = (
                    "id", "day_number", "attraction_name", "timed_entry_or_reservation", "price_low", "price_high",
                    "currency", "ticket_status", "price_basis", "price_status", "price_checked_at", "availability_status",
                )
                if not all(item.get(key) is not None and item.get(key) != "" for key in required_ticket_fields):
                    errors.append("Every attraction-ticket option needs an id, day, attraction/reservation details, per-person price, status, and check time.")
                if item.get("price_basis") != "per_person_ticket":
                    errors.append("ticket.price_basis must be per_person_ticket.")
                if item.get("price_status") not in PRICE_STATUSES:
                    errors.append("ticket.price_status must be researched_current, estimate, or user_confirmed.")
                if item.get("availability_status") not in {"available", "limited", "unknown"}:
                    errors.append("ticket.availability_status must be available, limited, or unknown.")
                if not is_nonnegative_money_range(item.get("price_low"), item.get("price_high")):
                    errors.append("ticket price range must be non-negative numbers with low less than or equal to high.")
                if not is_iso_datestamp(item.get("price_checked_at")):
                    errors.append("ticket.price_checked_at must be an ISO date or date-time.")
            if kind == "car":
                required_car_fields = (
                    "id", "pickup_location", "dropoff_location", "pickup_time", "dropoff_time", "vehicle_class",
                    "transmission", "capacity_note", "price_low", "price_high", "currency", "price_basis", "price_status",
                    "price_checked_at", "availability_status", "insurance_excess", "fuel_policy", "mileage_policy",
                    "cross_border_or_restriction_note", "rental_search_prefilled_fields",
                )
                if not all(item.get(key) is not None and item.get(key) != "" for key in required_car_fields):
                    errors.append("Every rental-car option needs exact pickup/dropoff, vehicle/terms, dated per-day price, availability, and search-prefill details.")
                if item.get("price_basis") != "per_vehicle_per_day":
                    errors.append("rental_car.price_basis must be per_vehicle_per_day.")
                if item.get("price_status") not in PRICE_STATUSES:
                    errors.append("rental_car.price_status must be researched_current, estimate, or user_confirmed.")
                if item.get("availability_status") not in {"available", "limited", "unknown"}:
                    errors.append("rental_car.availability_status must be available, limited, or unknown.")
                if not is_nonnegative_money_range(item.get("price_low"), item.get("price_high")):
                    errors.append("rental-car price range must be non-negative numbers with low less than or equal to high.")
                if not is_iso_datestamp(item.get("price_checked_at")):
                    errors.append("rental_car.price_checked_at must be an ISO date or date-time.")
                rental_fields = item.get("rental_search_prefilled_fields")
                if not isinstance(rental_fields, list) or not REQUIRED_RENTAL_SEARCH_FIELDS.issubset(set(rental_fields)):
                    errors.append("Every rental-car search must prefill pickup/dropoff locations and times.")
    required_access_categories = {"accommodation"}
    if options.get("flights"):
        required_access_categories.add("flight")
    if options.get("attraction_tickets"):
        required_access_categories.add("attraction_ticket")
    if options.get("rental_cars"):
        required_access_categories.add("rental_car")
    if mode == "public-transit":
        required_access_categories.add("rail_or_ground")
    missing_access_categories = required_access_categories - booking_access_categories
    if missing_access_categories:
        errors.append("Missing booking-access checks for: " + ", ".join(sorted(missing_access_categories)) + ".")
    accommodation_items = [item for item in options.get("accommodations", []) if isinstance(item, dict)]
    flight_items = [item for item in options.get("flights", []) if isinstance(item, dict)]
    if len(flight_items) == 1 and not flight_items[0].get("single_option_reason"):
        errors.append("Provide at least two comparable flight candidates, or record a researched single_option_reason for the only feasible option.")
    flight_ids = [item.get("id") for item in flight_items]
    if any(not identifier for identifier in flight_ids) or len(set(flight_ids)) != len(flight_ids):
        errors.append("Flight options must use distinct, non-empty ids so the comparison is not ambiguous.")
    flight_review_urls = [item.get("review_url") for item in flight_items]
    if len(flight_review_urls) != len(set(flight_review_urls)):
        errors.append("Flight candidates must not reuse the same review_url; provide genuinely distinct comparison paths.")
    accommodation_ids = {item.get("id") for item in accommodation_items if item.get("id")}
    if not accommodation_ids:
        errors.append("At least one accommodation option with an id is required.")
    if len(accommodation_ids) != len(accommodation_items):
        errors.append("Accommodation options must use distinct, non-empty ids so daily stay assignments remain unambiguous.")
    accommodation_counts: dict[str, int] = {}
    accommodation_windows: dict[str, tuple[date | None, date | None]] = {}
    for item in accommodation_items:
        group = item.get("stay_group_id")
        if group:
            accommodation_counts[group] = accommodation_counts.get(group, 0) + 1
        item_id = item.get("id")
        if item_id:
            accommodation_windows[item_id] = (
                parse_iso_date(item.get("check_in"), "hotel.check_in", []),
                parse_iso_date(item.get("check_out"), "hotel.check_out", []),
            )
    if any(count > 3 for count in accommodation_counts.values()):
        errors.append("Provide no more than three accommodation options per stay location.")
    for group, count in accommodation_counts.items():
        if count < 2:
            only_option = next((item for item in accommodation_items if item.get("stay_group_id") == group), {})
            if not only_option.get("single_option_reason"):
                errors.append(f"Provide two comparable hotel options for stay group {group}, or record a researched single_option_reason (for example, a remote stay or user-selected property).")
    ticket_ids = {
        item.get("id")
        for item in options.get("attraction_tickets", [])
        if isinstance(item, dict) and item.get("id")
    }
    for ticket in options.get("attraction_tickets", []):
        if isinstance(ticket, dict) and ticket.get("day_number") not in day_dates:
            errors.append("Every attraction-ticket option.day_number must name a real trip day.")
    for day in days:
        if not isinstance(day, dict):
            continue
        accommodation_id = day.get("accommodation_option_id")
        departure_without_overnight = day.get("day_type") == "departure" and not accommodation_id
        if not departure_without_overnight and accommodation_id not in accommodation_ids:
            errors.append(f"day {day.get('number', '?')} needs a valid accommodation_option_id.")
        elif accommodation_id in accommodation_ids:
            day_date = day_dates.get(day.get("number"))
            check_in, check_out = accommodation_windows[accommodation_id]
            if day_date and check_in and check_out:
                if day.get("day_type") == "departure":
                    if not check_in < day_date <= check_out:
                        errors.append(f"day {day.get('number', '?')} departure accommodation must be the hotel checked out on that date.")
                elif not check_in <= day_date < check_out:
                    errors.append(f"day {day.get('number', '?')} is not covered by its assigned overnight accommodation dates.")
        for activity in day.get("activities", []):
            if isinstance(activity, dict) and activity.get("ticket_option_id") and activity["ticket_option_id"] not in ticket_ids:
                errors.append(f"day {day.get('number', '?')} references an unknown ticket_option_id.")
    if trip.get("arrival_transport_mode") == "flight" and not options.get("flights"):
        errors.append("A flight-arrival plan needs at least one flight option.")
    if mode == "self-drive" and not options.get("rental_cars"):
        errors.append("A self-drive plan needs at least one rental-car option.")
    if mode == "public-transit" and options.get("rental_cars"):
        errors.append("A public-transit plan must not include rental-car options.")
    overview = plan.get("transport_overview") if isinstance(plan.get("transport_overview"), dict) else {}
    if not overview.get("overall_route_map_url") or not overview.get("overall_map_checked_at") or not overview.get("overall_map_provider") or not overview.get("overall_map_scope") or not overview.get("map_link_kind"):
        errors.append("transport_overview needs a directions URL, map scope/link kind, map check time, and map provider.")
    elif overview.get("overall_map_scope") not in ROUTE_MAP_SCOPES:
        errors.append("transport_overview.overall_map_scope must be multi_stop or primary_leg.")
    elif overview.get("map_link_kind") != "directions":
        errors.append("transport_overview.map_link_kind must be directions.")
    elif not is_https(overview["overall_route_map_url"]):
        errors.append("transport_overview.overall_route_map_url must be HTTPS.")
    elif not is_directions_url(overview["overall_map_provider"], overview["overall_route_map_url"]):
        errors.append("transport_overview.overall_route_map_url must be an actual directions URL, not a place/POI page.")
    elif not map_link_allowed(overview["overall_map_provider"], overview["overall_route_map_url"], regional_context):
        errors.append("transport_overview uses Google Maps despite the regional-access rule.")
    elif market == "mainland_china" and not mainland_primary_exception and not is_amap(overview["overall_map_provider"], overview["overall_route_map_url"]):
        errors.append("mainland-China transport overview must use Amap/高德地图.")
    validate_alternative_map_links(overview.get("overall_alternative_map_links"), "transport_overview", regional_context, errors)
    if mode == "public-transit" and overview.get("cost_low") is None and overview.get("cost_high") is None:
        errors.append("A public-transit overview needs a researched fare or range.")
    elif mode == "public-transit" and not is_nonnegative_money_range(overview.get("cost_low"), overview.get("cost_high")):
        errors.append("A public-transit overview cost range must be non-negative numbers with low less than or equal to high.")
    if mode == "self-drive" and (
        overview.get("overall_duration_minutes") is None or overview.get("overall_distance_km") is None
    ):
        errors.append("A self-drive overview needs overall duration and distance.")
    elif mode == "self-drive" and (not is_nonnegative_number(overview.get("overall_duration_minutes")) or not is_nonnegative_number(overview.get("overall_distance_km"))):
        errors.append("A self-drive overview duration and distance must be non-negative numbers.")
    return errors


def render_unlocalized(plan: dict) -> str:
    trip = plan["trip"]
    budget = plan.get("budget", {})
    options = plan.get("booking_options", {})
    sources = plan["sources"]
    accommodations = {item.get("id"): item for item in options.get("accommodations", []) if item.get("id")}
    tickets = {item.get("id"): item for item in options.get("attraction_tickets", []) if item.get("id")}
    cards = []
    for field, kind in (("flights", "flight"), ("accommodations", "hotel"), ("attraction_tickets", "ticket"), ("rental_cars", "car")):
        cards.extend(option_card(kind, item) for item in options.get(field, []))
    if not cards:
        cards.append('<p class="meta">No purchase options were requested for this plan.</p>')
    day_cards = []
    for day in plan["days"]:
        route = day["route"]
        stay = accommodations.get(day.get("accommodation_option_id"))
        activities = []
        day_tickets = []
        for activity in day.get("activities", []):
            ticket = tickets.get(activity.get("ticket_option_id"))
            activities.append(f'<li><time>{esc(activity.get("time"), "Flexible")}</time><div><strong>{esc(activity.get("name"))}</strong><p>{esc(activity.get("detail"))} {esc(activity.get("meal_or_rest_buffer"), "")}</p></div></li>')
            if ticket:
                day_tickets.append(link_for_ticket(ticket))
        transport_bits = [as_text(route.get("mode")), minutes(route.get("duration_minutes")), money(route.get("cost_low"), route.get("cost_high"), route.get("currency", trip["currency"])), as_text(route.get("fare_basis_or_fuel_toll_parking_note"), "")]
        transport_line = " · ".join(bit for bit in transport_bits if bit)
        segment_links = route_segment_links(route, trip["currency"])
        dining = dining_cards(day.get("dining"))
        route_scope = route.get("route_map_scope")
        route_map_label = "Open full-day route" if route_scope == "multi_stop" else "Open route overview — see segments below"
        stay_line = "Checkout / no overnight stay" if day.get("day_type") == "departure" else "Arranged independently"
        if stay:
            stay_line = f"{as_text(stay.get('property_name'))} · {as_text(stay.get('stay_location'))} · {as_text(stay.get('room_basis'))}"
        day_cards.append(f'''<article class="day-card" data-day="{attr(day["number"])}"><div class="day-top"><div><p class="eyebrow">Day {esc(day.get("number"))} · {esc(day.get("date"))}</p><h2>{esc(day.get("title"))}</h2><p>{esc(day.get("focus"))}</p></div><div class="day-number" aria-label="Day {attr(day["number"])}">{esc(day.get("number"))}</div></div><section class="day-accommodation"><h3>Stay</h3><p><strong>{esc(stay_line)}</strong></p></section><section class="day-activities"><h3>Plan</h3><ol class="timeline">{"".join(activities) or '<li><time>Flexible</time><div><strong>Free time</strong></div></li>'}</ol></section><section class="day-dining"><h3>Dining suggestions</h3>{dining}</section><section class="day-route"><h3>Route and mobility</h3><p>{esc(transport_line)}</p><p class="meta">{esc(route.get("route_logic"))}</p><figure class="route-map">{route_svg(route.get("stops_in_order", []))}<figcaption>Schematic — not for navigation. Stops are shown in visit order; use the live map for directions.</figcaption></figure><a class="map-link" data-map-scope="{attr(route_scope)}" data-verified-at="{attr(route["map_checked_at"])}" href="{attr(route["verified_map_url"])}" target="_blank" rel="noopener noreferrer">{route_map_label}</a><h4>Route by segment</h4>{segment_links}<p class="meta">{esc(route.get("service_or_driving_caveat"), "Recheck operating conditions before departure.")}</p></section><section class="day-bookings"><h3>Tickets and recheck</h3>{"".join(day_tickets) or '<p>No verified ticket is required for the listed activities.</p>'}<p class="warning">{esc(day.get("contingency"), "Keep a flexible alternative for disruptions.")}</p></section></article>''')
    overview = plan["transport_overview"]
    source_rows = "".join(f'<li class="source-item" data-source-type="{attr(source["source_type"])}" data-accessed-at="{attr(source["accessed_at"])}" data-source-url="{attr(source["url"])}"><a class="source-link" href="{attr(source["url"])}" target="_blank" rel="noopener noreferrer">{esc(source["name"])}</a> — {esc(source.get("claim_or_decision_supported"), "Plan evidence")} · {esc(source.get("confidence"), "researched")}</li>' for source in sources)
    total = money(budget.get("estimated_per_person_low"), budget.get("estimated_per_person_high"), trip["currency"])
    budget_breakdown = budget_breakdown_cards(budget.get("breakdown"), trip["currency"])
    recheck = "<br>".join(esc(value) for value in plan.get("recheck_before_purchase", []) if value) or "Recheck price, availability, entry requirements, and operating conditions before purchase."
    anchors = destination_anchor_cards(plan.get("destination_experience_anchors"))
    overview_scope = overview.get("overall_map_scope")
    overview_map_label = "Open overall route" if overview_scope == "multi_stop" else "Open transport overview — see daily segments"
    return f'''<!doctype html><html lang="{attr(trip["language"])}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>{esc(trip["title"])}</title><style>:root{{--ink:#162235;--muted:#5d6b7c;--paper:#f7f9fc;--card:#fff;--accent:#0b6e69;--soft:#e4f4f1;--line:#d9e2ec;--warn:#8a4b08;--warn-bg:#fff5df}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}main{{max-width:1120px;margin:auto;padding:32px 20px 56px}}h1{{font-size:clamp(2rem,5vw,3.6rem);line-height:1.05}}h2{{font-size:1.35rem}}h3{{font-size:1.05rem}}h4{{margin:18px 0 0;font-size:1rem}}.hero,.panel,.day-card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;margin:20px 0;box-shadow:0 8px 24px rgb(20 40 65/.05)}}.hero{{background:linear-gradient(135deg,#fff,var(--soft))}}.grid,.dining-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:14px}}.fact,.option,.dining-stop{{border:1px solid var(--line);border-radius:12px;padding:14px}}.fact strong{{display:block}}.eyebrow,.meta{{color:var(--muted);font-size:.92rem}}.eyebrow{{color:var(--accent);font-weight:800;text-transform:uppercase;letter-spacing:.08em}}.pill{{display:inline-block;padding:3px 8px;border-radius:99px;background:var(--soft);color:#075952;font-size:.78rem;font-weight:700}}.day-top{{display:flex;justify-content:space-between;gap:16px}}.day-number{{min-width:48px;height:48px;display:grid;place-items:center;border-radius:50%;background:var(--ink);color:#fff;font-weight:800}}.timeline,.segment-list,.option-details{{list-style:none;padding:0}}.timeline li{{display:grid;grid-template-columns:88px 1fr;gap:12px;padding:12px 0;border-top:1px solid var(--line)}}.timeline time{{color:var(--accent);font-weight:800}}.option-details li{{margin:7px 0}}.segment-list{{margin:8px 0}}.route-segment{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--line)}}.route-segment p{{margin:4px 0 0}}.route-map{{padding:14px;border-radius:12px;background:#f1f7f8;margin:16px 0}}.route-map svg{{display:block;width:100%;height:auto}}.route-map figcaption{{color:var(--muted);font-size:.88rem;margin-top:8px}}a{{color:#075952;font-weight:700}}.booking-link,.map-link,.dining-link,.dining-reservation-link{{display:inline-block;margin:8px 8px 0 0;padding:9px 12px;border-radius:9px;background:var(--accent);color:#fff;text-decoration:none}}.map-link{{background:var(--ink)}}.warning{{border-left:4px solid var(--warn);background:var(--warn-bg);padding:14px;border-radius:0 10px 10px 0}}@media(max-width:600px){{main{{padding:18px 12px 36px}}.hero,.panel,.day-card{{padding:18px}}.timeline li{{grid-template-columns:66px 1fr}}.route-segment{{align-items:flex-start;flex-direction:column}}}}@media print{{body{{background:#fff}}main{{max-width:none;padding:0}}.hero,.panel,.day-card{{box-shadow:none;break-inside:avoid}}.booking-link,.map-link,.dining-link,.dining-reservation-link{{color:#075952;background:transparent;padding:0;text-decoration:underline}}}}</style></head><body><main id="trip-plan" data-trip-plan><header id="trip-summary" class="hero"><p class="eyebrow">Researched itinerary · {esc(plan.get("plan_status"))}</p><h1>{esc(trip["title"])}</h1><p>{esc(trip["origin"])} → {esc(trip["destination"])} · {esc(trip["start_date"])} to {esc(trip["end_date"])} · {esc(trip["traveler_count"])} traveller(s)</p><p class="meta">Arrival: {esc(trip["arrival_transport_mode"])} · Pace: {esc(trip["pace"])} · Currency: {esc(trip["currency"])} · Research last checked: {esc(plan.get("generated_at"))}. Prices and availability require recheck before purchase.</p></header><section class="panel"><h2>Budget at a glance</h2><div class="grid"><div class="fact"><strong>{esc(total)}</strong><span>Comparable cost per person</span></div><div class="fact"><strong>{esc(trip["budget_basis"])}</strong><span>Included assumptions</span></div><div class="fact"><strong>{esc(plan["transport_preference"]["mode"])}</strong><span>Ground-mobility plan</span></div></div></section>{budget_breakdown}{anchors}<section id="booking-panel" class="panel"><h2>Browse options — no purchase made</h2><p class="meta">Current researched options only. Opening a link never creates a reservation.</p><div class="grid">{"".join(cards)}</div></section>{"".join(day_cards)}<section id="transport-overview" class="panel"><h2>Overall transport</h2><p>{esc(" · ".join(part for part in (as_text(plan["transport_preference"]["mode"]), minutes(overview.get("overall_duration_minutes")), as_text(overview.get("overall_distance_km"), "") + " km" if overview.get("overall_distance_km") is not None else "", money(overview.get("cost_low"), overview.get("cost_high"), trip["currency"]), " · ".join(as_text(note) for note in overview.get("notes", []) if note)) if part))}</p><a class="map-link" data-map-scope="{attr(overview_scope)}" data-verified-at="{attr(overview["overall_map_checked_at"])}" href="{attr(overview["overall_route_map_url"])}" target="_blank" rel="noopener noreferrer">{overview_map_label}</a></section><section id="source-register" class="panel"><h2>Sources, confidence, and recheck list</h2><details open><summary>Sources used</summary><ul>{source_rows}</ul></details><details><summary>Recheck before purchase</summary><p>{recheck}</p></details></section></main></body></html>'''


def render(plan: dict) -> str:
    """Render the page and localize renderer-owned text for the requested language."""
    page = decorate_primary_map_links(render_unlocalized(plan), plan)
    page = localize_static_page(page, plan["trip"]["language"], plan.get("ui_labels"))
    return page.replace("</style>", FINAL_PAGE_DESIGN + "</style>", 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a Travel Buddy final itinerary HTML page.")
    parser.add_argument("plan", help="Plan JSON path, or - to read standard input")
    parser.add_argument("output", nargs="?", default="-", help="Output HTML path, or - for standard output")
    args = parser.parse_args()
    try:
        plan = read_json(args.plan)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: Could not read plan JSON: {exc}", file=sys.stderr)
        return 2
    errors = validate_plan(plan)
    if errors:
        print("INVALID PLAN", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    result = render(plan)
    if args.output == "-":
        print(result)
    else:
        Path(args.output).write_text(result, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
