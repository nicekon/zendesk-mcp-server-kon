# Windows OAuth 설치

이 안내는 Windows OAuth 수정이 포함된 **로컬 소스**에 적용됩니다.
README의 고정된 이전 커밋으로 설치하면 이 수정이 포함되지 않습니다.
WSL 없이 Windows Python 3.10 이상과 PowerShell을 사용합니다.

## 설치와 로그인

수정본 소스 폴더에서 실행하세요. 기존 가상환경을 사용한다면 아래 경로를
그 환경으로 바꾸고 설치 시 `--force-reinstall`을 추가하세요.

```powershell
py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install .
$env:ZENDESK_WRITE_MODE = "read_only"
$env:ZENDESK_ENABLE_PUBLIC_WRITES = "false"
& .\.venv\Scripts\zendesk.exe login --subdomain your-company --client-id your-client-id
```

서브도메인과 공개 클라이언트 ID를 실제 값으로 바꾸세요. OAuth 클라이언트에는
`http://127.0.0.1:3000/oauth/callback`이 등록되어 있어야 합니다.
브라우저에서 로그인과 권한 승인을 완료하면 로컬 콜백을 통해 저장됩니다.
클라이언트 비밀키나 Mac의 토큰을 복사할 필요가 없습니다.

```powershell
& .\.venv\Scripts\zendesk.exe check --probe
(Resolve-Path .\.venv\Scripts\zendesk.exe).Path
```

## Codex 연결

출력된 실행 파일의 절대 경로를 Codex 설정에 넣으세요.
기존 `zendesk_mcp` 항목이 있다면 중복 추가하지 말고 수정합니다.

```toml
[mcp_servers.zendesk_mcp]
command = 'C:\absolute\path\to\.venv\Scripts\zendesk.exe'

[mcp_servers.zendesk_mcp.env]
ZENDESK_WRITE_MODE = "read_only"
ZENDESK_ENABLE_PUBLIC_WRITES = "false"
```

이 예시는 조회용입니다. 로그인할 때와 서버 실행 때 권한 설정을 동일하게 유지하세요.
자동 저장 연결을 사용하려면 기존 `ZENDESK_AUTH_MODE`, `ZENDESK_SUBDOMAIN`,
`ZENDESK_EMAIL`, `ZENDESK_API_TOKEN`, `ZENDESK_API_KEY`,
`ZENDESK_OAUTH_CLIENT_ID`, `ZENDESK_OAUTH_CLIENT_SECRET`, `ZENDESK_OAUTH_CLIENT_KIND`,
`ZENDESK_OAUTH_TOKEN_STORE`를 해당 서버의 환경 설정에서 제거하세요.
상위 프로세스 환경에도 인증 설정이 있으면 함께 확인하세요. 빈 값도 인증 설정으로 간주됩니다.
설정 저장 후 Codex를 다시 열어 연결을 확인합니다.

## 저장과 문제 해결

연결은 `%USERPROFILE%\.config\zendesk-mcp-server\connection.json`에 저장됩니다.
Windows에서는 pywin32로 파일 생성 시점부터 현재 사용자와 LocalSystem만 접근할 수 있는
보호된 DACL을 설정하며, 읽는 파일 핸들로 권한을 확인합니다. 토큰 갱신은 프로세스 간 잠금으로
직렬화됩니다. 다른 사용자에게 노출된 파일, 심볼릭 링크·재분석 지점과 다중 하드링크는 거부합니다.
기존 파일의 권한을 자동으로 완화하거나 수정하지 않습니다.

- `unsupported`: 이 수정본과 Windows 의존성인 pywin32가 설치되어 있는지 확인하세요.
- `unsafe_oauth_permissions`: 파일이 개인 계정의 로컬 NTFS 경로에 있는지 확인하세요.
  기존 파일과 상위 폴더의 접근 권한을 조사한 후 다시 로그인하세요.
- `oauth_callback_unavailable`: 3000 포트를 사용하는 프로그램을 확인하세요.
- 권한 범위를 바꾼 경우 로그인과 Codex 설정을 맞춘 후 다시 승인하세요.

이 변경은 OAuth 저장·갱신·로그인 지원에 한정됩니다. 기존 레거시 토큰 파일 변환과
롤백은 POSIX 전용입니다. 전체 Windows 테스트에서 발견된 별도 쓰기 승인·첨부 캐시 문제는
해당 기능의 Windows 호환성 작업이 필요합니다.
