/* =============================================
   BiliSummary — App Logic
   ============================================= */

// ---------------------------------------------------------------------------
// Theme: Dark / Light
// ---------------------------------------------------------------------------
function initTheme() {
    const saved = localStorage.getItem('bilisummary-theme') || 'dark';
    applyTheme(saved);
}

function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('themeToggle');
    if (btn) {
        btn.innerHTML = `<i data-lucide="${theme === 'dark' ? 'moon' : 'sun'}" class="lucide-icon"></i>`;
        if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [btn] });
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    localStorage.setItem('bilisummary-theme', next);
    applyTheme(next);
}

initTheme();

// Cache for summaries data
let summariesData = null;

// ---------------------------------------------------------------------------
// Navigation — static pages
// ---------------------------------------------------------------------------
document.querySelectorAll('.nav-item[data-page]').forEach(item => {
    item.addEventListener('click', () => {
        switchToPage(item.dataset.page, item);
    });
});

function switchToPage(pageId, navEl) {
    // Clear all active states
    document.querySelectorAll('.nav-item, .nav-child').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.fav-folder-item').forEach(n => n.classList.remove('active'));
    // Set active on clicked element
    if (navEl) navEl.classList.add('active');
    // Show page
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(pageId).classList.add('active');
}

// ---------------------------------------------------------------------------
// Status Check
// ---------------------------------------------------------------------------
async function checkStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();
        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        const loginBtn = document.getElementById('loginBtn');
        const logoutBtn = document.getElementById('logoutBtn');
        if (data.logged_in) {
            dot.className = 'status-dot online';
            text.textContent = 'Bilibili 已登录';
            loginBtn.style.display = 'none';
            logoutBtn.style.display = 'flex';
        } else {
            dot.className = 'status-dot offline';
            text.textContent = '未登录 Bilibili';
            loginBtn.style.display = 'flex';
            logoutBtn.style.display = 'none';
        }
    } catch {
        document.getElementById('statusDot').className = 'status-dot offline';
        document.getElementById('statusText').textContent = '连接失败';
    }
}
checkStatus();
loadFavoriteFolders();

// ---------------------------------------------------------------------------
// QR Login / Logout
// ---------------------------------------------------------------------------
let loginEventSource = null;

function startLogin() {
    const modal = document.getElementById('loginModal');
    const qrContainer = document.getElementById('qrContainer');
    const qrStatus = document.getElementById('qrStatus');

    modal.classList.add('active');
    qrContainer.innerHTML = '<div class="qr-loading"><span class="spinner"></span> 生成二维码中...</div>';
    qrStatus.textContent = '请使用 Bilibili App 扫描二维码';
    qrStatus.className = 'qr-status';

    // Close any existing connection
    if (loginEventSource) loginEventSource.close();

    loginEventSource = new EventSource('/api/login/qr');

    loginEventSource.addEventListener('qrcode', (e) => {
        const d = JSON.parse(e.data);
        qrContainer.innerHTML = `<img src="data:image/png;base64,${d.image}" alt="QR Code">`;
    });

    loginEventSource.addEventListener('scanned', (e) => {
        const d = JSON.parse(e.data);
        qrStatus.textContent = '📲 ' + d.message;
        qrStatus.className = 'qr-status scanned';
    });

    loginEventSource.addEventListener('done', (e) => {
        const d = JSON.parse(e.data);
        qrStatus.textContent = '✓ ' + d.message;
        qrStatus.className = 'qr-status success';
        loginEventSource.close();
        loginEventSource = null;
        // Refresh status and close modal after a beat
        setTimeout(() => {
            checkStatus();
            modal.classList.remove('active');
        }, 1200);
    });

    loginEventSource.addEventListener('timeout', (e) => {
        const d = JSON.parse(e.data);
        qrStatus.textContent = '⏰ ' + d.message;
        qrStatus.className = 'qr-status error';
        loginEventSource.close();
        loginEventSource = null;
    });

    loginEventSource.addEventListener('error', (e) => {
        try {
            const d = JSON.parse(e.data);
            qrStatus.textContent = '✗ ' + d.message;
        } catch {
            qrStatus.textContent = '✗ 连接失败';
        }
        qrStatus.className = 'qr-status error';
        if (loginEventSource) { loginEventSource.close(); loginEventSource = null; }
    });

    loginEventSource.onerror = () => {
        // SSE connection error (not our custom error event)
        if (loginEventSource) { loginEventSource.close(); loginEventSource = null; }
    };
}

function closeLoginModal() {
    document.getElementById('loginModal').classList.remove('active');
    if (loginEventSource) { loginEventSource.close(); loginEventSource = null; }
}

async function doLogout() {
    if (!confirm('确定要退出登录吗？')) return;
    try {
        await fetch('/api/logout', { method: 'POST' });
        checkStatus();
    } catch (err) {
        alert('注销失败: ' + err.message);
    }
}

