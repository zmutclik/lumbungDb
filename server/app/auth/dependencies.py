import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select, update

from app.auth.models import User, UserSession
from app.auth.service import hash_token
from app.core.config import settings
from app.core.database import async_session_factory, db_now_plus, db_time_plus

SESSION_COOKIE = "sid"
TOUCH_INTERVAL = 300   # perpanjang session / last_used maks 1x per 5 menit, bukan UPDATE tiap request


@dataclass(frozen=True)
class CurrentUser:
    id: int
    role: str
    sid: str           # hash session ID
    username: str = ""
    must_change_password: bool = False


def csrf_token_for(sid: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), sid.encode(), "sha256").hexdigest()


async def get_current_user(request: Request) -> CurrentUser:
    raw_sid = request.cookies.get(SESSION_COOKIE)
    if not raw_sid:
        raise HTTPException(status_code=401)
    sid = hash_token(raw_sid)

    # Session DB milik dependency ini sendiri, BUKAN session request (Depends(get_db)):
    # query di session request memicu autobegin sehingga db.begin() di service gagal.
    async with async_session_factory() as db, db.begin():
        row = (await db.execute(
            select(
                UserSession.user_id,
                UserSession.role,
                User.username,
                User.must_change_password,
                (UserSession.last_seen_at < db_now_plus(-TOUCH_INTERVAL)).label("stale"),
            ).join(User, User.id == UserSession.user_id)
            .where(UserSession.id == sid, UserSession.expires_at > func.now(), User.is_active.is_(True))
        )).one_or_none()
        if row is None:
            raise HTTPException(status_code=401)
        if row.stale:
            # Idle timeout diperpanjang, tapi tidak melewati batas mutlak sejak login. Cookie tidak perlu
            # diperbarui: max_age-nya sudah SESSION_MAX_AGE, server yang menentukan kapan session berakhir.
            await db.execute(
                update(UserSession).where(UserSession.id == sid)
                .values(last_seen_at=func.now(), expires_at=func.least(
                    db_now_plus(settings.SESSION_TTL),
                    db_time_plus(UserSession.created_at, settings.SESSION_MAX_AGE),
                ))
            )

    request.state.csrf_token = csrf_token_for(sid)
    request.state.username = row.username
    return CurrentUser(
        id=row.user_id, role=row.role, sid=sid, username=row.username, must_change_password=row.must_change_password,
    )


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_role(*roles: str):
    async def checker(user: CurrentUserDep) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=403)
        return user
    return checker


def origin_matches(request: Request) -> bool:
    """Origin dibandingkan dengan alamat yang dilihat browser: skema dari uvicorn (--forwarded-allow-ips hanya
    mempercayai proxy) dan Host yang diteruskan proxy apa adanya."""
    return request.headers.get("origin") == f"{request.url.scheme}://{request.headers.get('host', '')}"


async def require_ready(user: CurrentUserDep) -> None:
    """Akun dengan password awal hanya boleh membuka halaman ganti password."""
    if user.must_change_password:
        raise HTTPException(status_code=303, headers={"Location": "/account/password"})


async def require_csrf(request: Request, user: CurrentUserDep) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if not origin_matches(request):
        raise HTTPException(status_code=403, detail="Asal permintaan tidak dikenal.")
    sent = request.headers.get("X-CSRF-Token")
    if sent is None:
        sent = (await request.form()).get("csrf_token")      # form non-HTMX
    if not isinstance(sent, str) or not hmac.compare_digest(sent, csrf_token_for(user.sid)):
        raise HTTPException(status_code=403, detail="Token CSRF tidak valid.")

