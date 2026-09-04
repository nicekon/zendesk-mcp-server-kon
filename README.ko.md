# Zendesk MCP 서버 KON

이 프로젝트는 [reminia/zendesk-mcp-server](https://github.com/reminia/zendesk-mcp-server)를 포크하여 추가 기능과 개선사항을 추가한 버전입니다.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

Zendesk를 위한 Model Context Protocol 서버입니다.

이 서버는 Zendesk와의 포괄적인 통합을 제공하며 다음과 같은 기능을 제공합니다:

- Zendesk 티켓 및 댓글 관리 도구
- 커뮤니티 게시물, 댓글, 토픽 관리 도구
- 티켓 분석 및 응답 작성을 위한 특화된 프롬프트
- Zendesk 헬프 센터 문서에 대한 전체 접근

## 설치 및 설정

1. 패키지 설치:
```bash
uv venv && uv pip install -e .
```

2. Claude 데스크톱에서 설정:
```json
{
  "mcpServers": {
    "zendesk": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/zendesk-mcp-server-kon",
        "run",
        "zendesk"
      ],
      "env": {
        "ZENDESK_SUBDOMAIN": "your-zendesk-subdomain",
        "ZENDESK_EMAIL": "your-zendesk-email",
        "ZENDESK_API_KEY": "your-zendesk-api-key"
      }
    }
  }
}
```

환경 변수를 다음과 같이 설정하세요:
- `ZENDESK_SUBDOMAIN`: Zendesk 서브도메인 (예: Zendesk URL이 `company.zendesk.com`인 경우 `company`를 입력)
- `ZENDESK_EMAIL`: Zendesk 관리자 이메일 주소
- `ZENDESK_API_KEY`: Zendesk API 토큰

### 빠른 설치 (클론 불필요)

`uv`가 설치되어 있다면 1번 단계 없이 `uvx`로 이 저장소에서 바로 실행할 수 있습니다:

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/nicekon/zendesk-mcp-server-kon.git@4717ee6299539c653655ba1d579cdcde42a0757c",
        "zendesk"
      ],
      "env": {
        "ZENDESK_SUBDOMAIN": "your-zendesk-subdomain",
        "ZENDESK_EMAIL": "your-zendesk-email",
        "ZENDESK_API_KEY": "your-zendesk-api-key"
      }
    }
  }
}
```

`uvx`가 GitHub에서 패키지를 직접 가져와 실행하므로 로컬 클론이나 `--directory` 경로 관리가 필요 없습니다.

위 URL은 브랜치명이 아니라 특정 커밋 해시로 고정되어 있습니다. `uv`는 Git 의존성을 완전히 resolve된 커밋 해시 기준으로 캐싱하기 때문에, 커밋을 고정하면 최초 설치 이후에는 위의 로컬 클론 방식과 동일하게 완전히 오프라인으로 동작합니다. 반대로 브랜치(예: `...git@main`)를 지정하면 실행할 때마다 GitHub의 해당 브랜치 `HEAD`를 확인하는 네트워크 요청이 발생합니다 — 커밋이 안 바뀌었으면 재설치는 안 하지만 매번 인터넷 연결이 필요하고, 대신 새 커밋이 자동으로 반영됩니다. 이 설치 방식이 최신 버전을 추적하게 하려면 고정된 커밋 해시를 수동으로 갱신해야 합니다.

## 리소스

- zendesk://knowledge-base: 전체 헬프 센터 문서에 접근

## 프롬프트

### analyze-ticket

Zendesk 티켓을 분석하고 상세한 분석 결과를 제공합니다.

### draft-ticket-response

Zendesk 티켓에 대한 응답을 작성합니다.

## 도구

### 티켓 관리

#### get_ticket
티켓 ID로 Zendesk 티켓 조회
- 입력:
  - `ticket_id` (integer): 조회할 티켓의 ID

#### get_ticket_comments
티켓 ID로 해당 티켓의 모든 댓글 조회
- 입력:
  - `ticket_id` (integer): 댓글을 조회할 티켓의 ID

#### create_ticket_comment
기존 티켓에 새 댓글 작성
- 입력:
  - `ticket_id` (integer): 댓글을 작성할 티켓의 ID
  - `comment` (string): 댓글 내용
  - `public` (boolean, 선택): 공개 댓글 여부 (기본값: true)

### 커뮤니티 관리

#### get_community_posts
커뮤니티 게시물 조회 (필터링 및 정렬 옵션 지원)
- 입력:
  - `filter_by` (string, 선택): 상태별 필터링 (planned, not_planned, completed, answered, none)
  - `sort_by` (string, 선택): 정렬 기준 (created_at, edited_at, updated_at, recent_activity, votes, comments)

#### get_community_post_comments
커뮤니티 게시물과 모든 댓글 조회
- 입력:
  - `post_id` (integer): 댓글을 조회할 게시물의 ID

#### create_community_post_comment
커뮤니티 게시물에 새 댓글 작성
- 입력:
  - `post_id` (integer): 댓글을 작성할 게시물의 ID
  - `body` (string): 댓글 내용
  - `author_id` (integer, 선택): 댓글 작성자 ID (헬프 센터 관리자만 사용 가능)
  - `notify_subscribers` (boolean, 선택): 구독자 알림 여부 (기본값: true)

#### update_community_post_comment
커뮤니티 게시물의 댓글 수정
- 입력:
  - `post_id` (integer): 댓글이 속한 게시물의 ID
  - `comment_id` (integer): 수정할 댓글의 ID
  - `body` (string): 수정할 댓글 내용

#### update_community_post
커뮤니티 게시물 수정
- 입력:
  - `post_id` (integer): 수정할 게시물의 ID
  - `title` (string, 선택): 게시물 제목
  - `details` (string, 선택): 게시물 내용 (p, br, strong 태그 사용 가능)
  - `topic_id` (integer, 선택): 게시물이 속할 토픽의 ID
  - `status` (string, 선택): 게시물 상태 (planned, not_planned, answered, completed)

#### get_community_topics
모든 커뮤니티 토픽 조회
- 토픽의 이름, 설명, 팔로워 수 등의 상세 정보를 포함한 목록을 반환합니다. 