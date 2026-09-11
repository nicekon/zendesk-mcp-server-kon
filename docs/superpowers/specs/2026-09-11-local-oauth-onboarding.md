# 로컬 MCP 설치·OAuth 로그인 설계

작성일: 2026-09-11. 상태: 로컬 구현 및 Public OAuth 최초 로그인 E2E 완료, refresh·원격 CI 검증 대기.

## 목적과 기준선

사용자가 패키지를 설치하고 브라우저에서 승인하면, 코드 복사 없이 같은 인증 정보를 MCP 클라이언트에서 사용한다. 기존 통합 PRD의 API token·OAuth 동시 지원 및 기본 읽기 전용 원칙을 유지한다.

현재 `pyproject.toml`은 `zendesk` 실행 명령을 등록한다. CLI는 `check`, `approve`, `oauth-start`, `oauth-finish`만 지원한다. OAuth는 Client Secret을 필수로 요구하고 HTTPS redirect만 허용한다. 임시 콜백 서버, PKCE, `login`, 저장된 연결 설정은 없다. 기존 localhost 수동 인증 안내는 현재 코드와 일치하지 않으며 README 개편 시 바로잡는다.

## 사용자 흐름

1. 관리자가 해당 Zendesk 인스턴스에 Public OAuth client를 한 번 등록한다. Identifier와 redirect URI `http://127.0.0.1:3000/oauth/callback`을 배포 안내에 공유한다. Allowed scopes는 앱이 요청하는 읽기 scope를 포함해야 한다. 공용/global OAuth client는 이번 범위가 아니다.
2. 사용자는 uv와 Git을 준비하고 공개된 검증 커밋의 패키지를 `uv tool install`로 설치한다. 최종 README 명령은 실제 푸시된 커밋 SHA로 고정한다. PyPI 게시를 전제로 하지 않는다. 실행 파일 검색 경로가 없으면 `uv tool update-shell` 후 새 터미널을 연다.
3. 사용자가 터미널에서 `zendesk login --subdomain acme --client-id acme_mcp` 실행. CLI가 포트를 연 뒤 브라우저를 띄운다. 사용자에게 Client Secret이나 code/state 입력을 요구하지 않는다.
4. 사용자가 Zendesk에서 승인하면 CLI가 state 검증, 코드 교환, 읽기 전용 사용자 확인을 수행한다. 성공한 토큰과 연결 정보를 저장하고 콜백 서버를 종료한다.
5. MCP 클라이언트의 실행 명령에 설치된 `zendesk`의 절대 경로를 등록한다. 추가 args 없이 stdio 서버가 실행된다. 새 터미널·다른 작업 디렉터리에서도 저장된 연결을 읽는다. 클라이언트 프로세스가 터미널의 export를 상속한다고 가정하지 않는다.

`zendesk login`은 제안 명령이며 구현·검증 전 설치 안내로 배포하지 않는다. 원격 HTTP MCP의 클라이언트 로그인 버튼 연동은 이번 로컬 stdio 설계와 별개다.

## 구현 계약

