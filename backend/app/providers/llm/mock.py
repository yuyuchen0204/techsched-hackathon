"""MockLLMProvider: deterministic rule/alias matching against the REAL catalog (English + Chinese aliases)."""
from __future__ import annotations

import re
from typing import Any

from app.providers.llm.base import ComplaintClassification, InterpretedRequest
from app.services.catalog_importer import normalize_key

TRADE_ALIASES: dict[str, list[str]] = {
    "Air Conditioning": ["aircon", "air con", "air-con", "ac ", "a/c", "air conditioning", "air conditioner", "空调", "冷气"],
    "Refrigerator": ["fridge", "refrigerator", "freezer", "冰箱", "冰柜"],
    "Washing Machine": ["washing machine", "washer", "laundry", "洗衣机"],
    "Water Heater": ["water heater", "heater", "hot water", "storage heater", "热水器", "热水"],
    "Plumbing & Bathroom": ["plumbing", "pipe", "toilet", "tap", "faucet", "drain", "sink", "bathroom", "水管", "马桶", "厕所", "水龙头", "下水", "浴室", "漏水"],
    "Electrical & Lighting": ["electrical", "electric", "outlet", "socket", "power", "light", "lamp", "switch", "circuit", "wiring", "breaker", "trip", "电", "灯", "插座", "开关", "跳闸", "线路"],
    "Locks & Hardware": ["lock", "door handle", "hinge", "security door", "handle", "锁", "门把", "把手", "铰链", "防盗门"],
    "Gas Stove": ["gas stove", "stove", "burner", "hob", "cooker", "煤气", "燃气", "灶", "炉"],
    "Furniture & Woodwork": ["furniture", "cabinet", "drawer", "table", "chair", "assembly", "wardrobe", "家具", "柜", "抽屉", "桌", "椅", "组装"],
    "Network & Smart Devices": ["wifi", "wi-fi", "internet", "router", "network", "smart lock", "camera", "smart", "网络", "路由", "无线", "摄像头", "智能"],
}

