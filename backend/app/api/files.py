"""저장 스냅샷으로 파일 생성. 외부 URL·원본 기사 본문·임의 경로를 내보내지 않는다."""
import math
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from backend.app.api.common import require, ApiError


MIMES={'pdf':'application/pdf','xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
       'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def report_time(value):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M KST')


def user_xlsx_info(meta,conditions=None,compare_period=True):
    """일반 화면/정기 리포트용 정보 시트. SQL·내부 상태 대신 조회 조건과 출처를 싣는다."""
    conditions=conditions or {}
    rows=[]
    selected=conditions.get('period') or meta.get('period') or {}
    if selected.get('start') and selected.get('end'):
        rows.append(('조회 기간',f"{selected['start']} ~ {selected['end']}"))
    if conditions:
        hs=conditions.get('hs_items') or []
        rows.append(('조회 품목 (HS 코드)',', '.join(str(item['hs_code']) for item in hs) or '전체 품목'))
        country_codes=', '.join(conditions.get('country_codes') or [])
        # 개별 비교표 스냅샷은 리포트 전용 조건 대신 overlapping_scopes를 보존한다.
        if conditions.get('include_korea_total') or meta.get('overlapping_scopes'):
            scope='한국 전체'+(f' (비교 상대국: {country_codes})' if country_codes else '')
            rows.append(('조회 국가',scope))
        else:
            rows.append(('조회 국가 코드',country_codes or '전체 국가'))
        flow={'EXPORT':'수출','IMPORT':'수입','BOTH':'수출·수입'}.get(conditions.get('trade_flow'))
        if flow: rows.append(('조회 기준',flow))
        comparison=conditions.get('compare')
        if comparison:
            rows.append(('비교 방식',{'NONE':'비교 없음','MOM':'전월 대비','YOY':'전년 동월 대비',
                'YOY_PERIOD':'전년 동기 대비','CUSTOM':'지정 기간 비교'}.get(comparison,comparison)))
        previous=conditions.get('compare_period') or {}
        # 리포트 파일은 머리 정보에 비교 기간 날짜를 싣지 않는다(#95, 비교 방식·비교 수치는 유지). 일반 화면 엑셀은 그대로.
        if compare_period and comparison!='NONE' and previous.get('start') and previous.get('end'):
            rows.append(('비교 기간',f"{previous['start']} ~ {previous['end']}"))
    rows.append(('금액 단위','USD'))
    sources=[source.get('label') or source.get('code') if isinstance(source,dict) else str(source)
             for source in meta.get('sources',[])]
    if any(sources): rows.append(('출처',', '.join(dict.fromkeys(source for source in sources if source))))
    if meta.get('latest_available_month'): rows.append(('최신 공개 월',meta['latest_available_month']))
    if meta.get('generated_at'): rows.append(('생성 시각',report_time(meta['generated_at'])))
    if meta.get('rows_total') is not None: rows.append(('전체 결과 행 수',meta['rows_total']))
    notes=list(meta.get('warnings') or [])
    if meta.get('monthly_average_note'): notes.append(meta['monthly_average_note'])
    if conditions.get('include_korea_total') or meta.get('overlapping_scopes'):
        notes.append('한국 전체 수출액에는 개별 상대국 수출액이 포함되므로 비교 대상 간 금액을 합산하지 않습니다.')
    rows.extend(('주의사항',note) for note in dict.fromkeys(notes) if note)
    return rows


def result_xlsx(payload):
    meta=payload['meta']
    info=user_xlsx_info(meta,payload.get('conditions'),compare_period=meta.get('export_profile')!='PERIODIC_REPORT') if meta.get('export_profile') in ('TRADE_SCREEN','PERIODIC_REPORT') else None
    return xlsx_bytes([payload],meta,payload['sql'],unit_headers=True,info_rows=info)


