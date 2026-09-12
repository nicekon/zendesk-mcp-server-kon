# 통합 Zendesk MCP 제품 요구사항 문서(PRD)

- 상태: 초안 PR 검토 중 — 로컬 계약·패키지 검증, API token 도메인 읽기 E2E, Public OAuth 브라우저 로그인·사용자 조회·실제 만료 token refresh E2E 및 원격 CI 검증 기록이 있다. 별도 테스트 계정이 없어 실제 쓰기 E2E는 미검증으로 유지하며 계정을 재요청하거나 운영 쓰기로 대체하지 않는다. 전체 출시 완료는 미선언이며 최신 커밋별 근거는 완료 감사 문서와 PR #2를 따른다.
- 작성일: 2026-09-04
- 목표 릴리스: `1.0.0`(호환성 중단을 허용하는 메이저 릴리스)
- 기준 저장소: `zendesk-mcp-server-kon`
- 결정권자: 프로젝트 소유자

## 1. 요약

현재 KON 저장소·패키지를 출시 대상으로 유지하고,
[michaelrice/zendesk-mcp](https://github.com/michaelrice/zendesk-mcp)의 모듈을
구현 코어로 이식한다. 이 코어의 OAuth와 테스트·배포 방식을 채택한 뒤, 현재
KON의 API token 인증과 Community 기능, 설치된 SwiftEq MCP의 Help Center·
CSAT·관리 기능을 합친다.

목표는 세 구현의 도구 수를 단순 합산하는 것이 아니다. 겹치는 작업은 하나의
공통 백엔드와 일관된 계약으로 합치고, 사용자에게 필요한 편의 도구만 얇은
래퍼로 제공한다. Community는 2026-09-04 기준으로 선정한 Zendesk 공식
Help Center API 리소스를 구현하고, 같은 규칙으로 후속 엔드포인트를 추가할
수 있게 한다.

완료 상태는 다음 한 문장으로 정의한다.

> OAuth와 API token 중 어느 방식으로도 안전하게 연결되고, Support·Guide·CSAT·
> Community의 승인된 기능 합집합을 일관된 MCP 도구로 사용할 수 있으며,
> 공개·파괴적 작업과 고객 데이터가 기본적으로 보호되는 서버.

## 2. 배경과 기준선

### 2.1 비교 결과

| 구현 | 확인된 표면 | 강점 | 이번 제품에서 보완할 점 |
| --- | ---: | --- | --- |
| 현재 KON | 도구 9, 프롬프트 2, KB 리소스 1 | API token, Community 목록·댓글 생성·게시물/댓글 수정, 단순 설치 | 단일 파일 등록/분기, 테스트 부재, 첫 페이지만 조회, 공개 댓글·구독 알림의 위험한 기본값, 공통 timeout·오류 계약 부재 |
| michaelrice `8313e117` | 도구 29, 프롬프트 2, 선택형 KB 리소스 1, 테스트 함수 154 | 도메인 모듈화, OAuth refresh, 티켓·Views·Macros·시간 추적, Python 3.10–3.12 CI와 패키징 | OAuth만 지원, Community/Guide 관리/CSAT 부재, 임의 URL·경로 첨부 다운로드, 매크로 부분 성공, 하드코딩된 시간 필드, 불일치한 페이지네이션·응답 |
| 설치된 SwiftEq 세션 스냅샷 | 호출 가능 도구 21 | Help Center, 브랜드, CSAT, 티켓 필드·폼·트리거, 대화·첨부 | 소스와 테스트는 확인하지 못함, Community와 michaelrice 고유 기능 부재 |

도구 수 9+29+21은 중복을 포함한 원시 수치다. 목표 범위는 아래 기능 계약으로
고정하며 최종 도구 수 자체를 성공 지표로 사용하지 않는다.

### 2.2 확인 한계

- SwiftEq 비교는 2026-09-04 현재 Codex에 노출된 도구 메타데이터 기준이다.
  실제 Zendesk 계정으로 인증하거나 쓰기 요청을 실행하지 않았다.
- michaelrice 비교는 커밋 `8313e117094d005dcf1fc48ffb6f9197bc60a712`
  기준이다.
- Zendesk 기능은 계정 플랜, 활성 제품, 역할과 OAuth scope에 따라 사용할 수
  있는 범위가 다르다.

## 3. 목표

1. 기존 API token 방식과 OAuth 방식을 모두 유지한다.
2. michaelrice의 도메인 모듈화, OAuth refresh, 테스트와 배포 기반을 도입한다.
3. KON·michaelrice·SwiftEq의 승인된 기능 합집합을 중복 없는 도구 계약으로
   제공한다.
4. Support, Guide, CSAT, Community의 목록 API에 일관된 페이지네이션과 오류
   응답을 적용한다.
5. 공개 댓글, 게시·알림, 삭제, 작성자 대행, 외부 업로드 같은 고위험 작업은
   일반 쓰기와 분리해 명시적으로 승인한다.
6. Community는 이 문서가 선정한 공식 게시물·댓글·토픽·투표·구독·태그·
   사용자 구독·이미지·배지 기능을 포함하고, 공식 문서 기반 확장 절차를 둔다.
7. Python 3.10–3.12에서 설치, MCP handshake, 핵심 계약과 인증 경로를 자동
   검증한다.

## 4. 비목표

다음은 `1.0.0` 범위가 아니다.

- 임의 HTTP method와 URL을 받는 `zendesk_request` 같은 범용 프록시
- 한 서버 프로세스에서 여러 Zendesk 계정을 동시에 선택·전환하는 기능
- Zendesk 콘텐츠를 지시문으로 실행하는 에이전트 또는 자동 워크플로 엔진
- GitLab/GitHub 이슈를 실제 생성하는 기능. 이번 범위는 Markdown 문맥 변환과
  Git-Zen 링크 추출까지다.
- Zendesk 전체 API 지원이라는 포괄적 약속
- 현재 세 구현 모두에 없는 ticket audits/events, followers/CC, SLA,
  suspended tickets, schedules/routing, 사용자·조직 CRUD, incremental export,
  article comments/votes, category·section CRUD
- Community 인접 Help Center 기능인 article/section content subscriptions,
  사용자별 article·section을 섞은 전체 content subscription feed
- Content Tags 비동기 batch delete/merge job
- 별도 승인 없는 운영 계정 쓰기 E2E
- 압축 첨부파일 자동 해제

비목표의 엔드포인트를 나중에 추가할 때도 10장의 확장 규칙과 동일한 보안·
테스트 계약을 만족해야 한다.

## 5. 사용자와 핵심 시나리오

### 5.1 CS 상담원

- 검색 조건으로 티켓을 찾고 전체 대화를 읽는다.
- 첨부파일을 안전하게 확인하고 내부 메모 또는 공개 답변을 구분해 작성한다.
- 담당자, 그룹, 상태, 태그와 커스텀 필드를 수정한다.
- 티켓을 개발 이슈용 Markdown 문맥으로 변환한다.

### 5.2 CS/Guide 운영자

- 브랜드별 Help Center 문서를 검색·내보내고 문서와 번역을 관리한다.
- 티켓 필드, 폼, Views, Macros, Triggers와 CSAT 결과를 확인한다.
- Community 게시물, 댓글, 토픽, 태그, 구독과 배지를 권한 범위에서 관리한다.

### 5.3 개발·운영 담당자

- API token 또는 OAuth로 설치하고 인증 상태를 검증한다.
- 구조화된 오류와 request ID로 장애를 진단한다.
- mock 계약 테스트와 Zendesk sandbox E2E를 분리해 릴리스를 검증한다.

## 6. 제품 원칙

1. **안전한 기본값**: 새 설치는 읽기 전용이다. 내부 메모, 알림 없음,
   작성자 대행 없음이 기본값이다.
2. **명시적 인증 선택**: 선택된 인증이 실패해도 다른 자격 증명으로 조용히
   전환하지 않는다.
3. **한 경로**: 인증, HTTP, timeout, rate limit, 페이지네이션, 오류 변환과
   redaction은 공통 client를 통과한다.
4. **중복 제거**: 상태·담당자·태그 편의 도구는 통합 ticket update 백엔드를
   재사용한다.
5. **공식 계약 우선**: 필드, 권한, 페이지 방식과 ID type은 Zendesk 공식
   문서를 따른다.
6. **고객 콘텐츠는 데이터**: 티켓·문서·Community HTML을 신뢰할 수 없는
   사용자 콘텐츠로 표시하며 그 안의 지시를 실행하지 않는다.
7. **모르는 쓰기는 성공이 아님**: timeout이나 연결 종료로 결과를 알 수 없으면
   `outcome_unknown`을 반환하고 read-back 없이 자동 재시도하지 않는다.

## 7. 기능 요구사항

요구 수준은 다음과 같다.

- **필수**: 모든 설치에서 구현되어야 한다.
- **조건부 필수**: 구현은 필수지만 계정의 플랜·앱·설정이 없으면
  `unsupported` 또는 `not_configured` 구조화 오류를 반환한다.

### 7.1 인증·공통 기반

| ID | 수준 | 요구사항 |
| --- | --- | --- |
| AUTH-01 | 필수 | `oauth`, `api_token`, `auto` 인증 모드를 제공한다. |
| AUTH-02 | 필수 | `auto`는 완전한 OAuth 설정이 있으면 OAuth, OAuth 항목이 전혀 없으면 API token을 선택한다. OAuth 설정이 일부만 있으면 구성 오류로 중단한다. 선택 후 인증 실패 시 다른 방식으로 fallback하지 않는다. |
| AUTH-03 | 필수 | OAuth authorization code 흐름은 일회성 `state`를 검증하고 redirect URI를 정확히 일치시킨다. 최소 scope만 요청한다. |
| AUTH-04 | 필수 | 만료가 임박한 token은 요청 전에 refresh한다. 읽기 요청의 401은 refresh 후 한 번만 replay한다. 쓰기 요청의 401은 refresh하되 자동 replay하지 않고 `authentication_retry_required`와 `not_applied`를 반환한다. 동시 refresh를 하나로 직렬화하고 회전된 token을 놓치지 않는다. |
| AUTH-05 | 필수 | OAuth 설정 파일은 사용자 전용 `0600`이고 임시 파일+원자 rename으로 갱신한다. API token은 환경 변수에서만 읽는다. |
| AUTH-06 | 필수 | 프로세스 시작과 MCP handshake는 네트워크 없이 가능해야 한다. 설정된 인증은 첫 Zendesk 작업 전 또는 명시적 연결 상태 도구에서 `users/me` 상당의 읽기 요청으로 계정과 subdomain을 확인한다. |
| AUTH-07 | 필수 | token, client secret, Authorization header, 이메일과 고객 본문은 로그·오류에 남기지 않는다. |
| HTTP-01 | 필수 | 모든 Zendesk 요청은 하나의 공통 transport와 connect 5초, read/write 30초, pool 5초 timeout을 사용한다. |
| HTTP-02 | 필수 | 읽기 요청은 최초 시도+최대 2회 재시도한다. 429는 `Retry-After`와 jitter를 적용하고 전체 sleep은 30초를 넘지 않는다. 30초를 넘는 `Retry-After`는 기다리지 않고 반환한다. 읽기의 timeout/일시적 5xx도 같은 횟수 안에서 재시도한다. |
| HTTP-03 | 필수 | 쓰기 요청은 자동 재시도하지 않는다. 429·timeout·연결 종료에는 알려진 적용 상태를 포함한다. |
| HTTP-04 | 필수 | cursor pagination을 우선하고, 공식 API가 지원하지 않을 때만 offset/`next_page`를 사용한다. interactive 목록의 page size는 공식 endpoint 한도와 100 중 작은 값, 기본 총 100개·최대 총 1,000개다. export는 streaming하며 배포 기본 상한 100,000개를 넘으면 `truncated=true`다. |
| HTTP-05 | 필수 | Zendesk Authorization은 정확히 일치하는 tenant/brand HTTPS hostname에만 보낸다. pagination URL은 이 host만 허용한다. attachment `content_url`이 이 host면 최초 요청에 Authorization을 보내되 cross-host redirect 전에 제거한다. metadata가 처음부터 CDN URL이면 Authorization 없이 요청한다. CDN/redirect는 metadata에서 파생한 허용된 public host만 사용하고 loopback/private/link-local IP를 거부한다. presigned upload는 인증된 Zendesk API 응답에서 받은 HTTPS public host와 signed headers만 사용하며 Zendesk Authorization을 절대 전달하지 않는다. |
| HTTP-06 | 필수 | `brand_id`로 브랜드 host를 해석한다. 호출자가 임의 base URL을 주입할 수 없다. |

### 7.2 티켓·대화·첨부

| 기능군 | 요구사항 | 수준 |
| --- | --- | --- |
| 조회 | 티켓 목록·정렬, 단건 상세, 구조화 검색, 건수, JSON/CSV 내보내기 | 필수 |
| 검색 조건 | full text, status, priority, type, assignee, requester, group, organization, brand, form, attachment 유무, include/exclude tags, created/updated/solved range, custom field ID/value, 선택 반환 필드와 결과 limit | 필수 |
| 생성 | requester, subject, description, type, priority, assignee/group/organization, tags, custom fields를 검증해 생성 | 필수 |
| 통합 수정 | subject, status, custom status, type, priority, requester, assignee, group, organization, due date, tags, custom fields 수정 | 필수 |
| 편의 작업 | 상태 설정, 담당자 배정, 태그 추가·삭제는 통합 수정 백엔드를 호출하는 얇은 도구 | 필수 |
| 대화 | email, web, API, mobile, chat, messaging, WhatsApp 채널을 한 타임라인으로 정규화하고 작성자, 채널, 공개 여부, side를 보존한다. 알 수 없는 채널도 원래 channel 값을 잃지 않는다. | 필수 |
| 댓글 | 공개 답변과 내부 메모를 서로 다른 도구로 제공한다. 내부 메모가 안전한 기본값이다. | 필수 |
| 첨부 메타데이터 | 대화의 첨부 ID, 이름, MIME type, 크기, 부모 티켓, `deleted`, `malware_scan_result`, `malware_access_override`를 반환 | 필수 |
| 첨부 다운로드 | `ticket_id + attachment_id`만 입력받고 서버가 소속과 URL을 다시 확인한 뒤 고정 cache root 아래에 스트리밍 저장 | 필수 |
| 첨부 검사 | text/PDF의 제한된 text 추출, image MCP preview, archive entry manifest를 제공하되 archive를 해제하지 않음 | 필수 |
| 이슈 문맥 | 티켓과 대화를 비밀값을 제외한 Markdown으로 변환 | 필수 |
| Git-Zen | 설정된 custom field에서 issue/MR/commit 링크를 추출 | 조건부 필수 |
| 시간 추적 | 계정 metadata 또는 명시적 설정으로 시간 필드를 찾고 조회·기록 | 조건부 필수 |

검색·건수·export는 하나의 `TicketQuery` filter schema와 resolver를 재사용한다.
따라서 같은 필터가 도구마다 다르게 해석되지 않아야 한다.

- assignee/requester는 tagged reference로 `me`, `none`, `id`, `name`, `email`,
  `phone` 중 하나만 받는다.
- brand/group/organization은 `id` 또는 `name` 중 하나만 받으며 organization만
  `none`을 허용한다.
- form은 `id` 또는 `name` 중 하나만 받는다.
- 이름·이메일 등으로 둘 이상이 매칭되면 임의 선택하지 않고
  `validation_error`와 후보 ID를 반환한다.
- search/export의 별도 `TicketProjection`은 선택 반환 필드와
  `include_custom_objects: string[]`을 받는다. custom object는 필터가 아니라
  관련 레코드 출력 확장이다. JSON은 `custom_objects` 아래 중첩하고 CSV는
  `<object_key>.<field_key>` 열로 평탄화한다.
- 요청한 custom object key가 없으면 `validation_error`, 제품이 없으면
  `unsupported`, scope/역할이 부족하면 `permission_denied`를 반환한다.
  count 도구는 `TicketProjection`을 받지 않는다.
- `zendesk_search_tickets`는 일반 Search API의 offset pagination을 사용하고
  공식 최대 1,000개를 넘겨 전체 결과라고 표시하지 않는다. 더 많은 결과는
  `zendesk_export_tickets`를 사용한다.
- export adapter는 `/api/v2/search/export` 전용 serializer를 사용한다. 같은
  `TicketQuery`를 받되 `filter[type]=ticket`을 반드시 보내고 query 안에
  `type:ticket`을 삽입하지 않는다. 이 API의 sort는 `created_at`만 허용한다.
- export cursor는 발급 후 1시간 안에만 재개할 수 있다. 만료·거부된 cursor는
  처음부터 조용히 다시 시작하지 않고 `cursor_expired`를 반환한다.

첨부 다운로드 추가 조건:

- 기본 최대 크기는 20 MB이며 더 큰 응답은 body 전체를 읽기 전에 중단한다.
- `deleted=true` 또는 `malware_scan_result`가 `malware_found`면 다운로드와
  inspection을 거부한다. `not_scanned`, `failed_to_scan`, 값 누락도 안전하다고
  간주하지 않고 거부한다. 자동 처리 허용값은 `malware_not_found`뿐이다.
  `malware_access_override`를 설정·우회하는 기능은 이번 범위에 없다.
- HTTPS와 Zendesk attachment metadata에서 직접 받은 API/CDN URL만 허용한다.
- cross-host redirect에 Authorization header를 전달하지 않는다.
- 임의 `attachment_url`, 임의 `dest_dir`, 기존 파일 덮어쓰기, symlink와
  path traversal을 허용하지 않는다.
- attachment ID별 사용자 전용 디렉터리에 임시 저장 후 원자 rename한다.
- 실패한 partial 파일을 제거하고 기본 24시간 TTL로 cache를 정리한다.
- archive를 자동으로 풀지 않는다.
- text/PDF 추출은 최대 500 KiB text와 PDF 100 page, archive manifest는 최대
  500 entry까지만 반환하고 잘린 결과를 표시한다.
- PDF/image parser는 별도 subprocess에서 10초 wall-clock과 256 MiB memory
  한도로 실행한다. image는 최대 40 megapixel, 가로·세로 각각 16,384 pixel을
  넘으면 decode하지 않는다. 현재 OS에서 격리 한도를 적용할 수 없으면
  in-process parsing으로 fallback하지 않고 `unsupported`를 반환한다.
- image는 JSON base64가 아니라 MCP `ImageContent`, 그 밖의 다운로드는
  server-managed cache를 가리키는 MCP `ResourceLink`로 반환한다.

압축파일 자동 해제와 임의 경로 저장은 michaelrice 기능 합집합에서 제외한
승인된 보안 예외다. 대신 압축을 풀지 않는 entry manifest로 inspection 용도를
유지한다.

### 7.3 사용자·조직·메타데이터·자동화

| 기능군 | 요구사항 | 수준 |
| --- | --- | --- |
| 사용자 | 사용자 검색과 상담원 이름 검색 | 필수 |
| 그룹·조직 | 그룹 목록, 그룹 멤버 목록, 조직 단건 조회 | 필수 |
| 계정 metadata | 브랜드, 티켓 필드, 티켓 폼, custom status 목록 | 필수 |
| Views | 목록, 단건 정의, 현재 매칭 티켓 | 필수 |
| Macros | 목록, 적용 preview, 적용 | 필수 |
| Triggers | 목록, 조건, actions, 제공되는 경우 사용량 | 필수 |

Macro 적용은 가능하면 필드와 댓글을 한 번의 ticket update로 보낸다. 공식 API
제약상 여러 호출이 필요하면 단계별 결과와 복구 가능한 ID를 반환하고
`partial_success`를 일반 성공으로 표시하지 않는다.

### 7.4 Guide·Help Center·CSAT

| 기능군 | 요구사항 | 수준 |
| --- | --- | --- |
| 탐색 | 브랜드·locale별 category와 section 목록 | 필수 |
| 문서 조회 | 구조화 검색, 단건 본문·번역, 선택형 `embed_images`, 전체 내보내기 | 필수 |
| 문서 쓰기 | article 생성 시 title/body/locale/labels/position/permission group/user segment/draft를 지원하고, translation create/update(upsert), 기존 body 교체와 publish를 별도 도구로 분리 | 필수 |
| 권한 metadata | permission groups와 user segments 목록 | 필수 |
| KB 리소스 | `zendesk://knowledge-base`를 opt-in으로 제공하고 export 백엔드를 재사용 | 필수 |
| CSAT | legacy satisfaction ratings와 새 survey 체계를 감지하고 backend별 공식 필터로 조회해 JSON/CSV로 내보내기 | 조건부 필수 |

Guide 규칙:

- Help Center ID는 숫자로 강제 변환하지 않고 Zendesk wire type을 보존한다.
- 다중 브랜드는 사용자가 넘긴 URL이 아니라 `brand_id`로 선택한다.
- locale은 계정에서 활성화된 값인지 검증한다.
- 새 article과 translation은 항상 `draft=true`로 생성한다. create/upsert에
  `draft=false`가 들어오면 outbound write 없이 `validation_error`로 거부하고
  publish 도구를 안내한다. publish는 공개 쓰기 등급이며 기본 비활성화한다.
- `zendesk_publish_help_center_article`는 `article_id + locale`을 입력받아 해당
  translation을 `draft=false`로 update하고 read-back한다. article 객체의
  read-only `draft`를 수정하지 않는다. 이미 publish된 translation이면
  outbound write 없이 현재 상태를 반환한다.
- 공식 REST API는 기존 translation의 content block 사용 여부를 신뢰성 있게
  노출하지 않는다. `zendesk_upsert_article_translation`은 새 translation의
  draft body create와 기존 translation의 title 및 `draft=true` 전환만
  허용한다. 현재 translation을 먼저 읽고, publish된 translation의 title
  변경은 public gate와 사람 승인을 요구하며 `draft=true` 전환은 destructive
  gate와 사람 승인을 요구한다. 기존 translation에 body가 들어오면 outbound
  write 없이 `article_body_replace_required`를 반환한다.
- 기존 body 변경은 `zendesk_replace_article_translation_body`에서만 제공한다.
  이 도구는 content block이 평문으로 영구 치환될 수 있음을 preview하고
  destructive gate와 분리된 사람 승인을 요구한다. 대상 translation이
  publish 상태면 public gate도 함께 요구한다. 자동 감지나 안전 보존을 약속하지
  않는다.
- Article create의 `notify_subscribers` 기본값은 `false`이며 기본 요청에 이를
  명시한다. 호출자가 `true`를 요청하면 public gate와 사람 승인을
  요구하고, 수신자 수를 알 수 없으면 `recipient_count_unknown=true`로 표시한다.
- `embed_images`는 기본 `false`다. 활성화하면 전체 이미지 20 MB 상한 안에서
  검증된 Help Center 이미지만 MCP `ImageContent`/`ResourceLink`로 반환한다.
- KB 전체 리소스는 기본 비활성화하고 TTL 1시간 cache를 사용한다. 동명 section을
  이름 key로 저장하지 않고 ID 기반 구조로 보존한다.

CSAT 입력은 `backend=auto|legacy|survey` discriminated schema를 사용한다.
날짜 입력은 timezone이 포함된 ISO-8601로 통일하고 UTC로 정규화한다.

- `legacy`: `score`, `start_time`, `end_time`; adapter가 epoch seconds로 변환
- `survey`: `ticket_id`를 공식 `filter[subject_zrns]`로 변환하고
  `responder_ids`, `created_at_start`, `created_at_end`를 사용하며 날짜는
  epoch milliseconds로 변환
- `auto`: 감지한 backend가 지원하는 필드만 허용

서버가 지원하지 않는 backend/filter 조합을 client-side 전체 수집으로
흉내 내지 않고 `validation_error`로 거부한다. 따라서 page 경계를 넘겨 일부만
필터링한 결과를 전체 결과처럼 반환하지 않는다.

### 7.5 Community

Community는 현재 KON 기능을 보존하는 데서 끝나지 않는다. 다음 공식 API
리소스를 구현한다.

| 리소스 | 필수 기능 | 수준 |
| --- | --- | --- |
| Posts | 전체·topic별·사용자별 목록, search, 단건, 생성, 수정, 삭제; status/closed/featured/pinned/content tags | 필수 |
| Post Comments | post별·사용자별 목록, 단건·locale별 단건, 생성, 수정, 삭제, official 표시 | 필수 |
| Topics | 목록, 단건, 생성, 수정, 삭제 | 필수 |
| Votes | post/comment 및 사용자별 Community vote 목록, 단건, upvote, downvote, 본인 vote 취소 | 필수 |
| Content Subscriptions | post/topic 구독 목록·단건·생성·해제; topic만 갱신과 `include_comments` 지원 | 필수 |
| Content Tags | 검색(빈 prefix는 전체)·건수·단건·생성·수정·삭제; post의 `content_tag_ids` 연결 교체 | 필수 |
| User Subscriptions | follower/following 목록, follow/upsert, unfollow, `include_comments` | 필수 |
| User Images | 3단계 upload URL→binary PUT→Help Center path 생성을 하나의 안전한 작업으로 제공 | 필수 |
| Badge Categories | 목록·단건·생성·삭제 | 조건부 필수 |
| Badges | 목록·단건·생성·수정·삭제, 안전한 icon upload | 조건부 필수 |
| Badge Assignments | 필터 목록, 사용자에게 badge 부여, 회수 | 조건부 필수 |

Community 세부 규칙:

- cursor가 있는 목록은 cursor를 사용한다. 검색처럼 offset만 가능한 API는 공식
  한계와 최대 결과 수를 응답의 `truncated`에 반영한다. Community post 검색은
  공식 최대 1,000개 경계를 넘겨 전체 결과라고 표시하지 않는다.
- 사용자별 Votes endpoint는 Article, Article Comment, Post, Post Comment를
  섞어 반환한다. `zendesk_list_community_votes`는 Post/Post Comment만 노출하되
  요청한 Community 결과 수를 채우거나 upstream이 끝날 때까지 cursor를
  순회한다. raw 1,000개 scan cap에 먼저 도달하면 `truncated=true`,
  `has_more=true`, 재개 가능한 opaque `next_cursor`와 `scanned_count`를 반환한다.
- `zendesk_upsert_user_subscription` POST는 새 follow를 만들거나 기존 구독의
  `include_comments`를 갱신한다. preview에는 현재값과 변경값을 표시하고,
  적용 후 read-back과 `operation_state`를 반환한다. 값이 이미 같으면 outbound
  write 없이 성공하며 도구를 idempotent로 annotation한다.
- Content Tag create/update의 HTTP 400 `Invalid property`는
  `validation_error`로 변환한다.
- 목록 필터와 정렬 enum은 공식 API 값만 허용한다.
- Community/Guide ID는 `string | integer` wire type을 그대로 보존한다.
- `notify_subscribers`는 항상 기본 `false`다. `true`는 topic/post ID와 API가
  제공하는 `follower_count`를 preview한 뒤 고위험 승인을 받는다. 정확한
  수신자 수를 API가 제공하지 않으면 `recipient_count_unknown=true`로 표시한다.
- `author_id` 대행과 `created_at` 대행은 기본 비활성화하고 관리자 권한,
  impersonation 설정과 별도 승인을 모두 요구한다.
- 쓰기 HTML tag allowlist는 `p`, `div`, `span`, `br`, `b`, `i`, `u`,
  `strong`, `em`, `sub`, `sup`, `a`, `hr`, `img`, `h1`–`h6`, `ul`, `ol`,
  `li`, `dl`, `dt`, `dd`, `table`, `thead`, `tbody`, `tfoot`, `tr`, `th`,
  `td`, `colgroup`, `col`, `blockquote`, `pre`, `abbr`, `acronym`, `cite`,
  `code`, `tt`, `samp`, `kbd`, `var`, `dfn`, `address`,
  `x-zendesk-user`로 고정한다.
- 허용 attribute는 `a[href,title,rel]`, `img[src,alt,title,width,height]`,
  `th/td[colspan,rowspan,scope]`뿐이다. `a` URL은 `https`, `http`, `mailto`,
  `img`는 User Images API가 반환한 path 또는 그 path의 canonical absolute
  URL만 허용한다. 그 밖의 Zendesk URL도 image source로 허용하지 않는다. 모든
  event handler, `style`, `class`, `id`, `javascript:` URL을 거부한다.
  Zendesk가 정규화한 결과를 read-back해 반환한다.
- 읽은 HTML과 plain text는 분리하고 `untrusted_user_content`로 표시한다.
- @mention은 attribute 없는 `<x-zendesk-user>`와 숫자 user ID 본문 형식만
  허용한다.
- User Images는 최대 2 MB, 허용 MIME type, regular file, 허용된 local root를
  검증한다. 모든 local image upload는 `lstat`로 symlink를 거부하고 resolved
  path가 허용 root 안인지 확인한 뒤 no-follow로 열어 `fstat` 결과를 다시
  검증한다. presigned upload host에는 최초 요청부터 Zendesk Authorization을
  보내지 않고 Zendesk가 반환한 signed headers만 보낸다.
- Community User Image와 badge icon은 고객 환경의 파일을 Zendesk로 보내므로
  별도 external-upload gate와 사람 승인을 요구한다. preview에는 파일명,
  MIME type, 크기, SHA-256과 대상 resource를 표시하고 파일 내용과 presigned
  URL은 저장·노출하지 않는다.
- Badges 계열은 Gather Professional 및 Help Center manager 권한이 없으면
  `unsupported` 또는 `permission_denied`를 구분한다.
- Badge 삭제는 연결된 assignment도 삭제하므로 preview에 cascade 대상 수와
  복구 불가 여부를 포함한다. Badge Category는 badge가 남아 있으면 삭제하지
  못한다는 공식 제약을 그대로 전달한다.

### 7.6 MCP 프롬프트·리소스·배포

- `analyze-ticket(ticket_id)` 프롬프트를 제공한다.
- `draft-ticket-response(ticket_id)` 프롬프트를 제공하고 게시 전 검토를
  명시한다.
- 프롬프트는 작업을 자동 실행하지 않는 텍스트 템플릿이다.
- stdio transport를 기본으로 유지한다.
- Python 3.10–3.12를 지원한다.
- wheel/sdist, MCPB, MCP Registry 배포 절차를 제공한다.
- package publish job은 테스트·wheel 설치·MCP handshake job의 성공을 직접
  의존해야 한다.

## 8. 표준 도구 표면

모든 이름은 `zendesk_*` prefix를 사용한다. 아래 목록이 `1.0.0`의 canonical
manifest 99개다. 동일 capability를 구현하는 옛 이름은 alias로 남기지 않는다.

### 8.1 연결·계정

- `zendesk_get_connection_status`
- `zendesk_list_help_center_locales`

`zendesk_get_connection_status`는 선택된 인증 방식, 검증된 account/subdomain,
사용자 역할, OAuth scope, 활성 write gate와 감지된 조건부 capability만
redacted 형태로 반환한다.

### 8.2 Support

- `zendesk_list_tickets`
- `zendesk_count_tickets`
- `zendesk_search_tickets`
- `zendesk_export_tickets`
- `zendesk_get_ticket`
- `zendesk_create_ticket`
- `zendesk_update_ticket`
- `zendesk_set_ticket_status`
- `zendesk_assign_ticket`
- `zendesk_add_ticket_tag`
- `zendesk_remove_ticket_tag`
- `zendesk_get_ticket_conversation`
- `zendesk_post_public_reply`
- `zendesk_post_internal_note`
- `zendesk_list_ticket_attachments`
- `zendesk_download_ticket_attachment`
- `zendesk_inspect_ticket_attachment`
- `zendesk_ticket_to_issue_context`
- `zendesk_get_git_zen_links`
- `zendesk_get_time_tracking`
- `zendesk_log_time`

### 8.3 사용자·조직·운영 metadata

- `zendesk_search_users` (`role=agent`, `exact_name=true`로 상담원 이름 검색 포함)
- `zendesk_list_groups`
- `zendesk_list_group_users`
- `zendesk_get_organization`
- `zendesk_list_brands`
- `zendesk_list_ticket_fields`
- `zendesk_list_ticket_forms`
- `zendesk_list_custom_statuses`
- `zendesk_list_views`
- `zendesk_get_view`
- `zendesk_list_view_tickets`
- `zendesk_list_macros`
- `zendesk_preview_macro`
- `zendesk_apply_macro`
- `zendesk_list_triggers`

### 8.4 Guide·CSAT

- `zendesk_list_help_center_categories`
- `zendesk_list_help_center_sections`
- `zendesk_search_help_center_articles`
- `zendesk_get_help_center_article`
- `zendesk_export_help_center_articles`
- `zendesk_create_help_center_article`
- `zendesk_upsert_article_translation`
- `zendesk_replace_article_translation_body`
- `zendesk_publish_help_center_article`
- `zendesk_list_permission_groups`
- `zendesk_list_user_segments`
- `zendesk_get_satisfaction_ratings`
- `zendesk_export_satisfaction_ratings`

### 8.5 Community

- Posts: `zendesk_list_community_posts`, `zendesk_search_community_posts`,
  `zendesk_get_community_post`, `zendesk_create_community_post`,
  `zendesk_update_community_post`, `zendesk_delete_community_post`
- Comments: `zendesk_list_community_comments`, `zendesk_get_community_comment`,
  `zendesk_create_community_comment`, `zendesk_update_community_comment`,
  `zendesk_delete_community_comment`
- Topics: `zendesk_list_community_topics`, `zendesk_get_community_topic`,
  `zendesk_create_community_topic`, `zendesk_update_community_topic`,
  `zendesk_delete_community_topic`
- Votes: `zendesk_list_community_votes`, `zendesk_get_community_vote`,
  `zendesk_upvote_community_content`, `zendesk_downvote_community_content`,
  `zendesk_remove_community_vote`
- Content subscriptions: `zendesk_list_content_subscriptions`,
  `zendesk_get_content_subscription`, `zendesk_create_content_subscription`,
  `zendesk_update_content_subscription`, `zendesk_delete_content_subscription`.
  Update schema는 `content_type=topic`만 허용한다.
- Content tags: `zendesk_search_content_tags`, `zendesk_count_content_tags`,
  `zendesk_get_content_tag`, `zendesk_create_content_tag`,
  `zendesk_update_content_tag`, `zendesk_delete_content_tag`. Post 연결은
  `zendesk_update_community_post`의 `content_tag_ids`를 사용한다.
- User subscriptions: `zendesk_list_user_subscriptions`,
  `zendesk_upsert_user_subscription`, `zendesk_delete_user_subscription`
- Images: `zendesk_upload_community_image`
- Badge categories: `zendesk_list_badge_categories`,
  `zendesk_get_badge_category`, `zendesk_create_badge_category`,
  `zendesk_delete_badge_category`
- Badges: `zendesk_list_badges`, `zendesk_get_badge`, `zendesk_create_badge`,
  `zendesk_update_badge`, `zendesk_delete_badge`
- Badge assignments: `zendesk_list_badge_assignments`,
  `zendesk_assign_badge`, `zendesk_unassign_badge`

각 도구는 등록 manifest에서 input schema, output schema, `readOnlyHint`,
`destructiveHint`, `idempotentHint`, `openWorldHint`와 위험 등급을 선언한다.
도구 이름·schema·annotation은 exact contract test로 고정한다.

## 9. 아키텍처

구현의 기준 구조는 다음과 같다. `server.py`는 등록과 lifecycle만 담당한다.

```text
src/zendesk_mcp_server/
├── server.py
├── config.py
├── auth.py
├── client.py
├── contracts.py
├── prompts.py
├── resources.py
├── tools/
│   ├── tickets.py
│   ├── conversations.py
│   ├── attachments.py
│   ├── users.py
│   ├── organizations.py
│   ├── automation.py
│   ├── help_center.py
│   ├── csat.py
│   ├── time_tracking.py
│   └── community/
│       ├── models.py
│       ├── posts.py
│       ├── comments.py
│       ├── topics.py
│       ├── votes.py
│       ├── subscriptions.py
│       ├── content_tags.py
│       ├── user_images.py
│       └── badges.py
└── __main__.py
```

다음 원칙으로 파일 수를 제한한다.

- 하나의 리소스가 작으면 같은 도메인 모듈에 둔다.
- 한 구현만 존재하는 interface/factory를 만들지 않는다.
- Zenpy와 직접 HTTP를 별도 transport로 운용하지 않는다. 필요한 endpoint는
  공통 client에서 호출한다.
- 상태·담당자·태그 편의 도구는 별도 비즈니스 로직을 갖지 않는다.

### 9.1 요청 흐름

```text
MCP schema validation
  → capability/risk gate
  → auth selection and refresh
  → common HTTP client
  → Zendesk API
  → response normalization/read-back
  → redacted audit metadata
```

### 9.2 설정

최소 설정 계약:

| 설정 | 의미 |
| --- | --- |
| `ZENDESK_AUTH_MODE` | `auto`, `oauth`, `api_token`; 기본 `auto` |
| `ZENDESK_SUBDOMAIN` | 검증된 계정 subdomain |
| `ZENDESK_EMAIL` | API token 인증 사용자 |
| `ZENDESK_API_TOKEN` | API token; 환경 변수 전용 |
| OAuth config | client ID/secret, scopes, access/refresh token, expiry; 사용자 전용 파일 |
| `ZENDESK_CAPABILITIES` | 활성 기능군; 기본 `support,operations,guide,community`, 조건부 `csat,custom_objects,git_zen,time_tracking,badges` |
| `ZENDESK_WRITE_MODE` | `read_only`, `standard`; 기본 `read_only` |
| public/destructive/impersonation/external-upload gates | 각각 별도 opt-in, 기본 `false` |
| KB enable flag | 전체 KB 리소스 등록 여부, 기본 `false` |
| list/export limits | interactive 기본 100·최대 1,000, export 기본 상한 100,000 |
| cache TTL | attachment/export 24시간, KB 1시간 |

기존 `ZENDESK_API_KEY`는 `1.0.0`에서 `ZENDESK_API_TOKEN`으로 이동하고 migration
문서에서 명시한다. 비밀값을 CLI 인자로 받지 않는다.

OAuth setup은 활성 capability의 resource-specific scope 합집합을 요청한다.
사용자 승인 예외로 Support가 활성화되면 Search/Search Export를 위해 broad
`read`도 요청한다. 이 scope는 검색만이 아니라 계정 역할이 허용하는 모든 GET
리소스를 읽을 수 있게 한다. Support 비활성 시에는 요청하지 않으며 broad
`write`는 계속 금지한다. 서버 read-only 기본값과 쓰기 gate·사람 승인은 유지한다.

| 활성 capability | 요청 scope |
| --- | --- |
| Support·계정 metadata 읽기 | `tickets:read users:read groups:read organizations:read brands:read ticket_attachments:read` 및 승인된 검색 예외 `read` |
| Views·Macros·Triggers 읽기 | `ticket_views:read macros:read triggers:read` |
| Guide·Community 읽기 | `hc:read` |
| CSAT 읽기 | `satisfaction_ratings:read` |
| custom object 결과 확장 | `custom_objects:read` |
| standard ticket 쓰기 | `tickets:write` |
| Guide·Community 공개/파괴적 쓰기 | `hc:write` |
| 작성자 대행 | `impersonate` |

scope가 현재 gate보다 넓더라도 서버 gate를 우회할 수 없다. scope가 부족하면
`permission_denied`, 만료·취소됐고 refresh할 수 없으면
`reauthorization_required`를 반환한다. 비활성 capability의 도구는 manifest에
남아 있지만 outbound 요청 없이 `not_configured`를 반환한다. capability 추가로
scope가 늘어나면 OAuth 재인증을 요구한다.

## 10. Community API 확장 계약

Community 기능 추가는 범용 URL 도구가 아니라 다음 순서로 진행한다.

1. 공식 API reference와 OpenAPI에서 endpoint, 권한, 플랜, pagination, ID type,
   request/response를 확인한다.
2. 기존 `community` 모듈 중 소유 리소스를 정한다.
3. 공통 client의 검증된 상대 path만 사용해 endpoint 함수를 추가한다.
4. MCP schema, 표준 응답, risk annotation과 capability gate를 선언한다.
5. success, 리소스 고유 permission/validation, 목록이면 pagination, 쓰기면
   위험 입력의 mock contract test를 추가한다. 공통 429/error mapping은
   transport test를 재사용한다.
6. Zendesk sandbox에서 read E2E를 실행하고, 쓰기는 disposable fixture와 별도
   승인이 있을 때만 실행한다.
7. 공식 문서 URL과 마지막 검증일을 capability manifest에 기록한다.

공식 문서가 바뀌어도 임의 URL escape hatch를 추가하지 않는다. 새 endpoint는
위 과정을 거친 명시적 도구로만 공개한다.

## 11. 쓰기 승인과 안전 경계

### 11.1 위험 등급

| 등급 | 예 | 기본 |
| --- | --- | --- |
| 읽기 | 검색, 목록, 단건, export | 허용 |
| 표준 쓰기 | 내부 메모, 티켓 필드·담당자·태그 수정 | `write_mode=standard`일 때 허용 |
| 공개 쓰기 | 공개 답변, article publish, Community 생성·수정, subscriber 알림 | 별도 public gate 필요 |
| 파괴적 쓰기 | 삭제, badge 회수 등 되돌리기 어려운 작업 | 별도 destructive gate 필요 |
| 대행 쓰기 | `author_id`, `created_at`, impersonation scope | 별도 impersonation gate와 관리자 권한 필요 |
| 외부 업로드 | Community User Image, badge icon | 별도 external-upload gate 필요 |

MCP annotation은 사용자 경험을 위한 힌트이며 서버측 권한 경계를 대신하지 않는다.

Zendesk Trigger는 내부 메모나 필드 변경도 이메일·webhook 같은 외부 동작으로
바꿀 수 있다. 서버는 계정 자동화의 실제 실행 결과를 예측할 수 없으므로
`write_mode=standard` 활성화 자체를 이 부작용에 대한 운영자 승인으로 취급하고,
연결 상태와 모든 standard write 도구의 설명·응답 metadata에
`account_automation_side_effects_possible=true`를 표시한다. 이 위험을 허용할 수
없는 배포는 `read_only`를 유지해야 한다.

Macro는 이름으로 고정 분류하지 않고 preview 결과를 보고 위험 등급을 올린다.
공개 댓글, 알림, 대행 또는 파괴적 action이 하나라도 있으면 해당 gate와 사람
승인 계약을 적용한다.

### 11.2 고위험 작업 승인

공개·파괴적·대행·외부 업로드 쓰기는 다음 2단계 계약을 사용한다.

1. 이 도구들은 공통 입력 `execution_mode=preview|apply`를 가지며 기본값은
   `preview`다. preview 호출은 대상, 정규화 payload, 공개 범위, 알림 metadata와
   복구 한계를 저장하고 `approval_request_id`만 반환한다. 같은 MCP 호출자가
   사용할 수 있는 approval token은 발급하지 않는다.
2. 사람은 MCP 호출과 분리된 로컬 대화형 명령
   `zendesk approve <approval_request_id>`에서 동일 preview를 확인한다.
   이 명령만 5분 TTL의 일회성 approval token을 발급할 수 있다.
3. MCP 도구는 동일 canonical payload와 token을 제출할 때 한 번만 실행한다.
   token은 account, tool, target, payload hash, nonce와 만료 시각에 결합하고
   성공·실패와 관계없이 첫 제출에서 소비한다.

단순 `confirm=true`, preview 연속 호출, MCP 호출자 자체의 확인 문구는 승인으로
인정하지 않는다. 배포 설정 gate와 분리된 사람 승인 중 하나라도 없으면
outbound write를 보내지 않는다. approval request/token은 사용자 전용 상태에
`0600`으로 저장하고 5분 뒤 정리하며 로그에서 redaction한다.

### 11.3 감사와 데이터 최소화

- audit log에는 시간, tool, risk class, Zendesk request ID, 대상 resource ID,
  결과 code와 latency만 남긴다.
- 댓글·문서·티켓 본문, 첨부 내용, token과 Authorization header는 남기지 않는다.
- 도구 응답은 필요한 데이터만 포함하고 원시 header를 그대로 노출하지 않는다.
- Zendesk에서 읽은 본문은 `untrusted_user_content`로 표시한다.

## 12. 응답·오류 계약

### 12.1 성공

단건:

```json
{
  "ok": true,
  "data": {},
  "operation_state": "applied",
  "request_id": "zendesk-or-local-request-id"
}
```

읽기 응답은 `operation_state`를 생략한다. 쓰기 응답은 `applied`, `partial`,
`unknown` 중 하나를 반드시 포함한다.

목록:

```json
{
  "ok": true,
  "items": [],
  "has_more": false,
  "next_cursor": null,
  "truncated": false,
  "request_id": "zendesk-or-local-request-id"
}
```

### 12.2 오류

```json
{
  "ok": false,
  "error": {
    "code": "permission_denied",
    "message": "human-readable redacted message",
    "retryable": false,
    "retry_after_seconds": null,
    "request_id": "zendesk-or-local-request-id",
    "operation_state": "not_applied"
  }
}
```

필수 error code:

- `authentication_failed`
- `authentication_retry_required`
- `reauthorization_required`
- `permission_denied`
- `not_found`
- `conflict`
- `validation_error`
- `cursor_expired`
- `article_body_replace_required`
- `unsafe_attachment`
- `rate_limited`
- `upstream_error`
- `timeout`
- `unsupported`
- `not_configured`
- `write_disabled`
- `approval_required`
- `partial_success`
- `outcome_unknown`

`operation_state`는 `not_applied`, `applied`, `partial`, `unknown` 중 하나다.
HTTP 400은 `validation_error`로 변환하고, 401/403/404/409/412/422/429/5xx와
timeout을 서로 구분한다. 오류 문자열을 정상 문자열 결과로 반환하지 않는다.

### 12.3 파일·미디어 출력

- CSV/JSON export와 다운로드 첨부는 JSON envelope의 metadata와 함께
  server-managed artifact를 가리키는 MCP `ResourceLink`를 반환한다.
- image preview는 MCP `ImageContent`로 반환한다.
- text/PDF inspection의 제한된 text만 `data` 안에 inline할 수 있다.
- JSON 안에 대용량 base64, 임의 local destination, 원시 presigned URL을
  반환하지 않는다.
- artifact는 사용자 전용 고정 root와 24시간 TTL을 사용하며 응답에 크기,
  MIME type, item 수와 `truncated`를 포함한다.

## 13. 비기능 요구사항

### 13.1 성능과 제한

- 모든 네트워크 요청에 connect/read/write/pool timeout을 둔다.
- 목록은 호출별 page size와 총 item limit을 강제한다.
- 대량 export는 전체 결과를 한 번에 메모리에 올리지 않는다.
- JSON/CSV export는 사용자 전용 고정 export root에 원자적으로 쓰고, 호출자가
  임의 저장 경로를 정하지 못한다. 응답에는 ResourceLink, 형식, item 수와
  `truncated`를 반환한다.
- 첨부와 사용자 이미지 upload/download는 streaming한다.
- rate limiter는 같은 프로세스의 병렬 호출이 공유한다.

### 13.2 호환성과 플랫폼

- Python 3.10, 3.11, 3.12를 지원한다.
- macOS, Linux, Windows에서 wheel 설치와 stdio handshake를 확인한다.
- 설정과 cache path는 표준 사용자 디렉터리를 사용한다.
- token/config permission을 표현하기 어려운 플랫폼은 동일 수준의 사용자 전용
  ACL을 적용하거나 명확한 `unsupported` 오류로 중단한다.

### 13.3 라이선스

- 저장소의 Apache-2.0 라이선스를 유지한다.
- michaelrice 코드나 테스트를 복사·수정할 경우 원 저작권, LICENSE/NOTICE와
  변경 사실을 보존한다.
- SwiftEq는 관찰된 기능 계약만 참고한다. 확인되지 않은 소스 코드를 복사하지
  않는다.

## 14. 테스트와 출시 수용 기준

### 14.1 자동 테스트

| 계층 | 필수 검증 |
| --- | --- |
| Unit | auth mode 선택, OAuth state/refresh/회전 token race, redaction, pagination, 429, response/error mapping, HTML·ID·host·path validation |
| Tool contract | 모든 canonical 도구의 이름, input/output schema, read/write/risk annotation |
| Mock API | 공통 transport에서 400/401/403/404/409·412/422/429/5xx/timeout mapping 전체를 한 번 검증한다. 각 도구는 success, 고유 validation과 대표 permission 오류만 검증한다. |
| Security | auth fallback 금지, read-only에서 write 0회, payload-bound 사람 승인, attachment SSRF/path traversal/oversize, cross-host auth leak, HTML/notification/impersonation/external upload 차단. 자동 unit test는 주입한 test-only approval verifier를 사용하며 production package에는 bypass 설정·issuer를 포함하지 않음 |
| Package | wheel 설치 후 새 환경에서 import와 stdio MCP handshake |
| 자동 Sandbox E2E | OAuth/API token 각각 read canary만 실행 |
| 수동 Release Check | disposable sandbox 리소스와 실제 로컬 사람 승인 명령을 사용한 write E2E. CI·운영 계정에서는 자동 실행하지 않음 |

### 14.2 완료 조건

`1.0.0`은 다음이 모두 참일 때만 완료다.

- [ ] 8장의 canonical manifest가 전부 등록되고 exact contract test가 통과한다.
- [ ] KON, michaelrice, SwiftEq의 승인된 기능 합집합이 중복 없이 매핑된다.
- [ ] OAuth와 API token이 각각 독립적으로 인증·조회에 성공한다.
- [ ] `auto` 선택과 실패 시 no-fallback 테스트가 통과한다.
- [ ] OAuth 일부 설정은 API token으로 fallback하지 않고 구성 오류가 된다.
- [ ] OAuth state 검증, refresh 1회, concurrent refresh와 원자 저장 테스트가
      통과한다.
- [ ] OAuth는 활성 capability의 resource-specific scope 합집합과, Support
      활성 시 사용자 승인된 검색용 broad `read` 예외만 요청한다. broad `write`는
      요청하지 않으며 서버 gate는 scope와 독립적으로 적용된다.
- [ ] cursor/offset 목록이 동일한 표준 envelope를 반환하고 2페이지 이상,
      빈 마지막 페이지, 반복 cursor, cross-host next URL을 검증한다.
- [ ] 읽기 429가 최초+2회, 총 sleep 30초 제한 안에서 재시도되고 쓰기는 자동
      재시도되지 않는다. 쓰기 401은 refresh 후에도 자동 replay되지 않는다.
- [ ] 모든 필수 HTTP/error 상태가 표준 error code와 operation state로
      변환된다.
- [ ] 새 설치의 read-only 모드에서 outbound write가 0회다.
- [ ] 공개·삭제·알림·대행·외부 업로드 작업은 gate와 MCP 밖의 사람 승인, 5분 TTL
      single-use payload-bound token이 없으면 outbound write가 0회다.
- [ ] production package에는 approval bypass/issuer가 없고 수동 sandbox write
      check가 실제 로컬 승인 명령을 통과한다.
- [ ] 공개 답변과 내부 메모가 분리되고 내부 메모가 안전한 기본값이다.
- [ ] TicketQuery resolver의 `me/none/id/name/email/phone`, entity name/id,
      include/exclude tags와 TicketProjection custom object 확장 계약이
      search/count/export에서 일관되게 동작한다.
- [ ] 일반 ticket search는 1,000개 한계를 보존하고, 1,000개 초과 export는
      `filter[type]=ticket`, `created_at` sort, 전용 cursor serializer와 1시간
      cursor 만료 계약을 지킨다.
- [ ] attachment가 ID 기반 소속 검증, host/path/size 제한과 streaming을
      통과하고, text/PDF/image/archive manifest inspection 한계를 지킨다.
- [ ] deleted/malware/미검사 첨부는 outbound download와 parser 실행이 0회이고
      `malware_access_override`는 변경되지 않는다.
- [ ] CDN·presigned 최초 요청과 redirect 어디에도 Zendesk Authorization이
      전달되지 않고 local upload의 symlink/TOCTOU 검사가 통과한다.
- [ ] Community posts/comments/topics/votes/content·user subscriptions/tags/
      user images와 조건부 badge categories/badges/assignments가 문서에 명시한
      공식 권한·pagination·HTML·알림 계약을 통과한다.
- [ ] 계정에 없는 조건부 기능이 `unsupported`/`not_configured`를 반환한다.
- [ ] Help Center의 다중 브랜드·locale과 ID wire type 보존을 검증한다.
- [ ] 기존 translation의 body를 upsert에 넣으면 사용 여부를 추측하지 않고
      outbound write 0회와 `article_body_replace_required`로 거부한다. body
      replacement는 content block 평문 치환 경고와 destructive 사람 승인을
      거치고 publish된 대상이면 public gate도 요구한다. publish는 별도 public
      gate를 거쳐 지정 locale translation만 `draft=false`로 update한다.
- [ ] article/translation create와 upsert의 `draft=false`는 outbound write
      0회로 거부되고 publish 도구만 공개 전환할 수 있다. publish 상태에서
      title 변경은 public gate, `draft=true` 전환은 destructive gate와 사람
      승인을 요구한다.
- [ ] Community User Image와 badge icon upload는 external-upload gate와 사람
      승인 없이 presigned URL 요청과 binary upload가 모두 0회다.
- [ ] 사용자별 Community votes는 혼합 upstream cursor 전체에서 Post와 Post
      Comment만 누락 없이 필터링하고 scan cap이면 재개 cursor와 `truncated`를
      반환한다. User Subscription POST는 기존 `include_comments` 변경을 포함한
      upsert/read-back/idempotency 계약을 지킨다.
- [ ] legacy/survey CSAT는 각 backend의 공식 필터만 보내며 지원하지 않는
      조합을 client-side 부분 필터로 흉내 내지 않는다. ISO-8601 입력은 각각
      epoch seconds와 milliseconds로 정확히 변환된다.
- [ ] connection status와 enabled locale 조회가 비밀값 없이 동작하고, 무설정
      MCP handshake는 네트워크 호출 없이 성공한다.
- [ ] Macro 부분 성공이 `partial_success`와 복구 정보를 반환한다.
- [ ] Macro preview의 실제 action이 공개·알림·대행·파괴적이면 위험 등급과
      승인 요구가 동적으로 승격된다.
- [ ] Python 3.10–3.12 CI가 test→wheel install→MCP handshake 순서로 통과한다.
- [ ] 로그 snapshot에 token, Authorization, 고객 본문이 포함되지 않는다.
- [ ] export/download는 ResourceLink, image는 ImageContent를 사용하고 JSON
      대용량 base64와 임의 저장 경로를 노출하지 않는다.
- [ ] 설치, 두 인증 방식, scope/권한, 쓰기 gate, migration, rollback,
      수동 복구 문서가 있다.
- [ ] OAuth rollback은 최신 회전 token을 보존하거나 재인증하며 7일 지난
      credential backup을 사용하지 않는다.
- [ ] 운영 Zendesk 계정에는 별도 승인 없이 쓰기 E2E를 실행하지 않는다.

테스트 개수나 coverage 숫자만으로 출시를 승인하지 않는다. 위 위험 계약이
실행 가능한 테스트로 고정됐는지가 기준이다.

## 15. 구현·출시 순서

각 단계는 독립 pre-release로 검증할 수 있지만, 모든 단계가 끝나야 `1.0.0`
완료다.

1. **공통 기반**: config, dual auth, client, 표준 계약, risk gate, 기존 tool
   manifest 전환
2. **Support 핵심**: tickets, conversations, comments, attachments
3. **운영 기능**: users, groups, organizations, metadata, Views, Macros, Triggers,
   tags, time tracking, Git-Zen
4. **Guide·CSAT**: 브랜드·locale, article/search/export/write, 권한 metadata,
   KB resource, CSAT
5. **Community**: posts→comments→topics→votes→subscriptions/tags→images→badges
6. **출시**: prompts, 문서, Python matrix, wheel/MCPB/Registry, migration,
   sandbox E2E

각 단계는 공통 client와 계약을 먼저 재사용한다. 후속 단계용 추상화나
placeholder tool을 미리 만들지 않는다.

## 16. 마이그레이션

### 16.1 기존 KON 사용자

| 기존 | `1.0.0` |
| --- | --- |
| `get_ticket` | `zendesk_get_ticket` |
| `get_ticket_comments` | `zendesk_get_ticket_conversation` |
| `create_ticket_comment(public=true)` | `zendesk_post_public_reply` 또는 `zendesk_post_internal_note` |
| `get_community_posts` | `zendesk_list_community_posts` |
| `get_community_post_comments` | `zendesk_list_community_comments` |
| `create_community_post_comment` | `zendesk_create_community_comment` |
| `update_community_post_comment` | `zendesk_update_community_comment` |
| `update_community_post` | `zendesk_update_community_post` |
| `get_community_topics` | `zendesk_list_community_topics` |
| `ZENDESK_API_KEY` | `ZENDESK_API_TOKEN` |

옛 이름 alias는 제공하지 않는다. release note에 mapping과 표준 응답 변경을
명시하고 설정 검사 명령은 비밀값을 출력하지 않은 채 누락·충돌만 알려준다.

### 16.2 michaelrice 사용자

- OAuth config는 사용자 전용 `0600` backup 후 새 schema로 한 번만
  migration한다. backup은 7일 뒤 또는 rollback 확인 직후 삭제한다.
- migration은 임시 파일+원자 rename을 사용하고 실패 시 기존 파일을 유지한다.
- migration 뒤 token이 refresh되면 backup의 credential을 복원하지 않는다.
  rollback 도구는 현재의 최신 access/refresh token을 보존하면서 schema만 이전
  형식으로 변환하고, 변환할 수 없으면 재인증을 요구한다.
- 임의 attachment URL/저장 경로와 mixed string response는 호환하지 않는다.
- 시간 추적 custom field ID는 자동 탐색 또는 명시 설정으로 이동한다.

### 16.3 SwiftEq 사용자

- 관찰된 21개 기능을 canonical manifest에 매핑한다.
- SwiftEq 이름·응답의 binary 호환은 목표가 아니다.
- 설치된 SwiftEq MCP는 이 서버 출시와 별개로 유지할 수 있다.

## 17. Rollback과 쓰기 복구

- package rollback은 직전 안정 버전 pin으로 수행한다.
- migration 전 `0600` OAuth config backup을 남기되, 새 버전 실패 시 package와
  schema만 되돌리고 최신 회전 credential은 유지한다. 안전하게 변환할 수
  없거나 refresh token이 무효면 오래된 backup을 쓰지 않고 재인증한다.
- 서버 릴리스 rollback은 이미 Zendesk에 반영된 외부 쓰기를 자동 취소하지
  않는다.
- 모든 create/update/delete 결과는 resource ID, Zendesk request ID,
  operation state와 가능한 수동 복구 절차를 반환한다.
- 결과가 불명확한 ticket/article/Community update는 단건 read-back으로 요청한
  stable field를 비교한다. create는 응답 resource ID가 없으면, delete는
  대상 부재가 누구의 작업인지 입증되지 않으면, subscriber 알림은 전달 결과를
  조회할 수 없으면 `outcome_unknown`으로 남긴다.
- ticket audits/events는 이번 범위가 아니므로 reconciliation 근거로 가정하지
  않는다. `outcome_unknown`은 운영자가 Zendesk에서 수동 확인하기 전
  재시도하지 않는다.
- 삭제처럼 API가 복구를 제공하지 않는 작업은 preview에 그 사실을 표시한다.

## 18. 위험과 대응

| 위험 | 대응 |
| --- | --- |
| 범위가 커서 출시가 장기화됨 | 공통 기반부터 단계별 pre-release; 기능을 숨긴 placeholder는 만들지 않음 |
| OAuth와 API token 동작 차이 | 공통 client 이후 동일 contract test와 양쪽 sandbox read E2E |
| Zendesk 플랜·권한 차이 | 조건부 기능을 감지하고 `unsupported`, `not_configured`, `permission_denied` 구분 |
| Community HTML의 prompt injection/XSS | untrusted 표시, 허용 규칙 검증, Zendesk 정규화 read-back |
| 공개 알림·대행으로 고객 영향 | 기본 false, 별도 gate, payload-bound preview/approval |
| attachment SSRF/credential leak | ID 기반 재조회, host allowlist, redirect auth 제거, fixed cache root |
| Macro·timeout 부분 반영 | 가능한 단일 update, operation state와 partial recovery 정보 |
| API 문서 변경 | 10장의 공식 문서 기반 확장 절차와 contract test |
| upstream 코드 라이선스 누락 | Apache-2.0 attribution과 NOTICE 검토를 release gate에 포함 |

## 19. 고정된 제품 결정

- API token과 OAuth를 둘 다 유지한다.
- OAuth가 권장 경로이며 API token은 호환·운영 경로다.
- 기존 도구 이름과 응답 형식은 호환하지 않는 `1.0.0`으로 정리한다.
- michaelrice의 모듈과 테스트를 KON 저장소로 이식해 구현 코어로 삼고,
  공통 transport·인증·첨부·쓰기 승인은 이 PRD 계약으로 교체한다.
- 7.5장이 선정한 Community 공식 리소스는 출시 범위에 포함한다. 플랜 종속
  기능도 구현하되 사용할 수 없는 계정에서는 구조화된 조건부 오류를 낸다.
- 임의 attachment URL/경로와 archive 자동 해제는 기능 합집합의 승인된 보안
  예외이며 안전한 ResourceLink·inspection으로 대체한다.
- 범용 Zendesk HTTP 도구는 만들지 않는다.
- 모든 단계가 완료돼야 PRD 완료다.

## 20. 근거 자료

### 로컬·비교 구현

- 현재 KON 등록: `src/zendesk_mcp_server/server.py`
- 현재 KON API client: `src/zendesk_mcp_server/zendesk_client.py`
- [michaelrice README](https://github.com/michaelrice/zendesk-mcp/blob/8313e117094d005dcf1fc48ffb6f9197bc60a712/README.md)
- [michaelrice server registration](https://github.com/michaelrice/zendesk-mcp/blob/8313e117094d005dcf1fc48ffb6f9197bc60a712/src/zendesk_mcp/server.py)
- [michaelrice OAuth](https://github.com/michaelrice/zendesk-mcp/blob/8313e117094d005dcf1fc48ffb6f9197bc60a712/src/zendesk_mcp/auth.py)
- [michaelrice attachments](https://github.com/michaelrice/zendesk-mcp/blob/8313e117094d005dcf1fc48ffb6f9197bc60a712/src/zendesk_mcp/tools/attachments.py)
- [michaelrice macros](https://github.com/michaelrice/zendesk-mcp/blob/8313e117094d005dcf1fc48ffb6f9197bc60a712/src/zendesk_mcp/tools/macros.py)

### Zendesk 공식 문서

- [Authentication](https://developer.zendesk.com/documentation/authentication/)
- [OAuth grant types and scopes](https://developer.zendesk.com/api-reference/ticketing/oauth/grant_type_tokens/)
- [Pagination](https://developer.zendesk.com/api-reference/introduction/pagination/)
- [Rate limits](https://developer.zendesk.com/api-reference/introduction/rate-limits/)
- [Accessing end-user uploaded attachments](https://developer.zendesk.com/documentation/ticketing/managing-tickets/accessing-end-user-uploaded-attachments/)
- [Working with URL properties](https://developer.zendesk.com/documentation/api-basics/best-practices/working-with-url-properties/)
- [Ticket Attachments](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket-attachments/)
- [Ticket Search and Export Search](https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/)
- [Help Center API introduction](https://developer.zendesk.com/api-reference/help_center/help-center-api/introduction/)
- [Translations](https://developer.zendesk.com/api-reference/help_center/help-center-api/translations/)
- [Content block API limitations](https://developer.zendesk.com/documentation/help_center/help-center-api/content-blocks-limitations/)
- [Satisfaction Ratings](https://developer.zendesk.com/api-reference/ticketing/ticket-management/satisfaction_ratings/)
- [CSAT Survey Responses](https://developer.zendesk.com/api-reference/ticketing/ticket-management/csat_survey_responses/)
- [Posts](https://developer.zendesk.com/api-reference/help_center/help-center-api/posts/)
- [Post Comments](https://developer.zendesk.com/api-reference/help_center/help-center-api/post_comments/)
- [Topics](https://developer.zendesk.com/api-reference/help_center/help-center-api/topics/)
- [Votes](https://developer.zendesk.com/api-reference/help_center/help-center-api/votes/)
- [Content Subscriptions](https://developer.zendesk.com/api-reference/help_center/help-center-api/content_subscriptions/)
- [Content Tags](https://developer.zendesk.com/api-reference/help_center/help-center-api/content_tags/)
- [User Subscriptions](https://developer.zendesk.com/api-reference/help_center/help-center-api/user_subscriptions/)
- [User Images](https://developer.zendesk.com/api-reference/help_center/help-center-api/user_images/)
- [Badge Categories](https://developer.zendesk.com/api-reference/help_center/help-center-api/badge_categories/)
- [Badges](https://developer.zendesk.com/api-reference/help_center/help-center-api/badges/)
- [Badge Assignments](https://developer.zendesk.com/api-reference/help_center/help-center-api/badge_assignments/)
