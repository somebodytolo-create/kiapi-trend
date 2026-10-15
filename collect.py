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
SPAM = ["관련주", "특징주", "급등", "주가", "[포토]", "[사진]", "포토]", "포토뉴스", "QYResearch",
        "부고", "[인사]", "인사]", "증시", "목표가", "매수", "오늘뉴스",
        " stock", "Stock", "shares", "Shares", "investor", "Investor", "Research and Markets", "ResearchAndMarkets",
        "株価", "株式", "股价", "股票", "概念股", "港股", "A股", "目标价", "涨停", "异动"]
# 투자·보도자료 배포처(시장보고서 광고가 대부분)
SPAM_SRC = ["Motley Fool", "Zacks", "Seeking Alpha", "Benzinga", "InvestorPlace", "TipRanks", "MarketBeat",
            "openPR", "EIN Presswire", "GlobeNewswire", "PR Newswire", "Yahoo Finance", "Simply Wall",
            # 시장조사 보고서 판매 페이지 — 제목만 '시장 규모'이고 수치가 없다
            "Fortune Business", "Straits Research", "Research Nester", "Market Growth", "Market Data Forecast",
            "Precedence Research", "Grand View", "Mordor", "Business Research Insights", "Global Market Insights",
            "IMARC", "Allied Market", "Spherical Insights", "Persistence Market", "Verified Market", "Cognitive Market",
            "Data Bridge", "Market.us", "Insight Partners", "Coherent Market", "MarketsandMarkets", "Market Research", "IndexBox", "Unisba",
            # 해외 정부 도메인이지만 동향이 아닌 곳(기업 등기·연구과제 DB)
            "Find and update company information", "CORDIS"]
REPORT_RE = re.compile(r"(규모|size|規模|规模).{0,25}(점유율|share|シェア|份额)", re.I)
# 대상 권역이 아닌 나라 이야기는 해외 사례에서 뺀다(대상 권역이 함께 나오면 유지)
OTHER_LAND = ["Ghana", "India", "Indian", "Singapore", "Philippines", "Kazakhstan", "Dubai", "UAE", "Saudi", "Australia",
              "Malaysia", "Thailand", "Vietnam", "Indonesia", "Nigeria", "Kenya", "Pakistan", "New Zealand", "Brazil"]
# 해외 정부 사이트 결과는 교통 맥락이 있어야 한다(로봇·항공 묶음 제외)
FGOV_CTX = {"en": ["vehicle", "driving", "car", "cars", "robotaxi", "mobility", "transport", "automotive", "traffic", "road",
                   "EV", "battery", "charging", "truck", "bus", "shuttle"],
            "ja": ["車", "バス", "タクシー", "トラック", "交通", "道路", "モビリティ", "運転"],
            "zh": ["汽车", "车辆", "道路", "出租", "乘用", "交通", "智能网联", "电动", "充电"]}
INTL_EXCLUDE = ["metro", "Metro", "train", "railway", "ship", "vessel", "maritime", "farming", "agricultur", "microscop",
                "spacecraft", "列车", "地铁", "船", "农用", "鉄道", "農業", "中标", "招标", "核查", "送达"]
EU_TLD = (".uk", ".de", ".fr", ".eu", ".nl", ".it", ".es", ".se", ".be", ".at", ".ch", ".dk", ".fi", ".no", ".pl", ".ie",
          ".cz", ".pt")
GENERIC_TLD = (".com", ".org", ".net", ".io", ".news", ".co", ".tv", ".ai", ".us", ".gov", ".edu", ".info", ".media")
# 시장 규모·전망·기대효과 표현 (제목에 있으면 '시장·효과'로 표시)
MARKET_WORDS = ["시장 규모", "시장규모", "시장 전망", "시장 성장", "글로벌 시장", "세계 시장", "점유율", "억 달러", "억달러",
                "조 원 규모", "조원 규모", "CAGR", "연평균", "성장률", "파급효과", "파급 효과", "경제적 효과", "경제효과",
                "기대효과", "생산유발", "일자리 창출", "부가가치", "시장 선점",
                "market size", "Market Size", "market share", "billion", "Billion", "forecast", "economic impact",
                "市場規模", "億円", "兆円", "経済効果", "シェア", "市场规模", "亿元", "万亿", "渗透率", "经济效益", "产值"]
