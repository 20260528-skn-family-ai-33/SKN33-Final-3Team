"""저장된 실행 결과와 노트북 검증 결과를 한국어 PDF로 만든다."""
import argparse
import os
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from backend.app.ml import features as F
from backend.app.ml.validation import ROOT, file_sha256, load_context, read_json

DEFAULT_PDF = ROOT / "output/pdf/ml_market_report.pdf"


def _fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "MLKorean" in pdfmetrics.getRegisteredFontNames():
        return "MLKorean", "MLKoreanBold"
    candidates = [os.getenv("ML_REPORT_FONT"), "C:/Windows/Fonts/malgun.ttf",
                  "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
                  "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"]
    regular = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
    if regular is None:
        raise FileNotFoundError("한글 TTF 폰트가 필요합니다. ML_REPORT_FONT에 폰트 경로를 지정하세요.")
    bold_candidates = [os.getenv("ML_REPORT_FONT_BOLD"), "C:/Windows/Fonts/malgunbd.ttf",
                       "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"]
    bold = next((Path(p) for p in bold_candidates if p and Path(p).is_file()), regular)
    pdfmetrics.registerFont(TTFont("MLKorean", str(regular)))
    pdfmetrics.registerFont(TTFont("MLKoreanBold", str(bold)))
    return "MLKorean", "MLKoreanBold"