def xlsx_bytes(tables,meta,sql,unit_headers=False,info_rows=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb=Workbook(); wb.remove(wb.active)
    for n,table in enumerate(tables,1):
        require(table['total_rows']<=1000,'엑셀은 필터 전체 결과 1,000행까지만 다운로드할 수 있습니다.','EXPORT_LIMIT_EXCEEDED',422)
        ws=wb.create_sheet(f'결과표{n}')
        from backend.app.api.results import display_columns
        columns=display_columns(table['columns'],table['rows'],table.get('meta'))
        # 천 단위 콤마·소수 자리는 셀 서식으로 붙인다(값은 숫자 그대로). 결과표 다운로드는 머리글에 단위도 적는다.
        ws.append([c['label']+(f" ({c['unit']})" if unit_headers and c.get('unit') else '') for c in columns])
        formats=['#,##0' if c.get('unit')=='USD' else '0.0' if c.get('unit')=='%' else None for c in columns]
        for row in table['rows']:
            values=[]
            for c in columns:
                v=row.get(c['key'])
                if isinstance(v,dict) and 'hs_code' in v: v=v['hs_code']
                elif isinstance(v,(dict,list)): v=str(v)
                values.append(v)
            ws.append(values)
            for cell,fmt in zip(ws[ws.max_row],formats):
                if fmt and isinstance(cell.value,(int,float)) and not isinstance(cell.value,bool):
                    # USD는 정수면 #,##0, 센트가 있으면 #,##0.00으로 원본 값을 그대로 보이게 한다.
                    cents=fmt=='#,##0' and math.isfinite(cell.value) and cell.value!=int(cell.value)
                    cell.number_format='#,##0.00' if cents else fmt
        # 수식으로 해석될 수 있는 원문도 문자열 셀로 강제한다.
        for row in ws:
            for cell in row:
                if isinstance(cell.value,str): cell.data_type='s'
        for cell in ws[1]: cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='006849')
        ws.freeze_panes='A2'
        for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=24
    info=wb.create_sheet('정보')
    info.append(['항목','내용'])
    # 순위는 상위 1,000행만 저장한다(trade.rankings). 잘렸으면 파일에도 전체 행 수를 남긴다.
    if meta.get('truncated') and isinstance(meta.get('rows_total'),int):
        info.append(['안내',f"전체 {meta['rows_total']:,}행 중 상위 1,000행만 담았습니다. 조건을 좁히면 전체를 받을 수 있습니다."])
    if info_rows is None:
        for key,value in meta.items(): info.append([key,str(value)])
        for query in sql: info.append(['실행 SQL',query])
    else:
        for row in info_rows: info.append(row)
    for row in info:
        for cell in row:
            if isinstance(cell.value,str): cell.data_type='s'
            if info_rows is not None: cell.alignment=Alignment(wrap_text=True,vertical='top')
    info.column_dimensions['A'].width=24; info.column_dimensions['B'].width=100
    stream=BytesIO(); wb.save(stream); return stream.getvalue()


def display(value):
    if value is None: return '자료 없음'
    if isinstance(value,(int,float)): return f'{value:,.2f}'.rstrip('0').rstrip('.')
    return str(value)