# 해외 권역 판별어 (한 글자 표기 美·中·日은 한국어 제목에서만)
REGIONS = {
    "eu": ["유럽", "독일", "프랑스", "영국", "이탈리아", "스웨덴", "네덜란드", "스페인", "유엔", "UNECE", "EU",
           "Europe", "European", "Germany", "German", "France", "French", "UK", "Britain", "British", "Brussels",
           "Netherlands", "Sweden", "Spain", "Italy", "欧州", "ドイツ", "欧洲", "欧盟", "德国"],
    "na": ["미국", "캐나다", "캘리포니아", "텍사스", "애리조나", "NHTSA", "U.S.", "US", "United States", "America",
           "American", "Canada", "California", "Texas", "Arizona", "Federal", "FMCSA", "Congress", "Senate",
           "米国", "米", "美国"],
    "cn": ["중국", "베이징", "상하이", "선전", "China", "Chinese", "Beijing", "Shanghai", "Shenzhen", "中国"],
    "jp": ["일본", "도쿄", "Japan", "Japanese", "Tokyo", "日本"],
}
REGION_KO1 = {"美": "na", "中": "cn", "日": "jp"}
KOREA = ["Korea", "Korean", "Hyundai", "韓国", "韩国"]
# 해외 정부·국제기구 도메인
FGOV = (".gov", ".gov.uk", "europa.eu", "unece.org", ".go.jp", ".gov.cn", ".gc.ca", ".bund.de", ".gouv.fr")
FGOV_Q = {
    "na": "(site:nhtsa.gov OR site:transportation.gov OR site:whitehouse.gov OR site:dmv.ca.gov OR site:nist.gov OR site:faa.gov)",
    "eu": "(site:europa.eu OR site:unece.org OR site:gov.uk OR site:bmv.de)",
    "jp": "(site:mlit.go.jp OR site:meti.go.jp OR site:npa.go.jp OR site:soumu.go.jp OR site:kantei.go.jp)",
    "cn": "(site:gov.cn OR site:miit.gov.cn OR site:mot.gov.cn OR site:samr.gov.cn OR site:most.gov.cn)",
}
EDITIONS = {"ko": ("ko", "KR", "KR:ko"), "us": ("en-US", "US", "US:en"), "gb": ("en-GB", "GB", "GB:en"),
            "ja": ("ja", "JP", "JP:ja"), "zh": ("zh-CN", "CN", "CN:zh-Hans")}
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


def gnews(query, ed="ko"):
    hl, gl, ceid = EDITIONS[ed]
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": query, "hl": hl, "gl": gl, "ceid": ceid})
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


def lang_of(t):
    if re.search(r"[가-힣]", t):
        return "ko"
    if re.search(r"[\u3040-\u30ff]", t):
        return "ja"
    if re.search(r"[\u4e00-\u9fff]", t):
        return "zh"
    return "en"


