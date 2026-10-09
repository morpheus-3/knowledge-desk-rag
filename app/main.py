import json
import os
import threading
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from starlette.concurrency import run_in_threadpool
from . import store, retrieval, generation

ROOT = Path(__file__).resolve().parent.parent
CHAT_LOCK = threading.Lock()

@asynccontextmanager
async def lifespan(app):
    store.initialize()
    yield

app = FastAPI(title="Knowledge Desk · PDF RAG", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

@app.get("/")
def home():
    return FileResponse(ROOT / "static/index.html")

@app.get("/api/status")
def status():
    with store.db() as connection:
        count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        pages = connection.execute("SELECT COALESCE(SUM(pages),0) FROM documents").fetchone()[0]
    return {"provider": "Groq", "configured": bool(os.environ.get("GROQ_API_KEY", "").strip()), "model": os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b"), "documents": count, "pages": pages, "limit": 20}

class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=20, max_length=300)

@app.put("/api/settings")
def settings(body: Settings):
    key = body.api_key.strip()
    if not key.startswith("gsk_") or any(c.isspace() for c in key):
        raise HTTPException(422, "Enter a valid Groq key starting with gsk_.")
    os.environ["GROQ_API_KEY"] = key
    return {"configured": True, "persisted": False}

@app.get("/api/documents")
def documents():
    with store.db() as connection:
        rows = [dict(row) for row in connection.execute("SELECT d.id,d.name,d.pages,d.bytes,d.created_at,COUNT(c.id) AS chunks,COALESCE(SUM(length(c.text)),0) AS text_characters,COUNT(DISTINCT c.page) AS searchable_pages FROM documents d LEFT JOIN chunks c ON d.id=c.document_id GROUP BY d.id ORDER BY d.created_at DESC")]
    for row in rows:
        row["limited_text"] = row["text_characters"] / row["pages"] < 150 or row["searchable_pages"] < row["pages"]
    return rows

@app.post("/api/documents", status_code=201)
async def upload(files: list[UploadFile] = File(...)):
    if not 1 <= len(files) <= 20:
        raise HTTPException(422, "Choose between 1 and 20 PDFs.")
    batch, total = [], 0
    try:
        for file in files:
            content = await file.read(retrieval.MAX_FILE_BYTES+1)
            if len(content)>retrieval.MAX_FILE_BYTES:
                raise HTTPException(413, f"{file.filename}: maximum file size is 20 MB.")
            total += len(content)
            if total > retrieval.MAX_BATCH_BYTES:
                raise HTTPException(413, "Combined upload size must be at most 100 MB.")
            batch.append((file.filename or "document.pdf",content))
        return await run_in_threadpool(retrieval.ingest,batch)
    finally:
        for file in files:
            await file.close()

@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    with retrieval.INDEX_LOCK, store.db() as connection:
        result = connection.execute("DELETE FROM documents WHERE id=?",(document_id,))
        if not result.rowcount:
            raise HTTPException(404, "Document not found.")
        retrieval.invalidate()
    return {"deleted": True}

@app.get("/api/documents/{document_id}/pdf")
def pdf(document_id: str):
    with store.db() as connection:
        row = connection.execute("SELECT pdf FROM documents WHERE id=?",(document_id,)).fetchone()
    if not row:
        raise HTTPException(404,"Document was removed from the library.")
    return Response(bytes(row[0]),media_type="application/pdf",headers={"Content-Disposition":"inline; filename=document.pdf","X-Content-Type-Options":"nosniff"})

@app.get("/api/chats")
def chats():
    with store.db() as connection:
        return [dict(row) for row in connection.execute("SELECT c.*,COUNT(m.id) AS message_count FROM chats c LEFT JOIN messages m ON c.id=m.chat_id GROUP BY c.id ORDER BY c.updated_at DESC")]

@app.post("/api/chats",status_code=201)
def create_chat():
    chat_id = str(uuid.uuid4())
    with store.db() as connection:
        connection.execute("INSERT INTO chats(id,title) VALUES(?,?)",(chat_id,"New conversation"))
    return {"id":chat_id,"title":"New conversation","messages":[]}

@app.get("/api/chats/{chat_id}")
def chat(chat_id: str):
    with store.db() as connection:
        row = connection.execute("SELECT * FROM chats WHERE id=?",(chat_id,)).fetchone()
        if not row:
            raise HTTPException(404,"Conversation not found.")
        messages = [dict(m) for m in connection.execute("SELECT * FROM messages WHERE chat_id=? ORDER BY id",(chat_id,))]
    for message in messages:
        message["sources"] = json.loads(message["sources"])
    return {**dict(row),"messages":messages}

@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str):
    with store.db() as connection:
        result = connection.execute("DELETE FROM chats WHERE id=?",(chat_id,))
        if not result.rowcount:
            raise HTTPException(404,"Conversation not found.")
    return {"deleted":True}

class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1,max_length=3000)
    document_ids: list[str] = Field(default_factory=list,max_length=20)

@app.post("/api/chats/{chat_id}/messages")
def answer(chat_id: str,body: Question):
    question = body.question.strip()
    if not question:
        raise HTTPException(422,"Ask a nonempty question.")
    with CHAT_LOCK:
        conversation = chat(chat_id)
        with store.db() as connection:
            known = {row[0] for row in connection.execute("SELECT id FROM documents")}
        greeting = bool(re.fullmatch(r"(?:hi|hello|hey|good morning|good afternoon|good evening|thanks|thank you)[!.\s]*",question,re.I))
        if not known and not greeting:
            raise HTTPException(409,"Upload at least one PDF to start asking questions.")
        if not set(body.document_ids).issubset(known):
            raise HTTPException(422,"A selected document was removed. Refresh your library.")
        history = [{"role":m["role"],"content":m["content"][:2500]} for m in conversation["messages"][-6:]]
        query = question
        if len(question.split()) < 9 and any(word in question.lower().split() for word in ["it","its","they","their","that","those","this"]):
            previous = next((m["content"] for m in reversed(history) if m["role"]=="user"),"")
            query += " " + previous[:500]
        sources = [] if greeting else retrieval.retrieve(query,body.document_ids)
        if greeting:
            content = "Hi! I can help you explore your PDFs. Ask a question about their content, or choose a document and ask for its key points." if known else "Hi! Upload your PDFs, then ask me a question about them. I'll show the passages and page citations behind my answers."
        elif sources:
            content,sources = generation.generate(question,sources,history)
        else:
            content = "I couldn't find that information in the selected PDFs. Try a more specific question or upload a document that covers it."
        with store.db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute("SELECT 1 FROM chats WHERE id=?",(chat_id,)).fetchone():
                raise HTTPException(404,"This conversation was deleted while the answer was being generated.")
            connection.execute("INSERT INTO messages(chat_id,role,content) VALUES(?,?,?)",(chat_id,"user",question))
            cursor = connection.execute("INSERT INTO messages(chat_id,role,content,sources) VALUES(?,?,?,?)",(chat_id,"assistant",content,json.dumps(sources)))
            if not conversation["messages"]:
                connection.execute("UPDATE chats SET title=? WHERE id=?",(question[:65],chat_id))
            connection.execute("UPDATE chats SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",(chat_id,))
        return {"id":cursor.lastrowid,"role":"assistant","content":content,"sources":sources}
