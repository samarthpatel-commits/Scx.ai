import os
import json
import sqlite3
import asyncio
import uuid
import threading
import requests
import io
from datetime import datetime
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request, Header, UploadFile, File
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import AsyncOpenAI
from dotenv import load_dotenv

import pypdf
import docx

load_dotenv()

DB_FILE = "chat_threads.db"
DEFAULT_BASE_URL = os.getenv("SCX_BASE_URL", "https://api.scx.ai/v1")
DEFAULT_API_KEY = os.getenv("SCX_API_KEY", "")
DEFAULT_MODEL = os.getenv("SCX_DEFAULT_MODEL", "Meta-Llama-3.3-70B-Instruct")

GROUNDED_ENDPOINT = os.getenv("GROUNDED_ENDPOINT", "https://grounded-topaz.vercel.app/api/v1/monitor")
GROUNDED_API_KEY = os.getenv("GROUNDED_API_KEY", "grnd_c69448bc68f344fba557d1c465cf5a20b5daa9ac0737d87f")
GROUNDED_AGENT_ID = os.getenv("GROUNDED_AGENT_ID", "7646d28c-446b-491c-8f32-7d1c134d78a9")

app = FastAPI(title="SCX.AI Threaded ChatBot with Document RAG & Grounded AI Verification")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Text Extraction Helper
def extract_text_from_file(filename: str, file_bytes: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()
    text = ""
    
    try:
        if ext == ".pdf":
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for i, page in enumerate(reader.pages):
                p_text = page.extract_text()
                if p_text and p_text.strip():
                    pages_text.append(f"[Page {i+1}]\n{p_text.strip()}")
            text = "\n\n".join(pages_text)
            
        elif ext == ".docx":
            doc = docx.Document(io.BytesIO(file_bytes))
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            text = "\n".join(paragraphs)
            
        else: # .txt, .md, .csv, .json, .py, .js, .html, etc.
            try:
                text = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = file_bytes.decode("latin-1", errors="ignore")
    except Exception as e:
        print(f"Error extracting text from {filename}: {e}")
        text = f"[Could not parse binary content of {filename}]"
        
    return text.strip()

# Folder Document Knowledge Base directory
DOCUMENTS_DIR = os.getenv("DOCUMENTS_DIR", os.path.join(os.path.dirname(__file__), "documents"))
os.makedirs(DOCUMENTS_DIR, exist_ok=True)

def extract_text_from_path(filepath: str) -> str:
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
        return extract_text_from_file(os.path.basename(filepath), file_bytes)
    except Exception as e:
        print(f"Error reading file {filepath}: {e}")
        return ""

def load_folder_documents() -> List[Dict[str, Any]]:
    docs = []
    if not os.path.exists(DOCUMENTS_DIR):
        return docs
        
    for root, dirs, files in os.walk(DOCUMENTS_DIR):
        for filename in files:
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, DOCUMENTS_DIR)
            try:
                text = extract_text_from_path(filepath)
                if text and text.strip():
                    docs.append({
                        "filename": rel_path,
                        "content": text.strip(),
                        "size": os.path.getsize(filepath),
                        "ext": os.path.splitext(filename)[1].lower().replace(".", "")
                    })
            except Exception as e:
                print(f"Error loading document from folder {filepath}: {e}")
    return docs

def get_folder_documents_context() -> str:
    docs = load_folder_documents()
    if not docs:
        return ""
        
    doc_parts = []
    for d in docs:
        content_snippet = d["content"][:6000]
        doc_parts.append(f"--- FOLDER DOCUMENT: {d['filename']} ---\n{content_snippet}")
        
    return "\n\n".join(doc_parts)


# Grounded AI Evaluation & Verification Call
def monitor_grounded(question: str, response: str, agent_id: Optional[str] = None, api_key: Optional[str] = None) -> Dict[str, Any]:
    target_agent_id = agent_id or GROUNDED_AGENT_ID
    target_api_key = api_key or GROUNDED_API_KEY
    
    clean_resp = response.strip()
    if len(clean_resp) > 2500:
        clean_resp = clean_resp[:2500] + "..."

    try:
        res = requests.post(
            GROUNDED_ENDPOINT,
            headers={"Authorization": f"Bearer {target_api_key}"},
            json={
                "agentId": target_agent_id,
                "question": question,
                "aiResponse": clean_resp
            },
            timeout=45
        )
        if res.status_code == 200:
            data = res.json()
            data["success"] = True
            print(f"[Grounded AI Success] Score: {data.get('score')} Risk: {data.get('risk')}")
            return data
        else:
            print(f"[Grounded AI Error] HTTP {res.status_code}: {res.text}")
            return {
                "success": False,
                "error": f"API Error HTTP {res.status_code}",
                "detail": res.text
            }
    except Exception as e:
        print(f"[Grounded AI Exception] {e}")
        return {
            "success": False,
            "error": "Connection Timeout / Network Failure",
            "detail": str(e)
        }

