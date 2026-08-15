#!/usr/bin/env python3
"""
MY STOCK DESK - 데이터 수집기

watchlist.json 을 읽어서 시세 / 공시 / 목표주가 / 뉴스를 모아
data.json 과 data.js 를 만듭니다. 화면(index.html)은 이 파일만 읽습니다.

  python fetch.py           실제 수집
  python fetch.py --demo    네트워크 없이 샘플 데이터 생성 (화면 확인용)

환경변수 (없으면 해당 블록만 건너뜁니다):
  DART_API_KEY   https://opendart.fss.or.kr 무료 발급
  SEC_UA         SEC EDGAR 요청용. 예: "Dana dana@example.com"
"""

import io
import json
import os
import sys
import time
import zipfile
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree

import pandas as pd
import requests

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))
DEMO = "--demo" in sys.argv


def _load_env():
    """connect.py 가 만든 .env 를 읽습니다. 환경변수가 있으면 그쪽이 우선입니다."""
    f = HERE / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

DART_KEY = os.environ.get("DART_API_KEY", "").strip()
SEC_UA = os.environ.get("SEC_UA", "").strip()

ERRORS = []


def log(msg):
    print(f"  {msg}", flush=True)


def note_error(source, err):
    """수집 실패를 화면에 남깁니다. 조용히 실패하면 나중에 못 찾습니다."""
    msg = f"{source}: {type(err).__name__} {err}"
    ERRORS.append(msg)
    log(f"[실패] {msg}")


# ─────────────────────────────────────────────────────────────
# 지표 계산
# ─────────────────────────────────────────────────────────────

def rsi14(close: pd.Series) -> float | None:
    """Wilder RSI(14)."""
    if len(close) < 20:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    last_loss = float(loss.iloc[-1])
    if last_loss == 0:
        return 100.0
    rs = float(gain.iloc[-1]) / last_loss
    return round(100 - (100 / (1 + rs)), 1)


def pct_since(close: pd.Series, days_back: int) -> float | None:
    """days_back 달력일 전의 가장 가까운 종가 대비 수익률."""
    if close.empty:
        return None
    target = close.index[-1] - pd.Timedelta(days=days_back)
    past = close[close.index <= target]
    if past.empty:
        return None
    base = float(past.iloc[-1])
    if base == 0:
        return None
    return round((float(close.iloc[-1]) / base - 1) * 100, 2)


def pct_ytd(close: pd.Series) -> float | None:
    if close.empty:
        return None
    year_start = pd.Timestamp(close.index[-1].year, 1, 1, tz=close.index.tz)
    this_year = close[close.index >= year_start]
    if len(this_year) < 2:
        return None
    base = float(this_year.iloc[0])
    if base == 0:
        return None
    return round((float(close.iloc[-1]) / base - 1) * 100, 2)


# ─────────────────────────────────────────────────────────────
# 1. 시세 (yfinance) — 미국주/한국주 모두 처리
# ─────────────────────────────────────────────────────────────

_TK_CACHE: dict = {}


def ticker(symbol: str):
    """같은 종목을 두 번 만들지 않습니다.

    시세와 목표주가를 따로 가져오면 종목당 야후 호출이 두 배가 되고,
    종목이 20개면 그만큼 차단당할 확률이 올라갑니다.
    """
    if symbol not in _TK_CACHE:
        import yfinance as yf
        _TK_CACHE[symbol] = yf.Ticker(symbol)
    return _TK_CACHE[symbol]


