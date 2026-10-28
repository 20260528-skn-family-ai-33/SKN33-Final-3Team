"""저장 스냅샷으로 파일 생성. 외부 URL·원본 기사 본문·임의 경로를 내보내지 않는다."""
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


def xlsx_bytes(tables,meta,sql):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb=Workbook(); wb.remove(wb.active)
    for n,table in enumerate(tables,1):
        require(table['total_rows']<=1000,'엑셀은 필터 전체 결과 1,000행까지만 다운로드할 수 있습니다.','EXPORT_LIMIT_EXCEEDED',422)
        ws=wb.create_sheet(f'결과표{n}')
        columns=table['columns']; ws.append([c['label'] for c in columns])
        for row in table['rows']:
            values=[]
            for c in columns:
                v=row.get(c['key'])
                if isinstance(v,(dict,list)): v=str(v)
                values.append(v)
            ws.append(values)
        # 수식으로 해석될 수 있는 원문도 문자열 셀로 강제한다.
        for row in ws:
            for cell in row:
                if isinstance(cell.value,str): cell.data_type='s'
        for cell in ws[1]: cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='006849')
        ws.freeze_panes='A2'
        for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=24
    info=wb.create_sheet('정보')
    info.append(['항목','내용'])
    for key,value in meta.items(): info.append([key,str(value)])
    for query in sql: info.append(['실행 SQL',query])
    for row in info:
        for cell in row:
            if isinstance(cell.value,str): cell.data_type='s'
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
        ax.plot(labels,values,color='#006849',marker='o',markersize=3)
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
            cols=source['columns'][:6]
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
