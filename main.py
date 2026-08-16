import os
import sqlite3
import httpx
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI()

# 환경 변수에서 값 불러오기 (하드코딩 방지)
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# 데이터베이스 초기화
def init_db():
    conn = sqlite3.connect("bot_system.db", check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            user_id TEXT PRIMARY KEY,
            access_token TEXT NOT NULL,
            refresh_token TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recovery_keys (
            recovery_key TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            server_name TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

@app.get("/")
def home():
    return {"status": "Auth Server Running"}

@app.get("/login")
def login():
    auth_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join"
    )
    return RedirectResponse(auth_url)

@app.get("/auth/callback", response_class=HTMLResponse)
async def callback(code: str):
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
            return "<h3>인증 실패: 토큰을 발급받지 못했습니다.</h3>"

        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        
        user_resp = await client.get(
            "https://discord.com/api/users/@me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        user_data = user_resp.json()
        user_id = user_data.get("id")

        if user_id:
            conn = sqlite3.connect("bot_system.db")
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO oauth_tokens (user_id, access_token, refresh_token, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET access_token=?, refresh_token=?, updated_at=?",
                (user_id, access_token, refresh_token, str(datetime.now()), access_token, refresh_token, str(datetime.now()))
            )
            conn.commit()
            conn.close()

    return """
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>인증 완료</title>
        <style>
            body { background-color: #121212; color: #ffffff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
            .card { background-color: #1a1a1a; padding: 40px 30px; border-radius: 16px; text-align: center; box-shadow: 0 8px 24px rgba(0,0,0,0.6); width: 320px; }
            .icon { width: 50px; height: 50px; background-color: #2b2b2b; color: #ffffff; border-radius: 50%; display: flex; justify-content: center; align-items: center; margin: 0 auto 20px auto; font-size: 22px; font-weight: bold; }
            h2 { margin: 0 0 10px 0; font-size: 20px; }
            p { color: #9e9e9e; font-size: 13px; line-height: 1.5; margin: 0 0 25px 0; }
            .btn { background-color: #2b2b2b; color: white; border: none; padding: 10px 24px; border-radius: 20px; font-weight: bold; cursor: pointer; font-size: 14px; }
        </style>
    </head>
    <body>
        <div class="card">
            <div class="icon">✓</div>
            <h2>인증이 완료되었습니다</h2>
            <p>계정 인증에 성공했습니다.<br>이제 창을 닫으셔도 됩니다.</p>
            <button class="btn" onclick="window.close()">창 닫기</button>
        </div>
    </body>
    </html>
    """
