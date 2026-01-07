/**
 * NetAgent Common Utilities
 */

// Toast notifications
const toast = {
    container: null,

    init() {
        if (!this.container) {
            this.container = document.createElement('div');
            this.container.className = 'toast-container position-fixed bottom-0 end-0 p-3';
            this.container.style.zIndex = '1100';
            document.body.appendChild(this.container);
        }
    },

    show(message, type = 'info', duration = 5000) {
        this.init();

        const id = 'toast-' + Date.now();
        const bgClass = {
            'success': 'bg-success',
            'error': 'bg-danger',
            'warning': 'bg-warning',
            'info': 'bg-info'
        }[type] || 'bg-info';

        const icon = {
            'success': 'fa-check-circle',
            'error': 'fa-exclamation-circle',
            'warning': 'fa-exclamation-triangle',
            'info': 'fa-info-circle'
        }[type] || 'fa-info-circle';

        const toastEl = document.createElement('div');
        toastEl.id = id;
        toastEl.className = `toast align-items-center text-white ${bgClass} border-0`;
        toastEl.setAttribute('role', 'alert');
        toastEl.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">
                    <i class="fas ${icon} me-2"></i>
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;

        this.container.appendChild(toastEl);

        const bsToast = new bootstrap.Toast(toastEl, { delay: duration });
        bsToast.show();

        toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
    },

    success(message) { this.show(message, 'success'); },
    error(message) { this.show(message, 'error'); },
    warning(message) { this.show(message, 'warning'); },
    info(message) { this.show(message, 'info'); }
};

// Confirm dialog
async function confirmDialog(title, message, confirmText = 'Confirm', cancelText = 'Cancel') {
    return new Promise((resolve) => {
        const modalId = 'confirm-modal-' + Date.now();
        const modal = document.createElement('div');
        modal.innerHTML = `
            <div class="modal fade" id="${modalId}" tabindex="-1">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">${title}</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <p>${message}</p>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${cancelText}</button>
                            <button type="button" class="btn btn-primary" id="${modalId}-confirm">${confirmText}</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(modal);

        const modalEl = document.getElementById(modalId);
        const bsModal = new bootstrap.Modal(modalEl);

        document.getElementById(`${modalId}-confirm`).addEventListener('click', () => {
            bsModal.hide();
            resolve(true);
        });

        modalEl.addEventListener('hidden.bs.modal', () => {
            modal.remove();
            resolve(false);
        });

        bsModal.show();
    });
}

// Format relative time
function formatRelativeTime(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;

    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (seconds < 60) return 'just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    if (days < 7) return `${days}d ago`;

    return date.toLocaleDateString();
}

// Format date/time
function formatDateTime(timestamp) {
    const date = new Date(timestamp);
    return date.toLocaleString();
}

// Debounce function
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Copy to clipboard
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        toast.success('Copied to clipboard');
    } catch (err) {
        toast.error('Failed to copy');
    }
}

// Loading button state
function setButtonLoading(button, loading) {
    if (loading) {
        button.dataset.originalText = button.innerHTML;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Loading...';
        button.disabled = true;
    } else {
        button.innerHTML = button.dataset.originalText || button.innerHTML;
        button.disabled = false;
    }
}

// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Parse markdown (basic)
function parseMarkdown(text) {
    if (!text) return '';

    return text
        // Code blocks
        .replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>')
        // Inline code
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        // Bold
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        // Italic
        .replace(/\*([^*]+)\*/g, '<em>$1</em>')
        // Links
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
        // Line breaks
        .replace(/\n/g, '<br>');
}

// Generate UUID
function generateUUID() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        const r = Math.random() * 16 | 0;
        const v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// Status badge helper
function statusBadge(status) {
    const styles = {
        'active': 'bg-success',
        'running': 'bg-primary',
        'pending': 'bg-warning',
        'completed': 'bg-success',
        'failed': 'bg-danger',
        'error': 'bg-danger',
        'waiting_approval': 'bg-warning',
        'approved': 'bg-success',
        'rejected': 'bg-danger',
        'healthy': 'bg-success',
        'unhealthy': 'bg-danger',
        'unknown': 'bg-secondary'
    };

    const badgeClass = styles[status] || 'bg-secondary';
    return `<span class="badge ${badgeClass}">${status}</span>`;
}

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        toast,
        confirmDialog,
        formatRelativeTime,
        formatDateTime,
        debounce,
        copyToClipboard,
        setButtonLoading,
        escapeHtml,
        parseMarkdown,
        generateUUID,
        statusBadge
    };
}