def fetch_quote(stock: dict) -> dict:
    symbol = stock["yahoo"]
    tk = ticker(symbol)
    hist = tk.history(period="1y", auto_adjust=False)
    if hist.empty:
        raise ValueError(f"{symbol} 히스토리가 비어있음 (티커 오타 확인)")

    close = hist["Close"].dropna()
    price = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else price

    lo52 = float(close.min())
    hi52 = float(close.max())
    span = hi52 - lo52
    position = round((price - lo52) / span * 100, 1) if span else 50.0

    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else None

    # 상장한 지 1년이 안 된 종목은 "52주"라는 말이 거짓말이 됩니다.
    # 화면이 라벨을 바꿔 달 수 있도록 여기서 표시해 둡니다.
    history_from = close.index[0].strftime("%Y-%m-%d")
    is_new = len(close) < 200

    out = {
        "history_from": history_from,
        "is_new_listing": is_new,
        "range_label": "상장 이후" if is_new else "52주",
        "price": round(price, 2),
        "change_abs": round(price - prev, 2),
        "change_pct": round((price / prev - 1) * 100, 2) if prev else 0.0,
        "returns": {
            "d1": round((price / prev - 1) * 100, 2) if prev else None,
            "w1": pct_since(close, 7),
            "m1": pct_since(close, 30),
            "m3": pct_since(close, 91),
            "ytd": pct_ytd(close),
        },
        "week52": {"low": round(lo52, 2), "high": round(hi52, 2), "position": position},
        "rsi14": rsi14(close),
        "above_ma20": bool(ma20 and price > ma20),
        "above_ma60": bool(ma60 and price > ma60),
        "sparkline": [round(float(v), 2) for v in close.tail(20)],
    }

    # 신규 상장주는 연초 수익률이 무의미하므로 공모가 대비로 대체합니다
    ipo = stock.get("ipo_price")
    if is_new and ipo:
        out["vs_ipo"] = round((price / ipo - 1) * 100, 2)
        out["ipo_price"] = ipo
        out["returns"]["ytd"] = None

    # 밸류에이션 지표는 있으면 좋고 없으면 넘어갑니다
    try:
        info = tk.get_info()
        out["per"] = round(info["trailingPE"], 1) if info.get("trailingPE") else None
        out["market_cap"] = info.get("marketCap")
    except Exception as e:
        note_error(f"info {symbol}", e)

    return out


# ─────────────────────────────────────────────────────────────
# 2. 목표주가 — 미국주는 자동, 한국주는 watchlist.json 수동값
# ─────────────────────────────────────────────────────────────

def fetch_target(stock: dict, price: float | None) -> dict | None:
    manual = (stock.get("manual_target") or {}).get("mean")
    if manual:
        m = stock["manual_target"]
        return {
            "mean": m["mean"],
            "upside": round((m["mean"] / price - 1) * 100, 1) if price else None,
            "buy": m.get("buy"), "hold": m.get("hold"), "sell": m.get("sell"),
            "source": "수동 입력", "asof": m.get("asof"),
        }

    if stock.get("type") != "listed":
        return None

    tk = ticker(stock["yahoo"])
    mean = None

    try:
        pt = tk.analyst_price_targets
        if isinstance(pt, dict):
            mean = pt.get("mean") or pt.get("median")
    except Exception:
        pass

    if mean is None:
        try:
            mean = tk.get_info().get("targetMeanPrice")
        except Exception as e:
            note_error(f"target {stock['yahoo']}", e)

    if not mean:
        return None

    buy = hold = sell = None
    try:
        rec = tk.recommendations
        if rec is not None and len(rec):
            row = rec.iloc[0]
            buy = int(row.get("strongBuy", 0)) + int(row.get("buy", 0))
            hold = int(row.get("hold", 0))
            sell = int(row.get("sell", 0)) + int(row.get("strongSell", 0))
    except Exception:
        pass

    return {
        "mean": round(float(mean), 2),
        "upside": round((float(mean) / price - 1) * 100, 1) if price else None,
        "buy": buy, "hold": hold, "sell": sell,
        "source": "Yahoo 컨센서스", "asof": datetime.now(KST).strftime("%Y-%m-%d"),
    }


# ─────────────────────────────────────────────────────────────
# 3. 한국 공시 (DART) — 무료, 일 20,000건
# ─────────────────────────────────────────────────────────────

