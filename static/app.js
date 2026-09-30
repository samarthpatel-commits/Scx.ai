// State Management
const state = {
    threads: [],
    activeThreadId: null,
    activeThread: null,
    isGenerating: false,
    abortController: null,
    config: {
        apiKey: localStorage.getItem("scx_api_key") || "",
        baseUrl: localStorage.getItem("scx_base_url") || "https://api.scx.ai/v1",
        defaultModel: localStorage.getItem("scx_default_model") || "Meta-Llama-3.3-70B-Instruct",
        temperature: parseFloat(localStorage.getItem("scx_temperature") || "0.7"),
        groundedAgentId: localStorage.getItem("grounded_agent_id") || "7646d28c-446b-491c-8f32-7d1c134d78a9",
        groundedApiKey: localStorage.getItem("grounded_api_key") || "grnd_c69448bc68f344fba557d1c465cf5a20b5daa9ac0737d87f"
    }
};

// DOM Elements
const elements = {
    sidebar: document.getElementById("sidebar"),
    btnNewChat: document.getElementById("btn-new-chat"),
    searchInput: document.getElementById("search-threads-input"),
    threadsList: document.getElementById("threads-list"),
    threadCount: document.getElementById("thread-count"),
    activeModelDisplay: document.getElementById("active-model-display"),
    apiStatusDot: document.getElementById("api-status-dot"),
    btnOpenSettings: document.getElementById("btn-open-settings"),
    btnClearAll: document.getElementById("btn-clear-all"),
    
    currentThreadTitle: document.getElementById("current-thread-title"),
    threadCreatedAt: document.getElementById("thread-created-at"),
    headerModelSelect: document.getElementById("header-model-select"),
    btnExportChat: document.getElementById("btn-export-chat"),
    btnToggleSidebar: document.getElementById("btn-toggle-sidebar"),
    
    chatMessages: document.getElementById("chat-messages"),
    welcomeScreen: document.getElementById("welcome-screen"),
    
    userInput: document.getElementById("user-input"),
    btnSend: document.getElementById("btn-send"),
    btnStopGen: document.getElementById("btn-stop-gen"),
    charCount: document.getElementById("char-count"),
    
    // Document Upload Elements
    btnUploadDoc: document.getElementById("btn-upload-doc"),
    fileInputElement: document.getElementById("file-input-element"),
    attachedDocsBar: document.getElementById("attached-docs-bar"),
    
    // Settings Modal
    settingsModal: document.getElementById("settings-modal"),
    btnCloseSettings: document.getElementById("btn-close-settings"),
    settingApiKey: document.getElementById("setting-api-key"),
    btnToggleKeyVis: document.getElementById("btn-toggle-key-visibility"),
    settingBaseUrl: document.getElementById("setting-base-url"),
    settingDefaultModel: document.getElementById("setting-default-model"),
    settingTemperature: document.getElementById("setting-temperature"),
    settingGroundedAgentId: document.getElementById("setting-grounded-agent-id"),
    settingGroundedApiKey: document.getElementById("setting-grounded-api-key"),
    tempValDisplay: document.getElementById("temp-val-display"),
    btnSaveSettings: document.getElementById("btn-save-settings")
};

// Initialize Application
document.addEventListener("DOMContentLoaded", async () => {
    initLucideIcons();
    initSettingsUI();
    setupEventListeners();
    
    // Check if embedded in external website as a widget
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get("embed") === "true") {
        document.body.classList.add("embedded-mode");
        if (elements.sidebar) elements.sidebar.style.display = "none";
    }

    await fetchBackendConfig();
    await loadThreads();
    
    if (state.threads.length > 0) {
        selectThread(state.threads[0].id);
    } else {
        createNewThread();
    }
});

function initLucideIcons() {
    if (window.lucide) {
        lucide.createIcons();
    }
}

function initSettingsUI() {
    elements.settingApiKey.value = state.config.apiKey;
    elements.settingBaseUrl.value = state.config.baseUrl;
    elements.settingDefaultModel.value = state.config.defaultModel;
    elements.settingTemperature.value = state.config.temperature;
    elements.tempValDisplay.textContent = state.config.temperature;
    elements.headerModelSelect.value = state.config.defaultModel;
    
    if (elements.settingGroundedAgentId) elements.settingGroundedAgentId.value = state.config.groundedAgentId;
    if (elements.settingGroundedApiKey) elements.settingGroundedApiKey.value = state.config.groundedApiKey;
    
    updateAPIStatusBadge();
}

