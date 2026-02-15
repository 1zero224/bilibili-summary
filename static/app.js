/* =============================================
   BiliSummary — App Logic
   ============================================= */

// ---------------------------------------------------------------------------
// Theme: Dark / Light
// ---------------------------------------------------------------------------
function initTheme() {
    const saved = localStorage.getItem('bilisummary-theme') || 'light';
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
let browseViewMode = localStorage.getItem('bilisummary-browse-view') || 'thumb';
let currentBrowseItems = [];
let favViewMode = localStorage.getItem('bilisummary-fav-view') || 'thumb';
let currentFavVideos = [];

const STATUS_META = {
    processing: { label: '处理中', tone: 'info' },
    success: { label: '成功', tone: 'success' },
    failed: { label: '失败', tone: 'error' },
    no_subtitle: { label: '无字幕', tone: 'warning' },
    skipped: { label: '已跳过', tone: 'skip' },
    pending: { label: '未总结', tone: 'muted' },
};

function normalizeStatus(raw) {
    const map = {
        done: 'success',
        success: 'success',
        summarizing: 'processing',
        processing: 'processing',
        error: 'failed',
        failed: 'failed',
        none: 'pending',
        pending: 'pending',
        no_subtitle: 'no_subtitle',
        skipped: 'skipped',
    };
    return map[raw] || 'pending';
}

function statusText(raw) {
    const key = normalizeStatus(raw);
    return STATUS_META[key]?.label || STATUS_META.pending.label;
}

function renderState(container, {
    type = 'empty', // loading | empty | error
    title = '',
    message = '',
    actionText = '',
    onAction = null,
} = {}) {
    if (!container) return;
    container.innerHTML = '';

    const box = document.createElement('div');
    box.className = `ui-state ui-state-${type}`;

    if (type === 'loading') {
        const spinner = document.createElement('span');
        spinner.className = 'spinner';
        box.appendChild(spinner);
    }

    const titleEl = document.createElement('div');
    titleEl.className = 'ui-state-title';
    titleEl.textContent = title || (type === 'loading' ? '加载中' : type === 'error' ? '加载失败' : '暂无内容');
    box.appendChild(titleEl);

    if (message) {
        const messageEl = document.createElement('div');
        messageEl.className = 'ui-state-message';
        messageEl.textContent = message;
        box.appendChild(messageEl);
    }

    if (actionText && typeof onAction === 'function') {
        const btn = document.createElement('button');
        btn.className = 'btn btn-secondary ui-state-action';
        btn.type = 'button';
        btn.textContent = actionText;
        btn.addEventListener('click', onAction);
        box.appendChild(btn);
    }

    container.appendChild(box);
}

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
        qrStatus.textContent = d.message || '二维码已扫描，请在手机上确认';
        qrStatus.className = 'qr-status scanned';
    });

    loginEventSource.addEventListener('done', (e) => {
        const d = JSON.parse(e.data);
        qrStatus.textContent = d.message || '登录成功';
        qrStatus.className = 'qr-status success';
        loginEventSource.close();
        loginEventSource = null;
        // Refresh status and close modal after a beat
        setTimeout(() => {
            checkStatus();
            loadFavoriteFolders();
            modal.classList.remove('active');
        }, 1200);
    });

    loginEventSource.addEventListener('timeout', (e) => {
        const d = JSON.parse(e.data);
        qrStatus.textContent = d.message || '二维码已超时，请重试';
        qrStatus.className = 'qr-status error';
        loginEventSource.close();
        loginEventSource = null;
    });

    loginEventSource.addEventListener('error', (e) => {
        try {
            const d = JSON.parse(e.data);
            qrStatus.textContent = d.message || '连接失败';
        } catch {
            qrStatus.textContent = '连接失败';
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

function showActionDialog({
    title = '提示',
    message = '',
    confirmText = '确定',
    cancelText = '',
    danger = false,
} = {}) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';

        const confirmBtnClass = danger ? 'btn btn-danger' : 'btn btn-primary';
        overlay.innerHTML = `
            <div class="modal dialog-modal" role="dialog" aria-modal="true" aria-labelledby="dialogTitle">
                <div class="modal-header">
                    <h3 id="dialogTitle">${escapeHtml(title)}</h3>
                    <button type="button" class="modal-close" data-action="close" aria-label="关闭">✕</button>
                </div>
                <div class="modal-body modal-body-left">
                    <p class="modal-message">${escapeHtml(message)}</p>
                    <div class="modal-actions">
                        ${cancelText ? `<button type="button" class="btn btn-secondary" data-action="cancel">${escapeHtml(cancelText)}</button>` : ''}
                        <button type="button" class="${confirmBtnClass}" data-action="confirm">${escapeHtml(confirmText)}</button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        const closeAndResolve = (result) => {
            overlay.remove();
            document.removeEventListener('keydown', onKeyDown);
            resolve(result);
        };

        const onKeyDown = (e) => {
            if (e.key === 'Escape') closeAndResolve(false);
        };
        document.addEventListener('keydown', onKeyDown);

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeAndResolve(false);
            if (e.target.closest('[data-action="close"]')) closeAndResolve(false);
            if (e.target.closest('[data-action="cancel"]')) closeAndResolve(false);
            if (e.target.closest('[data-action="confirm"]')) closeAndResolve(true);
        });
    });
}

function showAlert(message, title = '提示') {
    return showActionDialog({ title, message, confirmText: '知道了' });
}

function showConfirm(message, {
    title = '请确认',
    confirmText = '确定',
    cancelText = '取消',
    danger = false,
} = {}) {
    return showActionDialog({ title, message, confirmText, cancelText, danger });
}

function showToast({
    title = '提示',
    message = '',
    tone = 'info', // info | success | error
    actionText = '',
    onAction = null,
    duration = 5000,
} = {}) {
    const container = document.getElementById('toastContainer');
    if (!container) return null;

    const toast = document.createElement('div');
    toast.className = `toast toast-${tone}`;
    toast.innerHTML = `
        <div class="toast-title">${title}</div>
        <div class="toast-message">${message}</div>
        ${actionText ? `<button type="button" class="toast-action">${actionText}</button>` : ''}
    `;
    container.appendChild(toast);

    const close = () => {
        toast.classList.add('toast-fadeout');
        setTimeout(() => toast.remove(), 280);
    };

    if (actionText && typeof onAction === 'function') {
        const btn = toast.querySelector('.toast-action');
        btn.addEventListener('click', async () => {
            try {
                await onAction();
            } finally {
                close();
            }
        });
    }

    if (duration > 0) {
        setTimeout(close, duration);
    }

    return { close, element: toast };
}

async function doLogout() {
    const confirmed = await showConfirm('确定要退出登录吗？', {
        title: '退出登录',
        confirmText: '退出登录',
        cancelText: '取消',
        danger: true,
    });
    if (!confirmed) return;

    try {
        await fetch('/api/logout', { method: 'POST' });
        checkStatus();
        loadFavoriteFolders();
    } catch (err) {
        await showAlert('注销失败: ' + err.message, '退出失败');
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
            renderState(container, { type: 'empty', title: '暂无总结', message: '先在“URL 模式”或“UP 主模式”生成内容' });
            return;
        }

        let html = '';
        for (const cat of summariesData.categories) {
            if (cat.type === 'users') {
                // UP 主: expandable parent → children are individual users
                html += `
                    <div class="nav-parent" onclick="toggleParent(this)">
                        <span class="icon"><i data-lucide="${cat.icon}" class="lucide-icon"></i></span>
                        <span class="label">${cat.label}</span>
                        <span class="count">${cat.count}</span>
                        <span class="chevron"><i data-lucide="chevron-right" class="lucide-icon"></i></span>
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
                        <span class="icon"><i data-lucide="${cat.icon}" class="lucide-icon"></i></span>
                        <span class="label">${cat.label}</span>
                        <span class="count">${cat.count}</span>
                        <span class="chevron"><i data-lucide="chevron-right" class="lucide-icon"></i></span>
                    </div>
                    <div class="nav-children"></div>`;
            }
        }
        container.innerHTML = html;
        lucide.createIcons({ nodes: [container] });
    } catch (err) {
        renderState(container, {
            type: 'error',
            title: '浏览目录加载失败',
            message: '请稍后重试',
            actionText: '重试',
            onAction: () => loadSidebarBrowse(),
        });
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

    // Render card grid
    const readingView = document.getElementById('readingView');
    readingView.classList.remove('active');
    const list = document.getElementById('browseList');
    list.style.display = 'block';
    currentBrowseItems = cat.items || [];
    renderBrowseItems(currentBrowseItems);
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

    // Render card grid
    const readingView = document.getElementById('readingView');
    readingView.classList.remove('active');
    const list = document.getElementById('browseList');
    list.style.display = 'block';
    currentBrowseItems = group.items || [];
    renderBrowseItems(currentBrowseItems);
}

function setBrowseViewMode(mode) {
    if (mode !== 'thumb' && mode !== 'compact') return;
    browseViewMode = mode;
    localStorage.setItem('bilisummary-browse-view', mode);

    const toggle = document.getElementById('browseViewToggle');
    if (toggle) {
        toggle.querySelectorAll('.browse-view-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === mode);
        });
    }

    if (currentBrowseItems.length > 0) {
        renderBrowseItems(currentBrowseItems);
    }
}

function renderBrowseItems(items) {
    const list = document.getElementById('browseList');
    if (!list) return;
    if (!items || items.length === 0) {
        renderState(list, { type: 'empty', title: '暂无内容', message: '该分类下还没有可展示的总结' });
        return;
    }

    if (browseViewMode === 'compact') {
        list.innerHTML = `<div class="browse-compact-list">${items.map(item => renderBrowseCompactItem(item)).join('')}</div>`;
    } else {
        // Use the same card size/style as favorites for visual consistency.
        list.innerHTML = `<div class="video-grid">${items.map(item => renderBrowseCard(item)).join('')}</div>`;
    }
    lucide.createIcons({ nodes: [list] });
}

function summaryBadge(status) {
    const normalized = normalizeStatus(status);
    const badgeClassMap = {
        success: 'done',
        no_subtitle: 'no_subtitle',
        processing: 'summarizing',
        failed: 'none',
        pending: 'none',
        skipped: 'done',
    };
    return {
        badgeClass: badgeClassMap[normalized] || 'none',
        badgeText: statusText(normalized),
    };
}

function renderSharedThumbCard({
    id = '',
    dataAttrs = '',
    title = '',
    cover = '',
    duration = '',
    badgeId = '',
    badgeClass = 'done',
    badgeText = '成功',
    metaLeft = '',
    metaRight = '',
    actionButtonHtml = '',
    onClick = '',
}) {
    const safeCover = safeHttpUrl(cover || '');
    const coverHtml = safeCover
        ? `<img src="${escapeAttr(safeCover)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
        : `<div class="cover-fallback"><i data-lucide="image-off" class="lucide-icon" style="width:18px;height:18px;"></i></div>`;

    return `
        <div class="video-card" ${id ? `id="${id}"` : ''} ${dataAttrs} ${onClick ? `onclick="${onClick}"` : ''}>
            <div class="cover-wrapper">
                ${coverHtml}
                ${actionButtonHtml}
                ${duration ? `<span class="duration-badge">${duration}</span>` : ''}
                <span class="summary-badge ${badgeClass}" ${badgeId ? `id="${badgeId}"` : ''}>${badgeText}</span>
            </div>
            <div class="card-info">
                <div class="card-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
                <div class="card-meta">
                    <span class="upper-name">${escapeHtml(metaLeft)}</span>
                    <span class="play-count">${escapeHtml(metaRight)}</span>
                </div>
            </div>
        </div>
    `;
}

function renderSharedCompactItem({
    bvid = '',
    title = '',
    cover = '',
    meta = '',
    badgeId = '',
    badgeClass = 'done',
    badgeText = '成功',
    actionButtonHtml = '',
    onClick = '',
    extraClass = '',
}) {
    const safeCover = safeHttpUrl(cover || '');
    const coverHtml = safeCover
        ? `<img src="${escapeAttr(safeCover)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
        : `<div class="browse-compact-placeholder"><i data-lucide="image-off" class="lucide-icon" style="width:14px;height:14px;"></i></div>`;

    return `
        <div class="browse-compact-item ${extraClass}" data-bvid="${escapeAttr(bvid)}" ${onClick ? `onclick="${onClick}"` : ''}>
            <div class="browse-compact-cover">${coverHtml}</div>
            <div class="browse-compact-main">
                <div class="browse-compact-title" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
                <div class="browse-compact-meta">${escapeHtml(meta)}</div>
            </div>
            <span class="browse-inline-badge ${badgeClass}" ${badgeId ? `id="${badgeId}"` : ''}>${badgeText}</span>
            ${actionButtonHtml}
        </div>
    `;
}

function renderBrowseCard(item) {
    const { badgeClass, badgeText } = summaryBadge(item.no_subtitle ? 'no_subtitle' : 'done');
    const duration = formatDuration(item.duration || 0);
    const metaLeft = item.author_name || '本地总结';
    const metaRight = item.bvid || 'BV 未记录';

    return renderSharedThumbCard({
        dataAttrs: `data-path="${escapeAttr(encodePath(item.path))}"`,
        title: item.name || item.bvid || '未命名视频',
        cover: item.cover || '',
        duration,
        badgeClass,
        badgeText,
        metaLeft,
        metaRight,
        onClick: `openSummary('${encodePath(item.path)}')`,
    });
}

function renderBrowseCompactItem(item) {
    const { badgeClass, badgeText } = summaryBadge(item.no_subtitle ? 'no_subtitle' : 'done');
    const compactMeta = `${item.author_name || '本地总结'} · ${item.bvid || 'BV 未记录'}`;
    return renderSharedCompactItem({
        bvid: item.bvid || '',
        title: item.name || item.bvid || '未命名视频',
        cover: item.cover || '',
        meta: compactMeta,
        badgeClass,
        badgeText,
        onClick: `openSummary('${encodePath(item.path)}')`,
    });
}

setBrowseViewMode(browseViewMode);

// ---------------------------------------------------------------------------
// Reading View — shared helpers
// ---------------------------------------------------------------------------
function renderReadingActions(containerId, {
    bvid = '',
    isNoSub = false,
    showUnfav = false,
    enableRetry = false,
    enableAsr = false,
    showOpen = true,
} = {}) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const buttons = [];
    if (bvid && showOpen) {
        buttons.push(
            `<button class="action-btn action-btn-open" onclick="openExternal('https://www.bilibili.com/video/${bvid}')"><i data-lucide="external-link" class="lucide-icon" style="width:12px;height:12px;"></i> 打开 B站</button>`
        );
    }
    if (bvid && isNoSub && enableRetry) {
        buttons.push(
            `<button class="action-btn action-btn-retry" onclick="retrySummarize('${bvid}')"><i data-lucide="refresh-cw" class="lucide-icon" style="width:12px;height:12px;"></i> 重试字幕总结</button>`
        );
    }
    if (bvid && isNoSub && enableAsr) {
        buttons.push(
            `<button class="action-btn action-btn-asr" onclick="asrSummarize('${bvid}')"><i data-lucide="mic" class="lucide-icon" style="width:12px;height:12px;"></i> 语音识别总结</button>`
        );
    }
    if (bvid && showUnfav) {
        buttons.push(
            `<button class="action-btn action-btn-unfav" onclick="unfavoriteFromReading('${bvid}')"><i data-lucide="heart-off" class="lucide-icon" style="width:12px;height:12px;"></i> 取消收藏</button>`
        );
    }

    container.innerHTML = buttons.join('');
    if (buttons.length) {
        lucide.createIcons({ nodes: [container] });
    }
}

function setupExternalLinks(container) {
    container.querySelectorAll('a').forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            openExternal(a.href);
        });
    });
}

async function openSummary(encodedPath) {
    const apiPath = encodedPath;
    const list = document.getElementById('browseList');
    const readingView = document.getElementById('readingView');
    const readingContent = document.getElementById('readingContent');

    try {
        const res = await fetch(`/api/summary/${apiPath}`);
        const data = await res.json();
        if (data.error) { await showAlert(data.error, '加载失败'); return; }
        list.style.display = 'none';
        readingView.classList.add('active');
        readingContent.innerHTML = renderMarkdown(data.content);

        const bvidMatch = data.content.match(/\*\*BV号\*\*:\s*(BV\w+)/);
        const bvid = bvidMatch ? bvidMatch[1] : '';
        const isNoSub = data.content.includes('无法获取字幕');
        // Browse page keeps actions aligned with favorites, but retry/asr are disabled here.
        renderReadingActions('readingActions', {
            bvid,
            isNoSub,
            showOpen: true,
            showUnfav: false,
            enableRetry: false,
            enableAsr: false,
        });

        setupExternalLinks(readingContent);
    } catch (err) { await showAlert('加载失败: ' + err.message, '加载失败'); }
}

function closeReading() {
    document.getElementById('readingView').classList.remove('active');
    document.getElementById('browseList').style.display = 'block';
}

// ---------------------------------------------------------------------------
// Markdown → HTML
// ---------------------------------------------------------------------------
function renderMarkdown(md) {
    const escaped = escapeHtml(md || '');

    const withMarkdownLinks = escaped.replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        (_, text, rawUrl) => {
            const safeUrl = safeHttpUrl(rawUrl);
            if (!safeUrl) return text;
            return `<a href="${escapeAttr(safeUrl)}" class="ext-link" target="_blank" rel="noopener noreferrer">${text}</a>`;
        }
    );

    const withLinks = withMarkdownLinks
        .split(/(<a [^>]+>.*?<\/a>)/g)
        .map(part => {
            if (part.startsWith('<a ')) return part;
            return part.replace(/(^|[\s(])(https?:\/\/[^\s<")]+)/g, (match, prefix, rawUrl) => {
                const safeUrl = safeHttpUrl(rawUrl);
                if (!safeUrl) return match;
                return `${prefix}<a href="${escapeAttr(safeUrl)}" class="ext-link" target="_blank" rel="noopener noreferrer">${escapeHtml(safeUrl)}</a>`;
            });
        })
        .join('');

    return withLinks
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/^---$/gm, '<hr>')
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
                addLog(logEl, `处理中: 共 ${d.total} 个视频 (并发 ${d.concurrency}, 模型 ${d.model})`, 'info');
                break;
            case 'info':
                addLog(logEl, d.message, 'info');
                break;
            case 'processing':
                addLog(logEl, `处理中: ${d.title} — ${d.step}`, '');
                break;
            case 'skip':
                completed++;
                updateProgress(progressBar, statsEl, completed, total);
                addLog(logEl, `已跳过: ${d.title}`, 'skip');
                if (d.path) completedPaths.push({ title: d.title, path: d.path, status: 'skipped' });
                break;
            case 'completed':
                completed++;
                updateProgress(progressBar, statsEl, completed, total);
                if (d.status === 'no_subtitle') {
                    addLog(logEl, `无字幕: ${d.title}`, 'warning');
                } else {
                    addLog(logEl, `成功: ${d.title} (${d.duration_sec}s)`, 'success');
                }
                if (d.path) completedPaths.push({ title: d.title, path: d.path, status: d.status, duration: d.duration_sec });
                break;
            case 'error':
                completed++;
                updateProgress(progressBar, statsEl, completed, total);
                addLog(logEl, `失败: ${d.title || ''} ${d.message || ''}`.trim(), 'error');
                break;
            case 'done':
                isDone = true;
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i data-lucide="play" class="lucide-icon" style="width:14px;height:14px;"></i> 开始总结';
                lucide.createIcons({ nodes: [submitBtn] });
                addLog(logEl, `完成: 成功 ${d.success} | 已跳过 ${d.skipped} | 无字幕 ${d.no_subtitle} | 失败 ${d.errors}`, 'info');
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
            addLog(logEl, `连接中断，正在重连 (${retryCount}/${MAX_RETRIES})`, 'warning');
            await new Promise(r => setTimeout(r, 2000));
            return connectSSE();
        }

        if (!isDone) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i data-lucide="play" class="lucide-icon" style="width:14px;height:14px;"></i> 开始总结';
            lucide.createIcons({ nodes: [submitBtn] });
            addLog(logEl, '连接中断，可重新点击开始总结', 'error');
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
        const badgeText = statusText(r.status);

        const card = document.createElement('div');
        card.className = 'result-card';
        if (index === 0) card.classList.add('expanded');
        card.innerHTML = `
            <div class="result-card-header" onclick="toggleResultCard(this)">
                <span class="title">${escapeHtml(r.title)}</span>
                <span class="badge ${badgeClass}">${badgeText}</span>
                <span class="chevron"><i data-lucide="chevron-right" class="lucide-icon"></i></span>
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

function escapeAttr(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function safeHttpUrl(rawUrl) {
    try {
        const parsed = new URL(rawUrl);
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return null;
        }
        return parsed.href;
    } catch {
        return null;
    }
}

// ---------------------------------------------------------------------------
// Submit Handlers
// ---------------------------------------------------------------------------
async function submitURL() {
    const text = document.getElementById('urlInput').value.trim();
    if (!text) return;
    const urls = text.split('\n').map(u => u.trim()).filter(Boolean);
    const concurrency = parseInt(document.getElementById('urlConcurrency').value) || 12;
    try {
        const res = await fetch('/api/summarize/url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls, concurrency })
        });
        const data = await res.json();
        if (data.error) { await showAlert(data.error, '请求失败'); return; }
        listenProgress(data.task_id, 'url');
    } catch (err) { await showAlert('请求失败: ' + err.message, '请求失败'); }
}

async function submitUser() {
    const userVal = document.getElementById('userInput').value.trim();
    if (!userVal) return;
    const count = parseInt(document.getElementById('userCount').value) || 50;
    const concurrency = parseInt(document.getElementById('userConcurrency').value) || 12;
    try {
        const res = await fetch('/api/summarize/user', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user: userVal, count, concurrency })
        });
        const data = await res.json();
        if (data.error) { await showAlert(data.error, '请求失败'); return; }
        listenProgress(data.task_id, 'user');
    } catch (err) { await showAlert('请求失败: ' + err.message, '请求失败'); }
}