def generate_report(context, validation, pdf_path=DEFAULT_PDF):
    """원본 결과를 추정하지 않는다. 누락·실패는 보고서에 그대로 표시한다."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon

    regular, bold = _fonts()
    navy, teal = colors.HexColor("#19314B"), colors.HexColor("#127F82")
    muted, light = colors.HexColor("#586B7D"), colors.HexColor("#F0F5F8")
    red = colors.HexColor("#AE3945")
    width = A4[0] - 88
    styles = {
        "body": ParagraphStyle("body", fontName=regular, fontSize=9.2, leading=15.3,
                               textColor=navy, spaceAfter=8, wordWrap="CJK"),
        "small": ParagraphStyle("small", fontName=regular, fontSize=8.0, leading=12.3,
                                textColor=muted, spaceAfter=7, wordWrap="CJK"),
        "title": ParagraphStyle("title", fontName=bold, fontSize=25, leading=35, textColor=navy, spaceAfter=18),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=18, leading=26, textColor=navy, spaceAfter=14),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=11, leading=17, textColor=teal, spaceBefore=9, spaceAfter=7),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=8.1, leading=12, textColor=navy, wordWrap="CJK"),
        "compact": ParagraphStyle("compact", fontName=regular, fontSize=7.5, leading=10, textColor=navy, wordWrap="CJK"),
        "header": ParagraphStyle("header", fontName=bold, fontSize=8.1, leading=12, textColor=colors.white, wordWrap="CJK"),
        "value": ParagraphStyle("value", fontName=bold, fontSize=22, leading=30, textColor=teal),
    }
    story = []
    meta, ev = context.get("metadata", {}), context.get("evaluation", {})
    summary = context.get("feature_summary", {})
    inventory = context.get("input_inventory", {})
    predictions = context.get("predictions_summary", {})
    cv = validation.get("cv", {})
    tests = validation.get("unit_tests", {})
    checks = validation.get("checks", [])
    passed = bool(tests.get("passed")) and bool(checks) and all(c["passed"] for c in checks)
    passed = passed and not validation.get("errors") and context.get("run_status") == "accepted"
    generated = validation.get("generated_at", datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"))

    def p(text, style="body"):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), styles[style])

    def add(text, style="body"):
        story.append(p(text, style))

    def page(title):
        if story:
            story.append(PageBreak())
        add(title, "h1")

    def table(rows, widths=None, compact=False):
        cell_style = "compact" if compact else "cell"
        cells = [[p(value, "header" if i == 0 else cell_style) for value in row] for i, row in enumerate(rows)]
        widths = widths or [width / len(rows[0])] * len(rows[0])
        t = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light]),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 2 if compact else 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2 if compact else 6),
            ("LINEBELOW", (0, -1), (-1, -1), .5, colors.HexColor("#D6E0E8")),
        ]))
        story.append(t)
        story.append(Spacer(1, 9))

    def num(value, digits=4):
        return "미평가" if value is None else f"{value:.{digits}f}"

    def count(value):
        return "미확인" if value is None else f"{value:,}"

    def metric(key, name="ndcg_at_5"):
        value = ev.get(key, {})
        return value.get(name) if isinstance(value, dict) else None

    def years(kind):
        values = inventory.get("external", {}).get(kind, {}).get("years", [])
        return ", ".join(str(y) for y in values) if values else "미확인"

    def chart(labels, series, height=175):
        d = Drawing(width, height)
        left, bottom, top = 35, 38, height - 15
        plot_width = width - left - 15
        for tick in (0, .25, .5, .75, 1):
            y = bottom + (top - bottom) * tick
            d.add(Line(left, y, width - 8, y, strokeColor=colors.HexColor("#DCE5EC"), strokeWidth=.4))
            d.add(String(3, y - 3, f"{tick:.2f}", fontName=regular, fontSize=7.5, fillColor=muted))
        group_width = plot_width / max(1, len(labels))
        bar_width = min(42, group_width / (len(series) + 1.8))
        for j, (name, values, color) in enumerate(series):
            for i, value in enumerate(values):
                if value is None:
                    continue
                x = left + group_width * (i + .5) + (j - (len(series) - 1) / 2) * (bar_width + 4) - bar_width / 2
                h = max(0, value) * (top - bottom)
                d.add(Rect(x, bottom, bar_width, h, fillColor=color, strokeColor=None))
                d.add(String(x + bar_width / 2, bottom + h + 4, f"{value:.3f}",
                             textAnchor="middle", fontName=bold, fontSize=7.5, fillColor=navy))
        for i, label in enumerate(labels):
            d.add(String(left + group_width * (i + .5), 23, str(label), textAnchor="middle",
                         fontName=regular, fontSize=8.0, fillColor=navy))
        for j, (name, _, color) in enumerate(series):
            x = left + j * 175
            d.add(Rect(x, 3, 8, 8, fillColor=color, strokeColor=None))
            d.add(String(x + 13, 3, name, fontName=regular, fontSize=7.5, fillColor=muted))
        return d

    def flow_diagram():
        d = Drawing(width, 175)
        nodes = [(0, 105, "01 수집 원본", "CSV / JSON / xlsx"),
                 (175, 105, "02 정합성·코드 통일", "완료 기록 / HS 대표 코드"),
                 (350, 105, "03 피처·미래 정답", "국가 x HS6 x 기준월"),
                 (350, 15, "04 시간 분할·학습", "12개월 분리 / 학습 내 변환"),
                 (175, 15, "05 평가·채택", "기준선 / 관련 피처 / K-fold"),
                 (0, 15, "06 결과·보고서", "예측 CSV / 노트북 / PDF")]
        for x, y, title, caption in nodes:
            d.add(Rect(x, y, 157, 55, rx=5, ry=5, fillColor=light, strokeColor=colors.HexColor("#CDDCE4")))
            d.add(String(x + 10, y + 32, title, fontName=bold, fontSize=9.5, fillColor=navy))
            d.add(String(x + 10, y + 14, caption, fontName=regular, fontSize=8, fillColor=muted))
        for x1, y1, x2, y2 in [(157, 133, 175, 133), (332, 133, 350, 133),
                               (428, 105, 428, 70), (350, 43, 332, 43), (175, 43, 157, 43)]:
            d.add(Line(x1, y1, x2, y2, strokeColor=teal, strokeWidth=1.2))
            if x1 == x2:
                points = [x2, y2, x2 - 3, y2 + 6, x2 + 3, y2 + 6]
            else:
                sign = 1 if x2 > x1 else -1
                points = [x2, y2, x2 - sign * 5, y2 + 3, x2 - sign * 5, y2 - 3]
            d.add(Polygon(points, fillColor=teal, strokeColor=None))
        return d

    # 1. Overview
    add("ML VALIDATION / EXPORT MARKET", "small")
    story.append(Spacer(1, 26))
    add("수출 유망 시장\n데이터·모델 검증 보고서", "title")
    add(f"작성 기준 {generated[:10]}  |  모델 A: 기존 수출 시장의 다음 12개월 성장 순위", "small")
    story.append(Spacer(1, 12))
    table([["검증 상태", "국가별 NDCG@5", "최신 예측 대상"],
           ["통과" if passed else "실패·미완료 항목 확인", num(metric("model_a")),
            count(predictions.get("rows")) + "쌍"]])
    add("핵심 결과", "h2")
    add(f"채택 지표는 {meta.get('promotion_metric', '미확인')}이다. 모델 점수 {num(metric('model_a'))}, "
        f"기준선 {num(metric('baseline_a'))}이며 선택한 피처 구성은 {meta.get('model_a_variant', ev.get('model_a_variant', '미확인'))}이다.")
    add(f"피처 {count(summary.get('rows'))}행, 기준월 {count(summary.get('anchors'))}개를 사용했다. "
        f"예측 기준월은 {meta.get('predict_anchor', '미확인')}이고, 국가 {count(predictions.get('countries'))}개·품목 {count(predictions.get('products'))}개가 결과에 포함된다.")
    add("이 보고서의 범위", "h2")
    add("수집 파일 형식·기간, 정합성 검증, 피처와 미래 정답 생성, 시간 분할, 기준선 비교, "
        "시간 순서 K-fold, 출력 스키마와 재실행 방법을 함께 기록한다. 미진출 시장 모델 C의 검증은 별도 과제다.")
    add("현재 제한", "h2")
    add("보수적인 외부 데이터 스냅샷 정책으로 시장·WDI 피처는 현재 학습·예측 구간에 연결하지 않았다. "
        "환율도 값이 없어 제외됐고 시장 유형 B는 미판정이다. 단일 평가월과 후향적 시간 교차검증은 독립 최종 Holdout을 대체하지 않는다.")
    if validation.get("errors") or context.get("error"):
        add("실행 오류: " + " / ".join(validation.get("errors", []) + [context.get("error", "")]), "small")

    # 2. Flow and periods
    page("01  전체 데이터 처리 흐름")
    story.append(flow_diagram())
    add("분석 단위와 기능", "h2")
    add("학습 한 행은 (국가, HS6 또는 HS 대응 묶음 대표 코드, 기준월 t)다. 같은 유망도 점수로 국가별 품목 Top 5와 품목별 국가 Top 5를 제공한다.")
    table([["구간", "실제 기간·크기", "역할"],
        ["관세청 원본", f"{inventory.get('customs_first', '미확인')} ~ {inventory.get('customs_last', '미확인')}\n{count(inventory.get('customs_months'))}개월 / {count(inventory.get('customs_files'))}개 파일", "월별 수출·수입·중량"],
        ["피처 기준월", f"{summary.get('first_anchor', '미확인')} ~ {summary.get('last_anchor', '미확인')}", "과거 24개월이 확보된 월"],
        ["정답 보유", f"{count(summary.get('labeled_anchors'))}개 기준월 / {count(summary.get('labeled_rows'))}행", "이후 12개월 수출이 모두 관측됨"],
        ["평가용 학습", " ~ ".join(ev.get("train_anchors", ["미확인"])) + f"\n{count(ev.get('train_rows'))}행", "평가월보다 최소 12개월 앞선 기준월"],
        ["단일 평가", str(ev.get("test_anchor", "미확인")) + f"\n{count(ev.get('test_rows'))}행", "그 다음 12개월 성장 순위 비교"],
        ["서비스 예측", meta.get("predict_anchor", "미확인"), "정답 없는 최신 기준월의 미래 성장 점수"]], [91, 203, width - 294])
    add("모든 실적 입력은 수집 파일을 사용한다. 환율만 fx_daily와 국가 통화 매핑을 DB에서 읽으며 접속 실패는 환율 결측으로 처리한다. ML은 수출·수입 실적을 DB에서 읽지 않는다.", "small")

    # 3. Customs schemas
    page("02  Input: 관세청 원본과 완료 기록")
    add("관세청 CSV 필수 열", "h2")
    table([["열", "형식·예시", "의미"],
        ["year", "문자열 YYYY.MM / 2026.09", "통계 대상 월"],
        ["statCd", "ISO2 문자열 / VN, NA", "상대 국가. NA(나미비아)를 결측으로 변환하지 않음"],
        ["hsCd", "10자리 문자열 / 8542321000", "앞 6자리를 HS6로 사용. 선행 0 보존"],
        ["expDlr", "유한 수치 / USD", "한국의 수출액. 결측·무한대는 오류"],
        ["expWgt", "수치 / kg", "수출 중량. 결측을 0 또는 일부 합계로 대체하지 않음"],
        ["impDlr", "유한 수치 / USD", "한국의 수입액. 결측·무한대는 오류"]], [71, 190, width - 261])
    add("파일 선택", "h2")
    add("연도별 일괄: collectors/customs/customs_outputs/*/customs_raw_*.csv\n정기 수집: data/customs_load_outputs/monthly/<YYYYMM>/<collection_dir>/customs_raw_<YYYYMM>.csv", "small")
    add("정기 수집은 월별 state.json이 지정한 collection_dir와 collect 완료 상태를 확인한다. 이름이 더 최신이어도 체크포인트가 선택하지 않은 수집본은 사용하지 않는다. 같은 달이 겹치면 정기 수집본을 우선하고 두 번 더하지 않는다.")
    add("확인된 무거래만 0", "h2")
    table([["완료 기록", "인정 조건"],
        ["customs_collection_*.json 또는 collection.json", "collection_complete=true, failed_countries·missing_months·unconfirmed_empty_countries가 비어 있음"],
        ["months", "YYYY.MM 문자열 배열. 실제 선택한 파일이 담당하는 월에만 적용"],
        ["행 부재", "위 완료 조건을 만족한 월의 없는 국가·HS6 행만 0으로 처리"],
        ["실제 행의 결측", "완료 월도 금액 결측·무한대 거부. 중량 결측은 보존"]], [180, width - 180])

    # 4. Other schemas
    page("03  Input: 외부 자료·기준정보·뉴스")
    table([["원천", "파일 형식·필수 열", "기간·현재 사용"],
        ["BACI / Comtrade", "CSV: year, country_code, hs6, import_world_usd, import_from_kor_usd", f"BACI: {years('baci')}\nComtrade: {years('comtrade')}\n연결 연도는 성장 비교에 사용"],
        ["WDI", "CSV: year, country_code, gdp_usd, gdp_growth_pct, population, gdp_per_capita_usd", years("wdi")],
        ["공개·버전 시점", "availability.csv: dataset, source, year, available_from\navailability_snapshot.json: 확보 시각·원본 SHA-256", f"로컬 확보 확인일 {context.get('snapshot', {}).get('available_from', '미확인')}. 공식 최초 공개일과 구분"],
        ["HS·국가 기준", "HS2017 코드 CSV, HS2022toHS2017.xlsx, 품목명 xlsx, 국가 시드 SQL", "HS 판 통일·국가 역할·지역 분류"],
        ["산업 묶음표", "CSV: group, hs4, role, source, note", f"{count(inventory.get('related_groups'))}개 산업 / {count(inventory.get('related_rows'))}행; product·input·related 역할"],
        ["단신속보", "원본 CSV: nttSn, nat, othbcDt\n분석 JSONL: external_id, hs_codes\n토픽 JSONL: external_id, picked", f"{inventory.get('news', {}).get('first', '미확인')} ~ {inventory.get('news', {}).get('last', '미확인')}\n{count(inventory.get('news', {}).get('rows'))}건"],
        ["환율", "DB: fx_daily(base_date, currency_code, rate_krw), country.currency_code", "유일한 DB 입력 예외. 현재 값 없어 결측"],
        ["UN 보조 자료", "CSV: freq_code, period_start, country_code, hs6_code, total_import_usd 등", f"월별 자료 마지막 {inventory.get('un', {}).get('last', '미확인')}. 피처에 사용하지 않음"]], [82, 240, width - 322])
    add("BACI는 FOB, Comtrade 수입은 CIF이므로 서로 다른 출처의 수준을 섞어 성장률을 계산하지 않는다. BACI 이후 해는 연결 연도의 Comtrade와 비교한다. 원본·완료 기록·버전 날짜표를 함께 보관해야 한다.", "small")

    # 5. Processing
    page("04  전처리·피처·정답 생성")
    steps = [
        ("국가·HS 정합성", "국가 시드의 COUNTRY만 남기고 총계·SPECIAL 행을 제외한다. HS2017 잔존 코드는 HS2022 대응 묶음 대표 코드로 합친다."),
        ("월별 관측 검증", "국가·HS6·월 키 중복을 거부한다. 수집 완료가 확인된 행 부재만 0으로 만들며 원본 금액 결측·무한대는 집계 전에 거부한다."),
        ("과거 24개월", "최근 12개월과 직전 12개월을 비교한다. 최근 12개월 수출이 100,000 USD 미만인 쌍은 후보에서 제외한다."),
        ("관련 품목", "같은 HS4·HS2 수출은 자기 품목을 제외하고 집계한다. 산업 묶음의 다른 제품·연관 수출과 투입 품목 수입을 성장 신호로 사용한다."),
        ("뉴스", "기사 게시 월을 기준으로 최근·직전 3개월 집계와 국가별 토픽 수를 붙인다. 수집 시작 전 창은 결측이다."),
        ("외부 자료 시점", "기준월 말까지 사용 가능했던 버전만 연결한다. 현재 정책에서는 시장·WDI가 모두 결측이다."),
        ("학습 내 변환", "학습 행에서 전부 결측인 열은 제거한다. 외부 성장률·GDP 성장률 등의 1%·99% 절단 경계와 y 절단 경계는 학습 행에서만 추정한다."),
    ]
    table([["단계", "처리 규칙"], *steps], [96, width - 96])
    add("정답과 수치 처리", "h2")
    add("y_growth = log1p(이후 12개월 수출 합계) - log1p(최근 12개월 수출 합계). 아직 미래 12개월이 확보되지 않은 최신 기준월의 정답은 결측으로 남긴다.")
    add("R1(일회성 거래) 보정은 현재 원본 CSV 경로에서 미적용이다. 그룹 합계 차감의 음수 반올림 오차는 0으로 제한하고 단가 로그의 무한대 피처는 결측으로 정리한다.")
    add("모델 A는 HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, random_state=0)다. region_id·hs2는 범주형으로 지정하며 별도 스케일링은 적용하지 않는다.")
    add(f"설정 피처 {len(F.FEATURES)}개 중 저장 모델 사용 피처는 {len(meta.get('features_used', []))}개다. 전체 목록과 실제 값 존재율은 마지막 부록에서 확인한다.", "small")

    # 6. Evaluation methodology
    page("05  평가 방법과 채택 기준")
    table([["항목", "정의·실행 규칙"],
        ["시간 분할", "정답 보유 마지막 기준월을 단일 평가월로 사용. 학습 기준월은 평가월보다 12개월 이상 앞선 월만 허용"],
        ["기준선", "직전 12개월 성장률 growth_12m이 높은 국가·품목을 고르는 규칙"],
        ["Precision@5", "예측 Top 5와 실제 미래 성장 Top 5의 공통 항목 수 / 5. 그룹별 값을 동일 가중 평균"],
        ["NDCG@5", "그룹 내 실제 미래 성장의 백분위 순위(rank(pct=True))를 relevance로 사용. relevance / log2(순위+1)의 합을 이상적인 순서의 DCG로 정규화"],
        ["양방향 평가", "국가별 품목 순위와 품목별 국가 순위를 모두 측정. 후보가 10개 미만이면 해당 그룹을 평가하지 않고 평가 그룹 수 기록"],
        ["관련 피처 비교", "같은 학습·평가 행에서 전체 피처 모델과 관련 품목 피처를 뺀 모델을 비교. 선택한 채택 지표가 높은 쪽을 사용, 동률이면 전체 유지"],
        ["저장 조건", "명시한 채택 지표가 기준선 이상이고 유한 값일 때 저장. 미평가 또는 기준선 미달이면 기존 산출물을 유지"],
        ["분포 변화", "반도체(HS4 8541·8542)와 나머지 품목군의 점수·평가 행·그룹 수를 별도로 기록"]], [100, width - 100])
    add("NDCG는 순서의 품질을 나타내며 Top 5의 직접 일치율은 Precision@5로 확인한다. 모델 점수는 로그 성장 예측값으로, 진출 성공 확률을 뜻하지 않는다.")
    add("모델 B는 미래 실적의 5개 시장 유형을 분류하고 동일 행·5개 클래스의 macro-F1으로 과거 규칙 기준선과 비교하도록 설계됐다. 현재는 공개 시점을 만족하는 시장 정답·임계값이 없어 B 점수와 유형을 미판정으로 남겼다.", "small")
    add("현재 단일 평가월은 관련 피처 선택에도 사용됐다. 새로운 데이터 구간 또는 별도 최종 Holdout에서 재검증하기 전에는 독립 최종 성능으로 해석하지 않는다.", "small")

    # 7. Saved results
    page("06  실제 평가 결과와 품목군 비교")
    full = ev.get("with_related", ev)
    reduced = ev.get("without_related", {})
    result_rows = [["비교", "국가 P@5", "국가 NDCG@5", "품목 P@5", "품목 NDCG@5"]]
    comparisons = [("기준선", {"model_a": ev.get("baseline_a", {}), "model_a_product": ev.get("baseline_a_product", {})}),
                   ("관련 피처 포함", full), ("관련 피처 제외", reduced)]
    for label, values in comparisons:
        a, b = values.get("model_a", {}), values.get("model_a_product", {})
        result_rows.append([label, num(a.get("precision_at_5")), num(a.get("ndcg_at_5")),
                            num(b.get("precision_at_5")), num(b.get("ndcg_at_5"))])
    table(result_rows, [103, 92, 104, 98, width - 397])
    story.append(chart([c[0] for c in comparisons], [("국가별 NDCG@5",
        [c[1].get("model_a", {}).get("ndcg_at_5") for c in comparisons], teal)]))
    add("선택 모델의 품목군별 점수", "h2")
    segment_rows = [["품목군", "행 / 평가 국가", "모델 NDCG@5", "기준선 NDCG@5"]]
    for key, label in (("semiconductor", "반도체"), ("other", "그 외")):
        segment = ev.get("segments", {}).get(key, {})
        segment_rows.append([label, f"{count(segment.get('rows'))} / {count(segment.get('model_a', {}).get('countries'))}",
            num(segment.get("model_a", {}).get("ndcg_at_5")), num(segment.get("baseline_a", {}).get("ndcg_at_5"))])
    table(segment_rows, [95, 145, 133, width - 373])
    add(f"전체 평가 그룹은 국가 {count(ev.get('model_a', {}).get('countries'))}개, 품목 {count(ev.get('model_a_product', {}).get('products'))}개다. 관련 피처의 이점은 선택한 지표·방향에 따라 달라지므로 양방향 점수를 함께 확인한다.", "small")

    # 8. K-fold
    page("07  모수 부족 시 시간 순서 K-fold")
    add(f"실행 상태: {cv.get('status', '미실행')} / 방식: {cv.get('method', '미확인')}")
    add(cv.get("reason", "교차검증 결과가 제공되지 않았다."))
    add("자동 조건은 단일 평가의 학습 기준월이 12개 미만이거나 학습 행이 1,000개 미만인 경우다. 행이 많아도 같은 기준월의 국가·품목은 독립적인 시간 관측으로 세지 않는다.")
    add("각 fold는 한 기준월 전체를 검증으로 묶고 과거 학습 창을 확장한다. 학습 정답 마지막 월 <= 검증 기준월을 만족하도록 12개월을 분리한다. 각 fold에서 결측 열 제거·절단 경계를 다시 학습한다. 저장 모델의 피처 구성은 고정한 후향적 보조 검증이다.")
    fold_rows = [["Fold", "학습 기준월", "검증 / 학습 행", "모델 NDCG", "기준선 NDCG"]]
    for fold in cv.get("folds", []):
        fold_rows.append([str(fold["fold"]), fold["train_start"] + "\n~ " + fold["train_end"],
            fold["validation_anchor"] + "\n" + count(fold["train_rows"]),
            num(fold["model_a"]["ndcg_at_5"]), num(fold["baseline_a"]["ndcg_at_5"])])
    if len(fold_rows) > 1:
        table(fold_rows, [37, 134, 130, 101, width - 402])
        story.append(chart([f"Fold {f['fold']}" for f in cv["folds"]], [
            ("모델 국가별 NDCG@5", [f["model_a"]["ndcg_at_5"] for f in cv["folds"]], teal),
            ("기준선", [f["baseline_a"]["ndcg_at_5"] for f in cv["folds"]], colors.HexColor("#A9B9C8"))]))
        aggregates = cv.get("aggregate", {})
        mean = aggregates.get("model_a", {}).get("ndcg_at_5", {})
        add(f"모델 평균 {num(mean.get('mean'))}, fold 간 표준편차 {num(mean.get('std'))}. 유효 fold {mean.get('valid_folds', 0)}개.", "small")
    add("fold 사이의 미래 12개월 정답 창은 일부 겹칠 수 있다. 이 평균·표준편차는 기술 통계이며 신뢰구간이 아니다. 과거가 부족해 fold 2개도 만들 수 없으면 이력 부족으로 기록하고 임의의 랜덤 K-fold로 대체하지 않는다.", "small")

    # 9. Output
    page("08  Output 형식과 조회 결과")
    table([["파일", "내용"],
        ["models.joblib", "모델 A·B와 학습 내 피처 변환기. B 미채택 시 model_b=None"],
        ["metadata.json", "학습 시각·예측 기준월·사용 피처·임계값·모델 선택·평가 결과"],
        ["predictions.csv", "최신 기준월의 국가·HS6별 성장 점수와 양방향 순위, 시장 유형"],
        ["ml_report_validation.json", "이번 노트북의 테스트·산출물 검증·K-fold 결과와 실행 시각"],
        ["ml_validation_report.executed.ipynb", "테스트와 검증 셀의 실제 실행 결과가 포함된 노트북"],
        ["ml_market_report.pdf", "이 문서. 테스트 완료 후 자동 생성하며 실패 항목도 포함"]], [192, width - 192])
    table([["predictions.csv 열", "형식·해석"],
        ["country_code / hs6", "ISO2 문자열 / 6자리 문자열. HS6는 대응 묶음 대표 코드일 수 있음"],
        ["score", "실수: 다음 12개월 수출 성장의 로그 차이 예측"],
        ["rank_in_country / rank_in_product", "1부터 시작하는 정수, 점수 내림차순의 국가 내 품목·품목 내 국가 순위"],
        ["market_type / market_type_name", "0~4 / 유형명. 라벨·임계값이 없으면 빈칸(미판정)"]], [192, width - 192])
    add(f"{context.get('sample_country_name', context.get('sample_country', '표본 국가'))} 품목 Top 5", "h2")
    preview_rows = [["순위", "HS6", "품목명", "점수"]]
    for row in context.get("preview", []):
        preview_rows.append([row["rank_in_country"], row["hs6"], row.get("product_name", ""), num(row["score"])])
    if len(preview_rows) > 1:
        table(preview_rows, [36, 67, 320, width - 423])
    add(f"시장 유형이 있는 예측은 {count(predictions.get('types_known'))}행이다. 현재 모델 B는 과거 공개 시장 라벨을 확보할 때까지 미판정으로 제공한다.", "small")

    # 10. Testing / reproducibility
    page("09  테스트·자동 생성·실행 명령")
    add(f"단위 테스트: 실행 {tests.get('run', 0)}개 / 실패 {tests.get('failures', 0)}개 / 오류 {tests.get('errors', 0)}개 / 건너뜀 {tests.get('skipped', 0)}개")
    for failure in tests.get("failure_details", []):
        add("실패 테스트: " + failure["test"] + "\n" + failure["traceback"][-500:], "small")
    table([["산출물 검사", "결과", "근거"], *[[c["name"], "통과" if c["passed"] else "실패", c["detail"]] for c in checks]], [112, 42, width - 154])
    add("저장소 루트에서 실행", "h2")
    for command in ["python -m pip install -r backend/requirements-ml-report.txt",
                    "python -m backend.app.ml.report_workflow",
                    "python -m backend.app.ml.report_workflow --train",
                    "python -m backend.app.ml.reporting"]:
        add(command, "small")
    add("첫 명령은 의존성 설치, 둘째는 기존 결과 검증·필요 시 시간 K-fold·PDF 자동 생성이다. --train은 원본부터 학습한 뒤 같은 검증 노트북을 실행한다. reporting 명령은 저장된 검증 JSON으로 PDF만 다시 만든다.")
    add("실행 파일과 실패 처리", "h2")
    add("notebooks/ml_validation_report.ipynb가 실제 테스트·산출물 확인·교차검증·PDF 생성을 수행한다. 테스트 실패는 보고서에 남긴 뒤 비정상 종료 코드를 반환한다. 과거 성공 PDF가 이번 결과처럼 남지 않도록 새 실행의 검증 기록을 사용한다.")
    add("런타임: " + ", ".join(f"{key} {value}" for key, value in context.get("runtime", {}).items()), "small")
    add(f"검증 실행 ID: {validation.get('run_id', '미확인')}\n모델 학습 시각: {meta.get('trained_at', '미확인')}\n검증 시각: {generated}", "small")
    add("한글 폰트는 Windows 맑은 고딕 또는 NanumGothic TTF를 사용한다. 다른 환경에서는 ML_REPORT_FONT와 선택적 ML_REPORT_FONT_BOLD를 지정한다. 모델 재현 시 scikit-learn·joblib 버전도 실행 기록과 맞춘다.", "small")

    # 11. Feature appendix
    page("부록  전체 피처와 값 존재율")
    add("분모는 이번 피처 테이블의 전체 행 수다. 실제 모델의 사용 여부는 학습 행에서 결정된다.", "small")
    nonmissing = summary.get("feature_nonmissing", {})
    used = set(meta.get("features_used", []))
    total = summary.get("rows", 0)
    rows = [["피처", "실제 값 존재율", "모델 사용"]]
    for name in F.FEATURES:
        ratio = f"{nonmissing[name] / total:.1%}" if total and name in nonmissing else "미확인"
        rows.append([name, ratio, "사용" if name in used else "제외"])
    table(rows, [302, 110, width - 412], compact=True)

    # 12. Sources and limitations
    page("근거 파일·한계·후속 검증")
    table([["근거", "역할"],
        ["data/ml/metadata.json", "채택 모델의 단일 평가·사용 피처·기준월"],
        ["data/ml/features_snapshot_summary.json", "피처 행 수·기준월·피처별 실제 값 수"],
        ["data/external/availability.csv 및 availability_snapshot.json", "외부 데이터의 보수적 사용 가능일·원본 버전 해시"],
        ["backend/app/ml/sources.py / features.py", "파일 선택·전처리·피처·미래 정답 구현"],
        ["backend/app/ml/market_model.py / validation.py", "채택 평가·시간 분할·K-fold 구현"],
        ["docs/ML_PLAN.md / ML_USAGE.md", "목표·범위·수집 현황·실행 계약"]], [255, width - 255])
    add("추가로 필요한 검증", "h2")
    for text in [
        "과거 버전별 외부 데이터와 공개 기록을 확보한 후 시장·WDI 피처와 모델 B를 다시 평가해야 한다.",
        "현재 UN 보조 자료에는 평가 정답 마지막 달이 없어 전체 12개월 수요 비교를 완료할 수 없다.",
        "관련 품목 묶음의 산업적 관계는 내용 검토가 필요하며, 자동 후보표는 검토 후 적용한다.",
        "2026년 반도체 등 분포 변화가 크므로 추가 시점 평가와 별도 최종 Holdout을 확보해야 한다.",
        "현재 CSV 스냅샷은 과거 시점의 모든 수정 이력을 복원한 자료를 뜻하지 않는다.",
    ]:
        add("• " + text)
    add("방법론 참고", "h2")
    add("scikit-learn 공식 문서: Cross-validation: evaluating estimator performance\nhttps://scikit-learn.org/stable/modules/cross_validation.html", "small")
    add("Jupyter NBClient 공식 문서: Executing notebooks\nhttps://nbclient.readthedocs.io/en/latest/client.html", "small")

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(44, A4[1] - 28, "수출 유망 시장 | 데이터·모델 검증")
        canvas.drawRightString(A4[0] - 44, A4[1] - 28, generated[:10])
        canvas.setStrokeColor(colors.HexColor("#D6E0E8"))
        canvas.line(44, 36, A4[0] - 44, 36)
        canvas.drawString(44, 23, "Model A / 기존 수출 시장 / 자동 생성 보고서")
        canvas.drawRightString(A4[0] - 44, 23, f"{doc.page}")
        canvas.restoreState()

    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = pdf_path.with_suffix(".part.pdf")
    doc = SimpleDocTemplate(str(temporary), pagesize=A4, leftMargin=44, rightMargin=44,
        topMargin=51, bottomMargin=49, title="수출 유망 시장 데이터·모델 검증 보고서", author="SKN33 Final 3Team")
    try:
        doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
        temporary.replace(pdf_path)
    finally:
        temporary.unlink(missing_ok=True)
    return pdf_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/ml")
    parser.add_argument("--validation", type=Path, default=ROOT / "output/pdf/ml_report_validation.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_PDF)
    args = parser.parse_args()
    validation = read_json(args.validation)
    if validation is None:
        parser.error("검증 기록이 없습니다. python -m backend.app.ml.report_workflow를 먼저 실행하세요.")
    context = load_context(args.data_dir)
    if validation.get("trained_at") != context["metadata"].get("trained_at"):
        parser.error("검증 기록과 현재 모델의 학습 시각이 다릅니다. report_workflow를 다시 실행하세요.")
    for path, digest in validation.get("artifacts_sha256", {}).items():
        if not Path(path).is_file() or file_sha256(path) != digest:
            parser.error(f"검증 후 산출물이 변경됐거나 없어졌습니다: {path}. report_workflow를 다시 실행하세요.")
    print(generate_report(context, validation, args.output))


if __name__ == "__main__":
    main()
