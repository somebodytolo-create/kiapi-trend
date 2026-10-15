# -*- coding: utf-8 -*-
"""뉴스·정책 수집기 (GitHub Actions에서 하루 3회 실행).

data/kw.enc.json(키워드 묶음, 로컬 PC가 성과 엑셀에서 갱신)을 읽어
Google 뉴스 RSS로 묶음별 검색어를 돌리고, 정책은 정책브리핑(korea.kr) 결과만 따로 받는다.
제목·링크·출처·날짜만 보관하며 월별 파일(data/YYYY-MM.enc.json)에 누적한다.
"""
import os, re, sys, json, time, glob, hashlib, datetime, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from vault import seal, unseal

PW = os.environ.get("TREND_PW") or sys.exit("TREND_PW 환경변수가 없습니다")
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
UA = "Mozilla/5.0 (compatible; kiapi-trend/1.0)"
# 주식 종목 기사·사진 기사·해외 시장보고서 광고 등은 동향이 아니라 버린다
SPAM = ["관련주", "특징주", "급등", "주가", "[포토]", "[사진]", "포토]", "포토뉴스", "시장 규모", "CAGR", "QYResearch",
        "부고", "[인사]", "인사]", "증시", "목표가", "매수"]
# 제목에 정부·국회·제도 표현이 있으면 언론 기사여도 '정책'으로 분류
POLICY_WORDS = ["산업부", "산업통상", "국토부", "국토교통부", "과기정통부", "과기부", "과학기술정보통신부", "경찰청",
                "중기부", "환경부", "정부", "국회", "법안", "개정안", "시행령", "시행규칙", "특별법", "규제", "로드맵",
                "예산", "기본계획", "종합계획", "가이드라인", "제도", "고시", "의무화", "국정", "장관", "차관", "대통령"]


def read(path):
    with open(path, encoding="utf-8") as f:
        return unseal(f.read(), PW)


def write(path, obj, salt):
    text = seal(obj, PW, salt)
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


def gnews(query):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                root = ET.fromstring(r.read())
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        name = (src.text or "").strip() if src is not None else ""
        title = (it.findtext("title") or "").strip()
        if name and title.endswith(" - " + name):
            title = title[: -len(name) - 3].strip()
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            pub = NOW
        out.append({"t": title, "u": it.findtext("link") or "", "s": name,
                    "d": urllib.parse.urlparse(src.get("url", "")).netloc if src is not None else "",
                    "p": pub.strftime("%Y-%m-%d %H:%M")})
    return out


def norm(title):
    return re.sub(r"[^0-9a-z가-힣]", "", title.lower())


def hit(title, groups):
    t = title.lower()
    return all(any(w.lower() in t for w in g) for g in groups)


def is_gov(domain):
    return domain.endswith("korea.kr") or domain.endswith(".go.kr")


def kind_of(raw, forced):
    if forced or is_gov(raw["d"]):
        return "gov"        # 정부 발표(정책브리핑·부처·지자체)
    if any(w in raw["t"] for w in POLICY_WORDS):
        return "policy"     # 정책 관련 언론 기사
    return "news"


def main():
    kw = read(os.path.join(DATA, "kw.enc.json"))
    salt = json.load(open(os.path.join(DATA, "kw.enc.json")))["salt"]

    items, months = {}, set()
    for path in sorted(glob.glob(os.path.join(DATA, "20??-??.enc.json"))):
        for x in read(path):
            items[x["id"]] = x
    meta_path = os.path.join(DATA, "meta.enc.json")
    meta = read(meta_path) if os.path.exists(meta_path) else {"runs": [], "qhits": {}}

    window = "30d" if not items else "3d"   # 첫 수집은 한 달치를 채운다
    before, new_ids, errors, qhits = len(items), set(), [], {}

    def add(raw, cid, kind, q):
        key = hashlib.sha1(norm(raw["t"]).encode()).hexdigest()[:12]
        x = items.get(key)
        if x is None:
            x = dict(raw, id=key, k=kind, c=[], f=NOW.strftime("%Y-%m-%d %H:%M"))
            items[key] = x
            new_ids.add(key)
        if cid not in x["c"]:
            x["c"].append(cid)
        rank = {"news": 0, "policy": 1, "gov": 2}
        if rank[kind] > rank[x["k"]]:
            x["k"] = kind
        return 1

    jobs = []
    for cl in kw["clusters"]:
        for q in cl["queries"]:
            jobs.append((cl, q["q"], f'{q["q"]} when:{window}', "news"))
        for p in cl["policy"]:
            jobs.append((cl, f"정책브리핑: {p}", f"{p} site:korea.kr when:{window}", "gov"))

    for cl, label, query, kind in jobs:
        try:
            res = gnews(query)
        except Exception as e:
            errors.append(f"{label}: {e}")
            continue
        n = 0
        for raw in res:
            if not raw["t"]:
                continue
            if any(w in raw["t"] for w in SPAM) or any(w in raw["t"] for w in cl.get("exclude", [])):
                continue
            k = kind_of(raw, kind == "gov")
            ok = hit(raw["t"], cl["must"]) or (k == "gov" and any(
                all(w.lower() in raw["t"].lower() for w in p.split()) for p in cl["policy"]))
            if ok:
                n += add(raw, cl["id"], k, label)
        qhits[f'{cl["id"]}|{label}'] = n
        time.sleep(1.0)

    # 월별 파일로 다시 나눠 쓰기 (바뀐 달만 실제로 파일이 바뀐다)
    by_month = {}
    for x in items.values():
        by_month.setdefault(x["p"][:7], []).append(x)
    for m, xs in by_month.items():
        xs.sort(key=lambda x: (x["p"], x["id"]), reverse=True)
        write(os.path.join(DATA, f"{m}.enc.json"), xs, salt)
        months.add(m)

    meta.setdefault("since", NOW.strftime("%Y-%m-%d %H:%M"))  # 첫 수집 시각(이때 채운 건 '신규' 표시 안 함)
    meta["months"] = sorted(months, reverse=True)
    meta["last_run"] = NOW.strftime("%Y-%m-%d %H:%M")
    meta["kw_updated"] = kw.get("updated", "")
    meta["runs"] = ([{"at": meta["last_run"], "new": len(new_ids), "total": len(items),
                      "errors": len(errors)}] + meta["runs"])[:60]
    meta["qhits"] = qhits
    meta["errors"] = errors[:20]
    write(meta_path, meta, salt)
    print(f"수집 {meta['last_run']}: 신규 {len(new_ids)}건 / 누적 {len(items)}건 (이전 {before}) / 오류 {len(errors)}")
    for e in errors[:10]:
        print("  !", e)


if __name__ == "__main__":
    main()
