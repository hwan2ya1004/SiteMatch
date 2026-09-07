"""
한국산업단지공단 공공데이터 ETL 파이프라인
산업동향조사 통계 조회 서비스 (B550624/indparkstats)
data.go.kr API 자동 수집 → SQLite 저장 → 일 1회 갱신
"""
import json
import os
import re
import requests
from datetime import datetime
from typing import List, Dict, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 실제 API 엔드포인트
KICOX_BASE = "https://apis.data.go.kr/B550624/indparkstats"

# 오퍼레이션명 및 파라미터 타입 매핑
# param_type: "stdrYm" = 단일 년월, "range" = srtStdrYm + endStdrYm
OPERATIONS = {
    # stdrYm 단일 파라미터
    "mvn_cmpny":          {"url": f"{KICOX_BASE}/kicoxMvnCmpnyStatsService",           "param": "stdrYm"},  # 단지별 입주업체
    "mvn_cmpny_industry": {"url": f"{KICOX_BASE}/kicoxMvnCmpnyByIndustryStatsService", "param": "stdrYm"},  # 업종별 입주업체
    "op_rate":            {"url": f"{KICOX_BASE}/kicoxOpRateByIrsttStatsService",       "param": "stdrYm"},  # 단지별 가동률
    "emp":                {"url": f"{KICOX_BASE}/kicoxEmpByIrsttStatsService",          "param": "stdrYm"},  # 단지별 고용
    "prd_rec":            {"url": f"{KICOX_BASE}/kicoxPrdRecByIrsttStatsService",       "param": "stdrYm"},  # 단지별 생산
    "export_rec":         {"url": f"{KICOX_BASE}/kicoxExportRecByIrsttStatsService",   "param": "stdrYm"},  # 단지별 수출
    "emp_industry":       {"url": f"{KICOX_BASE}/kicoxEmpByIndustryStatsService",      "param": "stdrYm"},  # 업종별 고용
    # srtStdrYm + endStdrYm 범위 파라미터
    "op_rate_industry":   {"url": f"{KICOX_BASE}/kicoxOpRateByIndustryStatsService",   "param": "range"},   # 업종별 가동률
    "prd_industry":       {"url": f"{KICOX_BASE}/kicoxPrdRecByIndustryStatsService",   "param": "range"},   # 업종별 생산
    "export_industry":    {"url": f"{KICOX_BASE}/kicoxExportRecByIndustryStatsService","param": "range"},   # 업종별 수출
    "op_cmpny_industry":  {"url": f"{KICOX_BASE}/kicoxOpCmpnyByIndustryStatsService",  "param": "range"},   # 업종별 가동업체
    "op_detail":          {"url": f"{KICOX_BASE}/kicoxDetailOpRateStatsService",        "param": "range"},   # 가동률 세부
}

# 업종 코드 매핑 (induty01~12)
INDUSTRY_CODE_MAP = {
    "induty01": "식품",
    "induty02": "섬유·의류",
    "induty03": "화학·소재",
    "induty04": "기계·금속 장비",
    "induty05": "전자·반도체",
    "induty06": "자동차·부품",
    "induty07": "조선·해양",
    "induty08": "바이오·의료기기",
    "induty09": "로봇·스마트팩토리",
    "induty12": "기타 제조업",
    "induty131415": "서비스·기타",
}