PROBLEM_KEYWORDS: dict[str, list[str]] = {
    "No cooling or heating": ["not cold", "no cooling", "not cooling", "warm air", "blowing warm", "no cold", "不冷", "不制冷", "不凉", "吹热风"],
    "Not cooling": ["not cold", "not cooling", "warm", "不冷", "不制冷"],
    "Water leakage": ["leak", "dripping", "drip", "water coming", "漏水", "滴水"],
    "Pipe leakage": ["pipe leak", "leaking pipe", "pipe", "水管漏", "漏水"],
    "Unusual noise": ["noise", "noisy", "rattling", "loud", "sound", "buzzing", "噪音", "响", "异响", "声音"],
    "Unusual noise or excessive vibration": ["noise", "vibration", "shaking", "vibrate", "噪音", "震动", "抖"],
    "Remote control or panel malfunction": ["remote", "panel", "display", "遥控", "面板"],
    "Compressor failure": ["compressor", "压缩机"],
    "Refrigerant refill or leakage": ["refrigerant", "gas top up", "top up gas", "freon", "雪种", "制冷剂", "加气"],
    "Worn door seal not closing properly": ["door seal", "not closing", "seal", "gasket", "门封", "关不严"],
    "Icing or defrost malfunction": ["ice", "frost", "icing", "defrost", "结冰", "结霜", "化霜"],
    "Not draining": ["not draining", "drain", "water stays", "won't drain", "不排水", "排水"],
    "Spin cycle malfunction": ["spin", "not spinning", "drum", "不甩干", "不转", "脱水"],
    "Won't start": ["won't start", "wont start", "not starting", "doesn't start", "dead", "no power", "不启动", "打不开", "开不了", "不开机"],
    "No hot water": ["no hot water", "cold water", "not heating", "没热水", "不热", "不加热"],
    "Gas water heater won't ignite": ["ignite", "won't light", "no flame", "打不着", "点不着"],
    "Unstable temperature": ["unstable", "hot and cold", "fluctuat", "temperature", "忽冷忽热", "温度不稳"],
    "Gas leak safety hazard": ["gas leak", "smell gas", "gas smell", "漏气", "煤气味", "燃气泄漏"],
    "Toilet clog": ["toilet clog", "toilet block", "toilet not flushing", "clogged toilet", "toilet", "clogged", "马桶堵", "厕所堵", "冲不下", "马桶", "厕所"],
    "Toilet leak or tank malfunction": ["toilet leak", "tank", "flush", "cistern", "马桶漏", "水箱"],
    "Dripping or faulty faucet": ["faucet", "tap", "dripping tap", "水龙头"],
    "Drain blockage": ["drain block", "blocked drain", "clog", "blockage", "sink block", "下水堵", "排水堵", "堵塞"],
    "Burst pipe emergency": ["burst", "flooding", "爆管", "爆裂", "喷水"],
    "Outlet no power": ["outlet", "socket", "no power", "sockets dead", "插座", "没电"],
    "Light fixture not working": ["light not working", "light dead", "lamp", "ceiling light", "灯不亮", "灯坏"],
    "Circuit trip or short circuit": ["trip", "tripped", "short circuit", "breaker", "跳闸", "短路"],
    "Exposed or aging wiring safety hazard": ["exposed wire", "wiring", "aging", "sparks", "电线裸露", "老化", "火花"],
    "Switch malfunction": ["switch", "开关"],
    "Lock won't open": ["won't open", "cannot open", "can't open", "jammed", "stuck lock", "locked out", "打不开", "锁死", "卡住"],
    "Lock damaged needs replacement": ["replace lock", "damaged lock", "broken lock", "换锁", "锁坏"],
    "Loose door handle or hinge": ["loose", "handle", "hinge", "松", "把手", "铰链"],
    "Security door warped or stuck": ["security door", "warped", "door stuck", "防盗门", "变形"],
    "Won't ignite": ["won't ignite", "no spark", "not lighting", "打不着", "点不着", "没火"],
    "Abnormal flame yellow or unstable": ["yellow flame", "flame", "unstable flame", "黄火", "火焰"],
    "Igniter malfunction": ["igniter", "点火器"],
    "Furniture assembly": ["assemble", "assembly", "install furniture", "组装", "安装"],
    "Cabinet door or drawer damage": ["cabinet", "drawer", "hinge broken", "柜门", "抽屉"],
    "Loose table or chair repair": ["table", "chair", "wobbly", "loose leg", "桌", "椅", "摇晃"],
    "WiFi connection failure": ["wifi", "wi-fi", "no internet", "internet down", "connection", "上不了网", "断网", "无线"],
    "Smart lock or camera malfunction": ["smart lock", "camera", "doorbell", "智能锁", "摄像头"],
    "Router or network device installation": ["install router", "router installation", "new router", "setup", "装路由", "安装路由"],
}

INTENT_WORDS = {
    "cancel": ["cancel", "取消", "不需要了", "不用了"],
    "status": ["status", "when", "eta", "where is", "arriv", "进度", "什么时候", "到了吗", "几点"],
    "complaint": ["complain", "complaint", "late", "rude", "unhappy", "angry", "terrible", "投诉", "迟到", "态度", "不满", "差"],
    "expedite": ["expedite", "hurry", "faster", "sooner", "speed up", "speed it up", "quicker", "加急", "快一点", "快点", "提前"],
}
URGENCY_WORDS = ["urgent", "asap", "emergency", "immediately", "right now", "紧急", "马上", "尽快", "急"]
PAYMENT_WORDS = ["paid", "payment", "i have paid", "already paid", "expedite fee", "付了", "已付款", "加急费", "付款"]


