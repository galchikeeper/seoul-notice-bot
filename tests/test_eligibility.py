"""자격 조건 요약: 공고문에 보이는 항목만, 판정 없이.  실행: python -m unittest discover tests"""
import pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import eligibility as E

# 실제 공고문에서 뽑은 문장(띄어쓰기·줄바꿈은 PDF/OCR 그대로 흐트러진 형태)
SOCO = ("■ 본 주택의 입주신청자(이하‘신청자‘)는 임차인 모집공고일 기준 19세 이상 39세 이하여야 합니다. "
        "■ 모든 계층의 신청자 및 입주 예정 세대원 전부는 무주택 요건을 충족하여야 합니다. "
        "(단, 기준 자동차가액 4,542만원 이내의 자동차(이륜차 포함)에 한함) "
        "■ 본 주택에 청약 시 특별공급과 일반공급 중 주택형(타입) 별로 1건만 가능하며, 신혼부부(예비신혼부부 포함)는 "
        "• 소득기준 – 청약 시 아래의 소득기준에 따라 정확한 순위 선택 필요 "
        "※ 청년안심주택 청년(본인) 자산기준은 30~39세 가구 금액을 따름 ※ 청년안심주택 (예비)신혼부부(구성될) 세대 자산기준은 소득3분위 금액을 따름 "
        "• 지역기준 – 동일 소득일 경우 지역순위 적용 순위 지역 비고 1순위 서울특별시 광진구 거주자 : 주민등록상 주소지 "
        "※ 주택 자체 근저당, 가압류 등에 의해 보증금 대출이 불가능 또는 일부 제한될 수 있으니 "
        "단지(임대사업자) 문제로 인한 입주예정자 대출 불가 시 계약 파기 해약금은 전액 환불 "
        "반려동물 양육이 불가한 현장임을 안내드립니다.")
ELYES = ("1) 나 이 제 한 : 만 19 세 이 상 ~ 만 39 세 이 하 2) 주 택 을 소 유 하 지 않 은 무 주 택 자 "
         "3) 차 량 제 한 : '26 년 기 준 차 량 기 준 가 액 4,542 만 원 ( 이 륜 차 포 함 ) 이 하 만 계 약 가 능 "
         "4) 국 토 교 통 부 전 자 계 약 가 능 한 자 ( 국 내 거 소 외 국 인 , 법 인 계 약 불 가 ) "
         "5) 국 토 부 전 자 계 약 체 결 일 로 부 터 1 개 월 이 내 입 주 가 능 한 실 거 주 예 정 자 "
         "2) 해 당 세 대 는 금 융 권 대 출 신 청 ( 버 팀 목 , 중 기 청 등 포 함 ) 이 불 가 능 합 니 다 .")


# 청년안심주택 특별공급 공고문 PDF 3쪽 글자(줄 단위 그대로) — 소득 순위표·자산 표
SOCO_TABLES = ("• 자산기준\n주거 전용 금회 청 년 (예비)신혼부부\n15.87㎡ 251 345 단위 : 백만원\n"
               "※ 청년안심주택 청년(본인) 자산기준은 30~39세 가구 금액을 따름\n"
               "※ 청년안심주택 (예비)신혼부부(구성될) 세대 자산기준은 소득3분위 금액을 따름\n"
               "- 통계청에서 발표하는 전년도 도시근로자 가구원수별 가구당 월평균 소득\n"
               "1순위 100% 3,813,363 5,866,270\n2순위 110% 4,194,699 6,452,897\n3순위 120% 4,576,036 7,039,524\n")


