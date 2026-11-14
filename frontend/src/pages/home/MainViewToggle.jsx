// 메인 전체↔내 관심 보기 전환. 메인 화면 머리글의 알림 버튼 왼쪽에 둔다.
// 시작 화면 기본값은 마이페이지 관심정보의 설정(profile.home)을 그대로 따른다.
import useMainView from './useMainView'

export default function MainViewToggle({ route }) {
  const { view, switchView } = useMainView(route)
  const interest = view === 'interest'
  // 스위치처럼 동작한다: 어느 쪽을 누르든 지금 보기의 반대쪽으로 바뀐다.
  const toggle = () => switchView(interest ? 'all' : 'interest')
  return (
    <div className="seg hd-view" role="group" aria-label="메인 보기 전환">
      <button type="button" className={'reset seg-btn' + (!interest ? ' on' : '')} aria-pressed={!interest} onClick={toggle}>전체</button>
      <button type="button" className={'reset seg-btn' + (interest ? ' on' : '')} aria-pressed={interest} onClick={toggle}>내 관심</button>
    </div>
  )
}