function updateAPIStatusBadge() {
    if (state.config.apiKey) {
        elements.apiStatusDot.style.background = "#10a37f";
        elements.apiStatusDot.style.boxShadow = "0 0 8px #10a37f";
    } else {
        elements.apiStatusDot.style.background = "#f59e0b";
        elements.apiStatusDot.style.boxShadow = "0 0 8px #f59e0b";
    }
    elements.activeModelDisplay.textContent = state.config.defaultModel;
}

async function fetchBackendConfig() {
    try {
        const res = await fetch("/api/config");
        if (res.ok) {
            const data = await res.json();
            if (!state.config.apiKey && data.has_default_api_key) {
                elements.apiStatusDot.style.background = "#10a37f";
            }
        }
    } catch (e) {
        console.warn("Backend config fetch failed:", e);
    }
}

// Thread Operations
async function loadThreads() {
    try {
        const res = await fetch("/api/threads");
        if (res.ok) {
            state.threads = await res.json();
            renderThreadsList();
        }
    } catch (e) {
        console.error("Failed to load threads:", e);
    }
}

function renderThreadsList(filterQuery = "") {
    elements.threadsList.innerHTML = "";
    
    const filtered = state.threads.filter(t => 
        t.title.toLowerCase().includes(filterQuery.toLowerCase())
    );
    
    elements.threadCount.textContent = filtered.length;
    
    if (filtered.length === 0) {
        elements.threadsList.innerHTML = `
            <div style="padding: 16px; text-align: center; color: var(--text-muted); font-size: 0.8rem;">
                No chats found
            </div>
        `;
        return;
    }
    
    filtered.forEach(thread => {
        const item = document.createElement("div");
        item.className = `thread-item ${thread.id === state.activeThreadId ? "active" : ""}`;
        item.onclick = () => selectThread(thread.id);
        
        const docBadge = thread.document_count > 0 ? `<i data-lucide="file-text" style="color: var(--accent-green); width: 13px; height: 13px;" title="${thread.document_count} document(s) attached"></i>` : '';
        
        item.innerHTML = `
            <div class="thread-item-content">
                <i data-lucide="${thread.pinned ? 'pin' : 'message-square'}"></i>
                <span class="thread-item-title" id="title-${thread.id}">${escapeHtml(thread.title)}</span>
                ${docBadge}
            </div>
            <div class="thread-item-actions">
                <button title="Rename" onclick="event.stopPropagation(); promptRenameThread('${thread.id}')">
                    <i data-lucide="edit-3"></i>
                </button>
                <button title="${thread.pinned ? 'Unpin' : 'Pin'}" onclick="event.stopPropagation(); togglePinThread('${thread.id}', ${!thread.pinned})">
                    <i data-lucide="pin"></i>
                </button>
                <button title="Delete" onclick="event.stopPropagation(); deleteThread('${thread.id}')">
                    <i data-lucide="trash-2"></i>
                </button>
            </div>
        `;
        elements.threadsList.appendChild(item);
    });
    
    initLucideIcons();
}

async function createNewThread(initialTitle = "New Conversation") {
    try {
        const res = await fetch("/api/threads", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                title: initialTitle,
                model: state.config.defaultModel
            })
        });
        
        if (res.ok) {
            const newThread = await res.json();
            state.threads.unshift(newThread);
            renderThreadsList();
            selectThread(newThread.id);
        }
    } catch (e) {
        console.error("Failed to create thread:", e);
    }
}

async function selectThread(threadId) {
    state.activeThreadId = threadId;
    renderThreadsList(elements.searchInput.value);
    
    try {
        const res = await fetch(`/api/threads/${threadId}`);
        if (res.ok) {
            state.activeThread = await res.json();
            
            elements.currentThreadTitle.textContent = state.activeThread.title;
            elements.threadCreatedAt.textContent = `Created: ${new Date(state.activeThread.created_at).toLocaleDateString()}`;
            elements.headerModelSelect.value = state.activeThread.model || state.config.defaultModel;
            
            renderAttachedDocuments(state.activeThread.documents || []);
            renderMessages(state.activeThread.messages || []);
        }
    } catch (e) {
        console.error("Failed to select thread:", e);
    }
}

