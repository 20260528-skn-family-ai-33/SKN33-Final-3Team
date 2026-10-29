"""실험 JSON에서 HTML·Markdown·CSV와 선택 PDF 생성. 모델 학습은 호출하지 않는다."""
import html
import json
from pathlib import Path

import numpy as np
import pandas as pd

from backend.app.ml.experiment_metrics import METRIC_GUIDE


RANK_METRICS = [("precision", "Precision@K"), ("ndcg", "NDCG@K"), ("hit", "Hit@K"), ("top20_precision", "상위 20% Precision@K"),
                ("top20_recall", "상위 20% Recall@K"), ("top20_f1", "상위 20% F1@K"), ("grew_share", "추천 중 실제 증가 비율"),
                ("mean_actual_growth", "추천의 실제 로그 성장 평균"), ("spearman", "그룹 내 Spearman")]
OTHER_METRICS = [("accuracy", "증가 여부", "Accuracy", "높을수록"), ("balanced_accuracy", "증가 여부", "Balanced accuracy", "높을수록"),
                 ("precision", "증가 여부", "Precision", "높을수록"), ("recall", "증가 여부", "Recall", "높을수록"),
                 ("f1", "증가 여부", "F1", "높을수록"), ("mcc", "증가 여부", "MCC", "높을수록"),
                 ("roc_auc", "증가 여부", "ROC-AUC", "높을수록"), ("average_precision", "증가 여부", "Average precision", "높을수록"),
                 ("predicted_growth_rate", "증가 여부", "증가라고 판정한 비율 (참고)", "참고"),
                 ("mae", "성장률 회귀", "MAE", "낮을수록"), ("rmse", "성장률 회귀", "RMSE", "낮을수록"),
                 ("median_ae", "성장률 회귀", "Median AE", "낮을수록"), ("r2", "성장률 회귀", "R²", "높을수록"),
                 ("explained_variance", "성장률 회귀", "Explained variance", "높을수록"),
                 ("pearson", "성장률 회귀", "Pearson", "높을수록"), ("spearman", "성장률 회귀", "Spearman", "높을수록"),
                 ("fit_seconds", "실행", "학습 시간(초)", "낮을수록")]


def metric_matrix(board, ks):
    """지표(행) × 후보(열) 평균표. 비교 가능한 후보 중 가장 좋은 값을 '최고 후보'로 표시한다."""
    if board.empty:
        return pd.DataFrame()
    names = (board.scenario + "/" + board.feature_set + "/" + board.model).tolist()
    ok = board.get("comparable", board.complete).to_numpy(dtype=bool)
    specs = [(f"{d}_{m}_at_{k}", f"{label} Top {k}", name.replace("@K", f"@{k}"), "높을수록")
             for d, label in (("country", "국가별 품목"), ("product", "품목별 국가")) for k in ks for m, name in RANK_METRICS]
    specs += [(key, group, name, better) for key, group, name, better in OTHER_METRICS]
    rows = []
    for key, group, name, better in specs:
        column = key + "_mean"
        if column not in board:
            continue
        values = pd.to_numeric(board[column], errors="coerce").to_numpy(dtype=float)
        usable = ok & np.isfinite(values)
        best = "—"
        if better != "참고" and usable.any():
            pick = np.where(usable, values, np.inf if better == "낮을수록" else -np.inf)
            best = names[int(np.argmin(pick) if better == "낮을수록" else np.argmax(pick))]
        rows.append({"구분": group, "지표": name, "좋은 방향": better, **dict(zip(names, values)), "최고 후보": best})
    return pd.DataFrame(rows)


def _value(value):
    if value is None or (isinstance(value, (float, np.floating)) and not np.isfinite(value)):
        return "미평가"
    if isinstance(value, (float, np.floating)):
        return f"{value:.5f}"
    return str(value)


def _table(frame):
    if frame.empty:
        return "<p>해당 결과 없음</p>"
    return frame.map(_value).to_html(index=False, escape=True, border=0)


def _md_table(frame):
    if frame.empty:
        return "해당 결과 없음\n"
    def cell(x): return _value(x).replace("|", "/").replace("\n", " ")
    return ("| " + " | ".join(map(str, frame.columns)) + " |\n| " + " | ".join(["---"] * len(frame.columns)) + " |\n" +
            "\n".join("| " + " | ".join(cell(x) for x in row) + " |" for row in frame.itertuples(index=False, name=None)))


