"""
"전국산업단지 공문" 폴더(산업통산부/전국산업단지 공문/{시도}/{시군구}/{산업단지명}/파일...)에서
산업단지별 공문·고시문서를 찾아 목록화하고, PDF는 텍스트를 추출해 챗봇 컨텍스트로 재사용한다.

주의: 이 폴더는 사용자 로컬 컴퓨터에만 있고 git 저장소(SiteMatch)에는 포함되지 않는다.
Render 등 배포 환경에는 이 폴더 자체가 없으므로, 로컬에서 실행할 때만 문서가 보이고
배포된 사이트에서는 조용히 빈 결과를 반환한다 (에러로 취급하지 않음 — 있으면 보여주고
없으면 생략하는 것이 원칙).
"""
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

_SERVICES_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SERVICES_DIR)
_SITEMATCH_DIR = os.path.dirname(_BACKEND_DIR)
_SANUPTONGSANBU_DIR = os.path.dirname(_SITEMATCH_DIR)
DOCS_ROOT = os.path.join(_SANUPTONGSANBU_DIR, "전국산업단지 공문")

DOC_EXTENSIONS = {".pdf", ".hwp", ".hwpx", ".doc", ".docx"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ALLOWED_EXTENSIONS = DOC_EXTENSIONS | IMAGE_EXTENSIONS

MAX_DOC_TEXT_CHARS = 2000  # 챗봇 컨텍스트에 얹을 최대 길이 — Groq 무료 티어 TPM 한도 보호
MAX_PDF_PAGES = 15  # 지나치게 긴 문서(별첨 도면 등) 파싱 시간 방지


# DB는 지역을 "경남"처럼 축약형으로 저장하는데, 사용자가 폴더를 만들 때는
# "경상남도"처럼 정식 명칭을 쓰는 경우가 많다. 충남/충북/전남/전북/경남/경북은
# 정식명칭의 접두사가 아니라서(예: "경상남도"는 "경남"으로 시작하지 않음) 단순
# startswith로는 못 잡고, 명시적으로 매핑해야 한다.
_REGION_ABBR_TO_FULL = {
    "서울": ["서울특별시"], "부산": ["부산광역시"], "대구": ["대구광역시"],
    "인천": ["인천광역시"], "광주": ["광주광역시"], "대전": ["대전광역시"],
    "울산": ["울산광역시"], "세종": ["세종특별자치시"],
    "경기": ["경기도"], "강원": ["강원특별자치도", "강원도"],
    "충북": ["충청북도"], "충남": ["충청남도"],
    "전북": ["전북특별자치도", "전라북도"], "전남": ["전라남도"],
    "경북": ["경상북도"], "경남": ["경상남도"],
    "제주": ["제주특별자치도"],
}


def _norm(s: Optional[str]) -> str:
    return (s or "").replace(" ", "").lower()


def _region_matches(folder_name: str, db_region: str) -> bool:
    fn, dr = _norm(folder_name), _norm(db_region)
    if not fn or not dr:
        return False
    if fn == dr:
        return True
    full_names = _REGION_ABBR_TO_FULL.get(db_region, [])
    return fn in {_norm(x) for x in full_names}


def find_park_folder(region: str, city: str, name: str) -> Optional[Path]:
    """DB의 region/city/name으로 문서 폴더를 찾는다. 폴더명이 DB 표기와 완전히
    같지 않을 수 있어(공백, "경남" vs "경상남도" 같은 축약형 차이, "OO일반산업단지"
    vs "OO" 같은 표기 차이) 느슨하게 매칭한다."""
    if not os.path.isdir(DOCS_ROOT):
        return None
    root = Path(DOCS_ROOT)

    region_dir = next((d for d in root.iterdir() if d.is_dir() and _region_matches(d.name, region)), None)
    if not region_dir:
        return None

    city_dir = next((d for d in region_dir.iterdir() if d.is_dir() and _norm(d.name) == _norm(city)), None)
    if not city_dir:
        return None

    name_n = _norm(name)
    if not name_n:
        return None
    best = None
    for d in city_dir.iterdir():
        if not d.is_dir():
            continue
        dn = _norm(d.name)
        if dn == name_n or name_n in dn or dn in name_n:
            if not best or len(dn) > len(_norm(best.name)):
                best = d
    return best


def list_park_documents(region: str, city: str, name: str) -> List[Dict]:
    """해당 단지 폴더의 문서/이미지 파일 목록 (폴더나 파일이 없으면 빈 리스트)."""
    folder = find_park_folder(region, city, name)
    if not folder:
        return []
    files = []
    for f in sorted(folder.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext in ALLOWED_EXTENSIONS:
            files.append({
                "filename": f.name,
                "kind": "document" if ext in DOC_EXTENSIONS else "image",
                "size": f.stat().st_size,
            })
    return files


def resolve_document_path(region: str, city: str, name: str, filename: str) -> Optional[Path]:
    """다운로드 요청이 실제로 그 단지 폴더 안의 파일인지 검증하며 경로를 반환한다
    (경로 조작 방지 — 목록에 없는 파일명은 절대 서빙하지 않음)."""
    valid_names = {d["filename"] for d in list_park_documents(region, city, name)}
    if filename not in valid_names:
        return None
    folder = find_park_folder(region, city, name)
    return (folder / filename) if folder else None


@lru_cache(maxsize=256)
def get_park_documents_text(region: str, city: str, name: str) -> str:
    """해당 단지의 PDF에서 텍스트를 추출해 하나의 문자열로 합친다 (챗봇 컨텍스트용).
    PDF 파싱은 비용이 있어 캐시한다 — 서버 실행 중 폴더 내용이 바뀌면 반영하려면
    재시작이 필요하지만, 공문은 자주 바뀌는 성격이 아니라 감수할 만하다."""
    folder = find_park_folder(region, city, name)
    if not folder:
        return ""
    texts = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() != ".pdf":
            continue
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(f))
            pages_text = []
            for page in reader.pages[:MAX_PDF_PAGES]:
                t = page.extract_text() or ""
                if t.strip():
                    pages_text.append(t)
            if pages_text:
                texts.append(f"--- {f.name} ---\n" + "\n".join(pages_text))
        except Exception as e:
            print(f"⚠️ 공문 PDF 텍스트 추출 실패 ({f}): {e}")
    full = "\n\n".join(texts)
    return full[:MAX_DOC_TEXT_CHARS]
