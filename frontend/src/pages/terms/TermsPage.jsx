// 푸터에서 여는 이용약관·개인정보 처리방침. 로그인 없이 볼 수 있다.
import { QueryState, useQuery } from '../../api/hooks'
import TermsBody from '../../components/TermsBody'

export default function TermsPage({ kind }) {
  const terms = useQuery(`/terms/${kind}`)
  return (
    <main className="page">
      <div className="wrap card" style={{ maxWidth: 820 }}>
        <QueryState state={terms}>
          {data => (
            <>
              <h1 className="t1">{data.terms.title}</h1>
              <TermsBody body={data.terms.body} />
            </>
          )}
        </QueryState>
      </div>
    </main>
  )
}
