# Time Tracking 앱 호환성 보완

## 확인한 누락

현재 `get_time_tracking`은 audit metadata.custom.time_spent만 읽고,
`log_time`은 내부 메모와 ticket metadata.time_spent만 기록한다.
따라서 Zendesk Time Tracking 앱의 누적/마지막 작업 시간 필드를 조회하거나
누적하는 기능은 아직 구현되지 않았다. 기존 audit 테스트 통과는 이를 증명하지 않는다.

근거:

- [Zendesk 앱 설정](https://support.zendesk.com/hc/en-us/articles/4408828227098-Setting-up-the-Time-Tracking-app): 설치 시 두 개의 커스텀 티켓 필드를 생성한다.
- michaelrice/zendesk-mcp `8313e117094d005dcf1fc48ffb6f9197bc60a712`의
  `src/zendesk_mcp/tools/time_tracking.py`: 계정 고정 필드 ID 두 개를 읽고 누적값을 갱신한다.
  해당 코드를 복사하지 않고 기능 계약만 참고한다.

## 구현 순서

1. Settings에 `ZENDESK_TIME_TRACKING_TOTAL_FIELD_ID`,
   `ZENDESK_TIME_TRACKING_LAST_FIELD_ID`를 추가한다.
   두 값 모두 없는 경우 기존 metadata 방식을 유지한다. 한 값만 있거나,
   양의 정수가 아니거나 같은 ID이면 구성 오류로 중단한다. upstream 계정 ID는 사용하지 않는다.
2. 두 필드가 설정된 경우 get_time_tracking은 ticket custom_fields를 읽어
   `total_time_spent_sec`, `time_spent_last_update_sec`와 backend 구분을 반환한다.
   필드 누락/잘못된 값은 0으로 추측하지 않는다. 실제 null과 숫자 문자열 처리 계약을 테스트한다.
3. 기존 log_time의 duration 문자열 및 내부 메모 입력을 유지한다.
   앱 backend에서는 duration을 초로 변환하고 현재 누적값에 더해 두 필드를 한 번의 PUT으로 갱신한다.
   metadata backend는 기존 동작을 유지한다. 두 backend를 동시에 쓰지 않는다.
4. 앱 누적 갱신은 읽은 updated_at을 safe_update/updated_stamp에 묶는다.
   충돌이나 불명확한 응답을 자동 재시도하지 않는다. read-only는 PUT 0회,
   앱 필드 구성/값 오류는 metadata backend로 fallback하지 않는다.
5. MCP 설명과 migration에 backend 선택, 초 단위, 필드 설정, 동시 수정 충돌을 명시한다.
   기존 canonical 도구 수/이름을 유지한다.
6. 설정/조회/누적/충돌/잘못된 필드 및 읽기 전용 테스트를 실패 재현 후 구현한다.
   실제 운영 Zendesk 필드 생성이나 시간 기록은 수행하지 않는다.

전체 구현 전까지 기능 합집합 및 Time Tracking 완료 조건은 미완료다.

## 구현 진행

- 두 필드 ID의 paired/distinct/positive 구성 검증과 앱 backend의 조회/누적 PUT을 구현했다.
- null은 아직 기록되지 않은 0초로, ASCII 숫자 문자열은 정수 초로 읽는다. 음수/불리언/비숫자 값과 필드 누락/중복은 거부한다.
- 갱신은 내부 메모와 두 필드를 한 PUT에 담고 safe_update/updated_stamp를 사용한다.
- 잘못된 값/시각은 PUT 0회, conflict는 PUT 1회이며 read_only에서 추가 PUT이 없음을 테스트했다.
- 전체 474 passed. MCP 설명은 양 backend를 구분하도록 수정했다.
- 후속 검증: 실제 Settings/build_ticket_tools/MCP dispatch 경로로 앱 필드 설정을 읽고 내부 메모+두 필드+safe_update PUT을 생성하는 통합 테스트를 추가했다. 누락/중복/문자열 ID 필드 오류도 확인했다. 5,000자리 구성값과 duration/비ASCII duration의 실패를 재현하고 필드 ID 20자리·duration 64자 경계를 추가했다. migration에 backend 선택/초 단위/null/충돌/설정 안내를 작성했다. frozen 전체 477 passed.
- 설치/호환성 검증: Python 3.10.17/3.11.16 각각 전체 477 passed. 새 `/tmp/zendesk-time-wheel-5gcPD8`의 sdist→wheel을 Python 3.12.5 새 venv에 설치한 뒤 저장소 밖 site-packages에서 앱 설정/조회/누적 3600+60=3660, 단일 safe_update PUT payload를 HTTP stub으로 확인했다. 실제 설치 CLI stdio initialize/101 tools 및 duration maxLength=64도 통과했다.
- 실제 계정 점검: 저장 OAuth로 ticket_fields 목록을 조회했으나 permission_denied여서 두 필드 ID를 확인하지 못했다. 필드 이름/ID를 추측하거나 OAuth scope를 넓히지 않았다.
- 남은 검증: 실제 계정의 확인된 Time Tracking 필드로 읽기 및 별도 승인된 sandbox 쓰기 E2E. 운영 쓰기는 수행하지 않았다.
