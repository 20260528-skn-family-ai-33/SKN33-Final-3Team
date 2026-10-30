// BS19 구독 설정 / 수정
import { useState } from 'react'
import { navigate } from '../../router'
import { useApp } from '../../store'
import { Btn, Check } from '../../components/ui'
import { toggleValue } from '../../data/catalog'

const CYCLES = [['weekly', '주간', '매주 월요일 발행 · 뉴스·이슈'], ['monthly', '월간', '매월 15일 발행 · 통계+이슈'], ['quarterly', '분기', '분기 종료 후 20일 발행']]
const FORMATS = [['PDF', '기본 선택'], ['DOCX', '편집 가능한 문서']]

function OptionCard({ checked, onChange, title, desc, style }) {
  return (
    <Check checked={checked} onChange={onChange} className={'li' + (checked ? ' sel' : '')} style={{ flex: 1, padding: '18px 20px', gap: 12, ...style }}>
      <div>
        <p style={{ fontSize: 16, fontWeight: 800 }}>{title}</p>
        <p className="note">{desc}</p>
      </div>
    </Check>
  )
}

export default function Subscription() {
  const { subscription, setSubscription, notify, options } = useApp()
  const [cycles, setCycles] = useState(subscription.cycles)
  const [formats, setFormats] = useState(subscription.formats.length ? subscription.formats : ['PDF'])
  const valid = cycles.length > 0 && formats.length > 0
  const save = async () => {
    if (!await setSubscription({ cycles, formats })) return
    notify('구독 설정을 저장했습니다. 다음 발행일부터 저장된 관심정보 기준으로 리포트가 생성됩니다.')
    navigate(`/account/reports?tab=${cycles[0]}`)
  }
  return (
    <section className="card">
      <p style={{ fontSize: 13, fontWeight: 700, color: 'var(--red-d)' }}>{subscription.cycles.length ? '구독 중 · 설정 수정' : '최초 미구독 상태'}</p>
      <h2 className="t1" style={{ fontSize: 24, marginTop: 4 }}>구독 설정 / 수정</h2>
      <div style={{ marginTop: 26 }}>
        <div className="between">
          <p style={{ fontSize: 16, fontWeight: 800 }}>발행 주기 <span className="muted" style={{ fontWeight: 500 }}>· 복수 선택 가능</span></p>
          <div style={{ display: 'flex', gap: 8 }}>
            <Btn onClick={() => setCycles(options.report_periods.map(p => p.option_code.toLowerCase()))}>전체선택</Btn>
            <Btn onClick={() => setCycles([])}>전체해제</Btn>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 12, marginTop: 14 }} className="resp-stack">
          {CYCLES.map(([key, title, desc]) => <OptionCard key={key} checked={cycles.includes(key)} onChange={() => options.report_periods.some(p => p.option_code === key.toUpperCase()) ? setCycles(c => toggleValue(c, key)) : notify('이 구독 주기는 운영 설정 준비 중입니다.')} title={title} desc={desc} />)}
        </div>
      </div>
      <div style={{ marginTop: 26 }}>
        <p style={{ fontSize: 16, fontWeight: 800 }}>출력 양식</p>
        <div style={{ display: 'flex', gap: 12, marginTop: 14, maxWidth: 520 }}>
          {FORMATS.map(([key, desc]) => <OptionCard key={key} checked={formats.includes(key)} onChange={() => setFormats(f => toggleValue(f, key))} title={key} desc={desc} />)}
        </div>
      </div>
      <div style={{ marginTop: 24, background: 'var(--bg)', borderRadius: 14, padding: '16px 18px' }}>
        <ul className="note" style={{ margin: 0, paddingLeft: 18 }}>
          <li>구독 리포트는 저장된 관심정보를 기준으로 생성됩니다.</li>
          <li>발행 주기는 복수 선택할 수 있으며, 전체해제 시 선택된 주기가 없습니다.</li>
          <li>처음에는 발행 주기가 선택되어 있지 않습니다. PDF는 기본 선택입니다.</li>
          <li>발행 주기와 출력 양식을 각각 하나 이상 선택한 후 저장하세요.</li>
        </ul>
      </div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 24 }}>
        <Btn size="md" onClick={() => window.history.back()}>취소</Btn>
        <Btn kind="red" size="md" disabled={!valid} onClick={save}>저장</Btn>
      </div>
    </section>
  )
}
