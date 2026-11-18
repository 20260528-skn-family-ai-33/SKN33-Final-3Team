// 국기 표시 기준(#132). 두 글자 국가 코드 중 국기 파일이 있는 코드만 국기를 그린다.
const NO_FLAG = new Set(['JG']) // 채널제도: 관세청 전용 코드라 하나로 된 국기가 없다
export const hasFlag = code => /^[A-Z]{2}$/.test(code || '') && !NO_FLAG.has(code)
