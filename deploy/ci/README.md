# main 브랜치 검증과 AWS 배포

`main`을 대상으로 PR을 열면 GitHub Actions가 백엔드 전체 단위 테스트, 프론트엔드 단위 테스트, Vite 운영 빌드, 알파 브라우저 테스트를 실행한다. **Actions → Test and deploy**에서 검사한 커밋, 임시로 허용한 테스트 실패, 웹 EC2가 보낸 배포 결과를 확인할 수 있다.

## 처음 배포할 때

1. **PR 검토와 첫 머지** — GitHub Actions의 저장소 변수 `SKN33_DEPLOY_MODE`를 비워 두고, 조직에서 같은 이름의 변수를 상속하는지도 확인한다. 값이 없으면 PR과 첫 `main` 머지에서는 테스트와 빌드만 실행한다.
2. **AWS 준비** — 머지 후 AWS OIDC 역할, 비공개 S3 버킷의 배포 권한, EC2에서 배포 요청을 처리하는 수신기를 설정한다.
3. **첫 수동 배포** — `SKN33_DEPLOY_MODE`를 `manual`로 설정한다. **Actions → Test and deploy → Run workflow**에서 `main`을 선택해 실행한다. 웹 EC2가 배포 성공을 보고했는지, 서비스가 정상인지, EC2에 적용된 커밋이 Actions에서 검사한 커밋과 일치하는지 확인한다.
4. **자동 배포** — 첫 배포를 확인한 뒤 값을 `auto`로 바꾼다. 이후 `main`에 푸시한 커밋이 검사를 통과하면 자동으로 배포된다. 새 배포를 일시 중지하려면 `off`로 바꾼다. 테스트와 빌드는 계속 실행된다.

## 기존 테스트 실패를 처리하는 기준

[`known-failures.json`](known-failures.json)에 등록된 테스트도 매번 실행한다. 전체 테스트 ID, 예외 유형, 오류 메시지의 첫 줄, 실패한 검증문이 **모두** 기록과 일치할 때만 실패를 임시로 허용한다. 목록에 있는 테스트가 통과해도 CI는 정상 진행한다. 원인을 수정하면 해당 항목을 삭제한다. 목록이 빈 객체(`{}`)가 되면 테스트 실패가 한 건만 발생해도 배포를 차단한다.

