#!/usr/bin/env python3
"""
SCX.AI Standalone Command Line ChatBot
-------------------------------------
Fast, token-efficient interactive CLI chatbot powered strictly by SCX.AI API.
"""

import os
import sys
import json
import asyncio
import io
from datetime import datetime
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

import pypdf
import docx
from openai import AsyncOpenAI

load_dotenv()

# Configuration Defaults
DEFAULT_BASE_URL = os.getenv("SCX_BASE_URL", "https://api.scx.ai/v1")
DEFAULT_API_KEY = os.getenv("SCX_API_KEY", "sk-scx-6d7be01ef8e90c890b17622e309053f3")
DEFAULT_MODEL = os.getenv("SCX_DEFAULT_MODEL", "Meta-Llama-3.3-70B-Instruct")

# Windows Console Encoding Fix
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ANSI Color Codes
class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    AMBER = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

def extract_text_from_file(filepath: str) -> str:
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
        
    ext = os.path.splitext(filepath)[1].lower()
    text = ""
    
    with open(filepath, "rb") as f:
        file_bytes = f.read()
        
    if ext == ".pdf":
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        pages = [p.extract_text().strip() for p in reader.pages if p.extract_text()]
        text = "\n\n".join(pages)
    elif ext == ".docx":
        doc = docx.Document(io.BytesIO(file_bytes))
        text = "\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])
    else:
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1", errors="ignore")
            
    return text.strip()

class SCXCLIChatBot:
    def __init__(self):
        self.api_key = DEFAULT_API_KEY or os.getenv("SCX_API_KEY", "")
        self.base_url = DEFAULT_BASE_URL
        self.model = DEFAULT_MODEL
        self.messages: List[Dict[str, str]] = []
        self.documents: Dict[str, str] = {} # filename -> text
        self.client = None
        self.init_client()

    def init_client(self):
        if self.api_key:
            self.client = AsyncOpenAI(
                base_url=self.base_url,
                api_key=self.api_key
            )

    def print_banner(self):
        print(f"\n{Colors.CYAN}{Colors.BOLD}========================================================")
        print(f"               SCX.AI STANDALONE CLI CHATBOT            ")
        print(f"========================================================{Colors.RESET}")
        print(f"{Colors.DIM}Model: {self.model} | Base URL: {self.base_url}{Colors.RESET}")
        print(f"{Colors.AMBER}Type '/help' for commands or '/exit' to quit.{Colors.RESET}\n")

    def print_help(self):
        print(f"\n{Colors.BOLD}AVAILABLE CLI COMMANDS:{Colors.RESET}")
        print(f"  {Colors.CYAN}/new{Colors.RESET}             - Start a new chat session")
        print(f"  {Colors.CYAN}/doc <filepath>{Colors.RESET}  - Load document context (PDF, TXT, DOCX, MD)")
        print(f"  {Colors.CYAN}/docs{Colors.RESET}            - List attached documents")
        print(f"  {Colors.CYAN}/cleardocs{Colors.RESET}       - Clear attached document context")
        print(f"  {Colors.CYAN}/model <name>{Colors.RESET}    - Switch LLM model")
        print(f"  {Colors.CYAN}/history{Colors.RESET}         - View message history")
        print(f"  {Colors.CYAN}/export{Colors.RESET}          - Save chat transcript to markdown")
        print(f"  {Colors.CYAN}/clear{Colors.RESET}           - Clear console screen")
        print(f"  {Colors.CYAN}/exit{Colors.RESET}            - Quit CLI\n")

    def build_effective_messages(self) -> List[Dict[str, str]]:
        formatted = []
        
        # Token-efficient document context injection
        if self.documents:
            doc_parts = []
            for fname, text in self.documents.items():
                # Limit snippet length to 2500 chars to save tokens
                snippet = text[:2500]
                doc_parts.append(f"--- Doc: {fname} ---\n{snippet}")
            doc_ctx = "\n\n".join(doc_parts)
            formatted.append({"role": "system", "content": f"Context Documents:\n{doc_ctx}"})
            
        formatted.extend(self.messages)
        return formatted

    async def chat(self, user_text: str):
        if not self.api_key:
            print(f"{Colors.RED}[!] SCX_API_KEY is missing. Please set SCX_API_KEY in .env file.{Colors.RESET}")
            return
            
        if not self.client:
            self.init_client()
            
        self.messages.append({"role": "user", "content": user_text})
        formatted_msgs = self.build_effective_messages()
        
        print(f"\n{Colors.GREEN}{Colors.BOLD}SCX.AI:{Colors.RESET} ", end="", flush=True)
        
        assistant_reply = ""
        try:
            stream = await self.client.client.chat.completions.create(
                model=self.model,
                messages=formatted_msgs,
                temperature=0.7,
                stream=True
            ) if hasattr(self.client, 'client') else await self.client.chat.completions.create(
                model=self.model,
                messages=formatted_msgs,
                temperature=0.7,
                stream=True
            )
            
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    assistant_reply += token
                    print(token, end="", flush=True)
            print("\n")
            
            self.messages.append({"role": "assistant", "content": assistant_reply})
            
        except Exception as e:
            print(f"\n{Colors.RED}[!] Error: {e}{Colors.RESET}\n")

