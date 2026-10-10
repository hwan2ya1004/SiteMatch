"""
단지별 관리기관·문의처를 공식 문서에서 뽑아 data/park_management.json 으로 저장한다.

- 관리기관: 단지 안내서(폴더명과 같은 이름의 PDF) 첫 페이지의 "관리기관" 칸.
  "경북_경주시"처럼 앞에 붙은 시도 약칭은 떼고 "경주시"로 정리한다.
- 문의처: 고시·공고 문서의 "관계도서는 ○○과(☎ 0XX-XXX-XXXX)에 비치" 같은 문장에서
  담당 부서명과 전화번호를 함께 찾는다. 부서명 없이 번호만 있는 경우는 쓰지 않는다
  (어느 기관 번호인지 모르는 번호를 문의처로 보여주면 안 되므로).

문서에 없는 값은 넣지 않는다(추측 금지). 새 단지 문서를 넣은 뒤 다시 실행하면 된다:
    python scripts/build_park_management.py
"""
import json
import os
import re
import sys

from pypdf import PdfReader

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from services.park_docs import find_park_folder  # noqa: E402

PARKS_PATH = os.path.join(BACKEND_DIR, "data", "industrial_parks.json")
OUT_PATH = os.path.join(BACKEND_DIR, "data", "park_management.json")

_ORG_RE = re.compile(r"관리기관\s*([^\n]+)")
_REGION_PREFIX_RE = re.compile(r"^(?:[가-힣]{2,10}_)+")
# "투자유치과(☎033-250-4238)", "지역경제과 ☏ 055-330-0986", "기업지원과(☎ 061-...)"
_PHONE_CTX_RE = re.compile(
    r"([가-힣]{2,12}(?:시청|군청|구청|도청|시|군|구|도)?\s*[가-힣]{1,10}(?:과|팀|센터|사업소|공단|협의회))"
    r"\s*[(（]?\s*[☎☏]\s*(0\d{1,2}-\d{3,4}-\d{4})"
)
_LEADING_NOISE_RE = re.compile(r"^(?:관계도서는|관련도서는|관계도서|관련도서|문의|및|는|에|은)\s*")


def _info_sheet(folder):
    path = os.path.join(folder, os.path.basename(folder) + ".pdf")
    return path if os.path.isfile(path) else None


def _org_from_sheet(path):
    try:
        text = PdfReader(path).pages[0].extract_text() or ""
    except Exception:
        return None
    m = _ORG_RE.search(text)
    if not m:
        return None
    parts = [_REGION_PREFIX_RE.sub("", p.strip()) for p in m.group(1).split(",")]
    org = ", ".join(p for p in parts if p)
    return org or None


def _doc_text(path):
    try:
        if path.lower().endswith(".pdf"):
            return "".join((p.extract_text() or "") for p in PdfReader(path).pages[:40])
        if path.lower().endswith(".txt"):
            return open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return ""
    return ""


def _phone_from_notices(folder, sheet):
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if path == sheet or not os.path.isfile(path):
            continue
        text = _doc_text(path).replace("\n", "")
        m = _PHONE_CTX_RE.search(text)
        if m:
            dept = re.sub(r"^.*?있도록\s*", "", m.group(1).strip())
            dept = re.sub(r"^.*?도서는\s*", "", dept)
            dept = _LEADING_NOISE_RE.sub("", dept)
            return {"dept": dept, "phone": m.group(2), "source": name}
    return None


def main():
    parks = json.load(open(PARKS_PATH, encoding="utf-8"))
    out = {}
    for p in parks:
        folder = find_park_folder(p.get("region") or "", p.get("city") or "", p.get("name") or "", p.get("type"))
        if not folder:
            continue
        folder = str(folder)
        sheet = _info_sheet(folder)
        entry = {"folder": os.path.relpath(folder, BACKEND_DIR).replace("\\", "/")}
        org = _org_from_sheet(sheet) if sheet else None
        if org:
            entry["org"] = org
        phone = _phone_from_notices(folder, sheet)
        if phone:
            entry["contact"] = phone
        if org or phone:
            out[str(p["id"])] = entry
    payload = {
        "_설명": "단지별 관리기관(단지 안내서 '관리기관' 칸)과 문의처(고시문 관계도서 비치 부서·전화번호). "
                "scripts/build_park_management.py로 생성 — 문서에 없는 값은 넣지 않음.",
        "parks": out,
    }
    json.dump(payload, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"{len(out)} parks, org {sum('org' in v for v in out.values())}, "
          f"contact {sum('contact' in v for v in out.values())}")


if __name__ == "__main__":
    main()
