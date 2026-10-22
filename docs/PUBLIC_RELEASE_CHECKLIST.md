# 공개 전 체크리스트

[M7 승인 설계 5절](M7_DESIGN_DRAFT.md#5-공개-전-점검-목록)의 C1~C13을 옮겼다. **모든 검사 명령·설치·공개 전환은 사용자 실행 항목**이다. 체크한 항목은 2026-10-21에 실제로 확인된 것만이며 각 항목 아래에 근거를 한 줄로 적는다. 체크하지 않은 항목은 미확인이다. 출력은 비밀 값이 아닌 건수·줄 번호·경로만 확인하고, 스캐너가 원문을 보여 주면 저장·공유하지 않는다. `.env`·토큰 파일·개인키를 직접 열거나 출력하지 않는다.

설계 단계의 경로 기준 추적·이력 검사는 값 기준 비밀 검사와 다르다. 커밋 이력 전체와 현재 작업 트리를 모두 확인한다. 평가 정답지 공개는 유지하며 이력은 재작성하지 않는다. 공개 신원은 사용자가 결정한다.

- [x] **C1 — 이력 비밀 값 검사(사용자 실행).** `gitleaks` 또는 `trufflehog`로 전체 이력을 검사한다. 합격: 비밀 값 발견 없음. 도구 설치도 사용자가 한다.
  - 근거(2026-10-21): 사용자가 `gitleaks`로 전체 이력(735 commits, 약 24.47 MB)을 검사했다. 1415건이 걸렸고 전부 `generic-api-key`(엔트로피 규칙)였으며, 공급사별 규칙(AWS·OpenAI·Anthropic·GitHub 토큰, 개인키 등)에는 0건이었다. 어드바이저가 걸린 문자열의 형태를 값 출력 없이 분류한 결과 1415건 전부 64자리 16진수(SHA-256 해시)였다(평가 결과 파일 `data/results` 1124, LLM 재생 기록 214, 목 토큰 해시 76, 동결 해시 테스트 1). 보고서는 확인 뒤 삭제했다.
  - 보완 근거: `.env`의 비밀 후보 값 29개를 메모리에서만 읽어 전체 이력과 대조했다(값 미출력). 이력에서 발견된 것은 URL 변수 4개(`KODOCS_MCP_URL`·`LITELLM_BASE_URL`·`MOCK_JIRA_URL`·`MOCK_SLACK_URL`)뿐이고 모두 127.0.0.1 로컬 주소이며 계정 정보가 없다. API 키·토큰·비밀번호·서명 키는 이력에 없다.
  - 한계: 해시와 같은 형태의 키는 `gitleaks`가 구분하지 못하므로 `.env` 값 대조로 보완했다.

  ```bash
  gitleaks detect --source . --log-opts="--all" --redact
  ```

  대체 명령(사용자 실행):

  ```bash
  trufflehog git file://. --only-verified
  ```

- [x] **C2 — 형식 직접 검사(사용자 실행).** 통과(2026-10-21): 이력의 형식 일치 4건은 모두 무해하고(체크리스트 문서의 검색식 2건, 키 파일 헤더 확인 코드 1건, 합성 테스트 토큰 1건), C1의 `gitleaks` 공급사별 규칙 일치는 0건이다. 공급사 키·Bearer·개인키·관측 키 형식을 이력에서 찾되 화면에는 일치 줄 번호만 출력한다. 합성 토큰 여부는 별도로 판단하며 실제 비밀 값 발견이 없어야 한다.

  ```bash
  git log --all -p | grep -nEi '(sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|Authorization: *Bearer [A-Za-z0-9._-]{16,}|BEGIN [A-Z ]*PRIVATE KEY|lsv2_|pk-lf-|sk-lf-)' | cut -d: -f1
  ```

- [x] **C3 — 커밋 금지 경로 이력(사용자 실행).** 경로만 확인한다. `.env.example`과 공개키 `data/seed/audit_public_key.pem`은 허용하며 `.env`·raw·interim·토큰·개인키 이력이 없어야 한다.
  - 근거(2026-10-21, 어드바이저가 아래 명령을 읽기 전용으로 실행): 이력에 `.env`·`data/raw`·`data/interim`·`tokens.json`·개인키가 없고 허용 두 파일(`.env.example`, `data/seed/audit_public_key.pem`)만 나왔다.

  ```bash
  git log --all --name-only --format= | sort -u | grep -E '^(\.env(\.example)?$|data/(raw|interim)/|.*tokens\.json$|.*\.pem$)'
  ```

- [x] **C4 — 대용량 이력(사용자 실행).** 아래에서 나온 파일이 의도한 자료인지 확인한다. 평가·비용 원장을 크기만으로 삭제하거나 이력을 재작성하지 않는다.
  - 근거(2026-10-21): 1MB 초과 blob은 `data/manifest/costs.jsonl`의 이전 버전들뿐이다(최대 약 3.6MB). 비용 원장이라 의도한 자료이며 삭제·재작성하지 않는다.

  ```bash
  git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '$1=="blob"&&$3>1000000' | sort -k3 -n -r
  ```

- [x] **C5 — 합성 자료만 포함(사용자 실행).** 합성·재생 검사 테스트를 포함한 `make verify`가 통과해야 한다. `data/replay`·`data/seed`의 재배포 문서가 `syn-*` 합성 문서뿐인지 기존 검사로 확인하고 실제 규정 원문이 포함되지 않았는지 점검한다. 접근 인덱스의 비합성 문서 ID 참조와 본문 재배포는 구분한다.
  - 근거(2026-10-22): 새 클론(C11, `.env`·`data/interim` 없음)에서 `make verify`가 통과했다(사용자 보고). #259 이후 로컬 `make verify`도 두 경우에서 5527 passed·9 skipped로 통과했다(A 원본 HEAD `f0c56b9`, A 기준 커밋 `d2eec9c` 복제본).

  ```bash
  make verify
  ```

- [x] **C6 — 실존 기관·인물명(사용자 실행).** 기존 합성 검사 테스트와 사용자가 준비한 실제 기관·인물명 목록으로 검색한다. 합성 티켓·메시지·코멘트에 실존 이름이 없어야 한다. 검색은 사용자 목록에 한정하고 발견한 원문을 보고서에 복사하지 않는다.
  - 근거(2026-10-21, 사용자 보고): 사용자가 준비한 이름 목록 8개(전 직장·그 프로젝트·본명·이메일 식별자 범주)로 `docs`·`src`·`tests`·`scripts`·`web/src`·`data/seed`·`data/replay`를 검색해 0건이었다. 이름은 적지 않는다.
  - 한계: 목록에 없는 이름은 확인하지 못했고, `data/results` 등 다른 폴더는 검색 범위 밖이다.

- [x] **C7 — 공개 신원 결정(사용자 실행).** 작성자 이름·이메일과 원격 계정을 사용자가 확인하고 공개할 신원을 결정한다. 이력 재작성은 하지 않는다.

  ```bash
  git log --all --format='%an <%ae>' | sort -u
  ```

  원격 계정 확인(사용자 실행, 자격 증명이 포함된 URL은 출력·공유하지 않음): 저장소 설정의 계정을 확인한다. 공개 신원 결정(사용자, 2026-10-21): 저작권자와 이후 커밋 작성자 이름은 Tommy Lee(GitHub 프로필 이름과 같음), 작성자 이메일은 gmail 도메인이다. 이력은 다시 쓰지 않는다(결과 문서가 인용한 커밋 해시 보존). 과거 머지 커밋(B 133개, A 25개)에는 GitHub 계정의 옛 Primary 이메일(liveuou.kr 도메인)이 남아 있으며 사용자가 이 노출을 허용했다. 사용자는 GitHub 계정의 Primary를 gmail로 바꾸고 옛 주소를 삭제했다(사용자 보고). A 저장소 이력에 있는 전 직장 도메인 이메일 5개 커밋도 사용자가 허용했다(이력을 다시 쓰면 B가 고정한 A 커밋 `d2eec9c`와 공식 결과의 출처가 깨지기 때문).

- [x] **C8 — 개인 경로·내부 URL(사용자 실행).** 우선 파일 이름만 찾고 A 참조 경로를 포함해 공개 의도를 확인한다. 내부 URL도 사용자가 정한 호스트 목록으로 같은 방식으로 검사한다.
  - 근거(2026-10-21): 걸린 파일 3개 중 2개(`docs/PUBLIC_RELEASE_CHECKLIST.md`, `docs/M7_DESIGN_DRAFT.md`)는 검색식을 설명하는 문장이고, `docs/results/hypotheses-m6-redteam-addendum.md` 10행은 검수자 사용자 ID 한 줄이다. 사용자 판단으로 허용(등록 문서라 수정하지 않음). 내부 URL 호스트 목록 검사는 미확인이다.

  ```bash
  rg -l '/Users/|taserlee' docs src tests scripts web/src
  ```

- [x] **C9 — `.env.example`(사용자 실행).** 주석과 변수 이름만 있고 모든 할당값이 비어 있는지 눈으로 확인한다. `.env`를 열거나 출력하지 않는다. 팀 키·세션·API 키 값과 Authorization 헤더가 예제에 없어야 한다.
  - 근거(2026-10-21): 값이 있는 할당 줄 0개(어드바이저 확인, `grep -cE '^[A-Z][A-Z0-9_]*=.+' .env.example` → 0). `tests/test_repo_rules.py`도 빈 할당만 허용한다.

- [x] **C10 — CI 시크릿(사용자 실행).** `.github/workflows/ci.yml`과 저장소 Secrets 설정을 확인한다. CI는 시크릿·실제 A·외부 API 없이 mock·replay로 통과해야 한다. 시크릿 값을 출력하지 않는다.
  - 근거(2026-10-21): `.github/workflows/ci.yml`의 `secrets.` 참조 0개. 저장소 Secrets 설정 화면은 미확인(사용자 몫).

- [x] **C11 — 새 클론 검증(사용자 실행).** 별도 디렉터리에 클론한 뒤 고정 의존성을 설치하고 `.env`·`data/interim` 없이 `make verify`가 통과하는지 확인한다. 실제 서비스 호출은 하지 않는다.
  - 근거(2026-10-21, 사용자 보고): 새 클론(`gh repo clone`, `.env`·`data/interim` 없음)에서 `uv sync --frozen`, `pnpm -C web install --frozen-lockfile`, `make verify`를 실행했고 마지막 단계까지 완료했다(web 33 files·240 tests passed, `playwright test --list` 4 tests). 종료 코드는 사용자가 따로 확인하지 않았다.

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

- [x] **C12 — 라이선스(사용자 실행).** [LICENSE](../LICENSE)에 MIT 전문이 있고 [README](../README.md)가 링크하는지 확인한다. `[저작권자]` 자리표시자를 공개 신원 결정에 맞춰 사용자가 채운다.
  - 근거(2026-10-21): `LICENSE` 3행을 `Copyright (c) 2026 Tommy Lee`로 채웠고, README의 라이선스 절이 `[MIT License](LICENSE)`로 링크하며 저작권자를 같은 이름으로 적는다.

- [x] **C13 — A 저장소 쪽 확인(사용자 실행, A에서 별도 처리).** A도 같은 MIT 라이선스로 공개 예정이다. A의 `LICENSE`, C1~C4와 같은 비밀·금지 경로·대용량 이력 검사를 A 쪽에서 확인한다. A 합성 자료의 재배포 허용 범위가 B의 `data/contract`·`data/replay/a_run`·`data/eval`을 포함하는지 확인하고, A README가 B를 참조하는지 점검한다. 이 step에서 A를 수정하지 않는다.
  - 근거(2026-10-21~22, 어드바이저의 읽기 전용 확인과 사용자 실행):
    - C1: `gitleaks`로 364 commits를 검사해 1556건이 걸렸고 전부 `generic-api-key`였다. 형태는 32·40·64자리 16진수뿐이고 공급사별 규칙은 0건이다. A의 MCP 토큰 38개는 이력에 없다(값 미출력, 메모리 대조).
    - C2: 형식 일치 0건. C3: `.env.example`만. C4: 비용 원장의 이전 버전만.
    - C6: 사용자 목록 검색 결과 4건은 A가 2026-10-03에 한 공개 준비 감사 기록의 도메인 이름 언급이며 합성 자료 오염이 아니다.
    - C8: 검수 라벨의 검수자 ID와 경로 한 줄 수준으로 허용했다.
    - C9: `MONTHLY_BUDGET_USD=45`가 있으나 비밀이 아닌 예산 기본값이라 허용했다.
    - C10: `secrets.` 참조 0개.
    - C11: 새 클론에서 `make verify`가 2278 passed·3 skipped로 통과했다(사용자 보고).
    - C12: A PR #52로 `LICENSE`(MIT, Tommy Lee)를 추가했고 README가 링크한다.

## 공개 자료의 관계

- B를 공개하면 A도 함께 공개해야 링크가 이어진다.
- A는 기준 커밋 `d2eec9c`에서 B의 평가가 재현된다.
- A의 합성 원장(`data/synthetic/ledger.sqlite`)은 A에서 gitignore라 새 클론에서는 A 실데이터 테스트(`tests/test_eval_meta_real_a.py`)가 건너뛴다.

B는 A의 고정 커밋·계약 스냅샷·합성 문서 메타데이터와 응답 재생 자료를 참조한다. live에만 A가 필요하고 `make verify`는 replay로 실행한다. A·B의 공개 라이선스와 합성 자료 재배포 조건을 함께 확인한다.

평가 정답지(`data/eval`)는 공식 test 동결 뒤 공개한다. 이후 비교에는 새 held-out 세트가 필요하다. README·블로그·영상이 이 한계와 mock 시뮬레이션·replay 여부를 명시하는지 확인한다. 영상 링크와 리허설 결과는 촬영 후 실제 값으로 채우고, PASS·FAIL·SKIP을 구별해 기록한다.
