"""Write the PDF print template as editable Word paragraphs and tables."""
from io import BytesIO
from pathlib import Path

from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Mm, Pt, RGBColor

from backend.app.services.report_pdf import print_page


LAYOUT = Path(__file__).resolve().parents[1] / 'templates/reports/word_layout.js'
EDGES = ('top', 'left', 'bottom', 'right')


def element(tag, **attributes):
    node = OxmlElement('w:' + tag)
    for key, value in attributes.items():
        node.set(qn('w:' + key), str(value))
    return node


def points(px):
    return Pt(px * .75)


def paragraph(parent, line):
    p = parent.add_paragraph()
    fmt = p.paragraph_format
    fmt.space_before = fmt.space_after = Pt(0)
    fmt.line_spacing = points(max(1.4, line))
    fmt.widow_control = False
    p._p.get_or_add_pPr().append(element('snapToGrid', val=0))
    for option in ('autoSpaceDE', 'autoSpaceDN'):
        p._p.pPr.append(element(option, val=0))
    return p


def field_begin(p, instruction):
    begin = p.add_run()
    begin._r.append(element('fldChar', fldCharType='begin'))
    code = element('instrText')
    code.set(qn('xml:space'), 'preserve')
    code.text = f' {instruction} '
    command = p.add_run()
    command._r.append(code)
    p.add_run()._r.append(element('fldChar', fldCharType='separate'))


def field_end(p):
    p.add_run()._r.append(element('fldChar', fldCharType='end'))


def write_text(p, block):
    p.alignment = {'right': WD_ALIGN_PARAGRAPH.RIGHT, 'center': WD_ALIGN_PARAGRAPH.CENTER}.get(
        block['align'], WD_ALIGN_PARAGRAPH.LEFT)
    if 'toc_style' in block:
        p.style = block['toc_style']
        page = block['page']
        p._p.append(element('bookmarkStart', id=page, name=f'ReportPage{page}'))
    for item in block['runs']:
        if not item['text']:
            continue
        run = p.add_run(item['text'])
        run.font.name = '맑은 고딕'
        run._r.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
        run.font.size = points(item['size'])
        run.bold = bool(item.get('bold'))
        if item.get('color'):
            run.font.color.rgb = RGBColor.from_string(item['color'])
        href = item.get('href')
        if href and href.lower().startswith(('https://', 'http://')):
            link = element('hyperlink')
            link.set(qn('r:id'), p.part.relate_to(href, RT.HYPERLINK, is_external=True))
            link.append(run._r)
            p._p.append(link)
    if 'toc_style' in block:
        p._p.append(element('bookmarkEnd', id=page))
    if block.get('tag') in ('H1', 'H2', 'H3'):
        p._p.get_or_add_pPr().append(element('outlineLvl', val=int(block['tag'][1]) - 1))


def native_table(parent, widths):
    table = parent.add_table(rows=0, cols=len(widths))
    table.autofit = False
    for col, width in zip(table.columns, widths):
        col.width = points(width)
    props = table._tbl.tblPr
    props.find(qn('w:tblW')).set(qn('w:type'), 'dxa')
    props.find(qn('w:tblW')).set(qn('w:w'), str(round(sum(widths) * 15)))
    margins = element('tblCellMar')
    for edge in EDGES:
        margins.append(element(edge, w=0, type='dxa'))
    props.append(margins)
    return table


def decorate(cell, block, outer=None):
    props = cell._tc.get_or_add_tcPr()
    fill = block['fill'] or (outer and outer['fill'])
    if fill:
        props.append(element('shd', fill=fill, val='clear'))
    margins, borders = element('tcMar'), element('tcBorders')
    for i, edge in enumerate(EDGES):
        pad = block['padding'][i]
        width, color = block['borders'][i]
        if outer and edge in ('top', 'bottom'):
            pad += outer['padding'][i]
            if not width:
                width, color = outer['borders'][i]
        margins.append(element(edge, w=round(pad * 15), type='dxa'))
        if width and color:
            borders.append(element(edge, val='single', color=color, sz=max(2, round(width * 6))))
    props.append(margins)
    props.append(borders)


