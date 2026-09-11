# Local OAuth Onboarding Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task in the current worktree. Steps use checkbox syntax for tracking. Do not spawn agents unless explicitly authorized.

**Goal:** 설치한 `zendesk` CLI가 브라우저 OAuth 인증을 자동 완료하고 MCP와 같은 연결 정보를 사용하게 한다.

**Architecture:** 기존 인증·토큰 저장·refresh 경로를 확장하고 작은 loopback 로그인 모듈을 추가한다. 로그인과 stdio 서버는 같은 설정 선택 규칙과 저장 파일을 사용한다. 기본 배포는 uv tool install, 최초 인증은 Public OAuth + PKCE다.

**Tech Stack:** Python 3.10–3.12, stdlib http.server/webbrowser/hashlib/secrets/argparse, 기존 httpx·pytest·MCP SDK.

**Spec:** `docs/superpowers/specs/2026-09-11-local-oauth-onboarding.md` 전체.

**Implementation status (2026-09-11):** 로컬 기능 구현·패키지 설치·MCP handshake 완료. 실제 Zendesk Public OAuth E2E와 원격 CI는 외부 client 등록 및 push 이후 검증 대기.

## Global Constraints

- 기본 읽기 전용, API token·Confidential OAuth·수동 HTTPS 인증 호환성 유지.
- 새 런타임 의존성 및 상시 운영 서버 없음.
- 콜백 바인딩은 127.0.0.1, 기본 포트 3000, 경로 /oauth/callback, 대기 180초.
- 명시적 인증 환경변수와 저장 연결을 섞지 않음.
- 구현과 실제 Zendesk E2E·push·publication은 별도 증거로 기록.
- 작업 경로: `/Users/onam/workspace/zendesk-mcp-server-kon/.worktrees/unified-zendesk-mcp-core`.

## Task 1: Public OAuth 및 PKCE 계약

**Files:** modify `src/zendesk_mcp_server/config.py`, `src/zendesk_mcp_server/auth.py`; tests `tests/test_config.py`, `tests/test_auth.py`.

**Interfaces:** OAuthConfig에 client_kind(public/confidential)를 추가한다. 기존 호출은 confidential 기본값 유지. `oauth_refresh_payload(client_id, client_secret, refresh_token)`는 Public의 빈 secret을 생략한다. 코드 교환·요청 생성 함수에는 선택 keyword `code_verifier`를 추가하며 Public은 반드시 필요하다. `pkce_challenge(verifier: str) -> str`을 auth.py에 둔다.

- [x] 아래 계약 테스트를 추가하고 `uv run pytest tests/test_auth.py tests/test_config.py -q`로 실패 확인.

```python
def test_pkce_rfc7636_vector():
    from zendesk_mcp_server.auth import pkce_challenge
    assert pkce_challenge('dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk') == 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM'

def test_public_refresh_omits_secret():
    from zendesk_mcp_server.auth import oauth_refresh_payload
    assert oauth_refresh_payload('client', '', 'refresh') == {
        'grant_type': 'refresh_token', 'client_id': 'client', 'refresh_token': 'refresh'}
```

- [x] S256 계산을 추가: `base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')`. 요청에는 challenge/method, 교환에는 verifier를 넣는다. Confidential 기존 payload는 보존한다.
- [x] Public 설정에서 secret 생략 허용, Confidential에서 누락 거부를 테스트한다. verifier 길이 43–128 및 허용 문자 검증, 임의 HTTP redirect 거부, loopback 예외 허용을 추가한다.
- [x] 같은 focused suite 통과 후 diff 검토, `feat: support public OAuth with PKCE` 커밋.

## Task 2: 로그인·MCP의 연결 저장 경로 통합

**Files:** modify `config.py`, `auth.py`, `server.py`; tests `test_config.py`, `test_auth.py`, `test_server.py`.

