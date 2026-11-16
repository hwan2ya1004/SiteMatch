"""184개 시·군·구의 기업·투자유치 지원 조례를 자치법규정보시스템(elis.go.kr)에서 찾아 원문 조항을 모은다.
1단계 결과(원본): data/local_subsidy_raw.json (커밋 안 함, 이어받기용)
2단계 결과(챗봇용): data/local_subsidy.json — 관련 없는 조례를 빼고 금액·비율이 있는 조항만 원문 그대로
사용: python scripts/fetch_local_subsidy.py            # 수집(이어받기) + 변환
      python scripts/fetch_local_subsidy.py --build    # 변환만"""
import html as H
import json
import os
import re
import sys
import time
from collections import defaultdict

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from database import IndustrialPark, SessionLocal  # noqa: E402

OUT = os.path.join(BASE_DIR, "data", "local_subsidy_raw.json")
FINAL = os.path.join(BASE_DIR, "data", "local_subsidy.json")
B = "https://elis.go.kr"
CTPV = {"서울": "11", "광주": "12", "전남": "12", "부산": "26", "대구": "27", "인천": "28", "대전": "30", "울산": "31",
        "세종": "36", "경기": "41", "강원": "42", "충북": "43", "충남": "44", "전북": "52", "경북": "47", "경남": "48", "제주": "50"}
KWS = ["투자", "기업", "산업단지"]
# 기업 입지·투자 보조금과 관련 있는 조례 이름
REL = re.compile(r"투자\s*유치|투자\s*촉진|기업\s*유치|투자\s*기업|기업\s*및\s*투자|기업\s*지원|기업\s*(육성|활동)|중소기업|산업단지|이전\s*기업|기업하기|공장\s*이전|기업\s*투자")
EXC = re.compile(r"공기업|민간투자|재정투자|사회적\s*기업|마을\s*기업|협동조합|여성\s*기업|경로당|공영개발|회계|옴부즈만|헌장|기업인\s*대상|에스오에스|SOS|자매결연|"
                 r"관리\s*기본\s*계획|위원회\s*(구성|운영)|농업|농식품|축산|수산|관광|문화|체육|복지|소상공인|전통시장|상점가|사회적경제|청년|노동|일자리\s*위원|공유재산|도시가스|"
                 r"계약심의|명예|포상|기업\s*혁신\s*파크")
ARTICLE = re.compile(r"(?=제\d+조(?:의\d+)?\()")
MONEY = re.compile(r"\d[\d,\.]*\s*(?:억|만|천)?\s*원|100분의\s*\d+|\d+\s*퍼센트|\d+\s*%|\d+분의\s*\d+")
SUPPORT = re.compile(r"지원|보조|감면|융자|이자")

s = requests.Session()
s.headers["User-Agent"] = "Mozilla/5.0 (research script)"


def token():
    t = s.get(B + "/locgovalr/locgovClAlrList", timeout=30).text
    return re.search(r'<meta name="_csrf" content="([^"]+)"', t).group(1)


TOK = token()


def post(url, data, **kw):
    for attempt in range(3):
        try:
            r = s.post(B + url, data=data, timeout=40, **kw)
            r.raise_for_status()
            time.sleep(0.35)
            return r
        except Exception as e:
            print("   retry", url, e, flush=True)
            time.sleep(3)
    raise RuntimeError(url)


_sgg_cache = {}


def sgg_list(ctpv):
    if ctpv not in _sgg_cache:
        _sgg_cache[ctpv] = post("/allalr/selectSggList", {"ctpvCd": ctpv}, headers={"X-CSRF-TOKEN": TOK}).json()
    return _sgg_cache[ctpv]


def search(ctpv, sgg, kw):
    out = []
    page = 1
    while True:
        t = post("/locgovalr/locgovSeAlrList", {"_csrf": TOK, "srchTabSeCd": "SE", "ctpvCd": ctpv, "sggCd": sgg, "srchKwd": kw,
                                               "ordnRuleSe": "", "pageSize": "100", "curPage": str(page)}).text
        items = re.findall(r"fnSrchDtls\('(\d+)', '(\d+)'\); return false;\">(.*?)</a>", t)
        out += [(a, h, H.unescape(re.sub(r"<[^>]+>", "", title)).strip()) for a, h, title in items]
        total = int((re.search(r'id="listCnt" value="(\d+)"', t) or [0, 0])[1])
        if page * 100 >= total or not items:
            return out
        page += 1


