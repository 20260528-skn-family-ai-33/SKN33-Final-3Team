// 약관 본문(제목·문단·목록·표·굵은 글씨만 쓰는 마크다운)을 화면 요소로 그린다.
const inline = text => text.split(/(\*\*[^*]+\*\*)/).map((part, index) => (
  part.startsWith('**') && part.endsWith('**') ? <b key={index}>{part.slice(2, -2)}</b> : part
))
const cells = line => line.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim())
const PATTERNS = { table: /^\|/, ol: /^\d+\.\s/, ul: /^-\s/ }

function group(body) {
  const blocks = []
  for (const line of body.split('\n')) {
    const kind = Object.keys(PATTERNS).find(key => PATTERNS[key].test(line))
    const last = blocks[blocks.length - 1]
    if (kind) {
      if (last?.kind === kind) last.lines.push(line)
      else blocks.push({ kind, lines: [line] })
    } else if (line.startsWith('## ')) blocks.push({ kind: 'h', lines: [line.slice(3)] })
    else if (line.trim()) blocks.push({ kind: 'p', lines: [line] })
  }
  return blocks
}

const TABLE_CELL = { border: '1px solid var(--line)', padding: '8px 10px', textAlign: 'left', verticalAlign: 'top' }

function Block({ kind, lines }) {
  if (kind === 'h') return <h3 style={{ fontSize: 16, fontWeight: 800, marginTop: 20 }}>{lines[0]}</h3>
  if (kind === 'p') return <p style={{ marginTop: 8 }}>{inline(lines[0])}</p>
  if (kind === 'ol' || kind === 'ul') {
    const List = kind
    return (
      <List style={{ marginTop: 8, paddingLeft: 22, listStyle: kind === 'ol' ? 'decimal' : 'disc' }}>
        {lines.map((line, index) => <li key={index} style={{ marginTop: 4 }}>{inline(line.replace(PATTERNS[kind], ''))}</li>)}
      </List>
    )
  }
  const [head, , ...rows] = lines.map(cells)
  return (
    <div style={{ overflowX: 'auto', marginTop: 10 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead><tr>{head.map((cell, index) => <th key={index} style={{ ...TABLE_CELL, background: 'var(--bg)' }}>{inline(cell)}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, index) => <td key={index} style={TABLE_CELL}>{inline(cell)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

export default function TermsBody({ body }) {
  return <div style={{ fontSize: 14, lineHeight: 1.75, color: '#444B57' }}>{group(body || '').map((block, index) => <Block key={index} {...block} />)}</div>
}
