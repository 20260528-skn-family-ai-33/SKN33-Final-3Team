// 파일 내려받기(엑셀·DOCX). 서버 연동 후에는 서버가 만든 파일 URL로 바꾼다.

export function download(name, data, type) {
  const url = URL.createObjectURL(data instanceof Blob ? data : new Blob([data], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const xml = s => String(s).replace(/[<>&"']/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' })[c])

/** 첫 행을 머리글로 하는 표를 엑셀(SpreadsheetML .xls)로 내려받는다. 숫자는 숫자 형식으로 보존한다. */
export function downloadExcel(rows, name = '수출입_데이터', sheet = '데이터') {
  const body = rows.map(row => `<Row>${row.map(v => `<Cell><Data ss:Type="${typeof v === 'number' ? 'Number' : 'String'}">${xml(v)}</Data></Cell>`).join('')}</Row>`).join('')
  download(
    `${name}.xls`,
    `<?xml version="1.0"?><?mso-application progid="Excel.Sheet"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="${xml(sheet)}"><Table>${body}</Table></Worksheet></Workbook>`,
    'application/vnd.ms-excel',
  )
}

/** 문단 텍스트만 담은 작은 DOCX(무압축 ZIP)를 만든다. 별도 라이브러리 없이 동작한다. */
export function downloadDocx(title, paragraphs) {
  const files = {
    '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
    '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
    'word/document.xml': `<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${[title, ...paragraphs].map((s, i) => `<w:p>${i === 0 ? '<w:pPr><w:pStyle w:val="Title"/></w:pPr>' : ''}<w:r>${i === 0 ? '<w:rPr><w:b/><w:sz w:val="36"/></w:rPr>' : ''}<w:t xml:space="preserve">${xml(s)}</w:t></w:r></w:p>`).join('')}<w:sectPr/></w:body></w:document>`,
  }
  const enc = new TextEncoder()
  const chunks = []
  const central = []
  let offset = 0
  const header = length => {
    const b = new Uint8Array(length)
    return [b, new DataView(b.buffer)]
  }
  for (const [name, text] of Object.entries(files)) {
    const n = enc.encode(name)
    const d = enc.encode(text)
    let crc = 0xffffffff
    for (const byte of d) {
      crc ^= byte
      for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0)
    }
    crc = (crc ^ 0xffffffff) >>> 0
    const [h, v] = header(30)
    v.setUint32(0, 0x04034b50, true); v.setUint16(4, 20, true); v.setUint32(14, crc, true)
    v.setUint32(18, d.length, true); v.setUint32(22, d.length, true); v.setUint16(26, n.length, true)
    chunks.push(h, n, d)
    const [c, w] = header(46)
    w.setUint32(0, 0x02014b50, true); w.setUint16(4, 20, true); w.setUint16(6, 20, true); w.setUint32(16, crc, true)
    w.setUint32(20, d.length, true); w.setUint32(24, d.length, true); w.setUint16(28, n.length, true); w.setUint32(42, offset, true)
    central.push(c, n)
    offset += 30 + n.length + d.length
  }
  const size = central.reduce((s, x) => s + x.length, 0)
  const [end, v] = header(22)
  v.setUint32(0, 0x06054b50, true); v.setUint16(8, 3, true); v.setUint16(10, 3, true); v.setUint32(12, size, true); v.setUint32(16, offset, true)
  download(`${title}.docx`, new Blob([...chunks, ...central, end], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }))
}
