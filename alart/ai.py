import httpx
from abc import ABC, abstractmethod
from fastapi import HTTPException
from .config import settings
from .db import transaction

def context_for(user_id: int):
    with transaction() as conn:
        services=conn.execute("SELECT s.name,p.name AS plan,p.price_minor,p.currency FROM services s JOIN plans p ON p.service_id=s.id WHERE s.enabled AND p.enabled ORDER BY s.id,p.price_minor LIMIT 30").fetchall()
        wallet=conn.execute("SELECT balance_minor FROM wallets WHERE user_id=%s",(user_id,)).fetchone()
        orders=conn.execute("SELECT id,status,price_minor FROM orders WHERE user_id=%s ORDER BY id DESC LIMIT 5",(user_id,)).fetchall()
        subs=conn.execute("SELECT status,expires_at FROM subscriptions WHERE user_id=%s ORDER BY id DESC LIMIT 5",(user_id,)).fetchall()
    return {"services":services,"wallet_balance_minor":wallet["balance_minor"] if wallet else 0,"orders":orders,"subscriptions":subs}

class AssistantProvider(ABC):
    @abstractmethod
    def respond(self, *, instructions: str, question: str) -> str: ...

class OpenAIAssistantProvider(AssistantProvider):
    def respond(self, *, instructions, question):
        config=settings()
        with httpx.Client(timeout=30) as client:
            response=client.post("https://api.openai.com/v1/responses",headers={"Authorization":"Bearer "+config["OPENAI_API_KEY"]},json={"model":config["OPENAI_MODEL"] or "gpt-4.1-mini","instructions":instructions,"input":question,"max_output_tokens":400,"store":False})
            response.raise_for_status()
            data=response.json()
        output="".join(part.get("text","") for item in data.get("output",[]) for part in item.get("content",[]) if part.get("type")=="output_text")
        if not output:
            raise ValueError("Empty assistant response")
        return output

def answer(user_id: int, question: str):
    config=settings()
    if not config["OPENAI_API_KEY"]:
        raise HTTPException(503,"Assistant is not configured")
    import json
    prompt="You are Alart support. Answer using only the following live application data. If information is absent, say you do not know. Do not claim to perform actions. Data: "+json.dumps(context_for(user_id),default=str)
    try:
        output=OpenAIAssistantProvider().respond(instructions=prompt,question=question)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502,"Assistant unavailable") from exc
    with transaction() as conn:
        conn.execute("INSERT INTO ai_messages(user_id,role,content) VALUES (%s,'user',%s),(%s,'assistant',%s)",(user_id,question,user_id,output))
    return output