async def main():
    bot = SCXCLIChatBot()
    bot.print_banner()
    
    while True:
        try:
            doc_info = f" [{len(bot.documents)} doc]" if bot.documents else ""
            user_input = input(f"{Colors.CYAN}{Colors.BOLD}You{doc_info}>{Colors.RESET} ").strip()
            
            if not user_input:
                continue
                
            cmd = user_input.lower()
            if cmd in ["/exit", "exit", "quit"]:
                print(f"\n{Colors.CYAN}[OK] Goodbye! SCX.AI session ended.{Colors.RESET}\n")
                break
                
            elif cmd == "/help":
                bot.print_help()
                
            elif cmd == "/new":
                bot.messages = []
                print(f"{Colors.GREEN}[OK] Started new chat thread.{Colors.RESET}")
                
            elif cmd == "/clear":
                os.system("cls" if os.name == "nt" else "clear")
                bot.print_banner()
                
            elif cmd.startswith("/doc "):
                filepath = user_input[5:].strip().strip('"').strip("'")
                try:
                    text = extract_text_from_file(filepath)
                    fname = os.path.basename(filepath)
                    bot.documents[fname] = text
                    print(f"{Colors.GREEN}[OK] Loaded document: '{fname}' ({len(text)} chars extracted){Colors.RESET}")
                except Exception as e:
                    print(f"{Colors.RED}[!] Could not load document: {e}{Colors.RESET}")
                    
            elif cmd == "/docs":
                if not bot.documents:
                    print(f"{Colors.DIM}No documents attached.{Colors.RESET}")
                else:
                    print(f"{Colors.BOLD}Attached Documents:{Colors.RESET}")
                    for fname, text in bot.documents.items():
                        print(f"  * {Colors.CYAN}{fname}{Colors.RESET} ({len(text)} chars)")
                        
            elif cmd == "/cleardocs":
                bot.documents = {}
                print(f"{Colors.GREEN}[OK] All documents removed.{Colors.RESET}")
                
            elif cmd.startswith("/model "):
                bot.model = user_input[7:].strip()
                print(f"{Colors.GREEN}[OK] Switched model to: {bot.model}{Colors.RESET}")
                
            elif cmd == "/history":
                print(f"\n{Colors.BOLD}Chat History ({len(bot.messages)} msgs):{Colors.RESET}")
                for msg in bot.messages:
                    role_lbl = "[User]" if msg["role"] == "user" else "[AI]"
                    print(f"  {Colors.CYAN}{role_lbl}{Colors.RESET} {msg['content'][:100]}...")
                print()
                
            elif cmd == "/export":
                filename = f"scx_chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(f"# SCX.AI CLI Conversation\nDate: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nModel: {bot.model}\n\n")
                    for msg in bot.messages:
                        f.write(f"**{msg['role'].capitalize()}**:\n{msg['content']}\n\n")
                print(f"{Colors.GREEN}[OK] Exported chat to '{filename}'{Colors.RESET}")
                
            else:
                await bot.chat(user_input)

        except KeyboardInterrupt:
            print(f"\n{Colors.CYAN}[OK] Session ended.{Colors.RESET}\n")
            break

if __name__ == "__main__":
    asyncio.run(main())
