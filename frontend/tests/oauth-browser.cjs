const {chromium}=require('playwright');
// 소셜 로그인 흐름 검사(실제 카카오·네이버·구글 대신 가짜 응답 사용)
// 사용: VITE_KAKAO_REST_API_KEY=kakao-test VITE_NAVER_CLIENT_ID=naver-test VITE_GOOGLE_CLIENT_ID=google-test npm run build
//       npx vite preview --port 4174  →  node tests/oauth-browser.cjs http://127.0.0.1:4174
const B=process.argv[2]||'http://127.0.0.1:4174';
(async()=>{
 const b=await chromium.launch({...(process.env.CHROMIUM?{executablePath:process.env.CHROMIUM}:{})});
 const ctx=await b.newContext({viewport:{width:1440,height:900}});
 const p=await ctx.newPage();
 const errs=[];p.on('pageerror',e=>errs.push(String(e)));
 const out=(n,ok)=>console.log((ok?'PASS ':'FAIL ')+n);
 let authUrl=null;
 for(const host of ['https://kauth.kakao.com/**','https://nid.naver.com/**','https://accounts.google.com/**'])
   await p.route(host,r=>{authUrl=r.request().url();r.fulfill({status:200,contentType:'text/html',body:'<h1>PROVIDER LOGIN PAGE</h1>'})});
 let apiUser={providerId:'k-1',email:'new.kakao@example.com',name:'카카오신규'};let apiStatus=200;
 await p.route('**/api/auth/oauth/**',r=>r.fulfill({status:apiStatus,contentType:'application/json',body:JSON.stringify(apiStatus===200?{user:apiUser}:{message:'카카오 토큰 발급 실패: invalid_grant'})}));
 await p.goto(B+'/#/login'); await p.evaluate(()=>localStorage.clear()); await p.reload();
 await p.click('button[aria-label="카카오로 로그인"]'); await p.waitForTimeout(500);
 const u=new URL(authUrl);
 out('카카오 버튼 → kauth.kakao.com 이동', u.host==='kauth.kakao.com' && u.searchParams.get('client_id')==='kakao-test' && u.searchParams.get('redirect_uri')===B+'/oauth/kakao' && u.searchParams.get('response_type')==='code');
 const state=u.searchParams.get('state');
 out('브라우저에 실제 제공자 페이지 표시', (await p.locator('body').innerText()).includes('PROVIDER LOGIN PAGE'));
 await p.goto(`${B}/oauth/kakao?code=abc&state=${state}`); await p.waitForTimeout(800);
 out('신규 회원 → LG05 가입 마무리', p.url().endsWith('/#/signup/social') && (await p.inputValue('#ss-name'))==='카카오신규' && (await p.inputValue('#ss-email'))==='new.kakao@example.com');
 await p.click('label:has-text("서비스 이용약관")'); await p.click('label:has-text("개인정보 수집")');
 await p.click('button:has-text("필수 동의 완료")'); await p.waitForTimeout(300);
 out('가입 → 관심사 등록', p.url().includes('#/onboarding'));
 await p.evaluate(()=>localStorage.removeItem('bs2.session'));
 await p.goto(B+'/#/login'); await p.reload(); await p.waitForTimeout(300);
 await p.click('button[aria-label="카카오로 로그인"]'); await p.waitForTimeout(400);
 const s2=new URL(authUrl).searchParams.get('state');
 await p.goto(`${B}/oauth/kakao?code=def&state=${s2}`); await p.waitForTimeout(800);
 out('기존 연결 계정 → 메인', p.url().endsWith('/#/') && (await p.locator('body').innerText()).includes('무역 현황'));
 await p.evaluate(()=>localStorage.removeItem('bs2.session'));
 await p.goto(B+'/#/login'); await p.reload(); await p.waitForTimeout(300);
 await p.click('button[aria-label="네이버로 로그인"]'); await p.waitForTimeout(400);
 const nu=new URL(authUrl); out('네이버 버튼 → nid.naver.com', nu.host==='nid.naver.com' && nu.searchParams.get('redirect_uri')===B+'/oauth/naver');
 apiUser={providerId:'nv-9',email:'demo@example.com',name:'체험'};
 await p.goto(`${B}/oauth/naver?code=n1&state=${nu.searchParams.get('state')}`); await p.waitForTimeout(800);
 out('같은 이메일 계정과 연결 → 메인', p.url().endsWith('/#/'));
 await p.evaluate(()=>localStorage.removeItem('bs2.session'));
 await p.goto(B+'/#/login'); await p.reload(); await p.waitForTimeout(300);
 await p.click('button[aria-label="구글로 로그인"]'); await p.waitForTimeout(400);
 const gu=new URL(authUrl); out('구글 버튼 → accounts.google.com', gu.host==='accounts.google.com' && gu.searchParams.get('scope')==='openid email profile');
 await p.goto(`${B}/oauth/google?error=access_denied&state=${gu.searchParams.get('state')}`); await p.waitForTimeout(600);
 out('취소 → LG06', p.url().includes('#/login/error') && (await p.locator('body').innerText()).includes('취소'));
 await p.goto(`${B}/oauth/kakao?code=zzz&state=forged`); await p.waitForTimeout(600);
 out('state 불일치 차단', p.url().includes('reason=state'));
 await p.click('a:has-text("로그인 다시 선택")'); await p.waitForTimeout(300);
 await p.click('button[aria-label="카카오로 로그인"]'); await p.waitForTimeout(400);
 apiStatus=400;
 await p.goto(`${B}/oauth/kakao?code=bad&state=${new URL(authUrl).searchParams.get('state')}`); await p.waitForTimeout(700);
 out('토큰 교환 실패 → LG06 사유 표시', (await p.locator('body').innerText()).includes('invalid_grant'));
 
 out('브라우저 오류 없음', errs.length===0); if(errs.length) console.log(errs);
 await b.close();
})();