CORP_CACHE = HERE / ".corp_codes.json"


def dart_corp_map() -> dict:
    """종목코드 → DART 고유번호. 하루 한 번만 내려받고 캐시합니다."""
    if CORP_CACHE.exists():
        age = time.time() - CORP_CACHE.stat().st_mtime
        if age < 86400:
            return json.loads(CORP_CACHE.read_text())

    r = requests.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": DART_KEY}, timeout=30,
    )
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml = z.read(z.namelist()[0])

    mapping = {}
    for el in ElementTree.fromstring(xml).iter("list"):
        code = (el.findtext("stock_code") or "").strip()
        if code:
            mapping[code] = (el.findtext("corp_code") or "").strip()

    CORP_CACHE.write_text(json.dumps(mapping))
    return mapping


def fetch_dart(stock: dict, corp_map: dict, lookback: int) -> list:
    corp = corp_map.get(stock["krx_code"])
    if not corp:
        raise ValueError(f"DART 고유번호 없음: {stock['krx_code']}")

    today = datetime.now(KST)
    r = requests.get(
        "https://opendart.fss.or.kr/api/list.json",
        params={
            "crtfc_key": DART_KEY, "corp_code": corp,
            "bgn_de": (today - timedelta(days=lookback)).strftime("%Y%m%d"),
            "end_de": today.strftime("%Y%m%d"),
            "page_count": 20,
        }, timeout=20,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("status") not in ("000", "013"):  # 013 = 조회결과 없음
        raise ValueError(f"DART status {body.get('status')} {body.get('message')}")

    items = []
    for it in body.get("list", []):
        d = it["rcept_dt"]
        items.append({
            "kind": "dart", "source": "DART 공시",
            "who": stock["name"], "stock_id": stock["id"],
            "title": it["report_nm"].strip(),
            "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['rcept_no']}",
        })
    return items


# ─────────────────────────────────────────────────────────────
# 4. 미국 공시 (SEC EDGAR) — 무료, User-Agent 필수
# ─────────────────────────────────────────────────────────────

CIK_CACHE = HERE / ".sec_ciks.json"
# 미국 기업은 8-K/10-Q/10-K, 외국기업(SEALSQ·Reitar 등)은 6-K/20-F 로 제출합니다.
# 외국기업 서식을 빼면 그 종목만 공시가 통째로 비어 보입니다.
WATCHED_FORMS = {"8-K", "10-Q", "10-K", "4", "S-1", "424B4", "6-K", "20-F", "40-F"}


def sec_cik_map() -> dict:
    if CIK_CACHE.exists() and time.time() - CIK_CACHE.stat().st_mtime < 604800:
        return json.loads(CIK_CACHE.read_text())

    r = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": SEC_UA}, timeout=20,
    )
    r.raise_for_status()
    mapping = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in r.json().values()}
    CIK_CACHE.write_text(json.dumps(mapping))
    return mapping


def fetch_sec(stock: dict, cik_map: dict, lookback: int) -> list:
    cik = cik_map.get(stock["sec_ticker"])
    if not cik:
        raise ValueError(f"CIK 없음: {stock['sec_ticker']}")

    r = requests.get(
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        headers={"User-Agent": SEC_UA}, timeout=20,
    )
    r.raise_for_status()
    recent = r.json()["filings"]["recent"]

    cutoff = (datetime.now(KST) - timedelta(days=lookback)).strftime("%Y-%m-%d")
    items = []
    for form, date, acc, doc in zip(
        recent["form"], recent["filingDate"],
        recent["accessionNumber"], recent["primaryDocument"],
    ):
        if date < cutoff or form not in WATCHED_FORMS:
            continue
        acc_clean = acc.replace("-", "")
        items.append({
            "kind": "sec", "source": f"SEC {form}",
            "who": stock["name"], "stock_id": stock["id"],
            "title": f"{form} 제출 — {FORM_KO.get(form, '정기/수시 보고')}",
            "date": date,
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_clean}/{doc}",
        })
        if len(items) >= 8:
            break
    return items


