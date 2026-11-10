// 메인 보기(전체·내 관심)를 고르고 바꾼다. 시작 화면 기본값은 마이페이지 관심정보의 설정(profile.home)을 따른다.
import { navigate } from '../../router'
import { useApp } from '../../store'

export default function useMainView(route) {
  const { profile, mainView, setMainView, notify } = useApp()
  const hasInterest = profile.products.length > 0 || profile.countries.length > 0
  const requested = route.query.get('view') || mainView || profile.home
  const view = requested === 'interest' && hasInterest ? 'interest' : 'all'
  const switchView = next => {
    if (next === 'interest' && !hasInterest) {
      notify('관심정보가 없어 전체 시장만 볼 수 있습니다. 관심정보를 먼저 등록해 주세요.')
      return
    }
    setMainView(next)
    navigate(next === 'interest' ? '/?view=interest' : '/?view=all', { replace: true })
  }
  return { view, switchView }
}
