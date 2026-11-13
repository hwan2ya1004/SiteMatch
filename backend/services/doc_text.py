"""첨부 문서(PDF·HWP·HWPX·ZIP)에서 글자만 뽑는다. 표는 칸 구분 없이 글자만 남는다.
이미지·엑셀 등 글자를 뽑을 수 없는 파일은 빈 문자열을 돌려준다."""
import io
import re
import struct
import zipfile
import zlib
from typing import List, Tuple

MAX_ZIP_FILES = 10


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _hwp(data: bytes) -> str:
    """HWP 5.x: OLE 안의 BodyText/SectionN 레코드 중 문단 글자(HWPTAG_PARA_TEXT=67)만 읽는다."""
    import olefile
    ole = olefile.OleFileIO(io.BytesIO(data))
    header = ole.openstream("FileHeader").read()
    compressed = bool(header[36] & 1)
    sections = sorted((e for e in ole.listdir() if e[0] == "BodyText"), key=lambda e: int(e[1].replace("Section", "") or 0))
    out: List[str] = []
    for sec in sections:
        raw = ole.openstream(sec).read()
        if compressed:
            raw = zlib.decompress(raw, -15)
        i = 0
        while i + 4 <= len(raw):
            h = struct.unpack_from("<I", raw, i)[0]
            tag, size = h & 0x3FF, (h >> 20) & 0xFFF
            i += 4
            if size == 0xFFF:
                size = struct.unpack_from("<I", raw, i)[0]
                i += 4
            if tag == 67:
                out.append(_hwp_para(raw[i:i + size]))
            i += size
    ole.close()
    return "\n".join(t for t in out if t.strip())


def _hwp_para(b: bytes) -> str:
    """문단 글자(UTF-16LE)에서 제어문자를 건너뛴다. 확장 제어문자는 16바이트(8글자)를 차지한다."""
    chars, i = [], 0
    while i + 1 < len(b):
        c = struct.unpack_from("<H", b, i)[0]
        if c < 32:
            if c in (9,):
                chars.append("\t")
            elif c in (10, 13):
                chars.append("\n")
            # 1~8, 11~12, 14~23: 확장/인라인 제어문자 — 8글자(16바이트) 단위로 건너뜀
            i += 16 if c not in (0, 9, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31) else 2
            continue
        chars.append(chr(c))
        i += 2
    return "".join(chars)