# Database Initialization
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        system_prompt TEXT,
        model TEXT,
        pinned INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        grounded_result TEXT,
        FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        file_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        content TEXT NOT NULL,
        uploaded_at TEXT NOT NULL,
        FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE
    )
    """)
    
    cursor.execute("PRAGMA table_info(messages)")
    columns = [col[1] for col in cursor.fetchall()]
    if "grounded_result" not in columns:
        cursor.execute("ALTER TABLE messages ADD COLUMN grounded_result TEXT")
        
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# Request Models
class MessageItem(BaseModel):
    id: Optional[str] = None
    role: str
    content: str
    timestamp: Optional[str] = None
    grounded_result: Optional[Dict[str, Any]] = None

class ThreadCreate(BaseModel):
    title: Optional[str] = "New Conversation"
    model: Optional[str] = DEFAULT_MODEL

class ThreadUpdate(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None
    pinned: Optional[bool] = None

class ChatRequest(BaseModel):
    thread_id: str
    message: str
    api_key: Optional[str] = None
    base_url: Optional[str] = DEFAULT_BASE_URL
    model: Optional[str] = DEFAULT_MODEL
    temperature: Optional[float] = 0.7
    grounded_agent_id: Optional[str] = None
    grounded_api_key: Optional[str] = None

class GroundedScoreRequest(BaseModel):
    question: str
    ai_response: str
    agent_id: Optional[str] = None
    api_key: Optional[str] = None

# Routes
# Health Check API Endpoint (Zero SCX tokens used)
@app.get("/health")
@app.get("/api/health")
def health_check():
    return {
        "status": "online",
        "timestamp": datetime.utcnow().isoformat(),
        "scx_configured": bool(DEFAULT_API_KEY),
        "grounded_configured": bool(GROUNDED_API_KEY)
    }

@app.get("/api/config")
def get_config():
    return {
        "default_base_url": DEFAULT_BASE_URL,
        "has_default_api_key": bool(DEFAULT_API_KEY),
        "default_model": DEFAULT_MODEL,
        "grounded_agent_id": GROUNDED_AGENT_ID,
        "available_models": [
            "Meta-Llama-3.3-70B-Instruct",
            "Meta-Llama-3.1-405B-Instruct",
            "Meta-Llama-3.1-8B-Instruct",
            "Qwen/Qwen2.5-72B-Instruct",
            "DeepSeek-R1",
            "Mistral-Large-2411"
        ]
    }

# Document Management APIs
@app.post("/api/threads/{thread_id}/documents")
async def upload_document(thread_id: str, file: UploadFile = File(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM threads WHERE id = ?", (thread_id,))
    if not cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail="Thread not found")
        
    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024: # 20MB limit
        conn.close()
        raise HTTPException(status_code=400, detail="File size exceeds 20MB limit.")
        
    extracted_text = extract_text_from_file(file.filename, file_bytes)
    if not extracted_text:
        conn.close()
        raise HTTPException(status_code=400, detail="Could not extract text from uploaded document.")
        
    doc_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    file_type = os.path.splitext(file.filename)[1].lower().replace(".", "") or "txt"
    
    cursor.execute(
        "INSERT INTO documents (id, thread_id, filename, file_type, file_size, content, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, thread_id, file.filename, file_type, len(file_bytes), extracted_text, now)
    )
    conn.commit()
    conn.close()
    
    return {
        "id": doc_id,
        "thread_id": thread_id,
        "filename": file.filename,
        "file_type": file_type,
        "file_size": len(file_bytes),
        "text_length": len(extracted_text),
        "preview": extracted_text[:200] + "...",
        "uploaded_at": now
    }

@app.get("/api/threads/{thread_id}/documents")
def list_documents(thread_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, thread_id, filename, file_type, file_size, LENGTH(content) as text_length, uploaded_at FROM documents WHERE thread_id = ? ORDER BY uploaded_at DESC", (thread_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

@app.post("/api/grounded/score")
def score_with_grounded(data: GroundedScoreRequest):
    result = monitor_grounded(
        question=data.question,
        response=data.ai_response,
        agent_id=data.agent_id,
        api_key=data.api_key
    )
    return {"status": "submitted", "result": result}

@app.get("/api/threads")
def list_threads():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.id, t.title, t.system_prompt, t.model, t.pinned, t.created_at, t.updated_at,
               (SELECT content FROM messages WHERE thread_id = t.id ORDER BY timestamp DESC LIMIT 1) as last_message,
               (SELECT COUNT(*) FROM messages WHERE thread_id = t.id) as message_count,
               (SELECT COUNT(*) FROM documents WHERE thread_id = t.id) as document_count
        FROM threads t
        ORDER BY t.pinned DESC, t.updated_at DESC
    """)
    rows = cursor.fetchall()
    threads = [dict(row) for row in rows]
    conn.close()
    return threads

