// 국가 코드(KR, US 등 두 글자)로 국기를 그린다(#132). 국기가 없는 코드는 같은 크기의 빈 자리로 둬 줄이 밀리지 않게 한다.
// 국기는 장식이므로 스크린리더는 옆의 국가 이름만 읽는다.
const NO_FLAG = new Set(['JG']) // 채널제도: 관세청 전용 코드라 하나로 된 국기가 없다
export default function Flag({ code }) {
  const ok = /^[A-Z]{2}$/.test(code || '') && !NO_FLAG.has(code)
  return <span aria-hidden="true" className={ok ? `flag fi fi-${code.toLowerCase()}` : 'flag'} />
}
