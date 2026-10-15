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
        title = clean(title)
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
        except Exception:
            pub = NOW
        out.append({"t": title, "u": it.findtext("link") or "", "s": name,
                    "d": urllib.parse.urlparse(src.get("url", "")).netloc if src is not None else "",
                    "p": pub.strftime("%Y-%m-%d %H:%M")})
    return out


def clean(title):
    # 기관 사이트 제목 뒤에 붙는 메뉴 경로(" | 전체 | 뉴스클리핑 | 홍보관 …")를 잘라낸다
    return title.split(" | ")[0].strip() if " | " in title else title


def norm(title):
    return re.sub(r"[^0-9a-z가-힣]", "", title.lower())


def hit(title, groups):
    t = title.lower()
    return all(any(w.lower() in t for w in g) for g in groups)


# 부처·전문기관·공공연구기관 도메인 (정책브리핑에 안 올라오는 기관 자료·공고를 잡는다)
INST_SITES = ["molit.go.kr", "motie.go.kr", "msit.go.kr", "police.go.kr", "mss.go.kr", "me.go.kr", "kotsa.or.kr",
              "katech.re.kr", "keit.re.kr", "kiat.or.kr", "kaia.re.kr", "iitp.kr", "etri.re.kr", "koroad.or.kr",
              "kistep.re.kr", "kari.re.kr", "kiapi.or.kr", "kiro.re.kr", "kitech.re.kr", "nipa.kr", "ktl.re.kr",
              "krri.re.kr", "kict.re.kr", "koti.re.kr", "kipot.or.kr", "ntis.go.kr", "kosha.or.kr", "kotra.or.kr"]
INST_Q = "(" + " OR ".join("site:" + d for d in INST_SITES) + ")"
ANN_WORDS = ["공고", "모집", "공모", "시행계획", "선정계획", "참여기업", "수요조사"]
# IRIS 공고 중 특정 묶음에 안 걸려도 보관할 모빌리티 일반어
ANN_GENERAL = ["자동차", "미래차", "차량", "자율주행", "도로교통", "국토교통", "모빌리티산업", "모빌리티 "]
IRIS = "https://www.iris.go.kr/contents/"


def iris(prg, page):
    req = urllib.request.Request(IRIS + "retrieveBsnsAncmBtinSituList.do",
                                 data=urllib.parse.urlencode({"pageIndex": page, "ancmPrg": prg}).encode(),
                                 headers={"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"})
    for attempt in range(3):   # 해외(Actions) 접속은 가끔 느려 재시도한다
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read())
            return d.get("listBsnsAncmBtinSitu") or [], d["paginationInfo"]["totalPageCount"]
        except Exception:
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))


def is_gov(domain):
    return domain.endswith("korea.kr") or domain.endswith(".go.kr")


def is_inst(domain):
    return any(domain == d or domain.endswith("." + d) for d in INST_SITES) or         domain.endswith(".re.kr") or domain.endswith(".or.kr")


