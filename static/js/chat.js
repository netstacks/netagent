/**
 * NetAgent Chat Interface
 * Real-time streaming chat with AI agents
 */

class AgentChat {
    constructor(options = {}) {
        this.agentId = options.agentId;
        this.sessionId = options.sessionId || null;
        this.onMessage = options.onMessage || (() => {});
        this.onToolCall = options.onToolCall || (() => {});
        this.onError = options.onError || (() => {});
        this.onStatusChange = options.onStatusChange || (() => {});

        this.isStreaming = false;
        this.abortController = null;
    }

    async createSession() {
        try {
            const response = await api.post('/api/chat/sessions', {
                agent_id: this.agentId
            });
            this.sessionId = response.id;
            return this.sessionId;
        } catch (error) {
            this.onError('Failed to create session');
            throw error;
        }
    }

    async sendMessage(content) {
        if (!this.sessionId) {
            await this.createSession();
        }

        if (this.isStreaming) {
            return;
        }

        this.isStreaming = true;
        this.onStatusChange('sending');
        this.abortController = new AbortController();

        try {
            const response = await fetch(`/api/chat/sessions/${this.sessionId}/message`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ content }),
                signal: this.abortController.signal
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            await this.processStream(response);
        } catch (error) {
            if (error.name === 'AbortError') {
                this.onStatusChange('stopped');
            } else {
                this.onError(error.message);
                this.onStatusChange('error');
            }
        } finally {
            this.isStreaming = false;
            this.abortController = null;
        }
    }

    async processStream(response) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        try {
            while (true) {
                const { done, value } = await reader.read();

                if (done) {
                    this.onStatusChange('ready');
                    break;
                }

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            this.handleStreamEvent(data);
                        } catch (e) {
                            console.error('Failed to parse SSE data:', e);
                        }
                    }
                }
            }
        } catch (error) {
            throw error;
        }
    }

    handleStreamEvent(data) {
        switch (data.type) {
            case 'thinking':
                this.onStatusChange('thinking');
                this.onMessage({
                    type: 'thinking',
                    content: data.content
                });
                break;

            case 'tool_call':
                this.onStatusChange('using_tool');
                this.onToolCall({
                    id: data.tool_id,
                    name: data.tool_name,
                    arguments: data.arguments,
                    status: 'running'
                });
                break;

            case 'tool_result':
                this.onToolCall({
                    id: data.tool_id,
                    result: data.result,
                    status: 'complete'
                });
                break;

            case 'content':
                this.onStatusChange('responding');
                this.onMessage({
                    type: 'content',
                    content: data.content,
                    delta: true
                });
                break;

            case 'done':
                this.onStatusChange('ready');
                this.onMessage({
                    type: 'done',
                    usage: data.usage
                });
                break;

            case 'error':
                this.onError(data.error);
                this.onStatusChange('error');
                break;

            case 'approval_required':
                this.onStatusChange('waiting_approval');
                this.onMessage({
                    type: 'approval_required',
                    approval_id: data.approval_id,
                    action: data.action,
                    description: data.description
                });
                break;
        }
    }

    stop() {
        if (this.abortController) {
            this.abortController.abort();
        }
    }

    async loadHistory() {
        if (!this.sessionId) {
            return [];
        }

        try {
            const messages = await api.get(`/api/chat/sessions/${this.sessionId}/messages`);
            return messages || [];
        } catch (error) {
            console.error('Failed to load history:', error);
            return [];
        }
    }

    async getActions() {
        if (!this.sessionId) {
            return [];
        }

        try {
            const actions = await api.get(`/api/chat/sessions/${this.sessionId}/actions`);
            return actions || [];
        } catch (error) {
            console.error('Failed to load actions:', error);
            return [];
        }
    }
}

/**
 * Markdown-like text formatter
 * Simple implementation for chat messages
 */
