"""
"전국산업단지 공문" 폴더(backend/data/park_docs/{시도}/{시군구}/{산업단지명}/파일...)에서
산업단지별 공문·고시문서를 찾아 목록화하고, PDF는 텍스트를 추출해 챗봇 컨텍스트로 재사용한다.

이 폴더는 backend/data/ 아래(git 저장소 안)에 있어 코드와 함께 커밋·배포된다 —
처음엔 저장소 밖(산업통산부/전국산업단지 공문)에 있어서 로컬에서만 보이고 배포
사이트(Render)에는 반영이 안 됐는데, 그러면 "로컬에서 답 잘 하는 걸 확인했다"가
배포 사이트에서는 그대로 재현이 안 되는 문제가 있어 저장소 안으로 옮겼다.
앞으로 새 단지 문서는 이 backend/data/park_docs/ 아래에 바로 추가해야
git push로 배포 사이트에도 반영된다.
"""
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

_SERVICES_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SERVICES_DIR)
DOCS_ROOT = os.path.join(_BACKEND_DIR, "data", "park_docs")

DOC_EXTENSIONS = {".pdf", ".hwp", ".hwpx", ".doc", ".docx"}
TEXT_EXTENSIONS = {".txt"}  # PDF만 지원했더니 사용자가 직접 정리해 넣은 .txt 문서가
                             # 조용히 무시되고 있었다 — 텍스트도 문서로 취급한다.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
ALLOWED_EXTENSIONS = DOC_EXTENSIONS | TEXT_EXTENSIONS | IMAGE_EXTENSIONS

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


def _find_name_match(parent: Path, name_n: str) -> Optional[Path]:
    best = None
    for d in parent.iterdir():
        if not d.is_dir():
            continue
        dn = _norm(d.name)
        if dn == name_n or name_n in dn or dn in name_n:
            if not best or len(dn) > len(_norm(best.name)):
                best = d
    return best


def find_park_folder(region: str, city: str, name: str) -> Optional[Path]:
    """DB의 region/city/name으로 문서 폴더를 찾는다. 폴더명이 DB 표기와 완전히
    같지 않을 수 있어(공백, "경남" vs "경상남도" 같은 축약형 차이, "OO일반산업단지"
    vs "OO" 같은 표기 차이) 느슨하게 매칭한다. 시/군/구 폴더 단계를 생략하고
    "시도/단지명"으로 바로 넣는 경우도 실제로 있어서(예: "울산광역시/GW"),
    city 폴더가 안 맞으면 region 바로 아래에서도 단지명을 찾아본다."""
    if not os.path.isdir(DOCS_ROOT):
        return None
    root = Path(DOCS_ROOT)

    region_dir = next((d for d in root.iterdir() if d.is_dir() and _region_matches(d.name, region)), None)
    if not region_dir:
        return None

    name_n = _norm(name)
    if not name_n:
        return None

    city_dir = next((d for d in region_dir.iterdir() if d.is_dir() and _norm(d.name) == _norm(city)), None)
    if city_dir:
        match = _find_name_match(city_dir, name_n)
        if match:
            return match

    # city 폴더가 없거나 그 안에서 못 찾았으면, region 바로 아래도 시도한다
    return _find_name_match(region_dir, name_n)


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
                "kind": "document" if ext in (DOC_EXTENSIONS | TEXT_EXTENSIONS) else "image",
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


def _extract_pdf_blocks(f: Path) -> List[str]:
    """PDF 한 개를 페이지 단위 "[출처: ...]" 블록 리스트로 추출."""
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
            return []
        title = _guess_doc_title(pages_text[0], fallback=f.name)
        return [f"[출처: {title}]\n{pt}" for pt in pages_text]
    except Exception as e:
        print(f"⚠️ 공문 PDF 텍스트 추출 실패 ({f}): {e}")
        return []


def _extract_txt_blocks(f: Path) -> List[str]:
    """텍스트 파일 한 개를 문단 단위 "[출처: ...]" 블록 리스트로 추출."""
    content = None
    for enc in ("utf-8", "cp949"):
        try:
            content = f.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if not content or not content.strip():
        return []
    # .txt는 파일명 자체가 이미 사람이 알아볼 수 있는 제목인 경우가 많다
    # (예: "투자인센티브지원제도.txt") — PDF처럼 표제부에서 고시번호를
    # 따로 찾을 필요 없이 파일명(확장자 제외)을 그대로 출처로 쓴다.
    title = f.stem
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()] or [content.strip()]
    return [f"[출처: {title}]\n{p}" for p in paragraphs]


@lru_cache(maxsize=256)
def get_park_documents_text(region: str, city: str, name: str, budget: int = 6000) -> str:
    """해당 단지의 문서(PDF·텍스트)에서 텍스트를 추출해 "[출처: ...]" 블록으로
    쪼개 반환한다. 실제로 겪은 두 가지 정확도 문제를 막기 위한 구조:

    1) 페이지/문단 단위로 쪼개두는 이유 — 문서를 앞부분만 통째로 잘라 넣으면
       "추진경위" 같은 상용구만 들어가고 정작 물어본 내용(뒤쪽 페이지)은
       잘려나가 챗봇이 "확인 안 됨"으로 잘못 답한 사례가 있었다.
    2) 문서(파일) 단위로 예산을 균등하게 나누는 이유 — 한 단지에 PDF와 txt를
       같이 넣었더니 PDF 하나만으로 전체 예산을 다 써버려서 뒤에 합친 txt
       문서(투자인센티브 지원제도 등)가 통째로 안 들어가는 문제가 실제로
       있었다. 파일별로 예산을 나눠서, 문서 개수가 늘어도 특정 파일이
       나머지를 완전히 밀어내지 않게 한다.

    PDF 파싱은 비용이 있어 캐시한다 — 서버 실행 중 폴더 내용이 바뀌면
    반영하려면 재시작이 필요하지만, 공문은 자주 바뀌는 성격이 아니라
    감수할 만하다."""
    folder = find_park_folder(region, city, name)
    if not folder:
        return ""

    file_blocks: List[List[str]] = []
    for f in sorted(folder.iterdir()):
        ext = f.suffix.lower()
        if ext == ".pdf":
            blocks = _extract_pdf_blocks(f)
        elif ext == ".txt":
            blocks = _extract_txt_blocks(f)
        else:
            continue
        if blocks:
            file_blocks.append(blocks)

    if not file_blocks:
        return ""

    per_file_budget = budget // len(file_blocks)
    final_blocks = []
    for blocks in file_blocks:
        used = 0
        for b in blocks:
            if used + len(b) > per_file_budget:
                continue  # 이 블록만 건너뛰고 계속 — 다음 블록이 더 작아 들어갈 수도 있음
            final_blocks.append(b)
            used += len(b)

    return "\n\n".join(final_blocks)
