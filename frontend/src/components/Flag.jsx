import { hasFlag } from '../utils/flag'

// 국가 코드(KR, US 등 두 글자)로 국기를 그린다(#132). 국기는 장식이므로 스크린리더는 옆의 국가 이름만 읽는다.
// 선택 목록처럼 줄을 맞춰야 하는 곳은 국기가 없는 코드도 같은 크기의 빈 자리로 둔다.
// 표처럼 빈 자리가 어색한 곳은 blank={false}로 국기가 있을 때만 그린다.
export default function Flag({ code, blank = true }) {
  const ok = hasFlag(code)
  if (!ok && !blank) return null
  return <span aria-hidden="true" className={ok ? `flag fi fi-${code.toLowerCase()}` : 'flag'} />
}