class MessageFormatter {
    static format(text) {
        if (!text) return '';

        // Escape HTML first
        text = this.escapeHtml(text);

        // Code blocks
        text = text.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
            return `<pre class="code-block"><code class="language-${lang}">${code.trim()}</code></pre>`;
        });

        // Inline code
        text = text.replace(/`([^`]+)`/g, '<code>$1</code>');

        // Bold
        text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

        // Italic
        text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');

        // Links
        text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');

        // Line breaks
        text = text.replace(/\n/g, '<br>');

        return text;
    }

    static escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

/**
 * Chat UI Controller
 * Manages the chat interface elements
 */
class ChatUI {
    constructor(options = {}) {
        this.messagesContainer = options.messagesContainer || document.getElementById('chat-messages');
        this.inputElement = options.inputElement || document.getElementById('message-input');
        this.sendButton = options.sendButton || document.getElementById('btn-send');
        this.stopButton = options.stopButton || document.getElementById('btn-stop');
        this.statusBadge = options.statusBadge || document.getElementById('status-badge');

        this.currentMessageElement = null;
        this.currentThinkingElement = null;
    }

    appendUserMessage(content) {
        const div = document.createElement('div');
        div.className = 'chat-message user-message';
        div.innerHTML = `
            <div class="message-avatar">
                <i class="bi bi-person"></i>
            </div>
            <div class="message-body">
                <div class="message-content">${MessageFormatter.escapeHtml(content)}</div>
            </div>
        `;
        this.messagesContainer.appendChild(div);
        this.scrollToBottom();
    }

    startAssistantMessage() {
        const div = document.createElement('div');
        div.className = 'chat-message assistant-message';
        div.innerHTML = `
            <div class="message-avatar">
                <i class="bi bi-robot"></i>
            </div>
            <div class="message-body">
                <div class="thinking-section" style="display: none;">
                    <div class="thinking-header" onclick="this.parentElement.classList.toggle('collapsed')">
                        <i class="bi bi-chevron-down"></i>
                        <span>Thinking...</span>
                    </div>
                    <div class="thinking-content"></div>
                    <div class="tool-calls"></div>
                </div>
                <div class="message-content">
                    <span class="typing-indicator"></span>
                </div>
            </div>
        `;
        this.messagesContainer.appendChild(div);
        this.currentMessageElement = div;
        this.currentThinkingElement = div.querySelector('.thinking-section');
        this.scrollToBottom();
        return div;
    }

    updateThinking(content) {
        if (!this.currentThinkingElement) return;

        this.currentThinkingElement.style.display = 'block';
        const contentEl = this.currentThinkingElement.querySelector('.thinking-content');
        contentEl.textContent = content;
        this.scrollToBottom();
    }

    addToolCall(toolCall) {
        if (!this.currentThinkingElement) return;

        this.currentThinkingElement.style.display = 'block';
        const container = this.currentThinkingElement.querySelector('.tool-calls');

        const div = document.createElement('div');
        div.className = 'tool-call';
        div.id = `tool-${toolCall.id}`;
        div.innerHTML = `
            <div class="tool-header">
                <i class="bi bi-gear-fill text-info me-2"></i>
                <strong>${MessageFormatter.escapeHtml(toolCall.name)}</strong>
                <span class="badge bg-warning ms-2">Running</span>
            </div>
            <div class="tool-input">
                <pre><code>${JSON.stringify(toolCall.arguments, null, 2)}</code></pre>
            </div>
            <div class="tool-output" style="display: none;"></div>
        `;
        container.appendChild(div);
        this.scrollToBottom();
    }

    updateToolResult(toolId, result) {
        const toolDiv = document.getElementById(`tool-${toolId}`);
        if (!toolDiv) return;

        const badge = toolDiv.querySelector('.badge');
        badge.className = 'badge bg-success ms-2';
        badge.textContent = 'Complete';

        const outputDiv = toolDiv.querySelector('.tool-output');
        outputDiv.style.display = 'block';
        outputDiv.innerHTML = `<pre><code>${MessageFormatter.escapeHtml(JSON.stringify(result, null, 2))}</code></pre>`;

        this.scrollToBottom();
    }

    appendContent(content, accumulated) {
        if (!this.currentMessageElement) return;

        const contentEl = this.currentMessageElement.querySelector('.message-content');
        contentEl.innerHTML = MessageFormatter.format(accumulated);
        this.scrollToBottom();
    }

    finishMessage() {
        if (this.currentThinkingElement) {
            this.currentThinkingElement.classList.add('collapsed');
            const header = this.currentThinkingElement.querySelector('.thinking-header span');
            if (header) header.textContent = 'Show reasoning';
        }

        this.currentMessageElement = null;
        this.currentThinkingElement = null;
    }

    updateStatus(status) {
        if (!this.statusBadge) return;

        const statusConfig = {
            ready: { class: 'bg-success', text: 'Ready', icon: '' },
            sending: { class: 'bg-info', text: 'Sending', icon: 'spinner-border spinner-border-sm me-1' },
            thinking: { class: 'bg-warning', text: 'Thinking', icon: 'spinner-border spinner-border-sm me-1' },
            using_tool: { class: 'bg-info', text: 'Using Tool', icon: 'spinner-border spinner-border-sm me-1' },
            responding: { class: 'bg-info', text: 'Responding', icon: 'spinner-border spinner-border-sm me-1' },
            stopped: { class: 'bg-secondary', text: 'Stopped', icon: '' },
            error: { class: 'bg-danger', text: 'Error', icon: '' },
            waiting_approval: { class: 'bg-warning', text: 'Waiting Approval', icon: '' }
        };

        const config = statusConfig[status] || statusConfig.ready;
        this.statusBadge.className = `badge ${config.class}`;
        this.statusBadge.innerHTML = config.icon ? `<span class="${config.icon}"></span>${config.text}` : config.text;
    }

    setInputEnabled(enabled) {
        if (this.inputElement) {
            this.inputElement.disabled = !enabled;
        }
        if (this.sendButton) {
            this.sendButton.disabled = !enabled;
        }
        if (this.stopButton) {
            this.stopButton.disabled = enabled;
        }
    }

    clearInput() {
        if (this.inputElement) {
            this.inputElement.value = '';
            this.inputElement.style.height = 'auto';
        }
    }

    scrollToBottom() {
        this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
    }

    showApprovalRequest(data) {
        if (!this.currentMessageElement) return;

        const contentEl = this.currentMessageElement.querySelector('.message-content');
        contentEl.innerHTML = `
            <div class="approval-request">
                <div class="d-flex align-items-center mb-2">
                    <i class="bi bi-exclamation-triangle text-warning me-2"></i>
                    <strong>Approval Required</strong>
                </div>
                <p>${MessageFormatter.escapeHtml(data.description)}</p>
                <div class="d-flex gap-2">
                    <a href="/approvals" class="btn btn-primary btn-sm">View in Approvals</a>
                </div>
            </div>
        `;
        this.scrollToBottom();
    }
}

// Export for use in templates
window.AgentChat = AgentChat;
window.ChatUI = ChatUI;
window.MessageFormatter = MessageFormatter;
