"""
QR Login / Logout routes.
"""

import json
import asyncio
import base64

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from bilibili_api.utils.network import Credential
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents
from dotenv import set_key

from routes.deps import DATA_DIR

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/login/qr")
async def qr_login_stream():
    """SSE stream: generates QR code, polls login state, saves credential."""
    async def _gen():
        import routes.deps as deps

        login = QrCodeLogin()
        await login.generate_qrcode()

        # Get QR code as base64 PNG
        pic = login.get_qrcode_picture()
        img_bytes = pic.content
        b64 = base64.b64encode(img_bytes).decode('utf-8')
        yield f"event: qrcode\ndata: {json.dumps({'image': b64})}\n\n"

        # Poll login state
        while True:
            try:
                state = await login.check_state()
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
                break

            if state == QrCodeLoginEvents.DONE:
                cred = login.get_credential()
                # Save to .env.local
                env_path = str(DATA_DIR / '.env.local')
                set_key(env_path, 'BILIBILI_SESSION_TOKEN', cred.sessdata)
                set_key(env_path, 'BILIBILI_BILI_JCT', cred.bili_jct)
                if cred.ac_time_value:
                    set_key(env_path, 'BILIBILI_AC_TIME_VALUE', cred.ac_time_value)
                # Update global credential
                deps.credential = Credential(
                    sessdata=cred.sessdata,
                    bili_jct=cred.bili_jct,
                    ac_time_value=cred.ac_time_value or ""
                )
                yield f"event: done\ndata: {json.dumps({'message': '登录成功'})}\n\n"
                break
            elif state == QrCodeLoginEvents.TIMEOUT:
                yield f"event: timeout\ndata: {json.dumps({'message': '二维码已过期'})}\n\n"
                break
            elif state == QrCodeLoginEvents.CONF:
                yield f"event: scanned\ndata: {json.dumps({'message': '已扫码，请在手机上确认'})}\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/logout")
async def logout():
    """Clear credential and remove from .env.local."""
    import routes.deps as deps
    deps.credential = None
    env_path = DATA_DIR / '.env.local'
    if env_path.exists():
        set_key(str(env_path), 'BILIBILI_SESSION_TOKEN', '')
        set_key(str(env_path), 'BILIBILI_BILI_JCT', '')
        set_key(str(env_path), 'BILIBILI_AC_TIME_VALUE', '')
    return {"message": "已注销"}
