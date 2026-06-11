"""
LangChain RAG 챗봇 라우터
WebSocket /ws/chat → 실시간 스트리밍 응답
POST /api/chat → 일반 응답
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from database import get_db, ChatHistory
from services.rag import get_rag_service

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    session_id: Optional[str] = "default"


@router.post("/api/chat")
async def chat_endpoint(req: ChatRequest, db: Session = Depends(get_db)):
    """일반 HTTP 챗봇 응답"""
    svc = get_rag_service()
    if svc is None:
        raise HTTPException(status_code=503, detail="챗봇 서비스가 초기화되지 않았습니다.")

    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    try:
        reply = svc.chat(messages)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"챗봇 오류: {str(e)}")

    # 대화 이력 저장
    try:
        last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if last_user:
            db.add(ChatHistory(
                session_id=req.session_id,
                role="user",
                content=last_user["content"],
            ))
        db.add(ChatHistory(
            session_id=req.session_id,
            role="assistant",
            content=reply,
        ))
        db.commit()
    except Exception:
        pass

    return {"reply": reply}


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """WebSocket 실시간 스트리밍 챗봇"""
    await websocket.accept()
    svc = get_rag_service()

    try:
        while True:
            # 클라이언트로부터 메시지 수신
            raw = await websocket.receive_text()
            data = json.loads(raw)
            messages = data.get("messages", [])

            if not messages:
                await websocket.send_text(json.dumps({"type": "error", "content": "메시지가 없습니다."}))
                continue

            if svc is None:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": "챗봇 서비스가 초기화 중입니다. 잠시 후 다시 시도해주세요."
                }))
                continue

            # 스트리밍 시작 신호
            await websocket.send_text(json.dumps({"type": "start"}))

            # 스트리밍 응답
            full_reply = ""
            try:
                async for chunk in svc.chat_stream(messages):
                    full_reply += chunk
                    await websocket.send_text(json.dumps({
                        "type": "chunk",
                        "content": chunk
                    }))
            except Exception as e:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": f"응답 생성 중 오류가 발생했습니다: {str(e)}"
                }))

            # 스트리밍 완료 신호
            await websocket.send_text(json.dumps({"type": "end", "full": full_reply}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(json.dumps({"type": "error", "content": str(e)}))
        except Exception:
            pass