// Document Management Functions
function renderAttachedDocuments(docs) {
    elements.attachedDocsBar.innerHTML = "";
    
    if (!docs || docs.length === 0) {
        elements.attachedDocsBar.classList.add("hidden");
        return;
    }
    
    elements.attachedDocsBar.classList.remove("hidden");
    
    docs.forEach(doc => {
        const sizeKb = Math.round(doc.file_size / 1024);
        const chip = document.createElement("div");
        chip.className = "doc-chip";
        chip.innerHTML = `
            <i data-lucide="file-text"></i>
            <span class="doc-chip-name" title="${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</span>
            <span class="doc-chip-size">(${sizeKb} KB)</span>
            <span class="doc-chip-remove" title="Remove document" onclick="deleteDocument('${doc.id}')">
                <i data-lucide="x" style="width: 12px; height: 12px;"></i>
            </span>
        `;
        elements.attachedDocsBar.appendChild(chip);
    });
    
    initLucideIcons();
}

async function handleFileUpload(file) {
    if (!state.activeThreadId) return;
    if (!file) return;
    
    const formData = new FormData();
    formData.append("file", file);
    
    // Show loading badge in attached docs bar
    elements.attachedDocsBar.classList.remove("hidden");
    const loadingChip = document.createElement("div");
    loadingChip.className = "doc-chip";
    loadingChip.id = "temp-upload-chip";
    loadingChip.innerHTML = `<i data-lucide="loader-2" class="spin"></i> Uploading & parsing ${escapeHtml(file.name)}...`;
    elements.attachedDocsBar.appendChild(loadingChip);
    initLucideIcons();
    
    try {
        const res = await fetch(`/api/threads/${state.activeThreadId}/documents`, {
            method: "POST",
            body: formData
        });
        
        if (res.ok) {
            await selectThread(state.activeThreadId);
            await loadThreads();
        } else {
            const err = await res.json();
            alert(`Document Upload Failed: ${err.detail || 'Could not upload file'}`);
        }
    } catch (e) {
        console.error("Upload error:", e);
        alert("Upload error: Failed to connect to server.");
    } finally {
        const tempChip = document.getElementById("temp-upload-chip");
        if (tempChip) tempChip.remove();
        elements.fileInputElement.value = "";
    }
}

async function deleteDocument(docId) {
    if (!confirm("Are you sure you want to remove this document from the conversation?")) return;
    try {
        const res = await fetch(`/api/documents/${docId}`, { method: "DELETE" });
        if (res.ok) {
            await selectThread(state.activeThreadId);
            await loadThreads();
        }
    } catch (e) {
        console.error("Delete doc error:", e);
    }
}

async function promptRenameThread(threadId) {
    const thread = state.threads.find(t => t.id === threadId);
    if (!thread) return;
    
    const newTitle = prompt("Enter new title:", thread.title);
    if (newTitle && newTitle.trim() !== "") {
        await updateThreadOnServer(threadId, { title: newTitle.trim() });
    }
}

async function togglePinThread(threadId, pinned) {
    await updateThreadOnServer(threadId, { pinned: pinned });
}