// ---------------------------------------------------------------------------
// Sidebar: Load browse categories
// ---------------------------------------------------------------------------
async function loadSidebarBrowse() {
    const container = document.getElementById('sidebarBrowse');
    try {
        const res = await fetch('/api/summaries');
        summariesData = await res.json();

        if (!summariesData.categories || summariesData.categories.length === 0) {
            container.innerHTML = '<div class="nav-item" style="color:var(--text-muted);cursor:default;font-size:12px;">暂无总结</div>';
            return;
        }

        let html = '';
        for (const cat of summariesData.categories) {
            if (cat.type === 'users') {
                // UP 主: expandable parent → children are individual users
                html += `
                    <div class="nav-parent" onclick="toggleParent(this)">
                        <span class="icon">${cat.icon}</span>
                        <span class="label">${cat.label}</span>
                        <span class="count">${cat.count}</span>
                        <span class="chevron">▶</span>
                    </div>
                    <div class="nav-children">`;
                for (const group of cat.groups) {
                    html += `
                        <div class="nav-child" onclick="showUserVideos('${group.uid}', this)" data-uid="${group.uid}">
                            <span class="child-label">${escapeHtml(group.display_name)}</span>
                            <span class="child-count">${group.count}</span>
                        </div>`;
                }
                html += `</div>`;
            } else {
                // Standalone / Favorites: expandable parent, clicking shows items
                html += `
                    <div class="nav-parent" onclick="toggleParent(this); showCategory('${cat.type}', this)" data-type="${cat.type}">
                        <span class="icon">${cat.icon}</span>
                        <span class="label">${cat.label}</span>
                        <span class="count">${cat.count}</span>
                        <span class="chevron">▶</span>
                    </div>
                    <div class="nav-children"></div>`;
            }
        }
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = `<div class="nav-item" style="color:var(--error);cursor:default;font-size:12px;">加载失败</div>`;
    }
}
loadSidebarBrowse();

function toggleParent(el) {
    el.classList.toggle('expanded');
}

// ---------------------------------------------------------------------------
// Browse: Show category items (standalone / favorites)
// ---------------------------------------------------------------------------
function showCategory(type, navEl) {
    if (!summariesData) return;
    const cat = summariesData.categories.find(c => c.type === type);
    if (!cat) return;

    // Update active state
    document.querySelectorAll('.nav-item, .nav-child').forEach(n => n.classList.remove('active'));

    // Switch to browse page
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('browse-page').classList.add('active');

    // Update header
    document.getElementById('browseTitle').textContent = `${cat.icon} ${cat.label}`;
    document.getElementById('browseSubtitle').textContent = `共 ${cat.items.length} 篇总结`;

    // Render item list
    const readingView = document.getElementById('readingView');
    readingView.classList.remove('active');
    const list = document.getElementById('browseList');
    list.style.display = 'block';
    list.innerHTML = cat.items.map(item => `
        <div class="summary-item" onclick="openSummary('${encodePath(item.path)}')">
            <span class="icon"><i data-lucide="${item.no_subtitle ? 'alert-triangle' : 'file-text'}" class="lucide-icon"></i></span>
            <span class="title">${escapeHtml(item.name)}</span>
        </div>
    `).join('');
    lucide.createIcons({ nodes: [list] });
}

// ---------------------------------------------------------------------------
// Browse: Show videos for a specific UP主
// ---------------------------------------------------------------------------
function showUserVideos(uid, navEl) {
    if (!summariesData) return;
    const usersCat = summariesData.categories.find(c => c.type === 'users');
    if (!usersCat) return;
    const group = usersCat.groups.find(g => g.uid === uid);
    if (!group) return;

    // Update active state
    document.querySelectorAll('.nav-item, .nav-child').forEach(n => n.classList.remove('active'));
    if (navEl) navEl.classList.add('active');

    // Switch to browse page
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('browse-page').classList.add('active');

    // Update header
    document.getElementById('browseTitle').innerHTML = `<i data-lucide="user" class="lucide-icon"></i> ${escapeHtml(group.display_name)}`;
    lucide.createIcons({ nodes: [document.getElementById('browseTitle')] });
    document.getElementById('browseSubtitle').textContent = `UID: ${group.uid} · ${group.count} 篇总结`;

    // Render item list
    const readingView = document.getElementById('readingView');
    readingView.classList.remove('active');
    const list = document.getElementById('browseList');
    list.style.display = 'block';
    list.innerHTML = group.items.map(item => `
        <div class="summary-item" onclick="openSummary('${encodePath(item.path)}')">
            <span class="icon"><i data-lucide="${item.no_subtitle ? 'alert-triangle' : 'file-text'}" class="lucide-icon"></i></span>
            <span class="title">${escapeHtml(item.name)}</span>
        </div>
    `).join('');
    lucide.createIcons({ nodes: [list] });
}

// ---------------------------------------------------------------------------
// Reading View
// ---------------------------------------------------------------------------
async function openSummary(encodedPath) {
    const apiPath = encodedPath; // already encoded per segment
    const list = document.getElementById('browseList');
    const readingView = document.getElementById('readingView');
    const readingContent = document.getElementById('readingContent');

    try {
        const res = await fetch(`/api/summary/${apiPath}`);
        const data = await res.json();
        if (data.error) { alert(data.error); return; }
        list.style.display = 'none';
        readingView.classList.add('active');
        readingContent.innerHTML = renderMarkdown(data.content);
    } catch (err) { alert('加载失败: ' + err.message); }
}

function closeReading() {
    document.getElementById('readingView').classList.remove('active');
    document.getElementById('browseList').style.display = 'block';
}

