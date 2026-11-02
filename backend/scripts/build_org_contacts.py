"""
관리기관 단위 문의처를 data/org_contacts.json 으로 만든다.

단지 고시문에 담당부서·전화번호가 없으면(park_management.json에 contact 없음) 상세 화면과
챗봇이 문의처를 보여주지 못한다. 관리기관은 대부분 확인돼 있으므로, 같은 관리기관의 다른
단지 고시문에 실제로 적힌 담당부서·전화번호를 그 기관의 문의처로 쓴다.

- 같은 시도 + 같은 관리기관 이름만 묶는다(다른 시도의 동명 기관과 섞이지 않게). 관리기관이 시·군
  자체가 아니면(한국산업단지공단·진흥원·도시공사 등 여러 시군을 맡는 기관) 같은 시군 안에서만 묶는다 —
  도 단위로 묶었더니 포항국가산단에 구미시청 부서가 붙는 문제가 있었다.
- 담당부서에 다른 시·군 이름이 들어 있으면(예: 포항 단지에 '구미시청') 쓰지 않는다.
- 시·군 기관인데 도청 부서 번호(예: 충청북도 산업입지과)가 적힌 경우는 그 기관 창구가 아니므로 뺀다.
- 번호가 여러 개면 가장 최근 고시(파일명 앞 YYYYMMDD)의 번호를 쓴다.
- 기관 공식 홈페이지 등에서 사람이 확인해 넣은 항목(basis="official_site", 공식 홈페이지 접속이 안 돼
  검색 업체정보로 넣은 항목은 basis="web_listing")은 다시 실행해도 지우지 않는다.

    python scripts/build_org_contacts.py
"""
import json
import os
import re

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARKS_PATH = os.path.join(BACKEND_DIR, "data", "industrial_parks.json")
MGMT_PATH = os.path.join(BACKEND_DIR, "data", "park_management.json")
OUT_PATH = os.path.join(BACKEND_DIR, "data", "org_contacts.json")

_PROVINCE_RE = re.compile(r"(?:특별자치도|충청북도|충청남도|경상북도|경상남도|전라북도|전라남도|경기도|강원도|제주특별자치도)")
_DATE_RE = re.compile(r"^(\d{8})")


# 실제 시·군 이름 목록(main에서 단지 데이터로 채움) — 정규식으로 'OO시'를 찾으면 '도시개발과'의
# '도시'까지 시 이름으로 잡혀서, DB에 있는 시군 이름과만 비교한다.
_CITY_NAMES = set()


def is_local_gov(org, city):
    """관리기관이 그 단지의 시·군·구 자체인가(예: city '김해시', org '김해시')."""
    base = (city or "").split(" ")[0]
    return bool(org) and org.replace(" ", "") in {base, (city or "").replace(" ", "")}


def org_key(region, org, city=None):
    """시·군 자체가 관리기관이면 시도+기관, 그 밖의 기관은 시도+시군+기관으로 묶는다."""
    if city is None or is_local_gov(org, city):
        return f"{region}|{org}"
    return f"{region}|{(city or '').split(' ')[0]}|{org}"


def mentions_other_city(dept, city):
    """담당부서 문구에 이 단지가 아닌 다른 시·군 이름이 들어 있는가."""
    base = (city or "").split(" ")[0]
    return any(n != base and n in dept for n in _CITY_NAMES)


def main():
    parks = json.load(open(PARKS_PATH, encoding="utf-8"))
    mgmt = json.load(open(MGMT_PATH, encoding="utf-8")).get("parks", {})
    try:
        old = json.load(open(OUT_PATH, encoding="utf-8")).get("orgs", {})
    except (OSError, ValueError):
        old = {}

    names = {str(p["id"]): p["name"] for p in parks}
    # '중구'·'서구'처럼 짧은 구 이름은 부서명 안의 다른 낱말과 겹치기 쉬워 시·군만 비교한다
    _CITY_NAMES.update(c for c in ((p.get("city") or "").split(" ")[0] for p in parks)
                       if len(c) >= 3 and c[-1] in "시군")
    cands = {}
    for p in parks:
        e = mgmt.get(str(p["id"])) or {}
        org, c = e.get("org"), e.get("contact")
        if not org or not c:
            continue
        # 시·군 기관 창구로 도청 부서 번호를 쓰지 않는다
        if not _PROVINCE_RE.search(org) and _PROVINCE_RE.search(c["dept"]):
            continue
        if mentions_other_city(c["dept"], p.get("city")):
            continue
        m = _DATE_RE.match(c.get("source", ""))
        cands.setdefault(org_key(p["region"], org, p.get("city")), []).append(
            (m.group(1) if m else "00000000", c, names[str(p["id"])]))

    # 사람이 확인해 넣은 항목은 유지 — notice_manual은 고시문에 '☎' 없이 '부서(번호)'로만 적혀
    # 자동 추출(build_park_management)에서 빠진 담당부서 번호를 직접 옮긴 것
    # user_provided는 공식 출처로 대조하지 못했지만 운영자가 직접 확인해 준 번호
    orgs = {k: v for k, v in old.items()
            if v.get("basis") in ("official_site", "web_listing", "notice_manual", "user_provided")}
    for k, lst in cands.items():
        if k in orgs:
            continue
        date, c, park_name = max(lst, key=lambda x: x[0])
        orgs[k] = {"dept": c["dept"], "phone": c["phone"], "basis": "same_org_notice",
                   "source": f"{park_name} {c['source']}"}
    payload = {
        "_설명": "관리기관 단위 문의처. 단지 고시문에 연락처가 없을 때 같은 관리기관(같은 시도)의 다른 단지 고시에 "
                "적힌 담당부서·전화번호(basis=same_org_notice) 또는 기관 공식 홈페이지에서 확인한 대표번호"
                "(basis=official_site)를 쓴다. scripts/build_org_contacts.py로 생성(official_site 항목은 유지).",
        "orgs": dict(sorted(orgs.items())),
    }
    json.dump(payload, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    covered = sum(1 for p in parks
                  if not (mgmt.get(str(p["id"])) or {}).get("contact")
                  and org_key(p["region"], (mgmt.get(str(p["id"])) or {}).get("org"), p.get("city")) in orgs)
    print(f"{len(orgs)} orgs, parks newly covered {covered}")


if __name__ == "__main__":
    main()