FORM_KO = {
    "8-K": "주요 경영사항 수시보고",
    "10-Q": "분기 보고서",
    "10-K": "연간 보고서",
    "4": "내부자 지분 변동 신고",
    "S-1": "증권신고서",
    "424B4": "최종 투자설명서",
    "6-K": "외국기업 수시보고",
    "20-F": "외국기업 연간 보고서",
    "40-F": "캐나다 기업 연간 보고서",
}


# ─────────────────────────────────────────────────────────────
# 5. 뉴스 (yfinance) — 비상장 종목은 키워드 검색으로
# ─────────────────────────────────────────────────────────────

def fetch_news(stock: dict, limit: int = 3) -> list:
    symbol = stock.get("yahoo")
    if not symbol:
        return []

    raw = ticker(symbol).news or []
    items = []
    for n in raw[:limit]:
        c = n.get("content", n)  # yfinance 버전에 따라 구조가 다릅니다
        title = c.get("title")
        if not title:
            continue
        url = (c.get("canonicalUrl") or {}).get("url") or n.get("link", "")
        pub = c.get("pubDate") or ""
        date = pub[:10] if pub else datetime.now(KST).strftime("%Y-%m-%d")
        if not pub and n.get("providerPublishTime"):
            date = datetime.fromtimestamp(
                n["providerPublishTime"], KST).strftime("%Y-%m-%d")
        items.append({
            "kind": "news", "source": "뉴스",
            "who": stock["name"], "stock_id": stock["id"],
            "title": title, "date": date, "url": url,
        })
    return items


# ─────────────────────────────────────────────────────────────
# 데모 데이터 (네트워크 없이 화면부터 확인할 때)
# ─────────────────────────────────────────────────────────────

def demo_quote(base: float, drift: float, seed_key: str = "") -> dict:
    random.seed(f"{seed_key}:{base}")
    series, p = [], base * 0.94
    for _ in range(20):
        p *= 1 + random.uniform(-0.018, 0.018) + drift / 100 / 20
        series.append(round(p, 2))
    price = series[-1]
    lo, hi = price * 0.62, price * 1.09
    return {
        "price": price,
        "change_abs": round(price - series[-2], 2),
        "change_pct": round((price / series[-2] - 1) * 100, 2),
        "returns": {
            "d1": round((price / series[-2] - 1) * 100, 2),
            "w1": round(random.uniform(-4, 7), 2),
            "m1": round(drift, 2),
            "m3": round(drift * 2.1, 2),
            "ytd": round(drift * 3.4, 2),
        },
        "week52": {"low": round(lo, 2), "high": round(hi, 2),
                   "position": round((price - lo) / (hi - lo) * 100, 1)},
        "rsi14": round(random.uniform(41, 69), 1),
        "above_ma20": drift > 0, "above_ma60": drift > -3,
        "sparkline": series,
        "per": round(random.uniform(15, 60), 1),
        "market_cap": None,
    }


DEMO_SEED = {
    "nvda":   {"base": 225.30, "drift": 11.8},
    "tsla":   {"base": 339.96, "drift": -2.3},
    "spacex": {"base": 140.00, "drift": 14.6},
    "googl":  {"base": 346.36, "drift": 4.2},
    "pltr":   {"base": 178.40, "drift": 8.9},
    "laes":   {"base": 3.33,   "drift": -6.4},
    "bmnr":   {"base": 27.59,  "drift": 22.1},
    "ritr":   {"base": 1.02,   "drift": -12.8},
    "qqq":    {"base": 648.20, "drift": 3.6},
    "005930": {"base": 88400,  "drift": 5.1},
    "035420": {"base": 236500, "drift": 9.2},
    "377300": {"base": 31450,  "drift": -3.1},
    "323410": {"base": 24700,  "drift": 2.4},
    "120110": {"base": 42300,  "drift": 6.7},
    "019680": {"base": 5380,   "drift": 15.2},
    "084680": {"base": 1742,   "drift": -4.9},
    "036620": {"base": 4115,   "drift": 7.3},
    "900290": {"base": 3000,   "drift": -1.8},
    "364980": {"base": 8940,   "drift": 5.5},
    "472170": {"base": 14355,  "drift": 2.1},
}