@app.post("/api/threads")
def create_thread(data: ThreadCreate):
    thread_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO threads (id, title, system_prompt, model, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (thread_id, data.title, None, data.model, 0, now, now)
    )
    conn.commit()
    conn.close()
    return {"id": thread_id, "title": data.title, "model": data.model, "created_at": now, "updated_at": now, "pinned": 0}

@app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM threads WHERE id = ?", (thread_id,))
    thread = cursor.fetchone()
    if not thread:
        conn.close()
        raise HTTPException(status_code=404, detail="Thread not found")
    
    cursor.execute("SELECT id, role, content, timestamp, grounded_result FROM messages WHERE thread_id = ? ORDER BY timestamp ASC", (thread_id,))
    rows = cursor.fetchall()
    
    cursor.execute("SELECT id, filename, file_type, file_size, LENGTH(content) as text_length, uploaded_at FROM documents WHERE thread_id = ?", (thread_id,))
    doc_rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for r in rows:
        item = dict(r)
        if item.get("grounded_result"):
            try:
                item["grounded_result"] = json.loads(item["grounded_result"])
            except Exception:
                pass
        messages.append(item)
    
    res = dict(thread)
    res["messages"] = messages
    res["documents"] = [dict(d) for d in doc_rows]
    return res

@app.put("/api/threads/{thread_id}")
def update_thread(thread_id: str, data: ThreadUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM threads WHERE id = ?", (thread_id,))
    thread = cursor.fetchone()
    if not thread:
        conn.close()
        raise HTTPException(status_code=404, detail="Thread not found")
    
    now = datetime.utcnow().isoformat()
    updates = []
    params = []
    
    if data.title is not None:
        updates.append("title = ?")
        params.append(data.title)
    if data.model is not None:
        updates.append("model = ?")
        params.append(data.model)
    if data.pinned is not None:
        updates.append("pinned = ?")
        params.append(1 if data.pinned else 0)
        
    updates.append("updated_at = ?")
    params.append(now)
    params.append(thread_id)
    
    cursor.execute(f"UPDATE threads SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/threads/{thread_id}")
def delete_thread(thread_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents WHERE thread_id = ?", (thread_id,))
    cursor.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
    cursor.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
    conn.commit()
    conn.close()
    return {"status": "deleted"}

@app.delete("/api/threads")
def clear_all_threads():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents")
    cursor.execute("DELETE FROM messages")
    cursor.execute("DELETE FROM threads")
    conn.commit()
    conn.close()
    return {"status": "cleared"}

@app.post("/api/chat/stream")
async def chat_stream(request_data: ChatRequest, x_api_key: Optional[str] = Header(None)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM threads WHERE id = ?", (request_data.thread_id,))
    thread = cursor.fetchone()
    if not thread:
        conn.close()
        raise HTTPException(status_code=404, detail="Thread not found")
    
    api_key = request_data.api_key or x_api_key or DEFAULT_API_KEY
    if not api_key:
        conn.close()
        raise HTTPException(status_code=400, detail="SCX API Key is required. Please provide it in settings or header.")
    
    base_url = request_data.base_url or DEFAULT_BASE_URL
    model_name = request_data.model or thread["model"] or DEFAULT_MODEL
    
    # Fetch thread documents & folder documents for RAG context
    cursor.execute("SELECT filename, content FROM documents WHERE thread_id = ?", (request_data.thread_id,))
    docs = cursor.fetchall()
    
    folder_context = get_folder_documents_context()
    
    thread_doc_parts = []
    if docs:
        for d in docs:
            thread_doc_parts.append(f"--- UPLOADED DOCUMENT: {d['filename']} ---\n{d['content'][:4000]}")
    thread_context = "\n\n".join(thread_doc_parts)
    
    doc_context_parts = []
    if folder_context:
        doc_context_parts.append(folder_context)
    if thread_context:
        doc_context_parts.append(thread_context)
        
    doc_context_str = "\n\n".join(doc_context_parts)

    now = datetime.utcnow().isoformat()
    
    # Save user message
    user_msg_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO messages (id, thread_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_msg_id, request_data.thread_id, "user", request_data.message, now)
    )
    
    # Auto-generate title if new
    cursor.execute("SELECT COUNT(*) as count FROM messages WHERE thread_id = ?", (request_data.thread_id,))
    msg_count = cursor.fetchone()["count"]
    new_title = None
    if msg_count <= 1 or thread["title"] in ["New Conversation", "New Chat"]:
        clean_msg = request_data.message.strip().replace("\n", " ")
        new_title = clean_msg[:32] + ("..." if len(clean_msg) > 32 else "")
        cursor.execute("UPDATE threads SET title = ?, updated_at = ? WHERE id = ?", (new_title, now, request_data.thread_id))
    else:
        cursor.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, request_data.thread_id))
    
    # Fetch message history for LLM
    cursor.execute("SELECT role, content FROM messages WHERE thread_id = ? ORDER BY timestamp ASC", (request_data.thread_id,))
    history_rows = cursor.fetchall()
    conn.commit()
    conn.close()
    
    formatted_messages = []
    
    # Construct Document Context if available
    if doc_context_str:
        effective_system_prompt = (
            f"DOCUMENT KNOWLEDGE BASE (Source documents uploaded by user):\n"
            f"{doc_context_str}\n\n"
            f"INSTRUCTION: Answer the user's question accurately based on the Document Knowledge Base provided above. Cite filenames when relevant."
        )
        formatted_messages.append({"role": "system", "content": effective_system_prompt})
    
    for row in history_rows:
        formatted_messages.append({"role": row["role"], "content": row["content"]})

    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key
    )

    async def generate_response():
        assistant_content = ""
        assistant_msg_id = str(uuid.uuid4())
        
        if new_title:
            yield f"data: {json.dumps({'type': 'meta', 'new_title': new_title})}\n\n"
        
        try:
            completion_stream = await client.chat.completions.create(
                model=model_name,
                messages=formatted_messages,
                temperature=request_data.temperature or 0.7,
                stream=True
            )
            
            async for chunk in completion_stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    delta = chunk.choices[0].delta.content
                    assistant_content += delta
                    yield f"data: {json.dumps({'type': 'content', 'delta': delta})}\n\n"
                    
            # Send evaluating status event to UI
            yield f"data: {json.dumps({'type': 'grounded_evaluating', 'message_id': assistant_msg_id})}\n\n"

            # Execute Grounded AI Verification
            grounded_res = await asyncio.to_thread(
                monitor_grounded,
                question=request_data.message,
                response=assistant_content,
                agent_id=request_data.grounded_agent_id,
                api_key=request_data.grounded_api_key
            )
            
            grounded_json_str = json.dumps(grounded_res)
            
            # Save assistant message + Grounded result to DB
            finish_time = datetime.utcnow().isoformat()
            db_conn = get_db()
            c = db_conn.cursor()
            c.execute(
                "INSERT INTO messages (id, thread_id, role, content, timestamp, grounded_result) VALUES (?, ?, ?, ?, ?, ?)",
                (assistant_msg_id, request_data.thread_id, "assistant", assistant_content, finish_time, grounded_json_str)
            )
            db_conn.commit()
            db_conn.close()
            
            # Yield Grounded Verification result payload to client UI
            yield f"data: {json.dumps({'type': 'grounded_verification', 'message_id': assistant_msg_id, 'verification': grounded_res})}\n\n"
            
            # Yield final done event
            yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_msg_id, 'full_content': assistant_content})}\n\n"

        except Exception as e:
            error_msg = str(e)
            yield f"data: {json.dumps({'type': 'error', 'error': error_msg})}\n\n"

    return StreamingResponse(generate_response(), media_type="text/event-stream")

