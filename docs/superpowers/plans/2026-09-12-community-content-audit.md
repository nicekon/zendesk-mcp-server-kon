# Community 콘텐츠 계약 감사

대조 기준: PRD 7.5와 구현 `70d281a9750eba5513510b788f45500e961819c1`.
실계정 요청 없이 source와 테스트 assertion을 대조했다. 전체 Community 수용 승인이 아니다.

## 확인한 경로

### 이미지 경로 이탈·멘션 자식 태그 차단

추가 독립 검토에서 `_safe_image`의 prefix 검사가 `/hc/user_images/../../articles/123`
및 canonical-host absolute 변형을 허용함을 재현했다. 기존 업로드 결과 경로 검증을
공통 `_valid_user_image_path`로 옮겨 HTML에도 적용하고, 인코딩된 경로 구분자·dot
우회를 막도록 percent-encoded 경로를 거부한다. 경로는 `/hc/user_images/` 아래
비어 있지 않은 단일 segment이며 dot/dotdot, query/fragment, 역슬래시는 허용하지 않는다.
또한 `<x-zendesk-user>` 안의 자식 태그를 거부해 직접 숫자 텍스트만 검증한다.
9개 악성 HTML 입력을 Post/Comment create/update 및 preview/apply에서 검증하며,
정상 상대·canonical absolute 이미지와 단순 숫자 멘션은 유지한다. 업로드 응답의
인코딩 경로도 같은 검사로 실패함을 검증한다. HTML 실행이나 실제 업로드는 없다.

### 작성자·시각 대행의 관리자 정책 보완

