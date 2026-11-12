// Read the laid-out print template. Keep text, tables and columns structured;
// the caller captures only SVG charts as images.
() => {
  const px = value => parseFloat(value) || 0
  const rgb = value => {
    const channels = value.match(/[\d.]+/g)
    if (!channels || channels[3] === '0') return null
    return channels.slice(0, 3).map(c => Number(c).toString(16).padStart(2, '0')).join('')
  }
  const rect = el => {
    const { x, y, width, height } = el.getBoundingClientRect()
    return { x, y, width, height }
  }
  function runs(el) {
    const result = []
    function visit(node, parent) {
      const style = getComputedStyle(parent)
      let text
      if (node.nodeType === Node.TEXT_NODE) text = node.textContent.replace(/\s+/g, ' ')
      else if (node.tagName === 'BR') text = '\n'
      else if (node.tagName === 'I' && !node.textContent) {
        result.push({ text: '■ ', color: rgb(getComputedStyle(node).backgroundColor) || '8A8F98', size: 10 })
        return
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        if (px(getComputedStyle(node).marginLeft)) result.push({ text: ' ', size: px(style.fontSize) })
        node.childNodes.forEach(n => visit(n, node))
        if (px(getComputedStyle(node).marginRight)) result.push({ text: ' ', size: px(style.fontSize) })
        return
      } else return
      result.push({ text, size: px(style.fontSize), bold: Number(style.fontWeight) >= 600,
        color: rgb(style.color), href: parent.closest('a')?.getAttribute('href') })
    }
    el.childNodes.forEach(n => visit(n, el))
    if (result.length) {
      result[0].text = result[0].text.trimStart()
      result[result.length - 1].text = result[result.length - 1].text.trimEnd()
    }
    if (el.tagName === 'LI') {
      const number = Array.from(el.parentElement.children).indexOf(el) + (el.parentElement.start || 1)
      result.unshift({ text: number + '. ', size: px(getComputedStyle(el).fontSize) })
    }
    return result
  }
  function box(el) {
    const style = getComputedStyle(el)
    const edges = ['Top', 'Left', 'Bottom', 'Right']
    return { ...rect(el), fill: rgb(style.backgroundColor),
      padding: edges.map(edge => px(style['padding' + edge])),
      borders: edges.map(edge => [px(style['border' + edge + 'Width']), rgb(style['border' + edge + 'Color'])]),
      line: px(style.lineHeight), align: style.textAlign }
  }
  let chartIndex = 0
  function block(el) {
    const data = box(el), children = Array.from(el.children)
    if (el.matches('.toc')) {
      return { ...data, kind: 'toc', rows: children.map(row => ({
        ...box(row), number: runs(row.querySelector('.n')), title: runs(row.querySelector('.t')),
        page: Number(row.dataset.tocPage)
      })) }
    }
    if (el.tagName.toLowerCase() === 'svg') return { ...data, kind: 'chart', index: chartIndex++ }
    if (el.tagName === 'TABLE') {
      const rows = Array.from(el.rows, row => Array.from(row.cells, cell => ({
        ...box(cell), runs: runs(cell), span: cell.colSpan, head: cell.tagName === 'TH'
      })))
      return { ...data, kind: 'table', rows }
    }
    const display = getComputedStyle(el).display
    if (el.matches('.legend')) {
      return { ...data, kind: 'text', runs: children.flatMap(child => [...runs(child), { text: '    ', size: 10 }]) }
    }
    if (display === 'grid' || display === 'flex') {
      const columns = display === 'grid' ? getComputedStyle(el).gridTemplateColumns.split(' ').map(px)
        : children.map(child => rect(child).width)
      return { ...data, kind: 'grid', columns, gap: px(getComputedStyle(el).columnGap),
        rowGap: px(getComputedStyle(el).rowGap), children: children.map(block) }
    }
    if (children.some(child => !getComputedStyle(child).display.startsWith('inline') && child.tagName !== 'BR')) {
      return { ...data, kind: 'stack', children: children.map(block) }
    }
    return { ...data, kind: 'text', runs: runs(el), tag: el.tagName }
  }
  return Array.from(document.querySelectorAll('section.page'), (el, index) => {
    const header = el.querySelector('.hdr, .top'), footer = el.querySelector('.ftr')
    const standard = header.classList.contains('hdr')
    const children = Array.from(el.children).filter(child => child !== header && child !== footer).map(block)
    const heading = children.find(child => child.tag === 'H2')
    if (heading) heading.page = index + 1
    return { ...rect(el), header: runs(standard ? header.firstElementChild : header),
      headerRight: standard ? header.lastElementChild.innerText : '',
      footer: footer.firstElementChild.innerText, children }
  })
}
