import os
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI()

# 환경 변수 불러오기
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

KST = timezone(timedelta(hours=9))

def get_db_connection():
    """PostgreSQL(Supabase 등) 데이터베이스 연결 함수"""
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL 환경 변수가 설정되지 않았습니다.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

@app.get("/")
def home():
    return {"status": "Auth Server Running with PostgreSQL"}

@app.get("/login")
def login(guild_id: str = None):
    # state에 guild_id를 담아 어떤 서버에서 인증을 시작했는지 추적합니다.
    auth_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join"
    )
    if guild_id:
        auth_url += f"&state={guild_id}"
    return RedirectResponse(auth_url)

@app.get("/auth/callback", response_class=HTMLResponse)
async def callback(code: str, state: str = None):
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://discord.com/api/oauth2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            }
        )
        token_data = token_resp.json()
        
        if "access_token" not in token_data:
            return """
            <!DOCTYPE html>
            <html lang="ko">
            <head><meta charset="UTF-8"><title>인증 실패</title>
            <style>body{background:#0f172a;color:#fff;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}.card{background:#1e293b;padding:40px;border-radius:20px;text-align:center;box-shadow:0 10px 30px rgba(0,0,0,0.5);border:1px solid #334155;}</style>
            </head><body><div class="card"><h2 style="color:#f87171;">❌ 인증 실패</h2><p>디스코드 토큰을 발급받지 못했습니다.<br>다시 시도해 주세요.</p></div></body></html>
            """

        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        
        # 유저 프로필 정보 가져오기
        user_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_data = user_resp.json()
        user_id = user_data.get("id")
        username = user_data.get("username")
        avatar = user_data.get("avatar")
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"

        if user_id and DATABASE_URL:
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        # 1. 봇의 user_tokens 테이블 구조(guild_id, user_id 복합키)에 맞춰 토큰 저장/갱신
                        if state:
                            cur.execute(
                                """INSERT INTO user_tokens (guild_id, user_id, access_token, refresh_token) 
                                   VALUES (%s, %s, %s, %s) 
                                   ON CONFLICT (guild_id, user_id) 
                                   DO UPDATE SET access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token""",
                                (int(state), int(user_id), access_token, refresh_token)
                            )
                        else:
                            # state가 없다면 등록된 모든 서버에 토큰 동기화
                            cur.execute("SELECT guild_id FROM guild_settings")
                            all_guilds = cur.fetchall()
                            for g in all_guilds:
                                cur.execute(
                                    """INSERT INTO user_tokens (guild_id, user_id, access_token, refresh_token) 
                                       VALUES (%s, %s, %s, %s) 
                                       ON CONFLICT (guild_id, user_id) 
                                       DO UPDATE SET access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token""",
                                    (g["guild_id"], int(user_id), access_token, refresh_token)
                                )
                        conn.commit()

                # 2. 인증 로그 전송 대상 조회
                targets = []
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        if state:
                            cur.execute("SELECT verify_log_channel_id FROM guild_settings WHERE guild_id = %s", (int(state),))
                            targets = cur.fetchall()
                        else:
                            cur.execute("SELECT verify_log_channel_id FROM guild_settings WHERE verify_log_channel_id IS NOT NULL")
                            targets = cur.fetchall()

                # 3. 디스코드 봇 토큰을 이용해 각 서버의 인증 로그 채널로 전송
                if BOT_TOKEN:
                    for row in targets:
                        ch_id = row.get("verify_log_channel_id")
                        if ch_id:
                            embed_payload = {
                                "embeds": [{
                                    "title": "🔓 웹 연동 인증 완료",
                                    "description": f"<@{user_id}> (`{username}`) 님이 웹 연동 인증을 성공적으로 완료하셨습니다.",
                                    "color": 5763719,  # 초록색
                                    "thumbnail": {"url": avatar_url},
                                    "fields": [{"name": "인증된 사용자 ID", "value": str(user_id), "inline": False}],
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                }]
                            }
                            async with httpx.AsyncClient() as log_client:
                                await log_client.post(
                                    f"https://discord.com/api/v10/channels/{ch_id}/messages",
                                    headers={"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"},
                                    json=embed_payload
                                )
            except Exception as e:
                print(f"❌ DB 연동 또는 로그 전송 오류: {e}")

    # 고급스러운 글래스모피즘 UI 반환
    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>디스코드 통합 인증 완료</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
                color: #f8fafc;
                font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }}
            .card {{
                background: rgba(30, 41, 59, 0.7);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                padding: 45px 35px;
                border-radius: 24px;
                text-align: center;
                box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
                width: 360px;
                animation: fadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1);
            }}
            @keyframes fadeIn {{
                from {{ opacity: 0; transform: translateY(20px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            .profile-img {{
                width: 72px;
                height: 72px;
                border-radius: 50%;
                border: 3px solid #38bdf8;
                margin: 0 auto 16px auto;
                object-fit: cover;
                box-shadow: 0 0 20px rgba(56, 189, 248, 0.4);
            }}
            .icon-badge {{
                width: 32px;
                height: 32px;
                background-color: #22c55e;
                color: #ffffff;
                border-radius: 50%;
                display: flex;
                justify-content: center;
                align-items: center;
                margin: -30px auto 15px auto;
                font-size: 14px;
                font-weight: bold;
                border: 3px solid #1e293b;
                position: relative;
                z-index: 2;
            }}
            h2 {{
                margin: 0 0 8px 0;
                font-size: 22px;
                font-weight: 700;
                letter-spacing: -0.5px;
            }}
            .username-highlight {{
                color: #38bdf8;
            }}
            p {{
                color: #94a3b8;
                font-size: 14px;
                line-height: 1.6;
                margin: 0 0 30px 0;
            }}
            .btn {{
                background: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
                color: white;
                border: none;
                width: 100%;
                padding: 12px 0;
                border-radius: 12px;
                font-weight: 600;
                cursor: pointer;
                font-size: 15px;
                box-shadow: 0 4px 12px rgba(56, 189, 248, 0.3);
                transition: all 0.2s ease;
            }}
            .btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 16px rgba(56, 189, 248, 0.4);
            }}
            .btn:active {{
                transform: translateY(0);
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <img src="{avatar_url}" alt="프로필" class="profile-img">
            <div class="icon-badge">✓</div>
            <h2>인증이 완료되었습니다</h2>
            <p><span class="username-highlight">{username}</span> 님의 계정 연동 및 인증이<br>성공적으로 처리되었습니다. 이제 창을 닫으셔도 됩니다.</p>
            <button class="btn" onclick="window.close()">창 닫기</button>
        </div>
    </body>
    </html>
    """