def build_demo(cfg: dict) -> dict:
    stocks, feed = [], []

    for s in cfg["stocks"]:
        if s["type"] == "private":
            stocks.append({**meta(s), "manual": s.get("manual", {})})
            continue

        seed = DEMO_SEED.get(s["id"], {"base": 100.0, "drift": 3.0})
        q = demo_quote(seed["base"], seed["drift"], s["id"])

        # 신규 상장주는 데모에서도 짧은 이력으로 취급합니다
        if s.get("listed_on") and s["listed_on"] >= "2025-11-01":
            q.update({
                "history_from": s["listed_on"], "is_new_listing": True,
                "range_label": "상장 이후",
            })
            q["returns"]["m3"] = None
            q["returns"]["ytd"] = None
            if s.get("ipo_price"):
                q["ipo_price"] = s["ipo_price"]
                q["vs_ipo"] = round((q["price"] / s["ipo_price"] - 1) * 100, 2)
        else:
            q.update({"history_from": "2025-08-15", "is_new_listing": False,
                      "range_label": "52주"})

        tgt = round(q["price"] * random.uniform(1.02, 1.24), 2)
        stocks.append({
            **meta(s), **q,
            "target": {"mean": tgt,
                       "upside": round((tgt / q["price"] - 1) * 100, 1),
                       "buy": random.randint(15, 40), "hold": random.randint(2, 20),
                       "sell": random.randint(0, 4),
                       "source": "데모", "asof": "2026-08-14"},
        })

    for who, title, kind, src in [
        ("삼성전자", "주요사항보고서 — 신규 시설투자 등", "dart", "DART 공시"),
        ("SpaceX", "8-K 제출 — 주요 경영사항 수시보고", "sec", "SEC 8-K"),
        ("NAVER", "기업설명회(IR) 개최 안내", "dart", "DART 공시"),
        ("테슬라", "4 제출 — 내부자 지분 변동 신고", "sec", "SEC 4"),
        ("SpaceX", "보호예수 해제 물량 관련 보도", "news", "뉴스"),
        ("엔비디아", "데이터센터 GPU 공급 계약 보도", "news", "뉴스"),
    ]:
        feed.append({"kind": kind, "source": src, "who": who, "title": title,
                     "date": "2026-08-14", "url": "#", "stock_id": ""})

    return {"stocks": stocks, "feed": feed}


# ─────────────────────────────────────────────────────────────

def load_prev() -> dict:
    """직전 data.json 을 종목 id 로 색인해 둡니다.

    GitHub Actions 에서는 야후가 간헐적으로 막힙니다. 그때 카드를 비우면
    화면이 통째로 무너지므로, 마지막으로 성공한 값을 그대로 쓰고
    '갱신 안 됨' 표시만 붙입니다.
    """
    f = HERE / "data.json"
    if not f.exists():
        return {"stocks": {}, "feed": [], "at": None}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return {
            "stocks": {s["id"]: s for s in d.get("stocks", []) if s.get("id")},
            "feed": d.get("feed", []),
            "at": d.get("generated_at"),
        }
    except Exception:
        return {"stocks": {}, "feed": [], "at": None}


def meta(s: dict) -> dict:
    return {k: s.get(k) for k in
            ("id", "name", "type", "market", "currency", "yahoo", "listed_on")}


def market_status() -> dict:
    now = datetime.now(KST)
    wd, hm = now.weekday(), now.hour * 60 + now.minute
    if wd >= 5:
        return {"kr": "휴장", "us": "휴장"}
    kr = "장중" if 540 <= hm <= 930 else ("장 마감" if hm > 930 else "개장 전")
    us = "장중" if (hm >= 1350 or hm <= 300) else "개장 전"
    return {"kr": kr, "us": us}