def kind_of(raw, forced):
    if is_inst(raw["d"]) and not is_gov(raw["d"]):
        return "inst"       # 공공·연구기관 자료
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
            x["t"] = clean(x["t"])
            items[x["id"]] = x
    meta_path = os.path.join(DATA, "meta.enc.json")
    meta = read(meta_path) if os.path.exists(meta_path) else {"runs": [], "qhits": {}}

    window = "30d" if not items else "3d"   # 첫 수집은 한 달치를 채운다
    inst_window = "3d" if meta.get("inst_init") else "60d"   # 공공기관 검색 첫 실행은 두 달치
    ann_path = os.path.join(DATA, "ann.enc.json")
    anns = {a["id"]: a for a in read(ann_path)} if os.path.exists(ann_path) else {}
    for a in list(anns.values()):   # 일반어 기준이 바뀌면 묶음 없는 IRIS 공고를 다시 거른다
        if a["src"] == "IRIS" and not a.get("c") and not any(w in a["t"] for w in ANN_GENERAL):
            del anns[a["id"]]
    ann_before = len(anns)

    def add_ann(a, cids):
        x = anns.get(a["id"])
        if x is None:
            a["f"] = NOW.strftime("%Y-%m-%d %H:%M")
            anns[a["id"]] = x = a
        else:
            x.update({k: v for k, v in a.items() if k != "c"})   # 상태·접수기간은 최신값으로
        x["c"] = sorted(set(x.get("c", [])) | set(cids))
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
        rank = {"news": 0, "policy": 1, "inst": 2, "gov": 3}
        if rank[kind] > rank[x["k"]]:
            x["k"] = kind
        return 1

    jobs = []
    for cl in kw["clusters"]:
        for q in cl["queries"]:
            jobs.append((cl, q["q"], f'{q["q"]} when:{window}', "news"))
        for p in cl["policy"]:
            jobs.append((cl, f"정책브리핑: {p}", f"{p} site:korea.kr when:{window}", "gov"))
            jobs.append((cl, f"공공기관: {p}", f"{p} {INST_Q} when:{inst_window}", "inst"))

    for cl, label, query, kind in jobs:
        try:
            res = gnews(query)
        except Exception as e:
            errors.append(f"{label}: {e}")
            continue
        n = 0
        for raw in res:
            if not raw["t"] or "HOME" in raw["t"] or " > " in raw["t"] or raw["t"] == raw["s"]:
                continue   # 기관 홈페이지·메뉴 페이지 제목은 버린다
            if any(w in raw["t"] for w in SPAM) or any(w in raw["t"] for w in cl.get("exclude", [])):
                continue
            k = kind_of(raw, kind == "gov")
            ok = hit(raw["t"], cl["must"]) or (k == "gov" and any(
                all(w.lower() in raw["t"].lower() for w in p.split()) for p in cl["policy"]))
            if ok and k in ("inst", "gov") and any(w in raw["t"] for w in ANN_WORDS):
                # 기관 사이트의 모집·공고 글은 사업공고 쪽으로 보관
                add_ann({"id": "w" + hashlib.sha1(norm(raw["t"]).encode()).hexdigest()[:11], "t": raw["t"],
                         "u": raw["u"], "org": raw["s"], "gov": "", "de": raw["p"][:10], "rs": "", "re": "",
                         "st": "", "src": raw["d"]}, [cl["id"]])
                n += 1
            elif ok:
                n += add(raw, cl["id"], k, label)
        qhits[f'{cl["id"]}|{label}'] = n
        time.sleep(1.0)

    # IRIS 사업공고: 진행중 전체 + 예정·마감 최근 몇 페이지(첫 실행은 더 깊게)
    deep = not meta.get("inst_init")
    for prg, pages in (("ancmIng", 99), ("ancmPre", 30 if deep else 5), ("ancmEnd", 30 if deep else 5)):
        page, total = 1, 1
        while page <= min(total, pages):
            try:
                rows, total = iris(prg, page)
            except Exception as e:
                errors.append(f"IRIS {prg} p{page}: {e}")
                break
            for r in rows:
                t = (r.get("ancmTl") or "").strip()
                if not t or "TEST" in t.upper():
                    continue
                tl = t.lower()
                cids = [cl["id"] for cl in kw["clusters"] if any(w.lower() in tl for w in cl["must"][0])
                        and not any(w in t for w in cl.get("exclude", []))]
                if not cids and not any(w in t for w in ANN_GENERAL):
                    continue
                add_ann({"id": "i" + r["ancmId"], "t": t, "u": IRIS + "retrieveBsnsAncmView.do?ancmId=" + r["ancmId"],
                         "org": r.get("sorgnNm") or "", "gov": r.get("blngGovdSeNm") or "", "no": r.get("ancmNo") or "",
                         "de": r.get("ancmDe") or "", "rs": (r.get("rcveStrDe") or "").replace(".", "-"),
                         "re": (r.get("rcveEndDe") or "").replace(".", "-"), "st": r.get("rcveStt") or "",
                         "src": "IRIS"}, cids)
            page += 1
            time.sleep(0.7)
    write(ann_path, sorted(anns.values(), key=lambda a: (a["de"], a["id"]), reverse=True), salt)
    meta["inst_init"] = True

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
    meta["ann_total"] = len(anns)
    write(meta_path, meta, salt)
    print(f"수집 {meta['last_run']}: 신규 {len(new_ids)}건 / 누적 {len(items)}건 (이전 {before}) / "
          f"공고 {len(anns)}건 (신규 {len(anns) - ann_before}) / 오류 {len(errors)}")
    for e in errors[:10]:
        print("  !", e)


if __name__ == "__main__":
    main()
