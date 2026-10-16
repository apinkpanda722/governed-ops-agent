# 공개 전 체크리스트

[M7 승인 설계 5절](M7_DESIGN_DRAFT.md#5-공개-전-점검-목록)의 C1~C13을 옮겼다. **모든 검사 명령·설치·공개 전환은 사용자 실행 항목**이다. 이 문서는 검사 완료 보고가 아니며 아래 항목은 미확인 상태로 둔다. 출력은 비밀 값이 아닌 건수·줄 번호·경로만 확인하고, 스캐너가 원문을 보여 주면 저장·공유하지 않는다. `.env`·토큰 파일·개인키를 직접 열거나 출력하지 않는다.

설계 단계의 경로 기준 추적·이력 검사는 값 기준 비밀 검사와 다르다. 커밋 이력 전체와 현재 작업 트리를 모두 확인한다. 평가 정답지 공개는 유지하며 이력은 재작성하지 않는다. 공개 신원은 사용자가 결정한다.

- [ ] **C1 — 이력 비밀 값 검사(사용자 실행).** `gitleaks` 또는 `trufflehog`로 전체 이력을 검사한다. 합격: 비밀 값 발견 없음. 도구 설치도 사용자가 한다.

  ```bash
  gitleaks detect --source . --log-opts="--all" --redact
  ```

  대체 명령(사용자 실행):

  ```bash
  trufflehog git file://. --only-verified
  ```

- [ ] **C2 — 형식 직접 검사(사용자 실행).** 공급사 키·Bearer·개인키·관측 키 형식을 이력에서 찾되 화면에는 일치 줄 번호만 출력한다. 합성 토큰 여부는 별도로 판단하며 실제 비밀 값 발견이 없어야 한다.

  ```bash
  git log --all -p | grep -nEi '(sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|Authorization: *Bearer [A-Za-z0-9._-]{16,}|BEGIN [A-Z ]*PRIVATE KEY|lsv2_|pk-lf-|sk-lf-)' | cut -d: -f1
  ```

- [ ] **C3 — 커밋 금지 경로 이력(사용자 실행).** 경로만 확인한다. `.env.example`과 공개키 `data/seed/audit_public_key.pem`은 허용하며 `.env`·raw·interim·토큰·개인키 이력이 없어야 한다.

  ```bash
  git log --all --name-only --format= | sort -u | grep -E '^(\.env(\.example)?$|data/(raw|interim)/|.*tokens\.json$|.*\.pem$)'
  ```

- [ ] **C4 — 대용량 이력(사용자 실행).** 아래에서 나온 파일이 의도한 자료인지 확인한다. 평가·비용 원장을 크기만으로 삭제하거나 이력을 재작성하지 않는다.

  ```bash
  git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '$1=="blob"&&$3>1000000' | sort -k3 -n -r
  ```

- [ ] **C5 — 합성 자료만 포함(사용자 실행).** 합성·재생 검사 테스트를 포함한 `make verify`가 통과해야 한다. `data/replay`·`data/seed`의 재배포 문서가 `syn-*` 합성 문서뿐인지 기존 검사로 확인하고 실제 규정 원문이 포함되지 않았는지 점검한다. 접근 인덱스의 비합성 문서 ID 참조와 본문 재배포는 구분한다.

  ```bash
  make verify
  ```

- [ ] **C6 — 실존 기관·인물명(사용자 실행).** 기존 합성 검사 테스트와 사용자가 준비한 실제 기관·인물명 목록으로 검색한다. 합성 티켓·메시지·코멘트에 실존 이름이 없어야 한다. 검색은 사용자 목록에 한정하고 발견한 원문을 보고서에 복사하지 않는다.

- [ ] **C7 — 공개 신원 결정(사용자 실행).** 작성자 이름·이메일과 원격 계정을 사용자가 확인하고 공개할 신원을 결정한다. 이력 재작성은 하지 않는다.

  ```bash
  git log --all --format='%an <%ae>' | sort -u
  ```

  원격 계정 확인(사용자 실행, 자격 증명이 포함된 URL은 출력·공유하지 않음): 저장소 설정의 계정을 확인한다. 공개 신원 결정: (사용자 기록).

- [ ] **C8 — 개인 경로·내부 URL(사용자 실행).** 우선 파일 이름만 찾고 A 참조 경로를 포함해 공개 의도를 확인한다. 내부 URL도 사용자가 정한 호스트 목록으로 같은 방식으로 검사한다.

  ```bash
  rg -l '/Users/|taserlee' docs src tests scripts web/src
  ```

- [ ] **C9 — `.env.example`(사용자 실행).** 주석과 변수 이름만 있고 모든 할당값이 비어 있는지 눈으로 확인한다. `.env`를 열거나 출력하지 않는다. 팀 키·세션·API 키 값과 Authorization 헤더가 예제에 없어야 한다.

- [ ] **C10 — CI 시크릿(사용자 실행).** `.github/workflows/ci.yml`과 저장소 Secrets 설정을 확인한다. CI는 시크릿·실제 A·외부 API 없이 mock·replay로 통과해야 한다. 시크릿 값을 출력하지 않는다.

- [ ] **C11 — 새 클론 검증(사용자 실행).** 별도 디렉터리에 클론한 뒤 고정 의존성을 설치하고 `.env`·`data/interim` 없이 `make verify`가 통과하는지 확인한다. 실제 서비스 호출은 하지 않는다.

  ```bash
  git clone https://github.com/apinkpanda722/governed-ops-agent.git governed-ops-agent-public-check
  ```

  ```bash
  cd governed-ops-agent-public-check
  ```

  ```bash
  uv sync --frozen
  ```

  ```bash
  pnpm -C web install --frozen-lockfile
  ```

  ```bash
  make verify
  ```

- [ ] **C12 — 라이선스(사용자 실행).** [LICENSE](../LICENSE)에 MIT 전문이 있고 [README](../README.md)가 링크하는지 확인한다. `[저작권자]` 자리표시자를 공개 신원 결정에 맞춰 사용자가 채운다.

- [ ] **C13 — A 저장소 쪽 확인(사용자 실행, A에서 별도 처리).** A도 같은 MIT 라이선스로 공개 예정이다. A의 `LICENSE`, C1~C4와 같은 비밀·금지 경로·대용량 이력 검사를 A 쪽에서 확인한다. A 합성 자료의 재배포 허용 범위가 B의 `data/contract`·`data/replay/a_run`·`data/eval`을 포함하는지 확인하고, A README가 B를 참조하는지 점검한다. 이 step에서 A를 수정하지 않는다.

## 공개 자료의 관계

B는 A의 고정 커밋·계약 스냅샷·합성 문서 메타데이터와 응답 재생 자료를 참조한다. live에만 A가 필요하고 `make verify`는 replay로 실행한다. A·B의 공개 라이선스와 합성 자료 재배포 조건을 함께 확인한다.

평가 정답지(`data/eval`)는 공식 test 동결 뒤 공개한다. 이후 비교에는 새 held-out 세트가 필요하다. README·블로그·영상이 이 한계와 mock 시뮬레이션·replay 여부를 명시하는지 확인한다. 영상 링크와 리허설 결과는 촬영 후 실제 값으로 채우고, PASS·FAIL·SKIP을 구별해 기록한다.
