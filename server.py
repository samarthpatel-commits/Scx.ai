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

# Environment & Security Configuration Controls
ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")
APP_AUTH_KEY = os.getenv("APP_AUTH_KEY", "")

DB_FILE = os.getenv("DB_FILE", "chat_threads.db")
DEFAULT_BASE_URL = os.getenv("SCX_BASE_URL", "https://api.scx.ai/v1")
DEFAULT_API_KEY = os.getenv("SCX_API_KEY", "")
DEFAULT_MODEL = os.getenv("SCX_DEFAULT_MODEL", "Meta-Llama-3.3-70B-Instruct")

GROUNDED_ENDPOINT = os.getenv("GROUNDED_ENDPOINT", "https://grounded-topaz.vercel.app/api/v1/monitor")
GROUNDED_API_KEY = os.getenv("GROUNDED_API_KEY", "")
GROUNDED_AGENT_ID = os.getenv("GROUNDED_AGENT_ID", "")

# Rate limiting & file upload size bounds
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "20"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".json", ".csv", ".py", ".js", ".html"}

# Database Abstraction (Dual SQLite & Render PostgreSQL Support)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

IS_POSTGRES = bool(DATABASE_URL)

try:
    import psycopg2
except ImportError:
    psycopg2 = None

class DBConn:
    def __init__(self):
        self.is_postgres = bool(DATABASE_URL)
        if self.is_postgres:
            if not psycopg2:
                raise RuntimeError("psycopg2-binary package is required for PostgreSQL connections.")
            self.conn = psycopg2.connect(DATABASE_URL)
        else:
            self.conn = sqlite3.connect(DB_FILE, timeout=30.0)
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
            self.conn.row_factory = sqlite3.Row



    def execute(self, query: str, params: tuple = ()):
        cursor = self.conn.cursor()
        if self.is_postgres:
            pg_query = query.replace("?", "%s")
            cursor.execute(pg_query, params)
        else:
            cursor.execute(query, params)
        return cursor

    def fetchall(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        if self.is_postgres:
            pg_query = query.replace("?", "%s")
            cursor.execute(pg_query, params)
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def fetchone(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        if self.is_postgres:
            pg_query = query.replace("?", "%s")
            cursor.execute(pg_query, params)
            row = cursor.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cursor.description]
            return dict(zip(columns, row))
        else:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else None

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

def get_db() -> DBConn:
    return DBConn()

app = FastAPI(
    title="SCX.AI Threaded ChatBot (Render Production Hardened)",
    docs_url="/docs" if DEBUG else None,
    redoc_url="/redoc" if DEBUG else None
)

# CORS Configuration - Strict Allowed Origins in Production
raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()] if raw_origins != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    return response

# In-Memory IP Rate Limiter Middleware
IP_REQUESTS: Dict[str, List[float]] = {}
IP_LOCK = threading.Lock()

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path.startswith("/static"):
        return await call_next(request)
        
    client_ip = request.client.host if request.client else "127.0.0.1"
    now = datetime.utcnow().timestamp()
    
    with IP_LOCK:
        timestamps = IP_REQUESTS.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < 60]
        if len(timestamps) >= RATE_LIMIT_PER_MINUTE:
            raise HTTPException(status_code=429, detail="Rate limit exceeded. Please slow down your requests.")
        timestamps.append(now)
        IP_REQUESTS[client_ip] = timestamps

    return await call_next(request)

# Authentication & Path Traversal Security Helpers
def verify_auth(request: Request, custom_token: Optional[str] = None):
    if not APP_AUTH_KEY:
        return
    token = custom_token or request.headers.get("x-api-key") or request.headers.get("authorization", "").replace("Bearer ", "").strip()
    if token != APP_AUTH_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized request: Invalid or missing application key.")

