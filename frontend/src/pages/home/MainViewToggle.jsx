// 메인 전체↔내 관심 보기 전환. 메인 화면 머리글의 알림 버튼 왼쪽에 둔다.
// 시작 화면 기본값은 마이페이지 관심정보의 설정(profile.home)을 그대로 따른다.
import useMainView from './useMainView'

export default function MainViewToggle({ route }) {
  const { view, switchView } = useMainView(route)
  const interest = view === 'interest'
  return (
    <div className="seg hd-view" role="group" aria-label="메인 보기 전환">
      <button type="button" className={'reset seg-btn' + (!interest ? ' on' : '')} aria-pressed={!interest} onClick={() => switchView('all')}>전체</button>
      <button type="button" className={'reset seg-btn' + (interest ? ' on' : '')} aria-pressed={interest} onClick={() => switchView('interest')}>내 관심</button>
    </div>
  )
}