// ---------------------------------------------------------------------------
// Favorites Browser
// ---------------------------------------------------------------------------
let currentFavId = null;
let currentFavPage = 1;
let favHasMore = false;
const favVideoData = new Map(); // bvid -> { summaryPath, title, ... }
let pendingSummarizeBvids = [];
let activeUndoToast = null;

async function restoreFavoriteVideo(favId, bvid) {
    const res = await fetch(`/api/favorites/${favId}/video/${bvid}/restore`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok || data.error) {
        throw new Error(data.error || `HTTP ${res.status}`);
    }
    return data;
}

function notifyUnfavoriteUndo({ favId, bvid, title }) {
    if (activeUndoToast?.close) activeUndoToast.close();

    activeUndoToast = showToast({
        title: '已取消收藏',
        message: title ? `已移除: ${title}` : `已移除: ${bvid}`,
        tone: 'info',
        actionText: '撤销',
        duration: 7000,
        onAction: async () => {
            try {
                await restoreFavoriteVideo(favId, bvid);
                showToast({
                    title: '恢复成功',
                    message: title ? `已恢复: ${title}` : `已恢复: ${bvid}`,
                    tone: 'success',
                    duration: 2600,
                });
                if (currentFavId === favId) {
                    await loadFavoriteVideos(favId, 1, false);
                }
            } catch (err) {
                await showAlert(`恢复收藏失败: ${err.message}`, '操作失败');
            }
        },
    });
}

