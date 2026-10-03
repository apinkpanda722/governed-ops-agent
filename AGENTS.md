# 프로젝트: {프로젝트명}

> 이 파일이 에이전트 규칙의 단일 원본이다. Codex는 AGENTS.md를, Claude Code는 이 파일을 import하는 CLAUDE.md를 읽는다.

## 기술 스택
- {언어/런타임}
- {프레임워크·핵심 라이브러리}
- {테스트 도구}

## 아키텍처 규칙
- CRITICAL: {절대 지켜야 할 규칙 1 (예: 외부 API 호출은 한 모듈의 래퍼를 통해서만 한다)}
- CRITICAL: {절대 지켜야 할 규칙 2 (예: 비밀 값은 .env 에서만 읽고 코드·로그에 남기지 않는다)}
- {일반 규칙 (예: 도메인 로직과 입출력 계층을 분리한다)}

## 개발 프로세스
- CRITICAL: 새 기능 구현 시 반드시 테스트를 먼저 작성하고, 테스트가 통과하는 구현을 작성할 것 (TDD)
- CRITICAL: `make verify` 가 유일한 검증 진입점이다. 검증 항목을 추가할 때는 Makefile 의 verify 타깃에 넣는다.
- 커밋 메시지는 conventional commits 형식을 따를 것 (feat:, fix:, docs:, refactor:)

## 명령어
make verify      # lint + test (Stop hook, execute.py, CI가 모두 이 명령을 부른다)
{프로젝트 전용 명령 (예: 개발 서버, 평가 실행)}
