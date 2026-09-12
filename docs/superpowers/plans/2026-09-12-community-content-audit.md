# Community 콘텐츠 계약 감사

대조 기준: PRD 7.5와 구현 `70d281a9750eba5513510b788f45500e961819c1`.
실계정 요청 없이 source와 테스트 assertion을 대조했다. 전체 Community 수용 승인이 아니다.

## 확인한 경로

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

권한·알림·첨부·구독·badge의 나머지 세부 조건은 별도 대조 대상이다.
CI `34675808818`은 위 기준 커밋에서 성공했지만, 위 누락 조건을 검사하지 않는 성공이다.
테스트 계정 재요청·운영 쓰기·병합·배포는 하지 않는다.