# Open Public Integration Endpoints (For connecting external apps with NO bearer tokens needed)
class OpenIntegrationRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = 0.7

@app.post("/")
@app.post("/chat")
@app.post("/api/v1/chat")
async def open_public_chat(data: OpenIntegrationRequest):
    """
    Public open endpoint for external application integrations.
    No Bearer token required. Server uses configured SCX_API_KEY automatically.
    """
    if not DEFAULT_API_KEY:
        raise HTTPException(status_code=500, detail="Server SCX_API_KEY is not configured in .env file.")
        
    thread_id = data.thread_id
    conn = get_db()
    cursor = conn.cursor()
    
    if not thread_id:
        thread_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        clean_msg = data.message.strip().replace("\n", " ")
        title = clean_msg[:32] + ("..." if len(clean_msg) > 32 else "")
        cursor.execute(
            "INSERT INTO threads (id, title, system_prompt, model, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (thread_id, title, None, data.model or DEFAULT_MODEL, 0, now, now)
        )
        conn.commit()
    
    now = datetime.utcnow().isoformat()
    user_msg_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO messages (id, thread_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_msg_id, thread_id, "user", data.message, now)
    )
    
    cursor.execute("SELECT role, content FROM messages WHERE thread_id = ? ORDER BY timestamp ASC", (thread_id,))
    history_rows = cursor.fetchall()
    
    cursor.execute("SELECT filename, content FROM documents WHERE thread_id = ?", (thread_id,))
    docs = cursor.fetchall()
    conn.commit()
    conn.close()
    
    folder_context = get_folder_documents_context()
    
    thread_doc_parts = []
    if docs:
        for d in docs:
            thread_doc_parts.append(f"--- UPLOADED DOCUMENT: {d['filename']} ---\n{d['content'][:4000]}")
    thread_context = "\n\n".join(thread_doc_parts)
    
    doc_context_parts = []
    if folder_context:
        doc_context_parts.append(folder_context)
    if thread_context:
        doc_context_parts.append(thread_context)
        
    doc_context_str = "\n\n".join(doc_context_parts)

    formatted_msgs = []
    if doc_context_str:
        formatted_msgs.append({
            "role": "system",
            "content": (
                "DOCUMENT KNOWLEDGE BASE (Trained/Loaded from documents folder & uploaded files):\n"
                f"{doc_context_str}\n\n"
                "INSTRUCTION: You must answer the user's question accurately based on the Document Knowledge Base provided above. Cite filenames when relevant."
            )
        })
    for row in history_rows:
        formatted_msgs.append({"role": row["role"], "content": row["content"]})

    client = AsyncOpenAI(
        base_url=DEFAULT_BASE_URL,
        api_key=DEFAULT_API_KEY
    )
    
    model_name = data.model or DEFAULT_MODEL

    try:
        completion = await client.chat.completions.create(
            model=model_name,
            messages=formatted_msgs,
            temperature=data.temperature or 0.7
        )
        
        assistant_content = completion.choices[0].message.content or ""
        
        # Execute Grounded AI verification in background thread to prevent HTTP timeouts
        threading.Thread(
            target=monitor_grounded,
            args=(data.message, assistant_content)
        ).start()
        
        # Save assistant message to DB
        finish_time = datetime.utcnow().isoformat()
        db_conn = get_db()
        c = db_conn.cursor()
        c.execute(
            "INSERT INTO messages (id, thread_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), thread_id, "assistant", assistant_content, finish_time)
        )
        db_conn.commit()
        db_conn.close()

        return {
            "status": "success",
            "thread_id": thread_id,
            "question": data.message,
            "answer": assistant_content,
            "response": assistant_content,
            "model": model_name
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Folder Document Management Endpoints (Global documents folder)
@app.get("/api/folder-documents")
def list_folder_documents_api():
    docs = load_folder_documents()
    res = []
    for d in docs:
        res.append({
            "filename": d["filename"],
            "file_type": d["ext"],
            "file_size": d["size"],
            "text_length": len(d["content"]),
            "preview": d["content"][:200] + "..." if len(d["content"]) > 200 else d["content"]
        })
    return res

@app.post("/api/folder-documents/upload")
async def upload_folder_document(file: UploadFile = File(...)):
    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds 20MB limit.")
        
    extracted_text = extract_text_from_file(file.filename, file_bytes)
    if not extracted_text:
        raise HTTPException(status_code=400, detail="Could not extract text from uploaded document.")
        
    save_path = os.path.join(DOCUMENTS_DIR, file.filename)
    with open(save_path, "wb") as f:
        f.write(file_bytes)
        
    return {
        "status": "success",
        "filename": file.filename,
        "file_size": len(file_bytes),
        "text_length": len(extracted_text),
        "message": f"Document '{file.filename}' saved to documents folder and available for training/answering."
    }

@app.delete("/api/folder-documents/{filename:path}")
def delete_folder_document(filename: str):
    file_path = os.path.join(DOCUMENTS_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found in documents folder")
        
    os.remove(file_path)
    return {"status": "deleted", "filename": filename}

@app.post("/api/folder-documents/sync")
def sync_folder_documents():
    docs = load_folder_documents()
    return {
        "status": "synced",
        "count": len(docs),
        "files": [d["filename"] for d in docs]
    }

# Static Files & Frontend Serving
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_index():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