// ---------------------------------------------------------------------------
// Markdown → HTML
// ---------------------------------------------------------------------------
function renderMarkdown(md) {
    return md
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/^---$/gm, '<hr>')
        // Markdown links: [text](url)
        .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" class="ext-link" target="_blank">$1</a>')
        // Bare URLs (not already inside an href)
        .replace(/(?<!href=")(https?:\/\/[^\s<"]+)/g, '<a href="$1" class="ext-link" target="_blank">$1</a>')
        .replace(/^[-*] (.+)$/gm, '<li>$1</li>')
        .replace(/^\d+\.\s+(.+)$/gm, '<li>$1</li>')
        .replace(/^(?!<[hlu]|<li|<hr|<a)(.+)$/gm, '<p>$1</p>')
        .replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');
}

// ---------------------------------------------------------------------------
// External link handler — open in system browser
// ---------------------------------------------------------------------------
function openExternal(url) {
    if (window.pywebview && window.pywebview.api) {
        window.pywebview.api.open_url(url);
    } else {
        window.open(url, '_blank');
    }
}

document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href]');
    if (!link) return;
    const href = link.getAttribute('href');
    if (href && href.startsWith('http')) {
        e.preventDefault();
        e.stopPropagation();
        openExternal(href);
    }
});

// ---------------------------------------------------------------------------
// SSE Progress (auto-reconnect via fetch + ReadableStream)
// ---------------------------------------------------------------------------
function listenProgress(taskId, prefix) {
    const progressArea = document.getElementById(`${prefix}Progress`);
    const progressBar = document.getElementById(`${prefix}ProgressBar`);
    const statsEl = document.getElementById(`${prefix}Stats`);
    const logEl = document.getElementById(`${prefix}Log`);
    const submitBtn = document.getElementById(`${prefix}Submit`);
    const resultsArea = document.getElementById(`${prefix}Results`);

    progressArea.classList.add('active');
    logEl.innerHTML = '';
    resultsArea.innerHTML = '';
    progressBar.style.width = '0%';
    statsEl.innerHTML = '';
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner"></span> 处理中...';

    let total = 0, completed = 0;
    const completedPaths = [];
    let lastEventId = -1;
    let isDone = false;
    let retryCount = 0;
    const MAX_RETRIES = 10;

    function handleEvent(eventType, data) {
        let d;
        try { d = JSON.parse(data); } catch { return; }

        switch (eventType) {
            case 'start':
                total = d.total;
                addLog(logEl, `▶ 开始处理 ${d.total} 个视频 (并发: ${d.concurrency}, 模型: ${d.model})`, 'info');
                break;
            case 'info':
                addLog(logEl, `ℹ ${d.message}`, 'info');
                break;
            case 'processing':
                addLog(logEl, `◌ ${d.title} — ${d.step}`, '');
                break;
            case 'skip':
                completed++;
                updateProgress(progressBar, statsEl, completed, total);
                addLog(logEl, `→ 已存在，跳过: ${d.title}`, 'skip');
                if (d.path) completedPaths.push({ title: d.title, path: d.path, status: 'skipped' });
                break;
            case 'completed':
                completed++;
                updateProgress(progressBar, statsEl, completed, total);
                if (d.status === 'no_subtitle') {
                    addLog(logEl, `△ 无字幕: ${d.title}`, 'warning');
                } else {
                    addLog(logEl, `✓ ${d.title} (${d.duration_sec}s)`, 'success');
                }
                if (d.path) completedPaths.push({ title: d.title, path: d.path, status: d.status, duration: d.duration_sec });
                break;
            case 'error':
                completed++;
                updateProgress(progressBar, statsEl, completed, total);
                addLog(logEl, `✗ ${d.title || ''}: ${d.message}`, 'error');
                break;
            case 'done':
                isDone = true;
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i data-lucide="play" class="lucide-icon" style="width:14px;height:14px;"></i> 开始总结';
                lucide.createIcons({ nodes: [submitBtn] });
                addLog(logEl, `✨ 完成! 成功: ${d.success} | 跳过: ${d.skipped} | 无字幕: ${d.no_subtitle} | 失败: ${d.errors}`, 'info');
                progressBar.style.width = '100%';
                showInlineResults(resultsArea, completedPaths);
                loadSidebarBrowse();
                break;
        }
    }

    async function connectSSE() {
        if (isDone) return;

        try {
            const resp = await fetch(`/api/progress/${taskId}`, {
                headers: { 'Last-Event-ID': String(lastEventId) }
            });

            if (!resp.ok || !resp.body) {
                throw new Error(`HTTP ${resp.status}`);
            }

            retryCount = 0; // Reset on successful connect
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const blocks = buffer.split('\n\n');
                buffer = blocks.pop(); // Keep incomplete block

                for (const block of blocks) {
                    if (!block.trim() || block.trim().startsWith(':')) continue; // Skip heartbeats

                    let eventType = 'message';
                    let eventData = '';
                    let eventId = null;

                    for (const line of block.split('\n')) {
                        if (line.startsWith('event: ')) eventType = line.slice(7);
                        else if (line.startsWith('data: ')) eventData = line.slice(6);
                        else if (line.startsWith('id: ')) eventId = parseInt(line.slice(4));
                    }

                    if (eventId !== null) lastEventId = eventId;
                    if (eventData) handleEvent(eventType, eventData);
                    if (isDone) return;
                }
            }
        } catch (err) {
            // Connection error — ignore if already done
        }

        // Auto-reconnect if not done
        if (!isDone && retryCount < MAX_RETRIES) {
            retryCount++;
            addLog(logEl, `↻ 重连中... (${retryCount}/${MAX_RETRIES})`, 'warning');
            await new Promise(r => setTimeout(r, 2000));
            return connectSSE();
        }

        if (!isDone) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i data-lucide="play" class="lucide-icon" style="width:14px;height:14px;"></i> 开始总结';
            lucide.createIcons({ nodes: [submitBtn] });
            addLog(logEl, '✗ 连接中断，可重新点击开始总结', 'error');
        }
    }

    connectSSE();
}