class T(unittest.TestCase):
    def test_soco(self):
        got = E.summarize(SOCO)
        self.assertEqual(got, [
            ("나이", "19~39세"), ("주택", "무주택(세대원 전부)"), ("소득", "공고문 참고"),
            ("자산", "청년 30~39세 가구 · 신혼 소득3분위 기준(금액 공고문 참고)"), ("차량", "4,542만 원 이하"),
            ("거주", "1순위 광진구(소득 같을 때)"), ("공급", "특별·일반"), ("불가", "반려동물")])
        # '임대사업자 … 대출 불가 시', '대출이 불가능 또는 일부 제한' 은 규정이 아니므로 넣지 않는다
        self.assertNotIn("대출", dict(got))
        self.assertNotIn("사업자", dict(got)["불가"])

    def test_table_rows_template(self):           # 사용자 견본: 대상 / 소득·자산·차량 / 거주 / 공급
        rows = E.table_rows(E.summarize(SOCO + SOCO_TABLES))
        self.assertEqual(rows, [
            ("대상", "19~39세 · 무주택(세대원 전부)"),
            ("소득·자산·차량", "소득 월평균 100·110·120%(1~3순위) · 자산 청년 2억 5,100만 / 신혼 3억 4,500만 · 차량 4,542만 원 이하"),
            ("거주", "1순위 광진구(소득 같을 때)"), ("공급", "특별·일반"), ("제한", "반려동물 불가")])
        self.assertFalse(any("금액은 공고문" in v for _, v in rows))     # 각주 줄은 없앴다
        self.assertEqual(E.split_money(rows)[1:3], [
            ("소득", "월평균 100·110·120%(1~3순위)"),
            ("자산·차량", "자산 청년 2억 5,100만 / 신혼 3억 4,500만 · 차량 4,542만 원 이하")])

    def test_missing_rows_not_made(self):         # 공고에 없는 항목은 행을 만들지 않는다
        self.assertEqual(E.table_rows([("차량", "4,542만 원 이하"), ("공급", "일반")]),
                         [("차량", "4,542만 원 이하"), ("공급", "일반")])

    def test_by_target(self):                     # 대상마다 다르면 나눠 적는다
        got = E.summarize("입주신청자는 만 19세 이상 만 39세 이하. 신혼부부는 혼인 기간 7년 이내인 자. 무주택자")
        self.assertEqual(E.table_rows(got)[0], ("대상", "청년 만19~39세 / 신혼 혼인 7년 이내 · 무주택"))

    def test_elyes_ocr(self):
        self.assertEqual(E.table_rows(E.summarize(ELYES)), [
            ("대상", "만19~39세 · 무주택"), ("차량", "4,542만 원 이하"), ("입주", "계약 후 1개월 이내"),
            ("제한", "법인·외국인 불가 · 대출 불가")])

    def test_newlywed_rule_not_in_table(self):    # '1세대로 간주·1건만 신청'은 신청 방법 — 자격 표에 넣지 않는다
        one = "신혼부부(예비신혼부부 포함)는 1세대로 간주하여 부부(예비부부) 중 1명이 대표로 1건만 신청이 가능하고"
        self.assertNotIn("혼인", dict(E.summarize(one)))
        intro = "■ 청년안심주택 공공지원민간임대는 무주택 청년 및 신혼부부의 주거안정을 위하여 공공의 지원을 받아"
        self.assertNotIn("혼인", dict(E.summarize(intro)))

    def test_nothing(self):
        self.assertEqual(E.summarize("공지사항 목록 1 2 3 리빙가이드북"), [])
        self.assertEqual(E.line([]), "자격: 공고문 확인")
        self.assertEqual(E.table_rows([]), [])

    def test_all_std_once(self):
        self.assertEqual(E.table_rows([("소득", E.STD), ("자산", E.STD), ("차량", E.STD)]),
                         [("소득·자산·차량", "공고문 참고")])

    def test_more_note(self):
        self.assertEqual(E.table_rows([("주택", "무주택"), E.MORE]), [("대상", "무주택"), ("※", "그 밖은 공고문 확인")])

    def test_income_asset_amounts(self):
        got = dict(E.summarize("도시근로자 가구당 월평균소득의 100% 이하, 신혼부부는 월평균소득 120% 이하. 총자산 3억4,500만원 이하인 자"))
        self.assertEqual(got["소득"], "월평균 100%·120% 이하 (유형별)")
        self.assertEqual(got["자산"], "3억 4,500만 원 이하")


if __name__ == "__main__":
    unittest.main()
