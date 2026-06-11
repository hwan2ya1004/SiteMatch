"""공공데이터 API 최종 테스트 - 202403 기준"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv('.env')
from services.public_data import PublicDataService

key = os.getenv('PUBLIC_DATA_API_KEY')
svc = PublicDataService(api_key=key)

# 데이터가 있는 년월: 202403
YM = "202403"

print("=== API 연결 테스트 ===")
result = svc.test_api_connection()
print(result)

print(f"\n=== 단지별 입주업체 현황 ({YM}) ===")
items = svc.fetch_complex_enterprise_stats(YM)
print(f"수집: {len(items)}건")
for item in items[:3]:
    print(f"  {item.get('irsttNm')}: 입주={item.get('monthMvnCmpCnt')}, 가동={item.get('monthOpCmpCnt')}")

print(f"\n=== 단지별 가동률 ({YM}) ===")
items2 = svc.fetch_operation_rate_stats(YM)
print(f"수집: {len(items2)}건")
for item in items2[:3]:
    print(f"  {item.get('irsttNm')}: 가동률={item.get('monthOpRate')}%")

print(f"\n=== 단지별 고용현황 ({YM}) ===")
items3 = svc.fetch_employment_stats(YM)
print(f"수집: {len(items3)}건")
for item in items3[:3]:
    print(f"  {item.get('irsttNm')}: 고용={item.get('monthTotal')}명")

print(f"\n=== 업종별 입주업체 ({YM}) ===")
items4 = svc.fetch_industry_enterprise_stats(YM)
print(f"수집: {len(items4)}건")
for item in items4[:2]:
    print(f"  {item.get('irsttNm')}: 전자반도체={item.get('induty05')}, 자동차={item.get('induty06')}, 기계금속={item.get('induty04')}")

print("\n=== 전체 통계 수집 ===")
all_stats = svc.fetch_all_stats(YM)
total = sum(len(v) for v in all_stats.values())
print(f"총 수집: {total}건")
for k, v in all_stats.items():
    print(f"  {k}: {len(v)}건")