def _contains(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


class MockLLMProvider:
    name = "mock"
    model = "rule-alias-matcher"

    def interpret(self, text: str, catalog: list[dict[str, Any]], context: dict[str, Any]) -> InterpretedRequest:
        raw = text or ""
        low = raw.lower()
        norm = normalize_key(raw)
        language = "zh" if re.search(r"[一-鿿]", raw) else "en"
        intent = "new_request"
        for name, words in INTENT_WORDS.items():
            if _contains(low, words):
                intent = name
                break
        trade_hits: dict[str, int] = {}
        for trade, aliases in TRADE_ALIASES.items():
            hits = sum(1 for a in aliases if a in low)
            if hits:
                trade_hits[trade] = hits
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in catalog:
            score = 0.0
            if item["trade_type"] in trade_hits:
                score += 2.0 * trade_hits[item["trade_type"]]
            kws = PROBLEM_KEYWORDS.get(item["problem_name"], [])
            score += sum(1.5 for k in kws if k in low)
            # direct textual overlap with the catalog problem name
            score += sum(0.5 for tok in normalize_key(item["problem_name"]).split() if len(tok) > 3 and tok in norm)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda x: (-x[0], x[1]["source_row"]))
        best = scored[0] if scored else None
        # a problem keyword alone (no trade) is ambiguous across trades → ask
        confident = best is not None and best[0] >= 3.0 and (len(scored) < 2 or best[0] > scored[1][0])
        alternatives = [it["id"] for _, it in scored[1:4]]
        phone = re.search(r"(\+?\d[\d \-]{6,}\d)", raw)
        name_m = re.search(r"(?:my name is|this is|\bname:?|我叫|我是)\s*([A-Za-z一-鿿][A-Za-z一-鿿'.-]*(?:\s[A-Z][A-Za-z'.-]*)?)", raw, re.IGNORECASE)
        loc_hint = None
        for loc in context.get("location_names", []):
            if loc.lower() in low or (loc.split()[0].lower() in low and len(loc.split()[0]) > 3):
                loc_hint = loc
                break
        if loc_hint is None:
            addr = re.search(r"((?:blk|block)\s*\d+[a-z]?\b[^,.;，。]*|\b\d{1,4}[a-z]?\s+[a-z][a-z .']*?\b(?:street|st|road|rd|avenue|ave|"
                             r"drive|dr|lane|crescent|cres|close|walk|way|terrace|link|place)\b(?:\s*\d{1,3})?|\b\d{6}\b)", low)
            if addr:
                loc_hint = addr.group(1).strip()
        if loc_hint is None:
            zh = re.search(r"([一-鿿]{2,6}(?:路|街|道|巷|大道|组屋|座)\s*\d{0,4}(?:号|座)?)", raw)
            if zh:
                loc_hint = zh.group(1)
        time_m = re.search(r"((?:between\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*(?:-|–|to|and|到|至)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
                           r"|\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|morning|afternoon|evening|上午|下午|晚上|\d{1,2}点(?:到\d{1,2}点)?)", low)
        question = None
        if intent == "new_request":
            if best is None:
                question = "Could you tell me which appliance or area has the problem (e.g. aircon, fridge, plumbing) and what is wrong?"
            elif not confident:
                question = f"I found a few possible issues. Is it '{best[1]['trade_type']} – {best[1]['problem_name']}', or one of the alternatives below?"
        return InterpretedRequest(
            catalog_item_id=best[1]["id"] if best is not None and confident else None,
            confidence=min(1.0, (best[0] / 6.0)) if best else 0.0,
            alternatives=([best[1]["id"]] if best and not confident else []) + alternatives,
            customer_name=name_m.group(1).strip() if name_m else None,
            contact_phone=phone.group(1).strip() if phone else None,
            location_hint=loc_hint, time_hint=time_m.group(1) if time_m else None,
            urgency_mentioned=_contains(low, URGENCY_WORDS), payment_claimed=_contains(low, PAYMENT_WORDS),
            intent=intent, language=language, clarifying_question=question,
            summary=f"{best[1]['trade_type']} – {best[1]['problem_name']}" if best else "unrecognised problem",
        )

    def classify_complaint(self, text: str) -> ComplaintClassification:
        low = (text or "").lower()
        if _contains(low, ["late", "waiting", "not here", "didn't come", "no show", "delay", "迟到", "还没来", "等了", "晚"]):
            return ComplaintClassification(complaint_type="lateness", confidence=0.8, rationale="mentions waiting/lateness")
        if _contains(low, ["rude", "attitude", "impolite", "unfriendly", "态度", "粗鲁", "没礼貌"]):
            return ComplaintClassification(complaint_type="attitude", confidence=0.8, rationale="mentions attitude")
        if _contains(low, ["broke again", "still not", "poor quality", "didn't fix", "not fixed", "damage", "质量", "没修好", "又坏"]):
            return ComplaintClassification(complaint_type="quality", confidence=0.7, rationale="mentions repair quality")
        return ComplaintClassification(complaint_type="other", confidence=0.4, rationale="no scheduling keywords found")
