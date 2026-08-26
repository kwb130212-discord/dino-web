# DinoBot 데이터 보존 및 보안 정책

DinoBot의 서버 설정, 티켓, 상점, 거래, 포인트, 라이센스, 복구키 등 운영 데이터는 **Git 저장소가 아니라 PostgreSQL (`DATABASE_URL`)** 에 저장됩니다.

## 코드 수정/재배포 시

- `CREATE TABLE IF NOT EXISTS`와 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 방식의 비파괴 마이그레이션을 사용합니다.
- 필수 migration 실패 시 부분적으로 부팅하지 않고 startup을 중단합니다.
- Git 저장소에는 사용자 데이터, OAuth 토큰, 복구키 원문을 저장하지 않습니다.
- 복구키는 HMAC digest로 저장하며, Discord OAuth access/refresh token은 암호화된 상태로 저장합니다.

## Render 운영 시 필수 환경변수

```text
DATABASE_URL=postgresql://...
DISCORD_TOKEN=...
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
SESSION_SECRET=고정된-랜덤-문자열
TOKEN_ENCRYPTION_KEY=고정된-랜덤-문자열
RECOVERY_KEY_PEPPER=고정된-랜덤-문자열
DINO_PUBLIC_BASE_URL=https://dinobotservice.64bit.kr
```

`SESSION_SECRET`, `TOKEN_ENCRYPTION_KEY`, `RECOVERY_KEY_PEPPER`는 재배포마다 바뀌지 않도록 Render Environment Variables에 고정해 주세요. 특히 `TOKEN_ENCRYPTION_KEY`가 바뀌면 기존 암호화 토큰을 복호화할 수 없습니다.

OAuth callback은 코드에서 `DINO_PUBLIC_BASE_URL/dashboard/callback`으로 계산되므로 Discord Developer Portal의 Redirect URI와 정확히 일치해야 합니다.

## 데이터 보존

Render Web Service의 ephemeral filesystem은 데이터베이스 대용으로 사용하지 않습니다. `DATABASE_URL`은 외부 PostgreSQL/Supabase 등 영속 DB를 가리켜야 합니다.

## 기능 배치 원칙

### Discord에서 관리

`/메인설정`, `/메인설정언어`, `/메인설정시간대`, `/메인설정채널`처럼 자주 확인하거나 즉시 바꾸는 기본 설정.

### 웹 Control Center에서 관리

상점/상품, 재고, 거래, 출금, 티켓 세부설정, 인증, 로그, 백업, 복구키, 서버 관리 등 복잡한 운영 기능.

모든 설정은 PostgreSQL에 저장되므로 소스코드를 업데이트해도 설정값을 초기화하지 않습니다.