def _bar_chart(board, metric, title):
    if metric not in board:
        return ""
    rows = board[board.complete & board[metric].notna()].head(16)
    if rows.empty:
        return ""
    maximum = max(float(rows[metric].max()), .001)
    height = 50 + 34 * len(rows)
    svg = [f'<svg viewBox="0 0 1000 {height}" role="img" aria-label="{html.escape(title, quote=True)}">',
           f'<text x="12" y="22" font-size="17">{html.escape(title)}</text>']
    for i, row in enumerate(rows.to_dict("records")):
        y = 45 + 34*i
        label = html.escape(f'{row["scenario"]} / {row["feature_set"]} / {row["model"]}')
        val = float(row[metric])
        svg += [f'<text x="12" y="{y+16}" font-size="11">{label}</text>',
                f'<rect x="400" y="{y}" width="{max(0,val)/maximum*460:.1f}" height="22" fill="#267e92"/>',
                f'<text x="{410+max(0,val)/maximum*460:.1f}" y="{y+16}" font-size="12">{val:.4f}</text>']
    return "".join(svg) + "</svg>"


def write_reports(run_dir, make_pdf=False):
    """이미 저장된 결과만 읽는다. PDF 실패해도 HTML/MD/CSV는 남긴다."""
    from backend.app.ml.experiments import leaderboard, _write
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text("utf-8"))
    results = json.loads((run_dir / "results.json").read_text("utf-8"))
    config = manifest["config"]
    primary = config["primary_metric"]
    board = leaderboard(run_dir)
    if not board.empty:
        valid_key = primary + "_valid_runs"
        board["comparable"] = board.complete & (board.get(valid_key, pd.Series(0, index=board.index)) == board.expected_runs)
        sort_columns = ["comparable"] + ([primary + "_mean"] if primary + "_mean" in board else [])
        board = board.sort_values(sort_columns, ascending=False, na_position="last")
    board.to_csv(run_dir / "leaderboard.csv", index=False, encoding="utf-8-sig")
    matrix = metric_matrix(board, config["ks"])
    matrix.to_csv(run_dir / "metric_matrix.csv", index=False, encoding="utf-8-sig")
    flat = pd.json_normalize(results, sep=".")
    flat.to_csv(run_dir / "fold_metrics.csv", index=False, encoding="utf-8-sig")
    segment_rows = []
    for record in results:
        for name, metrics in record.get("segments", {}).items():
            segment_rows.append({"case_id": record["case_id"], "segment": name, **metrics})
    segments = pd.DataFrame(segment_rows)
    segments.to_csv(run_dir / "segments.csv", index=False, encoding="utf-8-sig")
    successful = [r for r in results if r["status"] == "ok"]
    issues = pd.DataFrame([{"case": r["case_id"], "status": r["status"],
                            "reason": r.get("reason", r.get("error", ""))} for r in results if r["status"] != "ok"])
    notes = [
        f"실행 상태: {manifest['status']}. 성공 {len(successful)} / 기록된 실행 {len(results)}.",
        f"단계: {config['phase']} / 예측 기간: {config['horizon']}개월 / gap: {config['gap_months']}개월.",
        f"평가 기준월: {', '.join(config['validation_anchors'])}. 예약 Holdout: {config['holdout_anchor']}.",
        f"모델 선택 기준: {primary}. 모든 예정 fold·seed의 주 지표가 있는 후보만 비교 가능.",
        f"train 상한: {config['max_train_rows'] or '없음 (전체)'}. test는 후보를 샘플링하지 않음.",
        "시간/seed별 평균·표준편차는 기술 통계이며 신뢰구간이 아님. 독립 Holdout 여부는 실제 사용 이력으로 판단.",
        "NDCG는 정확도/성공 확률이 아님. Precision@K는 실제 Top K와의 교집합/K.",
        "모든 예측은 연속 로그 성장 점수. 0 기준 증가 여부 지표와 순위 지표를 구분.",
        "복구 피처는 값 존재와 기여가 다름. __coverage.csv의 train/test 관측률과 실제 사용 열을 확인.",
        "기존 운영 모델·metadata·DB는 갱신하지 않음. 결과와 모델은 이 실험 폴더에만 저장.",
    ]
    columns = ["scenario", "feature_set", "model", "kind", "comparable", "successful_runs", "expected_runs",
               primary + "_mean", primary + "_std", "country_precision_at_5_mean", "product_ndcg_at_5_mean",
               "rmse_mean", "mae_mean", "r2_mean", "roc_auc_mean", "average_precision_mean", "fit_seconds_mean"]
    compact = board[[c for c in columns if c in board]].copy()
    guide = pd.DataFrame(METRIC_GUIDE.items(), columns=["지표", "정의"])
    md = ["# 모델 A 실험 비교 리포트", "\n".join("- " + n for n in notes),
          "## 요약 비교", _md_table(compact),
          "## 지표별 비교 (fold·seed 평균, 최고 후보는 비교 가능한 후보 중)", _md_table(matrix),
          "## 실패와 생략", _md_table(issues),
          "## 지표 정의", _md_table(guide)]
    sections = []
    for record in results:
        caption = record["case_id"]
        metrics = pd.DataFrame(record.get("metrics", {}).items(), columns=["metric", "value"])
        section_md = [f"## {caption}", f"상태: {record['status']} / train {record['train_rows']:,} / test {record['test_rows']:,}",
                      "사용 피처: " + ", ".join(record["features_used"]), _md_table(metrics)]
        md += section_md
        status_text = html.escape(f"{record['status']} / train {record['train_rows']:,} / test {record['test_rows']:,}")
        param_json = html.escape(json.dumps(record.get("effective_params", record.get("params", {})), ensure_ascii=False, indent=2))
        sections.append(f'<details><summary>{html.escape(caption)} ({html.escape(record["status"])})</summary>'
                        f'<p>{status_text}</p><p>사용 피처: {html.escape(", ".join(record["features_used"]))}</p>'
                        f'<pre>{param_json}</pre><div class="scroll">{_table(metrics)}</div>'
                        f'<p>{html.escape(record.get("reason", record.get("error", "")))}</p></details>')
    md += ["## 품목군별 결과", _md_table(segments), "## 재현 설정", "```json\n" +
           json.dumps(manifest, ensure_ascii=False, indent=2) + "\n```"]
    (run_dir / "report.md").write_text("\n\n".join(md), encoding="utf-8")
    links = " ".join(f'<a href="{html.escape(name)}">{name}</a>' for name in
                     ["leaderboard.csv", "metric_matrix.csv", "fold_metrics.csv", "segments.csv", "manifest.json",
                      "results.json", "report.md"])
    charts = _bar_chart(board, primary + "_mean", primary + " (높을수록 좋음)")
    charts += _bar_chart(board, "rmse_mean", "RMSE (낮을수록 좋음)")
    document = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    document += '<title>모델 A 실험 비교 리포트</title><style>'
    document += ('body{font-family:system-ui,"Malgun Gothic",sans-serif;max-width:1400px;margin:32px auto;padding:0 24px;color:#183047;line-height:1.65}'
                 'h1,h2{color:#102438}table{border-collapse:collapse;font-size:13px;min-width:500px;width:100%}'
                 'th,td{border:1px solid #d8e1ea;padding:7px 10px;text-align:left;white-space:nowrap}'
                 'th{background:#eaf0f6}tr:nth-child(even){background:#f6f8fa}.scroll{overflow:auto;margin:16px 0}'
                 'details{border-bottom:1px solid #d8e1ea;padding:12px 0}summary{cursor:pointer;font-weight:600}'
                 'pre{white-space:pre-wrap;overflow-wrap:anywhere}a{margin-right:16px;color:#125c88}'
                 'svg{width:100%;max-width:1000px;height:auto}svg text{fill:#183047}'
                 '@media print{body{margin:0}details{break-inside:avoid}.scroll{overflow:visible}table{font-size:9px}th,td{white-space:normal}}')
    document += '</style></head><body><h1>모델 A 실험 비교 리포트</h1>'
    document += f'<p>{html.escape(manifest["run_id"])}</p><ul>' + ''.join('<li>'+html.escape(n)+'</li>' for n in notes) + '</ul>'
    document += f'<p>{links}</p><h2>비교 요약</h2>{charts}<div class="scroll">{_table(compact)}</div>'
    document += ('<h2>지표별 비교</h2><p>fold·seed 평균. \'최고 후보\'는 모든 fold를 마친 비교 가능한 후보 중 좋은 방향으로 가장 나은 값이다. '
                 '기준선(baseline_*)도 함께 비교한다.</p>' + f'<div class="scroll">{_table(matrix)}</div>')
    document += f'<h2>실패와 생략</h2><div class="scroll">{_table(issues)}</div><h2>각 실행의 전체 지표</h2>' + ''.join(sections)
    document += f'<h2>품목군별 결과</h2><div class="scroll">{_table(segments)}</div>'
    document += f'<h2>지표 정의</h2><div class="scroll">{_table(guide)}</div>'
    document += f'<details><summary>설정과 데이터 계보</summary><pre>{html.escape(json.dumps(manifest, ensure_ascii=False, indent=2))}</pre></details></body></html>'
    (run_dir / "report.html").write_text(document, encoding="utf-8")
    if make_pdf:
        try:
            _pdf(run_dir, notes, compact, issues, guide, results, matrix)
            _write(run_dir / "pdf_status.json", {"status": "generated", "visual_review": "사용자가 실행 결과를 확인해야 함"})
        except Exception as exc:
            _write(run_dir / "pdf_status.json", {"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            print(f"PDF 실패. HTML/MD/CSV는 보존했습니다: {exc}")
    return run_dir / "report.html"


def _pdf(run_dir, notes, compact, issues, guide, results, matrix):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from backend.app.ml.reporting import _fonts
    regular, bold = _fonts()
    style = ParagraphStyle("body", fontName=regular, fontSize=8, leading=12, wordWrap="CJK", spaceAfter=7)
    heading = ParagraphStyle("heading", fontName=bold, fontSize=17, leading=24, spaceAfter=12)
    cell = ParagraphStyle("cell", fontName=regular, fontSize=7, leading=10, wordWrap="CJK")
    story = []
    width = landscape(A4)[0] - 72
    def p(text, s=style): return Paragraph(html.escape(_value(text)).replace("\n", "<br/>"), s)
    def table(frame, widths=None):
        if frame.empty:
            story.append(p("해당 결과 없음")); return
        rows = [[p(c, cell) for c in frame.columns]] + [[p(v, cell) for v in row] for row in frame.itertuples(index=False, name=None)]
        t = Table(rows, colWidths=widths or [width/len(frame.columns)]*len(frame.columns), repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0,0),(-1,-1), .4, colors.HexColor("#D9D9D9")),
                               ("BACKGROUND", (0,0),(-1,0), colors.HexColor("#dce7f1")),
                               ("ROWBACKGROUNDS", (0,1),(-1,-1), [colors.white, colors.HexColor("#f5f7f9")]),
                               ("VALIGN", (0,0),(-1,-1), "MIDDLE"),
                               ("TOPPADDING", (0,0),(-1,-1),5), ("BOTTOMPADDING", (0,0),(-1,-1),5)]))
        story.extend([t, Spacer(1,10)])
    story.append(p("모델 A 실험 비교 리포트", heading))
    story.extend(p(n) for n in notes)
    # 넓은 요약을 한 표에 압축하지 않고 후보별 전체 지표를 뒤에 제공한다.
    short = [c for c in compact.columns if c in ("scenario", "feature_set", "model", "comparable", "rmse_mean", "mae_mean", "r2_mean")
             or ("ndcg" in c and c.endswith("_mean"))]
    table(compact[short])
    if not matrix.empty:
        # 후보가 많으면 6개씩 나눠 한 쪽 폭에 맞춘다.
        fixed = ["구분", "지표", "좋은 방향"]
        candidates = [c for c in matrix.columns if c not in (*fixed, "최고 후보")]
        for start in range(0, len(candidates), 6):
            chunk = candidates[start:start + 6]
            story.extend([PageBreak(), p(f"지표별 비교 ({start + 1}~{start + len(chunk)}번째 후보, fold·seed 평균)", heading)])
            cols = [*fixed, *chunk, "최고 후보"]
            table(matrix[cols], [width * .1, width * .16, width * .06, *[width * .68 / (len(chunk) + 1)] * (len(chunk) + 1)])
    if not issues.empty:
        story.extend([PageBreak(), p("실패와 생략", heading)]); table(issues)
    for record in results:
        story.extend([PageBreak(), p(record["case_id"], heading)])
        story.append(p(f"상태 {record['status']} / train {record['train_rows']:,} / test {record['test_rows']:,}"))
        story.append(p("사용 피처: " + ", ".join(record["features_used"])))
        story.append(p(record.get("reason", record.get("error", ""))))
        table(pd.DataFrame(record.get("metrics", {}).items(), columns=["metric", "value"]), [width*.72, width*.28])
        for name, metrics in record.get("segments", {}).items():
            story.append(p("품목군 " + name, heading))
            table(pd.DataFrame(metrics.items(), columns=["metric", "value"]), [width*.72, width*.28])
    story.extend([PageBreak(), p("지표 정의", heading)]); table(guide, [width*.32, width*.68])
    def footer(canvas, doc):
        canvas.setFont(regular,8); canvas.drawRightString(landscape(A4)[0]-36,20,str(doc.page))
    SimpleDocTemplate(str(run_dir / "report.pdf"), pagesize=landscape(A4), leftMargin=36,rightMargin=36,
                      topMargin=32,bottomMargin=36).build(story,onFirstPage=footer,onLaterPages=footer)