def main():
    cfg = json.loads((HERE / "watchlist.json").read_text(encoding="utf-8"))
    lookback = cfg.get("dart_lookback_days", 14)

    if DEMO:
        log("데모 모드 — 네트워크 없이 샘플 데이터를 만듭니다")
        payload = build_demo(cfg)
        stocks, feed = payload["stocks"], payload["feed"]
    else:
        stocks, feed = [], []
        corp_map, cik_map = {}, {}
        prev = load_prev()

        if DART_KEY:
            try:
                corp_map = dart_corp_map()
                log(f"DART 종목 매핑 {len(corp_map)}건")
            except Exception as e:
                note_error("DART corpCode", e)
        else:
            note_error("DART", "DART_API_KEY 환경변수가 없어 한국 공시를 건너뜁니다")

        if SEC_UA:
            try:
                cik_map = sec_cik_map()
            except Exception as e:
                note_error("SEC CIK", e)
        else:
            note_error("SEC", "SEC_UA 환경변수가 없어 미국 공시를 건너뜁니다")

        for s in cfg["stocks"]:
            log(f"수집 중 · {s['name']}")

            if s["type"] == "private":
                stocks.append({**meta(s), "manual": s["manual"]})
                continue

            row = meta(s)
            try:
                row.update(fetch_quote(s))
            except Exception as e:
                note_error(f"시세 {s['name']}", e)
                old = prev["stocks"].get(s["id"])
                if old and old.get("price") is not None:
                    row.update({k: v for k, v in old.items() if k not in row or row[k] is None})
                    row["stale"] = True
                    row["stale_since"] = old.get("stale_since") or prev["at"]
                    log(f"  └ 직전 값 유지 ({row['stale_since']} 기준)")

            try:
                t = fetch_target(s, row.get("price"))
                if t:
                    row["target"] = t
            except Exception as e:
                note_error(f"목표주가 {s['name']}", e)

            stocks.append(row)

            if s.get("krx_code") and corp_map:
                try:
                    feed += fetch_dart(s, corp_map, lookback)
                except Exception as e:
                    note_error(f"DART {s['name']}", e)

            if s.get("sec_ticker") and cik_map:
                try:
                    feed += fetch_sec(s, cik_map, lookback)
                except Exception as e:
                    note_error(f"SEC {s['name']}", e)

            try:
                feed += fetch_news(s)
            except Exception as e:
                note_error(f"뉴스 {s['name']}", e)

            time.sleep(0.4)  # API 예의

        # 공시·뉴스를 한 건도 못 받았으면 직전 피드를 그대로 둡니다
        if not feed and prev["feed"]:
            feed = prev["feed"]
            note_error("피드", "이번 수집 0건 — 직전 피드를 유지합니다")

    feed.sort(key=lambda x: x["date"], reverse=True)
    feed = feed[: cfg.get("news_feed_size", 12)]

    data = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "demo": DEMO,
        "market": market_status(),
        "stocks": stocks,
        "feed": feed,
        "errors": ERRORS,
    }

    # 이전 데이터 백업 — 수집이 망가져도 어제 화면은 남습니다
    out_json = HERE / "data.json"
    if out_json.exists():
        (HERE / "data.prev.json").write_text(
            out_json.read_text(encoding="utf-8"), encoding="utf-8")

    out_json.write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    # data.js 는 file:// 로 열 때 필요합니다 (fetch 는 CORS 에 막힙니다)
    (HERE / "data.js").write_text(
        "window.DESK_DATA = " + json.dumps(data, ensure_ascii=False) + ";",
        encoding="utf-8")

    log(f"완료 · 종목 {len(stocks)} · 피드 {len(feed)} · 실패 {len(ERRORS)}")


if __name__ == "__main__":
    main()
