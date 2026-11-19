import { Btn } from '../../components/ui'
import { articleBadges } from './articlePresentation'
import ReferenceText from './ReferenceText'

const SHORT_NEWS_SOURCES = new Set(['KOTRA_SHORT_NEWS', 'KOTRA_SHORTNEWS'])

// Keep original array indexes so citation numbers stay stable when grouped by task.
export default function ArticleCards({ articles = [], taskId, taskIds, orderBy }) {
  return <div className="chat-article-list">
    {articles.map((article, index) => {
    if (taskId !== undefined ? article.task_id !== taskId : taskIds?.includes(article.task_id)) return null
    return ( <div className="li chat-article-card" key={`${article.task_id || ''}:${article.article_id}`}><div>
      <div className="chat-article-heading"><b>[{index + 1}] {article.title}</b><span className="chat-article-badges">{articleBadges(article).map(label => <span key={label} className={'tag ' + (label === '근거' ? 'red' : '')}>{label}</span>)}</span></div>
      <p className="muted">{article.source_code} · {article.published_date}</p>
      {article.sentences?.length ? article.sentences.map((s, i) => <details key={i}>
        <summary><ReferenceText text={s.text} /> · 근거 보기</summary>
        {s.quotes.map((quote, j) => <blockquote key={j}>{quote}</blockquote>)}
      </details>) : (article.summary || article.evidence_sufficient === false) && <p><ReferenceText text={article.summary || (article.unavailable ? '답변 뒤 공개가 바뀌어 볼 수 없는 기사입니다.' : orderBy === 'published_date_desc' ? '발행일 기준 최신 기사' : '본문 근거 부족')} /></p>}
      {/* unavailable: 검색 뒤 숨겨진 기사. [n] 번호만 지키려고 남긴 자리라 상세로 가지 않는다(D-102) */}
      {/* 기사 상세는 새 탭에서 연다. 같은 탭으로 가면 진행 중인 대화 화면을 벗어난다. */}
      {!article.unavailable && <Btn to={`/articles?id=${article.article_id}`} target="_blank" rel="noopener">기사 상세</Btn>}
      {/* 단신속보는 원문 주소로 본 기사를 볼 수 없어 원문 보기를 두지 않는다(기사 상세에서 내용을 본다). */}
      {!SHORT_NEWS_SOURCES.has(article.source_code) && /^https?:\/\//i.test(article.source_url || '') && <a href={article.source_url} target="_blank" rel="noreferrer">원문 보기</a>}
    </div></div>)})}
  </div>
}
