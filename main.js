/* InterviewAI Global Javascript & AI Widget Controller */

document.addEventListener('DOMContentLoaded', () => {
    // Theme Management
    initTheme();

    // Init Floating AI Widget
    initAIWidget();

    // Auto-dismiss Alerts
    initAlerts();
});

// Theme Logic
function initTheme() {
    const themeToggle = document.getElementById('theme-toggle');
    if (!themeToggle) return;

    const currentTheme = localStorage.getItem('theme') || 'dark';
    if (currentTheme === 'light') {
        document.body.classList.add('light-theme');
        themeToggle.innerHTML = '<i class="fas fa-moon"></i>';
    } else {
        document.body.classList.remove('light-theme');
        themeToggle.innerHTML = '<i class="fas fa-sun"></i>';
    }

    themeToggle.addEventListener('click', () => {
        document.body.classList.toggle('light-theme');
        let theme = 'dark';
        if (document.body.classList.contains('light-theme')) {
            theme = 'light';
            themeToggle.innerHTML = '<i class="fas fa-moon"></i>';
        } else {
            themeToggle.innerHTML = '<i class="fas fa-sun"></i>';
        }
        localStorage.setItem('theme', theme);
    });
}

// Toast Notifications Helper
function showToast(message, type = 'success') {
    let toastContainer = document.querySelector('.toast-container');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container';
        document.body.appendChild(toastContainer);
    }

    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type === 'success' ? 'success' : type === 'error' ? 'danger' : 'warning'} border-0 show fade-in`;
    toast.role = 'alert';
    toast.ariaLive = 'assertive';
    toast.ariaAtomic = 'true';

    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                <i class="fas ${type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-exclamation-circle' : 'fa-info-circle'} me-2"></i>
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;

    toastContainer.appendChild(toast);

    // Auto-remove toast after 4s
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 4000);

    toast.querySelector('.btn-close').addEventListener('click', () => {
        toast.remove();
    });
}

// Floating AI Assistant Widget
function initAIWidget() {
    const widgetBtn = document.getElementById('ai-widget-toggle');
    const drawer = document.getElementById('ai-widget-drawer');
    const input = document.getElementById('ai-widget-input');
    const sendBtn = document.getElementById('ai-widget-send');
    const messagesContainer = document.getElementById('ai-widget-messages');

    if (!widgetBtn || !drawer) return;

    // Toggle Drawer
    widgetBtn.addEventListener('click', () => {
        widgetBtn.classList.toggle('active');
        drawer.classList.toggle('show');
        
        // Scroll to bottom on show
        if (drawer.classList.contains('show')) {
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
            input.focus();
        }
    });

    // Send Message Event
    const sendMessage = async () => {
        const text = input.value.trim();
        if (!text) return;

        // Render outgoing message
        appendWidgetMessage(text, 'outgoing');
        input.value = '';

        // Render typing loading indicator
        const loadingId = appendWidgetMessage('<span class="spinner-grow spinner-grow-sm" role="status"></span> Analyzing...', 'incoming');

        try {
            const response = await fetch('/api/career_coach/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, system_role: 'helper' })
            });
            const data = await response.json();
            
            // Remove loading indicator
            const loadingMsg = document.getElementById(loadingId);
            if (loadingMsg) loadingMsg.remove();

            if (data.success) {
                appendWidgetMessage(data.reply, 'incoming');
            } else {
                appendWidgetMessage('Sorry, I encountered an issue. Please try again.', 'incoming');
            }
        } catch (error) {
            console.error('Error sending AI widget query:', error);
            const loadingMsg = document.getElementById(loadingId);
            if (loadingMsg) loadingMsg.remove();
            appendWidgetMessage('Network error. Failed to connect to InterviewAI assistant.', 'incoming');
        }
    };

    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });
}

function appendWidgetMessage(text, direction) {
    const container = document.getElementById('ai-widget-messages');
    if (!container) return null;

    const msgId = 'widget-msg-' + Date.now();
    const msg = document.createElement('div');
    msg.id = msgId;
    msg.className = `ai-message ${direction} fade-in`;
    
    // Simple markdown mapping for widget (lists and formatting)
    if (direction === 'incoming') {
        msg.innerHTML = formatAIResponse(text);
    } else {
        msg.textContent = text;
    }

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;
    return msgId;
}

// Simple Helper to Parse Markdown features like bullets, bold, and linebreaks
function formatAIResponse(text) {
    if (!text) return '';
    let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        // Bold tags
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        // Inline code
        .replace(/`(.*?)`/g, '<code>$1</code>')
        // Linebreaks
        .replace(/\n/g, '<br>');
    
    return html;
}

// Dismiss notifications
function initAlerts() {
    const alerts = document.querySelectorAll('.alert-dismissible');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
}
