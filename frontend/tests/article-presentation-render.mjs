// 실제 JSX를 렌더링하여 혼합 기사와 완료된 본문의 문장 태그를 검증한다.
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const server = await createServer({server:{middlewareMode:true},appType:'custom'})
try {
  const {default:ArticleCards} = await server.ssrLoadModule('/src/pages/analysis/ArticleCards.jsx')
  const {default:ReferenceText} = await server.ssrLoadModule('/src/pages/analysis/ReferenceText.jsx')
  const direct='직접 근거 문장.', reference='참고(직접 답변 아님): 배경 문장.'
  const html=renderToStaticMarkup(createElement(ArticleCards,{articles:[{
    article_id:'1',title:'혼합 기사',source_code:'KOTRA_SHORT_NEWS',published_date:'2026-11-19',evidence_role:'mixed',
    sentences:[{text:direct,quotes:['근거 원문']},{text:reference,quotes:['참고 원문']}],
  }]}))
  assert.match(html, /class="tag red">근거/)
  assert.match(html, /<summary>직접 근거 문장\./)
  assert.match(html, /<summary><span class="tag">참고<\/span> 배경 문장\./)
  assert.doesNotMatch(html, /직접 답변 아님/)
  assert.match(html, /참고 원문/)
  const answer=renderToStaticMarkup(createElement(ReferenceText,{text:direct+'\n'+reference}))
  assert.equal((answer.match(/class="tag"/g)||[]).length,1)
  assert.match(answer,/직접 근거 문장\./)
  assert.doesNotMatch(answer,/근거<\/span>/) // 표기가 없는 문장에 새 근거 태그를 추론해 붙이지 않음.
  console.log('PASS mixed article evidence badges, sentence reference tags and completed answer text')
} finally { await server.close() }