def safe_path_in_dir(directory: str, filename: str) -> str:
    clean_name = os.path.basename(filename)
    full_path = os.path.realpath(os.path.join(directory, clean_name))
    dir_path = os.path.realpath(directory)
    if not full_path.startswith(dir_path):
        raise HTTPException(status_code=400, detail="Invalid file path or path traversal attempt detected.")
    return full_path

def validate_file_upload(filename: str, file_bytes: bytes):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File extension '{ext}' is not allowed.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"File size exceeds maximum allowed size of {MAX_UPLOAD_SIZE_MB}MB.")

def mask_secret(val: Optional[str]) -> str:
    if not val:
        return "Not Configured"
    if len(val) <= 8:
        return "****"
    return f"{val[:4]}...{val[-4:]}"

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
            
        else: # .txt, .md, .csv, .json, .py, .js, .html
            try:
                text = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = file_bytes.decode("latin-1", errors="ignore")
    except Exception:
        text = f"[Could not parse content of {os.path.basename(filename)}]"
        
    return text.strip()

# Folder Document Knowledge Base directory
DOCUMENTS_DIR = os.getenv("DOCUMENTS_DIR", os.path.join(os.path.dirname(__file__), "documents"))
os.makedirs(DOCUMENTS_DIR, exist_ok=True)

def extract_text_from_path(filepath: str) -> str:
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
        return extract_text_from_file(os.path.basename(filepath), file_bytes)
    except Exception:
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
            except Exception:
                pass
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
    
    if not target_api_key or not target_agent_id:
        return {"success": False, "error": "Grounded AI API key or Agent ID not configured."}

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
            timeout=30
        )
        if res.status_code == 200:
            data = res.json()
            data["success"] = True
            return data
        else:
            return {
                "success": False,
                "error": f"Grounded API returned HTTP {res.status_code}"
            }
    except Exception:
        return {
            "success": False,
            "error": "Grounded AI connection timeout or failure."
        }