def _hwpx(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
    out = []
    for n in names:
        xml = z.read(n).decode("utf-8", "replace")
        # 문단(<hp:p>)마다 줄을 바꾸고, 글자(<hp:t>)만 모은다
        for para in re.findall(r"<hp:p[ >].*?</hp:p>", xml, flags=re.S):
            text = "".join(re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", para, flags=re.S))
            text = re.sub(r"<[^>]+>", "", text)
            if text.strip():
                out.append(text)
    import html
    return html.unescape("\n".join(out))


def _fix_surrogates(s: str) -> str:
    """HWP 글자는 UTF-16 단위로 읽어서, 짝이 맞는 서로게이트는 합치고 짝 없는 것은 �로 바꾼다."""
    return s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def extract(name: str, data: bytes) -> List[Tuple[str, str]]:
    """(파일이름, 글자) 목록을 돌려준다. ZIP이면 안의 문서들을 각각 읽는다."""
    return [(n, _fix_surrogates(t)) for n, t in _extract(name, data)]


def _extract(name: str, data: bytes) -> List[Tuple[str, str]]:
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    try:
        if ext == "pdf" or data[:5] == b"%PDF-":
            return [(name, _pdf(data))]
        if ext == "hwp" or data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            return [(name, _hwp(data))]
        if ext in ("hwpx", "zip") or data[:2] == b"PK":
            z = zipfile.ZipFile(io.BytesIO(data))
            if any(n.startswith("Contents/section") for n in z.namelist()):
                return [(name, _hwpx(data))]
            out = []
            for info in z.infolist()[:MAX_ZIP_FILES]:
                inner = info.filename
                try:
                    inner = inner.encode("cp437").decode("cp949")  # 한글 파일명이 깨져 들어오는 경우
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                if info.is_dir() or not re.search(r"\.(pdf|hwp|hwpx)$", inner, re.I):
                    continue
                out += extract(inner, z.read(info))
            return out
    except Exception as e:  # 깨진 파일 등 — 이 파일만 건너뛴다
        return [(name, "")] if not str(e) else [(name, "")]
    return [(name, "")]


# ── 공고문을 번호 제목(1. 사업개요, 2. 분양대상 …)으로 나누기 ──
def clean(text: str) -> str:
    text = re.sub(r"^\s*-\s*\d+\s*-\s*$", "", text, flags=re.M)  # 쪽번호 "- 1 -"
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


_TITLE_STOP = re.compile(r"(?:\s?[가나다라마바사아자차카타파하]\s?\.|[:：]|「|\n|○|◦|ㅇ|※|□|■|-)")


_BARE_STOP = re.compile(r"(?:\s?[가나다라마바사아자차카타파하]\s?\.|[:：]|「|\n|○|◦|ㅇ|※|□|■|◆|[①-⑳]|\d|-)")


def _bare_ok(text: str, num_pos: int, stop) -> bool:
    """점 없는 번호 제목은 문장이 끝난 뒤(앞 글자가 '.'·줄바꿈)이거나, 제목 바로 뒤에 □·◆·① 같은 기호가 올 때만 인정한다."""
    prev = text[num_pos - 1] if num_pos else "\n"
    return prev in ".\n" or bool(re.match(r"\s?[□■◆①-⑳※○◦]", text[stop.start():stop.start() + 2]))


def _find_marks(text: str, pattern: str, stop_re, max_title: int, bare: bool = False) -> List[tuple]:
    """번호 1, 2, 3 … 을 차례대로 찾아 (번호 위치, 제목 시작, 제목) 목록을 만든다."""
    marks, pos, n = [], 0, 1
    while True:
        found = None
        for m in re.finditer(pattern.replace("{n}", str(n)), text[pos:]):
            start = pos + m.end()
            stop = stop_re.search(text, start, start + max_title + 4)
            if not stop:
                continue
            if bare and not _bare_ok(text, pos + m.start(), stop):
                continue
            title = re.sub(r"\s+", " ", text[start:stop.start()]).strip()
            if 2 <= len(title) <= max_title and not title.endswith(("(", "‘", ",")):
                found = (pos + m.start(), start, title)
                break
        if not found:
            break
        marks.append(found)
        pos, n = found[1], n + 1
    return marks


def split_sections(text: str, max_title: int = 16) -> List[dict]:
    """1, 2, 3 … 순서대로 나오는 최상위 번호 제목(짧은 제목 뒤에 '가.'·줄바꿈 등이 오는 것)을 찾아 항목으로 나눈다.
    본문 속 번호 목록("1. 구비서류 중 …")은 제목이 길어서 건너뛴다.
    번호가 2개 이상 차례로 이어지지 않으면 전체를 한 항목으로 둔다."""
    text = clean(text)
    dotted = _find_marks(text, r"(?<!\d){n}\s?\.\s?(?=[가-힣])", _TITLE_STOP, max_title)
    # LH 공고처럼 "…합니다.1공급대상토지□ …"로 번호 뒤에 점 없이 제목이 붙는 형식
    bare = _find_marks(text, r"(?<![\d,\-~∼]){n}(?=[가-힣])", _BARE_STOP, 12, bare=True)
    marks = bare if len(bare) >= 3 and len(bare) > len(dotted) else dotted
    if len(marks) < 2:
        return [{"title": "공고 내용", "text": text}] if text else []
    out = []
    head = text[: marks[0][0]].strip()
    if head:
        out.append({"title": "공고문", "text": head})
    for i, (s, body_start, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        body = text[body_start:end].strip()
        body = body[len(title):].strip() if body.startswith(title) else body
        out.append({"title": title, "text": body})
    return out


# ── 첨부 여러 개 중 공고문 본문 고르기 ──
_FORM_NAME = re.compile(r"양식|서식|신청서|위임장|계획서|확인서|서약서|동의서|위치도|도면|별지|별첨|첨부\s*\d|명세|체크리스트")
_FORM_START = re.compile(r"\[별지\s*제?\s*\d+\s*호|\(별지\s*제?\s*\d+\s*호|<\s*별지\s*\d+|\[서식\s*\d+\]|\[양식\s*\d+\]")


def pick_main(docs: List[Tuple[str, str]]) -> Tuple[str, str]:
    """(파일이름, 글자) 목록에서 공고문 본문을 고른다: 이름에 양식·서식 등이 없고, 앞부분에 '공고'가 있는 것 우선, 그다음 글자가 많은 것."""
    cands = [(n, t) for n, t in docs if t and t.strip()]
    if not cands:
        return ("", "")

    def score(item):
        n, t = item
        return (0 if _FORM_NAME.search(n) else 1, 1 if "공고" in t[:400] or "공고" in n else 0, len(t))

    return max(cands, key=score)


def trim_forms(text: str) -> str:
    """공고문 뒤에 붙은 신청서 양식([별지 제○호서식] 등)을 잘라낸다. 앞쪽 30% 안에서 나오면 본문 인용으로 보고 자르지 않는다."""
    for m in _FORM_START.finditer(text):
        if m.start() > len(text) * 0.3:
            return text[: m.start()].rstrip(" ■\n")
    return text