// ---------------------------------------------------------------------------
// Inline Results
// ---------------------------------------------------------------------------
async function showInlineResults(container, results) {
    if (!results.length) return;

    container.innerHTML = `<div class="card"><div class="card-title"><i data-lucide="file-text" class="lucide-icon" style="width:15px;height:15px;"></i> 生成的总结 (${results.length})</div><div id="resultsList"></div></div>`;
    lucide.createIcons({ nodes: [container] });
    const list = container.querySelector('#resultsList');

    let index = 0;
    for (const r of results) {
        const badgeClass = r.status === 'success' ? 'badge-success' :
            r.status === 'skipped' ? 'badge-skip' :
                r.status === 'no_subtitle' ? 'badge-warning' : 'badge-error';
        const badgeText = r.status === 'success' ? '✓ 完成' :
            r.status === 'skipped' ? '→ 已存在' :
                r.status === 'no_subtitle' ? '△ 无字幕' : '✗ 失败';

        const card = document.createElement('div');
        card.className = 'result-card';
        if (index === 0) card.classList.add('expanded');
        card.innerHTML = `
            <div class="result-card-header" onclick="toggleResultCard(this)">
                <span class="title">${escapeHtml(r.title)}</span>
                <span class="badge ${badgeClass}">${badgeText}</span>
                <span class="chevron">▶</span>
            </div>
            <div class="result-card-body">
                <div class="reading-content" style="padding-top:12px;">加载中...</div>
            </div>
        `;
        list.appendChild(card);
        index++;

        // Fetch and render content
        try {
            const apiPath = encodePath(r.path);
            const res = await fetch(`/api/summary/${apiPath}`);
            const data = await res.json();
            if (data.content) {
                card.querySelector('.reading-content').innerHTML = renderMarkdown(data.content);
            }
        } catch { /* ignore */ }
    }
}

function toggleResultCard(header) {
    header.parentElement.classList.toggle('expanded');
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function addLog(container, text, cls) {
    const div = document.createElement('div');
    div.className = `log-entry${cls ? ' ' + cls : ''}`;
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function updateProgress(bar, statsEl, completed, total) {
    if (total > 0) {
        const pct = Math.round((completed / total) * 100);
        bar.style.width = pct + '%';
        statsEl.innerHTML = `
            <span class="stat">已完成 <span class="num">${completed}</span> / ${total}</span>
            <span class="stat">进度 <span class="num">${pct}%</span></span>
        `;
    }
}

function encodePath(path) {
    // Encode each path segment individually, preserving /
    return path.split('/').map(encodeURIComponent).join('/');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Submit Handlers
// ---------------------------------------------------------------------------
async function submitURL() {
    const text = document.getElementById('urlInput').value.trim();
    if (!text) return;
    const urls = text.split('\n').map(u => u.trim()).filter(Boolean);
    const model = document.getElementById('urlModel').value;
    const concurrency = parseInt(document.getElementById('urlConcurrency').value) || 12;
    try {
        const res = await fetch('/api/summarize/url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls, model, concurrency })
        });
        const data = await res.json();
        if (data.error) { alert(data.error); return; }
        listenProgress(data.task_id, 'url');
    } catch (err) { alert('请求失败: ' + err.message); }
}

async function submitUser() {
    const userVal = document.getElementById('userInput').value.trim();
    if (!userVal) return;
    const count = parseInt(document.getElementById('userCount').value) || 50;
    const model = document.getElementById('userModel').value;
    const concurrency = parseInt(document.getElementById('userConcurrency').value) || 12;
    try {
        const res = await fetch('/api/summarize/user', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: userVal, count, model, concurrency })
        });
        const data = await res.json();
        if (data.error) { alert(data.error); return; }
        listenProgress(data.task_id, 'user');
    } catch (err) { alert('请求失败: ' + err.message); }
}

// ---------------------------------------------------------------------------
// Favorites Browser
// ---------------------------------------------------------------------------
let currentFavId = null;
let currentFavPage = 1;
let favHasMore = false;
const favVideoData = new Map(); // bvid -> { summaryPath, title, ... }

