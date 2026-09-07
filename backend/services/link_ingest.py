"""
외부 참고 링크(data/reference_links.txt)를 실제로 가져와 subsidy_docs.txt에 반영하는 스크립트.

사용법: backend 디렉터리에서 `python services/link_ingest.py` 실행.

한계(정직하게 밝혀둠): 이건 단순 HTTP GET + HTML 파싱이라, 서버사이드에서 이미 완성된
HTML을 내려주는 일반적인 페이지만 가져올 수 있다. 자바스크립트로 폼을 제출해야 내용이
열리는 게시판(예: factoryon.go.kr 자료실 게시글 상세보기)처럼 브라우저 렌더링이 필요한
페이지는 이 방식으로는 절대 못 가져온다 — "언제 가져오냐"의 문제가 아니라 애초에 이 방식
자체의 한계이므로, 그런 페이지는 FAILED 목록에 표시하고 사람이 직접 내용을 복사해서
subsidy_docs.txt에 넣어야 한다.
"""
import os
import re
from datetime import date
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINKS_PATH = os.path.join(BASE_DIR, "data", "reference_links.txt")
DOCS_PATH = os.path.join(BASE_DIR, "data", "subsidy_docs.txt")

AUTO_SECTION_START = "===== 외부 참고자료 자동 수집"
MAX_CHARS_PER_PAGE = 2000  # subsidy_docs.txt 검색 예산(2200자)을 한 페이지가 다 잡아먹지 않도록 제한
MIN_MEANINGFUL_CHARS = 200  # 이보다 짧게 추출되면 "사실상 빈 페이지(JS 셸)"로 간주해 실패 처리

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def _read_links() -> List[str]:
    if not os.path.exists(LINKS_PATH):
        return []
    urls = []
    with open(LINKS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def fetch_and_extract(url: str) -> Tuple[Optional[str], Optional[str]]:
    """(추출된 텍스트, 실패 사유) 튜플을 반환한다. 성공하면 실패 사유는 None."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except Exception as e:
        return None, f"요청 실패: {e}"
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"

    resp.encoding = resp.apparent_encoding or resp.encoding
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "iframe"]):
        tag.decompose()

    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)

    if len(cleaned) < MIN_MEANINGFUL_CHARS:
        return None, f"추출된 내용이 너무 짧음({len(cleaned)}자) — 자바스크립트로만 렌더링되는 페이지일 가능성 높음, 수동 확인 필요"

    return cleaned[:MAX_CHARS_PER_PAGE], None


def build_auto_section(results: List[Tuple[str, Optional[str], Optional[str]]]) -> str:
    """성공한 페이지만 문서에 넣는다. 실패 목록("수동 확인 필요" 같은 운영 메모)은 절대
    넣지 않는다 — RAG가 그 문단을 그대로 검색해서 챗봇이 사용자에게 내부 메모를
    그대로 노출해버릴 수 있기 때문(실제로 그런 위험이 있다는 지적을 받고 고침).
    실패 목록은 main()에서 콘솔에만 출력한다."""
    today = date.today().isoformat()
    ok_results = [(url, text) for url, text, err in results if text]
    if not ok_results:
        return ""
    lines = [f"{AUTO_SECTION_START} (마지막 갱신: {today}) ====="]
    lines.append(
        "아래는 data/reference_links.txt에 등록된 외부 페이지를 자동으로 가져온 원문입니다. "
        "출처 URL과 함께 표시했으며, 사람이 직접 검증한 다른 섹션보다는 신뢰도가 낮을 수 있으니 "
        "구체적 금액·조건은 원문 링크에서 다시 확인하는 것을 권장합니다."
    )
    for url, text in ok_results:
        lines.append(f"[자동 수집: {url}]\n{text}")
    return "\n\n".join(lines)


def update_docs_file(auto_section: str) -> None:
    if os.path.exists(DOCS_PATH):
        with open(DOCS_PATH, encoding="utf-8") as f:
            content = f.read()
    else:
        content = ""

    idx = content.find(AUTO_SECTION_START)
    if idx != -1:
        content = content[:idx].rstrip() + "\n"
    else:
        content = content.rstrip() + "\n\n"

    content += auto_section + "\n"

    with open(DOCS_PATH, "w", encoding="utf-8") as f:
        f.write(content)


def main():
    urls = _read_links()
    if not urls:
        print("reference_links.txt에 등록된 URL이 없습니다.")
        return

    results = []
    for url in urls:
        text, err = fetch_and_extract(url)
        results.append((url, text, err))
        status = "OK" if text else f"FAILED ({err})"
        print(f"- {url}: {status}")

    auto_section = build_auto_section(results)
    update_docs_file(auto_section)
    ok_count = sum(1 for _, t, _ in results if t)
    print(f"\n완료: {ok_count}/{len(urls)}개 성공, subsidy_docs.txt에 반영했습니다.")
    print("⚠️ 서버가 실행 중이면 재시작해야 챗봇이 새 내용을 인식합니다.")


if __name__ == "__main__":
    main()
