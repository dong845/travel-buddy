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
# Booking states from references/decision-and-research.md. Constrained so the page can
# localize them; a free-form status would print an English enum on a Chinese page.
BOOKING_STATES = ("idea", "researched", "held", "booked")
# Budget categories are an enum for the same reason, and so `included_categories` and
# `breakdown` can be compared without string-matching two different spellings.
BUDGET_CATEGORIES = (
    "flight",
    "rail",
    "intercity_bus",
    "ferry",
    "rental_car",
    "fuel_tolls_parking",
    "accommodation",
    "food",
    "local_transport",
    "attractions",
    "tours_and_activities",
    "insurance",
    "visa_and_entry",
    "shopping_and_misc",
    "contingency",
)
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
ARRIVAL_MODES = ("flight", "rail", "road", "other")
# What the traveller's own status means for getting into this destination. Nationality
# alone cannot answer it: a permit holder may need no visa where their passport would.
#
# Six spellings, three of them newer: the intake contract words the same question as
# not_required / required_held / required_to_apply, splitting "visa required" into
# already-held versus still-to-apply -- the difference between a trip that can be booked
# now and one that cannot. Both vocabularies are accepted because a plan written before
# the split must keep validating and rendering exactly as it did.
ENTRY_STATUSES = (
    "no_visa_required",
    "visa_required",
    "unverified",
    "not_required",
    "required_held",
    "required_to_apply",
)
ALLERGY_SEVERITIES = ("none", "preference", "intolerance", "severe")

# How this plan's requirements were collected. SKILL.md makes the loopback HTML form the required
# path and the chat questionnaire a fallback the TRAVELLER chooses; until this field existed that
# rule was prose, and prose is exactly what a second agent skipped. Measured: assistants on other
# harnesses opened no form at all and went straight to chat, which loses the intake server's
# rejection of document/payment/address fields, its scope-versus-work-mode consistency check, the
# profile's never_recommend and dietary prefill, and the saved intake that
# check_shortlist_consistency.py --intake computes the hard-constraint roster from. Each method
# has to carry the evidence that it was allowed, so that skipping the form costs more than using
# it rather than less.
INTAKE_METHODS = ("html_form", "user_supplied", "chat_fallback")
# Opening-hours provenance, as recorded by the dining research. Anything that is not an
# explicit "verified" is shown in the warning treatment: "researched" means somebody read
# a website once, which is not the same as somebody confirming the venue is open on the
# evening this plan sends the traveller there.
HOURS_STATUS_ENGLISH = {
    "verified": "verified",
    "researched": "researched",
    "unverified": "not verified",
    "closed_unknown": "opening status unknown",
}
HOURS_STATUS_LABEL_KEYS = {
    "verified": "hours_verified",
    "researched": "hours_researched",
    "unverified": "hours_unverified",
    "closed_unknown": "hours_closed_unknown",
}
TRANSPORT_MODES = ("self-drive", "public-transit")
CURRENCY_SYMBOLS = {
    "AUD": "A$", "BRL": "R$", "CAD": "C$", "CNY": "¥", "DKK": "kr", "EUR": "€", "GBP": "£",
    "HKD": "HK$", "IDR": "Rp", "INR": "₹", "JPY": "¥", "KRW": "₩", "MXN": "MX$", "MYR": "RM",
    "NOK": "kr", "NZD": "NZ$", "PHP": "₱", "RUB": "₽", "SEK": "kr", "SGD": "S$", "THB": "฿",
    "TRY": "₺", "TWD": "NT$", "USD": "$", "VND": "₫",
}
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

# One palette per trip character, chosen from what the traveller said they came for.
# trip.destination_type would be the obvious key and is useless: it reads "city" on all eleven
# saved plans. trip.traveler_preferences is the honest signal -- a coast trip and a forest trip
# are different trips even when both are technically cities, and the traveller is the one who
# said which this is.
#
# Each palette is written out rather than generated. A generated hue would eventually produce
# grey-on-grey or a warning colour that reads as decoration, and this page is read in sunlight on
# a phone. The figures in plan_visuals.py all draw with var(--accent), so they re-tone for free.
TRIP_PALETTES = {
    "coast": {  # sea and sand: the default holiday register
        "--ink": "#10283a", "--muted": "#5b7183", "--paper": "#f3f2ea", "--card": "#fffefb",
        "--accent": "#0e7490", "--accent-deep": "#0b5a70", "--accent-soft": "#d9eef5",
        "--line": "#d7ded9", "--warm": "#e8924a", "--warm-soft": "#fdf0e0",
        "hero": "linear-gradient(135deg,#0d2b43 0%,#0f5f80 55%,#12879b 100%)",
        "glow_a": "#cfe9f2", "glow_b": "#fbe3c6",
    },
    "highland": {  # forest, lakes, mountains
        "--ink": "#16261d", "--muted": "#5d7065", "--paper": "#f2f2ec", "--card": "#fffefc",
        "--accent": "#2f7d55", "--accent-deep": "#1f5c3e", "--accent-soft": "#dcefe1",
        "--line": "#d8ded4", "--warm": "#c9762f", "--warm-soft": "#fbeedd",
        "hero": "linear-gradient(135deg,#152b21 0%,#2c5c40 55%,#3f8358 100%)",
        "glow_a": "#d7ead9", "glow_b": "#efe3c4",
    },
    "urban": {  # streets, museums, food, nightlife
        "--ink": "#1b1a22", "--muted": "#6b6875", "--paper": "#f5f2f0", "--card": "#fffdfd",
        "--accent": "#8a3f6b", "--accent-deep": "#6b2d52", "--accent-soft": "#f3e2ec",
        "--line": "#e0d9dc", "--warm": "#d08033", "--warm-soft": "#fdf0e1",
        "hero": "linear-gradient(135deg,#231d2c 0%,#5c2f52 55%,#8a3f6b 100%)",
        "glow_a": "#efdfe9", "glow_b": "#f8e5cd",
    },
    "arid": {  # desert, canyon, dry heat
        "--ink": "#2a1c14", "--muted": "#7a675b", "--paper": "#f7f1e7", "--card": "#fffdf8",
        "--accent": "#b45a24", "--accent-deep": "#8d431a", "--accent-soft": "#fae5d2",
        "--line": "#e3d9c9", "--warm": "#3f7f86", "--warm-soft": "#e0f0f1",
        "hero": "linear-gradient(135deg,#3a2216 0%,#8a4520 55%,#c0682c 100%)",
        "glow_a": "#f6ddc4", "glow_b": "#d9ecec",
    },
    "alpine": {  # snow, ice, winter sport
        "--ink": "#16222e", "--muted": "#61707f", "--paper": "#f1f4f7", "--card": "#ffffff",
        "--accent": "#3f6ea8", "--accent-deep": "#2b527f", "--accent-soft": "#e0eaf6",
        "--line": "#d8e0e8", "--warm": "#c2703f", "--warm-soft": "#fbebe0",
        "hero": "linear-gradient(135deg,#1a2a3b 0%,#33567f 55%,#5688bd 100%)",
        "glow_a": "#dde9f5", "glow_b": "#f2e2d6",
    },
}

# Matched against the traveller's own words, in their own language. Kept explicit rather than
# clever: a word list can be read and argued with, while a classifier cannot be told why it was
# wrong about somebody's holiday.
PALETTE_HINTS = {
    # Multilingual on purpose. The first version held English and Chinese only, so a French plan
    # saying "forêt et lac" scored zero everywhere and fell to the default -- silently, which is
    # the worst way for a styling decision to be wrong. The traveller writes these words in their
    # own language and the plan records that language, so the vocabulary has to travel too.
    "coast": ("coast", "beach", "sea", "island", "ocean", "seaside",
              "playa", "costa", "mar", "isla", "plage", "côte", "mer", "île",
              "strand", "küste", "meer", "insel", "spiaggia", "costiera", "praia",
              "пляж", "море", "ビーチ", "海岸", "海", "岛", "滨", "沙滩", "바다"),
    "highland": ("forest", "lake", "mountain", "hik", "trail", "nature", "wood", "valley",
                 "bosque", "lago", "montaña", "sender", "forêt", "lac", "montagne", "randonn",
                 "wald", "see", "berg", "wander", "foresta", "lago", "montagna",
                 "лес", "озеро", "гора", "森", "湖", "山", "徒步", "自然", "숲", "산"),
    "alpine": ("snow", "ski", "glacier", "winter sport", "nieve", "esquí", "neige", "glacier",
               "schnee", "gletscher", "neve", "снег", "лыж", "雪", "滑雪", "冰川", "スキー"),
    "arid": ("desert", "dune", "canyon", "oasis", "desierto", "duna", "cañón",
             "désert", "wüste", "deserto", "пустын", "沙漠", "戈壁", "峡谷", "砂漠"),
    "urban": ("city", "street", "museum", "food", "market", "nightlife", "architect", "art",
              "ciudad", "calle", "museo", "gastronom", "mercado", "ville", "rue", "musée",
              "stadt", "straße", "città", "cidade", "город", "музей",
              "街区", "美食", "博物馆", "建筑", "市集", "城市", "都市", "グルメ", "도시"),
}


def palette_for(plan: dict) -> dict:
    """Pick the page's colour register from what the traveller asked for."""
    # `or {}` and `or []` treat a wrongly-typed value as present rather than absent, so a
    # traveler_preferences that arrived as a string, or a subtypes list that arrived as a number,
    # crashed the renderer with a traceback on the delivery path instead of failing cleanly.
    # Predates this fix (reproduced on 2.3.0); found by fuzzing the checks added around it. The
    # page colour is a decoration -- it must never be the thing that stops a plan being saved.
    trip = plan.get("trip")
    preferences = trip.get("traveler_preferences") if isinstance(trip, dict) else None
    if not isinstance(preferences, dict):
        preferences = {}
    words = []
    for field in ("ranked_must_haves", "natural_subtypes", "human_cultural_subtypes"):
        value = preferences.get(field)
        if isinstance(value, (list, tuple)):
            words += [v for v in value if isinstance(v, str)]
    haystack = " ".join(words).casefold()
    if not haystack.strip():
        return TRIP_PALETTES["coast"]
    scores = {name: sum(haystack.count(hint.casefold()) for hint in hints)
              for name, hints in PALETTE_HINTS.items()}
    # Ranked must-haves are ranked, so the first one breaks a tie rather than dictionary order.
    best = max(scores, key=lambda name: (scores[name], name == "coast"))
    return TRIP_PALETTES[best if scores[best] else "coast"]


def palette_css(palette: dict) -> str:
    variables = "".join(f"{key}:{value};" for key, value in palette.items()
                        if key.startswith("--"))
    return (f":root{{{variables}}}"
            f"body{{background:radial-gradient(circle at 8% -8%,{palette['glow_a']} 0 13%,"
            f"transparent 32%),radial-gradient(circle at 100% 5%,{palette['glow_b']} 0 12%,"
            f"transparent 29%),var(--paper)}}"
            f".hero{{background:{palette['hero']}}}")


FINAL_PAGE_DESIGN = r"""
:root{--ink:#132238;--muted:#637186;--paper:#f6f1e9;--card:#fffdfa;--accent:#0f766e;--accent-deep:#075c58;--accent-soft:#dff3ee;--line:#d9d9d1;--warm:#ee8d4a;--warm-soft:#fff0e3;--shadow:0 20px 54px rgb(26 42 57/.10);--radius:22px}
body{background:radial-gradient(circle at 8% -8%,#d5ede8 0 13%,transparent 32%),radial-gradient(circle at 100% 5%,#f9dfc3 0 12%,transparent 29%),var(--paper)}
main{max-width:1180px;padding:42px 22px 74px}h1,h2,h3,h4{color:var(--ink)}h1{max-width:780px;color:#fff}h2{letter-spacing:-.025em}
.hero{position:relative;isolation:isolate;overflow:hidden;padding:clamp(30px,5vw,54px);border:0;border-radius:30px;color:#e3f5f1;background:linear-gradient(135deg,#102c43 0%,#105f63 57%,#0d776e 100%);box-shadow:0 24px 60px rgb(10 41 55/.22)}
.hero::before{content:"";position:absolute;z-index:-1;width:440px;height:440px;right:-150px;top:-250px;border:1px solid rgb(255 255 255/.22);border-radius:50%;box-shadow:0 0 0 36px rgb(255 255 255/.06),0 0 0 98px rgb(255 255 255/.04)}
.hero::after{content:"✦";position:absolute;z-index:-1;right:28px;bottom:-22px;color:rgb(255 255 255/.09);font:800 clamp(5rem,16vw,12rem)/1 ui-serif,Georgia,serif;letter-spacing:-.1em}
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
.page-nav{position:sticky;top:0;z-index:5;display:flex;gap:8px;overflow-x:auto;margin:18px 0;padding:10px 12px;border:1px solid rgb(19 34 56/.10);border-radius:16px;background:rgb(255 253 250/.94);backdrop-filter:blur(12px);box-shadow:0 8px 20px rgb(26 42 57/.07)}
.page-nav-link{flex:0 0 auto;display:flex;flex-direction:column;padding:7px 12px;border-radius:11px;background:#eef4f2;color:var(--accent-deep);font-size:.86rem;font-weight:750;text-decoration:none;white-space:nowrap}
.page-nav-link:hover{background:var(--accent-soft)}.day-nav-date{color:var(--muted);font-size:.76rem;font-weight:600}
.panel,.day-card{scroll-margin-top:74px}
.route-rail{list-style:none;margin:0;padding:2px 0 0}
.route-stop{position:relative;display:flex;align-items:flex-start;gap:12px;padding-bottom:15px}
.route-stop::before{content:"";position:absolute;left:12px;top:27px;bottom:-1px;width:2px;background:#b6d5cf}
.route-stop:last-child{padding-bottom:0}.route-stop:last-child::before{display:none}
.route-stop-index{flex:0 0 auto;display:grid;place-items:center;width:26px;height:26px;border-radius:50%;background:var(--accent);color:#fff;font-size:.76rem;font-weight:800}
.route-stop-name{padding-top:3px;font-weight:700}
.unpriced-list{margin:0 0 5px;padding-left:19px;font-weight:700}.fact.unpriced{padding-top:19px}
.route-fallback{border-left:3px solid #cfe0dc;padding-left:11px}
.single-option-reason{border-left:3px solid var(--warm);padding-left:11px}
.ticket-note{margin:6px 0 0;border-left:3px solid var(--warm);padding-left:11px;color:#744016}
@media print{body{background:#fff}.page-nav{display:none}.day-card{break-inside:auto}.day-card section,.option,.dining-stop{break-inside:avoid}.hero{color:var(--ink);background:#fff;border:1px solid #d9d9d1}.hero h1{color:var(--ink)}.hero p,.hero .meta,.hero .eyebrow{color:var(--muted)}.hero::before,.hero::after{display:none}.panel,.day-card{box-shadow:none}h2,h3,h4{break-after:avoid}.booking-link,.map-link,.dining-link,.dining-reservation-link{padding:0;border-radius:0;background:none;box-shadow:none;color:#075952;text-decoration:underline}}
"""

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan_visuals  # noqa: E402