PRD 7.5는 author_id/created_at 대행에 관리자 권한도 요구하지만, 기존 공통
쓰기 경로는 impersonation 설정과 승인만 검사했다. Post/Comment 생성의
대행에 한해 gate·승인 확인 뒤 현재 사용자 GET으로 양의 ID와 admin 역할을
검증한다. 조회 실패·비관리자·누락 역할에는 POST하지 않는다. 16개 합성 사례와
조건부 OAuth scope 회귀를 포함해 전체 797 passed.
[Posts](https://developer.zendesk.com/api-reference/help_center/help-center-api/posts/)와
[Post Comments](https://developer.zendesk.com/api-reference/help_center/help-center-api/post_comments/)의
공식 대행 권한은 Help Center manager다. admin 검사는 PRD의 별도 로컬 제한이며
manager 권한을 증명하거나 Zendesk 권한 검사를 대체하지 않는다. Community 대행
활성 시 users:read scope가 추가되며 재인증·승인 재생성 절차는 migration 문서에 명시했다.

### 배지·구독 검증 근거 갱신 (b4b8c06 기준)

`uv run pytest -q tests/test_community.py -k 'subscription or badge'`를 실행해
40개 사례가 통과했다. 다음은 테스트 이름만 찾은 결과가 아니라 source와 assertion을
함께 대조한 범위다. 실제 Zendesk 요청은 하지 않았다.

| 요구 | 확인한 동작과 근거 | 남은 경계 |
| --- | --- | --- |
| User Subscription upsert | `test_user_subscription_upsert_is_idempotent_when_the_setting_already_matches`: 설정이 같으면 POST 0. `test_user_subscription_upsert_previews_change_and_reads_back_after_approval`: 현재/제안 표시와 POST 후 GET, applied 반환 | 실제 서버의 follow 갱신·알림 효과는 미검증 |
| 구독 read-back 실패 | `test_subscription_write_with_unverified_readback_is_unknown_without_retry`: 잘못된 boolean·누락·빈 결과·불완전 page를 성공으로 처리하지 않고 쓰기 1회만 수행 | 서버가 저장했는지 판정할 수 없는 경우 unknown 유지 |
| 배지 삭제 cascade | `test_badge_delete_binds_cascade_snapshot_to_approval`: 개수·복구 불가 표시, assignment ID 집합이 바뀌면 재승인 필요. `test_badge_delete_cannot_approve_unknown_cascade`: 중복·다른 badge·권한 오류에는 승인 요청을 만들지 않음 | preview 이후 재조회와 DELETE 사이의 서버 변경까지 원자적으로 보호하는 계약은 아님 |
| 배지 부여·회수 | `test_badge_assignments_require_public_impersonation_and_destructive_gates`: public/impersonation 및 destructive 정책과 고정 경로 연결 | 로컬 gate가 Help Center manager 역할이나 Gather Professional 플랜을 증명하지 않음 |
| 목록·범위 | content/user subscriptions 및 badge 목록 테스트: scope 필터 보존, cursor/local offset 재개, 고정 endpoint | 실계정의 모든 역할·플랜·리소스 조합 검증과 구별 |

PRD 7.5의 계정별 제품 부재/권한 구분은 이 표로 완료 처리하지 않는다.
일반 HTTP 403→permission_denied 처리와 로컬 capability 비활성→not_configured는
각각의 근거일 뿐, 제품 미구매 계정의 실제 응답을 관찰한 근거는 없다.

조건부 기능 오류 추가 대조: `_capability_error`는 client 생성 전에 비활성 설정을
not_configured로 반환하며, client의 404는 not_found다. 공식 Badges 문서는 Gather
Professional·Help Center manager 조건을 명시하지만 미구매 계정의 고유 오류 코드는
정의하지 않는다. 따라서 일반 403/404를 제품 미지원으로 재분류하지 않는다.
PRD의 계정 제품별 구분은 아직 충분히 입증되지 않았으며, 공식 판별 계약이나
비밀값을 제거한 실제 응답 증거 없이 추정 구현하지 않는다. 테스트 계정을 재요청하지 않는다.

Community 대행 scope 이전의 로컬 근거는 별도로 추가했다.
`tests/test_config.py::test_old_grant_requires_relogin_without_rewriting_tokens`의
community/users:read 사례는 이전 grant를 저장한 뒤 대행 설정으로 불러오면
oauth_relogin_required를 반환하고 credential 파일의 바이트를 그대로 유지함을 검증한다.

- Post 생성·수정과 Comment 생성·수정은 `_approved_request`를 공유한다.
  preview는 승인 요청만 저장하고 apply는 gate와 일회용 승인을 확인한 뒤 요청한다.
- Topic 생성은 별도 승인 경로이며 Topic 수정·삭제는 공통 경로다.
  [공식 Topics](https://developer.zendesk.com/api-reference/help_center/help-center-api/topics/)의
  생성·수정 권한은 Help Center manager다. 현재 일반 public gate는 이 역할의 증명이 아니며,
  실제 역할 권한은 Zendesk 응답으로 판정해야 한다. admin과 manager를 임의로 동일시하지 않는다.
- `_post_payload`의 details 및 `_comment_payload`의 body는 `_valid_html`을 사용한다.
  Topic description은 공식 문서에서 단순 문자열이며 HTML 지원으로 추정하지 않는다.

## 확인된 구현 누락

1. **정규화된 HTML 재조회:** `_approved_request`는 성공한 POST/PUT 응답을 바로 반환한다.
   Post/Comment 생성·수정 어디에도 후속 단건 GET이 없다. PRD의 저장 후 read-back 요구를
   충족하지 않는다. 쓰기 성공 뒤 GET 실패를 쓰기 실패/미적용으로 표현하거나 POST/PUT을
   재시도하면 안 된다. 기존 `test_community_post_update_requires_local_public_approval`과
   comment update 테스트는 최종 호출이 PUT인지 확인할 뿐 저장 후 본문을 확인하지 않는다.
2. **읽기 콘텐츠 표시·분리:** Community `_get`은 client 결과를 그대로 반환하고 목록 수집기는
   raw items를 반환한다. Community 코드에는 `untrusted_user_content` 표시나 HTML/plain text
   분리 처리가 없다. Ticket/Guide에 같은 이름의 표시가 있다는 사실은 Community 근거가 아니다.

## 다음 보완의 검증 기준

- Post/Comment 생성 및 HTML 수정의 실제 공통 경로에서 승인 전 outbound write 0을 유지한다.
- 성공한 쓰기에 한해 검증된 리소스 ID와 기존 고정 endpoint로 GET한다. 응답의 임의 URL을
  따라가지 않고, 서버가 정규화한 본문을 원문과 동일해야 한다고 강제하지 않는다.
- 잘못된 생성 ID, GET 오류, 다른 ID의 응답, 잘못된 본문 형태를 구분하며 쓰기 재전송 0을 검증한다.
- 읽기와 read-back에서 원본 HTML을 보존하고 plain text를 별도 필드로 제공한다.
  사용자 작성 콘텐츠 표시가 MCP 응답까지 남는지 확인한다. HTML 실행이나 이미지 다운로드는 없다.
- 기존 모의 응답도 실제 리소스 envelope를 반환하도록 수정한다. 예를 들어 공용 StubClient가
  모든 쓰기에 post를 반환하는 현재 형태는 comment 저장 후 검증을 증명할 수 없다.

## 저장 후 본문 재조회 보완

위 1번의 누락을 `_approved_request`의 성공한 Post/Comment 본문 쓰기 공통 경로에서 보완했다.
생성은 검증된 반환 ID, 수정은 승인된 대상 ID로 고정 endpoint를 조회한다. 임의 URL은 무시한다.
저장된 본문은 문자열·리소스 ID 일치를 검증하되 제출 HTML과의 동일성은 요구하지 않는다.
실패는 partial_success/applied/retryable=false로 반환하며 가능한 경우 복구용 ID와 경로를 제공한다.
20개 합성 사례로 네 소비자의 정규화·조회 실패·다른 ID·잘못된 본문·잘못된 생성 ID를 검증했고,
추가 테스트로 실패한 쓰기의 no-read-back/no-replay를 검증한다. 운영 쓰기 증거는 아니다.
## 읽기 콘텐츠 표시·분리 보완

위 2번은 Community 공통 `_get`에서 리소스와 목록 항목의 원문을 보존하며 보완했다.
문자열 콘텐츠가 있는 항목에 untrusted_user_content와 필드별 plain_text를 제공한다.
body/details는 기존 Ticket HTMLParser를 공통 모듈로 옮겨 텍스트를 추출하며,
title/name/description은 원래 일반 텍스트로 보존한다. HTML 실행·이미지 요청은 없다.
파서 실패 시 원문과 plain_text_unavailable를 반환한다. 보안 sanitizer라고 주장하지 않는다.
7개 읽기 경로·잘못된 HTML·저장 후 조회·실제 MCP 전달 회귀 포함 전체 723 passed.
이 증거는 두 콘텐츠 누락의 로컬 보완 근거이며 Community의 나머지 조건을 대체하지 않는다.

## 알림 미리보기 대상 표시 후속

PRD 7.5의 알림 미리보기는 대상 topic/post ID와 follower_count 또는
recipient_count_unknown을 요구한다. ID는 승인 payload에 있었지만 MCP preview
응답에는 없었으므로, Post 생성은 topic_id, Comment 생성은 post_id를 표시하도록
보완했다. 기본 notify_subscribers=false, 승인 payload 결합과 apply 경로는 유지한다.
`test_community_notification_previews_report_api_follower_counts`에서 양수·0은
알려진 수로, null/boolean/음수/문자열은 unknown으로 표시하며 두 대상 ID와
outbound_write=false를 검증한다. 이 검증은 모의 GET 응답이며 실제 알림 발송은 없다.

## User Subscription 대상 검증 후속

[공식 User Subscriptions](https://developer.zendesk.com/api-reference/help_center/help-center-api/user_subscriptions/)
계약의 followed_id는 필수 integer다. 공통 조회가 이를 검증하지 않아 boolean true를 ID 1로
오인할 수 있었으므로, 비교 전에 양의 정수임을 검증한다. 기존 구독 확인과 쓰기 후 재조회에
동일하게 적용한다. 잘못된 실행 모드도 GET/no-op 판단 전에 거부한다.
5개 실패 재현 후 회귀를 추가했으며 전체 728 passed. 실제 follow 요청은 없다.

추가로 CI 34676130829에서 patched Python 3.10/3.11/3.12의 HTMLParser가
`<![invalid]>`를 unknown_decl 기본 no-op으로 버리는 차이를 발견했다.
공통 parser에서 unknown_decl을 명시적 ValueError로 처리해 양쪽 소비자의 원문 보존
fallback을 일관되게 사용한다. Python 3.11.16 별도 환경 및 로컬 3.12 환경에서
각각 전체 728 passed를 확인했다. 이전 723 passed는 로컬 증거이지 해당 CI 성공이 아니다.

## 쓰기 HTML 선언 검증 후속

같은 stdlib 차이를 쓰기용 `_CommunityHTMLValidator`에서도 확인했다. patched 3.11.16은
알 수 없는 선언을 허용했고, 로컬 3.12.5는 AssertionError가 도메인 경계 밖으로 나갈 수 있었다.
unknown_decl을 명시적으로 거부하고 기존 `_valid_html` 오류 처리에 AssertionError를 포함했다.
Post/Comment 생성·수정의 preview/apply 모두 승인 저장·네트워크 전에 validation_error를
반환하는 회귀를 추가했다. Python 3.11/3.12 각각 전체 732 passed.

Posts의 상태 쓰기 4종, 목록 상태 필터 5종 및 정렬 6종은
[공식 Posts](https://developer.zendesk.com/api-reference/help_center/help-center-api/posts/)와
대조해 일치함을 확인했다. 목록의 none을 쓰기 status 값으로 임의 확대하지 않았다.

권한·알림·첨부·구독·badge의 나머지 세부 조건은 별도 대조 대상이다.
CI `34675808818`은 위 기준 커밋에서 성공했지만, 위 누락 조건을 검사하지 않는 성공이다.
테스트 계정 재요청·운영 쓰기·병합·배포는 하지 않는다.
