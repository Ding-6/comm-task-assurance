"""场景引擎路由（PyMySQL 原生版本）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.common.ws import get_hub
from app.core.scene_clock import get_clock
from app.schemas.comm import ApiResponse
from app.schemas.scene import SceneDecideIn, SceneStartIn
from app.services.approval import decide_approval
from app.services.engine import (
    get_state, handle_input as engine_handle_input,
    pause_scene, reset_scene, resume_scene, start_scene,
)

router = APIRouter(prefix="/scene", tags=["scene"])


@router.get("/state", response_model=ApiResponse)
def state() -> ApiResponse:
    return ApiResponse(data=get_state(get_clock().now_min()))


@router.post("/start", response_model=ApiResponse)
def start(payload: SceneStartIn) -> ApiResponse:
    return ApiResponse(data=start_scene())


@router.post("/pause", response_model=ApiResponse)
def pause() -> ApiResponse:
    return ApiResponse(data=pause_scene())


@router.post("/resume", response_model=ApiResponse)
def resume() -> ApiResponse:
    return ApiResponse(data=resume_scene())


@router.post("/reset", response_model=ApiResponse)
def reset() -> ApiResponse:
    return ApiResponse(data=reset_scene())


@router.post("/decide", response_model=ApiResponse)
def decide(payload: SceneDecideIn) -> ApiResponse:
    target = payload.plan_code or payload.approval_code
    if not target:
        return ApiResponse(code=400, message="plan_code / approval_code 必填")
    # 兼容旧 approval_code 路径
    if target.startswith("APV-"):
        target = target[len("APV-"):]
    ap = decide_approval(
        plan_code=target,
        decision=payload.decision,
        approver=payload.approver,
        decision_source=payload.decision_source,
        reason=payload.reason,
    )
    return ApiResponse(data={"approval_code": ap.approval_code, "decision": ap.decision})


@router.post("/input")
def input_route(payload: dict[str, Any]) -> ApiResponse:
    """引擎层 input：category + payload。"""
    category = payload.get("category", "")
    body = payload.get("payload", "")
    try:
        result = engine_handle_input(category, body)
        return ApiResponse(data=result)
    except ValueError as exc:
        return ApiResponse(code=400, message=str(exc))


@router.websocket("/ws")
async def scene_ws(ws: WebSocket) -> None:
    hub = get_hub()
    await hub.connect(ws)
    snap = get_state(get_clock().now_min())
    try:
        await ws.send_json({"type": "state", "data": snap})
    except Exception:
        pass
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