def labels_for(language: object, custom_labels: object = None) -> dict[str, str]:
    """Return presentation labels for the supported final-plan interface languages."""
    normalized = str(language).casefold()
    if normalized.startswith("zh") or "chinese" in normalized or "中文" in normalized:
        return {
            "review_option": "查看选项",
            "review_option_provider": "在 {provider} 查看选项",
            "direct_provider": "在 {provider} 查看官方直订页面",
            "review_reservation": "查看预订",
            "view_source": "查看来源",
            "no_meal_recommendation": "未调研到用餐推荐。",
            "opening_hours_recheck": "切换前请核验当前营业时间。",
            "per_person_suffix": "/ 人",
            "round_trip": "搜索往返行程",
            "round_trip_in": "在 {provider} 搜索往返行程（已带入日期）",
            "compare_booking": "在 Booking.com 比较（已带入日期和住客）",
            "compare_platform_prefix": "在 ",
            "compare_platform_suffix": " 比较（已带入日期和住客）",
            "segment_map": "在地图中打开此路段",
            "segment_map_provider": "在 {provider} 中打开此路段",
            "ticket_prefix": "查看门票：",
            "anchor_serves_label": "你要的是：",
            "fig_walking_caption": "每日步行分钟：接驳段加上各站点站立行走的时间",
            "fig_map_caption": "按到访顺序的真实相对位置。导航请用地图按钮。",
            "fig_budget_caption": "占人均总额的比例，以及用掉了多少上限",
            "fig_shape_caption": "当天的固定时间点",
            "fig_longest_walk": "最长单次步行",
            "photo_credit_label": "图片来源",
            "photo_source_label": "经 Wikimedia，取自条目",
            "unverified_banner_title": "未经事实核验",
            "unverified_banner_body": (
                "本计划没有任何核验记录。其中的票价、营业时间、入境规则与可订状态"
                "均未与运营方或官方来源核对过。请把每一个数字都当作估算，预订前自行核实。"),
            "pill_flight": "机票", "pill_hotel": "酒店", "pill_ticket": "门票",
            "pill_car": "租车", "pill_ground": "铁路/巴士/渡轮",
            "schematic_aria": "按游览顺序的路线示意图",
            "schematic_start": "起点", "schematic_end": "终点",
            # 直飞 means specifically "direct by air", so a through train printed it. The English
            # "non-stop" is fine for both, but a language that distinguishes them needs two keys.
            "nonstop": "直飞", "nonstop_ground": "直达",
            "stops_suffix": "次中转", "unit_km": "公里",
            "group_ground": "铁路 / 长途巴士 / 渡轮选项",
            "station_access": "车站与接驳：",
            "why_providers": "为什么选这些服务商：",
            "constraints_heading": "你的硬性约束",
            "constraints_severity": "过敏严重程度：",
            "constraints_dietary": "饮食限制：",
            "constraints_mobility": "行动能力：",
            "constraints_walk_cap": "单段连续步行上限：",
            "constraints_card": "过敏卡 —— 到店请出示这段文字：",
            "preferences_heading": "你提出的需求",
            "preferences_direction": "体验方向：",
            "preferences_avoid": "希望避开的：",
            "gates_label": "已通过的结构检查：",
            "gates_caveat": "它们证明这份计划自身前后一致，绝不证明其中的事实为真。",
            "plan_status_prefix": "行程状态 · ",
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
            "overall_route_overview": "打开机场接驳路线（每日分段见下方）",
            "overall_route_overview_provider": "在 {provider} 中打开机场接驳路线（每日分段见下方）",
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
            "guest_rating_label": "住客评分：",
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
            "walk": "步行",
            "transfers": "次换乘",
            "meal_breakfast": "早餐",
            "meal_lunch": "午餐",
            "meal_dinner": "晚餐",
            "meal_snack": "小食",
            "state_idea": "构想",
            "state_researched": "已调研",
            "state_held": "已预留",
            "state_booked": "已预订",
            "arrival_flight": "飞机",
            "arrival_rail": "铁路",
            "arrival_road": "陆路",
            "arrival_other": "其他方式",
            "mode_self_drive": "自驾",
            "mode_public_transit": "公共交通",
            "cat_flight": "机票",
            "cat_rail": "铁路",
            "cat_intercity_bus": "长途巴士",
            "cat_ferry": "轮渡",
            "cat_rental_car": "租车",
            "cat_fuel_tolls_parking": "油费/过路费/停车",
            "cat_accommodation": "住宿",
            "cat_food": "餐饮",
            "cat_local_transport": "市内交通",
            "cat_attractions": "景点门票",
            "cat_tours_and_activities": "体验与活动",
            "cat_insurance": "保险",
            "cat_visa_and_entry": "签证与入境",
            "cat_shopping_and_misc": "购物与杂项",
            "cat_contingency": "预备金",
            "not_supplied": "未提供",
            "provider_fallback": "供应商",
            "direct_provider_fallback": "官方直订渠道",
            "map_provider_fallback": "地图服务",
            "comparison_platform_fallback": "比较平台",
            "flexible_time": "灵活安排",
            "route_start": "起点",
            "route_end": "终点",
            "route_fallback_label": "备选方案：",
            "walking_burden_label": "全天步行：",
            "fare_conditions_label": "票价条款：",
            "single_option_label": "仅列一个选项的原因：",
            "unpriced_categories": "尚未核价的类别",
            "none_unpriced": "无",
            "planning_assumptions": "规划假设",
            "platform_note_label": "平台选择说明：",
            "ticket_state_label": "门票状态：",
            "ticket_note_label": "门票：",
            "nav_label": "本页导航",
            "nav_budget": "预算",
            "nav_options": "预订选项",
            "nav_transport": "全程交通",
            "nav_sources": "来源",
            "no_separate_ticket": "无需单独购票，或尚未核验。",
            "entry_heading": "入境资格",
            "entry_basis": "依据的身份：",
            "entry_no_visa_required": "无需另办签证",
            "entry_visa_required": "需要提前办理签证",
            "entry_unverified": "待核验",
            "entry_not_required": "无需另办签证",
            "entry_required_held": "需要签证（已持有）",
            "entry_required_to_apply": "需要签证（尚未办理）",
            "budget_cap": "人均预算上限",
            "venue_hours_label": "营业时间：",
            "rating_label": "评分：",
            "rating_none": "无公开评分",
            "rating_reviews": " 条评价",
            "hours_verified": "已核验",
            "hours_researched": "已调研（未核验）",
            "hours_unverified": "未核验",
            "hours_closed_unknown": "营业状态未知",
            "group_flight": "机票选项",
            "group_hotel": "住宿选项",
            "group_ticket": "门票选项",
            "group_car": "租车选项",
        }
    if isinstance(custom_labels, dict) and all(
        isinstance(value, str) and (value.strip() or key == "day_suffix")
        for key, value in custom_labels.items()
    ) and REQUIRED_UI_LABEL_KEYS.issubset(custom_labels):
        return {str(key): html.escape(value, quote=True) for key, value in custom_labels.items()}
    return {}


# Keys added after custom ui_labels became a supported input. Requiring them would reject every
# label set authored before they existed, silently dropping that page back to English -- so they
# are optional, and localize_static_page falls back to the English source string when absent.
OPTIONAL_UI_LABEL_KEYS = frozenset({
    "anchor_serves_label",
    "fig_walking_caption",
    "fig_map_caption",
    "fig_budget_caption",
    "fig_shape_caption",
    "fig_longest_walk",
    "photo_credit_label",
    "photo_source_label",
    "unverified_banner_title",
    "unverified_banner_body",
    # Added with the booking-page fixes: the stated budget cap, dining opening hours and
    # their provenance, and the three intake spellings of entry status.
    "round_trip_in",
    "budget_cap",
    "venue_hours_label",
    "rating_label",
    "rating_none",
    "rating_reviews",
    "hours_verified",
    "hours_researched",
    "hours_unverified",
    "hours_closed_unknown",
    "entry_not_required",
    "entry_required_held",
    "entry_required_to_apply",
    # Added with the per-activity ticket note, and optional for the same reason: making it
    # required would make REQUIRED_UI_LABEL_KEYS reject every label set written before it
    # existed, and a rejected set drops the entire page back to English -- trading one
    # untranslated prefix for a hundred translated ones.
    "ticket_note_label",
    # Added with the rail/coach/ferry category, the traveller-constraints panel, the provider
    # rationale and the ten formerly-hardcoded Chinese strings. Optional for the reason stated
    # above and demonstrated by this exact slip: requiring them hard-failed every French,
    # Japanese and Spanish plan already saved in a workspace, naming labels for a booking
    # category those trips do not contain and their author had never heard of.
    "group_ground",
    "station_access",
    "constraints_heading",
    "constraints_severity",
    "constraints_dietary",
    "constraints_mobility",
    "constraints_walk_cap",
    "constraints_card",
    # Added with the traveller-preferences panel, optional for the reason two comments above
    # spell out: making a new key required rejects every label set written before it existed,
    # and a rejected set drops the whole page back to English.
    "preferences_heading",
    "preferences_direction",
    "preferences_avoid",
    # Added with the gate stamp, optional on the same terms as everything above it.
    "gates_label",
    "gates_caveat",
    "why_providers",
    "pill_flight",
    "pill_hotel",
    "pill_ticket",
    "pill_car",
    "pill_ground",
    "schematic_aria",
    "schematic_start",
    "schematic_end",
    "nonstop",
    "nonstop_ground",
    "stops_suffix",
    "unit_km",
})
REQUIRED_UI_LABEL_KEYS = frozenset(labels_for("zh-CN")) - OPTIONAL_UI_LABEL_KEYS


def has_builtin_interface_language(language: object) -> bool:
    normalized = str(language).casefold()
    return normalized.startswith(("zh", "en")) or "chinese" in normalized or "english" in normalized or "中文" in normalized


def localize_enum_values(page: str, labels: dict[str, str]) -> str:
    """Translate machine enum values that are printed as visible text.

    Each substitution is anchored on the exact markup the renderer emits, because the
    same tokens also appear inside `data-*` attributes and URLs, which must stay
    machine-readable.  Without this pass a Chinese page shows `flight`, `dinner`, or
    `public-transit` next to fully translated headings.
    """
    states = {value: labels[f"state_{value}"] for value in BOOKING_STATES}
    meals = {value: labels[f"meal_{value}"] for value in MEAL_TYPES}
    arrivals = {value: labels[f"arrival_{value}"] for value in ARRIVAL_MODES}
    modes = {"self-drive": labels["mode_self_drive"], "public-transit": labels["mode_public_transit"]}
    categories = {value: labels[f"cat_{value}"] for value in BUDGET_CATEGORIES}

    # The budget figure prints its own legend and alt text, and both were built from the raw
    # category keys while the breakdown table beside them was translated. A zh page therefore
    # read "市内交通: €47–62" in the table and "local_transport 54" in the chart directly above
    # it -- the same fact in two languages, from one untranslated call site.
    #
    # Scoped to that one figure rather than applied page-wide, which is not tidiness. An
    # unscoped version of this rewrote author prose: a note reading "分类如下: food 120;
    # accommodation 300; local_transport 54" came back with the first two translated and the
    # third left alone, because only the first two happened to be followed by a semicolon.
    # Half a sentence in each language is worse than the defect being fixed.
    category_alternation = "|".join(sorted(BUDGET_CATEGORIES, key=len, reverse=True))

    def localize_budget_figure(figure: str) -> str:
        # Inside the figure every "<category> <number>" IS a legend entry -- in the pv-key spans
        # and again in the svg aria-label/<title>, where a screen reader reads it aloud.
        # Two prefixes, because the same token is punctuated differently in the two places it
        # appears: `">flight 225` opens a legend span with no space, while `: flight 225;`
        # separates entries inside the aria-label. A single lookbehind cannot cover both -- it
        # would have to be variable-width -- and the first version silently translated only the
        # aria-label while the visible legend beside it stayed English.
        return re.sub(
            r'(">|[>;:] )(' + category_alternation + r')(?= \d)',
            lambda match: f"{match.group(1)}{categories[match.group(2)]}",
            figure,
        )

    page = re.sub(
        r'<figure class="pv-figure pv-budget">.*?</figure>',
        lambda match: localize_budget_figure(match.group(0)),
        page,
        flags=re.S,
    )

    page = re.sub(
        r'(<p class="eyebrow">Plan status · )(' + "|".join(BOOKING_STATES) + r')(</p>)',
        lambda match: f"{match.group(1)}{states[match.group(2)]}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(<p class="meta">Arrival: )(' + "|".join(ARRIVAL_MODES) + r')( · Pace: )',
        lambda match: f"{match.group(1)}{arrivals[match.group(2)]}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(<strong>)(self-drive|public-transit)(</strong><span>Ground-mobility plan</span>)',
        lambda match: f"{match.group(1)}{modes[match.group(2)]}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(<h2>Overall transport</h2><p>)(self-drive|public-transit)',
        lambda match: f"{match.group(1)}{modes[match.group(2)]}",
        page,
    )
    page = re.sub(
        r'(<article class="fact budget-item" data-budget-category="[a-z_]+"><strong>)([a-z_]+)(: )',
        lambda match: f"{match.group(1)}{categories.get(match.group(2), match.group(2))}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(<li class="unpriced-category">)([a-z_]+)(</li>)',
        lambda match: f"{match.group(1)}{categories.get(match.group(2), match.group(2))}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(<article class="dining-stop" data-meal="[a-z]+"><p class="eyebrow">)(' + "|".join(MEAL_TYPES) + r')( · )',
        lambda match: f"{match.group(1)}{meals[match.group(2)]}{match.group(3)}",
        page,
    )
    entry_labels = {
        "no_visa_required": labels["entry_no_visa_required"],
        "visa_required": labels["entry_visa_required"],
        "unverified": labels["entry_unverified"],
        # Optional keys, so a ui_labels set authored before the intake spellings existed keeps
        # validating instead of failing the whole page back to English.
        "not_required": labels.get("entry_not_required", "No visa required"),
        "required_held": labels.get("entry_required_held", "Visa required — already held"),
        "required_to_apply": labels.get("entry_required_to_apply", "Visa required — not yet applied for"),
    }
    page = re.sub(
        r'(<strong class="entry-status">)(' + "|".join(ENTRY_STATUSES) + r')(</strong>)',
        lambda match: f"{match.group(1)}{entry_labels[match.group(2)]}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(<strong>Ticket status: </strong>)(' + "|".join(BOOKING_STATES) + r')',
        lambda match: f"{match.group(1)}{states[match.group(2)]}",
        page,
    )
    page = re.sub(
        r'( · )(' + "|".join(BOOKING_STATES) + r')(</li>)',
        lambda match: f"{match.group(1)}{states[match.group(2)]}{match.group(3)}",
        page,
    )
    return page


def localize_static_page(page: str, language: object, custom_labels: object = None) -> str:
    """Localize standard renderer copy; user content remains escaped throughout rendering."""
    labels = labels_for(language, custom_labels)
    if not labels:
        return page

    page = localize_enum_values(page, labels)
    replacements = static_replacements(labels)
    return _apply_replacements(page, replacements, labels)


