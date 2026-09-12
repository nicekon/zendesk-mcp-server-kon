# 원본 기능 합집합 대응표

2026-09-12, 현재 구현 `8d37ce3ae8eb9c4d081d5b2fc765bc00c5ed2f9e` 기준.
이 표는 기능의 연결 관계이며 전체 동등성·실제 쓰기 검증 완료 선언이 아니다.
PRD 2.1·7·8과 승인된 안전성 변경이 기준이다. 도구 수 합산은 완료 기준이 아니다.

## 대조 근거

- KON: 이 저장소 `origin/main`의 `325ff0aed5d52852461c988fff1e368596b80bb0`,
  `src/zendesk_mcp_server/server.py`를 `git show`로 읽어 원본 9개 도구를 확인했다.
- michaelrice: [고정 revision](https://github.com/michaelrice/zendesk-mcp/tree/8313e117094d005dcf1fc48ffb6f9197bc60a712)의
  tools/*.py와 prompts.py를 GitHub API로 읽고 AST decorator에서 도구 29개,
  프롬프트 2개, KB 리소스 1개를 확인했다. 최신 main으로 바꾸어 비교하지 않았다.
- SwiftEq: 현재 설치 플러그인의 노출된 도구 설명·입력 선언 21개를 읽었다.
  외부 도구를 실행하지 않았으며 구현 소스·테스트는 보지 못했다. 현재 선언이
  9월 4일 스냅샷의 모든 세부 입력과 동일하다는 주장도 하지 않는다.
- 현재: 실제 `build_tools()`의 101개 이름, MCP 등록·dispatch, 도메인 구현을 대조했다.
  `tests/fixtures/tool-contracts.json`은 현재 계약의 회귀 기준이지 원본 동등성 증거가 아니다.

아래 현재 도구 열은 `zendesk_` 접두사를 생략했다. 원본 이름은 그대로 표시한다.
`연결`은 구현 진입점이 있다는 뜻이며 행에 명시하지 않은 모든 입력까지 동등하다는 뜻은 아니다.
`안전 변경`은 승인된 PRD에 따른 차이, `보완`은 아래 G 항목의 남은 차이다.

## KON — 9개 도구

| ID | 원본 | 현재 도구 | 판단 |
| --- | --- | --- | --- |
| K01 | get_ticket | get_ticket | 연결 |
| K02 | get_ticket_comments | get_ticket_conversation | 연결, bounded pagination |
| K03 | create_ticket_comment | post_internal_note, post_public_reply | 안전 변경: 내부/공개 분리 |
| K04 | create_community_post_comment | create_community_comment | 연결 |
| K05 | get_community_posts | list_community_posts | 연결 |
| K06 | get_community_post_comments | list_community_comments | 연결 |
| K07 | get_community_topics | list_community_topics | 연결 |
| K08 | update_community_post_comment | update_community_comment | 연결 |
| K09 | update_community_post | update_community_post | 연결 |

## michaelrice — 29개 도구

| ID | 원본 | 현재 도구 | 판단 |
| --- | --- | --- | --- |
| M01 | zendesk_list_attachments | list_ticket_attachments | 연결 |
| M02 | zendesk_download_attachment | download_ticket_attachment, inspect_ticket_attachment | 안전 변경: 임의 URL·경로·압축 해제 제거 |
| M03 | zendesk_get_comments | get_ticket_conversation | 연결 |
| M04 | zendesk_create_ticket | create_ticket | 연결, 초기 comment 내부 기본값 |
| M05 | zendesk_list_custom_statuses | list_custom_statuses | 연결 |
| M06 | zendesk_get_git_zen_links | get_git_zen_links | PRD 7.2의 링크 추출. 원본의 GitLab state/labels 등 상세 metadata 동등성은 별개 |
| M07 | zendesk_ticket_to_gitlab_context | ticket_to_issue_context | PRD에 따른 tracker 중립 context, 이슈 생성 없음 |
| M08 | zendesk_get_groups | list_groups | 연결 |
| M09 | zendesk_get_group_users | list_group_users | 연결 |
| M10 | zendesk_get_tickets | list_tickets | 연결. 원본 offset/sort_by와 현재 cursor/sort 계약은 다름; 아래 추가 대조 |
| M11 | zendesk_list_macros | list_macros | 연결 |
| M12 | zendesk_preview_macro | preview_macro | 현재 ticket_id+macro_id 기반 실제 적용 preview |
| M13 | zendesk_apply_macro | apply_macro | 안전 변경: 위험별 gate/승인, 단일 PUT. partial 조건은 근거 색인 29번 |
| M14 | zendesk_get_organization | get_organization | 연결 |
| M15 | zendesk_add_tag | add_ticket_tag | 연결, 동시 갱신 보호 |
| M16 | zendesk_remove_tag | remove_ticket_tag | 연결, 동시 갱신 보호 |
| M17 | zendesk_get_ticket | get_ticket | 연결 |
| M18 | zendesk_search_tickets | search_tickets, count_tickets, export_tickets | 구조화 공통 query로 재구성; resolver 하위 조합은 색인 15번 |
| M19 | zendesk_get_time_tracking | get_time_tracking | 설정된 앱 필드·audit backend; 원본 출력 포맷과 별도 계약 |
| M20 | zendesk_log_time | log_time | 연결, 설정된 시간 필드와 쓰기 gate |
| M21 | zendesk_set_ticket_status | set_ticket_status | 연결, closed는 파괴적 gate |
| M22 | zendesk_assign_ticket | search_users, assign_ticket | 현재 ID 할당. 원본 assignee_email/me 직접 입력과 다름; G6 |
| M23 | zendesk_update_ticket | update_ticket | 연결, 공개 댓글·태그는 전용 도구 분리 |
| M24 | zendesk_search_users | search_users | 연결 |
| M25 | zendesk_list_views | list_views | 연결 |
| M26 | zendesk_get_view | get_view | 연결 |
| M27 | zendesk_get_view_tickets | list_view_tickets | 연결 |
| M28 | zendesk_post_comment | post_public_reply | 안전 변경: 공개 전용 gate/승인 |
| M29 | zendesk_post_internal_note | post_internal_note | 연결 |

## SwiftEq — 현재 선언 21개 도구

| ID | 원본 | 현재 도구 | 판단 |
| --- | --- | --- | --- |
| S01 | count_tickets | count_tickets | 연결, 공통 TicketQuery; 세부 필터는 색인 15번 |
| S02 | create_article | create_help_center_article, publish_help_center_article | 안전 변경: draft 생성과 publish 분리, brand_id 사용 |
| S03 | export_help_center_articles | export_help_center_articles | 보완 G3 |
| S04 | find_agent_by_name | search_users | 보완 G6 |
| S05 | get_help_center_article_content | get_help_center_article | 보완 G2 |
| S06 | get_satisfaction_ratings | list_csat, export_satisfaction_ratings, get_satisfaction_ratings | auto 감지 구현됨. get_satisfaction_ratings는 legacy 전용. discriminated schema는 G8 |
| S07 | get_ticket | get_ticket | 연결 |
| S08 | get_ticket_attachment | download_ticket_attachment, inspect_ticket_attachment | 안전 변경: parent ticket 소속 검증·관리 cache·ResourceLink/ImageContent |
| S09 | get_ticket_conversation | get_ticket_conversation | 보완·검증 G5 |
| S10 | list_brands | list_brands | 연결 |
| S11 | list_help_center_categories | list_help_center_categories | 브랜드·locale 완료, 정렬 보완 G4 |
| S12 | list_help_center_sections | list_help_center_sections | 브랜드·locale 완료, category/정렬 보완 G4 |
| S13 | list_permission_groups | list_permission_groups | 연결 |
| S14 | list_ticket_fields | list_ticket_fields | 연결 |
| S15 | list_ticket_forms | list_ticket_forms | active 필터 보완 G7 |
| S16 | list_triggers | list_triggers | active/category/sort 및 usage 대조 G7 |
| S17 | list_user_segments | list_user_segments | 연결, built_in/applicable 포함 |
| S18 | search_help_center_articles | search_help_center_articles | 보완 G1 |
| S19 | search_tickets | search_tickets, export_tickets | 안전·규모 계약: Search 1000 제한과 전용 Export 분리; 세부 필터는 색인 15번 |
| S20 | update_article_translation | upsert_article_translation, replace_article_translation_body, publish_help_center_article | 안전 변경: 기존 body 파괴 위험 및 publish 분리 |
| S21 | update_ticket | update_ticket, add_ticket_tag, remove_ticket_tag, post_internal_note, post_public_reply | 전용 태그·댓글 도구로 조합; 승인 결합 유지 |

## 도구 개수 밖의 기능

- `analyze-ticket`, `draft-ticket-response`: 원본과 현재 두 프롬프트 이름 유지.
  현재 `server.py`는 전용 template을 연결하고 ticket_id를 받는다.
- `zendesk://knowledge-base`: 현재 opt-in 리소스로 locale export backend를 재사용한다.
  ID 기반 데이터·관리 파일 링크·1시간 TTL 계약이다. 거대한 단일 inline KB 응답과는 다르다.
- API token과 OAuth는 둘 다 유지한다. 설정·브라우저 로그인·토큰 회전·모듈화·CI는
  이 도구 표가 아닌 검증 근거 색인의 별도 항목으로 검증한다.

## 남은 구현 및 의미 대조

| ID | 확인된 현재 차이 | 다음 검증/구현 |
| --- | --- | --- |
| G1 | `GuideTools.search_articles`는 query 필수·단일 brand/locale·기존 Article Search. SwiftEq는 query 선택·복수 locales/brands/categories/sections·Unified Search | 공식 Unified Search 계약을 확인하고 구조화 입력과 cursor를 연결. brand ID 검증·활성 locale 검증 유지 |
| G2 | `get_article`는 단일 본문 또는 한 locale 번역만 반환. `locale=all`, section/category/author 이름 결합 없음 | 전체 번역 pagination·본문/이미지 상한 및 metadata 결합 구현. 활성 locale 정책과 충돌하지 않는 입력 계약 명시 |
| G3 | export는 locale 필수, category/section 필터 없음. SwiftEq는 기본 locale·범위 필터 지원 | 기본 locale의 공식 출처 및 category/section 범위 경로 확인. 기존 관리 artifact·streaming·상한 유지 |
| G4 | category·section 목록에 locale은 추가했으나 sort_by/sort_order와 section의 category_id 없음 | 공식 목록 필터·정렬을 구현하고 여러 페이지에서 보존 검증 |
| G5 | 현재 대화는 Comments API만 호출하고 author_role/side/channel을 보강. SwiftEq 선언은 messaging에 Conversation Log 사용, author name·inline image placeholder도 제공 | 공식 Conversation Log 권한·채널 판별·정렬·pagination 조사 후 구현. 현재 channel 문자열 테스트를 messaging 전체 기록의 증거로 쓰지 않음 |
| G6 | `MetadataTools.search_users`는 일반 query만 수용; full-name agent 역할 제한 없음. assign_ticket은 assignee_id/group_id만 수용 | 상담원 찾기·동명이인 처리와 email/me → ID 연결을 검증. 일반 user 검색을 상담원 정확 일치라고 표기하지 않음 |
| G7 | forms active, triggers active/category/sort 필터 없음. trigger 목록은 `/api/v2/triggers.json`만 요청 | 공식 필터·정렬·usage 포함 방식 확인 후 구현; 반환되지 않은 usage를 0으로 조작하지 않음 |
| G8 | CSAT runtime은 backend별 필터를 거부하지만 현재 MCP 입력 schema는 공통 properties만 나열 | PRD 7.4의 discriminated schema를 추가하고 schema/실행의 조건부 거부를 함께 검증 |

### 후속 구현: G1

위 표의 기준 revision 이후 `locales` 배열 기반 Unified Search를 구현했다.
query 선택, plural brand/category/section 필터, ARTICLE 제한, 50건 페이지·opaque
cursor를 지원한다. 선택 브랜드의 활성 locale 합집합으로 검증하고, 브랜드 미지정 시
브랜드 목록을 완전히 수집한다. 기존 singular 입력 검색은 유지하며 두 경로 혼용은
거부한다. 16개 신규 도메인 회귀 및 실제 MCP 호출 회귀, 전체 586 passed가 근거다.
기존 offset cursor를 Unified Search에 재사용하면 안 된다. 실제 다중 브랜드 계정의
검색 결과 동등성은 아직 검증하지 않았다.

### 후속 구현: G3

locale 생략 시 선택 브랜드의 default_locale을 확인하며, category/section 범위를
공식 경로에 적용하는 export를 구현했다. 둘 다 지정하면 section이 우선한다.
공통 page reader가 explicit locale의 활성 여부도 확인하므로 inline/export artifact
경로에 동일하게 적용된다. 21개 신규 도메인 회귀와 MCP CSV/file-link 회귀,
전체 610 passed가 근거다. 실제 계정 전체 export 실증과는 구별한다.

### 후속 구현: G2

`locale=all`은 선택 브랜드의 활성 locale 번역을 cursor 순회로 수집하고,
`embed_images`는 반환 번역 전체에 공통 20 MB 예산을 적용한다. 비활성 언어는
PRD 활성 locale 정책에 따라 제외한다. `include_metadata=true`로 기본 locale의
section/category/author 이름을 ID로 연결하며, 확인되지 않은 이름은 null이다.
5개 신규 도메인 회귀와 MCP metadata/ImageContent 회귀, 전체 618 passed가 근거다.
운영 계정의 전체 번역 실증과 구별한다.

### 후속 구현: G4

category/section의 sort_by·sort_order와 section의 category_id를 구현했다.
공통 페이지 수집기에 필터를 넘기며 선택 브랜드·locale·category 경로를 유지한다.
18개 다중 페이지 조합, 5개 입력 거부, MCP 전달 회귀 및 전체 641 passed가 근거다.
다음은 G6/G7 및 G5/G8이다. 실제 계정별 결과 동등성 증거는 별도다.

### G7 필터 구현 후속

폼 active와 트리거 active/category_id/sort/sort_order를 MCP 입력부터 페이지 수집까지 연결했다.
active 생략은 기존처럼 모두 조회하며 false는 문자열 false로 전송한다. cursor 정렬은
alphabetical/created_at/updated_at/position만 허용한다. 필터 페이지 유지·입력 거부·MCP 전달을
포함한 로컬 전체 655 passed. 공식 근거: [Ticket Forms](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_forms/),
[Triggers](https://developer.zendesk.com/api-reference/ticketing/business-rules/triggers/).
G7의 usage sideload 및 실제 응답 보존 검증은 남아 있다. 필터 구현만으로 G7 전체를 완료 처리하지 않는다.

### G7 usage 구현 후속

`include_usage` 옵션은 공식 네 기간을 include 쿼리에 전달한다. 기본값 false 유지.
항목 내부 필드는 그대로, 페이지 최상위 비-pagination 필드는 `sideloads` 배열에 순서대로 보존한다.
0/null/누락 구분, 다중 페이지 전달, 마지막 페이지 오류, 잘못된 입력 및 MCP 전달 검증 후 전체 662 passed.
공식 문서에 usage 응답 예제가 없어 테스트는 합성 응답의 무손실 보존 증거이며 실제 JSON 형태 확정 증거가 아니다.
G7의 필터·정렬·통계 요청 구현은 반영했으나 계정별 통계 가용성·실제 응답 동등성은 미검증이다.
테스트 계정 요청 또는 운영 변경 없이 G6 및 G5/G8 구현을 이어간다.

### G6 사용자 검색 필터 후속

기존 search_users에 agents_only/exact_name 옵션을 연결했다. 반환된 candidate에 역할·전체 이름
필터를 적용하고 원본 offset/cursor/has_more를 유지한다. 동명이인을 하나로 축약하거나 자동 배정하지 않는다.
limit는 검사 candidate 수이며 비어 있는 결과에도 다음 페이지가 있을 수 있다. API 검색 상한과
인덱싱 범위 밖의 전체 계정 일치 보장은 하지 않는다. 기본 일반 query 동작은 유지한다.
동명이인·관리자·고객 제외·페이지 재개·입력 거부·MCP 전달 포함 로컬 667 passed.
G6의 assignee_email/me 해석과 안전한 배정 연결은 아직 남아 있다.

### G6 이메일/me 배정 후속

assignee_email(primary email 또는 me)을 입력 스키마부터 기존 update_ticket 경로까지 연결했다.
ID와 이메일 동시 지정·잘못된 ID/이메일·읽기 전용은 조회 전에 거부한다. 검색 결과의 primary email
정확 비교와 모든 제공 페이지 완료를 요구하며 동명이인/중복·미발견·정지·비상담원·검색 상한·오류에는 쓰지 않는다.
me는 인증 사용자를 읽어 agent/admin과 suspended=false를 확인한다. 이메일 보조 identity는 해석하지 않는다.
MCP 전달·두 번째 검색 묶음에서 일치 사용자 발견·정상 모의 배정·실패시 write-zero 포함 전체 673 passed. 실제 운영 배정은 수행하지 않았다.
G6 기능 보완은 로컬 구현되었으며 계정별 권한/검색 인덱싱/자동화 효과 검증은 별도다. 다음 G5/G8.

### G8 backend discriminator 후속

CSAT list/export에 legacy/survey/auto oneOf 입력 schema를 추가했다. 기존 runtime 허용 score와 enum을
공유하며 responder_ids는 비어 있을 수 없다. backend별 필터 혼용과 auto에서 양쪽 필터 동시 지정을
MCP 클라이언트 생성 전에 거부하는 실제 handler 회귀가 있다. 리뷰에서 legacy 날짜 입력 계약 누락을 확인해
start_time/end_time ISO-8601 입력을 list/export/auto 감지와 epoch seconds 변환까지 연결했다.
survey는 created_at_start/end와 milliseconds를 유지한다. 혼합 날짜와 알 수 없는 입력 키는 거부한다.
기존 pre-release legacy created_at 입력은 canonical start_time/end_time로 변경해야 한다.
MCP 두 소비자의 세 backend와 timezone 변환·날짜 역전/혼용 거부 포함 전체 677 passed가 로컬 근거다.
G5 메시징과 전체 수용 색인의 추가 대조는 남아 있다.

### G5 Conversation Log 명시 조회 후속

get_ticket_conversation의 source=conversation_log를 공식 endpoint로 연결했다.
공통 cursor collector와 created_at 정렬을 사용하며 raw events를 보존한다. 봇/버튼/첨부/비공개metadata를
댓글 텍스트로 축약하지 않고 data.events에 제공한다. source 기본은 comments이고 기존 첨부 소속 확인은 그대로다.
두 페이지 이벤트 보존·외부 next 무시·권한 오류 no-fallback·MCP source/cursor 전달 포함 680 passed.
자동 채널 선택, 기존 댓글 작성자 이름/이미지 표시 보강은 아직 남아 있어 G5 전체 완료는 아니다.

### G5 자동 선택·작성자 이름 후속

source=auto는 최초 요청의 ticket.from_messaging_channel boolean으로만 경로를 결정한다.
누락/잘못된 값은 거부하고, 응답의 확정 source로 재개하도록 auto+cursor를 막는다.
댓글 author_name은 기존 users sideload를 재사용하며 역할과 독립적으로 이름을 보존한다.
기본 comments 및 첨부 소속 확인은 변경하지 않았다. strict flag/양쪽 경로/이름 없음/실제 MCP
전달 포함 684 passed. 이미지 표시 보강과 전체 기능 수용 감사는 남아 있다.
근거: [Tickets](https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/).

### G5 HTML 이미지 텍스트 표시 후속

댓글 html_body와 log의 HTML content.body 공통 경로에 display_text를 추가했다.
HTML img 위치에 이미지/alt 표식을 남기고 원본 body·첨부·버튼 등은 유지한다. URL 다운로드/HTML 실행은 없다.
표시는 untrusted 데이터이며 sanitization 보장이 아니다. HTML 파싱 실패는 원본을 반환하고
display_text_unavailable로 구분한다. 양쪽 소스·원본 보존·파서 실패 회귀 포함 전체 686 passed.
G1–G8의 명시된 기능 보완은 각 후속 절에 구현 근거가 있으나 전체 요구사항 수용 색인의
resolver 세부 조합·Community 세부 계약·fresh wheel write-zero·미검증 외부 동작은 계속 대조해야 한다.

### 새 설치 write-zero 검증 후속

새 wheel을 별도 venv에 설치하고 isolated Python의 site-packages import를 확인했다.
43개 mutation 도구에 유효한 대표 입력을 넣고 실제 MCP handler/domain/client를 통과시켜
전부 write_disabled임과 HTTP 전송 경계의 non-GET/HEAD 시도 0을 검증했다.
읽기 preflight만 모의 응답으로 제공하며 실계정 요청은 없다. source 전체 687 passed,
새 wheel 전용 검사 1 passed. Linux Python matrix CI에 같은 검사를 추가했다.
수용 색인 11번의 fresh-wheel gap은 이 대표 입력 범위에서 보강됐다. 나머지 resolver/
Community 계약·외부 수동 검증 및 출시 조건은 별도이며 전체 완료로 바꾸지 않는다.

M10의 원본 priority/status 정렬, M06의 원본 상세 GitLab metadata, M19의 표시 포맷은
PRD가 승인한 범위와 추가 대조한다. 원본 입력과 다르다는 이유만으로 위험한 호환 alias나
승인하지 않은 외부 GitLab 조회를 추가하지 않는다. G 항목을 해결해도 색인의 resolver·
Community 세부 계약·fresh wheel write-zero 및 실제 쓰기 미검증 항목이 자동으로 닫히지 않는다.

### TicketQuery 참조 조합·잘못된 후보 ID 후속

검색·건수·내보내기의 공통 resolver에서 잘못된 ID 후보를 제외해 유일한 일치로
오판할 수 있던 user/brand/group/form 경로를 보완했다. 기존 organization 검증처럼
반환된 후보 ID가 잘못되면 ticket 조회 전 upstream_error로 중단한다.
사용자 참조 36조합과 entity 참조 24조합의 정상 전달, 잘못된 후보 차단 회귀를 추가했다.
전체 691 passed. 합성 응답 기반이며 실계정 쓰기·전체 필터 조합의 완료 근거는 아니다.
근거: tests/test_tickets.py의 test_user_reference_variants_reach_all_three_ticket_consumers,
test_entity_name_and_id_references_match_across_ticket_consumers,
test_user_resolver_cannot_discard_malformed_candidates_to_claim_uniqueness,
test_named_entity_resolvers_reject_malformed_ids_in_all_consumers.

다음 구현 순서는 G1 → G2/G3 → G4/G6/G7 → G5/G8이며, 작업 중 공통 경로 결함이
확인되면 그 원인을 먼저 수정한다. 테스트 계정 재요청·운영 쓰기·병합·배포는 하지 않는다.