def detail(alr, hist):
    t = post("/allalr/selectAlrBdtOne", {"_csrf": TOK, "srchTabSeCd": "", "srchSeCd": "", "ctpvCd": "", "sggCd": "", "punNo": "",
                                         "chptNo": "", "alrNo": alr, "histNo": hist, "menuNm": "locgovSeAlr"}).text
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"</?a\b[^>]*>", "", t)  # 법령 링크는 글자 사이에 있으므로 줄을 끊지 않고 지운다
    txt = H.unescape(re.sub(r"<[^>]+>", "\n", t))
    txt = re.sub(r"[ \t\u00a0]+", " ", txt)
    lines = [ln.strip() for ln in txt.split("\n") if ln.strip()]
    txt = "\n".join(lines)
    info = {}
    m = re.search(r"\[시행 ([\d\.]+)\]", txt)
    info["시행일"] = m.group(1) if m else ""
    m = re.search(r"\[시행 [\d\.]+\]\n(\([^)]*\) [\d\.]+ (?:조례|규칙|훈령|예규) 제\s*[\d\-]+\s*호)", txt)
    info["최종개정"] = m.group(1) if m else ""
    m = re.search(r"관리책임부서명 : (.+)", txt)
    info["부서"] = m.group(1).strip() if m else ""
    m = re.search(r"관리책임전화번호 : (.+)", txt)
    info["전화"] = m.group(1).strip() if m else ""
    # 본문: 첫 "제1조(" 부터 부칙 전까지. 법령 링크 때문에 줄이 끊긴 것을 이어 붙인다
    i = txt.find("제1조(")
    j = txt.find("\n부칙", i)
    body = txt[i: j if j > 0 else None] if i >= 0 else ""
    info["본문길이"] = len(body)
    arts = [a.strip() for a in ARTICLE.split(body) if a.strip()]
    info["지원조항"] = [a for a in arts if MONEY.search(a) and SUPPORT.search(a)]
    info["조항제목"] = [re.match(r"(제\d+조(?:의\d+)?\([^)]*\))", a).group(1) for a in arts if re.match(r"제\d+조(?:의\d+)?\([^)]*\)", a)]
    return info


def targets():
    db = SessionLocal()
    g = defaultdict(set)
    for p in db.query(IndustrialPark).all():
        g[(p.region, (p.city or "").split(" ")[0])].add(p.id)
    return sorted(g)


def main():
    store = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    for n, (region, city) in enumerate(targets(), 1):
        key = f"{region}|{city}"
        if key in store and store[key].get("done"):
            continue
        ctpv = CTPV.get(region)
        rec = {"region": region, "city": city, "ordinances": [], "note": ""}
        sggs = sgg_list(ctpv) if ctpv else []
        sgg = next((x["sggCd"] for x in sggs if x["sggNm"] == city), None)
        if sgg is None and region in ("세종", "제주"):
            sgg, rec["note"] = "000", "시·군 자치법규 없음 — 시·도 본청 조례 기준"
        if sgg is None:
            rec["note"] = "자치법규정보시스템 시·군·구 목록에 없음(행정구역 개편 등)"
            rec["done"] = True
            store[key] = rec
            print(n, key, "NO SGG", flush=True)
            continue
        found = {}
        for kw in KWS:
            for a, h, title in search(ctpv, sgg, kw):
                if REL.search(title) and not EXC.search(title):
                    found[a] = (h, title)
        for a, (h, title) in found.items():
            try:
                info = detail(a, h)
            except Exception as e:
                info = {"error": str(e)}
            rec["ordinances"].append({"alrNo": a, "histNo": h, "title": title, **info})
        rec["done"] = True
        store[key] = rec
        print(n, key, len(found), [o["title"] for o in rec["ordinances"]][:6], flush=True)
        tmp = OUT + ".tmp"
        json.dump(store, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
    tmp = OUT + ".tmp"
    json.dump(store, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("DONE", len(store))


SKIP = re.compile(r"폐수|위탁|장애인|ESG|1인\s*창조|사회적|기금\s*운용\s*심의")
PRIMARY = re.compile(r"투자\s*유치|기업\s*유치|투자\s*촉진|기업\s*및\s*투자|투자\s*기업|이전\s*기업")


def build(collected_at):
    d = json.load(open(OUT, encoding="utf-8"))
    out = {}
    for k, v in d.items():
        ords = [{"title": o["title"], "effective": o.get("시행일", ""), "amended": o.get("최종개정", ""),
                 "dept": o.get("부서", ""), "phone": o.get("전화", ""), "articles": o["지원조항"]}
                for o in v["ordinances"] if not SKIP.search(o["title"]) and o.get("지원조항")]
        ords.sort(key=lambda o: (0 if PRIMARY.search(o["title"]) else 1, o["title"]))
        if ords:
            out[k] = {"region": v["region"], "city": v["city"], "ordinances": ords}
    store = {"source": "자치법규정보시스템(elis.go.kr) 현행 자치법규 — 법규명에 투자·기업·산업단지가 들어간 조례 중 금액·비율이 적힌 조항 원문",
             "collected_at": collected_at, "local": out}
    json.dump(store, open(FINAL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("built", len(out))


if __name__ == "__main__":
    from datetime import date
    if "--build" not in sys.argv:
        main()
    build(date.today().isoformat())
