// 입력값 검증 규칙(회원가입·비밀번호 변경·계정 찾기)
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// 비밀번호: 영문 + 숫자 + 특수문자(키보드 기호 !@#$%^&* 등)를 모두 포함해 8자 이상.
// 공백·한글 등 다른 문자는 쓸 수 없다. 서버에서도 같은 규칙으로 다시 검사해야 한다.
const SPECIAL = '!-/:-@\\[-`{-~' // ASCII 기호 범위: ! " # $ % & ' ( ) * + , - . / : ; < = > ? @ [ \ ] ^ _ ` { | } ~
export const PASSWORD_RULES = [
  ['letter', '영문 포함', v => /[A-Za-z]/.test(v)],
  ['digit', '숫자 포함', v => /\d/.test(v)],
  ['special', '특수문자 포함', v => new RegExp(`[${SPECIAL}]`).test(v)],
  ['length', '8자 이상', v => v.length >= 8],
  ['charset', '공백·한글 없이 영문·숫자·특수문자만', v => v.length > 0 && new RegExp(`^[A-Za-z\\d${SPECIAL}]+$`).test(v)],
]
export const PASSWORD_HINT = '영문·숫자·특수문자를 모두 포함해 8자 이상 입력해 주세요.'

export const passwordChecks = v => PASSWORD_RULES.map(([key, label, test]) => ({ key, label, ok: test(v || '') }))
export const isEmail = v => EMAIL_RE.test(v.trim())
export const isStrongPassword = v => passwordChecks(v).every(c => c.ok)