def static_replacements(labels: dict[str, str]) -> dict[str, str]:
    """Every renderer-owned English string, mapped to its localized form.

    Exposed rather than kept local so a test can assert that none of these KEYS survives into a
    localized page. That is the backstop for a hole this table itself has: the i18n validator
    fails only English it already knows about, so a caption invented after the validator was
    written is invisible to it. Four figure captions shipped English onto a Chinese page exactly
    that way, and every gate stayed green. The keys of this dict are, by construction, the
    complete list of what must never survive.
    """
    replacements = {
        # Renderer-owned fallbacks first: they are substrings of later, shorter keys.
        "Direct provider": labels["direct_provider_fallback"],
        "Map provider": labels["map_provider_fallback"],
        "Comparison platform": labels["comparison_platform_fallback"],
        "Not supplied": labels["not_supplied"],
        ">Review option<": f">{labels['review_option']}<",
        ">Open this segment in maps<": f">{labels['segment_map']}<",
        ">Budget at a glance<": f">{labels['budget']}<",
        ">Comparable cost per person<": f">{labels['total']}<",
        ">Cap per person<": f">{labels.get('budget_cap', 'Cap per person')}<",
        ">Included assumptions<": f">{labels['included']}<",
        ">Ground-mobility plan<": f">{labels['ground']}<",
        ">Browse options — no purchase made<": f">{labels['browse']}<",
        ">Booking access checks<": f">{labels['booking_access_checks']}<",
        ">Source<": f">{labels['source_link']}<",
        ">Overall transport<": f">{labels['overall_transport']}<",
        ">Open overall route<": f">{labels['overall_route']}<",
        ">Open the airport transfer route — daily segments are below<": f">{labels['overall_route_overview']}<",
        ">Sources, confidence, and recheck list<": f">{labels['sources']}<",
        ">Sources used<": f">{labels['sources_used']}<",
        ">Recheck before purchase<": f">{labels['recheck_before']}<",
        ">No purchase options were requested for this plan.<": f">{labels['no_purchase_options']}<",
        '<span class="pill">flight<': f'<span class="pill">{labels.get("pill_flight", "flight")}<',
        '<span class="pill">hotel<': f'<span class="pill">{labels.get("pill_hotel", "hotel")}<',
        '<span class="pill">ticket<': f'<span class="pill">{labels.get("pill_ticket", "ticket")}<',
        '<span class="pill">car<': f'<span class="pill">{labels.get("pill_car", "car")}<',
        '<span class="pill">ground<': f'<span class="pill">{labels.get("pill_ground", "ground")}<',
        'aria-label="Schematic route in visit order"': f'aria-label="{labels.get("schematic_aria", "Schematic route in visit order")}"',
        '>Start</text>': f'>{labels.get("schematic_start", "Start")}</text>',
        '>End</text>': f'>{labels.get("schematic_end", "End")}</text>',
        '>Start</tspan>': f'>{labels.get("schematic_start", "Start")}</tspan>',
        '>End</tspan>': f'>{labels.get("schematic_end", "End")}</tspan>',
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
        "Station and access: ": labels.get("station_access", "Station and access: "),
        "Location and access: ": labels["hotel_location"],
        "Guest rating: ": labels.get("guest_rating_label", "Guest rating: "),
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
        "No meal recommendation was researched.": labels["no_meal_recommendation"],
        "Check current opening hours before switching.": labels["opening_hours_recheck"],
        "Rating: ": labels.get("rating_label", "Rating: "),
        "no public rating": labels.get("rating_none", "no public rating"),
        " reviews": labels.get("rating_reviews", " reviews"),
        # The hours provenance word is anchored inside its own span: bare "researched" also
        # appears as a source-confidence default, and a loose replacement would translate that
        # unrelated word too.
        **{
            f'<span class="dining-hours-status">{english}</span>':
                f'<span class="dining-hours-status">{labels.get(HOURS_STATUS_LABEL_KEYS[status], english)}</span>'
            for status, english in HOURS_STATUS_ENGLISH.items()
        },
        "Opening hours: ": labels.get("venue_hours_label", "Opening hours: "),
        # Anchored on the whole paragraph the renderer emits rather than on the bare word:
        # "Ticket: " is ordinary prose that an author may well have written inside an activity
        # detail or a source note, and a loose replacement would translate the traveller's own
        # text. Same reasoning as the dining hours-status span directly above.
        '<p class="meta ticket-note"><strong>Ticket: </strong>':
            f'<p class="meta ticket-note"><strong>{labels.get("ticket_note_label", "Ticket: ")}</strong>',
        " per person · ": f" {labels['per_person_suffix']} · ",
        ">Not fact-checked<": f">{labels.get('unverified_banner_title', 'Not fact-checked')}<",
        # NOTE: this key must stay byte-identical to the sentence emitted at :2079. It is a
        # substitution table keyed on the rendered English, so editing the banner without editing
        # this key leaves English on a Chinese page and the i18n gate then fails the file.
        ("No verification pass is recorded for this plan. Its fares, opening hours, entry rules, "
         "and availability have not been checked against operators or official sources. Treat "
         "every figure as an estimate and verify before booking."):
            labels.get("unverified_banner_body",
                       "No verification pass is recorded for this plan. Its fares, opening hours, "
                       "entry rules, and availability have not been checked against operators or "
                       "official sources. Treat every figure as an estimate and verify before "
                       "booking."),
        # NOTE: keyed byte-identically on the sentence emitted by destination_anchor_cards.
        # Editing one without the other leaks English onto a Chinese page, and the i18n gate
        # then fails the file -- which is how this table is meant to work, but the failure is
        # cheaper to avoid than to debug.
        "<strong>You asked for: </strong>":
            f"<strong>{labels.get('anchor_serves_label', 'You asked for: ')}</strong>",
        # The photo credit. Not politeness: these are CC BY / CC BY-SA files and the attribution
        # is the condition under which they may sit in this file at all, so it localizes like any
        # other renderer sentence rather than staying English on a Chinese page.
        "<figcaption class=\"photo-credit\">Photo credit: ":
            f"<figcaption class=\"photo-credit\">{labels.get('photo_credit_label', 'Photo credit')}: ",
        " · via Wikimedia, from the article ":
            f" · {labels.get('photo_source_label', 'via Wikimedia, from the article')} ",
        # The four figure captions. They are also each figure's SVG <title>, so one entry
        # localizes both the visible caption and the accessible name. Added here because the
        # i18n gate only fails English it already knows about -- a caption invented after the
        # gate was written is invisible to it, which is how these shipped English on a Chinese
        # page in the first draft and nothing fired.
        "Minutes on foot per day: connecting legs plus time on foot at each stop":
            labels.get("fig_walking_caption",
                       "Minutes on foot per day: connecting legs plus time on foot at each stop"),
        "Relative positions, in visit order. Use the map button to navigate.":
            labels.get("fig_map_caption",
                       "Relative positions, in visit order. Use the map button to navigate."),
        "Share of the per-person total, and how much of the cap it uses":
            labels.get("fig_budget_caption",
                       "Share of the per-person total, and how much of the cap it uses"),
        "Fixed points across the day":
            labels.get("fig_shape_caption", "Fixed points across the day"),
        " · Longest single walk: ":
            f" · {labels.get('fig_longest_walk', 'Longest single walk')}: ",
        ">Your constraints<": f">{labels.get('constraints_heading', 'Your constraints')}<",
        "Allergy severity: ": labels.get("constraints_severity", "Allergy severity: "),
        "Dietary needs: ": labels.get("constraints_dietary", "Dietary needs: "),
        "Mobility: ": labels.get("constraints_mobility", "Mobility: "),
        "Maximum continuous walking: ": labels.get("constraints_walk_cap", "Maximum continuous walking: "),
        "Allergy card — show this to staff: ": labels.get("constraints_card", "Allergy card — show this to staff: "),
        ">What you asked for<": f">{labels.get('preferences_heading', 'What you asked for')}<",
        "Experience direction: ": labels.get("preferences_direction", "Experience direction: "),
        "Asked to avoid: ": labels.get("preferences_avoid", "Asked to avoid: "),
        "Structure checks passed: ": labels.get("gates_label", "Structure checks passed: "),
        "They prove the plan agrees with itself, never that its facts are true.":
            labels.get("gates_caveat",
                       "They prove the plan agrees with itself, never that its facts are true."),
        "Why these providers: ": labels.get("why_providers", "Why these providers: "),
        "Plan status · ": labels["plan_status_prefix"],
        "Arrival: ": labels["arrival"],
        " · Pace: ": f" · {labels['pace']}",
        " · Currency: ": f" · {labels['currency']}",
        " · Research last checked: ": f" · {labels['last_checked']}",
        "Provider: ": labels["provider"],
        # After the labelled "Provider: " key, so the bare option-card fallback is caught too.
        "Provider": labels["provider_fallback"],
        " · Compared via: ": f" · {labels['compared']}",
        " · Checked: ": f" · {labels['checked']}",
        " · Source: ": f" · {labels['source']}",
        " guest(s) · ": f" {labels['guest']} · ",
        " room(s)": f" {labels['room']}",
        "Fare conditions: ": labels["fare_conditions_label"],
        "Ticket status: ": labels["ticket_state_label"],
        "Only one option shown: ": labels["single_option_label"],
        "Fallback: ": labels["route_fallback_label"],
        "Walking across the day: ": labels["walking_burden_label"],
        "Platform selection: ": labels["platform_note_label"],
        ">Unpriced categories<": f">{labels['unpriced_categories']}<",
        ">Planning assumptions<": f">{labels['planning_assumptions']}<",
        ">On this page<": f">{labels['nav_label']}<",
        ">Budget<": f">{labels['nav_budget']}<",
        ">Options<": f">{labels['nav_options']}<",
        ">Transport<": f">{labels['nav_transport']}<",
        ">Sources<": f">{labels['nav_sources']}<",
        "<h2>Entry eligibility</h2>": f"<h2>{labels['entry_heading']}</h2>",
        "Basis: ": labels["entry_basis"],
        "<h3>Flight options</h3>": f"<h3>{labels['group_flight']}</h3>",
        "<h3>Rail, coach and ferry options</h3>": f"<h3>{labels.get('group_ground', 'Rail, coach and ferry options')}</h3>",
        "<h3>Accommodation options</h3>": f"<h3>{labels['group_hotel']}</h3>",
        "<h3>Ticket options</h3>": f"<h3>{labels['group_ticket']}</h3>",
        "<h3>Rental-car options</h3>": f"<h3>{labels['group_car']}</h3>",
        ">Start<": f">{labels['route_start']}<",
        ">End<": f">{labels['route_end']}<",
        "No separate ticket is required or it was not verified.": labels["no_separate_ticket"],
    }
    return replacements


def _apply_replacements(page: str, replacements: dict[str, str], labels: dict[str, str]) -> str:
    for source, target in replacements.items():
        page = page.replace(source, target)

    page = re.sub(
        r'(data-booking-purpose="(?:review-option|rental-search)"[^>]*>)Review option in ([^<]+)(</a>)',
        lambda match: f"{match.group(1)}{labels['review_option_provider'].replace('{provider}', match.group(2))}{match.group(3)}",
        page,
    )
    page = re.sub(
        r'(data-booking-purpose="direct-provider"[^>]*>)Review direct provider in ([^<]+)(</a>)',
        lambda match: f"{match.group(1)}{labels['direct_provider'].replace('{provider}', match.group(2))}{match.group(3)}",
        page,
    )
    page = re.sub(
        # The platform name sits inside the label so a card headed "Transavia" cannot open
        # Skyscanner without saying so, the way the hotel button already names Booking.com.
        # It is captured rather than translated: a provider name is a proper noun.
        r'(data-booking-purpose="round-trip-search"[^>]*>)Search round trip in ([^—<]+) — ([^<]+)(</a>)',
        lambda match: (f"{match.group(1)}{labels['round_trip_in'].replace('{provider}', match.group(2).strip())}"
                       f" — {match.group(3).replace(' to ', labels['to'])}{match.group(4)}"),
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
        r'\bWalk (\d+) min\b',
        lambda match: f"{labels['walk']} {match.group(1)}{labels['minute']}",
        page,
    )
    page = re.sub(
        r'\b(\d+) transfer\(s\)',
        lambda match: f"{match.group(1)}{labels['transfers']}",
        page,
    )
    page = re.sub(r'(\d+) minutes?\b', lambda match: f"{match.group(1)}{labels['minute']}", page)
    page = re.sub(r'(\d+) min\b', lambda match: f"{match.group(1)}{labels['minute']}", page)
    # These were hardcoded Chinese inside a function every non-English page runs, so a French or
    # Japanese itinerary shipped "82公里" on every segment and "直飞" on every flight card while all
    # four gates reported it valid -- the exact failure the i18n rule exists to prevent, in the one
    # branch that rule cannot see.
    #
    # Two refinements since. First, a through train is not a 直飞 -- that word means specifically
    # "direct by air" -- so the ground branch counts "change(s)", the right English for rail and a
    # marker this flight-shaped substitution cannot claim. `stops_suffix` ("次中转") is
    # vehicle-neutral, so only the zero case needed a twin.
    #
    # Second, the zero cases substitute ONLY when the label exists. `labels.get("nonstop",
    # "non-stop")` printed an English word on a French page that no gate pattern recognises, and
    # "non-stop" and "direct" are both too common in real prose to be safely matchable. Falling
    # through leaves the count for the sibling rule below, and if that label is missing too the
    # page keeps `0 stop(s)` / `0 change(s)`, which RENDERER_ENGLISH_TEXT does catch. Elsewhere an
    # English fallback keeps an incomplete label set readable; here it would keep it silent, and
    # the whole point of the rule is that an untranslated label must be loud.
    if labels.get("nonstop_ground"):
        page = re.sub(r'\b0 change\(s\)', labels["nonstop_ground"], page)
    if labels.get("nonstop"):
        page = re.sub(r'\b0 stop\(s\)', labels["nonstop"], page)
    page = re.sub(r'\b(\d+) change\(s\)',
                  lambda match: f"{match.group(1)}{labels.get('stops_suffix', ' stop(s)')}", page)
    page = re.sub(r'\b(\d+) stop\(s\)',
                  lambda match: f"{match.group(1)}{labels.get('stops_suffix', ' stop(s)')}", page)
    page = re.sub(r'(\d+(?:\.\d+)?) km\b',
                  lambda match: f"{match.group(1)}{labels.get('unit_km', ' km')}", page)
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
        r'(<span class="day-nav-day">)Day (\d+)(</span>)',
        lambda match: f'{match.group(1)}{labels["day"]}{match.group(2)}{labels["day_suffix"]}{match.group(3)}',
        page,
    )
    page = page.replace('aria-label="On this page"', f'aria-label="{labels["nav_label"]}"')
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
        r'>Open the airport transfer route in ([^<]+) — daily segments are below<',
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
    page = re.sub(
        rf'(<strong>{re.escape(labels["availability"])}</strong>)(available|limited|unknown)',
        lambda match: f"{match.group(1)}{access_statuses[match.group(2)]}",
        page,
    )
    page = page.replace('<h4>Route by segment</h4>', f'<h4>{labels["route_by_segment"]}</h4>')
    page = page.replace('<h3>Tickets and recheck</h3>', f'<h3>{labels["tickets"]}</h3>')
    page = page.replace('<time>Flexible</time><div><strong>Free time</strong>', f'<time>{labels["flexible_time"]}</time><div><strong>{labels["free_time"]}</strong>')
    page = page.replace('<time>Flexible</time>', f'<time>{labels["flexible_time"]}</time>')
    page = page.replace('>Review reservation<', f'>{labels["review_reservation"]}<')
    page = page.replace('>View source<', f'>{labels["view_source"]}<')
    page = re.sub(
        r'<span class="pill">Day ([^<]+)<',
        lambda match: f'<span class="pill">{labels["day"]}{match.group(1)}{labels["day_suffix"]}<',
        page,
    )
    # Price provenance is decision-critical: do not leave machine enum values in an
    # otherwise localized checkout-facing page. These replacements only affect
    # renderer-owned visible text, never URLs or data attributes.
    for price_status in PRICE_STATUSES:
        status_label = labels.get(f"price_{price_status}", price_status)
        page = page.replace(f"</strong>{price_status}", f"</strong>{status_label}")
        page = page.replace(f">{price_status} ·", f">{status_label} ·")
    return page


def as_number(value: object) -> float:
    """A number, or 0.0. Figures must not raise on a field the plan left blank."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def as_list(value: object) -> list:
    """Normalise a field the contract declares as a list of strings.

    Iterating a str yields its characters, so a field written as one string instead of a
    one-element list renders as every character joined by the separator: a whole paragraph
    came out as "这 · 是 · 路 · 线 · 概 · 览" on a delivered page, and every gate passed it
    because the value was a perfectly good string and the join was perfectly good code.
    A lone string is semantically one note, so read it as one rather than exploding it --
    the renderer must not be able to produce that output at all. validate_plan reports the
    type mismatch separately so the data still gets fixed at the source.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def as_text(value: object, fallback: str = "Not supplied") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def esc(value: object, fallback: str = "Not supplied") -> str:
    return html.escape(as_text(value, fallback), quote=True)


def stamp(value: object) -> str:
    """Show a check time a person can read; the machine form stays in data attributes."""
    if isinstance(value, str) and value.strip():
        try:
            if len(value.strip()) == 10:
                return html.escape(date.fromisoformat(value.strip()).isoformat(), quote=True)
            moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return esc(value)
        return html.escape(moment.strftime("%Y-%m-%d %H:%M"), quote=True)
    return esc(value)


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