- Python 3.10–3.12 유지. 기존 httpx 및 표준 라이브러리만 사용한다.
- 기본 로그인은 Public client + Authorization Code + PKCE S256. Secret을 배포하지 않는다. 기존 Confidential OAuth 및 수동 HTTPS 명령은 유지한다. Public refresh 요청에서도 client_secret을 생략한다.
- 기본 callback은 `http://127.0.0.1:3000/oauth/callback`. 포트 충돌 시 자동으로 포트를 바꾸지 않는다. `--port`로 명시적으로 변경할 수 있으나 Zendesk 등록 URI도 정확히 변경해야 한다. 동적 포트 허용을 가정하지 않는다.
- listener는 IPv4 loopback에만 바인딩한다. HTTP 예외는 이 loopback callback에 한정한다. 기존 HTTPS redirect는 유지하고 userinfo, fragment, 임의 HTTP 호스트는 거부한다.
- 바인딩 성공 후 브라우저를 연다. 180초 timeout, Ctrl-C, 승인 거절, 교환 실패에서 listener를 닫는다. 브라우저 자동 실행 실패 시 승인 URL을 안내하고 같은 listener에서 계속 대기한다. code/state 수동 입력은 요구하지 않는다.
- callback은 정확한 Host·경로·GET·단일 state·단일 code 또는 error를 검증한다. 잘못된 state 요청은 교환하지 않고 대기한다. 유효한 callback은 한 번만 처리한다. favicon·중복 요청은 토큰 교환을 반복하지 않는다. 느린 연결의 읽기 timeout도 전체 대기 제한 안에 둔다.
- state와 PKCE verifier는 자동 로그인 세션 메모리에만 두고 로그에 남기지 않는다. HTTP 기본 access log를 억제한다. 브라우저 응답에 코드·토큰을 포함하지 않는다.
- 성공 전에 기존 연결 파일을 덮어쓰지 않는다. 토큰 교환 후 `/api/v2/users/me.json`을 호출해 확인한다. 실패 시 기존 연결을 보존하고 비밀값 없는 오류를 반환한다.
- 저장 파일은 기본 `Path.home() / '.config/zendesk-mcp-server/connection.json'`. 한 연결만 지원한다. 파일에 schema_version, subdomain, client_id, client_kind, scopes, access_token, refresh_token, expires_at을 함께 저장해 부분 갱신을 방지한다. chmod 0600·원자적 교체·기존 refresh lock을 재사용한다. Windows에서는 POSIX 모드만으로 ACL 보호를 보장했다고 표현하지 않으며 사용자 프로필 내 저장/실제 권한 동작을 별도 검증한다.
- 명시적 인증 환경변수(인증 모드·subdomain·email·token·OAuth 관련 값)가 하나라도 있으면 기존 환경변수 경로 전체를 선택한다. 누락 값을 저장 파일에서 보충하지 않는다. 인증 환경변수가 없을 때만 저장된 연결을 사용한다. 따라서 다른 tenant 설정과 토큰이 섞이지 않는다. capabilities·쓰기 gate 등 비인증 설정은 기존 규칙을 유지한다.
- 저장된 OAuth grant보다 넓은 scope가 필요하면 재로그인을 안내한다. login 자체는 읽기 전용으로 수행한다. 권한 확대를 자동 승인하지 않는다.
- `zendesk check`는 기존 무네트워크 의미 유지, `zendesk check --probe`는 실제 연결 확인, `--help`는 설치 확인에 사용한다. stdio 서버는 브라우저를 열지 않고 stdout에 MCP 메시지만 쓴다. 미인증 시 로그인 방법을 구조화된 오류로 안내한다.
- package uninstall은 인증 철회가 아님을 안내한다. 계정 접근 철회는 Zendesk에서 수행하고, 로컬 연결 파일은 정확한 경로를 안내하여 사용자가 관리한다. 이번 범위에 자동 서버 토큰 철회·여러 계정 관리 기능을 넣지 않는다.

## 완료 기준

깨끗한 설치 환경에서 실행 파일 생성 → 가짜 OAuth 서버와 실제 loopback callback → 새 프로세스의 저장 정보 재사용 → MCP initialize/tools/list를 검증한다. 잘못된 state, PKCE payload, timeout, 포트 충돌, 실패 후 기존 연결 유지, 환경변수 우선순위, refresh 회귀를 자동 검증한다. 실제 Zendesk의 Public client 승인·refresh는 별도 읽기 E2E 증거로 기록한다. 로컬 테스트만으로 실제 OAuth 성공을 선언하지 않는다.

2026-09-11 실제 `unicorn-adblock` tenant에서 Public OAuth 브라우저 승인,
loopback callback, 코드 교환, 저장된 연결의 새 CLI 프로세스 재사용 및
`users/me` 조회가 `auth_mode: oauth`, admin 사용자로 성공했다. 토큰 값은 출력하지
않았다. 실제 만료 token refresh와 새 MCP 프로세스의 도구 호출은 별도 대기다.

## 근거

- [Zendesk Public client 및 PKCE·refresh](https://developer.zendesk.com/documentation/authentication/oauth-migration/)
- [Native OAuth loopback와 PKCE 표준](https://www.rfc-editor.org/rfc/rfc8252.html)
- [uv tool install과 Git 설치](https://docs.astral.sh/uv/guides/tools/)
- [기존 통합 PRD](2026-09-04-unified-zendesk-mcp-design.md)