async function loadFavoriteFolders() {
    const container = document.getElementById('sidebarFavorites');
    if (!container) return;

    try {
        const res = await fetch('/api/favorites/list');
        const data = await res.json();
        if (data.error) {
            container.innerHTML = '<div style="padding:8px 12px;font-size:12px;color:var(--text-muted);">未登录</div>';
            return;
        }

        const folders = data.folders || [];
        const defaultFolder = folders.find(f => f.is_default);
        const otherFolders = folders.filter(f => !f.is_default);

        let html = '';

        // Default folder always visible
        if (defaultFolder) {
            html += `
                <div class="fav-folder-item" data-fav-id="${defaultFolder.id}" data-fav-title="${escapeHtml(defaultFolder.title)}">
                    <span class="folder-name">📁 ${escapeHtml(defaultFolder.title)}</span>
                    <span class="folder-count">${defaultFolder.count}</span>
                </div>`;
        }

        // Other folders in collapsible section
        if (otherFolders.length > 0) {
            html += `
                <div class="fav-folder-toggle" onclick="toggleFavFolders()">
                    <span class="toggle-arrow" id="favFoldArrow">▸</span>
                    <span>其他收藏夹 (${otherFolders.length})</span>
                </div>
                <div class="fav-folder-list collapsed" id="favFolderList">
                    ${otherFolders.map(f => `
                        <div class="fav-folder-item" data-fav-id="${f.id}" data-fav-title="${escapeHtml(f.title)}">
                            <span class="folder-name">📁 ${escapeHtml(f.title)}</span>
                            <span class="folder-count">${f.count}</span>
                        </div>
                    `).join('')}
                </div>`;
        }

        container.innerHTML = html;

        // Event delegation for folder clicks
        container.addEventListener('click', (e) => {
            const item = e.target.closest('.fav-folder-item');
            if (!item) return;
            const favId = parseInt(item.dataset.favId);
            const title = item.dataset.favTitle;
            selectFavoriteFolder(favId, title);
        });

    } catch (err) {
        container.innerHTML = '<div style="padding:8px 12px;font-size:12px;color:var(--text-muted);">加载失败</div>';
    }
}

function toggleFavFolders() {
    const list = document.getElementById('favFolderList');
    const arrow = document.getElementById('favFoldArrow');
    if (!list) return;
    list.classList.toggle('collapsed');
    arrow.textContent = list.classList.contains('collapsed') ? '▸' : '▾';
}

// Event delegation for video card clicks
const favGrid = document.getElementById('favVideoGrid');
favGrid.addEventListener('click', (e) => {
    // Handle unfavorite button click
    const unfavBtn = e.target.closest('.unfav-btn');
    if (unfavBtn) {
        e.stopPropagation();
        const card = unfavBtn.closest('.video-card');
        const bvid = card.dataset.bvid;
        unfavoriteVideo(bvid, card);
        return;
    }

    const card = e.target.closest('.video-card');
    if (!card) return;

    const bvid = card.dataset.bvid;
    const vdata = favVideoData.get(bvid);

    if (vdata && vdata.summaryPath) {
        showVideoSummary(bvid, vdata.summaryPath);
    } else {
        openExternal(`https://www.bilibili.com/video/${bvid}`);
    }
});

function selectFavoriteFolder(favId, title) {
    currentFavId = favId;
    currentFavPage = 1;

    // Highlight active folder
    document.querySelectorAll('.fav-folder-item').forEach(el => el.classList.remove('active'));
    const active = document.querySelector(`.fav-folder-item[data-fav-id="${favId}"]`);
    if (active) active.classList.add('active');

    // Switch to fav-page
    showPage('fav-page');

    // Update header
    document.getElementById('favBrowseTitle').innerHTML = `<i data-lucide="star" class="lucide-icon"></i> ${escapeHtml(title)}`;
    lucide.createIcons({ nodes: [document.getElementById('favBrowseTitle')] });
    document.getElementById('favBrowseSubtitle').textContent = '加载中...';

    // Clear and load — reset display states
    const grid = document.getElementById('favVideoGrid');
    grid.innerHTML = '';
    grid.style.display = '';
    document.getElementById('favAutoProgress').innerHTML = '';
    document.getElementById('favReadingView').style.display = 'none';
    document.getElementById('favLoadMore').style.display = 'none';

    loadFavoriteVideos(favId, 1, false);
}

async function loadFavoriteVideos(favId, page, append) {
    const grid = document.getElementById('favVideoGrid');
    const loadMore = document.getElementById('favLoadMore');

    try {
        const res = await fetch(`/api/favorites/${favId}/videos?page=${page}`);
        const data = await res.json();
        if (data.error) {
            document.getElementById('favBrowseSubtitle').textContent = data.error;
            return;
        }

        const videos = data.videos || [];
        currentFavPage = data.page;
        favHasMore = data.has_more;

        document.getElementById('favBrowseSubtitle').textContent = `共 ${videos.length} 个视频 (第 ${page} 页)`;
        loadMore.style.display = favHasMore ? '' : 'none';

        const html = videos.map(v => renderVideoCard(v)).join('');
        if (append) {
            grid.innerHTML += html;
        } else {
            grid.innerHTML = html;
        }

        // Auto-summarize videos that don't have summaries
        const unsummarized = videos.filter(v => v.summary_status === 'none').map(v => v.bvid);
        if (unsummarized.length > 0) {
            autoSummarizeVideos(unsummarized);
        }

    } catch (err) {
        document.getElementById('favBrowseSubtitle').textContent = '加载失败: ' + err.message;
    }
}

function renderVideoCard(v) {
    const durationStr = formatDuration(v.duration);
    const playStr = formatPlayCount(v.play_count);
    const badgeClass = v.summary_status;
    const badgeText = {
        'done': '已总结',
        'no_subtitle': '无字幕',
        'none': '未总结',
    }[v.summary_status] || '未总结';

    // Store video data in JS Map for reliable click handling
    favVideoData.set(v.bvid, {
        summaryPath: v.summary_path || null,
        title: v.title,
        upper: v.upper || '',
        upperMid: v.upper_mid || 0,
    });

    return `
        <div class="video-card" id="card-${v.bvid}" data-bvid="${v.bvid}">
            <div class="cover-wrapper">
                <img src="${v.cover}" alt="" loading="lazy" referrerpolicy="no-referrer">
                <button class="unfav-btn" title="取消收藏">✕</button>
                <span class="duration-badge">${durationStr}</span>
                <span class="summary-badge ${badgeClass}" id="badge-${v.bvid}">${badgeText}</span>
            </div>
            <div class="card-info">
                <div class="card-title" title="${escapeHtml(v.title)}">${escapeHtml(v.title)}</div>
                <div class="card-meta">
                    <span class="upper-name">${escapeHtml(v.upper)}</span>
                    <span class="play-count">▶ ${playStr}</span>
                </div>
            </div>
        </div>
    `;
}