# Database Initialization (SQLite & PostgreSQL Compatible)
def init_db():
    db = get_db()
    
    db.execute("""
    CREATE TABLE IF NOT EXISTS threads (
        id VARCHAR(255) PRIMARY KEY,
        title TEXT NOT NULL,
        system_prompt TEXT,
        model VARCHAR(255),
        pinned INT DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
    
    db.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id VARCHAR(255) PRIMARY KEY,
        thread_id VARCHAR(255) NOT NULL,
        role VARCHAR(50) NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        grounded_result TEXT,
        FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE
    )
    """)
    
    db.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id VARCHAR(255) PRIMARY KEY,
        thread_id VARCHAR(255) NOT NULL,
        filename TEXT NOT NULL,
        file_type VARCHAR(50) NOT NULL,
        file_size BIGINT NOT NULL,
        content TEXT NOT NULL,
        uploaded_at TEXT NOT NULL,
        FOREIGN KEY (thread_id) REFERENCES threads (id) ON DELETE CASCADE
    )
    """)
    
    if db.is_postgres:
        col = db.fetchone("SELECT column_name FROM information_schema.columns WHERE table_name = 'messages' AND column_name = 'grounded_result'")
        if not col:
            db.execute("ALTER TABLE messages ADD COLUMN grounded_result TEXT")
    else:
        cursor = db.conn.cursor()
        cursor.execute("PRAGMA table_info(messages)")
        columns = [c[1] for c in cursor.fetchall()]
        if "grounded_result" not in columns:
            db.execute("ALTER TABLE messages ADD COLUMN grounded_result TEXT")
            
    db.commit()
    db.close()

init_db()

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
@app.get("/health")
@app.get("/api/health")
def health_check():
    return {
        "status": "online",
        "database": "PostgreSQL" if IS_POSTGRES else "SQLite",
        "environment": ENVIRONMENT,
        "timestamp": datetime.utcnow().isoformat(),
        "scx_configured": bool(DEFAULT_API_KEY),
        "grounded_configured": bool(GROUNDED_API_KEY),
        "auth_enabled": bool(APP_AUTH_KEY)
    }

@app.get("/api/config")
def get_config(request: Request):
    verify_auth(request)
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
async def upload_document(thread_id: str, request: Request, file: UploadFile = File(...)):
    verify_auth(request)
    db = get_db()
    thread = db.fetchone("SELECT id FROM threads WHERE id = ?", (thread_id,))
    if not thread:
        db.close()
        raise HTTPException(status_code=404, detail="Thread not found")
        
    file_bytes = await file.read()
    validate_file_upload(file.filename, file_bytes)
        
    extracted_text = extract_text_from_file(file.filename, file_bytes)
    if not extracted_text:
        db.close()
        raise HTTPException(status_code=400, detail="Could not extract text from uploaded document.")
        
    doc_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    clean_filename = os.path.basename(file.filename)
    file_type = os.path.splitext(clean_filename)[1].lower().replace(".", "") or "txt"
    
    db.execute(
        "INSERT INTO documents (id, thread_id, filename, file_type, file_size, content, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, thread_id, clean_filename, file_type, len(file_bytes), extracted_text, now)
    )
    db.commit()
    db.close()
    
    return {
        "id": doc_id,
        "thread_id": thread_id,
        "filename": clean_filename,
        "file_type": file_type,
        "file_size": len(file_bytes),
        "text_length": len(extracted_text),
        "preview": extracted_text[:200] + "...",
        "uploaded_at": now
    }

@app.get("/api/threads/{thread_id}/documents")
def list_documents(thread_id: str, request: Request):
    verify_auth(request)
    db = get_db()
    rows = db.fetchall("SELECT id, thread_id, filename, file_type, file_size, LENGTH(content) as text_length, uploaded_at FROM documents WHERE thread_id = ? ORDER BY uploaded_at DESC", (thread_id,))
    db.close()
    return rows

@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str, request: Request):
    verify_auth(request)
    db = get_db()
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    db.commit()
    db.close()
    return {"status": "deleted"}

@app.post("/api/grounded/score")
def score_with_grounded(data: GroundedScoreRequest, request: Request):
    verify_auth(request)
    result = monitor_grounded(
        question=data.question,
        response=data.ai_response,
        agent_id=data.agent_id,
        api_key=data.api_key
    )
    return {"status": "submitted", "result": result}

@app.get("/api/threads")
def list_threads(request: Request):
    verify_auth(request)
    db = get_db()
    rows = db.fetchall("""
        SELECT t.id, t.title, t.system_prompt, t.model, t.pinned, t.created_at, t.updated_at,
               (SELECT content FROM messages WHERE thread_id = t.id ORDER BY timestamp DESC LIMIT 1) as last_message,
               (SELECT COUNT(*) FROM messages WHERE thread_id = t.id) as message_count,
               (SELECT COUNT(*) FROM documents WHERE thread_id = t.id) as document_count
        FROM threads t
        ORDER BY t.pinned DESC, t.updated_at DESC
    """)
    db.close()
    return rows

@app.post("/api/threads")
def create_thread(data: ThreadCreate, request: Request):
    verify_auth(request)
    thread_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO threads (id, title, system_prompt, model, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (thread_id, data.title, None, data.model, 0, now, now)
    )
    db.commit()
    db.close()
    return {"id": thread_id, "title": data.title, "model": data.model, "created_at": now, "updated_at": now, "pinned": 0}

@app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str, request: Request):
    verify_auth(request)
    db = get_db()
    thread = db.fetchone("SELECT * FROM threads WHERE id = ?", (thread_id,))
    if not thread:
        db.close()
        raise HTTPException(status_code=404, detail="Thread not found")
    
    rows = db.fetchall("SELECT id, role, content, timestamp, grounded_result FROM messages WHERE thread_id = ? ORDER BY timestamp ASC", (thread_id,))
    doc_rows = db.fetchall("SELECT id, filename, file_type, file_size, LENGTH(content) as text_length, uploaded_at FROM documents WHERE thread_id = ?", (thread_id,))
    db.close()
    
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
    res["documents"] = doc_rows
    return res

@app.put("/api/threads/{thread_id}")
def update_thread(thread_id: str, data: ThreadUpdate, request: Request):
    verify_auth(request)
    db = get_db()
    thread = db.fetchone("SELECT * FROM threads WHERE id = ?", (thread_id,))
    if not thread:
        db.close()
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
    
    db.execute(f"UPDATE threads SET {', '.join(updates)} WHERE id = ?", tuple(params))
    db.commit()
    db.close()
    return {"status": "success"}

@app.delete("/api/threads/{thread_id}")
def delete_thread(thread_id: str, request: Request):
    verify_auth(request)
    db = get_db()
    db.execute("DELETE FROM documents WHERE thread_id = ?", (thread_id,))
    db.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
    db.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
    db.commit()
    db.close()
    return {"status": "deleted"}

@app.delete("/api/threads")
def clear_all_threads(request: Request):
    verify_auth(request)
    db = get_db()
    db.execute("DELETE FROM documents")
    db.execute("DELETE FROM messages")
    db.execute("DELETE FROM threads")
    db.commit()
    db.close()
    return {"status": "cleared"}

@app.post("/api/chat/stream")
async def chat_stream(request_data: ChatRequest, request: Request, x_api_key: Optional[str] = Header(None)):
    verify_auth(request, custom_token=x_api_key)
    db = get_db()
    thread = db.fetchone("SELECT * FROM threads WHERE id = ?", (request_data.thread_id,))
    if not thread:
        db.close()
        raise HTTPException(status_code=404, detail="Thread not found")
    
    api_key = request_data.api_key or x_api_key or DEFAULT_API_KEY
    if not api_key:
        db.close()
        raise HTTPException(status_code=400, detail="SCX API Key is required. Please provide it in environment settings or request header.")
    
    base_url = request_data.base_url or DEFAULT_BASE_URL
    model_name = request_data.model or thread["model"] or DEFAULT_MODEL
    
    docs = db.fetchall("SELECT filename, content FROM documents WHERE thread_id = ?", (request_data.thread_id,))
    
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
    
    user_msg_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO messages (id, thread_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_msg_id, request_data.thread_id, "user", request_data.message, now)
    )
    
    msg_count_res = db.fetchone("SELECT COUNT(*) as count FROM messages WHERE thread_id = ?", (request_data.thread_id,))
    msg_count = msg_count_res["count"] if msg_count_res else 0
    new_title = None
    if msg_count <= 1 or thread["title"] in ["New Conversation", "New Chat"]:
        clean_msg = request_data.message.strip().replace("\n", " ")
        new_title = clean_msg[:32] + ("..." if len(clean_msg) > 32 else "")
        db.execute("UPDATE threads SET title = ?, updated_at = ? WHERE id = ?", (new_title, now, request_data.thread_id))
    else:
        db.execute("UPDATE threads SET updated_at = ? WHERE id = ?", (now, request_data.thread_id))
    
    history_rows = db.fetchall("SELECT role, content FROM messages WHERE thread_id = ? ORDER BY timestamp ASC", (request_data.thread_id,))
    db.commit()
    db.close()
    
    formatted_messages = []
    
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
                    
            yield f"data: {json.dumps({'type': 'grounded_evaluating', 'message_id': assistant_msg_id})}\n\n"

            grounded_res = await asyncio.to_thread(
                monitor_grounded,
                question=request_data.message,
                response=assistant_content,
                agent_id=request_data.grounded_agent_id,
                api_key=request_data.grounded_api_key
            )
            
            grounded_json_str = json.dumps(grounded_res)
            
            finish_time = datetime.utcnow().isoformat()
            db_conn = get_db()
            db_conn.execute(
                "INSERT INTO messages (id, thread_id, role, content, timestamp, grounded_result) VALUES (?, ?, ?, ?, ?, ?)",
                (assistant_msg_id, request_data.thread_id, "assistant", assistant_content, finish_time, grounded_json_str)
            )
            db_conn.commit()
            db_conn.close()
            
            yield f"data: {json.dumps({'type': 'grounded_verification', 'message_id': assistant_msg_id, 'verification': grounded_res})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_msg_id, 'full_content': assistant_content})}\n\n"

        except Exception:
            yield f"data: {json.dumps({'type': 'error', 'error': 'An error occurred while generating response.'})}\n\n"

    return StreamingResponse(generate_response(), media_type="text/event-stream")

# Open Public Integration Endpoints
class OpenIntegrationRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = 0.7

@app.post("/")
@app.post("/chat")
@app.post("/api/v1/chat")
async def open_public_chat(data: OpenIntegrationRequest, request: Request):
    verify_auth(request)
    if not DEFAULT_API_KEY:
        raise HTTPException(status_code=500, detail="Server SCX_API_KEY environment variable is not configured.")
        
    thread_id = data.thread_id
    db = get_db()
    
    if not thread_id:
        thread_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        clean_msg = data.message.strip().replace("\n", " ")
        title = clean_msg[:32] + ("..." if len(clean_msg) > 32 else "")
        db.execute(
            "INSERT INTO threads (id, title, system_prompt, model, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (thread_id, title, None, data.model or DEFAULT_MODEL, 0, now, now)
        )
        db.commit()
    
    now = datetime.utcnow().isoformat()
    user_msg_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO messages (id, thread_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_msg_id, thread_id, "user", data.message, now)
    )
    
    history_rows = db.fetchall("SELECT role, content FROM messages WHERE thread_id = ? ORDER BY timestamp ASC", (thread_id,))
    docs = db.fetchall("SELECT filename, content FROM documents WHERE thread_id = ?", (thread_id,))
    db.commit()
    db.close()
    
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
        
        threading.Thread(
            target=monitor_grounded,
            args=(data.message, assistant_content)
        ).start()
        
        finish_time = datetime.utcnow().isoformat()
        db_conn = get_db()
        db_conn.execute(
            "INSERT INTO messages (id, thread_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), thread_id, "assistant", assistant_content, finish_time)
        )
        db_conn.commit()
        db_conn.close()

        return {
            "status": "success",
            "thread_id": thread_id,
            "answer": assistant_content,
            "response": assistant_content,
            "model": model_name
        }

    except Exception:
        raise HTTPException(status_code=500, detail="Error communicating with LLM provider.")

# Folder Document Management Endpoints (Global documents folder)
@app.get("/api/folder-documents")
def list_folder_documents_api(request: Request):
    verify_auth(request)
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
async def upload_folder_document(request: Request, file: UploadFile = File(...)):
    verify_auth(request)
    file_bytes = await file.read()
    validate_file_upload(file.filename, file_bytes)
        
    extracted_text = extract_text_from_file(file.filename, file_bytes)
    if not extracted_text:
        raise HTTPException(status_code=400, detail="Could not extract text from uploaded document.")
        
    save_path = safe_path_in_dir(DOCUMENTS_DIR, file.filename)
    with open(save_path, "wb") as f:
        f.write(file_bytes)
        
    return {
        "status": "success",
        "filename": os.path.basename(file.filename),
        "file_size": len(file_bytes),
        "text_length": len(extracted_text),
        "message": f"Document '{os.path.basename(file.filename)}' saved to documents folder and available for training/answering."
    }

@app.delete("/api/folder-documents/{filename:path}")
def delete_folder_document(filename: str, request: Request):
    verify_auth(request)
    file_path = safe_path_in_dir(DOCUMENTS_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found in documents folder")
        
    os.remove(file_path)
    return {"status": "deleted", "filename": os.path.basename(filename)}

@app.post("/api/folder-documents/sync")
def sync_folder_documents(request: Request):
    verify_auth(request)
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
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=DEBUG)
