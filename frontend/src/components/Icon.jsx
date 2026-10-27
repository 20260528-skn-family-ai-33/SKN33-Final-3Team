// 목업에서 쓰는 선형 아이콘 모음. 모두 24×24 viewBox, stroke 기반이다.
const PATHS = {
  lock: <><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  arrowRight: <path d="M5 12h14M13 6l6 6-6 6" />,
  arrowLeft: <path d="M19 12H5M11 6l-6 6 6 6" />,
  check: <path d="M5 12l5 5 9-10" />,
  bell: <><path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" /><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" /></>,
  chat: <path d="M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z" />,
  file: <><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M9 13h6M9 17h6" /></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" /></>,
  chevronUp: <path d="M6 15l6-6 6 6" />,
  chevronDown: <path d="M6 9l6 6 6-6" />,
  pause: <path d="M9 5v14M15 5v14" />,
  sparkle: <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />,
  star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z" />,
  download: <path d="M12 4v12M6 10l6 6 6-6M4 20h16" />,
  plus: <path d="M12 5v14M5 12h14" />,
  edit: <path d="M4 20h4L19 9l-4-4L4 16z" />,
  trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  alert: <><path d="M12 3 2 20h20z" /><path d="M12 10v4M12 17v.5" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></>,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  external: <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />,
  logout: <path d="M9 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h3M16 17l5-5-5-5M21 12H9" />,
  userX: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 4-6 8-6s8 2 8 6" /><path d="M17 3l4 4M21 3l-4 4" /></>,
}

export default function Icon({ name, size = 16, color = 'currentColor', stroke = 1.9, style, ...rest }) {
  if (name === 'logo') {
    return (
      <svg width="28" height="30" viewBox="0 0 28 30" aria-hidden="true" {...rest}>
        <path d="M14 1 27 8.5v13L14 29 1 21.5v-13z" fill="#15181E" />
        <path d="M9 20V10l10 10V10" fill="none" stroke="#FFFFFF" strokeWidth="2.4" strokeLinejoin="round" />
      </svg>
    )
  }
  if (name === 'flag') {
    return (
      <svg width={size === 16 ? 13 : size} height={size === 16 ? 13 : size} viewBox="0 0 24 24" aria-hidden="true" {...rest}>
        <path d="M5 22V3h13l-3 4.5L18 12H5" fill="#C8102E" />
      </svg>
    )
  }
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ flex: 'none', ...style }} {...rest}
    >
      {PATHS[name]}
    </svg>
  )
}