function formatDuration(seconds) {
    if (!seconds) return '0:00';
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
}

function formatPlayCount(count) {
    if (!count) return '0';
    if (count >= 10000) return (count / 10000).toFixed(1) + '万';
    return String(count);
}

async function autoSummarizeVideos(bvids) {
    const progressEl = document.getElementById('favAutoProgress');
    progressEl.innerHTML = `
        <div>↻ 正在自动总结 ${bvids.length} 个视频...</div>
        <div class="mini-log" id="favMiniLog"></div>
    `;

    // Mark cards as summarizing
    bvids.forEach(bvid => {
        const badge = document.getElementById(`badge-${bvid}`);
        if (badge) {
            badge.className = 'summary-badge summarizing';
            badge.textContent = '总结中';
        }
    });

    try {
        const res = await fetch('/api/favorites/summarize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bvids, output_subdir: 'favorites' })
        });
        const data = await res.json();
        if (!data.task_id) {
            progressEl.innerHTML = '';
            return;
        }

        // Listen to SSE for auto-summarize progress
        listenAutoSummarize(data.task_id, progressEl);
    } catch (err) {
        progressEl.innerHTML = `<div style="color:var(--error);">自动总结失败: ${err.message}</div>`;
    }
}

function listenAutoSummarize(taskId, progressEl) {
    const miniLog = document.getElementById('favMiniLog');
    let lastEventId = -1;
    let isDone = false;
    let retryCount = 0;

    async function connectSSE() {
        if (isDone) return;
        try {
            const resp = await fetch(`/api/progress/${taskId}`, {
                headers: { 'Last-Event-ID': String(lastEventId) }
            });
            if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
            retryCount = 0;
            const reader = resp.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const blocks = buffer.split('\n\n');
                buffer = blocks.pop();

                for (const block of blocks) {
                    if (!block.trim() || block.trim().startsWith(':')) continue;
                    let eventType = 'message', eventData = '', eventId = null;
                    for (const line of block.split('\n')) {
                        if (line.startsWith('event: ')) eventType = line.slice(7);
                        else if (line.startsWith('data: ')) eventData = line.slice(6);
                        else if (line.startsWith('id: ')) eventId = parseInt(line.slice(4));
                    }
                    if (eventId !== null) lastEventId = eventId;

                    let d;
                    try { d = JSON.parse(eventData); } catch { continue; }

                    if (eventType === 'completed') {
                        const badge = document.getElementById(`badge-${d.bvid}`);
                        if (badge) {
                            if (d.status === 'no_subtitle') {
                                badge.className = 'summary-badge no_subtitle';
                                badge.textContent = '无字幕';
                            } else {
                                badge.className = 'summary-badge done';
                                badge.textContent = '已总结';
                                // Update JS Map for event delegation
                                const vdata = favVideoData.get(d.bvid);
                                if (vdata && d.path) {
                                    vdata.summaryPath = d.path;
                                }
                            }
                        }
                        if (miniLog) {
                            const icon = d.status === 'no_subtitle' ? '△' : '✓';
                            miniLog.innerHTML += `<div class="log-line">${icon} ${escapeHtml(d.title)}</div>`;
                            miniLog.scrollTop = miniLog.scrollHeight;
                        }
                    } else if (eventType === 'skip') {
                        // Already summarized
                    } else if (eventType === 'error') {
                        const badge = document.getElementById(`badge-${d.bvid || ''}`);
                        if (badge) {
                            badge.className = 'summary-badge none';
                            badge.textContent = '失败';
                        }
                    } else if (eventType === 'done') {
                        isDone = true;
                        progressEl.innerHTML = `<div style="color:var(--success);">✨ 自动总结完成</div>`;
                        setTimeout(() => { progressEl.innerHTML = ''; }, 3000);
                        return;
                    }
                }
            }
        } catch (err) { /* connection error */ }

        if (!isDone && retryCount < 5) {
            retryCount++;
            await new Promise(r => setTimeout(r, 2000));
            return connectSSE();
        }
    }
    connectSSE();
}

function loadMoreFavoriteVideos() {
    if (currentFavId && favHasMore) {
        loadFavoriteVideos(currentFavId, currentFavPage + 1, true);
    }
}