**Interfaces:** `saved_connection_path() -> Path`는 spec의 기본 경로를 반환한다. `Settings.load`는 인증 env가 없는 경우에만 이 파일의 identity/토큰 경로를 선택한다. `OAuthTokenStore.save`는 기존 파일의 identity metadata를 보존하며 refresh한다. `save_connection(settings: Settings, tokens: OAuthTokens) -> None`은 identity와 token을 하나의 원자적 파일로 기록한다. 저장 scope와 요청 scope 불일치는 재로그인 오류다.

- [x] 테스트에서 `monkeypatch.setattr(Path, 'home', lambda: tmp_path)` 사용. 저장 연결 생성 후 `Settings.load({})`가 동일 tenant와 OAuth를 선택함을 검증한다. `Settings.load({'ZENDESK_SUBDOMAIN': 'other'})`가 기존 토큰을 선택하지 않음도 검증한다.
- [x] `uv run pytest tests/test_config.py tests/test_auth.py tests/test_server.py -q`로 red 확인.
- [x] 기존 NamedTemporaryFile·0600·replace·refresh_lock을 재사용하여 metadata 보존 저장을 구현한다. 단일 연결 파일 읽기는 schema/type 필드 검증을 먼저 한다. 손상 파일은 명확한 오류이며 API token으로 fallback하지 않는다.
- [ ] 토큰 회전 뒤 metadata 보존, 기존 legacy token 파일 호환, 환경변수 경로 격리, scope 확대 거부, POSIX 권한 거부 및 Windows 권한 동작 검증을 추가한다.
- [x] focused suite 통과, `feat: share saved OAuth connection with MCP` 커밋.

## Task 3: 임시 콜백 로그인

**Files:** create `src/zendesk_mcp_server/login.py`, `tests/test_login.py`; reuse `auth.py`.

**Interfaces:** `login(settings: Settings, *, port: int = 3000, timeout: float = 180) -> None`. Public Settings는 CLI에서 생성한다. `HTTPServer`/`BaseHTTPRequestHandler`, `webbrowser.open`, 기존 token requester를 사용한다. 테스트는 browser open과 HTTP token/user 응답만 대체하고 실제 loopback socket을 통과한다.

- [x] 성공 통합 테스트를 작성한다. 가짜 browser 함수가 authorization URL의 state/challenge를 읽고 loopback으로 GET을 보낸다. 토큰 endpoint stub은 code_verifier와 secret 생략을 assert한다. 사용자 probe stub은 확인된 user를 반환한다. 완료 후 저장 파일 및 listener 종료를 assert한다.
- [x] `uv run pytest tests/test_login.py -q` 실행하여 red 확인.
- [x] 실행 순서를 `bind → state/verifier 생성 → browser open → callback 검증 → token 교환 → users/me → save_connection → close`로 구현한다. finally에서 listener close, monotonic deadline과 socket read timeout을 적용한다. query·token을 로깅하지 않는다. 사용자 probe는 새 토큰으로 수행하며 기존 연결 파일에 의존하지 않는다.
- [ ] wrong state 뒤 정상 callback 성공, 중복 query/code/error 거부, 잘못된 Host·path, 승인 거절, timeout, 포트 점유, browser open 실패, Ctrl-C, 교환/probe 실패 후 기존 파일 그대로 유지 테스트를 추가한다. loopback 밖에는 bind하지 않음을 assert한다.
- [x] focused suite 통과, `feat: complete OAuth through a temporary loopback callback` 커밋.

## Task 4: CLI와 설치 안내 연결

**Files:** modify `src/zendesk_mcp_server/__init__.py`, `README.md`, `README.ko.md`, `docs/migration.md`; create `tests/test_cli.py`; reuse `tests/test_approvals.py`.

**Interfaces:** `zendesk login --subdomain acme --client-id acme_mcp [--port 3000]`; `zendesk check --probe`; `zendesk --help`. 무인자 `zendesk`는 기존 stdio 서버 실행이다. 기존 check/approve/oauth-start/oauth-finish 인자는 유지한다.