async function updateThreadOnServer(threadId, payload) {
    try {
        const res = await fetch(`/api/threads/${threadId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            await loadThreads();
            if (state.activeThreadId === threadId) {
                if (payload.title) elements.currentThreadTitle.textContent = payload.title;
            }
        }
    } catch (e) {
        console.error("Failed to update thread:", e);
    }
}

async function deleteThread(threadId) {
    if (!confirm("Are you sure you want to delete this thread?")) return;
    
    try {
        const res = await fetch(`/api/threads/${threadId}`, { method: "DELETE" });
        if (res.ok) {
            state.threads = state.threads.filter(t => t.id !== threadId);
            if (state.activeThreadId === threadId) {
                if (state.threads.length > 0) {
                    selectThread(state.threads[0].id);
                } else {
                    createNewThread();
                }
            } else {
                renderThreadsList();
            }
        }
    } catch (e) {
        console.error("Failed to delete thread:", e);
    }
}

async function clearAllThreads() {
    if (!confirm("Are you sure you want to clear ALL chat history?")) return;
    
    try {
        const res = await fetch("/api/threads", { method: "DELETE" });
        if (res.ok) {
            state.threads = [];
            createNewThread();
        }
    } catch (e) {
        console.error("Failed to clear threads:", e);
    }
}

// Render Messages
function renderMessages(messages) {
    elements.chatMessages.innerHTML = "";
    
    if (messages.length === 0) {
        elements.chatMessages.appendChild(elements.welcomeScreen);
        elements.welcomeScreen.classList.remove("hidden");
        return;
    }
    
    elements.welcomeScreen.classList.add("hidden");
    
    messages.forEach((msg, idx) => {
        const prevUserMsg = (msg.role === "assistant" && idx > 0 && messages[idx-1].role === "user") ? messages[idx-1].content : "";
        appendMessageRow(msg.role, msg.content, msg.id, prevUserMsg, msg.grounded_result);
    });
    
    scrollToBottom();
}

function appendMessageRow(role, content, messageId = null, userQuestion = "", groundedData = null) {
    const row = document.createElement("div");
    row.className = `message-row ${role}`;
    if (messageId) row.dataset.messageId = messageId;
    if (userQuestion) row.dataset.userQuestion = userQuestion;
    
    const isUser = role === "user";
    const avatarIcon = isUser ? "user" : "bot";
    const authorName = isUser ? "You" : "SCX.AI Assistant";
    
    const groundedHtml = !isUser ? (groundedData ? renderGroundedCardHtml(groundedData) : `<div class="grounded-card-container"></div>`) : '';
    
    row.innerHTML = `
        <div class="message-avatar">
            <i data-lucide="${avatarIcon}"></i>
        </div>
        <div class="message-content-wrapper">
            <div class="message-header">
                <span class="message-author">${authorName}</span>
                <span class="message-time">${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            </div>
            <div class="message-bubble">
                ${isUser ? escapeHtml(content).replace(/\n/g, '<br>') : parseMarkdown(content)}
            </div>
            ${groundedHtml}
            <div class="message-actions">
                <button class="message-action-btn" onclick="copyMessageContent(this)">
                    <i data-lucide="copy"></i> Copy
                </button>
            </div>
        </div>
    `;
    
    elements.chatMessages.appendChild(row);
    initLucideIcons();
    setupCodeBlockCopyButtons(row);
    return row;
}

// Generate Grounded AI Verification HTML Card
function renderGroundedCardHtml(data) {
    if (!data) return "";
    
    if (!data.success || data.ok === false && data.score === undefined) {
        return `
            <div class="grounded-card unavailable">
                <div class="grounded-header">
                    <span class="grounded-status-badge">
                        <i data-lucide="alert-triangle"></i> Verification unavailable — this response has not been automatically verified.
                    </span>
                </div>
            </div>
        `;
    }

    const score = (typeof data.score === 'number') ? data.score : 0;
    const risk = data.risk || (score >= 70 ? "LOW" : score >= 50 ? "MEDIUM" : "HIGH");
    
    let statusClass = "verified";
    let statusText = "Verified";
    let trustLevel = "High";
    let iconName = "check-circle-2";

    if (score >= 70 || risk === "LOW") {
        statusClass = "verified";
        statusText = "Verified";
        trustLevel = "High";
        iconName = "check-circle-2";
    } else if (score >= 50 || risk === "MEDIUM") {
        statusClass = "partial";
        statusText = "Partially Verified";
        trustLevel = "Medium";
        iconName = "alert-circle";
    } else {
        statusClass = "low";
        statusText = "Not Verified";
        trustLevel = "Low";
        iconName = "x-circle";
    }

    const agentId = data.agentId || state.config.groundedAgentId;
    const alertMsg = data.message || `Grounding score: ${score}% evaluated against evidence baseline`;

    return `
        <div class="grounded-card ${statusClass}">
            <div class="grounded-header">
                <div class="grounded-status-badge">
                    <i data-lucide="${iconName}"></i> Grounded AI Verification: ${statusText}
                </div>
                <div class="grounded-metrics">
                    <div class="grounded-metric-item">
                        <span>Grounding Score:</span>
                        <span class="score-value">${score}%</span>
                    </div>
                    <div class="grounded-metric-item">
                        <span>Trust Status:</span>
                        <span class="score-value">${trustLevel}</span>
                    </div>
                </div>
            </div>

            <details class="grounded-details-toggle">
                <summary>
                    <i data-lucide="info" style="width: 13px; height: 13px;"></i> Why was this response verified?
                </summary>
                <div class="grounded-details-content">
                    <div><strong>Grounding Score:</strong> ${score}%</div>
                    <div><strong>Risk Level:</strong> ${risk}</div>
                    <div><strong>Grounded Agent ID:</strong> <code>${agentId}</code></div>
                    <div><strong>Verification Summary:</strong> ${escapeHtml(alertMsg)}</div>
                    <div class="grounded-claims-list" style="margin-top: 4px;">
                        <div class="grounded-claim-item ${score >= 50 ? 'verified' : 'unverified'}">
                            <i data-lucide="${score >= 50 ? 'check' : 'x'}"></i>
                            <span>${score >= 50 ? 'Response is supported by uploaded document / reference evidence context.' : 'Response contains claims requiring additional document verification.'}</span>
                        </div>
                    </div>
                </div>
            </details>
        </div>
    `;
}

// Send Message & Real-Time Streaming
async function handleSendMessage() {
    const text = elements.userInput.value.trim();
    if (!text || state.isGenerating) return;
    
    if (!state.config.apiKey) {
        openSettingsModal();
        alert("Please enter your SCX.AI API Key in settings to send messages.");
        return;
    }
    
    elements.welcomeScreen.classList.add("hidden");
    
    elements.userInput.value = "";
    elements.userInput.style.height = "auto";
    elements.charCount.textContent = "0 chars";
    
    appendMessageRow("user", text);
    scrollToBottom();
    
    const assistantRow = appendMessageRow("assistant", "", null, text);
    const assistantBubble = assistantRow.querySelector(".message-bubble");
    const groundedContainer = assistantRow.querySelector(".grounded-card-container");
    
    const cursor = document.createElement("span");
    cursor.className = "cursor-typing";
    assistantBubble.appendChild(cursor);
    
    setGeneratingState(true);
    state.abortController = new AbortController();
    
    try {
        const response = await fetch("/api/chat/stream", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-API-Key": state.config.apiKey
            },
            body: JSON.stringify({
                thread_id: state.activeThreadId,
                message: text,
                api_key: state.config.apiKey,
                base_url: state.config.baseUrl,
                model: elements.headerModelSelect.value || state.config.defaultModel,
                temperature: state.config.temperature,
                grounded_agent_id: state.config.groundedAgentId,
                grounded_api_key: state.config.groundedApiKey
            }),
            signal: state.abortController.signal
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail || "Error connecting to SCX.AI server.");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let accumulatedText = "";
        let buffer = "";

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n\n");
            buffer = lines.pop();

            for (const line of lines) {
                if (line.startsWith("data: ")) {
                    const jsonStr = line.substring(6).trim();
                    if (!jsonStr) continue;
                    try {
                        const event = JSON.parse(jsonStr);
                        if (event.type === "meta" && event.new_title) {
                            elements.currentThreadTitle.textContent = event.new_title;
                            await loadThreads();
                        } else if (event.type === "content") {
                            accumulatedText += event.delta;
                            assistantBubble.innerHTML = parseMarkdown(accumulatedText);
                            assistantBubble.appendChild(cursor);
                            scrollToBottom();
                        } else if (event.type === "grounded_evaluating") {
                            if (groundedContainer) {
                                groundedContainer.innerHTML = `
                                    <div class="grounded-loading-skeleton">
                                        <i data-lucide="loader-2" class="spin"></i> Evaluating response against document evidence with Grounded AI...
                                    </div>
                                `;
                                initLucideIcons();
                            }
                        } else if (event.type === "grounded_verification") {
                            if (groundedContainer) {
                                groundedContainer.innerHTML = renderGroundedCardHtml(event.verification);
                                initLucideIcons();
                                scrollToBottom();
                            }
                        } else if (event.type === "done") {
                            accumulatedText = event.full_content || accumulatedText;
                            assistantBubble.innerHTML = parseMarkdown(accumulatedText);
                            setupCodeBlockCopyButtons(assistantRow);
                        } else if (event.type === "error") {
                            assistantBubble.innerHTML = `<span style="color: #ef4444;">⚠️ Error: ${escapeHtml(event.error)}</span>`;
                        }
                    } catch (err) {
                        console.error("Error parsing SSE JSON:", err);
                    }
                }
            }
        }

    } catch (error) {
        if (error.name === "AbortError") {
            assistantBubble.innerHTML += `<br><i style="color: var(--text-muted); font-size: 0.8rem;">[Generation Stopped]</i>`;
        } else {
            assistantBubble.innerHTML = `<span style="color: #ef4444;">⚠️ Request Failed: ${escapeHtml(error.message)}</span>`;
            if (groundedContainer) {
                groundedContainer.innerHTML = renderGroundedCardHtml({ success: false });
                initLucideIcons();
            }
        }
    } finally {
        cursor.remove();
        setGeneratingState(false);
        await loadThreads();
    }
}

function stopGeneration() {
    if (state.abortController) {
        state.abortController.abort();
    }
}

function setGeneratingState(isGen) {
    state.isGenerating = isGen;
    if (isGen) {
        elements.btnSend.classList.add("hidden");
        elements.btnStopGen.classList.remove("hidden");
    } else {
        elements.btnSend.classList.remove("hidden");
        elements.btnStopGen.classList.add("hidden");
    }
}

// Markdown & Code Highlighting Parser
function parseMarkdown(content) {
    if (!content) return "";
    try {
        marked.setOptions({
            highlight: function(code, lang) {
                if (lang && hljs.getLanguage(lang)) {
                    return hljs.highlight(code, { language: lang }).value;
                }
                return hljs.highlightAuto(code).value;
            },
            breaks: true
        });
        
        let html = marked.parse(content);
        return html;
    } catch (e) {
        return escapeHtml(content);
    }
}

function setupCodeBlockCopyButtons(container) {
    const preBlocks = container.querySelectorAll("pre");
    preBlocks.forEach((pre) => {
        if (pre.querySelector(".code-header")) return;
        
        const code = pre.querySelector("code");
        const lang = code?.className.replace("language-", "") || "code";
        
        const header = document.createElement("div");
        header.className = "code-header";
        header.innerHTML = `
            <span>${lang}</span>
            <button class="copy-code-btn" onclick="copyCodeText(this)">
                <i data-lucide="copy"></i> Copy code
            </button>
        `;
        pre.insertBefore(header, code);
    });
    initLucideIcons();
}

function copyCodeText(btn) {
    const pre = btn.closest("pre");
    const code = pre.querySelector("code");
    navigator.clipboard.writeText(code.innerText).then(() => {
        btn.innerHTML = `<i data-lucide="check"></i> Copied!`;
        initLucideIcons();
        setTimeout(() => {
            btn.innerHTML = `<i data-lucide="copy"></i> Copy code`;
            initLucideIcons();
        }, 2000);
    });
}

function copyMessageContent(btn) {
    const bubble = btn.closest(".message-content-wrapper").querySelector(".message-bubble");
    navigator.clipboard.writeText(bubble.innerText).then(() => {
        btn.innerHTML = `<i data-lucide="check"></i> Copied`;
        initLucideIcons();
        setTimeout(() => {
            btn.innerHTML = `<i data-lucide="copy"></i> Copy`;
            initLucideIcons();
        }, 2000);
    });
}

function scrollToBottom() {
    elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
}

// Event Listeners
function setupEventListeners() {
    elements.btnNewChat.onclick = () => createNewThread();
    elements.searchInput.oninput = (e) => renderThreadsList(e.target.value);
    elements.btnClearAll.onclick = clearAllThreads;
    
    // Document Upload Event Listeners
    if (elements.btnUploadDoc && elements.fileInputElement) {
        elements.btnUploadDoc.onclick = () => elements.fileInputElement.click();
        elements.fileInputElement.onchange = (e) => {
            if (e.target.files && e.target.files.length > 0) {
                handleFileUpload(e.target.files[0]);
            }
        };
    }
    
    elements.btnSend.onclick = handleSendMessage;
    elements.btnStopGen.onclick = stopGeneration;
    
    elements.userInput.onkeydown = (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSendMessage();
        }
    };
    
    elements.userInput.oninput = (e) => {
        e.target.style.height = "auto";
        e.target.style.height = `${Math.min(e.target.scrollHeight, 180)}px`;
        elements.charCount.textContent = `${e.target.value.length} chars`;
    };
    
    elements.btnOpenSettings.onclick = openSettingsModal;
    elements.btnCloseSettings.onclick = closeSettingsModal;
    elements.btnSaveSettings.onclick = saveSettings;
    
    elements.btnToggleKeyVis.onclick = () => {
        const type = elements.settingApiKey.type === "password" ? "text" : "password";
        elements.settingApiKey.type = type;
        elements.btnToggleKeyVis.querySelector("i").setAttribute("data-lucide", type === "password" ? "eye" : "eye-off");
        initLucideIcons();
    };
    
    elements.settingTemperature.oninput = (e) => {
        elements.tempValDisplay.textContent = e.target.value;
    };
    

    
    elements.headerModelSelect.onchange = async (e) => {
        const selectedModel = e.target.value;
        if (state.activeThreadId) {
            await updateThreadOnServer(state.activeThreadId, { model: selectedModel });
        }
    };
    
    document.querySelectorAll(".prompt-card").forEach(card => {
        card.onclick = () => {
            const promptText = card.dataset.prompt;
            elements.userInput.value = promptText;
            handleSendMessage();
        };
    });
    
    elements.btnToggleSidebar.onclick = () => {
        elements.sidebar.classList.toggle("open");
    };
    
    elements.btnExportChat.onclick = exportCurrentChat;
}

function openSettingsModal() {
    elements.settingsModal.classList.remove("hidden");
}
function closeSettingsModal() {
    elements.settingsModal.classList.add("hidden");
}

function saveSettings() {
    state.config.apiKey = elements.settingApiKey.value.trim();
    state.config.baseUrl = elements.settingBaseUrl.value.trim();
    state.config.defaultModel = elements.settingDefaultModel.value.trim();
    state.config.temperature = parseFloat(elements.settingTemperature.value);
    
    if (elements.settingGroundedAgentId) {
        state.config.groundedAgentId = elements.settingGroundedAgentId.value.trim();
        localStorage.setItem("grounded_agent_id", state.config.groundedAgentId);
    }
    if (elements.settingGroundedApiKey) {
        state.config.groundedApiKey = elements.settingGroundedApiKey.value.trim();
        localStorage.setItem("grounded_api_key", state.config.groundedApiKey);
    }
    
    localStorage.setItem("scx_api_key", state.config.apiKey);
    localStorage.setItem("scx_base_url", state.config.baseUrl);
    localStorage.setItem("scx_default_model", state.config.defaultModel);
    localStorage.setItem("scx_temperature", state.config.temperature);
    
    updateAPIStatusBadge();
    closeSettingsModal();
}

function exportCurrentChat() {
    if (!state.activeThread || !state.activeThread.messages) return;
    
    let mdContent = `# ${state.activeThread.title}\n`;
    mdContent += `*Date: ${new Date(state.activeThread.created_at).toLocaleString()}*\n`;
    mdContent += `*Model: ${state.activeThread.model}*\n\n---\n\n`;
    
    state.activeThread.messages.forEach(msg => {
        const sender = msg.role === "user" ? "**User**" : "**SCX.AI Assistant**";
        mdContent += `${sender}:\n${msg.content}\n\n`;
        if (msg.grounded_result) {
            mdContent += `*Grounded AI Score: ${msg.grounded_result.score || 'N/A'}% | Risk: ${msg.grounded_result.risk || 'N/A'}*\n\n`;
        }
    });
    
    const blob = new Blob([mdContent], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.activeThread.title.replace(/[^a-z0-9]/gi, '_').toLowerCase()}_chat.md`;
    a.click();
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
