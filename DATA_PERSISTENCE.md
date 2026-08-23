# DinoBot 데이터 보존 정책

DinoBot의 서버 설정, 티켓, 상점, 거래, 포인트, 라이센스, 복구키 등 운영 데이터는 **Git 저장소가 아니라 PostgreSQL (`DATABASE_URL`)** 에 저장됩니다.

## 코드 수정/재배포 시

- `CREATE TABLE IF NOT EXISTS`와 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 방식의 비파괴 마이그레이션만 사용합니다.
- `DROP TABLE`, `TRUNCATE`, 전체 데이터 삭제 마이그레이션을 배포 코드에 사용하지 않습니다.
- `legacy_main.py`는 호환성/기존 기능 보존용으로 유지합니다.
- 새 기능은 가능한 한 별도 모듈로 추가하여 기존 데이터 구조를 직접 교체하지 않습니다.

## Render 운영 시 필수

Render Web Service의 디스크는 데이터베이스 대용으로 사용하지 않습니다. `DATABASE_URL`은 외부 PostgreSQL/Supabase 등 영속 DB를 가리켜야 합니다.

필수 환경변수 예시:

```text
DATABASE_URL=postgresql://...
DISCORD_TOKEN=...
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
SESSION_SECRET=고정된-랜덤-문자열
REDIRECT_URI=https://<render-domain>/auth/callback
DASHBOARD_REDIRECT_URI=https://<render-domain>/dashboard/callback
```

`SESSION_SECRET`은 재배포마다 새 값이 되지 않도록 반드시 Render Environment Variables에 고정해 주세요.

## 기능 배치 원칙

### Discord에서 관리

`/메인설정`, `/메인설정언어`, `/메인설정시간대`, `/메인설정채널`처럼 자주 확인하거나 즉시 바꾸는 기본 설정.

### 웹 Control Center에서 관리

상점/상품, 재고, 거래, 출금, 티켓 세부설정, 인증, 로그, 백업, 복구키, 서버 관리 등 복잡한 운영 기능.

모든 설정은 PostgreSQL에 저장되므로 소스코드를 업데이트해도 설정값을 초기화하지 않습니다.