async function showVideoSummary(bvid, path) {
    const readingView = document.getElementById('favReadingView');
    const readingContent = document.getElementById('favReadingContent');
    const grid = document.getElementById('favVideoGrid');
    const loadMore = document.getElementById('favLoadMore');

    readingContent.innerHTML = '<p style="color:var(--text-muted);">加载中...</p>';
    grid.style.display = 'none';
    loadMore.style.display = 'none';
    document.getElementById('favAutoProgress').style.display = 'none';
    readingView.style.display = 'block';

    try {
        // Encode path segments for URL (preserve /)
        const encodedPath = path.split('/').map(s => encodeURIComponent(s)).join('/');
        const res = await fetch(`/api/summary/${encodedPath}`);
        if (!res.ok) {
            readingContent.innerHTML = `<p style="color:var(--error);">HTTP ${res.status}: 无法加载总结</p>`;
            return;
        }
        const data = await res.json();
        if (data.content) {
            // Detect no-subtitle content → show retry button
            const isNoSub = data.content.includes('无法获取字幕');
            const actions = document.getElementById('favReadingActions');
            let actionsHtml = '';
            if (isNoSub) {
                actionsHtml += `<button class="btn-secondary" style="padding:5px 12px;font-size:12px;color:var(--accent);border-color:var(--accent);" onclick="retrySummarize('${bvid}')">重试</button>`;
            }
            actionsHtml += `<button class="btn-secondary" style="padding:5px 12px;font-size:12px;color:var(--error);border-color:var(--error);" onclick="unfavoriteFromReading('${bvid}')">✕ 取消收藏</button>`;
            actions.innerHTML = actionsHtml;

            readingContent.innerHTML = renderMarkdown(data.content);

            // Inject author line if missing from existing summaries
            const vdata = favVideoData.get(bvid);
            if (vdata && vdata.upper && !data.content.includes('**作者**:')) {
                const authorLink = vdata.upperMid
                    ? `<strong>作者</strong>: <a href="https://space.bilibili.com/${vdata.upperMid}" target="_blank">${escapeHtml(vdata.upper)}</a>`
                    : `<strong>作者</strong>: ${escapeHtml(vdata.upper)}`;
                const authorEl = document.createElement('p');
                authorEl.innerHTML = authorLink;
                // Insert after 视频链接 paragraph
                const paragraphs = readingContent.querySelectorAll('p');
                let inserted = false;
                for (const p of paragraphs) {
                    if (p.textContent.includes('视频链接')) {
                        p.insertAdjacentElement('afterend', authorEl);
                        inserted = true;
                        break;
                    }
                }
                if (!inserted && paragraphs.length > 0) {
                    paragraphs[0].insertAdjacentElement('afterend', authorEl);
                }
            }
            // Make links in summary open externally
            readingContent.querySelectorAll('a').forEach(a => {
                a.addEventListener('click', (e) => {
                    e.preventDefault();
                    openExternal(a.href);
                });
            });
        } else {
            readingContent.innerHTML = '<p style="color:var(--error);">总结内容为空</p>';
        }
    } catch (err) {
        readingContent.innerHTML = `<p style="color:var(--error);">加载失败: ${err.message}</p>`;
    }
}

function closeFavReading() {
    document.getElementById('favReadingView').style.display = 'none';
    document.getElementById('favVideoGrid').style.display = '';
    document.getElementById('favAutoProgress').style.display = '';
    document.getElementById('favLoadMore').style.display = favHasMore ? '' : 'none';
}

async function retrySummarize(bvid) {
    const readingContent = document.getElementById('favReadingContent');
    readingContent.innerHTML = '<p style="color:var(--text-muted);">↻ 正在重新获取字幕并生成总结...</p>';

    try {
        const res = await fetch(`/api/retry/${bvid}`, { method: 'POST' });
        const data = await res.json();
        if (data.error) {
            readingContent.innerHTML = `<p style="color:var(--error);">重试失败: ${data.error}</p>`;
            return;
        }

        const taskId = data.task_id;
        readingContent.innerHTML = '<p style="color:var(--text-muted);">◌ 正在获取字幕...</p>';

        // Listen to SSE for progress (server sends named events)
        const evtSrc = new EventSource(`/api/progress/${taskId}`);

        evtSrc.addEventListener('processing', (e) => {
            try {
                const d = JSON.parse(e.data);
                readingContent.innerHTML = `<p style="color:var(--text-muted);">◌ ${d.step || '处理中'}...</p>`;
            } catch (_) { }
        });

        evtSrc.addEventListener('completed', (e) => {
            evtSrc.close();
            try {
                const d = JSON.parse(e.data);
                const badge = document.getElementById(`badge-${bvid}`);
                if (d.status === 'no_subtitle') {
                    if (badge) {
                        badge.className = 'summary-badge no_subtitle';
                        badge.textContent = '无字幕';
                    }
                    readingContent.innerHTML = '<p style="color:var(--warning);">△ 仍然无法获取字幕，可稍后再试</p>';
                } else {
                    if (badge) {
                        badge.className = 'summary-badge done';
                        badge.textContent = '已总结';
                    }
                    const vdata = favVideoData.get(bvid);
                    if (vdata && d.path) {
                        vdata.summaryPath = d.path;
                    }
                    showVideoSummary(bvid, d.path);
                }
            } catch (_) { }
        });

        evtSrc.addEventListener('error', (e) => {
            evtSrc.close();
            try {
                const d = JSON.parse(e.data);
                readingContent.innerHTML = `<p style="color:var(--error);">重试失败: ${d.message || '未知错误'}</p>`;
            } catch (_) {
                readingContent.innerHTML = '<p style="color:var(--error);">连接中断</p>';
            }
        });

        evtSrc.addEventListener('done', () => {
            evtSrc.close();
        });

        evtSrc.onerror = () => {
            evtSrc.close();
        };
    } catch (err) {
        readingContent.innerHTML = `<p style="color:var(--error);">重试失败: ${err.message}</p>`;
    }
}