- [x] CLI 테스트에서 argv와 login 함수를 monkeypatch하여 위 인자가 Settings와 port로 전달됨을 검증한다. 무인자 경로는 서버만 실행하고 browser/login을 호출하지 않음을 검증한다.
- [x] `uv run pytest tests/test_cli.py tests/test_approvals.py -q`로 red 확인.
- [x] argparse로 기존 분기를 정리한다. 로그인 Public Settings 생성에는 명시적 subdomain/client-id 및 read-only 정책을 사용한다. check --probe는 `build_connection_status(..., probe=True)` 공통 경로를 호출한다. 오류는 비밀값 없이 stderr, nonzero exit로 전달한다.
- [x] README를 관리자 OAuth client 등록 → uv/Git 준비 → 푸시된 커밋 기반 uv tool install → PATH 확인 → login → 절대 실행 경로로 MCP 등록 → check --probe 순서로 작성한다. 저장소의 실제 remote는 `https://github.com/nicekon/zendesk-mcp-server-kon`. 구현 커밋이 나오면 설치 URL의 ref에 해당 SHA를 사용하고, 아직 push되지 않았다면 공개 설치 가능하다고 안내하지 않는다.
- [x] 개발자용 uv sync/uv run과 사용자용 uv tool install을 구분한다. Public에는 secret이 없으며 API-token 기존 설정은 유지한다. 설치 해제와 Zendesk 인증 철회의 차이 및 저장 파일 경로를 설명한다. 클라이언트 전용 설정 문법은 해당 공식 문서와 실제 클라이언트로 확인 후 기재한다.
- [x] focused suite 통과, `feat: add login CLI and installation walkthrough` 커밋.

## Task 5: 설치부터 MCP까지 검증

**Files:** extend `tests/test_handshake.py`, `.github/workflows/ci.yml`, `docs/releasing.md`.

- [x] `uv run pytest -q` 및 `git diff --check` 실행.
- [x] `mktemp -d`로 새 build 출력·uv tool 저장·실행 디렉터리를 만들고, `uv build --out-dir`로 wheel을 생성한다. `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`을 이 임시 경로에만 지정하여 정확한 새 wheel을 설치한다. 사용자의 실제 설치·PATH를 변경하지 않는다.
- [ ] 설치된 절대 실행 파일로 `--help`, 무설정 check, 가짜 OAuth 성공 후 다른 cwd/인증 env 없는 새 프로세스에서 check를 검증한다. 테스트용 HOME은 자식 프로세스에만 격리한다. 실제 사용자 홈이나 token 파일을 읽지 않는다.
- [x] 같은 설치 실행 파일에 MCP initialize/tools/list를 수행한다. 등록 도구 집합은 변경 전과 동일하고 stdout에 로그인 출력이 없어야 한다. 기존 CI의 Linux Python 3.10–3.12와 macOS/Windows smoke에 해당 계약을 포함한다.
- [ ] 가능해진 시점에 실제 Zendesk Public client로 브라우저 승인, 사용자 읽기, 토큰 refresh, 새 프로세스 MCP 읽기를 검증한다. 외부 client가 없으면 로컬 구현 완료와 실계정 검증 대기를 분리해 기록한다.
- [x] 결과 기록 후 `test: verify installed OAuth onboarding and MCP startup` 커밋. push/공개 배포를 실제 수행한 경우에만 원격 완료라고 표기한다.

## 준비 점검

- [x] 기존 worktree·entrypoint·OAuth/config·README 확인.
- [x] HTTPS-only 제약과 기존 localhost 안내 불일치 확인.
- [x] Zendesk Public PKCE/refresh 및 uv 설치 공식 계약 확인.
- [x] 사용자 흐름과 기존 인증 호환성 설계 고정.
- [ ] 실제 Zendesk Public OAuth와 push 후 원격 CI 결과를 이 계획에 추가해 최종 완료 처리.