def _get_recent_quarter_ym() -> str:
    """최근 분기 년월 반환 (YYYYMM) - 데이터 제공 시차 고려 (약 1년)"""
    now = datetime.now()
    # 공공데이터는 약 1년 시차로 제공됨 (2026년 현재 → 2024년 데이터)
    year = now.year - 2
    month = ((now.month - 1) // 3) * 3
    if month == 0:
        month = 12
        year -= 1
    return f"{year}{month:02d}"


def _prev_quarter_ym(ym: str) -> str:
    """분기 년월을 한 분기 전으로 이동"""
    year, month = int(ym[:4]), int(ym[4:])
    month -= 3
    if month <= 0:
        month += 12
        year -= 1
    return f"{year}{month:02d}"


def _normalize_park_name(name: str) -> str:
    """지역본부마다 다른 표기(공백·가운뎃점·약어)를 통일해 비교 가능하게 만든다.
    예: "구미 국가산단" → "구미국가산업단지" (KICOX 공식 표기와 동일해짐)"""
    name = re.sub(r"\(.*?\)", "", name)  # 괄호 안 지역 설명 제거
    name = name.replace(" ", "")
    name = name.replace("일반산단", "일반산업단지")
    name = name.replace("국가산단", "국가산업단지")
    return name


# 내장 샘플명과 KICOX 공식 명칭(irsttNm)이 표기 규칙만으로는 안 겹치는 단지들.
# 실제로는 같은 단지가 맞는지 확인 후 등록한 별칭 — 자동 정규화 규칙으로는 못 잡음.
KNOWN_ALIASES = {
    "서울디지털산업단지": ["서울디지털국가산업단지"],
    "김해골든루트산단": ["김해GoldenRoot일반산업단지", "김해골든루트일반산업단지"],
    "인천 부평국가산단": ["한국수출산업(부평)국가산업단지", "한국수출산업부평국가산업단지"],
    "부산 녹산국가산단": ["명지녹산국가산업단지"],
    "광주 첨단 과학산단": ["광주첨단과학국가산업단지"],
    "포항철강산단": ["포항국가산업단지"],
}


def _park_name_candidates(park_name: str) -> List[str]:
    """"반월·시화 국가산단"처럼 복수 단지를 묶은 이름은 개별 후보로 분리하고,
    KNOWN_ALIASES에 등록된 공식 명칭도 후보에 함께 넣는다."""
    parts = re.split(r"[·,]", park_name)
    candidates = [_normalize_park_name(p) for p in parts if p.strip()]
    for alias in KNOWN_ALIASES.get(park_name, []):
        candidates.append(_normalize_park_name(alias))
    return candidates


def _names_match(park_name_candidates: List[str], irstt_nm: str) -> bool:
    norm_irstt = _normalize_park_name(irstt_nm)
    return any(c in norm_irstt or norm_irstt in c for c in park_name_candidates)


class PublicDataService:
    """
    한국산업단지공단 공공데이터 수집 서비스
    산업동향조사 통계 조회 서비스 API 활용
    API 키가 없으면 내장 JSON 데이터를 사용합니다.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    def _call_api(self, operation: str, year_month: str = None) -> List[Dict]:
        """공공데이터 API 호출 공통 함수"""
        if not self.api_key:
            return []

        op_info = OPERATIONS.get(operation)
        if not op_info:
            print(f"⚠️ 알 수 없는 오퍼레이션: {operation}")
            return []

        url = op_info["url"]
        param_type = op_info["param"]
        ym = year_month or _get_recent_quarter_ym()

        # 파라미터 구성
        req_params = {
            "serviceKey": self.api_key,
            "pageNo": 1,
            "numOfRows": 100,
            "type": "json",
        }

        if param_type == "stdrYm":
            req_params["stdrYm"] = ym
        elif param_type == "range":
            req_params["srtStdrYm"] = ym
            req_params["endStdrYm"] = ym

        try:
            resp = requests.get(url, params=req_params, timeout=15)
            resp.raise_for_status()

            # XML 응답 파싱 (type=json이어도 XML로 올 수 있음)
            text = resp.text
            if text.strip().startswith("<"):
                # XML 응답 - 결과코드 확인
                if "<resultCode>00</resultCode>" not in text:
                    return []
                # XML에서 item 추출 (간단 파싱)
                return self._parse_xml_items(text)
            else:
                # JSON 응답
                try:
                    data = resp.json()
                    body = data.get("response", {}).get("body", {})
                    items = body.get("items", {})
                    if isinstance(items, dict):
                        item_list = items.get("item", [])
                    elif isinstance(items, list):
                        item_list = items
                    else:
                        item_list = []
                    if isinstance(item_list, dict):
                        item_list = [item_list]
                    return item_list
                except Exception:
                    return []

        except requests.exceptions.HTTPError as e:
            print(f"⚠️ API HTTP 오류 ({operation}): {e}")
            return []
        except Exception as e:
            print(f"⚠️ API 오류 ({operation}): {e}")
            return []

    def _parse_xml_items(self, xml_text: str) -> List[Dict]:
        """XML 응답에서 item 목록 추출"""
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(xml_text)
            items = []
            for item in root.findall(".//item"):
                d = {}
                for child in item:
                    d[child.tag] = child.text
                if d:
                    items.append(d)
            return items
        except Exception as e:
            print(f"⚠️ XML 파싱 오류: {e}")
            return []

    def fetch_complex_enterprise_stats(self, year_month: str = None) -> List[Dict]:
        """단지별 입주업체 현황 수집
        응답: irsttNm(산단명), monthMvnCmpCnt(입주업체수), monthOpCmpCnt(가동업체수), hireCmpCnt(임차업체수)
        """
        items = self._call_api("mvn_cmpny", year_month=year_month)
        print(f"  단지별 입주업체: {len(items)}건")
        return items

    def fetch_industry_enterprise_stats(self, year_month: str = None) -> List[Dict]:
        """업종별 입주업체 현황 수집
        응답: irsttNm(산단명), induty01~12(업종별 입주업체수)
        """
        items = self._call_api("mvn_cmpny_industry", year_month=year_month)
        print(f"  업종별 입주업체: {len(items)}건")
        return items

    def fetch_operation_rate_stats(self, year_month: str = None) -> List[Dict]:
        """단지별 가동률 현황 수집
        응답: irsttNm(산단명), monthOpRate(가동률), totalOpCmpnyCo(가동업체수)
        """
        items = self._call_api("op_rate", year_month=year_month)
        print(f"  단지별 가동률: {len(items)}건")
        return items

    def fetch_employment_stats(self, year_month: str = None) -> List[Dict]:
        """단지별 고용 현황 수집
        응답: irsttNm(산단명), monthTotal(총고용), monthMaleEmplymNmprCo(남), monthFemaleEmplymNmprCo(여)
        """
        items = self._call_api("emp", year_month=year_month)
        print(f"  단지별 고용현황: {len(items)}건")
        return items

    def _resolve_available_ym(self, max_lookback: int = 8) -> Optional[str]:
        """실제로 데이터가 존재하는 가장 최근 분기 년월을 탐색한다.
        공공데이터 제공 시차를 정확히 예측할 수 없어(예: 2024년 4월 이후
        미제공), 예상 시점부터 거슬러 올라가며 데이터가 있는 첫 분기를 찾는다."""
        ym = _get_recent_quarter_ym()
        for _ in range(max_lookback):
            if self._call_api("mvn_cmpny", year_month=ym):
                return ym
            ym = _prev_quarter_ym(ym)
        return None

    def fetch_all_stats(self, year_month: str = None) -> Dict[str, List[Dict]]:
        """모든 통계 데이터 수집"""
        ym = year_month or self._resolve_available_ym()
        if not ym:
            print(f"⚠️ 최근 {8}개 분기 내 데이터를 찾지 못함")
            return {"enterprise": [], "industry": [], "op_rate": [], "employment": []}
        print(f"📡 공공데이터 API 수집 시작 (년월: {ym})...")
        return {
            "enterprise": self.fetch_complex_enterprise_stats(ym),
            "industry":   self.fetch_industry_enterprise_stats(ym),
            "op_rate":    self.fetch_operation_rate_stats(ym),
            "employment": self.fetch_employment_stats(ym),
        }

    def _merge_stats_to_park(self, park: Dict, stats: Dict[str, List[Dict]]) -> Dict:
        """수집된 통계 데이터를 공단 정보에 병합"""
        candidates = _park_name_candidates(park.get("name", ""))

        # 단지별 입주업체 현황
        for item in stats.get("enterprise", []):
            irstt_nm = item.get("irsttNm", "")
            if _names_match(candidates, irstt_nm):
                total = int(item.get("monthMvnCmpCnt", 0) or 0)
                operating = int(item.get("monthOpCmpCnt", 0) or 0)
                if total > 0:
                    vacancy_rate = round((1 - operating / total) * 100, 1)
                    park["vacancy_rate"] = vacancy_rate
                    park["total_companies"] = total
                    park["operating_companies"] = operating
                break

        # 단지별 가동률
        for item in stats.get("op_rate", []):
            irstt_nm = item.get("irsttNm", "")
            if _names_match(candidates, irstt_nm):
                op_rate = float(item.get("monthOpRate", 0) or 0)
                if op_rate > 0:
                    park["operation_rate"] = op_rate
                    park["vacancy_rate"] = round(100 - op_rate, 1)
                break

        # 단지별 고용 현황
        for item in stats.get("employment", []):
            irstt_nm = item.get("irsttNm", "")
            if _names_match(candidates, irstt_nm):
                park["total_employees"] = int(item.get("monthTotal", 0) or 0)
                break

        # 업종별 입주업체 현황에서 주요 업종 추출
        for item in stats.get("industry", []):
            irstt_nm = item.get("irsttNm", "")
            if _names_match(candidates, irstt_nm):
                # induty01~12 중 값이 큰 업종 추출
                industry_counts = []
                for code, name in INDUSTRY_CODE_MAP.items():
                    cnt = int(item.get(code, 0) or 0)
                    if cnt > 0:
                        industry_counts.append((name, cnt))
                if industry_counts:
                    industry_counts.sort(key=lambda x: x[1], reverse=True)
                    park["industries"] = [i[0] for i in industry_counts[:5]]
                break

        return park

    def load_local_data(self) -> List[Dict]:
        """내장 JSON 데이터 로드"""
        data_path = os.path.join(BASE_DIR, "data", "industrial_parks.json")
        if not os.path.exists(data_path):
            return []
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def sync_to_db(self, db_session, year_month: str = None) -> int:
        """공공데이터를 DB에 동기화.

        API가 실패(키 없음·응답 0건)하면 정적 JSON 기본값으로 되돌리지 않고 DB에 이미
        있는 값(직전 성공 시점의 최신값, 혹은 최초 init 값)을 그대로 둔다 — 예전에는
        실패해도 매번 내장 JSON 기본값으로 덮어써버려서, API 장애가 나면 오히려
        "초기 상태로 리셋"되는 문제가 있었다(3-3 리스크 항목 참조, 이번에 수정).
        성공 여부와 무관하게 매일 스냅샷(VacancySnapshot/ParkVacancySnapshot)을 남겨
        추이 조회와 장애 시 최종 확인용 이력을 확보한다.
        """
        from database import IndustrialPark

        # 내장 데이터 로드 (기본 공단 정보 — 최초 DB 생성 시에만 실질적으로 쓰임)
        parks = self.load_local_data()
        if not parks:
            print("⚠️ 내장 데이터 없음")
            return 0

        # 공공데이터 API 수집
        api_succeeded = False
        if self.api_key:
            stats = self.fetch_all_stats(year_month)
            total_fetched = sum(len(v) for v in stats.values())
            if total_fetched > 0:
                print(f"✅ 공공데이터 수집 완료: 총 {total_fetched}건")
                parks = [self._merge_stats_to_park(p, stats) for p in parks]
                api_succeeded = True
            else:
                print("⚠️ 공공데이터 API 응답 0건 → DB 기존값 유지(정적 기본값으로 되돌리지 않음)")
        else:
            print("⚠️ 공공데이터 API 키 없음 → DB 기존값 유지(정적 기본값으로 되돌리지 않음)")

        updated = 0
        if api_succeeded:
            for p in parks:
                existing = db_session.query(IndustrialPark).filter_by(name=p["name"]).first()
                if not existing:
                    continue
                existing.available_area = p.get("available_area", existing.available_area)
                existing.vacancy_rate = p.get("vacancy_rate", existing.vacancy_rate)
                existing.rent_per_sqm = p.get("rent_per_sqm", existing.rent_per_sqm)
                existing.updated_at = datetime.utcnow()
                if p.get("industries"):
                    existing.industries = json.dumps(p["industries"], ensure_ascii=False)
                updated += 1
            db_session.commit()

        # API 성공/실패와 무관하게 오늘자 스냅샷을 남긴다 — 실패한 날은 어제와 같은 값이
        # 그대로 기록되어(=DB를 안 건드렸으므로) 추이 그래프에 공백이 생기지 않는다.
        self._save_snapshot(db_session)

        print(f"✅ DB 동기화 완료: {updated}개 공단 업데이트" if api_succeeded
              else "⚠️ API 실패 — DB 갱신은 건너뛰고 스냅샷만 기록")
        return updated

    def _save_snapshot(self, db_session) -> None:
        """오늘자 공실 현황 스냅샷을 기록한다(하루 1회, 같은 날 재실행 시 갱신).
        VacancySnapshot(전체 평균)과 ParkVacancySnapshot(단지별)에 각각 남긴다."""
        from database import IndustrialPark, VacancySnapshot, ParkVacancySnapshot

        parks = db_session.query(IndustrialPark).all()
        if not parks:
            return
        today = datetime.now().strftime("%Y-%m-%d")

        total_available = sum(p.available_area or 0 for p in parks)
        avg_vacancy = sum(p.vacancy_rate or 0 for p in parks) / len(parks)

        snap = db_session.query(VacancySnapshot).filter_by(snapshot_date=today).first()
        if snap:
            snap.avg_vacancy_rate = round(avg_vacancy, 1)
            snap.total_available_area = round(total_available)
        else:
            db_session.add(VacancySnapshot(
                snapshot_date=today,
                avg_vacancy_rate=round(avg_vacancy, 1),
                total_available_area=round(total_available),
            ))

        existing_park_snaps = {
            s.park_id: s for s in db_session.query(ParkVacancySnapshot)
            .filter_by(snapshot_date=today).all()
        }
        for p in parks:
            if p.id in existing_park_snaps:
                existing_park_snaps[p.id].vacancy_rate = p.vacancy_rate
            else:
                db_session.add(ParkVacancySnapshot(
                    park_id=p.id,
                    snapshot_date=today,
                    vacancy_rate=p.vacancy_rate,
                ))

        db_session.commit()
        print(f"✅ 스냅샷 저장 완료: {today} ({len(parks)}개 단지)")

    def get_vacancy_trend(self, db_session, days: int = 30) -> List[Dict]:
        """최근 N일 전체 평균 공실 현황 추이 (스냅샷 기반)."""
        from database import VacancySnapshot
        rows = (
            db_session.query(VacancySnapshot)
            .order_by(VacancySnapshot.snapshot_date.desc())
            .limit(days)
            .all()
        )
        return [
            {
                "date": r.snapshot_date,
                "avg_vacancy_rate": r.avg_vacancy_rate,
                "total_available_area": r.total_available_area,
            }
            for r in reversed(rows)
        ]

    def get_vacancy_stats(self, db_session) -> Dict:
        """공실 통계 집계"""
        from database import IndustrialPark
        parks = db_session.query(IndustrialPark).all()
        if not parks:
            return {}
        total_available = sum(p.available_area or 0 for p in parks)
        avg_vacancy = sum(p.vacancy_rate or 0 for p in parks) / len(parks)
        total_inquiries = sum(p.monthly_inquiries or 0 for p in parks)
        return {
            "total_parks": len(parks),
            "total_available_area": round(total_available),
            "avg_vacancy_rate": round(avg_vacancy, 1),
            "total_monthly_inquiries": total_inquiries,
            "updated_at": datetime.utcnow().isoformat(),
        }

    def test_api_connection(self) -> Dict:
        """API 연결 테스트"""
        if not self.api_key:
            return {"status": "no_key", "message": "API 키 없음"}

        ym = _get_recent_quarter_ym()
        url = OPERATIONS["mvn_cmpny"]["url"]

        try:
            resp = requests.get(url, params={
                "serviceKey": self.api_key,
                "pageNo": 1,
                "numOfRows": 3,
                "type": "json",
                "stdrYm": ym,
            }, timeout=10)

            text = resp.text
            result_code = ""
            if "<resultCode>" in text:
                s = text.find("<resultCode>") + 12
                e = text.find("</resultCode>")
                result_code = text[s:e]
            total_count = 0
            if "<totalCount>" in text:
                s = text.find("<totalCount>") + 12
                e = text.find("</totalCount>")
                try:
                    total_count = int(text[s:e])
                except:
                    pass

            return {
                "status": "ok" if result_code == "00" else "error",
                "result_code": result_code,
                "total_count": total_count,
                "year_month": ym,
                "endpoint": url,
            }
        except Exception as e:
            return {"status": "exception", "error": str(e)}
