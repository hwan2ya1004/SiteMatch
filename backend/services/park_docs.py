"""
"전국산업단지 공문" 폴더(산업통산부/전국산업단지 공문/{시도}/{시군구}/{산업단지명}/파일...)에서
산업단지별 공문·고시문서를 찾아 목록화하고, PDF는 텍스트를 추출해 챗봇 컨텍스트로 재사용한다.

주의: 이 폴더는 사용자 로컬 컴퓨터에만 있고 git 저장소(SiteMatch)에는 포함되지 않는다.
Render 등 배포 환경에는 이 폴더 자체가 없으므로, 로컬에서 실행할 때만 문서가 보이고
배포된 사이트에서는 조용히 빈 결과를 반환한다 (에러로 취급하지 않음 — 있으면 보여주고
없으면 생략하는 것이 원칙).
"""
import os
import re
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

# 문서 텍스트를 여기서 미리 통째로 잘라버리면(예: 8000자 고정 truncate) 질문에 따라
# 정작 필요한 부분(뒤쪽 페이지의 분양현황·입주제한 등)이 통째로 날아가 챗봇이
# "확인 안 됨"으로 잘못 답하는 정확도 문제가 실제로 있었다. 대신 여기서는 전체
# 텍스트를 (페이지 단위로 구분해) 캐시만 해두고, 질문과의 관련성에 따라 실제로
# 얼마를 프롬프트에 넣을지는 rag.py의 _keyword_filter_context()가 결정한다 —
# subsidy_docs.txt를 다루는 방식과 동일한 패턴. 여기 있는 상한은 병적으로 큰
# 문서가 파싱/캐시 자체를 못 하게 막는 안전장치일 뿐이다.
MAX_DOC_TEXT_CHARS_TOTAL = 40000
MAX_PDF_PAGES = 20  # 지나치게 긴 문서(별첨 도면 등) 파싱 시간 방지
_TITLE_HINT_RE_KEYWORDS = ("고시", "공고", "제20")  # 표제부에 흔히 등장하는 패턴


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


_NOTICE_NO_RE = re.compile(r"([가-힣]{2,6}(?:시|군|구))\s*(고시|공고)\s*제\s*(\d{4}-\d+)\s*호")


def _guess_doc_title(first_page_text: str, fallback: str) -> str:
    """PDF 표제부에서 "김해시 고시 제2025-216호"처럼 인용 가능한 제목을 추출한다.
    일부 PDF는 pypdf로 뽑으면 줄바꿈이 아예 없이 한 페이지가 한 덩어리로 붙어
    나오는 경우가 있어서(이 프로젝트에서 실제로 발생) 줄 단위로 나누는 방식은
    못 쓰고, 정규식으로 "OO시 고시 제2025-216호" 패턴 자체를 찾아낸다. 원문에
    띄어쓰기가 없어도(같은 이유) 표준 형태로 재조립해서 사람이 읽기 좋게 만든다.
    못 찾으면 파일명으로 대체한다 (파일명은 사람이 알아보기 어려운 코드라 최후의 수단)."""
    m = _NOTICE_NO_RE.search(first_page_text)
    if m:
        org, kind, no = m.groups()
        return f"{org} {kind} 제{no}호"
    return fallback


_HANGUL_RE = re.compile(r"[가-힣]")


def _looks_like_garbage(text: str) -> bool:
    """일부 PDF는 폰트 인코딩이 깨져 있어 pypdf가 "/HFT1/HFT2..."같은 읽을 수 없는
    glyph 코드를 뽑아낸다 — 이런 텍스트를 그대로 챗봇 컨텍스트에 넣으면 답변 정확도만
    떨어지므로, 한글 비율이 너무 낮으면(실질적으로 못 읽은 것으로 간주) 걸러낸다."""
    if len(text) < 30:
        return False  # 너무 짧으면 판단 보류 (오탐 방지)
    hangul_count = len(_HANGUL_RE.findall(text))
    return (hangul_count / len(text)) < 0.05


@lru_cache(maxsize=256)
def get_park_documents_text(region: str, city: str, name: str) -> str:
    """해당 단지의 PDF에서 텍스트를 추출한다. 페이지마다 별도 "[출처: ...]" 블록으로
    쪼개서 "\\n\\n"로 구분해 반환하는데, 이는 rag.py의 _keyword_filter_context()가
    문단을 "\\n\\n" 기준으로 나눠 질문과 관련성 높은 것만 골라 쓰는 방식과 맞추기
    위해서다 — 문서를 통째로 앞부분만 잘라 넣으면(예전 방식) "추진경위" 같은
    앞쪽 상용구만 들어가고 정작 물어본 내용(뒤쪽 페이지)은 잘려나가는 정확도
    문제가 실제로 있었다. 페이지 단위로 쪼개두면 질문에 맞는 페이지를 골라 쓸 수
    있다. PDF 파싱은 비용이 있어 캐시한다 — 서버 실행 중 폴더 내용이 바뀌면
    반영하려면 재시작이 필요하지만, 공문은 자주 바뀌는 성격이 아니라 감수할 만하다."""
    folder = find_park_folder(region, city, name)
    if not folder:
        return ""
    pdf_files = [f for f in sorted(folder.iterdir()) if f.suffix.lower() == ".pdf"]
    blocks = []
    for f in pdf_files:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(f))
            pages_text = []
            for page in reader.pages[:MAX_PDF_PAGES]:
                t = page.extract_text() or ""
                # 일부 PDF는 폰트 인코딩이 깨져 있어 "/HFT1/HFT2..." 같은 못 읽는
                # glyph 코드가 나온다 — 그런 페이지는 통째로 버린다 (실제로 이
                # 프로젝트의 두 번째 샘플 PDF에서 발생, 방치하면 챗봇이 그 잡음을
                # 근거로 잘못 답하거나 "확인 안 됨"으로 과잉 위축됨).
                if t.strip() and not _looks_like_garbage(t):
                    pages_text.append(t)
            if not pages_text:
                continue
            title = _guess_doc_title(pages_text[0], fallback=f.name)
            for page_text in pages_text:
                blocks.append(f"[출처: {title}]\n{page_text}")
        except Exception as e:
            print(f"⚠️ 공문 PDF 텍스트 추출 실패 ({f}): {e}")
    return "\n\n".join(blocks)[:MAX_DOC_TEXT_CHARS_TOTAL]