후속 작업(#99): HS 검색 4건(후보 없음·신뢰도·임베딩 재시도·HS4/HS6 노출)과 모델 A 순위 점수의 부동소수점 오차 1건을 각각 재현하고, 수정 PR에서 해당 예외를 함께 삭제한다. 파일에 없는 실패나 실패 내용의 변화는 계속 CI를 막는다.

수신기 테스트(`test_receive.py`)는 Linux의 `fcntl` 잠금을 사용한다. Ubuntu CI·WSL에서 `python -m unittest discover -s deploy/ci -p 'test_*.py'`로 실행한다. 임시 디렉터리와 모의 S3·systemd만 사용하므로 운영 EC2는 필요 없다. CI의 PDF 테스트에는 `API_REPORT_FONT`로 설치된 한글 글꼴을 전달한다.

## 수신기 설치 및 Python 환경 변경

웹에서는 `trade-release.service`, ML에서는 학습 유닛의 드롭인 `20-release.conf`가 각 호스트의 `/etc/trade/release-runtime.env`를 읽는다. 설치 전 root 소유·일반 사용자 쓰기 금지로 파일을 만들고 **해당 서버의 실제 실행 파일 절대 경로**를 지정한다.

- 웹: `TRADE_API_PYTHON=/실제/API/가상환경/bin/python` (운영 API 유닛과 같은 인터프리터)
- ML: `TRADE_ML_PYTHON=/실제/ML/가상환경/bin/python` (운영 재학습과 같은 인터프리터)

웹 수신기와 수집기는 기존 `/opt/trade/venv/bin/python`을 사용한다. ML에서는 두 Python의 역할이 다르다. `20-release.conf`의 `ExecStartPre`는 **수신기 전용 Python**으로 S3의 승인된 코드를 가져온다. `TRADE_ML_PYTHON`은 **학습용 Python**으로, 수신기가 새 코드를 실제 학습 환경에서 검사할 때 사용한다.

웹 수신기(`trade-release.service`)는 root로 실행된다. 웹에 설치하거나 패키지를 갱신할 때는 가상환경과 내부 패키지, Python 실행 파일, 그 상위 경로를 root가 소유하고 일반 사용자가 수정할 수 없는지 확인한다. 운영 웹 EC2의 가상환경은 `root:trade 750`, 실제 Python 실행 파일은 `root:root 755`다. `trade`는 기존 환경에서 수집기를 실행하고, 패키지 설치·갱신은 관리자가 수행한다.

```bash
cd /
sudo stat -Lc '%U:%G %a %n' /opt/trade/venv /opt/trade/venv/bin/python
sudo namei -l /opt/trade/venv/bin/python
sudo find -L /opt/trade/venv \( ! -user root -o -perm /022 \) -print -quit
sudo -u trade find -L /opt/trade/venv -writable -print -quit
```

마지막 두 명령은 모두 오류나 출력 없이 끝나야 한다. 경로가 나오면 수신기를 활성화하기 전에 해당 파일·디렉터리의 소유권과 쓰기 권한을 바로잡는다.

ML EC2를 처음 설치할 때는 Python 3.11과 `venv`를 준비하고, 드롭인을 설치하기 전에 아래 명령을 실행한다. 수신기는 관리자 권한으로 실행되므로 `/opt/trade/delivery`와 전용 가상환경은 root가 소유하고 일반 사용자가 수정할 수 없어야 한다.

```bash
sudo install -d -o root -g root -m 0755 /opt/trade/delivery
sudo /usr/bin/python3.11 -m venv /opt/trade/delivery/venv
sudo /opt/trade/delivery/venv/bin/python -m pip install 'boto3==1.43.125'
/opt/trade/delivery/venv/bin/python -m pip check
/opt/trade/delivery/venv/bin/python -I -c "import boto3; print(boto3.__version__)"
```

수신기와 유닛을 함께 설치하고 유닛 변경 후 `sudo systemctl daemon-reload`를 실행한다. ML에서는 `systemctl cat trade-train.service`로 `ExecStartPre`가 위 전용 Python을 가리키는지 확인한다. 이 확인에는 재학습을 시작할 필요가 없다. API·ML 학습 런타임을 교체할 때는 `release-runtime.env`도 갱신하고 `sudo -u trade /실제/경로/bin/python --version`으로 실행 권한을 확인한다. 경로만 바꿀 때는 수신기를 재설치할 필요가 없다. 경로 누락·오류는 서비스 전환 전에 `runtime configuration` 단계에서 보고된다.

## 웹 EC2에 적용되는 순서

1. GitHub Actions가 OIDC로 AWS 임시 자격 증명을 받아 비공개 S3 버킷의 `delivery/` 경로에 배포 압축 파일과 유효 시간 20분인 요청을 올린다.
2. 웹 EC2 수신기는 1분마다 요청을 확인한다. 압축 파일의 해시와 소스 파일 목록을 검증한 뒤, 운영 서비스가 쓰는 Python 환경에서 새 코드를 검사한다. 필수 모델 A와 레지스트리를 읽어 표본 예측을 조회하고, 활성 RAG 기사 색인에 실제 쿼리를 보낸다. DB 마이그레이션 계획도 확인한다. 선택 모델 B의 누락은 경고로 표시한다. 런타임 업그레이드나 DB 마이그레이션이 필요하면 운영자가 배포 전에 준비한다.
3. 진행 중인 채팅·리포트·수집 작업이 끝나면 변경할 파일을 백업하고 새 버전을 적용한 뒤 API·워커·스케줄러를 재시작한다. 하루가 지난 배치도 DB에 `RUNNING`으로 남아 있으면 배포를 보류한다. 요청이 만료되면 배치 상태를 확인한 뒤 다시 배포한다. Redis 데이터, 운영 데이터 디렉터리, 비밀값은 배포 패키지에서 제외한다.
4. EC2의 준비 상태 확인 엔드포인트로 DB 스키마와 Redis 연결을 확인하고, API·워커·스케줄러·Nginx가 **각각** 실행 중인지 검사한다. 이 단계에서 실패하면 이전 소스와 프론트엔드를 복원한다.
5. 웹 EC2가 성공을 보고하면 Actions가 공개 HTTPS 홈페이지를 내려받아 이번에 빌드한 `index.html`과 비교한다. 이 외부 점검은 서버 전환 후에 실행되며, 실패하면 Actions에 별도로 표시된다.

웹 빌드의 `assets/` 파일은 현재 빌드와 직전 빌드까지 보존한다. 다음 전환에서 두 빌드 모두 참조하지 않고 기록한 체크섬과 일치하는 파일만 백업 후 삭제한다. 전환 실패 시 삭제한 파일과 자산 목록도 복원한다. 첫 설치 전부터 서버에 있던 미기록 파일, 운영자가 수정한 옛 파일, `assets/` 밖의 파일은 자동 청소 대상이 아니다.

## ML EC2에 적용되는 시점

웹 배포 검증을 통과하면 수신기가 승인한 버전을 S3에 기록한다. ML EC2는 다음 예약 기동 때 그 버전을 적용한 뒤 재학습을 시작한다. 기동 시 배포 검사에 실패하면 학습을 중단한다. 따라서 Actions의 배포 성공은 **웹 EC2에 적용된 결과**를 뜻하며, ML EC2의 적용 시점은 다음 예약 기동이다.

## 실패 확인과 복구

- **배포 실패 또는 시간 초과** — Actions에서 해당 커밋과 실패 단계를 확인한다. 시간 초과는 정해진 시간 안에 EC2의 결과를 받지 못했다는 뜻이다. 웹 EC2에서 `journalctl -u trade-release.service`로 수신기 로그를 보고, 배포 기록(`/opt/trade/deployed-main.json`)과 EC2가 보낸 결과를 대조한다.
- **수신기 업데이트** — 관리자 권한으로 실행되는 수신기와 systemd 유닛은 애플리케이션과 별도로 설치한다. `deploy/ci` 변경 사항을 검토한 뒤 해당 서버에 검토한 버전을 설치한다.
- **롤백까지 실패하거나 롤백 도중 수신기가 중단된 경우** — `transaction.json`이 남고 자동 복구는 멈춘다. 웹 Actions의 `rollback` 결과에는 원래 실패 단계와 오류 유형도 표시된다. ML은 복구 전 학습을 시작하지 않는다. 웹에서는 `sudo systemctl stop trade-release.timer`로 타이머를 일시 중지한다. 수신기 로그·트랜잭션의 백업 경로·서비스 상태를 확인하고 원인을 해결한 뒤 **해당 서버에서만** 아래 복원 명령을 한 번 실행한다.

  | 서버 | 복원 명령 |
  | --- | --- |
  | 웹 | `sudo /opt/trade/venv/bin/python -I /opt/trade/delivery/receive.py retry-rollback` |
  | ML | `sudo /opt/trade/delivery/venv/bin/python -I /opt/trade/delivery/receive.py retry-rollback` |

  실패하면 계속 보류되며, 성공하면 트랜잭션 파일이 자동으로 사라진다. 파일을 직접 삭제하지 않는다. 웹 복원 시에는 복원한 커밋과 서비스 상태를 확인한 뒤 타이머를 다시 시작하고 실패했던 웹 배포는 Actions에서 새 실행으로 요청한다.
- **백업과 코드 복구** — 현재 소스 목록(`/opt/trade/delivery/baseline.json`), 같은 커밋의 배포 기록(`/opt/trade/deployed-main.json`), 복원용 파일을 함께 백업한다. Git에서 이전 코드로 되돌릴 때는 `git revert`로 되돌림 커밋을 만들고 `main`에 반영해 같은 검증과 배포 절차를 거친다.