async function unfavoriteVideo(bvid, cardEl) {
    if (!currentFavId) return;
    if (!confirm('确定取消收藏这个视频？')) return;

    // Visual feedback
    if (cardEl) {
        cardEl.style.opacity = '0.4';
        cardEl.style.pointerEvents = 'none';
    }

    try {
        const res = await fetch(`/api/favorites/${currentFavId}/video/${bvid}`, {
            method: 'DELETE'
        });
        const data = await res.json();
        if (data.error) {
            alert('取消收藏失败: ' + data.error);
            if (cardEl) {
                cardEl.style.opacity = '';
                cardEl.style.pointerEvents = '';
            }
            return;
        }
        // Remove card with animation
        if (cardEl) {
            cardEl.style.transition = 'all 0.3s ease';
            cardEl.style.transform = 'scale(0.8)';
            cardEl.style.opacity = '0';
            setTimeout(() => cardEl.remove(), 300);
        }
        favVideoData.delete(bvid);
    } catch (err) {
        alert('取消收藏失败: ' + err.message);
        if (cardEl) {
            cardEl.style.opacity = '';
            cardEl.style.pointerEvents = '';
        }
    }
}

async function unfavoriteFromReading(bvid) {
    if (!currentFavId) return;
    if (!confirm('确定取消收藏这个视频？')) return;

    try {
        const res = await fetch(`/api/favorites/${currentFavId}/video/${bvid}`, {
            method: 'DELETE'
        });
        const data = await res.json();
        if (data.error) {
            alert('取消收藏失败: ' + data.error);
            return;
        }
        // Remove card from grid
        const card = document.getElementById(`card-${bvid}`);
        if (card) card.remove();
        favVideoData.delete(bvid);
        // Go back to grid
        closeFavReading();
    } catch (err) {
        alert('取消收藏失败: ' + err.message);
    }
}

function showPage(pageId) {
    switchToPage(pageId, null);
}

// ---------------------------------------------------------------------------
// Settings & Model Selection
// ---------------------------------------------------------------------------
let settingsLoaded = false;

async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        document.getElementById('settingsBaseUrl').value = data.base_url || '';
        document.getElementById('settingsToken').placeholder = data.auth_token_masked || '输入 API Token';
        document.getElementById('settingsToken').value = '';
        settingsLoaded = true;
        // Auto-load models on first visit
        loadModels();
    } catch (err) {
        console.error('加载设置失败:', err);
    }
}

async function saveSettings() {
    const statusEl = document.getElementById('settingsSaveStatus');
    const baseUrl = document.getElementById('settingsBaseUrl').value.trim();
    const token = document.getElementById('settingsToken').value.trim();

    statusEl.style.color = 'var(--text-muted)';
    statusEl.textContent = '保存中...';

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                base_url: baseUrl,
                auth_token: token,
            })
        });
        const data = await res.json();
        if (data.success) {
            statusEl.style.color = 'var(--success)';
            statusEl.textContent = '✓ 已保存';
            // Reload to show masked token
            setTimeout(() => loadSettings(), 500);
        } else {
            statusEl.style.color = 'var(--error)';
            statusEl.textContent = '✗ 保存失败: ' + (data.error || '');
        }
    } catch (err) {
        statusEl.style.color = 'var(--error)';
        statusEl.textContent = '✗ 保存失败: ' + err.message;
    }
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
}

async function loadModels() {
    const listEl = document.getElementById('modelList');
    listEl.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">◌ 加载中...</p>';

    try {
        const res = await fetch('/api/models');
        if (!res.ok) {
            const err = await res.json();
            listEl.innerHTML = `<p style="color:var(--error);font-size:13px;">✗ ${err.error || '加载失败'}</p>`;
            return;
        }
        const data = await res.json();
        const models = data.models || [];
        const current = data.current || '';

        if (models.length === 0) {
            listEl.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">没有可用模型</p>';
            return;
        }

        listEl.innerHTML = models.map(m => {
            const isActive = m.id === current;
            return `<div class="model-item${isActive ? ' active' : ''}" onclick="selectModel('${m.id}', this)">
                <div class="model-name">${m.id}</div>
                <div class="model-owner">${m.owned_by || ''}</div>
                ${isActive ? '<span class="model-check">✓</span>' : ''}
            </div>`;
        }).join('');

    } catch (err) {
        listEl.innerHTML = `<p style="color:var(--error);font-size:13px;">✗ ${err.message}</p>`;
    }
}

async function selectModel(modelId, el) {
    // Visual feedback
    document.querySelectorAll('.model-item').forEach(i => {
        i.classList.remove('active');
        const check = i.querySelector('.model-check');
        if (check) check.remove();
    });
    el.classList.add('active');
    el.insertAdjacentHTML('beforeend', '<span class="model-check">✓</span>');

    // Save to backend
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ default_model: modelId })
        });
    } catch (err) {
        console.error('保存模型失败:', err);
    }
}

function toggleTokenVisibility() {
    const input = document.getElementById('settingsToken');
    const btn = document.getElementById('toggleTokenBtn');
    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = '<i data-lucide="eye-off" class="lucide-icon" style="width:14px;height:14px;"></i>';
    } else {
        input.type = 'password';
        btn.innerHTML = '<i data-lucide="eye" class="lucide-icon" style="width:14px;height:14px;"></i>';
    }
    lucide.createIcons({ nodes: [btn] });
}

// Load settings when navigating to settings page
const origSwitchToPage = switchToPage;
switchToPage = function (pageId, navEl) {
    origSwitchToPage(pageId, navEl);
    if (pageId === 'settings-page' && !settingsLoaded) {
        loadSettings();
    }
};
