import hmac
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.auth import service as auth_service
from app.auth.dependencies import (
    SESSION_COOKIE,
    CurrentUserDep,
    origin_matches,
    require_csrf,
    require_ready,
    require_role,
)
from app.auth.schemas import AccountForm
from app.core.config import settings
from app.core.database import DbSession
from app.core.dependencies import client_ip
from app.core.templating import render, templates

router = APIRouter(tags=["auth"])

LOGIN_CSRF_COOKIE = "login_csrf"


def _login_page(request: Request, error: str | None = None, status_code: int = 200):
    # Belum ada session → CSRF login memakai double-submit cookie (tanpa tulis DB)
    token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request, name="auth/login.html",
        context={"login_csrf": token, "error": error}, status_code=status_code,
    )
    response.set_cookie(
        LOGIN_CSRF_COOKIE, token, max_age=3600, path="/login",
        httponly=True, secure=settings.COOKIE_SECURE, samesite="strict",
    )
    return response


def _session_response(raw_sid: str, location: str):
    response = RedirectResponse(location, status_code=303)
    response.set_cookie(
        SESSION_COOKIE, raw_sid, max_age=settings.SESSION_MAX_AGE,   # batas mutlak
        httponly=True, secure=settings.COOKIE_SECURE, samesite="strict",
    )
    return response


@router.get("/login")
async def login_page(request: Request):
    return _login_page(request)


@router.post("/login")
async def login_submit(request: Request, db: DbSession):
    form = await request.form()
    cookie_token = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    form_token = form.get("csrf_token")
    if not cookie_token or not isinstance(form_token, str) or not hmac.compare_digest(cookie_token, form_token):
        raise HTTPException(status_code=403, detail="Token CSRF tidak valid.")
    if request.headers.get("origin") and not origin_matches(request):
        raise HTTPException(status_code=403, detail="Asal permintaan tidak dikenal.")

    username = str(form.get("username", ""))
    if len(username) > 100:
        return _login_page(request, "Username atau password salah.", status_code=400)
    try:
        raw_sid = await auth_service.login(db, username, str(form.get("password", "")), client_ip(request))
    except auth_service.TooManyAttempts:
        return _login_page(request, "Terlalu banyak percobaan. Coba lagi nanti.", status_code=429)
    if raw_sid is None:
        return _login_page(request, "Username atau password salah.", status_code=400)

    response = _session_response(raw_sid, settings.HOME_PATH)
    response.delete_cookie(LOGIN_CSRF_COOKIE, path="/login")
    return response


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(user: CurrentUserDep, db: DbSession):
    await auth_service.logout(db, user.sid)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------- ganti password sendiri ----------
@router.get("/account/password", dependencies=[Depends(require_csrf)])
async def password_page(request: Request, user: CurrentUserDep):
    return templates.TemplateResponse(request=request, name="auth/password.html", context={"user": user, "error": None})


@router.post("/account/password", dependencies=[Depends(require_csrf)])
async def password_submit(request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    new_password = str(form.get("new_password", ""))
    error = None
    if new_password != str(form.get("repeat_password", "")):
        error = "Password baru tidak sama."
    else:
        async with db.begin():
            row = await db.get(auth_service.User, user.id)
            current_hash = row.password_hash
        if not await auth_service.verify_password(current_hash, str(form.get("current_password", ""))):
            error = "Password sekarang salah."
    if error is None:
        try:
            await auth_service.change_password(db, user.id, new_password, actor=user.username, ip=client_ip(request))
        except auth_service.InvalidPassword as err:
            error = str(err)
    if error:
        return templates.TemplateResponse(
            request=request, name="auth/password.html", context={"user": user, "error": error}, status_code=422,
        )
    # Mengganti password mengakhiri semua sesi, termasuk sesi ini: login lagi dengan password baru.
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------- akun admin ----------
account_deps = [Depends(require_csrf), Depends(require_ready), Depends(require_role("admin"))]


async def _accounts_context(db: DbSession, user, **extra) -> dict:
    return {
        "user": user, "users": await auth_service.list_users(db), "log": await auth_service.recent_log(db),
        "error": None, "once": None, **extra,
    }


@router.get("/account/users", dependencies=account_deps)
async def accounts_page(request: Request, user: CurrentUserDep, db: DbSession):
    return render(request, "auth/users.html", "auth/_users.html", await _accounts_context(db, user))


@router.post("/account/users", dependencies=account_deps)
async def add_account(request: Request, user: CurrentUserDep, db: DbSession):
    form = await request.form()
    try:
        username = AccountForm.model_validate({"username": form.get("username", "")}).username
    except ValidationError:
        return await _accounts_fragment(request, db, user, error="Isi username, maksimal 100 karakter.", status_code=422)
    password = auth_service.generate_password()
    try:
        await auth_service.create_user(
            db, username, password, must_change_password=True, actor=user.username, ip=client_ip(request),
        )
    except auth_service.InvalidUsername as err:
        return await _accounts_fragment(request, db, user, error=str(err), status_code=422)
    except IntegrityError:
        return await _accounts_fragment(request, db, user, error=f"Username {username} sudah dipakai.", status_code=422)
    once = {"username": username, "password": password, "action": "Akun dibuat"}
    return await _accounts_fragment(request, db, user, once=once)


async def _accounts_fragment(request, db, user, *, status_code=200, **extra):
    context = await _accounts_context(db, user, **extra)
    headers = {"HX-Retarget": "#accounts", "HX-Reswap": "outerHTML"}
    return templates.TemplateResponse(
        request=request, name="auth/_users.html", context=context, status_code=status_code, headers=headers,
    )


@router.post("/account/users/{username}/disable", dependencies=account_deps)
async def disable_account(username: str, request: Request, user: CurrentUserDep, db: DbSession):
    try:
        found = await auth_service.set_active(db, username, False, actor=user.username, ip=client_ip(request))
    except ValueError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    if not found:
        raise HTTPException(status_code=404)
    return await _accounts_fragment(request, db, user)


@router.post("/account/users/{username}/enable", dependencies=account_deps)
async def enable_account(username: str, request: Request, user: CurrentUserDep, db: DbSession):
    if not await auth_service.set_active(db, username, True, actor=user.username, ip=client_ip(request)):
        raise HTTPException(status_code=404)
    return await _accounts_fragment(request, db, user)


@router.post("/account/users/{username}/reset-password", dependencies=account_deps)
async def reset_account_password(username: str, request: Request, user: CurrentUserDep, db: DbSession) -> Response:
    password = await auth_service.reset_password(db, username, actor=user.username, ip=client_ip(request))
    if password is None:
        raise HTTPException(status_code=404)
    once = {"username": username, "password": password, "action": "Password direset"}
    return await _accounts_fragment(request, db, user, once=once)