async function loadFavoriteFolders() {
    const container = document.getElementById('sidebarFavorites');
    if (!container) return;

    try {
        const res = await fetch('/api/favorites/list');
        const data = await res.json();
        if (data.error) {
            renderState(container, { type: 'empty', title: '未登录', message: '请先登录 Bilibili 以加载收藏' });
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
                    <span class="folder-name"><i data-lucide="folder" class="lucide-icon"></i> ${escapeHtml(defaultFolder.title)}</span>
                    <span class="folder-count">${defaultFolder.count}</span>
                </div>`;
        }

        // Other folders in collapsible section
        if (otherFolders.length > 0) {
            html += `
                <div class="fav-folder-toggle" onclick="toggleFavFolders()">
                    <span class="toggle-arrow" id="favFoldArrow"><i data-lucide="chevron-right" class="lucide-icon"></i></span>
                    <span>其他收藏 (${otherFolders.length})</span>
                </div>
                <div class="fav-folder-list collapsed" id="favFolderList">
                    ${otherFolders.map(f => `
                        <div class="fav-folder-item" data-fav-id="${f.id}" data-fav-title="${escapeHtml(f.title)}">
                            <span class="folder-name"><i data-lucide="folder" class="lucide-icon"></i> ${escapeHtml(f.title)}</span>
                            <span class="folder-count">${f.count}</span>
                        </div>
                    `).join('')}
                </div>`;
        }

        container.innerHTML = html;
        lucide.createIcons({ nodes: [container] });

        // Event delegation for folder clicks
        container.addEventListener('click', (e) => {
            const item = e.target.closest('.fav-folder-item');
            if (!item) return;
            const favId = parseInt(item.dataset.favId);
            const title = item.dataset.favTitle;
            selectFavoriteFolder(favId, title);
        });

    } catch (err) {
        renderState(container, {
            type: 'error',
            title: '收藏加载失败',
            message: '请检查网络后重试',
            actionText: '重试',
            onAction: () => loadFavoriteFolders(),
        });
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
        const card = unfavBtn.closest('[data-bvid]');
        const bvid = card.dataset.bvid;
        unfavoriteVideo(bvid, card);
        return;
    }

    const card = e.target.closest('.video-card, .fav-compact-item');
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
    pendingSummarizeBvids = [];
    currentFavVideos = [];

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
    renderState(grid, { type: 'loading', title: '加载中', message: '正在获取收藏视频' });
    grid.style.display = '';
    document.getElementById('favAutoProgress').innerHTML = '';
    document.getElementById('favReadingView').classList.remove('active');
    document.getElementById('favLoadMore').style.display = 'none';
    setFavViewMode(favViewMode);

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
            renderState(grid, {
                type: 'error',
                title: '收藏加载失败',
                message: data.error,
                actionText: '重试',
                onAction: () => loadFavoriteVideos(favId, page, append),
            });
            return;
        }

        const videos = data.videos || [];
        currentFavPage = data.page;
        favHasMore = data.has_more;
        currentFavVideos = append ? [...currentFavVideos, ...videos] : videos;

        document.getElementById('favBrowseSubtitle').textContent = `共 ${currentFavVideos.length} 个视频 (第 ${page} 页)`;
        loadMore.style.display = favHasMore ? '' : 'none';

        renderFavoriteItems(currentFavVideos);

        // Prepare manual summarize action for unsummarized videos.
        const unsummarized = videos.filter(v => v.summary_status === 'none').map(v => v.bvid);
        if (!append) {
            pendingSummarizeBvids = [];
        }
        if (unsummarized.length > 0) {
            pendingSummarizeBvids = Array.from(new Set([...pendingSummarizeBvids, ...unsummarized]));
        }
        renderPendingSummarizeAction();

    } catch (err) {
        document.getElementById('favBrowseSubtitle').textContent = '加载失败: ' + err.message;
        renderState(grid, {
            type: 'error',
            title: '收藏加载失败',
            message: err.message,
            actionText: '重试',
            onAction: () => loadFavoriteVideos(favId, page, append),
        });
    }
}

function setFavViewMode(mode) {
    if (mode !== 'thumb' && mode !== 'compact') return;
    favViewMode = mode;
    localStorage.setItem('bilisummary-fav-view', mode);

    const toggle = document.getElementById('favViewToggle');
    if (toggle) {
        toggle.querySelectorAll('.fav-view-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.view === mode);
        });
    }

    if (currentFavVideos.length > 0) {
        renderFavoriteItems(currentFavVideos);
    }
}

function renderFavoriteItems(videos) {
    const grid = document.getElementById('favVideoGrid');
    if (!grid) return;
    if (!videos || videos.length === 0) {
        renderState(grid, { type: 'empty', title: '暂无视频', message: '当前收藏暂无可展示内容' });
        return;
    }

    if (favViewMode === 'compact') {
        grid.className = 'browse-compact-list';
        grid.innerHTML = videos.map(v => renderFavoriteCompactItem(v)).join('');
    } else {
        grid.className = 'video-grid';
        grid.innerHTML = videos.map(v => renderVideoCard(v)).join('');
    }
    lucide.createIcons({ nodes: [grid] });
}

function renderVideoCard(v) {
    const durationStr = formatDuration(v.duration);
    const playStr = formatPlayCount(v.play_count);
    const { badgeClass, badgeText } = summaryBadge(v.summary_status);

    // Store video data in JS Map for reliable click handling
    favVideoData.set(v.bvid, {
        summaryPath: v.summary_path || null,
        title: v.title,
        upper: v.upper || '',
        upperMid: v.upper_mid || 0,
    });

    return renderSharedThumbCard({
        id: `card-${v.bvid}`,
        dataAttrs: `data-bvid="${escapeAttr(v.bvid)}"`,
        title: v.title,
        cover: v.cover,
        duration: durationStr,
        badgeId: `badge-${v.bvid}`,
        badgeClass,
        badgeText,
        metaLeft: v.upper || '',
        metaRight: `${playStr} 播放`,
        actionButtonHtml: `<button class="unfav-btn" title="取消收藏">✕</button>`,
    });
}

function renderFavoriteCompactItem(v) {
    const { badgeClass, badgeText } = summaryBadge(v.summary_status);
    const compactMeta = `${v.upper || '未知UP'} · ${formatPlayCount(v.play_count)} 播放`;
    return renderSharedCompactItem({
        bvid: v.bvid,
        title: v.title,
        cover: v.cover,
        meta: compactMeta,
        badgeId: `badge-${v.bvid}`,
        badgeClass,
        badgeText,
        actionButtonHtml: `<button class="compact-unfav-btn unfav-btn" title="取消收藏">✕</button>`,
        extraClass: 'fav-compact-item',
    });
}

setFavViewMode(favViewMode);

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

function renderPendingSummarizeAction() {
    const progressEl = document.getElementById('favAutoProgress');
    if (!progressEl) return;

    if (pendingSummarizeBvids.length === 0) {
        progressEl.innerHTML = '';
        return;
    }

    progressEl.innerHTML = `
        <div>发现 ${pendingSummarizeBvids.length} 个未总结视频</div>
        <button class="btn-secondary" style="margin-top:8px;padding:6px 12px;font-size:12px;" onclick="startPendingSummarize()">
            <i data-lucide="play" class="lucide-icon" style="width:12px;height:12px;"></i> 总结未总结视频
        </button>
    `;
    lucide.createIcons({ nodes: [progressEl] });
}

function startPendingSummarize() {
    if (!pendingSummarizeBvids.length) return;
    autoSummarizeVideos([...pendingSummarizeBvids]);
}

async function autoSummarizeVideos(bvids) {
    const targets = Array.from(new Set(bvids)).filter(Boolean);
    if (!targets.length) {
        renderPendingSummarizeAction();
        return;
    }

    const progressEl = document.getElementById('favAutoProgress');
    progressEl.innerHTML = `
        <div>处理中: 正在总结 ${targets.length} 个视频</div>
        <div class="mini-log" id="favMiniLog"></div>
    `;

    // Mark cards as summarizing
    targets.forEach(bvid => {
        const badge = document.getElementById(`badge-${bvid}`);
        if (badge) {
            badge.className = 'summary-badge summarizing';
            badge.textContent = statusText('processing');
        }
    });

    try {
        const res = await fetch('/api/favorites/summarize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bvids: targets, output_subdir: 'favorites' })
        });
        const data = await res.json();
        if (!data.task_id) {
            renderPendingSummarizeAction();
            return;
        }

        // Listen to SSE for auto-summarize progress
        listenAutoSummarize(data.task_id, progressEl);
    } catch (err) {
        renderState(progressEl, { type: 'error', title: '自动总结失败', message: err.message });
        setTimeout(() => renderPendingSummarizeAction(), 2000);
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
                        pendingSummarizeBvids = pendingSummarizeBvids.filter(b => b !== d.bvid);
                        const badge = document.getElementById(`badge-${d.bvid}`);
                        if (badge) {
                            if (d.status === 'no_subtitle') {
                                badge.className = 'summary-badge no_subtitle';
                                badge.textContent = statusText('no_subtitle');
                            } else {
                                badge.className = 'summary-badge done';
                                badge.textContent = statusText('success');
                                // Update JS Map for event delegation
                                const vdata = favVideoData.get(d.bvid);
                                if (vdata && d.path) {
                                    vdata.summaryPath = d.path;
                                }
                            }
                        }
                        if (miniLog) {
                            miniLog.innerHTML += `<div class="log-line">${statusText(d.status)}: ${escapeHtml(d.title)}</div>`;
                            miniLog.scrollTop = miniLog.scrollHeight;
                        }
                    } else if (eventType === 'skip') {
                        pendingSummarizeBvids = pendingSummarizeBvids.filter(b => b !== d.bvid);
                    } else if (eventType === 'error') {
                        const badge = document.getElementById(`badge-${d.bvid || ''}`);
                        if (badge) {
                            badge.className = 'summary-badge none';
                            badge.textContent = statusText('failed');
                        }
                    } else if (eventType === 'done') {
                        isDone = true;
                        const remaining = pendingSummarizeBvids.length;
                        if (remaining > 0) {
                            progressEl.innerHTML = `<div style="color:var(--warning);">已完成，仍有 ${remaining} 个视频可重试</div>`;
                        } else {
                            progressEl.innerHTML = `<div style="color:var(--success);">处理完成</div>`;
                        }
                        setTimeout(() => renderPendingSummarizeAction(), 2200);
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

    renderState(readingContent, { type: 'loading', title: '加载中', message: '正在读取总结内容' });
    grid.style.display = 'none';
    loadMore.style.display = 'none';
    document.getElementById('favAutoProgress').style.display = 'none';
    readingView.classList.add('active');

    try {
        // Encode path segments for URL (preserve /)
        const encodedPath = path.split('/').map(s => encodeURIComponent(s)).join('/');
        const res = await fetch(`/api/summary/${encodedPath}`);
        if (!res.ok) {
            renderState(readingContent, { type: 'error', title: '加载失败', message: `HTTP ${res.status}: 无法加载总结` });
            return;
        }
        const data = await res.json();
        if (data.content) {
            const isNoSub = data.content.includes('无法获取字幕');
            renderReadingActions('favReadingActions', {
                bvid,
                isNoSub,
                showOpen: true,
                showUnfav: true,
                enableRetry: true,
                enableAsr: true,
            });

            readingContent.innerHTML = renderMarkdown(data.content);

            // Inject author line if missing from existing summaries
            const vdata = favVideoData.get(bvid);
            if (vdata && vdata.upper && !data.content.includes('**作者**:')) {
                const authorLink = vdata.upperMid
                    ? `<strong>作者</strong>: <a href="https://space.bilibili.com/${vdata.upperMid}" target="_blank">${escapeHtml(vdata.upper)}</a>`
                    : `<strong>作者</strong>: ${escapeHtml(vdata.upper)}`;
                const authorEl = document.createElement('p');
                authorEl.innerHTML = authorLink;
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
            setupExternalLinks(readingContent);
        } else {
            renderState(readingContent, { type: 'empty', title: '暂无内容', message: '总结内容为空' });
        }
    } catch (err) {
        renderState(readingContent, { type: 'error', title: '加载失败', message: err.message });
    }
}

function closeFavReading() {
    document.getElementById('favReadingView').classList.remove('active');
    document.getElementById('favVideoGrid').style.display = '';
    document.getElementById('favAutoProgress').style.display = '';
    document.getElementById('favLoadMore').style.display = favHasMore ? '' : 'none';
}

async function retrySummarize(bvid) {
    const readingContent = document.getElementById('favReadingContent');
    renderState(readingContent, { type: 'loading', title: '处理中', message: '正在重新获取字幕并生成总结' });

    try {
        const res = await fetch(`/api/retry/${bvid}`, { method: 'POST' });
        const data = await res.json();
        if (data.error) {
            renderState(readingContent, { type: 'error', title: '重试失败', message: data.error });
            return;
        }

        const taskId = data.task_id;
        renderState(readingContent, { type: 'loading', title: '处理中', message: '正在获取字幕' });

        // Listen to SSE for progress (server sends named events)
        const evtSrc = new EventSource(`/api/progress/${taskId}`);

        evtSrc.addEventListener('processing', (e) => {
            try {
                const d = JSON.parse(e.data);
                renderState(readingContent, { type: 'loading', title: '处理中', message: d.step || '处理中' });
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
                        badge.textContent = statusText('no_subtitle');
                    }
                    renderState(readingContent, { type: 'error', title: '无字幕', message: '仍然无法获取字幕，可稍后再试' });
                } else {
                    if (badge) {
                        badge.className = 'summary-badge done';
                        badge.textContent = statusText('success');
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
                renderState(readingContent, { type: 'error', title: '重试失败', message: d.message || '未知错误' });
            } catch (_) {
                renderState(readingContent, { type: 'error', title: '连接中断', message: '请稍后重试' });
            }
        });

        evtSrc.addEventListener('done', () => {
            evtSrc.close();
        });

        evtSrc.onerror = () => {
            evtSrc.close();
        };
    } catch (err) {
        renderState(readingContent, { type: 'error', title: '重试失败', message: err.message });
    }
}

async function asrSummarize(bvid) {
    // Create toast notification
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = `
        <div class="toast-title">语音识别总结</div>
        <div class="toast-message">处理中: 准备中...</div>
    `;
    container.appendChild(toast);
    const msgEl = toast.querySelector('.toast-message');

    try {
        const res = await fetch(`/api/asr-summarize/${bvid}`, { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            msgEl.textContent = `失败: ${err.error || '未知错误'}`;
            toast.classList.add('toast-error');
            setTimeout(() => { toast.classList.add('toast-fadeout'); setTimeout(() => toast.remove(), 300); }, 5000);
            return;
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { value, done: streamDone } = await reader.read();
            if (streamDone) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                try {
                    const d = JSON.parse(line.slice(6));

                    if (d.step === 'error') {
                        msgEl.textContent = `失败: ${d.message}`;
                        toast.classList.add('toast-error');
                        setTimeout(() => { toast.classList.add('toast-fadeout'); setTimeout(() => toast.remove(), 300); }, 8000);
                        return;
                    }

                    if (d.step === 'done') {
                        msgEl.textContent = `成功: 总结完成（${d.llm_time}s）`;
                        toast.classList.add('toast-done');
                        // Update badge
                        const badge = document.getElementById(`badge-${bvid}`);
                        if (badge) {
                            badge.className = 'summary-badge done';
                            badge.textContent = statusText('success');
                        }
                        const vdata = favVideoData.get(bvid);
                        if (vdata && d.path) {
                            vdata.summaryPath = d.path;
                        }
                        // Auto-open the summary if user is still on this video's reading view
                        const readingView = document.getElementById('favReadingView');
                        if (readingView && readingView.classList.contains('active')) {
                            showVideoSummary(bvid, d.path);
                        }
                        setTimeout(() => { toast.classList.add('toast-fadeout'); setTimeout(() => toast.remove(), 300); }, 5000);
                        return;
                    }

                    // Progress steps
                    msgEl.textContent = `处理中: ${d.message}`;
                } catch (_) { }
            }
        }
    } catch (err) {
        msgEl.textContent = `失败: ${err.message}`;
        toast.classList.add('toast-error');
        setTimeout(() => { toast.classList.add('toast-fadeout'); setTimeout(() => toast.remove(), 300); }, 5000);
    }
}

async function unfavoriteVideo(bvid, cardEl) {
    if (!currentFavId) return;
    const removedVideo = favVideoData.get(bvid) || { title: bvid };
    const favId = currentFavId;

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
            await showAlert('取消收藏失败: ' + data.error, '操作失败');
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
        notifyUnfavoriteUndo({ favId, bvid, title: removedVideo.title });
    } catch (err) {
        await showAlert('取消收藏失败: ' + err.message, '操作失败');
        if (cardEl) {
            cardEl.style.opacity = '';
            cardEl.style.pointerEvents = '';
        }
    }
}

async function unfavoriteFromReading(bvid) {
    if (!currentFavId) return;
    const removedVideo = favVideoData.get(bvid) || { title: bvid };
    const favId = currentFavId;

    try {
        const res = await fetch(`/api/favorites/${currentFavId}/video/${bvid}`, {
            method: 'DELETE'
        });
        const data = await res.json();
        if (data.error) {
            await showAlert('取消收藏失败: ' + data.error, '操作失败');
            return;
        }
        // Remove card from grid
        const card = document.getElementById(`card-${bvid}`);
        if (card) card.remove();
        favVideoData.delete(bvid);
        // Go back to grid
        closeFavReading();
        notifyUnfavoriteUndo({ favId, bvid, title: removedVideo.title });
    } catch (err) {
        await showAlert('取消收藏失败: ' + err.message, '操作失败');
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
        document.getElementById('settingsModel').value = data.default_model || '';
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
    const defaultModel = document.getElementById('settingsModel').value.trim();

    statusEl.style.color = 'var(--text-muted)';
    statusEl.textContent = '保存中...';

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                base_url: baseUrl,
                auth_token: token,
                default_model: defaultModel,
            })
        });
        const data = await res.json();
        if (data.success) {
            statusEl.style.color = 'var(--success)';
            statusEl.textContent = '保存成功';
            // Reload to show masked token
            setTimeout(() => loadSettings(), 500);
        } else {
            statusEl.style.color = 'var(--error)';
            statusEl.textContent = '保存失败: ' + (data.error || '');
        }
    } catch (err) {
        statusEl.style.color = 'var(--error)';
        statusEl.textContent = '保存失败: ' + err.message;
    }
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
}

async function loadModels() {
    const listEl = document.getElementById('modelList');
    renderState(listEl, { type: 'loading', title: '加载中', message: '正在获取模型列表' });

    try {
        const res = await fetch('/api/models');
        if (!res.ok) {
            const err = await res.json();
            renderState(listEl, { type: 'error', title: '加载失败', message: err.error || '加载失败' });
            return;
        }
        const data = await res.json();
        const models = data.models || [];
        const current = data.current || '';

        if (models.length === 0) {
            renderState(listEl, { type: 'empty', title: '没有可用模型', message: '请检查 API 配置' });
            return;
        }

        listEl.innerHTML = models.map(m => {
            const isActive = m.id === current;
            return `<div class="model-item${isActive ? ' active' : ''}" onclick="selectModel('${m.id}', this)">
                <div class="model-name">${m.id}</div>
                <div class="model-owner">${m.owned_by || ''}</div>
                ${isActive ? '<span class="model-check">当前</span>' : ''}
            </div>`;
        }).join('');

    } catch (err) {
        renderState(listEl, { type: 'error', title: '加载失败', message: err.message });
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
    el.insertAdjacentHTML('beforeend', '<span class="model-check">当前</span>');

    // Save to backend
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ default_model: modelId })
        });
        // Update the manual input field
        document.getElementById('settingsModel').value = modelId;
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
