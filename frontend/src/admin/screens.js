// 관리자 화면 데이터(화면설계서 BS32~BS166, 키워드·분류 체계 BS89~BS96 제외).
// 화면 목업(2026.10.22 전체 반영본)의 관리자 영역을 그대로 옮긴 선언형 데이터이며 AdminScreen.jsx가 그린다.
// 서버 연동 시 각 화면의 표·카드 값만 API 응답으로 채우면 된다.
// 블록 type: tabs · table · kv · buttons · note · para · heading · banner · label · step · timeline · log · card · skeleton · memberSearch · ops
const inboxTabs = active => [
  { label: '관리자 알림', to: '/admin/inbox/BS33', on: active === 'alerts' },
  { label: '데이터 수집 이력', to: '/admin/inbox/collection', on: active === 'collection' },
  { label: '회원 알림 전달 내역', to: '/admin/inbox/BS149', on: active === 'delivery' },
]

export const adminScreens = {
  collection: {
    menu: 'inbox',
    lead: '데이터 수집 성공·실패 이력을 확인하고 실패한 작업을 다시 시도합니다.',
    head: {
      crumb: '관리자 / 알림함',
      title: '데이터 수집 이력',
      sub: '실행별 결과와 실패 원인을 확인하고 필요한 작업을 재수집합니다.',
      tabs: inboxTabs('collection'),
      actions: [{ label: '수집 현황 보기', to: '/admin/inbox/BS64' }],
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '실패한 작업은 상세와 실행 로그를 확인한 뒤 재시도할 수 있습니다.' },
      {
        type: 'table',
        cols: '1.5fr 1.3fr 1fr 0.6fr 1.2fr',
        head: ['수집 작업 / 실행 ID', '실행 시각', '대상 기간', '결과', '관리'],
        rows: [
          { cells: ['KOTRA 통합 기사\n#RUN-1021-02', '2026.10.21 09:00', '2026.10.21', { tag: 'ok', text: '성공' }, { btns: [{ label: '상세', to: '/admin/inbox/BS66' }] }] },
          { cells: ['관세청 수출 통계\n#RUN-1021-01', '2026.10.21 08:00', '2026.09', { tag: 'red', text: '실패' }, { btns: [{ label: '상세', to: '/admin/inbox/BS65' }, { label: '재시도', to: '/admin/inbox/BS69', kind: 'red' }] }] },
          { cells: ['ECOS 환율\n#RUN-1020-03', '2026.10.20 17:00', '2026.10.20', { tag: 'ok', text: '성공' }, { btns: [{ label: '상세', to: '/admin/inbox/BS67' }] }] },
        ],
      },
      { type: 'note', text: '표시 3건 · 최근 실행순 · 시연 수집 이력' },
    ],
  },

  BS32: {
    menu: 'ops',
    lead: '2026년 10월 21일 · 현재 운영 상태와 관리자 알림을 확인합니다.',
    head: { crumb: '관리자 / 운영 현황', title: '운영 현황', sub: '2026년 10월 21일 · 현재 운영 상태와 관리자 알림을 확인합니다.' },
    blocks: [{ type: 'ops', tab: '전체' }]
  },
  BS33: {
    menu: 'inbox',
    lead: '유형별 운영 알림을 확인하고 해당 업무의 상세 화면으로 이동합니다.',
    head: {
      crumb: '관리자 / 알림함',
      title: '관리자 알림',
      sub: '유형별 운영 알림을 확인하고 해당 업무의 상세 화면으로 이동합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/ops/BS32' }],
      tabs: inboxTabs('alerts')
    },
    blocks: [
      { type: 'tabs', sm: true, items: [
        { label: '전체', to: '/admin/inbox/BS33', on: true },
        { label: '데이터', to: '/admin/inbox/BS35' },
        { label: '리포트', to: '/admin/inbox/BS37' },
        { label: '회원관리', to: '/admin/inbox/BS39' }
      ] },
      {
        type: 'table',
        cols: '0.8fr 0.9fr 3fr 1fr 1fr',
        head: ['우선순위', '분류', '알림 내용', '발생 시각', '관리'],
        rows: [
          {
            cells: [
              { tag: 'red', text: '높음' },
              '데이터',
              '9월 수출 통계 일부 국가 응답 누락',
              '08:12',
              { btns: [{ label: '상세', to: '/admin/inbox/BS65' }] }
            ]
          },
          {
            cells: [
              { tag: 'red', text: '높음' },
              '리포트',
              '월간 리포트 PDF 생성 실패',
              '08:35',
              { btns: [{ label: '상세', to: '/admin/inbox/BS150' }] }
            ]
          },
          {
            cells: [
              { tag: 'amber', text: '보통' },
              '회원관리',
              '탈퇴 회원의 1개월 보존기간 만료 예정',
              '09:00',
              { btns: [{ label: '상세', to: '/admin/members/BS44' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '현재 탭 3건 · 상세 버튼에서 관련 업무를 확인합니다.' }
    ]
  },
  BS34: {
    menu: 'ops',
    lead: '2026년 10월 21일 · 현재 운영 상태와 관리자 알림을 확인합니다.',
    head: { crumb: '관리자 / 운영 현황', title: '운영 현황', sub: '2026년 10월 21일 · 현재 운영 상태와 관리자 알림을 확인합니다.' },
    blocks: [{ type: 'ops', tab: '데이터' }]
  },
  BS35: {
    menu: 'inbox',
    lead: '데이터 알림 · 운영 현황에서 선택한 탭을 유지합니다.',
    head: {
      crumb: '관리자 / 알림함',
      title: '관리자 알림',
      sub: '데이터 알림 · 운영 현황에서 선택한 탭을 유지합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/ops/BS34' }],
      tabs: inboxTabs('alerts')
    },
    blocks: [
      { type: 'tabs', sm: true, items: [
        { label: '전체', to: '/admin/inbox/BS33' },
        { label: '데이터', to: '/admin/inbox/BS35', on: true },
        { label: '리포트', to: '/admin/inbox/BS37' },
        { label: '회원관리', to: '/admin/inbox/BS39' }
      ] },
      {
        type: 'table',
        cols: '0.8fr 0.9fr 3fr 1fr 1fr',
        head: ['우선순위', '분류', '알림 내용', '발생 시각', '관리'],
        rows: [
          {
            cells: [
              { tag: 'red', text: '높음' },
              '데이터',
              '9월 수출 통계 일부 국가 응답 누락',
              '08:12',
              { btns: [{ label: '상세', to: '/admin/inbox/BS65' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '현재 탭 1건 · 상세 버튼에서 관련 업무를 확인합니다.' }
    ]
  },
  BS36: {
    menu: 'ops',
    lead: '2026년 10월 21일 · 현재 운영 상태와 관리자 알림을 확인합니다.',
    head: { crumb: '관리자 / 운영 현황', title: '운영 현황', sub: '2026년 10월 21일 · 현재 운영 상태와 관리자 알림을 확인합니다.' },
    blocks: [{ type: 'ops', tab: '리포트' }]
  },
  BS37: {
    menu: 'inbox',
    lead: '리포트 알림 · 운영 현황에서 선택한 탭을 유지합니다.',
    head: {
      crumb: '관리자 / 알림함',
      title: '관리자 알림',
      sub: '리포트 알림 · 운영 현황에서 선택한 탭을 유지합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/ops/BS36' }],
      tabs: inboxTabs('alerts')
    },
    blocks: [
      { type: 'tabs', sm: true, items: [
        { label: '전체', to: '/admin/inbox/BS33' },
        { label: '데이터', to: '/admin/inbox/BS35' },
        { label: '리포트', to: '/admin/inbox/BS37', on: true },
        { label: '회원관리', to: '/admin/inbox/BS39' }
      ] },
      {
        type: 'table',
        cols: '0.8fr 0.9fr 3fr 1fr 1fr',
        head: ['우선순위', '분류', '알림 내용', '발생 시각', '관리'],
        rows: [
          {
            cells: [
              { tag: 'red', text: '높음' },
              '리포트',
              '월간 리포트 PDF 생성 실패',
              '08:35',
              { btns: [{ label: '상세', to: '/admin/inbox/BS150' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '현재 탭 1건 · 상세 버튼에서 관련 업무를 확인합니다.' }
    ]
  },
  BS38: {
    menu: 'ops',
    lead: '2026년 10월 21일 · 현재 운영 상태와 관리자 알림을 확인합니다.',
    head: { crumb: '관리자 / 운영 현황', title: '운영 현황', sub: '2026년 10월 21일 · 현재 운영 상태와 관리자 알림을 확인합니다.' },
    blocks: [{ type: 'ops', tab: '회원관리' }]
  },
  BS39: {
    menu: 'inbox',
    lead: '회원관리 알림 · 운영 현황에서 선택한 탭을 유지합니다.',
    head: {
      crumb: '관리자 / 알림함',
      title: '관리자 알림',
      sub: '회원관리 알림 · 운영 현황에서 선택한 탭을 유지합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/ops/BS38' }],
      tabs: inboxTabs('alerts')
    },
    blocks: [
      { type: 'tabs', sm: true, items: [
        { label: '전체', to: '/admin/inbox/BS33' },
        { label: '데이터', to: '/admin/inbox/BS35' },
        { label: '리포트', to: '/admin/inbox/BS37' },
        { label: '회원관리', to: '/admin/inbox/BS39', on: true }
      ] },
      {
        type: 'table',
        cols: '0.8fr 0.9fr 3fr 1fr 1fr',
        head: ['우선순위', '분류', '알림 내용', '발생 시각', '관리'],
        rows: [
          {
            cells: [
              { tag: 'amber', text: '보통' },
              '회원관리',
              '탈퇴 회원의 1개월 보존기간 만료 예정',
              '09:00',
              { btns: [{ label: '상세', to: '/admin/members/BS44' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '현재 탭 1건 · 상세 버튼에서 관련 업무를 확인합니다.' }
    ]
  },
  BS40: {
    menu: 'members',
    lead: '회원의 이용 상태와 운영 권한을 관리합니다.',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원·접근 권한',
      sub: '회원의 이용 상태와 운영 권한을 관리합니다.',
      tabs: [
        { label: '전체', to: '/admin/members/BS40', on: true },
        { label: '정상', to: '/admin/members/BS41' },
        { label: '탈퇴', to: '/admin/members/BS42' },
        { label: '이용 제한', to: '/admin/members/BS43' },
      ]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '탈퇴: 탈퇴일로부터 1개월 보존 후 완전 삭제 · 이용 제한: 영구 보존' },
      { type: 'memberSearch', value: '', to: '/admin/members/BS49' },
      {
        type: 'table',
        cols: '1.9fr 0.8fr 1.1fr 0.9fr 0.9fr 0.9fr 0.8fr',
        head: ['회원', '이름', '권한', '로그인 수단', '계정 상태', '활성 구독', '관리'],
        rows: [
          {
            cells: [
              'member01@example.com',
              '김민수',
              '일반 회원',
              '카카오',
              { tag: 'ok', text: '정상' },
              '월간',
              { btns: [{ label: '상세', to: '/admin/members/BS50' }] }
            ]
          },
          {
            cells: [
              'editor@example.com',
              '이서연',
              '관리자(콘텐츠)',
              '이메일',
              { tag: 'ok', text: '정상' },
              '없음',
              { btns: [{ label: '상세', to: '/admin/members/BS51' }] }
            ]
          },
          {
            cells: [
              'reviewer@example.com',
              '박지훈',
              '일반 회원',
              '네이버',
              { tag: 'ok', text: '정상' },
              '분기',
              { btns: [{ label: '상세', to: '/admin/members/BS54' }] }
            ]
          },
          {
            cells: [
              'withdrawn@example.com',
              '최유진',
              '일반 회원',
              '구글',
              { tag: 'gray', text: '탈퇴' },
              '중지',
              { btns: [{ label: '상세', to: '/admin/members/BS44' }] }
            ]
          },
          {
            cells: [
              'blocked@example.com',
              '정하늘',
              '일반 회원',
              '구글',
              { tag: 'red', text: '이용 제한' },
              '중지',
              { btns: [{ label: '상세', to: '/admin/members/BS46' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '표시 5건 · 시연 목록 전체' },
      { type: 'buttons', items: [{ label: '로그인 화면 미리보기', to: '/login' }] }
    ]
  },
  BS41: {
    menu: 'members',
    lead: '정상 회원 목록',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원·접근 권한',
      sub: '정상 회원 목록',
      tabs: [
        { label: '전체', to: '/admin/members/BS40' },
        { label: '정상', to: '/admin/members/BS41', on: true },
        { label: '탈퇴', to: '/admin/members/BS42' },
        { label: '이용 제한', to: '/admin/members/BS43' },
      ]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '탈퇴: 탈퇴일로부터 1개월 보존 후 완전 삭제 · 이용 제한: 영구 보존' },
      { type: 'memberSearch', value: '', to: '/admin/members/BS49' },
      {
        type: 'table',
        cols: '1.9fr 0.8fr 1.1fr 0.9fr 0.9fr 0.9fr 0.8fr',
        head: ['회원', '이름', '권한', '로그인 수단', '계정 상태', '활성 구독', '관리'],
        rows: [
          {
            cells: [
              'member01@example.com',
              '김민수',
              '일반 회원',
              '카카오',
              { tag: 'ok', text: '정상' },
              '월간',
              { btns: [{ label: '상세', to: '/admin/members/BS50' }] }
            ]
          },
          {
            cells: [
              'editor@example.com',
              '이서연',
              '관리자(콘텐츠)',
              '이메일',
              { tag: 'ok', text: '정상' },
              '없음',
              { btns: [{ label: '상세', to: '/admin/members/BS51' }] }
            ]
          },
          {
            cells: [
              'reviewer@example.com',
              '박지훈',
              '일반 회원',
              '네이버',
              { tag: 'ok', text: '정상' },
              '분기',
              { btns: [{ label: '상세', to: '/admin/members/BS54' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '표시 3건 · 시연 목록 전체' },
      { type: 'buttons', items: [{ label: '로그인 화면 미리보기', to: '/login' }] }
    ]
  },
  BS42: {
    menu: 'members',
    lead: '탈퇴 회원 목록',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원·접근 권한',
      sub: '탈퇴 회원 목록',
      tabs: [
        { label: '전체', to: '/admin/members/BS40' },
        { label: '정상', to: '/admin/members/BS41' },
        { label: '탈퇴', to: '/admin/members/BS42', on: true },
        { label: '이용 제한', to: '/admin/members/BS43' },
      ]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '탈퇴: 탈퇴일로부터 1개월 보존 후 완전 삭제 · 이용 제한: 영구 보존' },
      { type: 'memberSearch', value: '', to: '/admin/members/BS49' },
      {
        type: 'table',
        cols: '1.9fr 0.8fr 1.1fr 0.9fr 0.9fr 0.9fr 0.8fr',
        head: ['회원', '이름', '권한', '로그인 수단', '계정 상태', '활성 구독', '관리'],
        rows: [
          {
            cells: [
              'withdrawn@example.com',
              '최유진',
              '일반 회원',
              '구글',
              { tag: 'gray', text: '탈퇴' },
              '중지',
              { btns: [{ label: '상세', to: '/admin/members/BS44' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '표시 1건 · 시연 목록 전체' },
      { type: 'buttons', items: [{ label: '로그인 화면 미리보기', to: '/login' }] }
    ]
  },
  BS43: {
    menu: 'members',
    lead: '이용 제한 회원 목록',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원·접근 권한',
      sub: '이용 제한 회원 목록',
      tabs: [
        { label: '전체', to: '/admin/members/BS40' },
        { label: '정상', to: '/admin/members/BS41' },
        { label: '탈퇴', to: '/admin/members/BS42' },
        { label: '이용 제한', to: '/admin/members/BS43', on: true },
      ]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '탈퇴: 탈퇴일로부터 1개월 보존 후 완전 삭제 · 이용 제한: 영구 보존' },
      { type: 'memberSearch', value: '', to: '/admin/members/BS49' },
      {
        type: 'table',
        cols: '1.9fr 0.8fr 1.1fr 0.9fr 0.9fr 0.9fr 0.8fr',
        head: ['회원', '이름', '권한', '로그인 수단', '계정 상태', '활성 구독', '관리'],
        rows: [
          {
            cells: [
              'blocked@example.com',
              '정하늘',
              '일반 회원',
              '구글',
              { tag: 'red', text: '이용 제한' },
              '중지',
              { btns: [{ label: '상세', to: '/admin/members/BS46' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '표시 1건 · 시연 목록 전체' },
      { type: 'buttons', items: [{ label: '로그인 화면 미리보기', to: '/login' }] }
    ]
  },
  BS44: {
    menu: 'members',
    lead: '탈퇴일로부터 1개월 보존 후 자동으로 완전 삭제합니다.',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '탈퇴 회원 상세',
      sub: '탈퇴일로부터 1개월 보존 후 자동으로 완전 삭제합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS42' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '회원', v: 'withdrawn@example.com' },
          { k: '계정 상태', v: '탈퇴' },
          { k: '탈퇴일', v: '2026.10.01' },
          { k: '완전 삭제 예정일', v: '2026.11.01' },
          { k: '보존 상태', v: '1개월 보존 기간 내' },
          { k: '삭제 이후', v: '회원 목록·검색 및 개인 데이터에서 제거' },
          { k: '로그인 수단', v: '구글' },
          { k: '최근 로그인', v: '2026.09.29 10:00' }
        ]
      },
      { type: 'note', text: '사용자 지정 보존 정책을 반영한 화면입니다. 실제 삭제 작업을 실행하지 않습니다.' },
      { type: 'buttons', items: [{ label: '기한 경과 후 화면', to: '/admin/members/BS45' }] }
    ]
  },
  BS45: {
    menu: 'members',
    lead: '탈퇴 회원 목록',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원·접근 권한',
      sub: '탈퇴 회원 목록',
      tabs: [
        { label: '전체', to: '/admin/members/BS40' },
        { label: '정상', to: '/admin/members/BS41' },
        { label: '탈퇴', to: '/admin/members/BS42', on: true },
        { label: '이용 제한', to: '/admin/members/BS43' },
      ]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '보존기간 만료 회원 삭제 완료 · 해당 회원은 목록·검색 결과에서 제거되었습니다.' },
      { type: 'memberSearch', value: '', to: '/admin/members/BS49' },
      {
        type: 'table',
        cols: '1.9fr 0.8fr 1.1fr 0.9fr 0.9fr 0.9fr 0.8fr',
        head: ['회원', '이름', '권한', '로그인 수단', '계정 상태', '활성 구독', '관리'],
        rows: []
      },
      { type: 'note', text: '표시 0건 · 시연 목록 전체' },
      { type: 'buttons', items: [{ label: '로그인 화면 미리보기', to: '/login' }] },
      {
        type: 'banner',
        tone: 'ok',
        text: '삭제 완료 · 회원의 이메일과 관심 정보를 남기지 않습니다. · 탈퇴 후 1개월이 지나 회원과 연결된 개인 데이터를 완전 삭제했습니다. / 회원 목록과 검색 결과에서도 제외됩니다.'
      }
    ]
  },
  BS46: {
    menu: 'members',
    lead: '이용 제한 회원은 보존 기한 없이 영구 보존합니다.',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '이용 제한 회원 상세',
      sub: '이용 제한 회원은 보존 기한 없이 영구 보존합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS43' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '회원', v: 'blocked@example.com' },
          { k: '계정 상태', v: '이용 제한' },
          { k: '제한 시작일', v: '2026.09.30' },
          { k: '보존 기간', v: '영구 보존' },
          { k: '자동 삭제 예정일', v: '없음' },
          { k: '보존 대상', v: '회원 정보 · 관심사 · 기존 결과 및 처리 이력' },
          { k: '로그인 수단', v: '구글' },
          { k: '최근 로그인', v: '2026.09.29 10:00' }
        ]
      },
      { type: 'buttons', items: [{ label: '이용 제한 해제', to: '/admin/members/BS47', kind: 'red' }] }
    ]
  },
  BS47: {
    menu: 'members',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 회원·권한', title: '이용 제한을 해제할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS46' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/members/BS46',
      title: '이용 제한을 해제할까요?',
      lines: ['대상: blocked@example.com', '계정 상태를 정상으로 변경합니다.', '기존 구독은 다음 일정부터 재개하고 중단 기간을 자동 소급하지 않습니다.'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/members/BS46' }, { label: '제한 해제', to: '/admin/members/BS48', kind: 'red' }]
    }
  },
  BS48: {
    menu: 'members',
    lead: 'blocked@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 상세',
      sub: 'blocked@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS43' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '회원', v: 'blocked@example.com' },
          { k: '계정 상태', v: '정상' },
          { k: '관심 정보', v: '기존 등록 정보 유지' },
          { k: '구독', v: '다음 일정부터 재개' },
          { k: '처리 결과', v: '이용 제한 해제' },
          { k: '처리 이력', v: '대상·사유·처리자·시각 기록' },
          { k: '로그인 수단', v: '구글' },
          { k: '최근 로그인', v: '2026.09.29 10:00' }
        ]
      },
      { type: 'buttons', items: [{ label: '회원 목록', to: '/admin/members/BS40', kind: 'red' }] }
    ]
  },
  BS49: {
    menu: 'members',
    lead: '검색 조건: member01@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 검색 결과',
      sub: '검색 조건: member01@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }],
      tabs: [{ label: '전체', to: '/admin/members/BS40' }, { label: '정상', to: '/admin/members/BS41' }, { label: '탈퇴', to: '/admin/members/BS42' }, { label: '이용 제한', to: '/admin/members/BS43' }]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '입력·검색은 고정된 예시 결과로 보여줍니다.' },
      { type: 'memberSearch', value: 'member01@example.com', to: '/admin/members/BS49' },
      {
        type: 'table',
        cols: '1.9fr 0.8fr 1.1fr 0.9fr 0.9fr 0.9fr 0.8fr',
        head: ['회원', '이름', '권한', '로그인 수단', '계정 상태', '활성 구독', '관리'],
        rows: [
          {
            cells: [
              'member01@example.com',
              '김민수',
              '일반 회원',
              '카카오',
              { tag: 'ok', text: '정상' },
              '월간',
              { btns: [{ label: '상세', to: '/admin/members/BS50' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '표시 1건 · 시연 목록 전체' },
      { type: 'buttons', items: [{ label: '로그인 화면 미리보기', to: '/login' }] }
    ]
  },
  BS50: {
    menu: 'members',
    lead: 'member01@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 상세',
      sub: 'member01@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }],
      tabs: [{ label: '기본 정보', to: '/admin/members/BS50', on: true }, { label: '관심사·맞춤 서비스', to: '/admin/members/BS63' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '계정 상태', v: '정상' },
          { k: '권한', v: '일반 회원' },
          { k: '시작 화면', v: '내 관심 시장' },
          { k: '구독', v: '월간 리포트 활성' },
          { k: '관심 품목 전체', v: '854232 메모리 · HS2022 \n 330499 기타 화장품 · HS2022' },
          { k: '관심 국가 전체', v: '미국 \n 베트남' },
          { k: '알림', v: '전체 수신 ON · 현재 약관 동의' },
          { k: '로그인 수단 / 최근 로그인', v: '카카오 / 2026.10.22 09:12' }
        ]
      },
      {
        type: 'buttons',
        items: [
          { label: '권한 변경', to: '/admin/members/BS55', kind: 'red' },
          { label: '이용 제한', to: '/admin/members/BS58', kind: 'danger' },
          { label: '수신 설정 보기', to: '/admin/inbox/BS153' }
        ]
      },
      { type: 'note', text: '관심 품목과 국가는 개수로 축약하지 않고 등록된 항목 전체를 표시합니다.' }
    ]
  },
  BS51: {
    menu: 'members',
    lead: 'editor@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '운영자 상세',
      sub: 'editor@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '계정 상태', v: '정상' },
          { k: '현재 권한', v: '관리자(콘텐츠)' },
          { k: '허용 메뉴', v: '기사 · 키워드 분류' },
          { k: '권한 관리', v: '최고 관리자가 부여·회수' },
          { k: '회원 정보', v: '업무에 필요한 최소 범위 조회' },
          { k: '변경 기록', v: '최근 변경 없음' },
          { k: '로그인 수단', v: '이메일' },
          { k: '최근 로그인', v: '2026.10.22 09:00' }
        ]
      },
      { type: 'buttons', items: [{ label: '권한 회수', to: '/admin/members/BS52', kind: 'danger' }] }
    ]
  },
  BS52: {
    menu: 'members',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 회원·권한', title: '운영 권한을 회수할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS51' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/members/BS51',
      title: '운영 권한을 회수할까요?',
      danger: true,
      lines: ['editor@example.com의 콘텐츠 관리자 권한을 회수합니다.', '계정은 일반 회원으로 유지되며, 관리자 메뉴 접근은 종료됩니다.'],
      inputs: [],
      notes: ['확인 버튼은 처리 결과 예시로 이동하며 실제 계정·구독은 변경하지 않습니다.'],
      textarea: '처리 사유',
      buttons: [{ label: '취소', to: '/admin/members/BS51' }, { label: '권한 회수', to: '/admin/members/BS53', kind: 'danger' }]
    }
  },
  BS53: {
    menu: 'members',
    lead: 'editor@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '운영자 상세',
      sub: 'editor@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '계정 상태', v: '정상' },
          { k: '현재 권한', v: '일반 회원' },
          { k: '허용 메뉴', v: '관리자 접근 불가' },
          { k: '권한 관리', v: '최고 관리자가 부여·회수' },
          { k: '회원 정보', v: '업무에 필요한 최소 범위 조회' },
          { k: '변경 기록', v: '최근 변경 없음' },
          { k: '로그인 수단', v: '이메일' },
          { k: '최근 로그인', v: '2026.10.22 09:00' }
        ]
      },
      {
        type: 'buttons',
        items: [{ label: '회원 목록', to: '/admin/members/BS40', kind: 'red' }, { label: '처리 이력', to: '/admin/history/BS156' }]
      },
      {
        type: 'banner',
        tone: 'ok',
        text: '운영 권한을 회수했습니다 · editor@example.com · 일반 회원 / 회수 시각: 2026.10.21 10:30 · 처리자: 운영 관리자'
      }
    ]
  },
  BS54: {
    menu: 'members',
    lead: 'reviewer@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 상세',
      sub: 'reviewer@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '계정 상태', v: '정상' },
          { k: '권한', v: '일반 회원' },
          { k: '관심 정보', v: '메모리 · 미국' },
          { k: '구독', v: '분기 리포트 활성' },
          { k: '시작 화면', v: '전체 시장' },
          { k: '알림', v: '전체 수신 OFF' },
          { k: '로그인 수단', v: '네이버' },
          { k: '최근 로그인', v: '2026.10.22 09:00' }
        ]
      }
    ]
  },
  BS55: {
    menu: 'members',
    lead: '선택한 역할과 접근 가능한 범위를 확인하세요.',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 권한 변경',
      sub: '선택한 역할과 접근 가능한 범위를 확인하세요.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS50' }]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '작성 예시 · 최고 관리자만 권한을 변경할 수 있습니다.' },
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '대상 회원', v: 'member01@example.com' },
          { k: '현재 권한', v: '일반 회원' },
          { k: '변경할 권한', v: '관리자(콘텐츠)' },
          { k: '허용 범위', v: '기사 검수 · 키워드 분류' },
          { k: '제외 범위', v: '회원 권한 변경 · 공통 운영 설정' },
          { k: '변경 사유', v: '기사 분류 검수 담당 지정' }
        ]
      },
      {
        type: 'buttons',
        items: [{ label: '변경 내용 확인', to: '/admin/members/BS56', kind: 'red' }, { label: '취소', to: '/admin/members/BS50' }]
      }
    ]
  },
  BS56: {
    menu: 'members',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 회원·권한', title: '콘텐츠 관리자 권한을 부여할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS55' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/members/BS55',
      title: '콘텐츠 관리자 권한을 부여할까요?',
      lines: ['일반 회원 → 관리자(콘텐츠)', '기사와 키워드 분류 관리 메뉴에 접근할 수 있습니다.', '사유와 변경 전후 권한이 처리 이력에 남습니다.'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/members/BS55' }, { label: '권한 부여', to: '/admin/members/BS57', kind: 'red' }]
    }
  },
  BS57: {
    menu: 'members',
    lead: 'member01@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 상세',
      sub: 'member01@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '계정 상태', v: '정상' },
          { k: '권한', v: '관리자(콘텐츠)' },
          { k: '시작 화면', v: '내 관심 시장' },
          { k: '구독', v: '월간 리포트 활성' },
          { k: '관심 품목 전체', v: '854232 메모리 · HS2022 \n 330499 기타 화장품 · HS2022' },
          { k: '관심 국가 전체', v: '미국 \n 베트남' },
          { k: '알림', v: '전체 수신 ON · 현재 약관 동의' },
          { k: '로그인 수단 / 최근 로그인', v: '카카오 / 2026.10.22 09:12' }
        ]
      },
      {
        type: 'buttons',
        items: [
          { label: '권한 변경', to: '/admin/members/BS55', kind: 'red' },
          { label: '이용 제한', to: '/admin/members/BS58', kind: 'danger' },
          { label: '수신 설정 보기', to: '/admin/inbox/BS153' },
          { label: '변경 이력', to: '/admin/history/BS156' }
        ]
      },
      { type: 'note', text: '관심 품목과 국가는 개수로 축약하지 않고 등록된 항목 전체를 표시합니다.' },
      { type: 'banner', tone: 'ok', text: '권한 변경을 완료했습니다 · member01@example.com · 관리자(콘텐츠) / 기사·키워드 분류 메뉴 사용 가능' }
    ]
  },
  BS58: {
    menu: 'members',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 회원·권한', title: '회원 이용을 제한할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS50' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/members/BS50',
      title: '회원 이용을 제한할까요?',
      danger: true,
      lines: ['대상: member01@example.com', '로그인·신규 분석·정기 발행·신규 알림을 중단하고, 회원 정보는 영구 보존합니다.'],
      inputs: [],
      notes: ['확인 버튼은 처리 결과 예시로 이동하며 실제 계정·구독은 변경하지 않습니다.'],
      textarea: '처리 사유',
      buttons: [{ label: '취소', to: '/admin/members/BS50' }, { label: '이용 제한', to: '/admin/members/BS59', kind: 'danger' }]
    }
  },
  BS59: {
    menu: 'members',
    lead: 'member01@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 상세',
      sub: 'member01@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '계정 상태', v: '이용 제한 · 영구 보존' },
          { k: '권한', v: '일반 회원' },
          { k: '시작 화면', v: '내 관심 시장' },
          { k: '구독', v: '제한 중 신규 발행 중단' },
          { k: '관심 품목 전체', v: '854232 메모리 · HS2022 \n 330499 기타 화장품 · HS2022' },
          { k: '관심 국가 전체', v: '미국 \n 베트남' },
          { k: '알림', v: '신규 알림 중단 · 기존 결과 보존' },
          { k: '로그인 수단 / 최근 로그인', v: '카카오 / 2026.10.22 09:12' }
        ]
      },
      {
        type: 'buttons',
        items: [{ label: '이용 제한 해제', to: '/admin/members/BS60', kind: 'red' }, { label: '처리 이력', to: '/admin/history/BS156' }]
      },
      { type: 'note', text: '관심 품목과 국가는 개수로 축약하지 않고 등록된 항목 전체를 표시합니다.' },
      { type: 'banner', tone: 'ok', text: '회원 이용을 제한했습니다 · 계정 상태: 이용 제한 · 영구 보존 / 자동 삭제 기한 없이 회원 정보와 기존 결과를 보존합니다.' }
    ]
  },
  BS60: {
    menu: 'members',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 회원·권한', title: '회원 이용 제한을 해제할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS59' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/members/BS59',
      title: '회원 이용 제한을 해제할까요?',
      lines: ['계정 상태를 정상으로 변경합니다.', '기존 구독 일정부터 발행을 재개하며, 중단 기간은 자동 소급 발행하지 않습니다.', '처리 사유: 이용 정책 재안내 완료'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/members/BS59' }, { label: '제한 해제', to: '/admin/members/BS61', kind: 'red' }]
    }
  },
  BS61: {
    menu: 'members',
    lead: 'member01@example.com',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 상세',
      sub: 'member01@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '계정 상태', v: '정상' },
          { k: '권한', v: '일반 회원' },
          { k: '시작 화면', v: '내 관심 시장' },
          { k: '구독', v: '월간 리포트 활성' },
          { k: '관심 품목 전체', v: '854232 메모리 · HS2022 \n 330499 기타 화장품 · HS2022' },
          { k: '관심 국가 전체', v: '미국 \n 베트남' },
          { k: '알림', v: '전체 수신 ON · 현재 약관 동의' },
          { k: '로그인 수단 / 최근 로그인', v: '카카오 / 2026.10.22 09:12' }
        ]
      },
      {
        type: 'buttons',
        items: [
          { label: '권한 변경', to: '/admin/members/BS55', kind: 'red' },
          { label: '이용 제한', to: '/admin/members/BS58', kind: 'danger' },
          { label: '수신 설정 보기', to: '/admin/inbox/BS153' }
        ]
      },
      { type: 'note', text: '관심 품목과 국가는 개수로 축약하지 않고 등록된 항목 전체를 표시합니다.' },
      { type: 'banner', tone: 'ok', text: '이용 제한을 해제했습니다 · member01@example.com · 정상 / 기존 관심 정보와 구독 설정을 유지합니다.' }
    ]
  },
  BS63: {
    menu: 'members',
    lead: 'member01@example.com · 관심 조건과 노출 결과 비교',
    head: {
      crumb: '관리자 / 회원·권한',
      title: '회원 상세',
      sub: 'member01@example.com · 관심 조건과 노출 결과 비교',
      actions: [{ label: '목록 / 뒤로', to: '/admin/members/BS40' }],
      tabs: [{ label: '기본 정보', to: '/admin/members/BS50' }, { label: '관심사·맞춤 서비스', to: '/admin/members/BS63', on: true }]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '회원의 관심사를 임의로 변경하지 않고, 저장된 조건과 연결 결과를 점검합니다.' },
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '관심 품목', v: '854232 메모리 · 330499 기타 화장품' },
          { k: '관심 국가', v: '미국 · 베트남' },
          { k: '기사 노출 예시', v: '반도체 수출 정책 · 베트남 화장품 인증' }
        ]
      },
      { type: 'note', text: '직접 선택한 관심 국가에 관심 HS6의 최근 12개월 수출 상위 5개 연결 국가를 포함합니다.' },
      { type: 'note', text: '연결 국가는 관심 변경·새 월 통계 공개 때 다시 계산합니다. 개인별 범위 선택과 관리자 편집은 제공하지 않습니다.' },
      { type: 'buttons', items: [{ label: '노출 기사 확인', to: '/admin/data/BS77' }] }
    ]
  },
  BS64: {
    menu: 'inbox',
    lead: '출처별 최신 데이터와 실행 상태를 확인합니다.',
    head: {
      crumb: '관리자 / 알림함 / 데이터 수집 현황',
      title: '데이터 수집 현황',
      sub: '출처별 최신 데이터와 실행 상태를 확인합니다.',
      actions: [{ label: '수집 이력 보기', to: '/admin/inbox/collection' }],
      tabs: inboxTabs('collection')
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '실패한 기간은 정상 공개 기간으로 표시하지 않습니다.' },
      {
        type: 'table',
        cols: '1.6fr 1fr 1fr 0.8fr 0.8fr',
        head: ['작업', '대상 기간', '최근 실행', '상태', '관리'],
        rows: [
          {
            cells: ['관세청 수출 통계', '2026.09', '오늘 08:12', { tag: 'red', text: '실패' }, { btns: [{ label: '상세', to: '/admin/inbox/BS65' }] }]
          },
          {
            cells: [
              'KOTRA 통합 기사',
              '2026.10.21',
              '오늘 09:00',
              { tag: 'ok', text: '완료' },
              { btns: [{ label: '상세', to: '/admin/inbox/BS66' }] }
            ]
          },
          {
            cells: ['ECOS 환율', '2026.10.20', '어제 17:00', { tag: 'ok', text: '완료' }, { btns: [{ label: '상세', to: '/admin/inbox/BS67' }] }]
          }
        ]
      },
      { type: 'note', text: '표시 3건 · 시연 목록 전체' }
    ]
  },
  BS65: {
    menu: 'inbox',
    lead: '실행 #RUN-1021-01 · 관세청 · 2026년 9월',
    head: {
      tabs: inboxTabs('collection'),
      crumb: '관리자 / 알림함 / 데이터 수집 이력',
      title: '수출 통계 수집 상세',
      sub: '실행 #RUN-1021-01 · 관세청 · 2026년 9월',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/collection' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '실행 상태', v: '실패' },
          { k: '작업 범위', v: '수출 원본 · 2026.09.01~09.30' },
          { k: '시작 / 종료', v: '08:00 / 08:12' },
          { k: '요청 횟수', v: '128회' },
          { k: '수신 행 수', v: '42,310행' },
          { k: '실패 원인', v: '베트남 응답 시간 초과 · 해당 범위 미공개' }
        ]
      },
      {
        type: 'buttons',
        items: [
          { label: '실패 범위 재수집', to: '/admin/inbox/BS69', kind: 'red' },
          { label: '실행 로그', to: '/admin/inbox/BS68' },
        ]
      }
    ]
  },
  BS66: {
    menu: 'inbox',
    lead: '실행 #RUN-1021-02 · KOTRA 통합 기사',
    head: {
      tabs: inboxTabs('collection'),
      crumb: '관리자 / 알림함 / 데이터 수집 이력',
      title: '기사 수집 상세',
      sub: '실행 #RUN-1021-02 · KOTRA 통합 기사',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/collection' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '실행 상태', v: '완료' },
          { k: '대상 기간', v: '2026.10.21' },
          { k: '시작 / 종료', v: '09:00 / 09:03' },
          { k: '수신 행 수', v: '386행' },
          { k: '출처', v: '시장뉴스 · 단신속보' },
          { k: '후속 처리', v: '태그 연결 · 일별 기사량 집계 완료' }
        ]
      },
      { type: 'buttons', items: [{ label: '수집 기사 확인', to: '/admin/data/BS77', kind: 'red' }] }
    ]
  },
  BS67: {
    menu: 'inbox',
    lead: '실행 #RUN-1020-03 · ECOS',
    head: {
      tabs: inboxTabs('collection'),
      crumb: '관리자 / 알림함 / 데이터 수집 이력',
      title: '환율 수집 상세',
      sub: '실행 #RUN-1020-03 · ECOS',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/collection' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '실행 상태', v: '완료' },
          { k: '기준일', v: '2026.10.20' },
          { k: '시작 / 종료', v: '17:00 / 17:01' },
          { k: '수신 행 수', v: '23행' },
          { k: '표시 단위', v: '통화별 기준 단위 유지' },
          { k: '품질 점검', v: '누락 통화 없음' }
        ]
      }
    ]
  },
  BS68: {
    menu: 'inbox',
    lead: '실행 #RUN-1021-01 · 오류 위치와 재처리 범위를 확인합니다.',
    head: {
      tabs: inboxTabs('collection'),
      crumb: '관리자 / 알림함 / 데이터 수집 이력',
      title: '수집 실행 로그',
      sub: '실행 #RUN-1021-01 · 오류 위치와 재처리 범위를 확인합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/BS65' }]
    },
    blocks: [
      {
        type: 'card',
        title: '실행 로그',
        blocks: [
          { type: 'note', text: '터미널 출력(stdout/stderr) 원문 · 가공하지 않음' },
          {
            type: 'log',
            lines: [
              '$ python -m batch.run --job export_monthly --source KCS --period 2026-09 --run-id RUN-1021-01',
              '2026-10-21 08:00:00,012 INFO  [RUN-1021-01] start job=export_monthly target=export_monthly period=2026-09..2026-09',
              '2026-10-21 08:00:00,214 INFO  [RUN-1021-01] countries=24 hs_ver=HS2022 page_size=1000',
              '2026-10-21 08:00:01,377 INFO  [RUN-1021-01] GET apis.data.go.kr/1220000/nitemtrade country=US page=1/49 -> 200 (1.16s)',
              '2026-10-21 08:04:21,903 INFO  [RUN-1021-01] country=US pages=49 rows=48,213 elapsed=261.5s',
              '2026-10-21 08:04:22,108 INFO  [RUN-1021-01] validate country=US sum_check=OK dup=0 null_amount=0',
              '2026-10-21 08:06:47,550 INFO  [RUN-1021-01] country=CN pages=5 rows=4,691 validate=OK',
              '2026-10-21 08:08:39,021 INFO  [RUN-1021-01] GET ... country=VN page=1/7 -> 200 (2.84s)',
              '2026-10-21 08:09:10,550 WARN  [RUN-1021-01] country=VN page=2/7 ReadTimeout(30s) retry=1/3 backoff=30s',
              '2026-10-21 08:10:10,912 WARN  [RUN-1021-01] country=VN page=2/7 ReadTimeout(30s) retry=2/3 backoff=60s',
              '2026-10-21 08:12:00,903 ERROR [RUN-1021-01] country=VN page=2/7 ReadTimeout(30s) retry=3/3 exhausted',
              'Traceback (most recent call last):',
              '  File "batch/sources/kcs.py", line 88, in fetch_page',
              '    resp = session.get(url, params=params, timeout=30)',
              'requests.exceptions.ReadTimeout: HTTPSConnectionPool(host=\'apis.data.go.kr\', port=443): Read timed out. (read timeout=30)',
              '2026-10-21 08:12:00,905 ERROR [RUN-1021-01] publish=HOLD scope=VN/2026-09 (partial rows discarded: 1,000)',
              '2026-10-21 08:12:01,010 INFO  [RUN-1021-01] finish status=FAILED request_count=24 received_count=52,904',
              '2026-10-21 08:12:01,011 INFO  [RUN-1021-01] failed_scope=VN/2026-09 next=manual_retry',
              'Process finished with exit code 1'
            ]
          }
        ]
      },
      { type: 'buttons', items: [{ label: '실패 범위 재수집', to: '/admin/inbox/BS69', kind: 'red' }] }
    ]
  },
  BS69: {
    menu: 'inbox',
    lead: '관리자 업무 화면입니다.',
    head: { tabs: inboxTabs('collection'), crumb: '관리자 / 알림함 / 데이터 수집 이력', title: '실패 범위를 다시 수집할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/BS65' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/inbox/BS65',
      title: '실패 범위를 다시 수집할까요?',
      lines: ['대상: 베트남 · 수출 원본 · 2026년 9월', '진행 중인 동일 작업이 없는지 확인한 뒤 실행합니다.', '원본 검증과 집계가 모두 완료된 후 공개 상태를 갱신합니다.'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/inbox/BS65' }, { label: '재수집 시작', to: '/admin/inbox/BS70', kind: 'red' }]
    }
  },
  BS70: {
    menu: 'inbox',
    lead: '베트남 · 2026년 9월 수출 통계',
    head: {
      tabs: inboxTabs('collection'),
      crumb: '관리자 / 알림함 / 데이터 수집 이력',
      title: '재수집 진행 중',
      sub: '베트남 · 2026년 9월 수출 통계',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/collection' }]
    },
    blocks: [
      {
        type: 'card',
        title: '작업을 처리하고 있습니다',
        blocks: [
          { type: 'note', text: '목록을 이동해도 서버 작업은 계속됩니다.' },
          { type: 'step', n: '1', label: '원천 데이터 요청', state: 'on' },
          { type: 'step', n: '2', label: '코드·금액·중복 검증', state: 'on' },
          { type: 'step', n: '3', label: 'HS6 집계 재계산' },
          { type: 'step', n: '4', label: '정상 공개 기간 갱신' }
        ]
      },
      {
        type: 'buttons',
        items: [{ label: '완료 상태 보기', to: '/admin/inbox/BS71', kind: 'red' }, { label: '실패 상태 보기', to: '/admin/inbox/BS72' }]
      },
      { type: 'note', text: '목업에서는 버튼으로 완료·지연·실패 등 대표 상태를 확인합니다.' }
    ]
  },
  BS71: {
    menu: 'inbox',
    lead: '실행 #RUN-1021-01 · 관세청 · 2026년 9월',
    head: {
      tabs: inboxTabs('collection'),
      crumb: '관리자 / 알림함 / 데이터 수집 이력',
      title: '수출 통계 수집 상세',
      sub: '실행 #RUN-1021-01 · 관세청 · 2026년 9월',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/collection' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '실행 상태', v: '완료' },
          { k: '작업 범위', v: '수출 원본 · 2026.09.01~09.30' },
          { k: '시작 / 종료', v: '08:00 / 08:12' },
          { k: '요청 횟수', v: '128회' },
          { k: '수신 행 수', v: '42,310행' },
          { k: '실패 원인', v: '없음 · 검증·집계 완료 후 공개' }
        ]
      },
      { type: 'buttons', items: [{ label: '수집 이력', to: '/admin/inbox/collection' }] },
      { type: 'banner', tone: 'ok', text: '재수집과 검증을 완료했습니다 · 베트남 · 2026년 9월 수출 원본 및 집계 갱신 / 정상 공개 기간에 반영했습니다.' }
    ]
  },
  BS72: {
    menu: 'inbox',
    lead: '실행 #RUN-1021-01 · 관세청 · 2026년 9월',
    head: {
      tabs: inboxTabs('collection'),
      crumb: '관리자 / 알림함 / 데이터 수집 이력',
      title: '수출 통계 수집 상세',
      sub: '실행 #RUN-1021-01 · 관세청 · 2026년 9월',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/collection' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '실행 상태', v: '재시도 실패 · 공개 보류' },
          { k: '작업 범위', v: '수출 원본 · 2026.09.01~09.30' },
          { k: '시작 / 종료', v: '08:00 / 08:12' },
          { k: '요청 횟수', v: '128회' },
          { k: '수신 행 수', v: '42,310행' },
          { k: '실패 원인', v: '베트남 응답 시간 초과 · 해당 범위 미공개' }
        ]
      },
      {
        type: 'buttons',
        items: [
          { label: '실패 범위 재수집', to: '/admin/inbox/BS69', kind: 'red' },
          { label: '실행 로그', to: '/admin/inbox/BS68' },
          { label: '다시 시도', to: '/admin/inbox/BS69', kind: 'ink' }
        ]
      },
      {
        type: 'banner',
        tone: 'error',
        text: '재수집을 완료하지 못했습니다 · 원천 응답 지연이 계속되고 있습니다. / 정상 공개 기간은 변경하지 않았습니다. 잠시 후 다시 시도하세요.'
      }
    ]
  },
  BS73: {
    menu: 'data',
    lead: '누락·중복·코드·단위 이상을 확인합니다.',
    head: {
      crumb: '관리자 / 데이터 수집·품질',
      title: '데이터 품질 점검',
      sub: '누락·중복·코드·단위 이상을 확인합니다.',
      tabs: [
        { label: '수집 현황', to: '/admin/inbox/BS64' },
      ]
    },
    blocks: [
      {
        type: 'table',
        cols: '1.6fr 1.1fr 1fr 1fr 0.9fr',
        head: ['검사 항목', '대상', '결과', '영향', '관리'],
        rows: [
          {
            cells: [
              'HS 판본 확인',
              '9월 수입 원본',
              { tag: 'amber', text: '검토 12건' },
              '공개 보류',
              { btns: [{ label: '검토', to: '/admin/data/BS74' }] }
            ]
          },
          {
            cells: ['통화 단위 검증', 'ECOS 환율', { tag: 'ok', text: '정상' }, '없음', { btns: [{ label: '실행 확인', to: '/admin/inbox/BS67' }] }]
          },
          {
            cells: ['기사 중복 검사', 'KOTRA 기사', { tag: 'ok', text: '정상' }, '없음', { btns: [{ label: '실행 확인', to: '/admin/inbox/BS66' }] }]
          }
        ]
      },
      { type: 'note', text: '표시 3건 · 시연 목록 전체' }
    ]
  },
  BS74: {
    menu: 'data',
    lead: '수입 원본 12건 · 임시 적재 영역',
    head: {
      crumb: '관리자 / 데이터 수집·품질',
      title: 'HS 판본 미확인 데이터',
      sub: '수입 원본 12건 · 임시 적재 영역',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS73' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '문제', v: '원천 응답에 품목 판본 정보 누락' },
          { k: '영향', v: '동일 코드의 다른 판본 연결 위험' },
          { k: '현재 처리', v: '공개 보류 · 오류 기록 유지' },
          { k: '검토 근거', v: '원천 파일 설명에 HS2022 확인' },
          { k: '조치 예시', v: '판본 확인 후 해당 범위 재검증' },
          { k: '처리 사유', v: '공식 원천 설명과 대조 완료' }
        ]
      },
      { type: 'buttons', items: [{ label: '재검증 요청', to: '/admin/data/BS75', kind: 'red' }] }
    ]
  },
  BS75: {
    menu: 'data',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 데이터 수집·품질', title: '확인한 판본으로 재검증할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS74' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/data/BS74',
      title: '확인한 판본으로 재검증할까요?',
      lines: ['대상 12건 · 원천 근거: HS2022', '사전 코드·중복·집계 검증을 다시 실행합니다.', '검증 통과 전에는 공개하지 않습니다.'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/data/BS74' }, { label: '재검증', to: '/admin/data/BS76', kind: 'red' }]
    }
  },
  BS76: {
    menu: 'data',
    lead: '수입 원본 12건 · 임시 적재 영역',
    head: {
      crumb: '관리자 / 데이터 수집·품질',
      title: 'HS 판본 미확인 데이터',
      sub: '수입 원본 12건 · 임시 적재 영역',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS73' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '문제', v: '원천 응답에 품목 판본 정보 누락' },
          { k: '영향', v: '동일 코드의 다른 판본 연결 위험' },
          { k: '현재 처리', v: '검증 통과 · 공개 반영' },
          { k: '검토 근거', v: '원천 파일 설명에 HS2022 확인' },
          { k: '조치 예시', v: '12건 검증 완료' },
          { k: '처리 사유', v: '공식 원천 설명과 대조 완료' }
        ]
      },
      { type: 'buttons', items: [{ label: '품질 목록', to: '/admin/data/BS73', kind: 'red' }] },
      { type: 'banner', tone: 'ok', text: '12건의 재검증을 완료했습니다 · 사전 연결·중복·집계 검증 통과 / 검토 근거와 조치 이력을 남겼습니다.' }
    ]
  },
  BS77: {
    menu: 'data',
    lead: '통합 기사와 국가·품목 연결을 검수합니다.',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사·출처 관리',
      sub: '통합 기사와 국가·품목 연결을 검수합니다.',
      tabs: [
        { label: '수집 현황', to: '/admin/inbox/BS64' },
      ]
    },
    blocks: [
      {
        type: 'tabs',
        items: [{ label: '노출 중', to: '/admin/data/BS77', on: true }, { label: '노출 제외', to: '/admin/data/BS85' }],
        sm: true
      },
      {
        type: 'table',
        cols: '2.4fr 1.3fr 1fr 1fr 0.8fr',
        head: ['기사 제목', '출처', '발행일', '공개 상태', '관리'],
        rows: [
          {
            cells: [
              '베트남 화장품 인증 절차 안내',
              'KOTRA 시장뉴스',
              '2026.10.20',
              { tag: 'ok', text: '노출 중' },
              { btns: [{ label: '검수', to: '/admin/data/BS78' }] }
            ]
          },
          {
            cells: [
              '미국 반도체 수출 규제 동향',
              'KOTRA 단신속보',
              '2026.10.20',
              { tag: 'ok', text: '노출 중' },
              { btns: [{ label: '검수', to: '/admin/data/BS79' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '표시 2건 · 시연 목록 전체' },
      { type: 'buttons', items: [{ label: '출처 현황', to: '/admin/data/BS88' }] }
    ]
  },
  BS78: {
    menu: 'data',
    lead: '베트남 화장품 인증 절차 안내',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사 검수',
      sub: '베트남 화장품 인증 절차 안내',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS77' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '출처 / 발행일', v: 'KOTRA 시장뉴스 / 2026.10.20' },
          { k: '노출 상태', v: '노출 중' },
          { k: '연결 국가', v: '베트남' },
          { k: '연결 품목', v: '330499 기타 화장품 · HS2022' },
          { k: '추출 키워드', v: '화장품 · 인증 · 통관' },
          { k: '원천 기사 분류', v: '통상·규제' }
        ]
      },
      { type: 'note', text: '관리자 분야 분류는 원천 기사 분류와 구분합니다.' },
      { type: 'note', text: '노출 제외 시 검색·추천·신규 분석 근거에서 제외하며, 기존 리포트는 별도 영향 확인이 필요합니다.' },
      { type: 'note', text: '과거 리포트의 기사 인용은 자동 수정하지 않고 생성 당시 결과로 보존합니다.' },
      {
        type: 'buttons',
        items: [
          { label: '기사 내용·출처 보기', to: '/admin/data/BS80' },
          { label: '국가·품목 연결 수정', to: '/admin/data/BS81', kind: 'red' },
          { label: '노출 제외', to: '/admin/data/BS83', kind: 'danger' }
        ]
      }
    ]
  },
  BS79: {
    menu: 'data',
    lead: '미국 반도체 수출 규제 동향',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사 검수',
      sub: '미국 반도체 수출 규제 동향',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS77' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '출처 / 발행일', v: 'KOTRA 단신속보 / 2026.10.20' },
          { k: '노출 상태', v: '노출 중' },
          { k: '연결 국가', v: '미국' },
          { k: '연결 품목', v: '854232 메모리 · HS2022' },
          { k: '추출 키워드', v: '반덤핑신규분석 · 반도체' },
          { k: '확인 사항', v: '분야 분류 검토 대기' }
        ]
      },
      { type: 'buttons', items: [{ label: '연결 키워드 검토', to: '/admin/keywords', kind: 'red' }] }
    ]
  },
  BS80: {
    menu: 'data',
    lead: '원문 대조를 위한 기사 상세 패널',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사 내용·출처',
      sub: '원문 대조를 위한 기사 상세 패널',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS78' }]
    },
    blocks: [
      {
        type: 'card',
        title: '베트남 화장품 인증 절차 안내',
        blocks: [
          { type: 'label', text: '출처' },
          { type: 'para', text: 'KOTRA 해외시장뉴스 · 2026.10.20 · 시연용 기사' },
          { type: 'label', text: '본문 요약 예시' },
          { type: 'para', text: '베트남 시장에 진출하는 화장품 수출 기업이 확인해야 할 인증 서류와 통관 절차를 설명하는 기사입니다. 현지 제도 변경 여부와 실제 적용 시점을 원문에서 확인합니다.' },
          { type: 'label', text: '검수 포인트' },
          { type: 'para', text: '기사에 언급된 국가는 베트남이며, 연결 품목은 기타 화장품입니다. 관세율·시행일 등 구체 수치는 원문 근거가 있는 경우에만 분석에 사용합니다.' },
          { type: 'label', text: '원문 연결' },
          { type: 'para', text: '실서비스에서는 수집된 원문 URL로 이동합니다. 이 목업은 가상 기사로 화면 이동만 시연합니다.' }
        ]
      }
    ]
  },
  BS81: {
    menu: 'data',
    lead: '변경 예시 · 품목 오연결 교정',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사 연결 정보 수정',
      sub: '변경 예시 · 품목 오연결 교정',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS78' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '대상 기사', v: '베트남 화장품 인증 절차 안내' },
          { k: '국가', v: '베트남' },
          { k: '기존 품목', v: '330499 기타 화장품 · HS2022' },
          { k: '수정 품목', v: '330410 입술화장용 제품 · HS2022' },
          { k: '판단 근거', v: '본문의 대상 제품과 HS 사전 대조' },
          { k: '반영 범위', v: '해당 기사 태그 및 관련 일별 집계' }
        ]
      },
      {
        type: 'buttons',
        items: [{ label: '수정 저장', to: '/admin/data/BS82', kind: 'red' }, { label: '취소', to: '/admin/data/BS78' }]
      }
    ]
  },
  BS82: {
    menu: 'data',
    lead: '베트남 화장품 인증 절차 안내',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사 검수',
      sub: '베트남 화장품 인증 절차 안내',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS77' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '출처 / 발행일', v: 'KOTRA 시장뉴스 / 2026.10.20' },
          { k: '노출 상태', v: '노출 중' },
          { k: '연결 국가', v: '베트남' },
          { k: '연결 품목', v: '330410 입술화장용 제품 · HS2022' },
          { k: '추출 키워드', v: '화장품 · 인증 · 통관' },
          { k: '원천 기사 분류', v: '통상·규제' }
        ]
      },
      { type: 'note', text: '관리자 분야 분류는 원천 기사 분류와 구분합니다.' },
      { type: 'note', text: '노출 제외 시 검색·추천·신규 분석 근거에서 제외하며, 기존 리포트는 별도 영향 확인이 필요합니다.' },
      { type: 'note', text: '과거 리포트의 기사 인용은 자동 수정하지 않고 생성 당시 결과로 보존합니다.' },
      {
        type: 'buttons',
        items: [
          { label: '기사 내용·출처 보기', to: '/admin/data/BS80' },
          { label: '국가·품목 연결 수정', to: '/admin/data/BS81', kind: 'red' },
          { label: '노출 제외', to: '/admin/data/BS83', kind: 'danger' },
          { label: '처리 이력', to: '/admin/history/BS156' }
        ]
      },
      { type: 'banner', tone: 'ok', text: '기사 연결 정보를 수정했습니다 · 품목: 330410 입술화장용 제품 · HS2022 / 관련 기사량 집계 갱신 작업을 접수했습니다.' }
    ]
  },
  BS83: {
    menu: 'data',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 데이터 수집·품질 / 기사·출처', title: '기사 노출을 제외할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS78' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/data/BS78',
      title: '기사 노출을 제외할까요?',
      danger: true,
      lines: ['베트남 화장품 인증 절차 안내', '사유: 원천 기사 정정 여부 확인 중', '신규 검색·추천·분석 근거에서 제외합니다. 원문 데이터는 보존합니다.'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/data/BS78' }, { label: '노출 제외', to: '/admin/data/BS84', kind: 'danger' }]
    }
  },
  BS84: {
    menu: 'data',
    lead: '베트남 화장품 인증 절차 안내',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사 검수',
      sub: '베트남 화장품 인증 절차 안내',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS77' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '출처 / 발행일', v: 'KOTRA 시장뉴스 / 2026.10.20' },
          { k: '노출 상태', v: '노출 제외' },
          { k: '연결 국가', v: '베트남' },
          { k: '연결 품목', v: '330499 기타 화장품 · HS2022' },
          { k: '추출 키워드', v: '화장품 · 인증 · 통관' },
          { k: '원천 기사 분류', v: '통상·규제' }
        ]
      },
      { type: 'note', text: '관리자 분야 분류는 원천 기사 분류와 구분합니다.' },
      { type: 'note', text: '노출 제외 시 검색·추천·신규 분석 근거에서 제외하며, 기존 리포트는 별도 영향 확인이 필요합니다.' },
      { type: 'note', text: '과거 리포트의 기사 인용은 자동 수정하지 않고 생성 당시 결과로 보존합니다.' },
      {
        type: 'buttons',
        items: [{ label: '복구', to: '/admin/data/BS86', kind: 'red' }, { label: '처리 이력', to: '/admin/history/BS156' }]
      },
      { type: 'banner', tone: 'ok', text: '기사 노출을 제외했습니다 · 시연 기사 1건 · 정정 확인 전까지 노출 중단 / 이 기사를 인용한 기존 리포트는 별도로 확인할 수 있습니다.' }
    ]
  },
  BS85: {
    menu: 'data',
    lead: '원문은 보존하고 공개 여부만 관리합니다.',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '노출 제외 기사',
      sub: '원문은 보존하고 공개 여부만 관리합니다.',
      tabs: [{ label: '노출 중', to: '/admin/data/BS77' }, { label: '노출 제외', to: '/admin/data/BS85', on: true }]
    },
    blocks: [
      {
        type: 'table',
        cols: '2.4fr 1.4fr 1fr 1fr 0.8fr',
        head: ['기사 제목', '제외 사유', '처리 시각', '상태', '관리'],
        rows: [
          {
            cells: [
              '베트남 화장품 인증 절차 안내',
              '원천 정정 확인',
              '오늘 10:30',
              { tag: 'red', text: '노출 제외' },
              { btns: [{ label: '복구', to: '/admin/data/BS86' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '표시 1건 · 시연 목록 전체' }
    ]
  },
  BS86: {
    menu: 'data',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 데이터 수집·품질 / 기사·출처', title: '기사 노출을 복구할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS85' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/data/BS85',
      title: '기사 노출을 복구할까요?',
      lines: ['원천 내용과 연결 정보를 확인했습니다.', '검색·추천·신규 분석 근거에 다시 포함합니다.', '복구 사유: 원천 정정 및 검수 완료'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/data/BS85' }, { label: '노출 복구', to: '/admin/data/BS87', kind: 'red' }]
    }
  },
  BS87: {
    menu: 'data',
    lead: '베트남 화장품 인증 절차 안내',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사 검수',
      sub: '베트남 화장품 인증 절차 안내',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS77' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '출처 / 발행일', v: 'KOTRA 시장뉴스 / 2026.10.20' },
          { k: '노출 상태', v: '노출 중' },
          { k: '연결 국가', v: '베트남' },
          { k: '연결 품목', v: '330499 기타 화장품 · HS2022' },
          { k: '추출 키워드', v: '화장품 · 인증 · 통관' },
          { k: '원천 기사 분류', v: '통상·규제' }
        ]
      },
      { type: 'note', text: '관리자 분야 분류는 원천 기사 분류와 구분합니다.' },
      { type: 'note', text: '노출 제외 시 검색·추천·신규 분석 근거에서 제외하며, 기존 리포트는 별도 영향 확인이 필요합니다.' },
      { type: 'note', text: '과거 리포트의 기사 인용은 자동 수정하지 않고 생성 당시 결과로 보존합니다.' },
      {
        type: 'buttons',
        items: [
          { label: '기사 내용·출처 보기', to: '/admin/data/BS80' },
          { label: '국가·품목 연결 수정', to: '/admin/data/BS81', kind: 'red' },
          { label: '노출 제외', to: '/admin/data/BS83', kind: 'danger' }
        ]
      },
      { type: 'banner', tone: 'ok', text: '기사 노출을 복구했습니다 · 베트남 화장품 인증 절차 안내 · 노출 중' }
    ]
  },
  BS88: {
    menu: 'data',
    lead: '출처별 사용 여부와 수집 결과를 확인합니다.',
    head: {
      crumb: '관리자 / 데이터 수집·품질 / 기사·출처',
      title: '기사 출처 현황',
      sub: '출처별 사용 여부와 수집 결과를 확인합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/data/BS77' }]
    },
    blocks: [
      {
        type: 'table',
        cols: '1.8fr 1fr 1fr 1fr 1fr',
        head: ['출처', '사용 여부', '최근 실행', '오늘 수신', '관리'],
        rows: [
          {
            cells: ['KOTRA 시장뉴스', { tag: 'ok', text: '사용' }, '09:03 완료', '244건', { btns: [{ label: '수집 상세', to: '/admin/inbox/BS66' }] }]
          },
          {
            cells: ['KOTRA 단신속보', { tag: 'ok', text: '사용' }, '09:03 완료', '142건', { btns: [{ label: '수집 상세', to: '/admin/inbox/BS66' }] }]
          }
        ]
      },
      { type: 'note', text: '표시 2건 · 시연 목록 전체' },
      { type: 'buttons', items: [{ label: '출처 운영 설정', to: '/admin/history/BS159' }] }
    ]
  },
  BS149: {
    menu: 'inbox',
    lead: '리포트·주요 이슈·급증 알림이 회원 알림함에 들어갔는지 확인합니다.',
    head: { tabs: inboxTabs('delivery'), crumb: '관리자 / 알림함 / 회원 알림 전달 내역', title: '회원 알림 전달 내역', sub: '리포트·주요 이슈·급증 알림이 회원 알림함에 들어갔는지 확인합니다.' },
    blocks: [
      { type: 'banner', tone: 'info', text: '조회 목적: 알림 누락·중복·수신 제외 원인 확인. 상세에서 회원의 동의·수신 설정을 함께 봅니다.' },
      {
        type: 'table',
        cols: '2fr 1fr 1fr 1.4fr 0.8fr',
        head: ['회원에게 발생한 알림', '유형', '발생 시각', '전달 결과', '상세'],
        rows: [
          {
            cells: [
              '월간 리포트 발행 완료',
              '리포트',
              '10:34',
              { tag: 'ok', text: '개인 알림 편입' },
              { btns: [{ label: '상세', to: '/admin/inbox/BS150' }] }
            ]
          },
          {
            cells: [
              '미국 반도체 기사량 증가',
              '급증',
              '09:20',
              { tag: 'plum', text: '수신 설정에 따라 반영' },
              { btns: [{ label: '상세', to: '/admin/inbox/BS151' }] }
            ]
          }
        ]
      },
      { type: 'note', text: '표시 2건 · 시연 목록 전체' },
      {
        type: 'buttons',
        items: [{ label: '회원 수신 설정', to: '/admin/inbox/BS153' }, { label: '급증 판정 기준', to: '/admin/inbox/BS155' }]
      }
    ]
  },
  BS150: {
    menu: 'inbox',
    lead: '회원 알림함 반영 여부 · 월간 리포트 #RPT-1048',
    head: {
      tabs: inboxTabs('delivery'),
      crumb: '관리자 / 알림함 / 회원 알림 전달 내역',
      title: '회원 리포트 알림 전달 상세',
      sub: '회원 알림함 반영 여부 · 월간 리포트 #RPT-1048',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/BS149' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '수신 회원', v: 'member01@example.com' },
          { k: '발생 시각', v: '2026.10.21 10:34' },
          { k: '동의 / 전체 수신', v: '현재 버전 동의 / ON' },
          { k: '개인 알림함', v: '편입 완료' },
          { k: '팝업 / 읽음', v: '표시 기록 있음 / 미읽음' },
          { k: '중복 검사', v: '같은 발행 사건 중복 없음' }
        ]
      },
      { type: 'buttons', items: [{ label: '수신 설정', to: '/admin/inbox/BS153' }] }
    ]
  },
  BS151: {
    menu: 'inbox',
    lead: '미국 · 메모리 / HS2022 · 관심 급증 사건 #SPIKE-1021',
    head: {
      tabs: inboxTabs('delivery'),
      crumb: '관리자 / 알림함 / 회원 알림 전달 내역',
      title: '회원 급증 알림 전달 상세',
      sub: '미국 · 메모리 / HS2022 · 관심 급증 사건 #SPIKE-1021',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/BS149' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '대상일 T / 기사 수', v: '2026.10.20 / 5건' },
          { k: '이전 7일', v: '2026.10.13~10.19 / 합계 14건' },
          { k: '기준 일평균', v: '14 ÷ 7 = 2건' },
          { k: '발생 당시 배수', v: '5 ÷ 2 = 2.5배 / 기준 2.5배 이상' },
          { k: '집계 상태', v: '시장뉴스·단신속보 수집 및 집계 완료' },
          { k: '알림 편입', v: '관심 조건·약관 동의·전체 수신 확인' }
        ]
      },
      { type: 'note', text: '고유 기사 ID 기준 집계. 대상일은 비교 기간에서 제외합니다.' },
      { type: 'note', text: '발생 당시 조건과 기사 수·평균·배수를 보존합니다. 현재 기준으로 과거 사건을 다시 판정하지 않습니다.' },
      {
        type: 'buttons',
        items: [
          { label: '수신 제외 사유', to: '/admin/inbox/BS152' },
          { label: '판정 기준', to: '/admin/inbox/BS155', kind: 'red' },
          { label: '관련 기사', to: '/admin/data/BS79' }
        ]
      }
    ]
  },
  BS152: {
    menu: 'inbox',
    lead: '현재 설정에 따른 진단 · 사건 당시 제외 사유는 별도 기록이 필요합니다.',
    head: {
      tabs: inboxTabs('delivery'),
      crumb: '관리자 / 알림함 / 회원 알림 전달 내역',
      title: '알림 편입 제외 내역',
      sub: '현재 설정에 따른 진단 · 사건 당시 제외 사유는 별도 기록이 필요합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/BS151' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '회원', v: 'reviewer@example.com' },
          { k: '현재 관심 조건', v: '일치' },
          { k: '현재 약관 동의', v: '현재 버전 동의' },
          { k: '현재 전체 수신', v: 'OFF' },
          { k: '개인 알림함', v: '해당 사건 편입 기록 없음' },
          { k: '당시 제외 사유', v: '기록 없음 · 현재 설정만으로 확정 불가' }
        ]
      }
    ]
  },
  BS153: {
    menu: 'inbox',
    lead: 'member01@example.com · 설정과 동의 상태 조회',
    head: {
      tabs: inboxTabs('delivery'),
      crumb: '관리자 / 알림함 / 회원 알림 전달 내역',
      title: '회원 수신 설정 확인',
      sub: 'member01@example.com · 설정과 동의 상태 조회',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/BS149' }]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '수신 동의는 회원의 선택입니다. 관리자가 임의로 동의·수신 ON으로 변경하지 않습니다.' },
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '전체 수신', v: 'ON' },
          { k: '리포트 팝업', v: 'ON' },
          { k: '주요 이슈 팝업', v: 'ON' },
          { k: '급증 팝업', v: 'OFF' },
          { k: '약관 상태', v: '현재 버전 동의' },
          { k: '동의 이력', v: '2026.10.10 14:20 · 동의' }
        ]
      },
      { type: 'buttons', items: [{ label: '약관 응답 이력', to: '/admin/inbox/BS154' }] }
    ]
  },
  BS154: {
    menu: 'inbox',
    lead: 'member01@example.com · 버전별 응답을 보존합니다.',
    head: {
      tabs: inboxTabs('delivery'),
      crumb: '관리자 / 알림함 / 회원 알림 전달 내역',
      title: '약관 응답 이력',
      sub: 'member01@example.com · 버전별 응답을 보존합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/BS153' }]
    },
    blocks: [
      {
        type: 'card',
        title: '실행 타임라인',
        blocks: [
          { type: 'timeline', time: '10.08 11:20', tag: '동의', tone: 'ok', text: '이전 약관 버전에 동의' },
          { type: 'timeline', time: '10.10 14:20', tag: '동의', tone: 'ok', text: '현재 약관 버전에 동의' },
          { type: 'timeline', time: '10.20 09:10', tag: '설정', tone: 'plum', text: '급증 팝업 OFF. 약관 동의 철회와 별도' }
        ]
      }
    ]
  },
  BS155: {
    menu: 'inbox',
    lead: '관심 급증과 주요 이슈의 판정 기간을 구분합니다. 현재 기준 조회 전용입니다.',
    head: {
      tabs: inboxTabs('delivery'),
      crumb: '관리자 / 알림함 / 회원 알림 전달 내역',
      title: '기사량 알림 판정 기준',
      sub: '관심 급증과 주요 이슈의 판정 기간을 구분합니다. 현재 기준 조회 전용입니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/inbox/BS149' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '관심 급증 / 대상', v: '어제 T의 기사 수' },
          { k: '관심 급증 / 비교', v: 'T-7~T-1 일평균 대비 2.5배 이상' },
          { k: '주요 이슈 / 대상', v: 'T-6~T 최근 7일 기사 합계' },
          { k: '주요 이슈 / 비교', v: 'T-62~T-7 합계 ÷ 8 대비 3배 이상' },
          { k: '평균이 0인 경우', v: '판정 분모만 1로 대체 / 양수 평균 유지' },
          { k: '수집 누락 처리', v: '미수집·실패 기간을 0건으로 채우지 않음' }
        ]
      },
      { type: 'note', text: '양쪽 원천의 수집·태깅·집계 완료 여부를 먼저 확인합니다. 각 집계 대상에서 고유 기사 ID로 중복을 제거합니다.' },
      { type: 'note', text: '임의의 최소 10건·4배 기준은 적용하지 않습니다. 주요 이슈의 기사 대상 조건은 별도로 함께 확인합니다.' }
    ]
  },
  BS156: {
    menu: 'history',
    lead: '변경 전후 값·처리 사유·영향 범위를 추적합니다.',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '관리자 처리 이력',
      sub: '변경 전후 값·처리 사유·영향 범위를 추적합니다.',
      tabs: [{ label: '처리 이력', to: '/admin/history/BS156', on: true }, { label: '운영 설정', to: '/admin/history/BS159' }]
    },
    blocks: [
      { type: 'banner', tone: 'info', text: '예시 이력입니다. 각 화면에서 수행한 실제 변경이 저장되는 목업은 아닙니다.' },
      {
        type: 'table',
        cols: '1fr 1.4fr 1.2fr 1.4fr 0.8fr',
        head: ['처리 시각', '관리자', '작업', '대상', '관리'],
        rows: [
          { cells: ['10:30', '운영 관리자', '분야 지정', '반덤핑신규분석', { btns: [{ label: '상세', to: '/admin/history/BS157' }] }] },
          { cells: ['10:35', '최고 관리자', '회원 권한 변경', 'member01', { btns: [{ label: '상세', to: '/admin/history/BS158' }] }] }
        ]
      },
      { type: 'note', text: '표시 2건 · 시연 목록 전체' }
    ]
  },
  BS157: {
    menu: 'history',
    lead: '분야 지정 · 반덤핑신규분석',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '처리 이력 상세',
      sub: '분야 지정 · 반덤핑신규분석',
      actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS156' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '처리자 / 시각', v: '운영 관리자 / 2026.10.21 10:30' },
          { k: '변경 전', v: '미처리 · 분야 없음' },
          { k: '변경 후', v: '분류함 · 관세·무역규제' },
          { k: '반영 범위', v: '기존 142건 + 신규 기사' },
          { k: '처리 사유', v: '최근 기사 근거로 분야 확정' },
          { k: '결과', v: '기존 기사 반영 완료' }
        ]
      },
      { type: 'buttons', items: [{ label: '현재 키워드 보기', to: '/admin/keywords?status=done' }] }
    ]
  },
  BS158: {
    menu: 'history',
    lead: '회원 권한 변경 · member01@example.com',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '처리 이력 상세',
      sub: '회원 권한 변경 · member01@example.com',
      actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS156' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '처리자 / 시각', v: '최고 관리자 / 2026.10.21 10:35' },
          { k: '변경 전', v: '일반 회원' },
          { k: '변경 후', v: '관리자(콘텐츠)' },
          { k: '허용 범위', v: '기사·키워드 분류' },
          { k: '사유', v: '기사 분류 검수 담당 지정' },
          { k: '처리 결과', v: '완료' }
        ]
      }
    ]
  },
  BS159: {
    menu: 'history',
    lead: '서비스 전체에 적용되는 옵션을 관리합니다.',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '공통 운영 설정',
      sub: '서비스 전체에 적용되는 옵션을 관리합니다.',
      tabs: [{ label: '처리 이력', to: '/admin/history/BS156' }, { label: '운영 설정', to: '/admin/history/BS159', on: true }]
    },
    blocks: [
      {
        type: 'table',
        cols: '1.4fr 1.6fr 1fr 0.8fr 0.8fr',
        head: ['설정 영역', '현재 값', '적용 범위', '상태', '관리'],
        rows: [
          {
            cells: [
              '기사량 알림 기준',
              '관심 2.5배 · 주요 이슈 3배',
              '급증 판정',
              { tag: 'ok', text: '사용' },
              { btns: [{ label: '상세', to: '/admin/inbox/BS155' }] }
            ]
          },
          {
            cells: ['정기 발행 주기', '주간·월간·분기', '리포트', { tag: 'ok', text: '사용' }, { btns: [{ label: '상세', to: '/admin/history/BS160' }] }]
          },
          {
            cells: ['기사 출처', '시장뉴스·단신속보', '수집·노출', { tag: 'ok', text: '사용' }, { btns: [{ label: '상세', to: '/admin/history/BS161' }] }]
          },
          {
            cells: ['알림 약관', '현재 버전', '동의 확인', { tag: 'plum', text: '적용' }, { btns: [{ label: '상세', to: '/admin/history/BS166' }] }]
          }
        ]
      },
      { type: 'note', text: '표시 4건 · 시연 목록 전체' }
    ]
  },
  BS160: {
    menu: 'history',
    lead: '발행 주기와 데이터 준비 조건을 조회합니다. 월간·분기는 고정 날짜 발행이 아닙니다.',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '정기 발행 일정',
      sub: '발행 주기와 데이터 준비 조건을 조회합니다. 월간·분기는 고정 날짜 발행이 아닙니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS159' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '주간', v: '매주 월요일 새벽 / 기사 수집·집계 후' },
          { k: '월간', v: '새 월 통계 적재·검증 완료 후' },
          { k: '분기', v: '3·6·9·12월 통계 적재·검증 완료 후' },
          { k: '분기 분석 범위', v: '1~3월 / 4~6월 / 7~9월 / 10~12월' },
          { k: '시간대', v: 'Asia/Seoul' },
          { k: '데이터 미완료 시', v: '생성 대기 / 검증 실패 시 발행 보류' }
        ]
      },
      { type: 'note', text: '분기는 가입일 기준 3개월이 아닌 달력 분기입니다. 선택 주기의 활성 구독을 대상으로 생성합니다.' }
    ]
  },
  BS161: {
    menu: 'history',
    lead: '출처별 신규 수집 사용 여부를 관리합니다.',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '기사 출처 운영 설정',
      sub: '출처별 신규 수집 사용 여부를 관리합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS159' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '대상 출처', v: 'KOTRA 단신속보' },
          { k: '현재 상태', v: '사용' },
          { k: '데이터 통합', v: '시장뉴스와 함께 기사 영역에 제공' },
          { k: '일시 중지 영향', v: '신규 수집 중단' },
          { k: '기존 기사', v: '열람과 저장 근거 유지' },
          { k: '중지 사유 예시', v: '원천 API 점검' }
        ]
      },
      { type: 'buttons', items: [{ label: '신규 수집 일시 중지', to: '/admin/history/BS162', kind: 'danger' }] }
    ]
  },
  BS162: {
    menu: 'history',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 처리 이력·운영 설정', title: '출처의 신규 수집을 중지할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS161' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/history/BS161',
      title: '출처의 신규 수집을 중지할까요?',
      danger: true,
      lines: ['KOTRA 단신속보 · 원천 API 점검', '신규 수집만 중지합니다. 기존 기사와 리포트 근거는 보존합니다.', '현재 실행 중인 작업은 종료 후 다음 실행부터 중지합니다.'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/history/BS161' }, { label: '수집 중지', to: '/admin/history/BS163', kind: 'danger' }]
    }
  },
  BS163: {
    menu: 'history',
    lead: '출처별 신규 수집 사용 여부를 관리합니다.',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '기사 출처 운영 설정',
      sub: '출처별 신규 수집 사용 여부를 관리합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS159' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '대상 출처', v: 'KOTRA 단신속보' },
          { k: '현재 상태', v: '일시 중지' },
          { k: '데이터 통합', v: '시장뉴스와 함께 기사 영역에 제공' },
          { k: '일시 중지 영향', v: '신규 수집 중단' },
          { k: '기존 기사', v: '열람과 저장 근거 유지' },
          { k: '중지 사유 예시', v: '원천 API 점검' }
        ]
      },
      {
        type: 'buttons',
        items: [{ label: '수집 재개', to: '/admin/history/BS164', kind: 'red' }, { label: '수집 현황', to: '/admin/inbox/BS64' }]
      },
      { type: 'banner', tone: 'ok', text: '신규 수집을 중지했습니다 · KOTRA 단신속보 · 일시 중지 / 기존 기사와 리포트는 유지됩니다.' }
    ]
  },
  BS164: {
    menu: 'history',
    lead: '관리자 업무 화면입니다.',
    head: { crumb: '관리자 / 처리 이력·운영 설정', title: '기사 수집을 재개할까요?', actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS163' }] },
    blocks: [{ type: 'card', title: '변경 내용 확인', blocks: [{ type: 'skeleton', rows: 4 }] }],
    modal: {
      label: '작업 확인',
      close: '/admin/history/BS163',
      title: '기사 수집을 재개할까요?',
      lines: ['KOTRA 단신속보 · 원천 API 점검 완료', '다음 수집 일정부터 재개합니다. 중단 기간은 별도 재수집합니다.'],
      inputs: [],
      notes: ['확인하면 처리 결과 화면으로 이동합니다.'],
      buttons: [{ label: '취소', to: '/admin/history/BS163' }, { label: '수집 재개', to: '/admin/history/BS165', kind: 'red' }]
    }
  },
  BS165: {
    menu: 'history',
    lead: '출처별 신규 수집 사용 여부를 관리합니다.',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '기사 출처 운영 설정',
      sub: '출처별 신규 수집 사용 여부를 관리합니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS159' }]
    },
    blocks: [
      {
        type: 'kv',
        cols: 2,
        items: [
          { k: '대상 출처', v: 'KOTRA 단신속보' },
          { k: '현재 상태', v: '사용' },
          { k: '데이터 통합', v: '시장뉴스와 함께 기사 영역에 제공' },
          { k: '일시 중지 영향', v: '신규 수집 중단' },
          { k: '기존 기사', v: '열람과 저장 근거 유지' },
          { k: '중지 사유 예시', v: '원천 API 점검' }
        ]
      },
      { type: 'note', text: '다음 수집 일정부터 재개합니다. 중단 기간은 수집 현황에서 범위를 확인해 별도로 재수집합니다.' },
      { type: 'buttons', items: [{ label: '신규 수집 일시 중지', to: '/admin/history/BS162', kind: 'danger' }] },
      { type: 'banner', tone: 'ok', text: '기사 수집을 재개했습니다 · KOTRA 단신속보 · 사용 / 다음 수집 일정부터 실행합니다.' }
    ]
  },
  BS166: {
    menu: 'history',
    lead: '기존 동의 기록과 연결된 본문은 덮어쓰지 않습니다.',
    head: {
      crumb: '관리자 / 처리 이력·운영 설정',
      title: '알림 약관 버전',
      sub: '기존 동의 기록과 연결된 본문은 덮어쓰지 않습니다.',
      actions: [{ label: '목록 / 뒤로', to: '/admin/history/BS159' }]
    },
    blocks: [
      {
        type: 'card',
        title: '현재 알림 약관 · 시연용 본문',
        blocks: [
          { type: 'label', text: '수신 대상' },
          { type: 'para', text: '관심 정보에 따른 주요 이슈·기사 급증·리포트 발행 알림을 제공합니다.' },
          { type: 'label', text: '수신 선택' },
          { type: 'para', text: '전체 알림과 유형별 팝업은 회원이 설정합니다. 전체 알림 OFF 시 신규 개인 알림 편입과 팝업을 중단합니다.' },
          { type: 'label', text: '기존 기록' },
          { type: 'para', text: '설정 변경으로 기존 알림을 삭제하지 않습니다. 동의 철회는 일반 수신 OFF와 구분하여 기록합니다.' },
          { type: 'label', text: '버전 보존' },
          { type: 'para', text: '현재 본문과 적용 시점, 버전별 동의 이력을 조회합니다. 과거 본문과 응답은 덮어쓰지 않습니다. 이 목업의 본문은 시연용입니다.' }
        ]
      },
      { type: 'buttons', items: [{ label: '회원 동의 이력', to: '/admin/inbox/BS154' }] }
    ]
  }
}