def chart_image(section, chart, settings):
    """저장 표와 같은 행으로 그린다. 외부 서비스 호출 없음."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.font_manager import FontProperties
    table=next(t for t in section['tables'] if t['result_id']==chart['result_id'])
    rows=table['rows']; x=chart['x_key']; y=chart['series_keys'][0]
    font_path=Path(settings.get('API_REPORT_FONT','C:/Windows/Fonts/malgun.ttf'))
    font=FontProperties(fname=str(font_path)) if font_path.is_file() else FontProperties()
    fig=Figure(figsize=(7,3),dpi=150); FigureCanvasAgg(fig); ax=fig.subplots()
    if not rows:
        ax.set_axis_off()
        ax.text(.5,.5,'조회된 자료가 없습니다.',ha='center',va='center',transform=ax.transAxes,fontproperties=font)
        ax.set_title(chart['title'],fontproperties=font,fontsize=11)
        fig.tight_layout(); out=BytesIO(); fig.savefig(out,format='png'); return out.getvalue()
    labels=[str(r[x]) for r in rows]; values=[r[y] for r in rows]
    if chart['chart_type']=='HORIZONTAL_BAR':
        ax.barh(labels,values,color='#006849'); ax.invert_yaxis()
    else:
        colors=['#006849','#2E6DB4','#8E5BB5','#E07B24','#C2477A']
        names={c['key']:c['label'] for c in table['columns']}
        for i,key in enumerate(chart['series_keys']):
            ax.plot(labels,[r.get(key) for r in rows],color=colors[i%len(colors)],marker='o',markersize=3,label=names.get(key,key))
        if len(chart['series_keys'])>1: ax.legend(prop=font,fontsize=8)
        if len(labels)>12:
            positions=list(range(0,len(labels),max(1,len(labels)//6)))
            ax.set_xticks(positions,[labels[i] for i in positions])
        ax.tick_params(axis='x',labelrotation=30)
    from matplotlib.ticker import FuncFormatter
    (ax.xaxis if chart['chart_type']=='HORIZONTAL_BAR' else ax.yaxis).set_major_formatter(FuncFormatter(lambda value, _:f'{value:,.0f}'))
    for label in ax.get_xticklabels()+ax.get_yticklabels(): label.set_fontproperties(font); label.set_fontsize(8)
    ax.set_title(chart['title'],fontproperties=font,fontsize=11)
    ax.spines[['top','right']].set_visible(False); ax.grid(axis='x' if chart['chart_type']=='HORIZONTAL_BAR' else 'y',alpha=.15)
    fig.tight_layout(); out=BytesIO(); fig.savefig(out,format='png'); return out.getvalue()


def report_pdf(document,settings):
    if document.get('template_pdf'):
        from backend.app.services.report_pdf import template_pdf
        saved=document['template_pdf']
        return template_pdf(saved['report_type'],saved['context'],settings)
    return reportlab_pdf(document,settings)


def reportlab_pdf(document,settings):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, KeepTogether, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4
    font=Path(settings.get('API_REPORT_FONT','C:/Windows/Fonts/malgun.ttf'))
    if not font.is_file(): raise ApiError(503,'REPORT_FONT_UNAVAILABLE','한글 PDF 글꼴 API_REPORT_FONT 설정이 필요합니다.')
    if 'ReportKorean' not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont('ReportKorean',str(font)))
    styles=getSampleStyleSheet()
    for key in styles.byName: styles[key].fontName='ReportKorean'
    styles.add(ParagraphStyle('KBody',fontName='ReportKorean',fontSize=9,leading=15,spaceAfter=8))
    styles.add(ParagraphStyle('KTitle',fontName='ReportKorean',fontSize=25,leading=36,spaceAfter=24))
    styles.add(ParagraphStyle('KHead',fontName='ReportKorean',fontSize=16,leading=23,textColor=colors.HexColor('#006849'),spaceAfter=16))
    p=lambda text,style='KBody':Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])
    stream=BytesIO(); story=[Spacer(1,95),p('BORDERLESS SELLERS','KHead'),p(document['title'],'KTitle'),
        p(document['subtitle']),p('생성일 '+report_time(document['generated_at'])),PageBreak(),p('목차','KHead')]
    for i,s in enumerate(document['sections'],1): story.append(p(f'{i:02}  {s["title"]}'))
    story.append(PageBreak())
    for i,s in enumerate(document['sections'],1):
        story.extend([p(f'{i:02}  {s["title"]}','KHead'),p(s['text'])])
        for chart in s.get('charts',[]): story.extend([Image(BytesIO(chart_image(s,chart,settings)),width=480,height=206),Spacer(1,12)])
        for table in s.get('tables',[]):
            columns=table['columns']; visible=[c for c in columns if c['type'] in ('number','string')][:6]
            data=[[p(c['label']) for c in visible]]
            data.extend([[p(display(row.get(c['key']))) for c in visible] for row in table['rows']])
            if visible:
                grid=Table(data,colWidths=[483/len(visible)]*len(visible),repeatRows=1,hAlign='LEFT')
                grid.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#EEEAE4')),('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),0.4,colors.HexColor('#D9D4CC')),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
                story.extend([grid,Spacer(1,18)])
        for a in s.get('articles',[]): story.extend([p(a['title']),p(a['summary']),p(f'{a["source_code"]} · {a["published_date"]} · {a["source_url"]}')])
        story.append(PageBreak())
    story.extend([p('데이터 기준 · 출처','KHead'),p(document['subtitle'])])
    for source in document['meta']['sources']: story.append(p(source['label']+' '+(source.get('url') or '')))
    story.append(p('수치는 생성 당시 저장된 결과표 기준입니다. 이후 적재된 데이터로 재계산하지 않습니다.'))
    def footer(canvas,doc):
        canvas.setFont('ReportKorean',8); canvas.setFillColor(colors.HexColor('#777777'))
        canvas.drawString(56,28,'BORDERLESS SELLERS  무역·해외시장 분석 리포트')
        canvas.drawRightString(A4[0]-56,28,str(doc.page))
    SimpleDocTemplate(stream,pagesize=A4,leftMargin=56,rightMargin=56,topMargin=48,bottomMargin=52,title=document['title']).build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()


def report_docx(document,settings=None):
    from backend.app.api.config import Settings
    settings=settings or Settings()
    from docx import Document
    from docx.shared import Pt, Cm, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    # 사용자가 제공한 요약보고서의 결재란/행 구조를 보존한 템플릿이다.
    doc=Document(Path(__file__).resolve().parents[1]/'templates/reports/summary.docx'); section=doc.sections[0]
    section.top_margin=section.bottom_margin=Cm(2); section.left_margin=section.right_margin=Cm(2)
    for name in ('Normal','Title','Heading 1','Heading 2'):
        style=doc.styles[name]; style.font.name='맑은 고딕'; style.font.size=Pt(10 if name=='Normal' else 20)
        style.element.rPr.rFonts.set(qn('w:eastAsia'),'맑은 고딕')
    replacements={
        '{{report_date}}':report_time(document['generated_at'])[:10], '{{title}}':document['title'],
        '{{scope}}':document['subtitle']+'\n출처: '+', '.join(s['label'] for s in document['meta']['sources']),
        '{{summary}}':next((s['text'] for s in document['sections'] if s['section_code']=='SUMMARY'),document['sections'][0]['text']),
        '{{conclusion}}':'수치와 기사 목록은 첨부 상세 분석을 참조하세요. ML 시장 평가·RAG 원인 해석은 담당 모듈 연결 전까지 제공하지 않습니다.',
        '{{follow_up}}':'담당자가 통계 기준과 원문 기사를 확인한 뒤 시장 판단·담당·일정·예산을 기입합니다.'}
    seen=set()
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell._tc in seen: continue
                seen.add(cell._tc)
                for paragraph in cell.paragraphs:
                    for key,value in replacements.items():
                        if key in paragraph.text: paragraph.text=paragraph.text.replace(key,value)
    doc.add_page_break()
    doc.add_paragraph('BORDERLESS SELLERS'); doc.add_heading(document['title'],0)
    doc.add_paragraph(document['subtitle']); doc.add_paragraph('생성일 '+report_time(document['generated_at'])); doc.add_page_break()
    doc.add_heading('목차',1)
    for s in document['sections']: doc.add_paragraph(s['title'])
    doc.add_page_break()
    for s in document['sections']:
        doc.add_heading(s['title'],1); doc.add_paragraph(s['text'])
        for chart in s.get('charts',[]): doc.add_picture(BytesIO(chart_image(s,chart,settings)),width=Cm(16))
        for source in s.get('tables',[]):
            # Preserve every saved column, including the seven-column report KPI.
            cols=source['columns']
            table=doc.add_table(rows=1,cols=len(cols)); table.style='Light Shading Accent 1'
            for cell,col in zip(table.rows[0].cells,cols): cell.text=col['label']
            repeat=OxmlElement('w:tblHeader'); table.rows[0]._tr.get_or_add_trPr().append(repeat)
            for row in source['rows']:
                for cell,col in zip(table.add_row().cells,cols): cell.text=display(row.get(col['key']))
        for a in s.get('articles',[]):
            doc.add_heading(a['title'],2); doc.add_paragraph(a['summary']); doc.add_paragraph(f'{a["source_code"]} · {a["published_date"]}\n{a["source_url"]}')
        doc.add_page_break()
    doc.add_heading('데이터 기준 · 출처',1); doc.add_paragraph(document['subtitle'])
    for source in document['meta']['sources']: doc.add_paragraph(source['label']+' '+(source.get('url') or ''))
    stream=BytesIO(); doc.save(stream); return stream.getvalue()
