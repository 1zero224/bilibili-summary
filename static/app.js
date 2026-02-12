/* =============================================
   BiliSummary — App Logic
   ============================================= */

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
        if (data.logged_in) {
            dot.className = 'status-dot online';
            text.textContent = 'Bilibili 已登录';
        } else {
            dot.className = 'status-dot offline';
            text.textContent = '未登录 Bilibili';
        }
    } catch {
        document.getElementById('statusDot').className = 'status-dot offline';
        document.getElementById('statusText').textContent = '连接失败';
    }
}
checkStatus();

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
            <span class="icon">${item.no_subtitle ? '⚠️' : '📄'}</span>
            <span class="title">${escapeHtml(item.name)}</span>
        </div>
    `).join('');
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
    document.getElementById('browseTitle').textContent = `👤 ${group.display_name}`;
    document.getElementById('browseSubtitle').textContent = `UID: ${group.uid} · ${group.count} 篇总结`;

    // Render item list
    const readingView = document.getElementById('readingView');
    readingView.classList.remove('active');
    const list = document.getElementById('browseList');
    list.style.display = 'block';
    list.innerHTML = group.items.map(item => `
        <div class="summary-item" onclick="openSummary('${encodePath(item.path)}')">
            <span class="icon">${item.no_subtitle ? '⚠️' : '📄'}</span>
            <span class="title">${escapeHtml(item.name)}</span>
        </div>
    `).join('');
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
document.addEventListener('click', (e) => {
    const link = e.target.closest('a[href]');
    if (!link) return;
    const href = link.getAttribute('href');
    if (href && href.startsWith('http')) {
        e.preventDefault();
        e.stopPropagation();
        window.open(href, '_blank');
    }
});

// ---------------------------------------------------------------------------
// SSE Progress Listener
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

    const es = new EventSource(`/api/progress/${taskId}`);

    es.addEventListener('start', (e) => {
        const d = JSON.parse(e.data);
        total = d.total;
        addLog(logEl, `🚀 开始处理 ${d.total} 个视频 (并发: ${d.concurrency}, 模型: ${d.model})`, 'info');
    });

    es.addEventListener('info', (e) => {
        const d = JSON.parse(e.data);
        addLog(logEl, `ℹ️  ${d.message}`, 'info');
    });

    es.addEventListener('processing', (e) => {
        const d = JSON.parse(e.data);
        addLog(logEl, `⏳ ${d.title} — ${d.step}`, '');
    });

    es.addEventListener('skip', (e) => {
        const d = JSON.parse(e.data);
        completed++;
        updateProgress(progressBar, statsEl, completed, total);
        addLog(logEl, `⏭️  已存在，跳过: ${d.title}`, 'skip');
        if (d.path) {
            completedPaths.push({ title: d.title, path: d.path, status: 'skipped' });
        }
    });

    es.addEventListener('completed', (e) => {
        const d = JSON.parse(e.data);
        completed++;
        updateProgress(progressBar, statsEl, completed, total);
        if (d.status === 'no_subtitle') {
            addLog(logEl, `⚠️  无字幕: ${d.title}`, 'warning');
        } else {
            addLog(logEl, `✅ ${d.title} (${d.duration_sec}s)`, 'success');
        }
        if (d.path) {
            completedPaths.push({ title: d.title, path: d.path, status: d.status, duration: d.duration_sec });
        }
    });

    es.addEventListener('error', (e) => {
        const d = JSON.parse(e.data);
        completed++;
        updateProgress(progressBar, statsEl, completed, total);
        addLog(logEl, `❌ ${d.title || ''}: ${d.message}`, 'error');
    });

    es.addEventListener('done', (e) => {
        const d = JSON.parse(e.data);
        es.close();
        submitBtn.disabled = false;
        submitBtn.innerHTML = '🚀 开始总结';
        addLog(logEl, `✨ 完成! 成功: ${d.success} | 跳过: ${d.skipped} | 无字幕: ${d.no_subtitle} | 失败: ${d.errors}`, 'info');
        progressBar.style.width = '100%';
        showInlineResults(resultsArea, completedPaths);
        loadSidebarBrowse(); // Refresh sidebar
    });

    es.onerror = () => {
        es.close();
        submitBtn.disabled = false;
        submitBtn.innerHTML = '🚀 开始总结';
        addLog(logEl, '❌ 连接中断', 'error');
    };
}

// ---------------------------------------------------------------------------
// Inline Results
// ---------------------------------------------------------------------------
async function showInlineResults(container, results) {
    if (!results.length) return;

    container.innerHTML = `<div class="card"><div class="card-title">📄 生成的总结 (${results.length})</div><div id="resultsList"></div></div>`;
    const list = container.querySelector('#resultsList');

    let index = 0;
    for (const r of results) {
        const badgeClass = r.status === 'success' ? 'badge-success' :
            r.status === 'skipped' ? 'badge-skip' :
                r.status === 'no_subtitle' ? 'badge-warning' : 'badge-error';
        const badgeText = r.status === 'success' ? '✅ 完成' :
            r.status === 'skipped' ? '⏭️ 已存在' :
                r.status === 'no_subtitle' ? '⚠️ 无字幕' : '❌ 失败';

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

function openExternal(url) {
    window.open(url, '_blank');
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

async function submitFavorites() {
    const count = parseInt(document.getElementById('favCount').value) || 20;
    const model = document.getElementById('favModel').value;
    const concurrency = parseInt(document.getElementById('favConcurrency').value) || 12;
    try {
        const res = await fetch('/api/summarize/favorites', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ count, model, concurrency })
        });
        const data = await res.json();
        if (data.error) { alert(data.error); return; }
        listenProgress(data.task_id, 'fav');
    } catch (err) { alert('请求失败: ' + err.message); }
}