def data_table(parent, block):
    rows = block['rows']
    if not rows:
        return
    # Browser cell widths already include CSS percentages and intrinsic text widths.
    columns = max(rows, key=len)
    widths = [cell['width'] / cell['span'] for cell in columns for _ in range(cell['span'])]
    table = native_table(parent, widths)
    for i, cells in enumerate(rows):
        row = table.add_row()
        row._tr.get_or_add_trPr().append(element('cantSplit'))
        if i == 0 and all(cell['head'] for cell in cells):
            row._tr.trPr.append(element('tblHeader', val=1))
        position = 0
        for source in cells:
            end = position + source['span']
            cell = row.cells[position]
            if end > position + 1:
                cell = cell.merge(row.cells[end - 1])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            decorate(cell, source)
            cell._tc.clear_content()
            write_text(paragraph(cell, source['line']), source)
            position = end


def write_contents(parent, block):
    # A native TOC makes Word paginate before refreshing its nested PAGEREF fields.
    # One field per print-template fragment keeps long contents lists independently updatable.
    for i, row in enumerate(block['rows']):
        p = paragraph(parent, row['line'])
        fmt = p.paragraph_format
        fmt.space_before = points(row['padding'][0])
        fmt.space_after = points(row['padding'][2])
        fmt.tab_stops.add_tab_stop(Mm(13))
        fmt.tab_stops.add_tab_stop(points(block['width']), WD_TAB_ALIGNMENT.RIGHT)
        border = element('pBdr')
        border.append(element('bottom', val='single', sz=4, space=2, color='D6DDE9'))
        p._p.get_or_add_pPr().append(border)
        if i == 0:
            field_begin(p, f'TOC \\t "{block["toc_style"]},1" \\h')
        write_text(p, {'runs': row['number'], 'align': 'left'})
        p.add_run().add_tab()
        write_text(p, {'runs': row['title'], 'align': 'left'})
        p.add_run().add_tab()
        field_begin(p, f"PAGEREF ReportPage{row['page']} \\h")
        p.add_run(str(row['page']))
        field_end(p)
    if block['rows']:
        field_end(p)


def write_block(parent, block, chart, *, inside_cell=False):
    kind = block['kind']
    if kind == 'toc':
        write_contents(parent, block)
    elif kind == 'table':
        data_table(parent, block)
    elif kind == 'chart':
        p = paragraph(parent, 1.4)
        p.paragraph_format.line_spacing = 1.0  # inline graphics need their full line height
        p.add_run().add_picture(BytesIO(chart(block['index'])), width=points(block['width']))
    elif kind == 'grid':
        widths = []
        for index, width in enumerate(block['columns']):
            if index and block['gap']:
                widths.append(block['gap'])
            widths.append(width)
        table = native_table(parent, widths)
        for i, child in enumerate(block['children']):
            col = i % len(block['columns'])
            if col == 0:
                if i and block['rowGap']:
                    spacer = table.add_row()
                    spacer.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
                    spacer.height = points(block['rowGap'])
                row = table.add_row()
            cell = row.cells[col * (2 if block['gap'] else 1)]
            cell._tc.clear_content()
            decorate(cell, child, block)
            write_block(cell, child, chart, inside_cell=True)
            # Word requires every cell, including those ending in a nested table, to end in a paragraph.
            if cell._tc[-1].tag != qn('w:p'):
                paragraph(cell, 1.4)
    elif not inside_cell and (block['fill'] or any(w for w, _ in block['borders'])):
        row = native_table(parent, [block['width']]).add_row()
        if kind == 'text' and not block['runs']:
            row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
            row.height = points(max(1.4, block['height']))
        cell = row.cells[0]
        cell._tc.clear_content()
        decorate(cell, block)
        write_block(cell, block, chart, inside_cell=True)
    elif kind == 'stack':
        write_stack(parent, block['children'], block['y'] + block['padding'][0], chart)
    else:
        write_text(paragraph(parent, block['line']), block)


def write_stack(parent, blocks, top, chart):
    for block in blocks:
        gap = block['y'] - top
        if gap > 1:
            paragraph(parent, gap)
        write_block(parent, block, chart)
        top = block['y'] + block['height']