def regions_of(t, lang):
    out = set()
    for r, ws in REGIONS.items():
        for w in ws:
            if re.fullmatch(r"[A-Za-z. ]+", w):   # 영문은 단어 단위로 (US가 'USB'에 걸리지 않게)
                if re.search(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", t):
                    out.add(r)
            elif w == "米":
                if lang == "ja" and re.search(r"米(?!国産|価|粉)", t):
                    out.add(r)
            elif w in t:
                out.add(r)
    if lang == "ko":
        out |= {r for ch, r in REGION_KO1.items() if ch in t}
    return out


def is_market(t):
    return any(w in t for w in MARKET_WORDS)


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


def is_fgov(domain):
    return any(domain.endswith(s) if s.startswith(".") else (domain == s or domain.endswith("." + s)) for s in FGOV)


def kind_of(raw, forced):
    if is_fgov(raw["d"]) and not is_gov(raw["d"]):
        return "fgov"       # 해외 정부·국제기구 자료
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
    intl_window = "3d" if meta.get("intl_init") else "30d"   # 해외 검색 첫 실행은 한 달치
    mk_window = "7d" if meta.get("intl_init") else "90d"     # 시장·효과 기사는 드물어 넓게
    fgov_window = "7d" if meta.get("intl_init") else "90d"
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

    def add(raw, cid, kind, q, regions=()):
        key = hashlib.sha1(norm(raw["t"]).encode()).hexdigest()[:12]
        x = items.get(key)
        if x is None:
            x = dict(raw, id=key, k=kind, c=[], f=NOW.strftime("%Y-%m-%d %H:%M"))
            items[key] = x
            new_ids.add(key)
        if cid not in x["c"]:
            x["c"].append(cid)
        if regions:
            x["r"] = sorted(set(x.get("r", [])) | set(regions))
        rank = {"news": 0, "policy": 1, "inst": 2, "fgov": 2, "gov": 3}
        if rank[kind] > rank[x["k"]]:
            x["k"] = kind
            x.update({f: raw[f] for f in ("u", "s", "d")})   # 같은 글이 기관 사이트에도 있으면 그 출처로
        return 1

    # job: (묶음, 라벨, 검색식, 종류, 뉴스판, 기본 권역, 제목 필터, 모드)
    # 모드 market=시장·효과 표현 필수, abroad=해외 권역어 필수(국문 기사), intl=외국어 기사
    jobs = []
    ABROAD = "(미국 OR 유럽 OR 중국 OR 일본 OR 독일 OR EU)"
    for cl in kw["clusters"]:
        for q in cl["queries"]:
            jobs.append((cl, q["q"], f'{q["q"]} when:{window}', "news", "ko", None, cl["must"], ""))
        for p in cl["policy"]:
            jobs.append((cl, f"정책브리핑: {p}", f"{p} site:korea.kr when:{window}", "gov", "ko", None, cl["must"], ""))
            jobs.append((cl, f"공공기관: {p}", f"{p} {INST_Q} when:{inst_window}", "inst", "ko", None, cl["must"], ""))
        topic = [cl["must"][0]]
        for m in cl.get("market", [])[:3]:
            jobs.append((cl, f"시장: {m}", f"{m} when:{mk_window}", "news", "ko", None, topic, "market"))
        if cl.get("market"):
            m0 = cl["market"][0].split()[0]
            jobs.append((cl, f"시장(공공기관): {m0}", f"{m0} (시장 OR 전망 OR 파급효과 OR 경제적) {INST_Q} when:{mk_window}",
                         "inst", "ko", None, topic, "market"))
        for q in [q["q"] for q in cl["queries"]][:2]:
            jobs.append((cl, f"해외(국문): {q}", f"{q} {ABROAD} when:{intl_window}", "news", "ko", None, cl["must"], "abroad"))
        it = cl.get("intl") or {}
        for q in it.get("en", [])[:2]:
            jobs.append((cl, f"북미: {q}", f"{q} (US OR NHTSA OR California OR Canada) when:{intl_window}",
                         "news", "us", "na", it["en_must"], "intl"))
            jobs.append((cl, f"유럽: {q}", f"{q} (EU OR Europe OR UNECE OR Germany OR UK) when:{intl_window}",
                         "news", "gb", "eu", it["en_must"], "intl"))
        for q in it.get("ja", [])[:2]:
            jobs.append((cl, f"일본: {q}", f"{q} when:{intl_window}", "news", "ja", "jp", it["ja_must"], "intl"))
        for q in it.get("zh", [])[:2]:
            jobs.append((cl, f"중국: {q}", f"{q} when:{intl_window}", "news", "zh", "cn", it["zh_must"], "intl"))
        if it:   # 해외 정부·국제기구 사이트
            for r, lang, ed in (("na", "en", "us"), ("eu", "en", "gb"), ("jp", "ja", "ja"), ("cn", "zh", "zh")):
                q = "(" + " OR ".join(f'"{w.strip()}"' for w in it[lang + "_must"][0][:4]) + ")"
                jobs.append((cl, f"해외정부({r})", f"{q} {FGOV_Q[r]} when:{fgov_window}",
                             "fgov", ed, r, it[lang + "_must"], "intl"))

    for cl, label, query, kind, ed, rdef, must, mode in jobs:
        try:
            res = gnews(query, ed)
        except Exception as e:
            errors.append(f"{label}: {e}")
            continue
        n = 0
        for raw in res:
            if not raw["t"] or "HOME" in raw["t"] or " > " in raw["t"] or raw["t"] == raw["s"]:
                continue   # 기관 홈페이지·메뉴 페이지 제목은 버린다
            if any(w in raw["t"] for w in SPAM) or any(w in raw["t"] for w in cl.get("exclude", [])):
                continue
            if any(w.lower() in raw["s"].lower() for w in SPAM_SRC):
                continue
            if REPORT_RE.search(raw["t"]):
                continue
            if mode == "market" and not is_market(raw["t"]):
                continue
            lang = lang_of(raw["t"])
            regs = regions_of(raw["t"], lang)
            if mode == "abroad" and not regs:
                continue
            if mode == "intl":
                if lang == "ko":
                    continue   # 외국판에 섞여 나온 국문 기사는 국문 검색이 맡는다
                if not regs and any(w in raw["t"] for w in KOREA):
                    continue   # 한국 이야기를 다룬 외신은 해외 사례가 아니다
                if not regs and any(re.search(r"(?<![A-Za-z])" + w + r"(?![A-Za-z])", raw["t"]) for w in OTHER_LAND):
                    continue
                if any(w in raw["t"] for w in INTL_EXCLUDE):
                    continue
                if kind == "fgov" and cl["id"] not in ("physai", "aam") and not any(
                        re.search(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", raw["t"], re.I) if lang == "en" else w in raw["t"]
                        for w in FGOV_CTX[lang]):
                    continue
                if not regs:
                    # 권역어가 없는 영어 기사는 매체 도메인으로 판단(영국판의 일반 도메인·기타 국가 도메인은 버림)
                    d = raw["d"]
                    if lang != "en" or ed == "ja" or ed == "zh":
                        regs = {rdef}
                    elif d.endswith(EU_TLD):
                        regs = {"eu"}
                    elif d.endswith(".ca"):
                        regs = {"na"}
                    elif ed == "us" and (d.endswith(GENERIC_TLD) or not d):
                        regs = {"na"}
                    elif kind == "fgov" and is_fgov(d):
                        regs = {rdef}
                    else:
                        continue
            k = kind_of(raw, kind == "gov")
            ok = hit(raw["t"], must) or (k == "gov" and any(
                all(w.lower() in raw["t"].lower() for w in p.split()) for p in cl["policy"]))
            if ok and k in ("inst", "gov") and any(w in raw["t"] for w in ANN_WORDS):
                # 기관 사이트의 모집·공고 글은 사업공고 쪽으로 보관
                add_ann({"id": "w" + hashlib.sha1(norm(raw["t"]).encode()).hexdigest()[:11], "t": raw["t"],
                         "u": raw["u"], "org": raw["s"], "gov": "", "de": raw["p"][:10], "rs": "", "re": "",
                         "st": "", "src": raw["d"]}, [cl["id"]])
                n += 1
            elif ok:
                n += add(raw, cl["id"], k, label, regs)
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
    meta["intl_init"] = True

    # 기존 기사에도 권역·시장 표시를 다시 붙인다(판별어를 고치면 과거분까지 반영)
    for x in items.values():
        lang = x.get("l") or lang_of(x["t"])
        if lang != "ko":
            x["l"] = lang
        regs = regions_of(x["t"], lang) | (set(x.get("r", [])) if lang != "ko" else set())
        if regs:
            x["r"] = sorted(regs)
        else:
            x.pop("r", None)
        if is_market(x["t"]):
            x["m"] = 1
        else:
            x.pop("m", None)

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
