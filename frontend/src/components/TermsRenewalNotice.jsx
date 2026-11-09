// 알림을 켰지만 알림 약관이 바뀌어 다시 동의해야 하는 회원에게 보여 주는 안내. 동의 전에는 새 알림이 오지 않는다.
import { useApp } from '../store'
import { Btn } from './ui'

export default function TermsRenewalNotice({ onAgree, to }) {
  const { settings } = useApp()
  if (!settings.all || !settings.termsRequired) return null
  return (
    <div role="alert" className="between" style={{ flexWrap: 'wrap', gap: 12, marginTop: 14, padding: '14px 18px', borderRadius: 12, border: '1px solid #F1A9B4', background: '#FFF6F7' }}>
      <span style={{ fontSize: 14, lineHeight: 1.6 }}>
        <b>알림 수신 약관이 변경되었습니다.</b> 다시 동의하기 전까지 새 알림이 오지 않습니다.
      </span>
      <Btn kind="red" size="sm" to={to} onClick={onAgree}>다시 동의하기</Btn>
    </div>
  )
}