def is_one_of(value: object, allowed: set[str]) -> bool:
    """`value not in {"available", ...}` raises TypeError when the author wrote a list or a dict,
    so a plausible typo killed validate_plan with a bare traceback instead of printing the
    one-line reason the rule exists to print. Non-strings are simply not the allowed value."""
    return isinstance(value, str) and value in allowed


def has_search_fields(value: object, required: set[str]) -> bool:
    """Same crash, one line over: `set(value)` on author-supplied elements raised
    `TypeError: unhashable type: 'dict'` for `[{"origin": "Leiden"}]` -- a believable slip, since
    the itinerary fields beside it really are objects. Non-string elements cannot name a field."""
    if not isinstance(value, list):
        return False
    return required.issubset({field for field in value if isinstance(field, str)})


def dedupe_key(value: object) -> object:
    """Duplicate detection puts author-supplied ids and URLs into a set. Anything unhashable
    reaches its repr instead of crashing, which still distinguishes two different values."""
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def is_nonnegative_money_range(low: object, high: object) -> bool:
    """Keep displayed costs comparable and prevent inverted or text-only ranges."""
    numeric = (int, float)
    if any(not isinstance(value, numeric) or isinstance(value, bool) for value in (low, high)):
        return False
    return low >= 0 and high >= low


def is_nonnegative_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0


