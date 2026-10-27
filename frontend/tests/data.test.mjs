// 데이터·계산 함수 단위 테스트: node --test tests/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { figures, parseQuestion, reportParagraphs } from '../src/data/analysis.js'
import { tradeRanking, periodMonths } from '../src/data/trade.js'
import { searchArticles } from '../src/data/articles.js'
import { maskEmail, initialReports, initialNotifications, initialSettings, currentNotifications, currentSettings } from '../src/data/seed.js'

test('질문에서 국가·품목·기간·리포트 요청을 읽는다', () => {
  const q = parseQuestion('중국 메모리 최근 6개월 리포트로 만들어줘')
  assert.equal(q.country, '중국')
  assert.equal(q.product, '854232')
  assert.equal(q.from, '2025-07')
  assert.equal(q.compareFrom, '2024-07')
  assert.equal(q.wantsReport, true)
  const follow = parseQuestion('미국으로 바꿔서 비교해줘', q)
  assert.equal(follow.country, '미국')
  assert.equal(follow.product, '854232')
})

test('중국·메모리 3개월 수치는 목업(157.2억 USD, +10.0%)과 같다', () => {
  const f = figures({ country: '중국', product: '854232', from: '2025-10', to: '2025-12', compareFrom: '2024-10', compareTo: '2024-12' })
  assert.equal(f.total, 157.2)
  assert.equal(f.prevTotal, 142.9)
  assert.equal(f.growthText, '+10.0%')
  assert.equal(f.imp, 39.6)
})

test('중국 교역 품목 순위는 수출 기준으로 정렬된다', () => {
  const rows = tradeRanking({ country: '중국' })
  assert.deepEqual(rows.map(r => r.name), ['메모리', '기초화장품', '전자집적회로', '승용차'])
  assert.equal(rows[0].exp, 157.2)
  const byImport = tradeRanking({ country: '중국', basis: 'imp' })
  assert.equal(byImport[0].imp, Math.max(...byImport.map(r => r.imp)))
  assert.equal(periodMonths('2025-01', '2025-12'), 12)
})

test('기사 검색은 탭·키워드·기간을 함께 적용한다', () => {
  assert.ok(searchArticles({ tab: 'short' }).every(a => a.kind === 'short'))
  const hits = searchArticles({ q: '반도체' })
  assert.ok(hits.length >= 5 && hits.every(a => (a.title + a.country + a.tags.join(' ')).includes('반도체')))
  assert.ok(searchArticles({ q: '반도체', from: '2026-10-07', to: '2026-10-08' }).every(a => a.date >= '2026.10.07'))
})

test('계정 찾기 화면은 이메일 일부를 가린다', () => {
  assert.equal(maskEmail('honggd@example.com'), 'ho***@example.com')
  assert.equal(maskEmail('gildong.h@gmail.com'), 'gi***@gmail.com')
})

test('DOCX 문단은 선택한 리포트 항목만 담는다', () => {
  const weekly = initialReports.find(r => r.kind === 'weekly')
  const lines = reportParagraphs(weekly)
  assert.ok(lines.some(l => l.startsWith('05 현지 이슈')))
  assert.ok(!lines.some(l => l.startsWith('02 주요 지표')))
})

test('비밀번호는 영문+숫자+특수문자 8자 이상만 허용한다', async () => {
  const { isStrongPassword, passwordChecks } = await import('../src/utils/validate.js')
  for (const ok of ['demo1234!', 'Abcdefg1@', 'a1~b2c3d4']) assert.equal(isStrongPassword(ok), true, ok)
  for (const bad of ['abcd1234', 'abcdefg!', '12345678!', 'ab1!', 'abcd 123!', 'abcd1234한', '비밀번호1234!']) assert.equal(isStrongPassword(bad), false, bad)
  assert.deepEqual(passwordChecks('abc').map(c => c.ok), [true, false, false, false, true])
})


test('이전 속보 알림·설정만 제거하고 다른 알림 이력과 설정은 보존한다', () => {
  const kept = { ...initialNotifications[0], read: true }
  const old = [kept, { id: 'n2', type: 'breaking', read: false }, ...initialNotifications.slice(1)]
  assert.deepEqual(currentNotifications(old), [kept, ...initialNotifications.slice(1)])
  assert.equal(old.length, initialNotifications.length + 1)
  const settings = { ...initialSettings, all: false, breaking: 'interest', scope: 'linked' }
  assert.deepEqual(currentSettings(settings), { ...initialSettings, all: false })
  assert.deepEqual(currentNotifications(currentNotifications(old)), currentNotifications(old))
})

test('순위에서 전달한 국가·품목·정확한 기간이 이전 대화보다 우선한다', () => {
  const previous = parseQuestion('중국 메모리 최근 6개월 분석')
  for (const period of ['2025-10~2025-12', '2025-07~2025-12', '2025-01~2025-12', '2025-12~2025-12']) {
    for (const [country, product] of [['미국', '330499'], ['', '330499'], ['미국', '']]) {
      const result = parseQuestion(`${country || '전체 국가'} · ${product ? `HS ${product}` : '전체 품목'} · ${period} 수출입을 분석해줘`, previous)
      assert.deepEqual([result.country, result.product, result.from, result.to], [country, product, ...period.split('~')])
      const follow = parseQuestion('이유를 알려줘', result)
      assert.deepEqual([follow.country, follow.product, follow.from, follow.to], [country, product, ...period.split('~')])
    }
  }
})