def page_caption(container, left, right='', *, footer=False, cover=False):
    container.is_linked_to_previous = False
    p = container.paragraphs[0]
    fmt = p.paragraph_format
    fmt.space_before = fmt.space_after = Pt(0)
    fmt.line_spacing = 1.0
    if cover:
        fmt.left_indent = Mm(5.5)
    fmt.tab_stops.add_tab_stop(Mm(174.5 if cover else 180), WD_TAB_ALIGNMENT.RIGHT)
    write_text(p, {'runs': left, 'align': 'left'})
    run = p.add_run()
    if right or footer:
        run.add_tab()
    if footer:
        for field in ('PAGE', 'SECTIONPAGES'):
            if field == 'SECTIONPAGES':
                p.add_run(' / ')
            p._p.append(element('fldSimple', instr=field))
    else:
        run.add_text(right)
    run.font.size = Pt(7.5)
    run.font.color.rgb = RGBColor.from_string('6B7079')
    if cover and not footer:
        return
    border = element('pBdr')
    border.append(element('top' if footer else 'bottom', val='single', sz=4, space=4, color='D6DDE9'))
    p._p.get_or_add_pPr().append(border)


def append_print_template(doc, saved, settings):
    settings_xml = doc.settings.element
    update = settings_xml.find(qn('w:updateFields'))
    if update is None:
        update = element('updateFields')
        settings_xml.append(update)
    update.set(qn('w:val'), 'true')
    with print_page(saved['report_type'], saved['context'], settings, scale=2) as page:
        pages = page.evaluate(LAYOUT.read_text(encoding='utf-8'))
        headings = {b['page']: b for sheet in pages for b in sheet['children'] if 'page' in b}
        for sheet in pages:
            for toc in (b for b in sheet['children'] if b['kind'] == 'toc' and b['rows']):
                rows = toc['rows']
                style = doc.styles.add_style(f"Report Heading {rows[0]['page']}", WD_STYLE_TYPE.PARAGRAPH)
                style.base_style = doc.styles['Normal']
                toc['toc_style'] = style.name
                for row in rows:
                    headings[row['page']]['toc_style'] = style.name
        charts = page.locator('svg.chart')
        def chart(index):
            return charts.nth(index).screenshot(animations='disabled', timeout=30000)

        section = doc.add_section(WD_SECTION.NEW_PAGE)
        section.left_margin = section.right_margin = Mm(15)
        section.top_margin, section.bottom_margin = Mm(25), Mm(18)
        section.header_distance, section.footer_distance = Mm(13), Mm(7)
        section.different_first_page_header_footer = True
        section._sectPr.append(element('pgNumType', start=1))
        border = element('pgBorders', display='firstPage', offsetFrom='page')
        border.append(element('left', val='single', color='1E293A', sz=96, space=0))
        section._sectPr.append(border)
        def footer_runs(sheet):
            return [{'text': sheet['footer'], 'size': 9.5, 'color': '9AA0A8'}]

        page_caption(section.first_page_header, pages[0]['header'], cover=True)
        page_caption(section.first_page_footer, footer_runs(pages[0]), footer=True, cover=True)
        if len(pages) > 1:
            page_caption(section.header, pages[1]['header'], pages[1]['headerRight'])
            page_caption(section.footer, footer_runs(pages[1]), footer=True)
        for index, sheet in enumerate(pages):
            if index:
                p = paragraph(doc, 1.4)
                p.paragraph_format.page_break_before = True
            if index == 0:
                # The cover text is inset 20.5 mm; all subsequent pages use 15 mm.
                wrapper = native_table(doc, [sheet['width'] - 30 * 96 / 25.4]).add_row().cells[0]
                wrapper._tc.clear_content()
                margin = element('tcMar')
                for side in ('left', 'right'):
                    margin.append(element(side, w=Mm(5.5).twips, type='dxa'))
                wrapper._tc.get_or_add_tcPr().append(margin)
                write_stack(wrapper, sheet['children'], sheet['y'] + 25 * 96 / 25.4, chart)
            else:
                write_stack(doc, sheet['children'], sheet['y'] + 25 * 96 / 25.4, chart)