def format_amount(value: object) -> str:
    """Group thousands so a five-figure fare is readable at a glance."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    return as_text(value)


def money(low: object, high: object, currency: object) -> str:
    code = as_text(currency, "").strip().upper()
    symbol = CURRENCY_SYMBOLS.get(code)
    priced = lambda text: f"{symbol}{text}" if symbol else f"{code} {text}".strip()
    if low is None and high is None:
        return "Price not currently verified"
    if low == high or high is None:
        return priced(format_amount(low))
    if low is None:
        return f"Up to {priced(format_amount(high))}"
    return f"{priced(format_amount(low))}–{format_amount(high)}"


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
    if kind == "ground":
        return f"{as_text(item.get('provider'))}: {as_text(item.get('origin_station'))} → {as_text(item.get('destination_station'))}"
    if kind == "hotel":
        return f"{as_text(item.get('property_name'))} · {as_text(item.get('stay_location'))}"
    if kind == "ticket":
        return as_text(item.get("attraction_name"))
    return f"{as_text(item.get('provider'))} · {as_text(item.get('vehicle_class'))}"


def booking_details(kind: str, item: dict) -> str:
    if kind == "ground":
        # Without this branch option_card printed its no-data fallback, "Conditions require
        # recheck", in the slot directly under the fare -- on every ground card, however fully it
        # was researched, and three rows above the line that actually states the conditions. A
        # warning nobody earned, sitting under a price the traveller is about to act on.
        return " · ".join(
            part
            for part in (
                " → ".join(part for part in (as_text(item.get("outbound_date"), ""), as_text(item.get("return_date"), "")) if part),
                as_text(item.get("travel_class"), ""),
                as_text(item.get("seat_reservation"), ""),
            )
            if part
        )
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
        # `ticket_status` is rendered as a labelled row in option_detail_list; repeating
        # `timed_entry_or_reservation` here as well printed the same sentence twice.
        return as_text(item.get("timed_entry_or_reservation"), "")
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


def flight_leg_summary(leg: object, kind: str = "flight") -> str:
    if not isinstance(leg, dict):
        return ""
    # Rail counts changes, not stops, and the distinction is not cosmetic: the localizer rewrites
    # "0 stop(s)" to 直飞 page-wide with no notion of booking type, so a through train on a
    # Chinese page was labelled "direct flight".
    stop_noun = "change(s)" if kind == "ground" else "stop(s)"
    return " · ".join(
        part
        for part in (
            as_text(leg.get("service_identifier"), ""),
            " → ".join(part for part in (as_text(leg.get("departure_local"), ""), as_text(leg.get("arrival_local"), "")) if part),
            minutes(leg.get("duration_minutes")),
            f"{as_text(leg.get('stops'))} {stop_noun}" if leg.get("stops") is not None else "",
            as_text(leg.get("connection_or_terminal_note"), ""),
        )
        if part
    )


def option_detail_list(kind: str, item: dict) -> str:
    rows: list[str] = []
    if kind == "ground":
        # Same rows as a flight, because the traveller's questions are the same ones: which
        # service, when, how long, how many changes, what the fare lets them do, and how they get
        # from the station into town. `station_transfer_note` is the ground analogue of the
        # airport-transfer note that stops a cheap-looking flight hiding an impractical arrival.
        rows.extend(
            (
                f'<li><strong>Outbound: </strong>{esc(flight_leg_summary(item.get("outbound_itinerary"), "ground"), "Not supplied")}</li>',
                f'<li><strong>Return: </strong>{esc(flight_leg_summary(item.get("return_itinerary"), "ground"), "Not supplied")}</li>',
                f'<li><strong>Fare conditions: </strong>{esc(item.get("material_conditions"))}</li>',
                f'<li><strong>Station and access: </strong>{esc(item.get("station_transfer_note"))}</li>',
                # Required by validate_plan and, until this line, printed nowhere -- so the card
                # showed a fare with no hint that it was an estimate, priced weeks ago, on limited
                # inventory, while the hotel and flight cards beside it said exactly that. Same
                # labels as the flight row, so they are already localized.
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{stamp(item.get("price_checked_at"))}</li>',
            )
        )
    if kind == "flight":
        rows.extend(
            (
                f'<li><strong>Outbound: </strong>{esc(flight_leg_summary(item.get("outbound_itinerary")), "Not supplied")}</li>',
                f'<li><strong>Return: </strong>{esc(flight_leg_summary(item.get("return_itinerary")), "Not supplied")}</li>',
                f'<li><strong>Fare conditions: </strong>{esc(item.get("material_conditions"))}</li>',
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{stamp(item.get("price_checked_at"))}</li>',
                f'<li>{esc(item.get("airport_transfer_note"))}</li>',
            )
        )
    elif kind == "hotel":
        rows.extend(
            (
                # The guest score sits above location and price because it is the field that
                # decides whether the other two are worth having, and because it was collected
                # and rendered nowhere at all until a traveller asked why hotels were not judged
                # the way restaurants are.
                hotel_rating_line(item),
                f'<li><strong>Location and access: </strong>{esc(item.get("neighborhood"))} · {esc(item.get("address_or_location_reference"))} · {esc(item.get("arrival_access_note"))} · {esc(item.get("key_area_access_note"))}</li>',
                f'<li><strong>Why it fits: </strong>{esc(item.get("selection_rationale"))}</li>',
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{stamp(item.get("price_checked_at"))}</li>',
            )
        )
    elif kind == "ticket":
        rows.extend(
            (
                f'<li><strong>Ticket status: </strong>{esc(item.get("ticket_status"))}</li>',
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{stamp(item.get("price_checked_at"))}</li>',
            )
        )
    elif kind == "car":
        rows.extend(
            (
                f'<li>{esc(item.get("pickup_location"))} → {esc(item.get("dropoff_location"))} · {esc(item.get("pickup_time"))} → {esc(item.get("dropoff_time"))}</li>',
                f'<li>{esc(item.get("transmission"))} · {esc(item.get("capacity_note"))} · {esc(item.get("insurance_excess"))}</li>',
                f'<li><strong>Availability: </strong>{esc(item.get("availability_status"))} · <strong>Price status: </strong>{esc(item.get("price_status"))} · <strong>Price checked: </strong>{stamp(item.get("price_checked_at"))}</li>',
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
            f'<p class="meta">{esc(item.get("price_status"))} · {stamp(item.get("checked_at"))} · {esc(item.get("note"))}</p></article>'
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
        "Vehicle price per day: " if kind == "car" else
        "Price per person, round trip: " if kind == "ground" else ""
    )
    stay_total = ""
    if kind == "hotel":
        stay_total = f'<p class="meta"><strong>Trip total for stay: </strong>{esc(money(item.get("trip_cost_low"), item.get("trip_cost_high"), item.get("currency")))}</p>'
    actions = [booking_link(kind, provider, checked_at, url, f"Review option in {provider}", "review-option")]
    direct_url = item.get("direct_review_url")
    if direct_url and direct_url != url:
        direct_provider = as_text(item.get("direct_provider"), "Direct provider")
        actions.append(booking_link(kind, direct_provider, item.get("comparison_checked_at") or checked_at, direct_url, f"Review direct provider in {direct_provider}", "direct-provider"))
    if kind == "ground":
        # Rail, coach and ferry get the flight treatment because the traveller needs the same
        # things: which service, when it leaves, how long, how many changes, and what the fare
        # conditions are. Before this they got nothing -- a Japan or European rail trip shipped as
        # "booking-ready" with no way to reach, price-check or availability-check the single
        # largest and most time-sensitive thing on it, while the hotels had three compared
        # candidates each.
        actions.insert(0, booking_link("ground", item.get("round_trip_search_provider") or comparison_platform or provider, item.get("round_trip_search_checked_at") or checked_at, item.get("round_trip_search_url"), f"Search round trip in {as_text(item.get('round_trip_search_provider') or comparison_platform or provider)} — {as_text(item.get('outbound_date'))} to {as_text(item.get('return_date'))}", "round-trip-search", item.get("round_trip_prefilled_fields") if isinstance(item.get("round_trip_prefilled_fields"), list) else None))
    if kind == "flight":
        actions.insert(0, booking_link("flight", item.get("round_trip_search_provider") or comparison_platform or provider, item.get("round_trip_search_checked_at") or checked_at, item.get("round_trip_search_url"), f"Search round trip in {as_text(item.get('round_trip_search_provider') or comparison_platform or provider)} — {as_text(item.get('outbound_date'))} to {as_text(item.get('return_date'))}", "round-trip-search", item.get("round_trip_prefilled_fields") if isinstance(item.get("round_trip_prefilled_fields"), list) else None))
    if kind == "hotel":
        actions.extend(hotel_comparison_links(item))
    if kind == "car":
        actions[0] = booking_link("car", provider, checked_at, url, f"Review option in {provider}", "rental-search", item.get("rental_search_prefilled_fields") if isinstance(item.get("rental_search_prefilled_fields"), list) else None)
    # A single option is allowed only with a researched reason; showing the reason is what
    # keeps the missing comparison legible instead of looking like an oversight.
    single_reason = (
        f'<p class="meta single-option-reason"><strong>Only one option shown: </strong>{esc(item.get("single_option_reason"))}</p>'
        if item.get("single_option_reason")
        else ""
    )
    # data-option-kind carries the machine enum so the delivery gate can attribute each button to
    # the card it sits in. The pill beside it says the same thing in the traveller's language and
    # is therefore unreadable to a gate on a translated page.
    return f'''<article class="option" data-option-kind="{attr(kind)}"><span class="pill">{attr(kind)}</span><h3>{esc(booking_title(kind, item))}</h3><p><strong>{price_label}</strong>{esc(price)}</p>{stay_total}<p>{esc(booking_details(kind, item), "Conditions require recheck")}</p>{option_detail_list(kind, item)}{single_reason}<p class="meta">Provider: {esc(provider)} · Compared via: {esc(comparison_platform)} · Checked: {stamp(checked_at)} · Source: {esc(item.get("source_type"))}</p>{"".join(actions)}</article>'''


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


def route_diagram(stops: list[object]) -> str:
    """Render the visit-order schematic as a vertical, text-sized ordered list.

    The previous horizontal SVG needed a ~720px minimum width to keep stop names
    legible, so inside its scroll box a phone silently showed only the first two stops
    of the day — the diagram looked complete and was not.  An ordered list reflows
    instead of truncating, keeps names at real font size, and stays selectable and
    readable by assistive technology.
    """
    names = [as_text(stop) for stop in stops if stop not in (None, "")]
    if len(names) < 2:
        names = ["Start", "End"]
    items = "".join(
        f'<li class="route-stop"><span class="route-stop-index" aria-hidden="true">{index}</span>'
        f'<span class="route-stop-name">{esc(name)}</span></li>'
        for index, name in enumerate(names, 1)
    )
    return f'<ol class="route-rail">{items}</ol>'


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
    overview_label = ("Open overall route" if overview_scope == "multi_stop"
                      else "Open the airport transfer route — daily segments are below")
    old_overview = f'<a class="map-link" data-map-scope="{attr(overview_scope)}" data-verified-at="{attr(overview.get("overall_map_checked_at"))}" href="{attr(overview.get("overall_route_map_url"))}" target="_blank" rel="noopener noreferrer">{overview_label}</a>'
    provider = as_text(overview.get("overall_map_provider"), "Map provider")
    overview_provider_label = (f"Open overall route in {provider}" if overview_scope == "multi_stop"
                               else f"Open the airport transfer route in {provider} — daily segments are below")
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
                # validate_plan REQUIRES fare_basis on every segment and nothing printed it, so on
                # the measured plan 15 segments carried "KVB single ticket Preisstufe 1b EUR 4.00,
                # kvb.koeln, checked 2026-08-05" and the page showed a bare number the traveller
                # had no way to question. A price with no basis is the black box the whole source
                # discipline exists to prevent.
                as_text(segment.get("fare_basis"), ""),
                as_text(segment.get("journey_instruction"), ""),
                as_text(segment.get("arrival_instruction"), ""),
                as_text(segment.get("fallback_note"), ""),
            )
            if part
        )
        primary = map_link(segment.get("map_provider"), segment.get("map_checked_at"), segment.get("verified_map_url"), f"Open this segment in {as_text(segment.get('map_provider'))}", segment_number=index, link_kind=as_text(segment.get("map_link_kind"), "directions"))
        rows.append(f'''<li class="route-segment" data-route-segment="{index}"><div><strong>{esc(segment.get("from"))} → {esc(segment.get("to"))}</strong><p class="meta">{esc(details)}</p></div>{primary}{alternative_map_links(segment.get("alternative_map_links"))}</li>''')
    return f'<ol class="segment-list">{"".join(rows)}</ol>'


def hotel_rating_line(item: dict) -> str:
    """The guest score, its count and where it was read, as a row in the option card."""
    status = str(item.get("guest_rating_status") or "").strip().casefold()
    if status == "none":
        reason = item.get("guest_rating_absence_reason")
        return (f'<li class="option-rating" data-rating-status="none"><strong>Guest rating: '
                f'</strong>no public rating{" · " + esc(reason) if reason else ""}</li>')
    value = item.get("guest_rating_value")
    if value is None:
        return ""
    scale = item.get("guest_rating_scale") or 10
    count = item.get("guest_rating_count")
    source = item.get("guest_rating_source")
    url = item.get("guest_rating_url")
    shown = f'{as_text(value)}/{as_text(scale)}'
    inner = (f'<a class="booking-link" data-booking-type="hotel" '
             f'data-provider="{attr(item.get("comparison_platform") or source)}" '
             f'data-verified-at="{attr(item.get("guest_rating_checked_at"))}" href="{attr(url)}" '
             f'target="_blank" rel="noopener noreferrer">{esc(shown)}</a>') if url else esc(shown)
    reviews = f' · {as_text(count)} reviews' if count is not None else ""
    origin = f' · {esc(source)}' if source else ""
    return (f'<li class="option-rating" data-rating-status="{attr(item.get("guest_rating_status"))}" '
            f'data-rating-value="{attr(value)}" data-rating-count="{attr(count)}">'
            f'<strong>Guest rating: </strong>{inner}{reviews}{origin}</li>')


def dining_rating_line(item: dict) -> str:
    """Print the venue's rating, its count and where it came from.

    The plan collected all of it and the page printed none of it, so a recommendation looked
    identical whether somebody had opened the venue's page or copied it out of a blog listicle.
    That is not hypothetical: a delivered plan shipped a dinner at a venue with no listing on
    any platform, and the traveller had no way to tell. The count travels with the value on
    purpose -- 4.8 from 12 reviews and 4.3 from 2,000 are different claims -- and a card that
    honestly has no rating says so rather than staying silent, because silence reads as an
    omission while "no public rating: market stalls are rated individually" reads as a decision.
    """
    status = str(item.get("rating_status") or "").strip().casefold()
    if status == "none":
        reason = item.get("rating_absence_reason")
        return (f'<p class="meta dining-rating" data-rating-status="none">'
                f'<strong>Rating: </strong>no public rating'
                f'{" · " + esc(reason) if reason else ""}</p>')
    value, scale, count = item.get("rating_value"), item.get("rating_scale"), item.get("rating_count")
    if value is None:
        return ""
    source = item.get("rating_source")
    url = item.get("rating_url")
    shown = f'{as_text(value)}/{as_text(scale or 5)}'
    reviews = f' · {as_text(count)} reviews' if count is not None else ""
    origin = f' · {esc(source)}' if source else ""
    body = (f'<strong>Rating: </strong>{esc(shown)}{reviews}{origin}')
    if url:
        # Classed rather than bare so check_link_targets.py follows it. The rating is the one
        # field on the card that testifies somebody opened the venue's page, and for a while it
        # was the least verified link on the page: the checker reads booking-link, dining-link
        # and map-link, and this anchor carried none of them.
        body = (f'<strong>Rating: </strong>'
                f'<a class="dining-link" data-dining-provider="{attr(item.get("rating_source"))}" '
                f'data-verified-at="{attr(item.get("rating_checked_at"))}" '
                f'href="{attr(url)}" target="_blank" rel="noopener noreferrer">{esc(shown)}</a>'
                f'{reviews}{origin}')
    return (f'<p class="meta dining-rating" data-rating-status="{attr(item.get("rating_status"))}" '
            f'data-rating-value="{attr(value)}" data-rating-count="{attr(count)}">{body}</p>')


def dining_hours_line(item: dict) -> str:
    """Show the researched opening hours, and say plainly when nobody verified them.

    The plan collected `venue_hours` and `hours_status` and the page printed neither, so a
    dinner card looked identical whether its hours had been confirmed with the venue or
    guessed -- which is how a 20:00 dinner survives at a place that closes at 17:00 on that
    weekday. Anything other than "verified" gets the warning treatment, because a quiet meta
    line at the bottom of a card is exactly the presentation that let this go unnoticed.
    """
    hours = item.get("venue_hours")
    raw_status = str(item.get("hours_status") or "").strip()
    if not hours and not raw_status:
        return ""
    status = raw_status.casefold()
    # An unrecognised status word is not evidence that anyone checked, so it reads as
    # unverified rather than being trusted or silently dropped.
    status_text = HOURS_STATUS_ENGLISH.get(status, HOURS_STATUS_ENGLISH["unverified"])
    treatment = "meta" if status == "verified" else "warning"
    window = f"{esc(hours)} · " if hours else ""
    return (
        f'<p class="{treatment} dining-hours" data-hours-status="{attr(raw_status)}">'
        f'<strong>Opening hours: </strong>{window}'
        f'<span class="dining-hours-status">{status_text}</span></p>'
    )


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
            f'{dining_rating_line(item)}'
            f'{dining_hours_line(item)}'
            f'<a class="dining-link" data-dining-provider="{attr(provider)}" data-verified-at="{attr(item.get("checked_at"))}" href="{attr(item.get("venue_url"))}" target="_blank" rel="noopener noreferrer">View restaurant in {esc(provider)}</a>'
            f'{reservation}{backup}</article>'
        )
    return f'<div class="dining-grid">{"".join(cards)}</div>'


def imagery_figure(entry: object, alt: object) -> str:
    """A verified photograph with the attribution its licence requires.

    The credit line is not decoration and not politeness: these are CC BY / CC BY-SA files, and
    the licence is the reason they may be in this file at all. The caption names the article the
    image came from rather than asserting "this is X", because what was verified is the
    provenance -- a lead image is chosen by editors and can show the subject from an angle nobody
    would caption that way.
    """
    entry = entry if isinstance(entry, dict) else {}
    source = entry.get("data_uri")
    if not source:
        return ""
    credit = " · ".join(str(part) for part in
                        (entry.get("artist"), entry.get("license")) if part)
    link = entry.get("file_url") or entry.get("page_url") or ""
    return (f'<figure class="anchor-photo"><img src="{attr(source)}" alt="{attr(alt)}" '
            f'loading="lazy" decoding="async">'
            f'<figcaption class="photo-credit">Photo credit: '
            + (f'<a href="{attr(link)}" target="_blank" rel="noopener noreferrer">{esc(credit)}</a>'
               if link else esc(credit))
            + f' · via Wikimedia, from the article {esc(entry.get("page"))}</figcaption></figure>')


def destination_anchor_cards(value: object, imagery: object = None) -> str:
    if not isinstance(value, list):
        return ""
    items = []
    ordered = sorted(
        (anchor for anchor in value if isinstance(anchor, dict)),
        key=lambda anchor: anchor.get("planned_day") if isinstance(anchor.get("planned_day"), int) else 10**6,
    )
    images = imagery if isinstance(imagery, dict) else {}
    positions = {id(a): i for i, a in enumerate(value) if isinstance(a, dict)}
    for anchor in ordered:
        if not isinstance(anchor, dict):
            continue
        photo = imagery_figure(images.get(f"anchor:{positions.get(id(anchor))}"),
                               anchor.get("name"))
        # The traveller stated what the trip was for; this is where the page answers them. The
        # link exists in the data either way, but a field only they can verify is worth nothing
        # while it stays in the JSON: they are the only reader who knows whether "old-town lanes"
        # is really what they meant by "街区漫步".
        serves = anchor.get("satisfies_preference")
        serves_line = (f'<p class="meta anchor-serves"><strong>You asked for: </strong>'
                       f'{esc(serves)}</p>') if serves else ""
        items.append(
            f'<article class="anchor"><span class="pill">Day {esc(anchor.get("planned_day"))}</span><h3>{esc(anchor.get("name"))}</h3>'
            f'<p>{esc(anchor.get("category"))} · {esc(anchor.get("neighborhood_or_area"))}</p>'
            f'<p>{esc(anchor.get("why_it_matters"))}</p>{serves_line}{photo}'
            f'<a class="anchor-link" data-verified-at="{attr(anchor.get("checked_at"))}" href="{attr(anchor.get("source_url"))}" target="_blank" rel="noopener noreferrer">View source</a></article>'
        )
    return f'<section id="destination-essentials" class="panel"><h2>Destination essentials</h2><div class="grid">{"".join(items)}</div></section>' if items else ""


def link_for_ticket(ticket: dict | None) -> str:
    if not ticket:
        return "No separate ticket is required or it was not verified."
    return f'<a class="booking-link" data-booking-type="ticket" data-provider="{attr(as_text(ticket.get("official_or_authorised_provider"), "Provider"))}" data-verified-at="{attr(as_text(ticket.get("checked_at")))}" href="{attr(ticket.get("review_url"))}" target="_blank" rel="noopener noreferrer">Review ticket: {esc(ticket.get("attraction_name"))}</a>'


def intake_context_errors(intake_context: object) -> list[str]:
    """Refuse a plan that will not say how its requirements were collected.

    This one is required rather than optional, and that is the whole point. SKILL.md already said
    to default to the HTML form and fall back to chat only when the traveller declines, and other
    harnesses read "default" as a preference and opened no form at all. An optional field changes
    nothing there: the agent that skipped the form is exactly the agent that omits the key, so the
    gate reports clean on the run that motivated it. Required, the shortcut has to be declared, and
    declaring it costs more than doing the intake properly.

    Each branch demands the evidence that it was allowed, not just its own name:

    - `html_form` names the saved intake file, which only the intake server writes.
    - `user_supplied` says what the traveller supplied instead, so "they told me already" is a
      claim with content rather than a shrug.
    - `chat_fallback` carries the traveller's own words declining the form, and when they said so.
      Their words, not a summary: this is the one branch the traveller has to authorise, and a
      paraphrase is indistinguishable from an assistant that decided for them.

    Placeholders are rejected outright. A skeleton's `TODO:` string satisfies "is this key
    present" and answers nothing, which is how a whole intake gate goes green on an empty form.

    Called from save_trip_deliverables.py rather than validate_plan, and deliberately: the
    requirement is about handing a plan to a traveller, not about rendering a draft. Putting it in
    validate_plan would fail new_plan_skeleton.py's own output -- a skeleton cannot know how the
    requirements were collected -- and retroactively invalidate every plan already in a workspace,
    which audit_workspace.py reads with this same validator.
    """
    errors: list[str] = []
    if intake_context is None:
        return ["intake_context is required: say how this plan's requirements were collected "
                "(method html_form | user_supplied | chat_fallback). The loopback HTML form is "
                "the required path; chat_fallback is legitimate only when the traveller declined "
                "it, and this field is where that shows."]
    if not isinstance(intake_context, dict):
        return ["intake_context must be an object."]

    method = intake_context.get("method")
    if method not in INTAKE_METHODS:
        return [f"intake_context.method must be one of: {', '.join(INTAKE_METHODS)}."]

    required = {
        "html_form": ("intake_file",),
        "user_supplied": ("source_note",),
        "chat_fallback": ("declined_verbatim", "declined_at"),
    }[method]
    for field in required:
        value = intake_context.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"intake_context.{field} is required when method is {method}.")
        elif any(marker in value for marker in ("TODO:", "example.invalid")):
            errors.append(f"intake_context.{field} still holds a placeholder, which answers nothing.")
        elif field != "declined_verbatim" and re.search(r"<[^<>\s]{1,60}>", value):
            # templates/final-trip-plan.json ships intake_file as
            # "<workspace>/plans/trip-intake-<timestamp>.json", and a template copied without
            # editing is a non-empty string carrying no TODO -- it would pass every check above.
            # Not applied to declined_verbatim: that is a human being quoted, and refusing their
            # sentence because it contains a bracket would be the gate inventing a defect.
            #
            # The bracketed run must contain no whitespace, and that is the whole difference
            # between a gate and a nuisance. Written as `<[^<>]{1,60}>` first, it refused
            # "Budget stated as < 2000 > per person" -- an ordinary sentence, rejected as a
            # placeholder, on the delivery path. Every placeholder this template actually ships
            # is a single token; prose that happens to compare two numbers is not.
            errors.append(f"intake_context.{field} is still the template's <bracketed> placeholder.")

    # What this deliberately does NOT do: require intake_file to resolve on disk. It was written
    # that way first, on the reasoning that claiming the form ran should cost producing what the
    # form writes -- and it made every legitimate re-save fail. A plan is a portable document: it
    # is re-rendered, replanned weeks later, audited from a moved workspace, restored from backup.
    # Tying its validity to a sibling file still existing on this machine blocks the traveller's
    # own plan for a reason that has nothing to do with how intake happened, and the way out the
    # error suggested was to relabel the method, i.e. to write something false. A forged path is
    # no harder than a forged file anyway, so the check bought little and broke much.
    # A record that contradicts itself is worse than a missing one, because it reads as evidence.
    # new_plan_skeleton.py accepted --from-intake together with --intake-method chat_fallback and
    # emitted exactly that: the traveller's declining words next to the path of the form intake
    # they supposedly never filled. Only one of those can be true.
    intake_file = intake_context.get("intake_file")
    if method != "html_form" and isinstance(intake_file, str) and intake_file.strip():
        errors.append(
            f"intake_context says method {method} but also names an intake_file "
            f"({intake_file.strip()}), which only the form server writes. Either the form was "
            "filled -- method html_form -- or it was not, and the file does not belong here.")

    if method == "chat_fallback":
        declined_at = intake_context.get("declined_at")
        if isinstance(declined_at, str) and declined_at.strip():
            if not is_iso_datestamp(declined_at):
                errors.append("intake_context.declined_at must be an ISO date or date-time.")
            elif declined_at.startswith("1970-01-01"):
                # new_plan_skeleton.py stamps every date it cannot know as the epoch, and its own
                # docstring lists that as a hole: 1970 is conspicuous on a page but no gate rejects
                # it. Here it would be a fabricated fact in the one record that says the traveller
                # authorised the shortcut, so this is the one date field where the sentinel is an
                # error rather than a visible blank.
                errors.append(
                    "intake_context.declined_at is still the skeleton's 1970-01-01 placeholder. "
                    "Write the date the traveller actually declined the form; a provenance record "
                    "with an invented date is worse than none.")
    return errors


def validate_plan(plan: dict) -> list[str]:
    errors: list[str] = []
    if not is_iso_datestamp(plan.get("generated_at")):
        errors.append("generated_at must be an ISO date or date-time.")
    if plan.get("plan_status") not in BOOKING_STATES:
        errors.append("plan_status must be idea, researched, held, or booked.")
    if not isinstance(plan.get("assumptions", []), list):
        errors.append("assumptions must be a list.")
    entry_context = plan.get("entry_context")
    if entry_context is not None:
        if not isinstance(entry_context, dict):
            errors.append("entry_context must be an object when present.")
        else:
            if entry_context.get("status") not in ENTRY_STATUSES:
                errors.append("entry_context.status must be one of: " + ", ".join(ENTRY_STATUSES) + ".")
            for field in ("summary", "traveler_basis", "source_url", "checked_at"):
                if not entry_context.get(field):
                    errors.append(f"entry_context.{field} is required when entry_context is present.")
            if entry_context.get("source_url") and not is_https(entry_context["source_url"]):
                errors.append("entry_context.source_url must be a safe HTTPS URL.")
            if entry_context.get("checked_at") and not is_iso_datestamp(entry_context["checked_at"]):
                errors.append("entry_context.checked_at must be an ISO date or date-time.")
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
    # Optional, and only checked when it is there: every plan authored before intake started
    # carrying constraints has no such key and must validate exactly as it did. What is checked
    # is the shape the downstream gates read -- a severity outside the enum or a walking cap
    # written as prose does not fail loudly, it fails silently, by leaving the mobility and
    # dietary checks with nothing to compare against.
    constraints = trip.get("traveler_constraints")
    if constraints is not None:
        if not isinstance(constraints, dict):
            errors.append("trip.traveler_constraints must be an object with the traveller's dietary, allergy, and mobility fields, or be omitted entirely.")
        else:
            severity = constraints.get("allergy_severity")
            if severity is not None and severity not in ALLERGY_SEVERITIES:
                errors.append("trip.traveler_constraints.allergy_severity must be one of: " + ", ".join(ALLERGY_SEVERITIES) + ".")
            walking_cap = constraints.get("max_continuous_walking_minutes")
            if walking_cap is not None and (
                not isinstance(walking_cap, int) or isinstance(walking_cap, bool) or walking_cap <= 0
            ):
                errors.append("trip.traveler_constraints.max_continuous_walking_minutes must be a positive whole number of minutes (for example 20), or null when the traveller stated no limit.")
            for field in ("dietary_or_religious_needs", "mobility_notes"):
                if constraints.get(field) is not None and not isinstance(constraints[field], list):
                    errors.append(f"trip.traveler_constraints.{field} must be a list of strings, so each need stays separately readable.")
    if trip.get("arrival_transport_mode") not in set(ARRIVAL_MODES):
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
    else:
        unknown_unverified = {str(item) for item in as_list(budget.get("unverified_categories"))} - set(BUDGET_CATEGORIES)
        if unknown_unverified:
            errors.append("budget.unverified_categories uses unsupported categories: " + ", ".join(sorted(unknown_unverified)) + ".")
    if isinstance(budget.get("included_categories"), list):
        unknown_included = {str(item) for item in as_list(budget.get("included_categories"))} - set(BUDGET_CATEGORIES)
        if unknown_included:
            errors.append("budget.included_categories uses unsupported categories: " + ", ".join(sorted(unknown_included)) + ".")
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
            if not is_one_of(item.get("price_status"), PRICE_STATUSES):
                errors.append(f"budget.breakdown[{number}].price_status must be researched_current, estimate, or user_confirmed.")
            if not is_iso_datestamp(item["checked_at"]):
                errors.append(f"budget.breakdown[{number}].checked_at must be an ISO date or date-time.")
            if item["category"] not in BUDGET_CATEGORIES:
                errors.append(f"budget.breakdown[{number}].category must be one of: " + ", ".join(BUDGET_CATEGORIES) + ".")
            if item["category"] in breakdown_categories:
                errors.append(f"budget.breakdown has duplicate category: {item['category']}.")
            breakdown_categories.add(item["category"])
        if isinstance(budget.get("included_categories"), list):
            missing_breakdown = {str(category) for category in budget["included_categories"]} - breakdown_categories
            if missing_breakdown:
                errors.append("budget.breakdown is missing included categories: " + ", ".join(sorted(missing_breakdown)) + ".")
    transport = plan.get("transport_preference") if isinstance(plan.get("transport_preference"), dict) else {}
    mode = transport.get("mode")
    if mode not in set(TRANSPORT_MODES):
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
        if isinstance(activities, list):
            # Two optional per-activity fields. Each is rejected only when it is present and
            # malformed, so a plan that omits it keeps validating exactly as before.
            for activity_number, activity in enumerate(activities, 1):
                if not isinstance(activity, dict):
                    continue
                # ticket_note is optional -- most activities are free, and no plan carries it
                # everywhere -- but the day card prints it verbatim under a "Ticket" label the
                # traveller reads as researched fact. A number, an object, or a blank string
                # would put "None", "{}" or an empty prefix exactly where an admission price
                # and a ticketing channel belong.
                ticket_note = activity.get("ticket_note")
                if ticket_note is not None and (not isinstance(ticket_note, str) or not ticket_note.strip()):
                    errors.append(f"day {expected} activity {activity_number}.ticket_note must be a non-empty string naming the admission price and where the official ticket is sold (plus any queue or advance-booking caveat), or be omitted.")
                # An activity that never states its on-foot time is left out of the walking
                # figure rather than assumed to be zero. But a value of the wrong type is
                # worse than none at all -- "90 min" as prose reads as present, sums as nothing,
                # and hands the walking-budget check a total it will report with confidence.
                if activity.get("on_foot_minutes") is None:
                    continue
                on_foot = activity["on_foot_minutes"]
                if not isinstance(on_foot, int) or isinstance(on_foot, bool) or on_foot < 0:
                    errors.append(f"day {expected} activity {activity_number}.on_foot_minutes must be a whole number of minutes of 0 or more (the time spent on foot or standing during that activity), or be omitted.")
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
                if item.get("meal") not in set(MEAL_TYPES):
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
        elif not source.get("claim_or_decision_supported"):
            # Silently defaulting to "Plan evidence" turned an authoring slip into eighteen
            # identical rows: the measured plan wrote this field as `supports`, the renderer read
            # `claim_or_decision_supported`, nothing objected, and the register that exists to say
            # WHAT each source proves said nothing at all, eighteen times. A source register whose
            # every line reads the same is not a register.
            errors.append(
                "Every source needs claim_or_decision_supported saying what it proves -- the field "
                "is spelled that way in templates/final-trip-plan.json, and a register whose rows "
                "all read alike tells the traveller nothing about which fact rests on which page.")
        elif not is_iso_datestamp(source["accessed_at"]):
            errors.append("Every source.accessed_at must be an ISO date or date-time.")
    options = plan.get("booking_options") if isinstance(plan.get("booking_options"), dict) else {}
    required = {"flights": "flight", "ground_transport": "ground", "accommodations": "hotel",
                "attraction_tickets": "ticket", "rental_cars": "car"}
    for field, kind in required.items():
        items = options.get(field, [])
        if not isinstance(items, list):
            errors.append(f"booking_options.{field} must be a list.")
            continue
        for item in items:
            if not isinstance(item, dict) or not all(item.get(key) for key in ("provider" if kind != "ticket" else "official_or_authorised_provider", "checked_at", "review_url")) or not is_https(item.get("review_url")):
                errors.append(f"Every {kind} option needs provider, checked_at, and an HTTPS review_url.")
                continue
            if kind in {"flight", "hotel", "ground"} and not item.get("id"):
                errors.append(f"Every {kind} option needs a stable, non-empty id for day assignments and comparison.")
            if item.get("comparison_platform") and not item.get("comparison_checked_at"):
                errors.append(f"Every compared {kind} option needs comparison_checked_at.")
            if not is_iso_datestamp(item.get("checked_at")):
                errors.append(f"Every {kind} option.checked_at must be an ISO date or date-time.")
            if item.get("comparison_checked_at") and not is_iso_datestamp(item.get("comparison_checked_at")):
                errors.append(f"Every compared {kind} option.comparison_checked_at must be an ISO date or date-time.")
            if item.get("direct_review_url") and not is_https(item.get("direct_review_url")):
                errors.append(f"Every direct {kind} cross-check URL must be HTTPS.")
            if kind == "ground":
                # Held to the flight standard on purpose. A category with looser evidence rules
                # becomes the place authors put the thing they did not research, and this is the
                # purchase a rail traveller most needs to check: which service, when, what the fare
                # allows, whether it is still sellable, and how they get from the station into town.
                required_ground_fields = (
                    "origin_station", "destination_station", "outbound_date", "return_date",
                    "outbound_itinerary", "return_itinerary", "material_conditions",
                    "availability_status", "price_checked_at", "station_transfer_note",
                    "round_trip_search_url", "round_trip_search_provider",
                    "round_trip_search_checked_at", "round_trip_prefilled_fields",
                )
                if not all(item.get(key) for key in required_ground_fields):
                    errors.append(
                        "Every rail/coach/ferry option needs both stations, both dates, concrete "
                        "outbound and return itineraries, fare conditions, a current-price status, "
                        "a station-to-city transfer note, and a verified dated round-trip search URL.")
                elif not is_https(item["round_trip_search_url"]):
                    errors.append("Every ground-transport round-trip search URL must be HTTPS.")
                search_fields = item.get("round_trip_prefilled_fields")
                if not has_search_fields(search_fields, REQUIRED_FLIGHT_SEARCH_FIELDS):
                    errors.append(
                        "Every ground-transport round-trip search must prefill origin, destination, "
                        "outbound date, return date, and travellers.")
                if not is_iso_datestamp(item.get("round_trip_search_checked_at")):
                    errors.append("ground.round_trip_search_checked_at must be an ISO date or date-time.")
                for leg_name in ("outbound_itinerary", "return_itinerary"):
                    leg = item.get(leg_name)
                    if not isinstance(leg, dict) or not all(leg.get(key) is not None and leg.get(key) != "" for key in ("service_identifier", "departure_local", "arrival_local", "duration_minutes", "stops", "connection_or_terminal_note")):
                        errors.append(f"ground.{leg_name} needs the service identifier, local times, duration, changes, and an interchange note.")
                    elif not (isinstance(leg.get("duration_minutes"), (int, float)) and not isinstance(leg.get("duration_minutes"), bool) and leg["duration_minutes"] > 0):
                        # A journey of zero minutes is not a researched journey, and zero was what
                        # the contract template seeded the field with -- so the one value that
                        # means "I did not fill this in" was the one value every gate accepted.
                        errors.append(f"ground.{leg_name}.duration_minutes must be a positive number of minutes.")
                if not is_one_of(item.get("availability_status"), {"available", "limited", "unknown"}):
                    errors.append("ground.availability_status must be available, limited, or unknown.")
                if item.get("price_basis") != "per_person_round_trip":
                    errors.append("ground.price_basis must be per_person_round_trip.")
                if not is_one_of(item.get("price_status"), PRICE_STATUSES):
                    errors.append("ground.price_status must be researched_current, estimate, or user_confirmed.")
                if item.get("fare_low") is None or item.get("fare_high") is None or not item.get("fare_currency"):
                    errors.append("Every ground-transport option needs a checked per-person fare range and currency.")
                elif not is_nonnegative_money_range(item.get("fare_low"), item.get("fare_high")):
                    errors.append("Every ground-transport fare range must be non-negative with low <= high.")
                elif item["fare_low"] == 0 and item["fare_high"] == 0:
                    # Same reasoning as the zero-minute leg: 0/0 was the template's seed value, so
                    # "unfilled" and "free" were indistinguishable to every gate. A ticket that is
                    # genuinely free needs no round-trip search URL, which this branch requires.
                    errors.append("A ground-transport fare of 0-0 is an unfilled field, not a researched fare.")
                if not is_iso_datestamp(item.get("price_checked_at")):
                    errors.append("ground.price_checked_at must be an ISO date or date-time.")
                # The two dates were truthiness-checked only, so "next Friday" and a return three
                # days BEFORE departure both validated and printed straight onto the search button.
                # price_checked_at and round_trip_search_checked_at on the same object are already
                # ISO-checked, so the looseness was an oversight rather than a decision.
                outbound = parse_iso_date(item.get("outbound_date"), "ground.outbound_date", errors)
                inbound = parse_iso_date(item.get("return_date"), "ground.return_date", errors)
                if outbound and inbound and inbound < outbound:
                    errors.append("ground.return_date cannot be before ground.outbound_date.")
                # Deliberately NOT copied from the flight branch: a flight's outbound_date must equal
                # trip.start_date, because the flight IS the arrival. A rail leg is often mid-trip --
                # the hop between two cities -- so pinning it to the trip window would reject the
                # multi-city case this category exists to serve. Both dates must still fall inside
                # the trip, which is a weaker and correct claim.
                trip_start = parse_iso_date(trip.get("start_date"), "trip.start_date", [])
                trip_end = parse_iso_date(trip.get("end_date"), "trip.end_date", [])
                for label, value in (("outbound_date", outbound), ("return_date", inbound)):
                    if value and trip_start and trip_end and not trip_start <= value <= trip_end:
                        errors.append(
                            f"ground.{label} {value.isoformat()} falls outside the trip window "
                            f"{trip_start.isoformat()}..{trip_end.isoformat()}.")
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
                if not has_search_fields(search_fields, REQUIRED_FLIGHT_SEARCH_FIELDS):
                    errors.append("Every flight round-trip search must prefill origin, destination, outbound date, return date, and travellers.")
                if not is_iso_datestamp(item.get("round_trip_search_checked_at")):
                    errors.append("flight.round_trip_search_checked_at must be an ISO date or date-time.")
                for leg_name in ("outbound_itinerary", "return_itinerary"):
                    leg = item.get(leg_name)
                    if not isinstance(leg, dict) or not all(leg.get(key) is not None and leg.get(key) != "" for key in ("service_identifier", "departure_local", "arrival_local", "duration_minutes", "stops", "connection_or_terminal_note")):
                        errors.append(f"flight.{leg_name} needs carrier/flight or service identifier, local times, duration, stops, and connection/terminal note.")
                if not is_one_of(item.get("availability_status"), {"available", "limited", "unknown"}):
                    errors.append("flight.availability_status must be available, limited, or unknown.")
                if item.get("price_basis") != "per_person_round_trip":
                    errors.append("flight.price_basis must be per_person_round_trip.")
                if not is_one_of(item.get("price_status"), PRICE_STATUSES):
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
                if not is_one_of(item.get("availability_status"), {"available", "limited", "unknown"}):
                    errors.append("hotel.availability_status must be available, limited, or unknown.")
                if not item.get("stay_group_id"):
                    errors.append("Every hotel option needs a stay_group_id so comparable options cannot be split by different neighborhood labels.")
                if item.get("price_basis") != "per_room_per_night":
                    errors.append("hotel.price_basis must be per_room_per_night.")
                if not is_one_of(item.get("price_status"), PRICE_STATUSES):
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
                        if not has_search_fields(fields, REQUIRED_STAY_SEARCH_FIELDS):
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
                if item.get("ticket_status") and item.get("ticket_status") not in BOOKING_STATES:
                    errors.append("ticket.ticket_status must be idea, researched, held, or booked.")
                if item.get("price_basis") != "per_person_ticket":
                    errors.append("ticket.price_basis must be per_person_ticket.")
                if not is_one_of(item.get("price_status"), PRICE_STATUSES):
                    errors.append("ticket.price_status must be researched_current, estimate, or user_confirmed.")
                if not is_one_of(item.get("availability_status"), {"available", "limited", "unknown"}):
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
                if not is_one_of(item.get("price_status"), PRICE_STATUSES):
                    errors.append("rental_car.price_status must be researched_current, estimate, or user_confirmed.")
                if not is_one_of(item.get("availability_status"), {"available", "limited", "unknown"}):
                    errors.append("rental_car.availability_status must be available, limited, or unknown.")
                if not is_nonnegative_money_range(item.get("price_low"), item.get("price_high")):
                    errors.append("rental-car price range must be non-negative numbers with low less than or equal to high.")
                if not is_iso_datestamp(item.get("price_checked_at")):
                    errors.append("rental_car.price_checked_at must be an ISO date or date-time.")
                rental_fields = item.get("rental_search_prefilled_fields")
                if not has_search_fields(rental_fields, REQUIRED_RENTAL_SEARCH_FIELDS):
                    errors.append("Every rental-car search must prefill pickup/dropoff locations and times.")
    required_access_categories = {"accommodation"}
    if options.get("flights"):
        required_access_categories.add("flight")
    if options.get("attraction_tickets"):
        required_access_categories.add("attraction_ticket")
    if options.get("rental_cars"):
        required_access_categories.add("rental_car")
    # Keyed on the card as well as the mobility mode, because every other category is keyed on
    # "is this card here" and this one was not. A car ferry, a motorail or a Eurotunnel shuttle
    # sits inside a self-drive trip, and the mode test alone left that crossing -- often the one
    # sellable-out purchase on the page -- as the only channel with no record of whether the
    # traveller can reach and buy from it.
    if mode == "public-transit" or options.get("ground_transport"):
        required_access_categories.add("rail_or_ground")
    missing_access_categories = required_access_categories - booking_access_categories
    if missing_access_categories:
        errors.append("Missing booking-access checks for: " + ", ".join(sorted(missing_access_categories)) + ".")
    accommodation_items = [item for item in options.get("accommodations", []) if isinstance(item, dict)]
    flight_items = [item for item in options.get("flights", []) if isinstance(item, dict)]
    ground_items = [item for item in options.get("ground_transport", []) if isinstance(item, dict)]
    # One rule set over both categories. SKILL.md and references/booking-html-output.md both
    # promise ground is "held to exactly the flight standard"; leaving the three comparison rules
    # keyed on flight_items made that sentence false, and an uncompared, unexplained single rail
    # option is exactly what the strictness exists to prevent -- as is a pair of "compared" cards
    # pointing at one review_url, which looks like a comparison and is not.
    for items, noun, label in ((flight_items, "flight", "Flight"),
                               (ground_items, "rail/coach/ferry", "Rail/coach/ferry")):
        if len(items) == 1 and not items[0].get("single_option_reason"):
            errors.append(f"Provide at least two comparable {noun} candidates, or record a researched single_option_reason for the only feasible option.")
        identifiers = [item.get("id") for item in items]
        # A non-string id is not merely odd: ids are matched by equality against day assignments
        # elsewhere in the plan, so a list or a number is a value that can never match and never
        # says why. Truthiness alone accepted `["ground-1"]`.
        if any(not isinstance(identifier, str) or not identifier.strip() for identifier in identifiers) \
                or len({dedupe_key(v) for v in identifiers}) != len(identifiers):
            errors.append(f"{label} options must use distinct, non-empty string ids so the comparison is not ambiguous.")
        review_urls = [item.get("review_url") for item in items]
        if len({dedupe_key(v) for v in review_urls}) != len(review_urls):
            errors.append(f"{label} candidates must not reuse the same review_url; provide genuinely distinct comparison paths.")
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
    # The same rule for the ground modes, and it is the one that makes the category worth having.
    # Adding ground_transport made a train card POSSIBLE; without this it was never REQUIRED, so a
    # rail-arrival plan with three compared hotels and no way to reach, price-check or
    # availability-check the train passed validate_plan, check_plan_consistency and
    # validate_trip_html exactly as before -- the defect the category was built to end, recurring on
    # any run where the author simply did not think of it. `--require-booking-type ground` cannot
    # cover for this: it is opt-in, and the flight rule pointedly does not depend on the operator
    # remembering a flag.
    #
    # "road" is included only for public transit, where it means a coach; on a self-drive trip the
    # road arrival is the rental car, which the next rule already requires.
    if trip.get("arrival_transport_mode") == "rail" and not options.get("ground_transport"):
        errors.append(
            "A rail-arrival plan needs at least one rail/coach/ferry option in "
            "booking_options.ground_transport -- the train is the largest and most time-sensitive "
            "purchase on the page, and without a card the traveller cannot reach, price or "
            "availability-check it.")
    # "road" is only an intercity coach when the trip actually leaves town. On a same-city plan --
    # the repo's own fixture is Chengdu to Chengdu -- road means the taxi or the bus that met the
    # traveller, and demanding a bookable coach card for it would be a gate firing on correct
    # authoring, which is worse than no gate.
    leaves_town = str(trip.get("origin") or "").strip() != str(trip.get("destination") or "").strip()
    if (trip.get("arrival_transport_mode") == "road" and mode != "self-drive" and leaves_town
            and not options.get("ground_transport")):
        errors.append(
            "A road arrival between two places on public transit means an intercity coach, so it "
            "needs at least one rail/coach/ferry option in booking_options.ground_transport.")
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
    # Kept as a list, in `attraction_tickets` order, because a day's tickets are joined on two
    # keys, not one: the day card used to read only activities[].ticket_option_id, so a ticket
    # that carried `day_number` and was never referenced by an activity reached the booking
    # panel and vanished from the day. The measured run printed "no ticket required" on all
    # four days of a plan holding three paid tickets -- an affirmative false statement on the
    # page the traveller books from, and the reason the empty-state line below is now gated on
    # the union rather than on one of its two halves.
    ticket_options = [item for item in options.get("attraction_tickets", []) if isinstance(item, dict)]
    # Grouped by type: a single grid put flight cards and hotel cards side by side in one
    # row, inviting a comparison between things that are not comparable.
    groups = []
    for field, kind, heading in (
        ("flights", "flight", "Flight options"),
        # Rail, coach and ferry sit next to flights because they answer the same question -- how
        # the traveller crosses the distance -- and because a rail trip's biggest, most
        # time-sensitive purchase previously had no card at all: the page compared three hotels and
        # offered no way to reach, price or availability-check the train.
        ("ground_transport", "ground", "Rail, coach and ferry options"),
        ("accommodations", "hotel", "Accommodation options"),
        ("attraction_tickets", "ticket", "Ticket options"),
        ("rental_cars", "car", "Rental-car options"),
    ):
        items = [item for item in options.get(field, []) if isinstance(item, dict)]
        if not items:
            continue
        rendered = "".join(option_card(kind, item) for item in items)
        groups.append(f'<section class="option-group" data-option-group="{attr(kind)}"><h3>{heading}</h3><div class="grid">{rendered}</div></section>')
    cards = groups or ['<p class="meta">No purchase options were requested for this plan.</p>']
    day_cards = []
    for day in plan["days"]:
        route = day["route"]
        stay = accommodations.get(day.get("accommodation_option_id"))
        activities = []
        referenced_ticket_ids = set()
        day_has_ticket_note = False
        for activity in day.get("activities", []):
            # The activity's own admission line: what it costs and where the official ticket is
            # actually sold. The plan collected it and the page printed it nowhere -- the measured
            # run researched "adult EUR 12.00, official ticketing at tickets.koelner-dom.de; book
            # online to skip the Roncalliplatz queue" and the traveller saw a cathedral with no
            # price and no channel. It belongs on the activity rather than only in the ticket
            # panel below, because the panel lists only options with a booking URL, and a note
            # about buying on site or joining a queue has none. A required field that never
            # reaches the page is research the traveller paid for and cannot see.
            ticket_note = activity.get("ticket_note") if isinstance(activity, dict) else None
            note_line = ""
            if ticket_note:
                note_line = f'<p class="meta ticket-note"><strong>Ticket: </strong>{esc(ticket_note)}</p>'
                day_has_ticket_note = True
            activities.append(f'<li><time>{esc(activity.get("time"), "Flexible")}</time><div><strong>{esc(activity.get("name"))}</strong><p>{esc(activity.get("detail"))} {esc(activity.get("meal_or_rest_buffer"), "")}</p>{note_line}</div></li>')
            if isinstance(activity, dict) and activity.get("ticket_option_id"):
                referenced_ticket_ids.add(activity["ticket_option_id"])
        # One pass over the ordered options is the de-duplication: a ticket both referenced by
        # an activity and dated to this day is listed once, where `attraction_tickets` puts it.
        day_tickets = [
            link_for_ticket(ticket)
            for ticket in ticket_options
            if ticket.get("id") in referenced_ticket_ids or ticket.get("day_number") == day.get("number")
        ]
        # The empty state is an affirmative claim, not decoration, so it may only be printed
        # when the day really shows no admission cost anywhere -- which is why the previous
        # release widened its condition from one join key to both. A ticket_note is the third
        # member of that union: an on-site fee or a city card has no bookable option to link,
        # so the day list stays empty while the activity above it now prints a price, and the
        # old wording would have told the traveller no ticket was needed on the same screen.
        ticket_panel = "".join(day_tickets) or (
            "" if day_has_ticket_note else '<p>No verified ticket is required for the listed activities.</p>'
        )
        transport_bits = [as_text(route.get("mode")), minutes(route.get("duration_minutes")), money(route.get("cost_low"), route.get("cost_high"), route.get("currency", trip["currency"])), as_text(route.get("fare_basis_or_fuel_toll_parking_note"), "")]
        transport_line = " · ".join(bit for bit in transport_bits if bit)
        segment_links = route_segment_links(route, trip["currency"])
        dining = dining_cards(day.get("dining"))
        route_scope = route.get("route_map_scope")
        route_map_label = "Open full-day route" if route_scope == "multi_stop" else "Open route overview — see segments below"
        # The whole-day walking load and the bad-weather/closure fallback are required by
        # the plan contract; leaving them unrendered collected the research and then hid it
        # from the person who has to walk the route.
        walking_line = (
            f'<p class="meta"><strong>Walking across the day: </strong>{esc(route.get("walking_burden"))}</p>'
            if route.get("walking_burden")
            else ""
        )
        fallback_line = (
            f'<p class="meta route-fallback"><strong>Fallback: </strong>{esc(route.get("fallback_plan"))}</p>'
            if route.get("fallback_plan")
            else ""
        )
        # Two figures per day, drawn from numbers the plan already carries. The stop list shows
        # the order; only a map shows the shape, and only an axis shows whether the afternoon is
        # empty. Both return "" when their data is missing, so a thin day loses a figure rather
        # than gaining an invented one.
        day_map_caption = "Relative positions, in visit order. Use the map button to navigate."
        day_map_figure = plan_visuals.day_map(route, day_map_caption, day_map_caption)
        timeline_entries = [("act", activity.get("time"), activity.get("name"))
                            for activity in day.get("activities") or []
                            if isinstance(activity, dict)]
        timeline_entries += [("meal", card.get("time") or card.get("time_window"),
                              card.get("venue_name"))
                             for card in day.get("dining") or [] if isinstance(card, dict)]
        day_shape_caption = "Fixed points across the day"
        day_timeline_figure = plan_visuals.day_timeline(
            timeline_entries, day_shape_caption, day_shape_caption)
        stay_line = "Checkout / no overnight stay" if day.get("day_type") == "departure" else "Arranged independently"
        if stay:
            stay_line = f"{as_text(stay.get('property_name'))} · {as_text(stay.get('stay_location'))} · {as_text(stay.get('room_basis'))}"
        day_cards.append(f'''<article class="day-card" id="day-{attr(day["number"])}" data-day="{attr(day["number"])}"><div class="day-top"><div><p class="eyebrow">Day {esc(day.get("number"))} · {esc(day.get("date"))}</p><h2>{esc(day.get("title"))}</h2><p>{esc(day.get("focus"))}</p></div><div class="day-number" aria-label="Day {attr(day["number"])}">{esc(day.get("number"))}</div></div><section class="day-accommodation"><h3>Stay</h3><p><strong>{esc(stay_line)}</strong></p></section><section class="day-activities"><h3>Plan</h3>{day_timeline_figure}<ol class="timeline">{"".join(activities) or '<li><time>Flexible</time><div><strong>Free time</strong></div></li>'}</ol></section><section class="day-dining"><h3>Dining suggestions</h3>{dining}</section><section class="day-route"><h3>Route and mobility</h3><p>{esc(transport_line)}</p><p class="meta">{esc(route.get("route_logic"))}</p><figure class="route-map">{day_map_figure}{route_diagram(route.get("stops_in_order", []))}<figcaption>Schematic — not for navigation. Stops are shown in visit order; use the live map for directions.</figcaption></figure><a class="map-link" data-map-scope="{attr(route_scope)}" data-verified-at="{attr(route["map_checked_at"])}" href="{attr(route["verified_map_url"])}" target="_blank" rel="noopener noreferrer">{route_map_label}</a><h4>Route by segment</h4>{segment_links}{walking_line}{fallback_line}<p class="meta">{esc(route.get("service_or_driving_caveat"), "Recheck operating conditions before departure.")}</p></section><section class="day-bookings"><h3>Tickets and recheck</h3>{ticket_panel}<p class="warning">{esc(day.get("contingency"), "Keep a flexible alternative for disruptions.")}</p></section></article>''')
    overview = plan["transport_overview"]
    source_rows = "".join(f'<li class="source-item" data-source-type="{attr(source["source_type"])}" data-accessed-at="{attr(source["accessed_at"])}" data-source-url="{attr(source["url"])}"><a class="source-link" href="{attr(source["url"])}" target="_blank" rel="noopener noreferrer">{esc(source["name"])}</a> — {esc(source.get("claim_or_decision_supported"), "Plan evidence")} · {esc(source.get("confidence"), "researched")}</li>' for source in sources)
    total = money(budget.get("estimated_per_person_low"), budget.get("estimated_per_person_high"), trip["currency"])
    # The traveller's own stated ceiling belongs next to the number it constrains. It reached
    # check_plan_consistency and stopped there, so a page could show a per-person total well
    # over the cap the traveller gave at intake and read as though nothing were wrong -- the
    # one figure that makes the total judgeable was the one figure the page withheld.
    cap = budget.get("cap_per_person")
    cap_fact = (
        f'<div class="fact budget-cap" data-budget-cap="{attr(cap)}">'
        f'<strong>{esc(money(cap, cap, trip["currency"]))}</strong><span>Cap per person</span></div>'
        if cap is not None
        else ""
    )
    budget_breakdown = budget_breakdown_cards(budget.get("breakdown"), trip["currency"])
    # Two whole-trip figures. The breakdown table is exact and hard to read at a glance; the
    # walking numbers are already computed and already checked, and the one thing nobody could do
    # without adding up five day cards by hand was see which day is the heavy one.
    budget_rows = []
    for row in as_list(budget.get("breakdown")):
        if not isinstance(row, dict):
            continue
        low, high = row.get("per_person_low"), row.get("per_person_high")
        values = [v for v in (low, high) if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if values:
            budget_rows.append((as_text(row.get("category")), sum(values) / len(values)))
    budget_caption = "Share of the per-person total, and how much of the cap it uses"
    budget_figure = plan_visuals.budget_bar(
        budget_rows, sum(value for _, value in budget_rows),
        cap if isinstance(cap, (int, float)) else None, budget_caption, budget_caption)
    walking_rows = []
    for day in as_list(plan.get("days")):
        if not isinstance(day, dict):
            continue
        route_block = day.get("route") if isinstance(day.get("route"), dict) else {}
        legs = sum(as_number(segment.get("walking_minutes"))
                   for segment in as_list(route_block.get("segments")) if isinstance(segment, dict))
        on_foot = sum(as_number(activity.get("on_foot_minutes"))
                      for activity in as_list(day.get("activities")) if isinstance(activity, dict))
        walking_rows.append((f"Day {as_text(day.get('number'))}", legs + on_foot))
    # No cap line, and that is a correction rather than an omission. The first version drew
    # traveler_constraints.max_continuous_walking_minutes across these bars, but that field is a
    # limit on a SINGLE unbroken stretch while the bars are a whole-day total. Comparing them
    # marked all five days of a real plan as over the limit when not one leg broke it -- a figure
    # that manufactures an alarm is worse than no figure, and it would have been read as the plan
    # violating a stated accessibility need. The per-stretch rule is enforced by
    # check_walking_budget, which compares like with like.
    walk_cap = (trip.get("traveler_constraints") or {}).get("max_continuous_walking_minutes")
    longest_leg = max(
        [as_number(segment.get("walking_minutes"))
         for day in as_list(plan.get("days")) if isinstance(day, dict)
         for segment in as_list((day.get("route") or {}).get("segments"))
         if isinstance(segment, dict)] or [0.0])
    walking_caption = "Minutes on foot per day: connecting legs plus time on foot at each stop"
    if isinstance(walk_cap, (int, float)) and walk_cap:
        walking_caption += (f" · Longest single walk: {longest_leg:.0f} / {walk_cap:.0f} min")
    walking_figure = plan_visuals.walking_bars(
        walking_rows, None, walking_caption, walking_caption, "")
    hero_photo = imagery_figure((plan.get("imagery") or {}).get("hero"), trip["destination"])
    if hero_photo:
        hero_photo = f'<section class="hero-photo">{hero_photo}</section>'
    recheck = "<br>".join(esc(value) for value in as_list(plan.get("recheck_before_purchase")) if value) or "Recheck price, availability, entry requirements, and operating conditions before purchase."
    anchors = destination_anchor_cards(plan.get("destination_experience_anchors"), plan.get("imagery"))
    overview_scope = overview.get("overall_map_scope")
    # "transport overview" promised a trip-wide route and opened one leg -- the airport hop the
    # traveller already has on day 1. Name the leg it actually opens; the daily segment
    # buttons are the navigation, and the notes below carry what is genuinely trip-wide.
    # The header carries only figures whose scope is unambiguous. A public-transit trip gets the
    # mode and the trip-wide fare: its duration and distance are per-leg facts that live on the
    # day cards, and printing one leg's numbers beside a whole-trip fare read as one thing while
    # describing two -- a delivered page said "28 minutes · 12.0 km · €13.60–19.10" where the
    # first two were the airport hop. A self-drive trip is the opposite: the whole-trip driving
    # total is exactly what the traveller needs for fuel and fatigue, and validate_plan requires
    # it, so it is shown -- a required field that is never displayed is the defect this file
    # warns about elsewhere.
    _ov_parts = [as_text(plan["transport_preference"]["mode"])]
    if plan["transport_preference"]["mode"] == "self-drive":
        _ov_parts.append(minutes(overview.get("overall_duration_minutes")))
        if overview.get("overall_distance_km") is not None:
            _ov_parts.append(as_text(overview.get("overall_distance_km"), "") + " km")
    _ov_parts.append(money(overview.get("cost_low"), overview.get("cost_high"), trip["currency"]))
    overview_headline = " · ".join(part for part in _ov_parts if part)

    overview_map_label = ("Open overall route" if overview_scope == "multi_stop"
                          else "Open the airport transfer route — daily segments are below")
    # A multi-day plan is tens of thousands of pixels tall. Without in-page jumps the
    # traveller can only reach day 4 by scrolling past days 1-3 every time they look.
    nav_days = "".join(
        f'<a class="page-nav-link day-nav-link" href="#day-{attr(day["number"])}">'
        f'<span class="day-nav-day">Day {esc(day.get("number"))}</span>'
        f'<span class="day-nav-date">{esc(day.get("date"))}</span></a>'
        for day in plan["days"]
    )
    page_nav = (
        '<nav id="page-nav" class="page-nav" aria-label="On this page">'
        '<a class="page-nav-link" href="#budget-breakdown">Budget</a>'
        '<a class="page-nav-link" href="#booking-panel">Options</a>'
        f'{nav_days}'
        '<a class="page-nav-link" href="#transport-overview">Transport</a>'
        '<a class="page-nav-link" href="#source-register">Sources</a>'
        "</nav>"
    )
    # Categories the plan admits it could not price must be visible, or the per-person
    # total silently reads as if it covered everything.
    unpriced_items = "".join(
        f'<li class="unpriced-category">{esc(category)}</li>'
        for category in as_list(budget.get("unverified_categories"))
        if category
    )
    unpriced = (
        f'<div class="fact unpriced"><ul class="unpriced-list">{unpriced_items}</ul>'
        "<span>Unpriced categories</span></div>"
        if unpriced_items
        else ""
    )
    assumption_rows = "".join(f"<li>{esc(value)}</li>" for value in as_list(plan.get("assumptions")) if value)
    # Open by default, like the sources list above it. A closed <details> prints as a heading
    # with nothing under it, and in print it prints as a heading with nothing under it
    # permanently -- so the assumptions the numbers rest on, and the recheck list further
    # down, read as empty sections rather than as the safety content they are.
    assumptions_block = (
        f'<details class="plan-assumptions" open><summary>Planning assumptions</summary><ul>{assumption_rows}</ul></details>'
        if assumption_rows
        else ""
    )
    entry = plan.get("entry_context") if isinstance(plan.get("entry_context"), dict) else {}
    entry_panel = (
        f'<section id="entry-context" class="panel"><h2>Entry eligibility</h2>'
        f'<p><strong class="entry-status">{attr(entry.get("status"))}</strong> — {esc(entry.get("summary"))}</p>'
        f'<p class="meta"><strong>Basis: </strong>{esc(entry.get("traveler_basis"))}</p>'
        f'<p class="meta"><a class="entry-source-link" href="{attr(entry.get("source_url"))}" target="_blank" rel="noopener noreferrer">View source</a> · {stamp(entry.get("checked_at"))}</p>'
        "</section>"
        if entry
        else ""
    )
    # A plan saved with --unverified skipped the five-domain pass. Recording that in a JSON
    # field only tells whoever opens the JSON; the person who reads the page and books from it
    # has to see it too, so the gap stays visible where the decision actually gets made.
    unverified_banner = (
        '<section id="verification-notice" class="panel"><p class="warning">'
        '<strong>Not fact-checked</strong> — No verification pass is recorded for this plan. '
        'Its fares, opening hours, entry rules, and availability have not been checked against '
        'operators or official sources. Treat every figure as an estimate and verify before booking.'
        "</p></section>"
        # Show the banner unless the plan says, in as many words, that it WAS verified. The test
        # used to be `== "unverified"`, which made the safe state the one nobody writes: the
        # skeleton emits `None`, a plan that never reached the verification stage keeps `None`, and
        # replan_trip.py deliberately resets to `None` -- so the default of every plan in the repo
        # rendered as fully fact-checked. A page whose status nobody set is a page nobody checked,
        # and it is the traveller standing at an airline counter who pays for the difference.
        if plan.get("verification_status") != "verified"
        else ""
    )
    # The traveller's own hard constraints, on the page they carry. The block was validated here
    # and rendered nowhere: a severe triple allergy's entire mechanical effect was "run four more
    # verification agents", and `allergy_card_text` -- the sentence written to hand to restaurant
    # staff -- lived in the JSON only. Whether the allergy appeared at all depended on the author
    # separately retyping it into dining prose. SKILL.md's own rule is that a required field never
    # displayed is research the traveller paid for and cannot see; this was the sharpest case of it
    # in the file, because the field exists to be read out loud at a table.
    constraints = trip.get("traveler_constraints") if isinstance(trip.get("traveler_constraints"), dict) else {}
    constraint_rows = []
    severity = str(constraints.get("allergy_severity") or "").strip()
    if severity and severity != "none":
        constraint_rows.append(
            f'<p><strong>Allergy severity: </strong><span class="pill">{esc(severity)}</span></p>')
    for field, label in (("dietary_or_religious_needs", "Dietary needs"),
                         ("mobility_notes", "Mobility")):
        values = [v for v in constraints.get(field, []) if isinstance(v, str) and v.strip()]
        if values:
            items = "".join(f"<li>{esc(v)}</li>" for v in values)
            constraint_rows.append(f'<p><strong>{label}: </strong></p><ul>{items}</ul>')
    cap = constraints.get("max_continuous_walking_minutes")
    if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
        constraint_rows.append(
            f'<p><strong>Maximum continuous walking: </strong>{cap} min</p>')
    card = constraints.get("allergy_card_text")
    if isinstance(card, str) and card.strip():
        constraint_rows.append(
            '<p><strong>Allergy card — show this to staff: </strong></p>'
            f'<blockquote class="allergy-card">{esc(card)}</blockquote>')
    constraints_panel = (
        '<section id="traveller-constraints" class="panel"><h2>Your constraints</h2>'
        + "".join(constraint_rows) + "</section>"
        if constraint_rows else ""
    )

    # The other half of the same intake form. check_plan_consistency requires every avoid_list
    # entry to carry an avoid_list_handling entry saying what keeps it out, and requires anchors to
    # name the must-haves they answer -- and then the page printed neither the avoid list nor how
    # each item was honoured nor the scenery/culture subtypes the traveller picked. Measured with
    # canary strings through the whole save path: all three were in the JSON and none of them
    # reached the HTML. That is the defect this skill names elsewhere in its own words -- a rating
    # stored and never shown is the same defect as a rating never gathered -- and it lands on the
    # half of the intake that says why the traveller is going at all. The must-haves already reach
    # the page through each anchor's satisfies_preference, so they are not repeated here.
    preferences = trip.get("traveler_preferences") if isinstance(trip.get("traveler_preferences"), dict) else {}
    preference_rows = []
    direction = [v for field in ("natural_subtypes", "human_cultural_subtypes")
                 for v in as_list(preferences.get(field)) if isinstance(v, str) and v.strip()]
    if direction:
        items = "".join(f"<li>{esc(v)}</li>" for v in direction)
        preference_rows.append(f'<p><strong>Experience direction: </strong></p><ul>{items}</ul>')
    handling = {str(entry.get("item") or "").strip(): str(entry.get("how_avoided") or "").strip()
                for entry in as_list(preferences.get("avoid_list_handling"))
                if isinstance(entry, dict)}
    avoid_items = []
    for item in as_list(preferences.get("avoid_list")):
        if not isinstance(item, str) or not item.strip():
            continue
        how = handling.get(item.strip())
        # An unhandled entry is printed bare rather than dropped. check_plan_consistency already
        # refuses to save that plan, so this only ever shows in a draft render -- and a silent
        # omission there would hide exactly what the author still has to answer.
        avoid_items.append(f"<li>{esc(item)}{f' — {esc(how)}' if how else ''}</li>")
    if avoid_items:
        preference_rows.append(
            f'<p><strong>Asked to avoid: </strong></p><ul>{"".join(avoid_items)}</ul>')
    preferences_panel = (
        '<section id="traveller-preferences" class="panel"><h2>What you asked for</h2>'
        + "".join(preference_rows) + "</section>"
        if preference_rows else ""
    )

    # Every gate in this skill is a script, and a script only runs when the agent calls it -- a
    # hand-written page bypasses all of them and looks exactly like a saved one. Nothing in the
    # scripts can fix that; the enforcement point is upstream of them. What the page CAN do is
    # carry the evidence, so a reader can tell the two apart. save_trip_deliverables.py already
    # stamped gates_passed into the plan JSON and it stopped there -- the same gap this repo
    # closed for the unverified banner, whose lesson was that a flag stored only in JSON never
    # reaches the person holding the itinerary at an airline counter.
    #
    # Deliberately says nothing when the stamp is absent: a draft render and a hand-made page both
    # lack it, and a warning would not survive a forger who simply omits it either. Presence is
    # the signal. The caveat is not decoration -- 22 structural checks say the plan agrees with
    # itself, and a reader who reads that as "fact-checked" has been misled by the reassurance.
    # Not named `stamp`: that is the module-level date formatter this same template calls a few
    # lines down, and shadowing it made every render raise "'dict' object is not callable".
    gate_stamp = plan.get("gates_passed") if isinstance(plan.get("gates_passed"), dict) else {}
    checks = gate_stamp.get("checks")
    gates_line = ""
    if isinstance(checks, int) and not isinstance(checks, bool) and checks > 0:
        gates_line = (
            f'<p class="meta" data-gates-checks="{attr(checks)}">'
            f'<strong>Structure checks passed: </strong>{esc(checks)}. '
            f'They prove the plan agrees with itself, never that its facts are true.</p>')
    regional = plan.get("regional_service_context") if isinstance(plan.get("regional_service_context"), dict) else {}
    platform_note = (
        # selection_basis is REQUIRED by validate_plan and was printed nowhere, so the page said
        # which map and booking providers were used and never why those suit this destination --
        # the whole point of routing by market rather than by brand habit. It goes here, beside the
        # platform note it explains.
        (f'<p class="meta">Why these providers: {esc(regional.get("selection_basis"))}</p>'
         if regional.get("selection_basis") else "")
        + (f'<p class="meta">Platform selection: {esc(regional.get("booking_platform_selection_note"))}</p>'
           if regional.get("booking_platform_selection_note") else "")
    )
    return f'''<!doctype html><html lang="{attr(trip["language"])}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>{esc(trip["title"])}</title><style>:root{{--ink:#162235;--muted:#5d6b7c;--paper:#f7f9fc;--card:#fff;--accent:#0b6e69;--soft:#e4f4f1;--line:#d9e2ec;--warn:#8a4b08;--warn-bg:#fff5df}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}}main{{max-width:1120px;margin:auto;padding:32px 20px 56px}}h1{{font-size:clamp(2rem,5vw,3.6rem);line-height:1.05}}h2{{font-size:1.35rem}}h3{{font-size:1.05rem}}h4{{margin:18px 0 0;font-size:1rem}}.hero,.panel,.day-card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px;margin:20px 0;box-shadow:0 8px 24px rgb(20 40 65/.05)}}.hero{{background:linear-gradient(135deg,#fff,var(--soft))}}.grid,.dining-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:14px}}.fact,.option,.dining-stop{{border:1px solid var(--line);border-radius:12px;padding:14px}}.fact strong{{display:block}}.eyebrow,.meta{{color:var(--muted);font-size:.92rem}}.eyebrow{{color:var(--accent);font-weight:800;text-transform:uppercase;letter-spacing:.08em}}.pill{{display:inline-block;padding:3px 8px;border-radius:99px;background:var(--soft);color:#075952;font-size:.78rem;font-weight:700}}.day-top{{display:flex;justify-content:space-between;gap:16px}}.day-number{{min-width:48px;height:48px;display:grid;place-items:center;border-radius:50%;background:var(--ink);color:#fff;font-weight:800}}.timeline,.segment-list,.option-details{{list-style:none;padding:0}}.timeline li{{display:grid;grid-template-columns:88px 1fr;gap:12px;padding:12px 0;border-top:1px solid var(--line)}}.timeline time{{color:var(--accent);font-weight:800}}.option-details li{{margin:7px 0}}.segment-list{{margin:8px 0}}.route-segment{{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--line)}}.route-segment p{{margin:4px 0 0}}.route-map{{padding:14px;border-radius:12px;background:#f1f7f8;margin:16px 0}}.route-map svg{{display:block;width:100%;height:auto}}.route-map figcaption{{color:var(--muted);font-size:.88rem;margin-top:8px}}a{{color:#075952;font-weight:700}}.booking-link,.map-link,.dining-link,.dining-reservation-link{{display:inline-block;margin:8px 8px 0 0;padding:9px 12px;border-radius:9px;background:var(--accent);color:#fff;text-decoration:none}}.map-link{{background:var(--ink)}}.warning{{border-left:4px solid var(--warn);background:var(--warn-bg);padding:14px;border-radius:0 10px 10px 0}}@media(max-width:600px){{main{{padding:18px 12px 36px}}.hero,.panel,.day-card{{padding:18px}}.timeline li{{grid-template-columns:66px 1fr}}.route-segment{{align-items:flex-start;flex-direction:column}}}}.anchor-photo{{margin:10px 0 0}}.anchor-photo img{{width:100%;height:auto;border-radius:10px;display:block}}.photo-credit{{color:var(--muted);font-size:.72rem;margin-top:4px}}.hero-photo{{margin:20px 0}}.hero-photo img{{width:100%;max-height:340px;object-fit:cover;border-radius:16px}}@media print{{.hero-photo img{{max-height:200px}}}}{plan_visuals.VISUAL_CSS}@media print{{body{{background:#fff}}main{{max-width:none;padding:0}}.hero,.panel,.day-card{{box-shadow:none;break-inside:avoid}}.booking-link,.map-link,.dining-link,.dining-reservation-link{{color:#075952;background:transparent;padding:0;text-decoration:underline}}}}</style></head><body><main id="trip-plan" data-trip-plan><header id="trip-summary" class="hero"><p class="eyebrow">Plan status · {esc(plan.get("plan_status"))}</p><h1>{esc(trip["title"])}</h1><p>{esc(trip["origin"])} → {esc(trip["destination"])} · {esc(trip["start_date"])} to {esc(trip["end_date"])} · {esc(trip["traveler_count"])} traveller(s)</p><p class="meta">Arrival: {esc(trip["arrival_transport_mode"])} · Pace: {esc(trip["pace"])} · Currency: {esc(trip["currency"])} · Research last checked: {stamp(plan.get("generated_at"))}. Prices and availability require recheck before purchase.</p></header>{hero_photo}{unverified_banner}{constraints_panel}{preferences_panel}{page_nav}<section id="budget-summary" class="panel"><h2>Budget at a glance</h2><div class="grid"><div class="fact"><strong>{esc(total)}</strong><span>Comparable cost per person</span></div>{cap_fact}<div class="fact"><strong>{esc(trip["budget_basis"])}</strong><span>Included assumptions</span></div><div class="fact"><strong>{esc(plan["transport_preference"]["mode"])}</strong><span>Ground-mobility plan</span></div>{unpriced}</div>{budget_figure}{walking_figure}</section>{entry_panel}{budget_breakdown}{anchors}<section id="booking-panel" class="panel"><h2>Browse options — no purchase made</h2>{platform_note}<p class="meta">Current researched options only. Opening a link never creates a reservation.</p>{"".join(cards)}</section>{"".join(day_cards)}<section id="transport-overview" class="panel"><h2>Overall transport</h2><p>{esc(overview_headline)}</p>{"".join(f"<p>{esc(note)}</p>" for note in as_list(overview.get("notes")) if note)}<a class="map-link" data-map-scope="{attr(overview_scope)}" data-verified-at="{attr(overview["overall_map_checked_at"])}" href="{attr(overview["overall_route_map_url"])}" target="_blank" rel="noopener noreferrer">{overview_map_label}</a></section><section id="source-register" class="panel"><h2>Sources, confidence, and recheck list</h2><details open><summary>Sources used</summary><ul>{source_rows}</ul></details>{assumptions_block}<details open><summary>Recheck before purchase</summary><p>{recheck}</p></details>{gates_line}</section></main></body></html>'''


def render(plan: dict) -> str:
    """Render the page and localize renderer-owned text for the requested language."""
    page = decorate_primary_map_links(render_unlocalized(plan), plan)
    page = localize_static_page(page, plan["trip"]["language"], plan.get("ui_labels"))
    design = FINAL_PAGE_DESIGN + palette_css(palette_for(plan))
    return page.replace("</style>", design + "</style>", 1)


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
