"""DB의 단지명("나주")은 산업단지공단 통계의 약칭이라 같은 시군에 "나주"·"나주신도"·"나주혁신"이
나란히 있으면 어떤 단지인지 알기 어렵다. 화면과 챗봇에는 유형을 붙인 정식 명칭
("나주일반산업단지")을 쓰고, 약칭(name)은 문서 폴더·매칭 규칙의 키로 그대로 둔다."""
import re
from typing import Optional

_TYPE_SUFFIX = {
    "일반산단": "일반산업단지",
    "농공산단": "농공단지",
    "국가산단": "국가산업단지",
    "도시첨단산단": "도시첨단산업단지",
}

# 이미 단지 유형이 드러난 이름은 그대로 둔다 ("충남동물약품수출단지", "...일반산업단지")
_ALREADY_FULL_RE = re.compile(r"(단지|산단)$")
_TRAILING_NOTE_RE = re.compile(r"^(.*?)\s*([\(\[].*)$")


def full_park_name(name: Optional[str], park_type: Optional[str]) -> str:
    name = (name or "").strip()
    suffix = _TYPE_SUFFIX.get((park_type or "").strip())
    if not name or not suffix:
        return name
    m = _TRAILING_NOTE_RE.match(name)
    base, note = (m.group(1), m.group(2)) if m else (name, "")
    if _ALREADY_FULL_RE.search(base.replace(" ", "")) or _ALREADY_FULL_RE.search(name.replace(" ", "")):
        return name
    # "창원덴소도시첨단"처럼 유형 일부가 이미 붙은 약칭은 나머지만 잇는다 (→ "...도시첨단산업단지")
    for k in range(len(suffix) - 1, 1, -1):
        if base.endswith(suffix[:k]):
            return f"{base}{suffix[k:]}{note}"
    return f"{base}{suffix}{note}"
