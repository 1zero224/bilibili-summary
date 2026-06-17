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
initSidebarState();

function initSidebarState() {
    const collapsed = localStorage.getItem('bilisummary-sidebar-collapsed') === 'true';
    document.querySelector('.app')?.classList.toggle('sidebar-collapsed', collapsed);
    updateSidebarToggleIcon(collapsed);
}

function toggleSidebar() {
    const app = document.querySelector('.app');
    if (!app) return;
    const collapsed = !app.classList.contains('sidebar-collapsed');
    app.classList.toggle('sidebar-collapsed', collapsed);
    localStorage.setItem('bilisummary-sidebar-collapsed', String(collapsed));
    updateSidebarToggleIcon(collapsed);
}

function updateSidebarToggleIcon(collapsed) {
    const btn = document.getElementById('sidebarToggle');
    if (!btn) return;
    btn.innerHTML = `<i data-lucide="${collapsed ? 'panel-left-open' : 'panel-left-close'}" class="lucide-icon" id="sidebarToggleIcon"></i>`;
    btn.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
    if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [btn] });
}

// Cache for summaries data
let summariesData = null;
let localFolders = [];
let browseViewMode = localStorage.getItem('bilisummary-browse-view') || 'thumb';
let currentBrowseItems = [];
let currentBrowseType = '';
let currentBrowseFolder = '';
let browseSelectionMode = false;
const selectedBrowsePaths = new Set();
let browseHeaderBeforeReading = null;
let favViewMode = localStorage.getItem('bilisummary-fav-view') || 'thumb';
let currentFavVideos = [];
let favHeaderBeforeReading = null;
let urlTaskLogPage = 1;
let urlTaskLogRefreshTimer = null;
const urlTaskLogCache = new Map();
let currentUserPage = 1;
let userHasMore = false;
let currentUser = '';
let currentUserUid = null;
let currentUserName = '';
let currentUserVideos = [];
const userVideoData = new Map();
const selectedUserBvids = new Set();
const SUMMARY_DETAIL_RESIZE_KEY = 'bilisummary-detail-left-percent-v2';
let summaryDetailResizePercent = Number(localStorage.getItem(SUMMARY_DETAIL_RESIZE_KEY)) || 66.666;
const DEFAULT_LOCAL_FOLDER = '默认文件夹';

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
    document.querySelectorAll('.nav-item, .nav-parent, .nav-child').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.fav-folder-item').forEach(n => n.classList.remove('active'));
    // Set active on clicked element
    if (navEl) navEl.classList.add('active');
    // Show page
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(pageId).classList.add('active');
    updateGlobalBackButton();
}

function updateGlobalBackButton() {
    const btn = document.getElementById('globalBackBtn');
    const browseReading = document.getElementById('readingView')?.classList.contains('active');
    const favReading = document.getElementById('favReadingView')?.classList.contains('active');
    const visible = !!(browseReading || favReading);
    if (btn) {
        btn.classList.toggle('active', visible);
        btn.setAttribute('aria-hidden', visible ? 'false' : 'true');
    }

    updateBrowseHeaderActions();
    document.getElementById('favViewToggle')?.classList.toggle('is-hidden', !!favReading);
}

function handleGlobalBack() {
    const browseReading = document.getElementById('readingView')?.classList.contains('active');
    const favReading = document.getElementById('favReadingView')?.classList.contains('active');
    if (favReading) {
        closeFavReading();
    } else if (browseReading) {
        closeReading();
    }
}

const globalBackBtn = document.getElementById('globalBackBtn');
if (globalBackBtn) {
    globalBackBtn.addEventListener('click', handleGlobalBack);
}
updateGlobalBackButton();

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
            loginBtn.classList.add('is-hidden');
            logoutBtn.classList.remove('is-hidden');
        } else {
            dot.className = 'status-dot offline';
            text.textContent = '未登录 Bilibili';
            loginBtn.classList.remove('is-hidden');
            logoutBtn.classList.add('is-hidden');
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

function showTextPrompt({
    title = '输入',
    message = '',
    placeholder = '',
    confirmText = '确定',
    cancelText = '取消',
} = {}) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay active';
        overlay.innerHTML = `
            <div class="modal dialog-modal" role="dialog" aria-modal="true" aria-labelledby="promptTitle">
                <div class="modal-header">
                    <h3 id="promptTitle">${escapeHtml(title)}</h3>
                    <button type="button" class="modal-close" data-action="close" aria-label="关闭">✕</button>
                </div>
                <div class="modal-body modal-body-left">
                    ${message ? `<p class="modal-message">${escapeHtml(message)}</p>` : ''}
                    <input class="input prompt-input" type="text" placeholder="${escapeAttr(placeholder)}">
                    <div class="modal-actions">
                        <button type="button" class="btn btn-secondary" data-action="cancel">${escapeHtml(cancelText)}</button>
                        <button type="button" class="btn btn-primary" data-action="confirm">${escapeHtml(confirmText)}</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        const input = overlay.querySelector('.prompt-input');
        setTimeout(() => input?.focus(), 0);

        const closeAndResolve = (result) => {
            overlay.remove();
            document.removeEventListener('keydown', onKeyDown);
            resolve(result);
        };

        const onKeyDown = (e) => {
            if (e.key === 'Escape') closeAndResolve(null);
            if (e.key === 'Enter') closeAndResolve(input?.value.trim() || null);
        };
        document.addEventListener('keydown', onKeyDown);

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeAndResolve(null);
            if (e.target.closest('[data-action="close"]')) closeAndResolve(null);
            if (e.target.closest('[data-action="cancel"]')) closeAndResolve(null);
            if (e.target.closest('[data-action="confirm"]')) closeAndResolve(input?.value.trim() || null);
        });
    });
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
        localFolders = summariesData.folders || [];
        updateFolderSelects();

        const allCat = summariesData.categories?.find(c => c.type === 'all') || {
            type: 'all', label: '所有视频', icon: 'library', count: 0, items: []
        };
        const folderCategories = getFolderCategories(summariesData);

        let html = `
            <div class="nav-parent ${currentBrowseType === 'all' ? 'active' : ''}" onclick="showCategory('all', this)" data-type="all" title="${escapeAttr(allCat.label)}">
                <span class="icon"><i data-lucide="${allCat.icon}" class="lucide-icon"></i></span>
                <span class="label">${escapeHtml(allCat.label)}</span>
                <span class="count">${allCat.count}</span>
                <span class="nav-folder-action-slot"><span class="nav-folder-delete-placeholder"></span></span>
            </div>`;

        for (const folder of folderCategories) {
            const folderLabel = folder.label || folder.folder;
            const canDelete = folder.folder !== DEFAULT_LOCAL_FOLDER;
            html += `
                <div class="nav-parent ${currentBrowseType === 'folder' && currentBrowseFolder === folder.folder ? 'active' : ''}"
                    data-type="folder" data-folder="${escapeAttr(folder.folder)}" title="${escapeAttr(folderLabel)}">
                    <span class="icon"><i data-lucide="${folder.icon || 'folder'}" class="lucide-icon"></i></span>
                    <span class="label">${escapeHtml(folderLabel)}</span>
                    <span class="count">${folder.count || 0}</span>
                    <span class="nav-folder-action-slot">
                    ${canDelete ? `
                        <button class="nav-folder-delete" type="button"
                            title="删除文件夹"
                            aria-label="删除文件夹 ${escapeAttr(folderLabel)}"
                            data-folder="${escapeAttr(folder.folder)}"
                            data-label="${escapeAttr(folderLabel)}"
                            data-count="${Number(folder.count || 0)}">
                            <i data-lucide="trash-2" class="lucide-icon icon-xs"></i>
                        </button>
                    ` : '<span class="nav-folder-delete-placeholder"></span>'}
                    </span>
                </div>`;
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

document.getElementById('sidebarBrowse')?.addEventListener('click', (event) => {
    const deleteBtn = event.target.closest('.nav-folder-delete');
    if (deleteBtn) {
        event.preventDefault();
        event.stopPropagation();
        deleteLocalFolder(deleteBtn.dataset.folder || '', deleteBtn.dataset.label || '', Number(deleteBtn.dataset.count || 0));
        return;
    }

    const folderNav = event.target.closest('.nav-parent[data-type="folder"]');
    if (!folderNav) return;
    showFolderVideos(folderNav.dataset.folder || '', folderNav);
});

function toggleParent(el) {
    el.classList.toggle('expanded');
    const children = el.nextElementSibling;
    if (children?.classList.contains('nav-children')) {
        children.style.display = el.classList.contains('expanded') ? 'block' : '';
    }
}

function getFolderCategories(data) {
    const categories = data?.categories || [];
    const flatFolders = categories.filter(c => c.type === 'folder');
    if (flatFolders.length) return flatFolders;

    const legacyFolderCategory = categories.find(c => c.type === 'folders');
    const legacyFolders = (legacyFolderCategory?.groups || []).map(group => ({
        type: 'folder',
        label: group.display_name || group.name,
        icon: 'folder',
        count: group.count || 0,
        folder: group.name,
        items: group.items || [],
    }));
    if (legacyFolders.length) return legacyFolders;

    return (data?.folders || []).map(folder => ({
        type: 'folder',
        label: folder.display_name || folder.name,
        icon: 'folder',
        count: folder.count || 0,
        folder: folder.name,
        items: folder.items || [],
    }));
}

// ---------------------------------------------------------------------------
// Browse: Show category items (standalone / favorites)
// ---------------------------------------------------------------------------
function showCategory(type, navEl) {
    if (!summariesData) return;
    const cat = summariesData.categories.find(c => c.type === type);
    if (!cat) return;

    // Update active state
    document.querySelectorAll('.nav-item, .nav-parent, .nav-child').forEach(n => n.classList.remove('active'));
    if (navEl) navEl.classList.add('active');

    // Switch to browse page
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('browse-page').classList.add('active');

    // Update header
    document.getElementById('browseTitle').innerHTML = `<i data-lucide="${cat.icon}" class="lucide-icon"></i> ${escapeHtml(cat.label)}`;
    lucide.createIcons({ nodes: [document.getElementById('browseTitle')] });
    document.getElementById('browseSubtitle').textContent = `共 ${(cat.items || []).length} 个视频`;
    browseHeaderBeforeReading = null;

    // Render card grid
    const readingView = document.getElementById('readingView');
    readingView.classList.remove('active');
    updateGlobalBackButton();
    const list = document.getElementById('browseList');
    list.style.display = 'block';
    currentBrowseItems = cat.items || [];
    currentBrowseType = type;
    currentBrowseFolder = '';
    clearBrowseSelection(false);
    updateBrowseHeaderActions();
    renderBrowseItems(currentBrowseItems);
}

function showFolderVideos(folderName, navEl) {
    if (!summariesData) return;
    const group = getFolderCategories(summariesData).find(c => c.folder === folderName);
    if (!group) return;

    document.querySelectorAll('.nav-item, .nav-parent, .nav-child').forEach(n => n.classList.remove('active'));
    if (navEl) navEl.classList.add('active');

    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('browse-page').classList.add('active');

    document.getElementById('browseTitle').innerHTML = `<i data-lucide="folder" class="lucide-icon"></i> ${escapeHtml(group.label || folderName)}`;
    lucide.createIcons({ nodes: [document.getElementById('browseTitle')] });
    document.getElementById('browseSubtitle').textContent = `共 ${group.count} 个视频`;
    browseHeaderBeforeReading = null;

    const readingView = document.getElementById('readingView');
    readingView.classList.remove('active');
    updateGlobalBackButton();
    const list = document.getElementById('browseList');
    list.style.display = 'block';
    currentBrowseItems = group.items || [];
    currentBrowseType = 'folder';
    currentBrowseFolder = folderName;
    clearBrowseSelection(false);
    updateBrowseHeaderActions();
    renderBrowseItems(currentBrowseItems);
}

function refreshCurrentBrowseView() {
    if (!summariesData) return;
    if (currentBrowseType === 'folder' && currentBrowseFolder) {
        const navEl = document.querySelector(`.nav-parent[data-folder="${selectorEscape(currentBrowseFolder)}"]`);
        showFolderVideos(currentBrowseFolder, navEl);
        return;
    }
    if (currentBrowseType) {
        const navEl = document.querySelector(`.nav-parent[data-type="${selectorEscape(currentBrowseType)}"]`);
        showCategory(currentBrowseType, navEl);
    }
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

function updateBrowseHeaderActions() {
    const actions = document.getElementById('browseHeaderActions');
    const toggle = document.getElementById('browseViewToggle');
    const editBtn = document.getElementById('browseEditBtn');
    const browseReading = document.getElementById('readingView')?.classList.contains('active');
    const shouldShow = (currentBrowseType === 'folder' || currentBrowseType === 'all') && !browseReading;

    actions?.classList.toggle('is-hidden', !shouldShow);
    toggle?.classList.toggle('is-hidden', !shouldShow);
    editBtn?.classList.toggle('is-hidden', !shouldShow || browseSelectionMode || currentBrowseItems.length === 0);
}

function renderBrowseItems(items) {
    const list = document.getElementById('browseList');
    if (!list) return;
    currentBrowseItems = items || [];
    updateBrowseHeaderActions();
    if (!items || items.length === 0) {
        renderState(list, { type: 'empty', title: '暂无内容', message: '该分类下还没有可展示的总结' });
        return;
    }

    const toolbarHtml = renderBrowseSelectionToolbar(items);
    if (browseViewMode === 'compact') {
        list.innerHTML = `${toolbarHtml}<div class="browse-compact-list">${items.map(item => renderBrowseCompactItem(item)).join('')}</div>`;
    } else {
        // Use the same card size/style as favorites for visual consistency.
        list.innerHTML = `${toolbarHtml}<div class="video-grid">${items.map(item => renderBrowseCard(item)).join('')}</div>`;
    }
    lucide.createIcons({ nodes: [list] });
}

function renderBrowseSelectionToolbar(items = []) {
    const selectedCount = selectedBrowsePaths.size;
    const totalCount = items.length;
    const modeClass = browseSelectionMode ? 'active' : '';
    const deleteDisabled = selectedCount ? '' : 'disabled';
    const moveDisabled = selectedCount && localFolders.length ? '' : 'disabled';
    const folderOptions = renderFolderOptions('', '选择文件夹');
    if (!browseSelectionMode) return '';
    return `
        <div class="browse-selection-toolbar ${modeClass}">
            <div class="browse-selection-status">已选择 ${selectedCount} / ${totalCount}</div>
            <div class="browse-selection-actions">
                <button class="btn btn-secondary btn-secondary-compact" type="button" onclick="selectAllBrowseRecords()">全选</button>
                <button class="btn btn-secondary btn-secondary-compact" type="button" onclick="unselectAllBrowseRecords()">全不选</button>
                <select class="input input-compact browse-move-select" id="browseMoveFolder">${folderOptions}</select>
                <button class="btn btn-secondary btn-secondary-compact" type="button" onclick="moveSelectedBrowseRecords()" ${moveDisabled}>
                    <i data-lucide="folder-input" class="lucide-icon icon-xs"></i> 移动
                </button>
                <button class="btn btn-danger btn-secondary-compact" type="button" onclick="deleteSelectedBrowseRecords()" ${deleteDisabled}>
                    <i data-lucide="trash-2" class="lucide-icon icon-xs"></i> 删除选中
                </button>
                <button class="btn btn-secondary btn-secondary-compact" type="button" onclick="exitBrowseSelectionMode()">完成</button>
            </div>
        </div>
    `;
}

function clearBrowseSelection(rerender = true) {
    browseSelectionMode = false;
    selectedBrowsePaths.clear();
    updateBrowseHeaderActions();
    if (rerender) renderBrowseItems(currentBrowseItems);
}

function enterBrowseSelectionMode() {
    if (!currentBrowseItems.length) return;
    browseSelectionMode = true;
    selectedBrowsePaths.clear();
    updateBrowseHeaderActions();
    renderBrowseItems(currentBrowseItems);
}

function exitBrowseSelectionMode() {
    clearBrowseSelection(true);
}

function selectAllBrowseRecords() {
    if (!browseSelectionMode) return;
    currentBrowseItems.forEach(item => {
        if (item.path) selectedBrowsePaths.add(item.path);
    });
    renderBrowseItems(currentBrowseItems);
}

function unselectAllBrowseRecords() {
    if (!browseSelectionMode) return;
    selectedBrowsePaths.clear();
    renderBrowseItems(currentBrowseItems);
}

function toggleBrowseRecordSelection(encodedPath) {
    const path = decodePath(encodedPath);
    if (!path) return;
    if (selectedBrowsePaths.has(path)) {
        selectedBrowsePaths.delete(path);
    } else {
        selectedBrowsePaths.add(path);
    }
    renderBrowseItems(currentBrowseItems);
}

async function moveSelectedBrowseRecords() {
    const paths = Array.from(selectedBrowsePaths);
    const folder = document.getElementById('browseMoveFolder')?.value || '';
    if (!paths.length) return;
    if (!folder) {
        await showAlert('请选择目标文件夹', '无法移动');
        return;
    }

    const confirmed = await showConfirm(`确定将选中的 ${paths.length} 条视频记录移动到“${folder}”吗？`, {
        title: '移动视频记录',
        confirmText: '移动',
        cancelText: '取消',
    });
    if (!confirmed) return;

    try {
        const res = await fetch('/api/summaries/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths, folder }),
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || `HTTP ${res.status}`);
        }

        const movedFrom = new Set((data.moved || []).map(item => item.from));
        currentBrowseItems = currentBrowseItems.filter(item => !movedFrom.has(item.path));
        clearBrowseSelection(false);
        await loadSidebarBrowse();
        refreshCurrentBrowseView();
        showToast({
            title: '移动完成',
            message: `已移动 ${movedFrom.size} 条记录${data.errors?.length ? `，${data.errors.length} 条失败` : ''}`,
            tone: data.errors?.length ? 'error' : 'success',
            duration: 3200,
        });
    } catch (err) {
        await showAlert('移动失败: ' + err.message, '移动失败');
    }
}

async function deleteSelectedBrowseRecords() {
    const paths = Array.from(selectedBrowsePaths);
    if (!paths.length) return;

    const confirmed = await showConfirm(`确定删除选中的 ${paths.length} 条本地视频记录吗？会同时删除对应字幕、总结附属文件和本地视频文件。`, {
        title: '删除视频记录',
        confirmText: '删除',
        cancelText: '取消',
        danger: true,
    });
    if (!confirmed) return;

    try {
        const res = await fetch('/api/summaries/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths }),
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || `HTTP ${res.status}`);
        }

        const deleted = new Set(data.deleted || []);
        currentBrowseItems = currentBrowseItems.filter(item => !deleted.has(item.path));
        clearBrowseSelection(false);
        await loadSidebarBrowse();
        refreshCurrentBrowseView();
        showToast({
            title: '删除完成',
            message: `已删除 ${deleted.size} 条记录${data.errors?.length ? `，${data.errors.length} 条失败` : ''}`,
            tone: data.errors?.length ? 'error' : 'success',
            duration: 3200,
        });
    } catch (err) {
        await showAlert('删除失败: ' + err.message, '删除失败');
    }
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
    extraClass = '',
    title = '',
    cover = '',
    duration = '',
    badgeId = '',
    badgeClass = 'done',
    badgeText = '成功',
    metaLeft = '',
    metaRight = '',
    actionButtonHtml = '',
    selectionHtml = '',
    onClick = '',
}) {
    const safeCover = safeHttpUrl(cover || '');
    const coverHtml = safeCover
        ? `<img src="${escapeAttr(safeCover)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
        : `<div class="cover-fallback"><i data-lucide="image-off" class="lucide-icon"></i></div>`;

    return `
        <div class="video-card ${extraClass}" ${id ? `id="${id}"` : ''} ${dataAttrs} ${onClick ? `onclick="${onClick}"` : ''}>
            <div class="cover-wrapper">
                ${coverHtml}
                ${selectionHtml}
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
    selectionHtml = '',
    onClick = '',
    extraClass = '',
}) {
    const safeCover = safeHttpUrl(cover || '');
    const coverHtml = safeCover
        ? `<img src="${escapeAttr(safeCover)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
        : `<div class="browse-compact-placeholder"><i data-lucide="image-off" class="lucide-icon icon-sm"></i></div>`;

    return `
        <div class="browse-compact-item ${extraClass}" data-bvid="${escapeAttr(bvid)}" ${onClick ? `onclick="${onClick}"` : ''}>
            ${selectionHtml}
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
    const showUnfav = currentBrowseType === 'favorites' && !!defaultFavId && !!item.bvid && !browseSelectionMode;
    const encodedPath = encodePath(item.path);
    const selected = selectedBrowsePaths.has(item.path);
    const actionButtonHtml = showUnfav
        ? `<button class="unfav-btn" title="取消收藏" onclick="event.stopPropagation(); unfavoriteFromBrowse('${item.bvid}', this)">✕</button>`
        : '';

    return renderSharedThumbCard({
        dataAttrs: `data-path="${escapeAttr(encodedPath)}"`,
        title: item.name || item.bvid || '未命名视频',
        cover: item.cover || '',
        duration,
        badgeClass,
        badgeText,
        metaLeft,
        metaRight,
        actionButtonHtml: `${browseSelectionMode ? renderSelectionCheck(selected) : ''}${actionButtonHtml}`,
        onClick: browseSelectionMode ? `toggleBrowseRecordSelection('${encodedPath}')` : `openSummary('${encodedPath}')`,
        extraClass: `${browseSelectionMode ? 'selection-mode' : ''} ${selected ? 'selected' : ''}`.trim(),
    });
}

function renderBrowseCompactItem(item) {
    const { badgeClass, badgeText } = summaryBadge(item.no_subtitle ? 'no_subtitle' : 'done');
    const compactMeta = `${item.author_name || '本地总结'} · ${item.bvid || 'BV 未记录'}`;
    const showUnfav = currentBrowseType === 'favorites' && !!defaultFavId && !!item.bvid && !browseSelectionMode;
    const encodedPath = encodePath(item.path);
    const selected = selectedBrowsePaths.has(item.path);
    return renderSharedCompactItem({
        bvid: item.bvid || '',
        title: item.name || item.bvid || '未命名视频',
        cover: item.cover || '',
        meta: compactMeta,
        badgeClass,
        badgeText,
        actionButtonHtml: `${browseSelectionMode ? renderSelectionCheck(selected) : ''}${showUnfav
            ? `<button class="compact-unfav-btn unfav-btn" title="取消收藏" onclick="event.stopPropagation(); unfavoriteFromBrowse('${item.bvid}', this)">✕</button>`
            : ''}`,
        onClick: browseSelectionMode ? `toggleBrowseRecordSelection('${encodedPath}')` : `openSummary('${encodedPath}')`,
        extraClass: `${showUnfav ? 'fav-compact-item' : ''} ${browseSelectionMode ? 'selection-mode' : ''} ${selected ? 'selected' : ''}`.trim(),
    });
}

function renderSelectionCheck(selected) {
    return `
        <span class="record-selection-check ${selected ? 'selected' : ''}" aria-hidden="true">
            <i data-lucide="${selected ? 'check' : 'circle'}" class="lucide-icon icon-xs"></i>
        </span>
    `;
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
    if (bvid && enableRetry) {
        buttons.push(
            `<button class="action-btn action-btn-retry" onclick="retrySummarize('${bvid}', ${isNoSub})"><i data-lucide="refresh-cw" class="lucide-icon icon-xs"></i> 重新总结</button>`
        );
    }
    if (bvid && showOpen) {
        buttons.push(
            `<button class="action-btn action-btn-open" onclick="openExternal('https://www.bilibili.com/video/${bvid}')"><i data-lucide="external-link" class="lucide-icon icon-xs"></i> 打开 B站</button>`
        );
    }
    if (bvid && isNoSub && enableAsr) {
        buttons.push(
            `<button class="action-btn action-btn-asr" onclick="asrSummarize('${bvid}')"><i data-lucide="mic" class="lucide-icon icon-xs"></i> 转录总结</button>`
        );
    }
    if (bvid && showUnfav) {
        buttons.push(
            `<button class="action-btn action-btn-unfav" onclick="unfavoriteFromReading('${bvid}')"><i data-lucide="heart-off" class="lucide-icon icon-xs"></i> 取消收藏</button>`
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

function decodePath(encodedPath) {
    try {
        return encodedPath.split('/').map(decodeURIComponent).join('/');
    } catch {
        return encodedPath;
    }
}

function extractBvidFromContent(content) {
    const match = String(content || '').match(/\*\*BV号\*\*:\s*(BV[0-9A-Za-z]+)/);
    return match ? match[1] : '';
}

function normalizeBvid(bvid) {
    const value = String(bvid || '').trim();
    return /^BV[0-9A-Za-z]+$/.test(value) ? value : '';
}

function getSummaryBvid(data, fallbackBvid = '') {
    return normalizeBvid(data?.meta?.bvid) || normalizeBvid(fallbackBvid) || extractBvidFromContent(data?.content || '');
}

function renderVideoHeaderFacts({ bvid = '', author = '', duration = 0 } = {}) {
    const facts = [];
    if (bvid) facts.push(bvid);
    if (author) facts.push(author);
    if (duration) facts.push(formatHms(duration));
    return facts.join(' · ');
}

function getSummaryHeaderInfo(data, knownVideo = {}, fallbackBvid = '') {
    const meta = data?.meta || {};
    const bvid = getSummaryBvid(data, fallbackBvid);
    return {
        title: meta.title || knownVideo.title || summaryTitleFromContent(data?.content || '') || bvid || '视频总结',
        bvid,
        author: meta.author_name || knownVideo.upper || '',
        duration: Number(meta.duration || knownVideo.duration || 0),
    };
}

function snapshotHeader(titleId, subtitleId) {
    return {
        titleHtml: document.getElementById(titleId)?.innerHTML || '',
        subtitleText: document.getElementById(subtitleId)?.textContent || '',
    };
}

function applyVideoHeader(titleId, subtitleId, info) {
    const titleEl = document.getElementById(titleId);
    const subtitleEl = document.getElementById(subtitleId);
    if (titleEl) {
        titleEl.innerHTML = `<i data-lucide="video" class="lucide-icon"></i> ${escapeHtml(info.title)}`;
        lucide.createIcons({ nodes: [titleEl] });
    }
    if (subtitleEl) {
        subtitleEl.textContent = renderVideoHeaderFacts(info);
    }
}

function restoreHeader(titleId, subtitleId, snapshot) {
    if (!snapshot) return;
    const titleEl = document.getElementById(titleId);
    const subtitleEl = document.getElementById(subtitleId);
    if (titleEl) {
        titleEl.innerHTML = snapshot.titleHtml;
        lucide.createIcons({ nodes: [titleEl] });
    }
    if (subtitleEl) {
        subtitleEl.textContent = snapshot.subtitleText;
    }
}

function formatHms(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function formatTimelineTime(seconds, compact = false) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (compact || h === 0) {
        return `${m}:${String(s).padStart(2, '0')}`;
    }
    return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function summaryTitleFromContent(content) {
    const match = String(content || '').match(/^#\s+(.+)$/m);
    return match ? match[1].trim() : '';
}

function withMissingAuthorLine(content, knownVideo = {}) {
    if (!knownVideo?.upper || String(content || '').includes('**作者**:')) {
        return content || '';
    }

    const authorLine = knownVideo.upperMid
        ? `**作者**: [${knownVideo.upper}](https://space.bilibili.com/${knownVideo.upperMid})`
        : `**作者**: ${knownVideo.upper}`;
    const videoLinkPattern = /(\*\*视频链接\*\*:[^\n]*\n)/;
    if (videoLinkPattern.test(content)) {
        return content.replace(videoLinkPattern, `$1${authorLine}\n`);
    }
    return `${content || ''}\n${authorLine}\n`;
}

function renderAssetButton(asset, encodedPath, label) {
    return `
        <button class="summary-asset-btn" type="button" onclick="generateSummaryAsset('${asset}', '${encodedPath}', this)">
            <i data-lucide="sparkles" class="lucide-icon icon-xs"></i>
            ${escapeHtml(label)}
        </button>
    `;
}

function renderDetailedSummaryPanel(content, encodedPath) {
    const renderedContent = renderTimestampSummaryMarkdown(content);
    if (!String(content || '').trim()) {
        return `
            <div class="summary-detail-empty">
                <div class="summary-detail-empty-title">尚未生成详细总结</div>
                <div class="summary-detail-empty-message">基于本地字幕生成更完整的结构化总结</div>
                ${renderAssetButton('detailed-summary', encodedPath, '生成详细总结')}
            </div>
        `;
    }

    return `
        <div class="summary-asset-toolbar">
            ${renderAssetButton('detailed-summary', encodedPath, '重新生成')}
        </div>
        <div class="reading-content summary-markdown">${renderedContent}</div>
    `;
}

function renderSummaryAssetPanelByType(asset, payload, encodedPath) {
    if (asset === 'detailed-summary') return renderDetailedSummaryPanel(payload.detailed_summary || '', encodedPath);
    return '';
}

function renderSubtitlePanel(subtitles = [], duration = 0) {
    if (!Array.isArray(subtitles) || subtitles.length === 0) {
        return `
            <div class="summary-detail-empty">
                <div class="summary-detail-empty-title">暂无字幕</div>
                <div class="summary-detail-empty-message">当前总结没有可用的本地字幕文件</div>
            </div>
        `;
    }

    const compactTime = subtitlesUseCompactTime(subtitles, duration);
    return `
        <div class="subtitle-list" data-compact-time="${compactTime ? 'true' : 'false'}">
            ${subtitles.map((segment, index) => `
                <button class="subtitle-row" type="button" data-start="${Number(segment.start) || 0}" data-index="${index}">
                    <span class="subtitle-time">${formatTimelineTime(segment.start, compactTime)}</span>
                    <span class="subtitle-text">${escapeHtml(segment.text || '')}</span>
                </button>
            `).join('')}
        </div>
    `;
}

function subtitlesUseCompactTime(subtitles = [], duration = 0) {
    const videoDuration = Number(duration || 0);
    if (videoDuration > 0) return videoDuration <= 3600;
    const maxEnd = Math.max(0, ...subtitles.map(segment => Number(segment.end || segment.start || 0)));
    return maxEnd > 0 && maxEnd <= 3600;
}

function renderSummaryDetail(data, {
    fallbackBvid = '',
    knownVideo = {},
} = {}) {
    const bvid = getSummaryBvid(data, fallbackBvid);
    const content = withMissingAuthorLine(data?.content || '', knownVideo);
    const mediaUrl = data?.media_url || '';
    const subtitles = Array.isArray(data?.subtitles) ? data.subtitles : [];
    const encodedPath = encodePath(data?.path || '');
    const videoDuration = Number(data?.meta?.duration || knownVideo.duration || 0);
    const defaultTab = data?.detailed_summary ? 'detailed-summary' : (subtitles.length > 0 ? 'subtitles' : 'detailed-summary');

    const mediaHtml = mediaUrl
        ? `
            <video
                class="summary-video-player"
                id="summaryVideoPlayer"
                controls
                preload="metadata"
                src="${escapeAttr(mediaUrl)}">
                当前浏览器不支持本地视频播放。
            </video>
        `
        : `
            <div class="summary-video-placeholder">
                <i data-lucide="video-off" class="lucide-icon"></i>
                <span>本地视频文件不存在</span>
            </div>
        `;

    return `
        <div class="summary-detail-layout" data-bvid="${escapeAttr(bvid)}" data-path="${escapeAttr(encodedPath)}" style="--summary-left-width: ${summaryDetailResizePercent}%;">
            <section class="summary-media-panel">
                <div class="summary-video-shell">
                    ${mediaHtml}
                </div>
            </section>
            <div class="summary-resize-handle" role="separator" aria-orientation="vertical" aria-label="调整视频和内容宽度" tabindex="0"></div>
            <section class="summary-insights-panel">
                <div class="summary-tabs" role="tablist" aria-label="字幕与总结">
                    <button class="summary-tab ${defaultTab === 'subtitles' ? 'active' : ''}" type="button" data-tab="subtitles" role="tab" aria-selected="${defaultTab === 'subtitles'}">
                        <i data-lucide="captions" class="lucide-icon icon-sm"></i>
                        字幕
                    </button>
                    <button class="summary-tab ${defaultTab === 'detailed-summary' ? 'active' : ''}" type="button" data-tab="detailed-summary" role="tab" aria-selected="${defaultTab === 'detailed-summary'}">
                        <i data-lucide="list-tree" class="lucide-icon icon-sm"></i>
                        详细总结
                    </button>
                </div>
                <div class="summary-tab-panels">
                    <div class="summary-tab-panel ${defaultTab === 'subtitles' ? 'active' : ''}" data-panel="subtitles" role="tabpanel">
                        ${renderSubtitlePanel(subtitles, videoDuration)}
                    </div>
                    <div class="summary-tab-panel ${defaultTab === 'detailed-summary' ? 'active' : ''}" data-panel="detailed-summary" role="tabpanel">
                        ${renderDetailedSummaryPanel(data?.detailed_summary || '', encodedPath)}
                    </div>
                </div>
            </section>
        </div>
    `;
}

function setupSummaryDetailInteractions(container) {
    const layout = container.querySelector('.summary-detail-layout');
    if (!layout) return;

    layout.addEventListener('click', (e) => {
        const tab = e.target.closest('.summary-tab');
        if (tab) {
            const target = tab.dataset.tab;
            layout.querySelectorAll('.summary-tab').forEach(btn => {
                const isActive = btn.dataset.tab === target;
                btn.classList.toggle('active', isActive);
                btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
            });
            layout.querySelectorAll('.summary-tab-panel').forEach(panel => {
                panel.classList.toggle('active', panel.dataset.panel === target);
            });
            return;
        }

        const subtitleRow = e.target.closest('.subtitle-row');
        if (subtitleRow) {
            const start = Number(subtitleRow.dataset.start || 0);
            seekSummaryVideo(layout, start);
            layout.querySelectorAll('.subtitle-row.active').forEach(row => row.classList.remove('active'));
            subtitleRow.classList.add('active');
            return;
        }

        const segmentJump = e.target.closest('.summary-time-jump');
        if (segmentJump) {
            const start = Number(segmentJump.dataset.start || 0);
            seekSummaryVideo(layout, start);
        }
    });

    setupSummaryResizer(layout);

    if (typeof lucide !== 'undefined') {
        lucide.createIcons({ nodes: [layout] });
    }
}

function seekSummaryVideo(layout, start) {
    const player = layout.querySelector('#summaryVideoPlayer');
    if (!player) return;
    player.currentTime = Math.max(0, start);
    player.play().catch(() => {});
}

function setupSummaryResizer(layout) {
    const handle = layout.querySelector('.summary-resize-handle');
    if (!handle) return;

    const applyPercent = (percent) => {
        summaryDetailResizePercent = Math.max(35, Math.min(68, percent));
        layout.style.setProperty('--summary-left-width', `${summaryDetailResizePercent}%`);
        localStorage.setItem(SUMMARY_DETAIL_RESIZE_KEY, String(Math.round(summaryDetailResizePercent)));
    };

    const updateFromClientX = (clientX) => {
        const rect = layout.getBoundingClientRect();
        if (!rect.width) return;
        const minLeft = Math.min(360, rect.width * 0.42);
        const minRight = Math.min(420, rect.width * 0.42);
        const raw = ((clientX - rect.left) / rect.width) * 100;
        const minPercent = (minLeft / rect.width) * 100;
        const maxPercent = 100 - (minRight / rect.width) * 100;
        applyPercent(Math.max(minPercent, Math.min(maxPercent, raw)));
    };

    handle.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        handle.setPointerCapture(event.pointerId);
        layout.classList.add('is-resizing');

        const onPointerMove = (moveEvent) => updateFromClientX(moveEvent.clientX);
        const onPointerUp = () => {
            layout.classList.remove('is-resizing');
            window.removeEventListener('pointermove', onPointerMove);
            window.removeEventListener('pointerup', onPointerUp);
        };

        window.addEventListener('pointermove', onPointerMove);
        window.addEventListener('pointerup', onPointerUp);
    });

    handle.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        applyPercent(summaryDetailResizePercent + (event.key === 'ArrowLeft' ? -3 : 3));
    });
}

async function generateSummaryAsset(asset, encodedPath, button) {
    if (!encodedPath) return;

    const layout = button.closest('.summary-detail-layout');
    const panel = layout?.querySelector(`[data-panel="${asset}"]`);
    const previousHtml = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> 生成中';

    try {
        const res = await fetch(`/api/summary-asset/${asset}/${encodedPath}`, { method: 'POST' });
        const payload = await res.json();
        if (!res.ok || payload.error) {
            throw new Error(payload.error || `HTTP ${res.status}`);
        }

        if (panel) {
            panel.innerHTML = renderSummaryAssetPanelByType(asset, payload, encodedPath);
            setupExternalLinks(panel);
            if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [panel] });
        }
        showToast({ title: '生成完成', message: '内容已更新', tone: 'success', duration: 2600 });
    } catch (err) {
        await showAlert('生成失败: ' + err.message, '生成失败');
        button.disabled = false;
        button.innerHTML = previousHtml;
        if (typeof lucide !== 'undefined') lucide.createIcons({ nodes: [button] });
    }
}

async function openSummary(encodedPath) {
    const apiPath = encodedPath;
    const list = document.getElementById('browseList');
    const readingView = document.getElementById('readingView');
    const readingContent = document.getElementById('readingContent');

    try {
        renderState(readingContent, { type: 'loading', title: '加载中', message: '正在读取视频详情' });
        const res = await fetch(`/api/summary-detail/${apiPath}`);
        const data = await res.json();
        if (data.error) { await showAlert(data.error, '加载失败'); return; }
        list.style.display = 'none';
        readingView.classList.add('active');
        updateGlobalBackButton();

        const summaryPath = decodePath(encodedPath);
        const knownVideo = currentBrowseItems.find(item => item.path === summaryPath) || {};
        const headerInfo = getSummaryHeaderInfo(data, knownVideo);
        if (!browseHeaderBeforeReading) {
            browseHeaderBeforeReading = snapshotHeader('browseTitle', 'browseSubtitle');
        }
        applyVideoHeader('browseTitle', 'browseSubtitle', headerInfo);
        readingContent.innerHTML = renderSummaryDetail(data, { knownVideo });

        const bvid = headerInfo.bvid;
        const isNoSub = data.content.includes('无法获取字幕');
        renderReadingActions('readingActions', {
            bvid,
            isNoSub,
            showOpen: true,
            showUnfav: currentBrowseType === 'favorites' && !!defaultFavId,
            enableRetry: true,
            enableAsr: false,
        });

        setupSummaryDetailInteractions(readingContent);
        setupExternalLinks(readingContent);
    } catch (err) { await showAlert('加载失败: ' + err.message, '加载失败'); }
}

function closeReading() {
    document.getElementById('readingView').classList.remove('active');
    document.getElementById('browseList').style.display = 'block';
    restoreHeader('browseTitle', 'browseSubtitle', browseHeaderBeforeReading);
    browseHeaderBeforeReading = null;
    updateGlobalBackButton();
}

// ---------------------------------------------------------------------------
// Markdown → HTML
// ---------------------------------------------------------------------------
function renderMarkdown(md) {
    const normalized = normalizeMarkdown(md);

    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        const rawHtml = marked.parse(normalized, {
            async: false,
            breaks: true,
            gfm: true,
        });
        const cleanHtml = DOMPurify.sanitize(rawHtml, {
            USE_PROFILES: { html: true },
            ADD_TAGS: ['button'],
            ADD_ATTR: ['type', 'data-start'],
            ALLOWED_URI_REGEXP: /^(?:(?:(?:f|ht)tps?|mailto):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
            FORBID_TAGS: ['iframe', 'object', 'embed', 'script', 'style', 'img'],
            FORBID_ATTR: ['style'],
        });
        return withSafeExternalLinks(cleanHtml);
    }

    return renderMarkdownFallback(normalized);
}

function renderTimestampSummaryMarkdown(md) {
    const source = normalizeMarkdown(md);
    const tokenPattern = /\[\[时间段:\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)\s*-\s*([0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)\]\]\s*/g;
    const withoutTokens = source.replace(tokenPattern, (_, start, end) => {
        const startSeconds = parseTimelineTimestamp(start);
        const label = `${start}-${end}`;
        return `<button class="summary-time-jump" type="button" data-start="${startSeconds}">${label}</button> `;
    });
    return renderMarkdown(withoutTokens);
}

function parseTimelineTimestamp(value) {
    const parts = String(value || '').split(':').map(part => Number(part));
    if (parts.some(part => Number.isNaN(part))) return 0;
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    return 0;
}

function normalizeMarkdown(md) {
    const text = String(md || '').trim();
    const fenced = text.match(/^```(?:markdown|md)?[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t]*$/i);
    return fenced ? fenced[1].trim() : text;
}

function withSafeExternalLinks(html) {
    const template = document.createElement('template');
    template.innerHTML = html;
    template.content.querySelectorAll('a[href]').forEach(link => {
        const safeUrl = safeHttpUrl(link.getAttribute('href'));
        if (!safeUrl) {
            link.removeAttribute('href');
            return;
        }
        link.setAttribute('href', safeUrl);
        link.classList.add('ext-link');
        link.setAttribute('target', '_blank');
        link.setAttribute('rel', 'noopener noreferrer');
    });
    return template.innerHTML;
}

function renderMarkdownFallback(md) {
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

    function resetSubmitButton() {
        submitBtn.disabled = prefix === 'user' ? selectedUserBvids.size === 0 : false;
        submitBtn.innerHTML = prefix === 'user'
            ? '<i data-lucide="play" class="lucide-icon icon-sm"></i> 总结选中'
            : '<i data-lucide="play" class="lucide-icon icon-sm"></i> 开始总结';
        lucide.createIcons({ nodes: [submitBtn] });
    }

    function handleEvent(eventType, data) {
        let d;
        try { d = JSON.parse(data); } catch { return; }

        switch (eventType) {
            case 'start':
                total = d.total;
                addLog(logEl, `处理中: 共 ${d.total} 个视频 (并发 ${d.concurrency}, 模型 ${d.model}, 模块 ${formatGenerationModules(d.modules)})`, 'info');
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
                if (prefix === 'user') updateUserVideoSummaryState(d.bvid, d.status || 'success', d.path);
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
                if (prefix === 'user') updateUserVideoSummaryState(d.bvid, d.status || 'success', d.path);
                break;
            case 'error':
                completed++;
                updateProgress(progressBar, statsEl, completed, total);
                addLog(logEl, `失败: ${d.title || ''} ${d.message || ''}`.trim(), 'error');
                if (prefix === 'user') updateUserVideoSummaryState(d.bvid, 'failed', '');
                break;
            case 'done':
                isDone = true;
                resetSubmitButton();
                addLog(logEl, `完成: 成功 ${d.success} | 已跳过 ${d.skipped} | 无字幕 ${d.no_subtitle} | 失败 ${d.errors}`, 'info');
                progressBar.style.width = '100%';
                showInlineResults(resultsArea, completedPaths);
                loadSidebarBrowse();
                if (prefix === 'url') loadUrlTaskLogs(1);
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
            resetSubmitButton();
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

    container.innerHTML = `<div class="card"><div class="card-title"><i data-lucide="file-text" class="lucide-icon icon-md"></i> 生成的总结 (${results.length})</div><div id="resultsList"></div></div>`;
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
                <div class="reading-content pt-3">加载中...</div>
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

function jsStringLiteral(value) {
    return escapeAttr(JSON.stringify(String(value ?? '')));
}

function selectorEscape(text) {
    if (window.CSS?.escape) return CSS.escape(String(text));
    return String(text).replace(/["\\]/g, '\\$&');
}

function safeHttpUrl(rawUrl) {
    try {
        const normalized = String(rawUrl || '').trim();
        if (!normalized) return null;
        const withScheme = normalized.startsWith('//') ? `https:${normalized}` : normalized;
        const parsed = new URL(withScheme);
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return null;
        }
        return parsed.href;
    } catch {
        return null;
    }
}

function renderFolderOptions(selected = '', _defaultLabel = '默认文件夹') {
    const normalizedSelected = selected || DEFAULT_LOCAL_FOLDER;
    const options = [];
    const hasDefaultFolder = localFolders.some(folder => (folder.name || folder.display_name || '') === DEFAULT_LOCAL_FOLDER);
    if (!hasDefaultFolder) {
        options.push(`<option value="${escapeAttr(DEFAULT_LOCAL_FOLDER)}" ${normalizedSelected === DEFAULT_LOCAL_FOLDER ? 'selected' : ''}>${escapeHtml(DEFAULT_LOCAL_FOLDER)}</option>`);
    }
    for (const folder of localFolders) {
        const name = folder.name || folder.display_name || '';
        if (!name) continue;
        options.push(`<option value="${escapeAttr(name)}" ${name === normalizedSelected ? 'selected' : ''}>${escapeHtml(folder.display_name || name)}</option>`);
    }
    return options.join('');
}

function updateFolderSelects() {
    const configs = [
        ['urlFolderSelect', '默认文件夹'],
        ['userFolderSelect', '默认文件夹'],
        ['favFolderSelect', '默认文件夹'],
    ];
    for (const [id, label] of configs) {
        const select = document.getElementById(id);
        if (!select) continue;
        const current = select.value || DEFAULT_LOCAL_FOLDER;
        select.innerHTML = renderFolderOptions(current, label);
    }
}

function getSelectedFolder(prefix) {
    return document.getElementById(`${prefix}FolderSelect`)?.value || DEFAULT_LOCAL_FOLDER;
}

async function createLocalFolder(event) {
    event?.stopPropagation();
    const name = await showTextPrompt({
        title: '新建文件夹',
        placeholder: '文件夹名称',
        confirmText: '创建',
        cancelText: '取消',
    });
    if (!name) return;

    try {
        const res = await fetch('/api/folders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name }),
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || `HTTP ${res.status}`);
        }
        await loadSidebarBrowse();
        showToast({ title: '文件夹已创建', message: data.folder?.display_name || name, tone: 'success', duration: 2600 });
    } catch (err) {
        await showAlert('新建文件夹失败: ' + err.message, '操作失败');
    }
}

async function deleteLocalFolder(folderName, folderLabel = '', count = 0) {
    if (!folderName || folderName === DEFAULT_LOCAL_FOLDER) return;
    const displayName = folderLabel || folderName;
    const confirmed = await showConfirm(
        `确定删除“${displayName}”文件夹吗？该操作会删除文件夹内 ${count} 条总结记录及关联字幕、媒体文件，无法撤销。`,
        {
            title: '删除文件夹',
            confirmText: '删除',
            cancelText: '取消',
            danger: true,
        }
    );
    if (!confirmed) return;

    try {
        const res = await fetch(`/api/folders/${encodeURIComponent(folderName)}`, {
            method: 'DELETE',
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || `HTTP ${res.status}`);
        }

        if (currentBrowseType === 'folder' && currentBrowseFolder === folderName) {
            currentBrowseType = 'all';
            currentBrowseFolder = '';
            const allCat = summariesData?.categories?.find(c => c.type === 'all');
            currentBrowseItems = allCat?.items || [];
            document.getElementById('browseTitle').innerHTML = '<i data-lucide="library" class="lucide-icon"></i> 所有视频';
            document.getElementById('browseSubtitle').textContent = `共 ${currentBrowseItems.length} 篇总结`;
            renderBrowseItems(currentBrowseItems);
        }

        await loadSidebarBrowse();
        showToast({
            title: '文件夹已删除',
            message: `已删除 ${data.deleted?.length || 0} 条记录`,
            tone: 'success',
            duration: 3200,
        });
    } catch (err) {
        await showAlert('删除文件夹失败: ' + err.message, '操作失败');
    }
}

// ---------------------------------------------------------------------------
// Submit Handlers
// ---------------------------------------------------------------------------
function getGenerationModules(prefix) {
    return {
        summary: false,
        detailed_summary: true,
    };
}

function formatGenerationModules(modules = {}) {
    const labels = [];
    if (modules.detailed_summary) labels.push('详细总结');
    return labels.length ? labels.join('、') : '仅转录';
}

function renderGenerationModules(prefix) {
    return '';
}

async function submitURL() {
    const text = document.getElementById('urlInput').value.trim();
    if (!text) return;
    const urls = text.split('\n').map(u => u.trim()).filter(Boolean);
    const modules = getGenerationModules('url');
    const folder = getSelectedFolder('url');
    const submitBtn = document.getElementById('urlSubmit');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner"></span> 创建任务...';
    }
    try {
        const res = await fetch('/api/summarize/url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ urls, modules, folder })
        });
        const data = await res.json();
        if (data.error) { await showAlert(data.error, '请求失败'); return; }
        await loadUrlTaskLogs(1);
        startUrlTaskLogAutoRefresh();
        showToast({ title: '任务已创建', message: `已提交 ${data.total || urls.length} 个视频，进度可在任务详情中查看`, tone: 'success', duration: 2600 });
    } catch (err) {
        await showAlert('请求失败: ' + err.message, '请求失败');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i data-lucide="play" class="lucide-icon icon-sm"></i> 开始总结';
            lucide.createIcons({ nodes: [submitBtn] });
        }
    }
}

async function submitUser() {
    const targets = Array.from(selectedUserBvids);
    if (!targets.length) {
        await showAlert('请选择要总结的视频', '未选择视频');
        return;
    }
    const modules = getGenerationModules('user');
    const folder = getSelectedFolder('user');
    try {
        const res = await fetch('/api/summarize/user-selected', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user: currentUser || document.getElementById('userInput').value.trim(),
                uid: currentUserUid,
                bvids: targets,
                modules,
                folder,
            })
        });
        const data = await res.json();
        if (data.error) { await showAlert(data.error, '请求失败'); return; }
        listenProgress(data.task_id, 'user');
    } catch (err) { await showAlert('请求失败: ' + err.message, '请求失败'); }
}

function formatTaskTime(seconds) {
    if (!seconds) return '';
    const date = new Date(seconds * 1000);
    return date.toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
    });
}

function taskStatusText(status) {
    const map = {
        queued: '排队中',
        running: '处理中',
        done: '已完成',
        failed: '失败',
    };
    return map[status] || status || '未知';
}

function taskTypeText(type) {
    const map = {
        url: 'URL',
        user: 'UP 主',
        favorites: '收藏夹',
    };
    return map[type] || type || '-';
}

function taskEventClass(eventName = '') {
    if (eventName === 'error') return 'task-log-event-error';
    if (eventName === 'completed') return 'task-log-event-success';
    if (eventName === 'skip') return 'task-log-event-skip';
    if (eventName === 'processing') return 'task-log-event-processing';
    return '';
}

function taskEventTime(seconds) {
    if (!seconds) return '';
    const date = new Date(seconds * 1000);
    return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
    });
}

function hasRunningUrlTasks(items = []) {
    return items.some(task => ['queued', 'running'].includes(task.status));
}

function taskProgressPercent(task = {}) {
    if (Number.isFinite(task.progress_percent)) {
        return Math.max(0, Math.min(100, task.progress_percent));
    }
    return task.total ? Math.round(((task.completed || 0) / task.total) * 100) : 0;
}

function taskSummaryText(task = {}) {
    const pieces = [
        `成功 ${task.success || 0}`,
        `跳过 ${task.skipped || 0}`,
        `无字幕 ${task.no_subtitle || 0}`,
        `失败 ${task.errors || 0}`,
    ];
    return pieces.join(' / ');
}

function latestTaskEventMessage(task = {}) {
    const latestEvent = (task.events || []).slice(-1)[0];
    return latestEvent?.message || taskStatusText(task.status);
}

function startUrlTaskLogAutoRefresh() {
    if (urlTaskLogRefreshTimer) return;
    urlTaskLogRefreshTimer = window.setInterval(async () => {
        const keepRefreshing = await loadUrlTaskLogs(urlTaskLogPage, { silent: true });
        if (!keepRefreshing) stopUrlTaskLogAutoRefresh();
    }, 2000);
}

function stopUrlTaskLogAutoRefresh() {
    if (!urlTaskLogRefreshTimer) return;
    window.clearInterval(urlTaskLogRefreshTimer);
    urlTaskLogRefreshTimer = null;
}

async function loadUrlTaskLogs(page = urlTaskLogPage, options = {}) {
    const list = document.getElementById('urlTaskLogList');
    const pagination = document.getElementById('urlTaskLogPagination');
    if (!list || !pagination) return false;
    urlTaskLogPage = Math.max(1, page);

    try {
        const res = await fetch(`/api/tasks?type=url&page=${urlTaskLogPage}&page_size=5`);
        const data = await res.json();
        const items = data.items || [];
        items.forEach(task => {
            if (task?.task_id) urlTaskLogCache.set(task.task_id, task);
        });
        if (!items.length) {
            if (!options.silent) {
                renderState(list, { type: 'empty', title: '暂无任务日志', message: '开始总结后会在这里记录任务' });
            }
            pagination.innerHTML = '';
            return false;
        }

        const rows = items.map(task => {
            const pct = taskProgressPercent(task);
            const currentText = latestTaskEventMessage(task);
            return `
                <tr>
                    <td>
                        <div class="task-log-title">${escapeHtml(task.title || task.task_id)}</div>
                        <div class="task-log-meta">${formatTaskTime(task.created_at)} · ${escapeHtml(task.task_id)}</div>
                    </td>
                    <td>${escapeHtml(taskTypeText(task.type))}</td>
                    <td><span class="task-status task-status-${escapeAttr(task.status || '')}">${taskStatusText(task.status)}</span></td>
                    <td>
                        <div class="task-table-progress-cell">
                            <div class="task-table-progress-text">
                                <span>${task.completed || 0}/${task.total || 0}</span>
                                <span>${pct}%</span>
                            </div>
                            <div class="task-log-progress" aria-label="任务进度 ${pct}%">
                                <div class="task-log-progress-bar" style="width:${pct}%"></div>
                            </div>
                        </div>
                    </td>
                    <td>${escapeHtml(taskSummaryText(task))}</td>
                    <td><div class="task-log-current">${escapeHtml(currentText)}</div></td>
                    <td class="task-log-action-cell">
                        <button class="task-detail-btn" type="button" title="查看任务详情" aria-label="查看任务详情" onclick="showTaskDetail(${jsStringLiteral(task.task_id)})">
                            <i data-lucide="panel-right-open" class="lucide-icon icon-sm"></i>
                        </button>
                    </td>
                </tr>
            `;
        }).join('');

        list.innerHTML = `
            <div class="task-log-table-wrap">
                <table class="task-log-table">
                    <thead>
                        <tr>
                            <th>任务</th>
                            <th>类型</th>
                            <th>状态</th>
                            <th>进度</th>
                            <th>结果</th>
                            <th>当前步骤</th>
                            <th class="task-log-action-head">详情</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
        lucide.createIcons({ nodes: [list] });

        const totalPages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 5)));
        pagination.innerHTML = `
            <button class="btn-secondary btn-secondary-xs" ${urlTaskLogPage <= 1 ? 'disabled' : ''} onclick="loadUrlTaskLogs(${urlTaskLogPage - 1})">上一页</button>
            <span>第 ${urlTaskLogPage} / ${totalPages} 页</span>
            <button class="btn-secondary btn-secondary-xs" ${data.has_more ? '' : 'disabled'} onclick="loadUrlTaskLogs(${urlTaskLogPage + 1})">下一页</button>
        `;
        if (hasRunningUrlTasks(items)) {
            startUrlTaskLogAutoRefresh();
            return true;
        }
        return false;
    } catch (err) {
        if (!options.silent) {
            renderState(list, { type: 'error', title: '任务日志加载失败', message: err.message });
        }
        pagination.innerHTML = '';
        return false;
    }
}

async function showTaskDetail(taskId) {
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay active';
    overlay.innerHTML = `
        <div class="modal task-detail-modal" role="dialog" aria-modal="true" aria-labelledby="taskDetailTitle">
            <div class="modal-header">
                <h3 id="taskDetailTitle"><i data-lucide="clipboard-list" class="lucide-icon"></i> 任务详情</h3>
                <button type="button" class="modal-close" data-action="close" aria-label="关闭">✕</button>
            </div>
            <div class="modal-body modal-body-left">
                <div class="task-detail-loading"><span class="spinner"></span> 加载中...</div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
    lucide.createIcons({ nodes: [overlay] });

    let refreshTimer = null;
    let closed = false;
    const body = overlay.querySelector('.modal-body');

    const close = () => {
        closed = true;
        if (refreshTimer) window.clearInterval(refreshTimer);
        overlay.remove();
        document.removeEventListener('keydown', onKeyDown);
    };
    const onKeyDown = (e) => {
        if (e.key === 'Escape') close();
    };
    document.addEventListener('keydown', onKeyDown);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.closest('[data-action="close"]')) close();
    });

    const renderTask = (task, { fromCache = false } = {}) => {
        if (closed || !body) return;
        body.innerHTML = renderTaskDetail(task, { fromCache });
        lucide.createIcons({ nodes: [overlay] });
    };

    const loadDetail = async ({ silent = false } = {}) => {
        try {
            const res = await fetch(`/api/tasks/${encodeURIComponent(taskId)}`);
            const task = await res.json();
            if (!res.ok || task.error) throw new Error(task.error || `HTTP ${res.status}`);
            urlTaskLogCache.set(task.task_id, task);
            renderTask(task);
            if (!['queued', 'running'].includes(task.status) && refreshTimer) {
                window.clearInterval(refreshTimer);
                refreshTimer = null;
            }
            return true;
        } catch (err) {
            const cached = urlTaskLogCache.get(taskId);
            if (cached) {
                renderTask(cached, { fromCache: true });
                return false;
            }
            if (!silent && body) {
                body.innerHTML = `<div class="task-detail-error">${escapeHtml(err.message || '任务详情加载失败')}</div>`;
            }
            return false;
        }
    };

    await loadDetail();
    refreshTimer = window.setInterval(() => {
        loadDetail({ silent: true });
    }, 2000);
}

function renderTaskDetail(task, { fromCache = false } = {}) {
    const pct = taskProgressPercent(task);
    const meta = task.meta || {};
    const metaRows = [
        ['任务 ID', task.task_id],
        ['类型', taskTypeText(task.type)],
        ['状态', taskStatusText(task.status)],
        ['创建时间', formatTaskTime(task.created_at)],
        ['更新时间', formatTaskTime(task.updated_at)],
        ['完成时间', formatTaskTime(task.finished_at) || '-'],
        ['保存到', meta.folder || '-'],
        ['模型', meta.model || '-'],
        ['并发', meta.concurrency || '-'],
        ['模块', formatGenerationModules(meta.modules || {})],
    ];
    const events = (task.events || []).map(event => `
        <div class="task-detail-event ${taskEventClass(event.event)}">
            <div class="task-detail-event-time">${escapeHtml(taskEventTime(event.time))}</div>
            <div class="task-detail-event-body">
                <div class="task-detail-event-message">${escapeHtml(event.message || '')}</div>
                ${event.data?.path ? `<button class="task-event-link" type="button" onclick="openSummaryFromTask(${jsStringLiteral(event.data.path)})">查看总结</button>` : ''}
            </div>
        </div>
    `).join('');

    return `
        ${fromCache ? '<div class="task-detail-stale">详情接口暂不可用，当前显示任务列表中的最近一次持久化记录</div>' : ''}
        <div class="task-detail-summary">
            <div>
                <div class="task-log-title">${escapeHtml(task.title || task.task_id)}</div>
                <div class="task-log-meta">${escapeHtml(task.task_id || '')}</div>
            </div>
            <span class="task-status task-status-${escapeAttr(task.status || '')}">${taskStatusText(task.status)}</span>
        </div>
        <div class="task-detail-progress">
            <div class="task-table-progress-text">
                <span>完成 ${task.completed || 0}/${task.total || 0}</span>
                <span>${pct}%</span>
            </div>
            <div class="task-log-progress">
                <div class="task-log-progress-bar" style="width:${pct}%"></div>
            </div>
        </div>
        <div class="task-detail-counts">
            <span>成功 ${task.success || 0}</span>
            <span>跳过 ${task.skipped || 0}</span>
            <span>无字幕 ${task.no_subtitle || 0}</span>
            <span>失败 ${task.errors || 0}</span>
        </div>
        <div class="task-detail-grid">
            ${metaRows.map(([label, value]) => `
                <div class="task-detail-meta-item">
                    <span>${escapeHtml(label)}</span>
                    <strong>${escapeHtml(value)}</strong>
                </div>
            `).join('')}
        </div>
        <div class="task-detail-section-title">事件记录</div>
        <div class="task-detail-events">
            ${events || '<div class="task-detail-empty">暂无事件</div>'}
        </div>
    `;
}

function openSummaryFromTask(path) {
    const safePath = String(path || '');
    if (!safePath) return;
    document.querySelector('.modal-overlay.active .modal-close')?.click();
    showCategory('all');
    openSummary(encodePath(safePath));
}

async function loadUserVideos(page = 1) {
    const userVal = document.getElementById('userInput').value.trim();
    const pageSize = Math.max(1, Math.min(50, parseInt(document.getElementById('userCount').value) || 20));
    const folder = getSelectedFolder('user');
    const grid = document.getElementById('userVideoGrid');
    const loadBtn = document.getElementById('userLoadBtn');
    if (!userVal || !grid) return;

    currentUser = userVal;
    currentUserPage = Math.max(1, page);
    loadBtn.disabled = true;
    loadBtn.innerHTML = '<span class="spinner"></span> 加载中...';
    renderState(grid, { type: 'loading', title: '加载中', message: '正在获取 UP 主视频' });

    try {
        const res = await fetch(`/api/user/videos?user=${encodeURIComponent(userVal)}&page=${currentUserPage}&page_size=${pageSize}&folder=${encodeURIComponent(folder)}`);
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || `HTTP ${res.status}`);
        }

        currentUserUid = data.uid;
        currentUserName = data.name || userVal;
        currentUserVideos = data.videos || [];
        userHasMore = !!data.has_more;
        renderUserVideos(currentUserVideos);
        renderUserPagination(data);
        updateUserSelectionToolbar();
        document.getElementById('userBrowseSubtitle').textContent =
            `${currentUserName} · 第 ${currentUserPage} 页 · 已选 ${selectedUserBvids.size} 个`;
    } catch (err) {
        document.getElementById('userBrowseSubtitle').textContent = '加载失败: ' + err.message;
        renderState(grid, {
            type: 'error',
            title: 'UP 主视频加载失败',
            message: err.message,
            actionText: '重试',
            onAction: () => loadUserVideos(currentUserPage),
        });
    } finally {
        loadBtn.disabled = false;
        loadBtn.innerHTML = '<i data-lucide="search" class="lucide-icon icon-sm"></i> 加载视频';
        lucide.createIcons({ nodes: [loadBtn] });
    }
}

function renderUserVideos(videos) {
    const grid = document.getElementById('userVideoGrid');
    if (!grid) return;
    if (!videos.length) {
        renderState(grid, { type: 'empty', title: '暂无视频', message: '当前页没有可展示的视频' });
        return;
    }

    grid.className = 'video-grid user-video-grid';
    grid.innerHTML = videos.map(v => renderUserVideoCard(v)).join('');
    lucide.createIcons({ nodes: [grid] });
}

function renderUserVideoCard(v) {
    const { badgeClass, badgeText } = summaryBadge(v.summary_status);
    const selected = selectedUserBvids.has(v.bvid);
    userVideoData.set(v.bvid, v);

    return renderSharedThumbCard({
        id: `user-card-${v.bvid}`,
        dataAttrs: `data-bvid="${escapeAttr(v.bvid)}"`,
        extraClass: `user-video-card ${selected ? 'selected' : ''}`,
        title: v.title,
        cover: v.cover,
        duration: formatDuration(v.duration),
        badgeId: `user-badge-${v.bvid}`,
        badgeClass,
        badgeText,
        metaLeft: formatTaskTime(v.created),
        metaRight: `${formatPlayCount(v.play_count)} 播放`,
        selectionHtml: `
            <label class="video-select">
                <input type="checkbox" data-bvid="${escapeAttr(v.bvid)}" ${selected ? 'checked' : ''} aria-label="选择视频">
                <span></span>
            </label>
        `,
    });
}

function renderUserPagination(data = {}) {
    const pagination = document.getElementById('userPagination');
    if (!pagination) return;
    const total = data.total || 0;
    const pageSize = data.page_size || parseInt(document.getElementById('userCount').value) || 20;
    const totalPages = total ? Math.max(1, Math.ceil(total / pageSize)) : currentUserPage + (userHasMore ? 1 : 0);
    pagination.innerHTML = `
        <button class="btn-secondary btn-secondary-xs" ${currentUserPage <= 1 ? 'disabled' : ''} onclick="loadUserVideos(${currentUserPage - 1})">上一页</button>
        <span>第 ${currentUserPage}${total ? ` / ${totalPages}` : ''} 页${total ? ` · 共 ${total} 个` : ''}</span>
        <button class="btn-secondary btn-secondary-xs" ${userHasMore ? '' : 'disabled'} onclick="loadUserVideos(${currentUserPage + 1})">下一页</button>
    `;
}

function updateUserSelectionToolbar() {
    const toolbar = document.getElementById('userSelectionToolbar');
    const submitBtn = document.getElementById('userSubmit');
    if (!toolbar || !submitBtn) return;
    const pageBvids = currentUserVideos.map(v => v.bvid).filter(Boolean);
    const selectedOnPage = pageBvids.filter(bvid => selectedUserBvids.has(bvid)).length;
    submitBtn.disabled = selectedUserBvids.size === 0;

    toolbar.innerHTML = `
        <div class="selection-summary">已选 ${selectedUserBvids.size} 个视频${pageBvids.length ? `，当前页 ${selectedOnPage}/${pageBvids.length}` : ''}</div>
        <div class="selection-actions">
            <button class="btn-secondary btn-secondary-xs" onclick="selectCurrentUserPage()">选择当前页</button>
            <button class="btn-secondary btn-secondary-xs" onclick="clearCurrentUserPageSelection()">取消当前页</button>
            <button class="btn-secondary btn-secondary-xs" onclick="clearAllUserSelection()">清空选择</button>
        </div>
    `;
}

function userCardSelector(bvid) {
    return `[data-bvid="${selectorEscape(bvid)}"].user-video-card`;
}

function updateUserVideoSummaryState(bvid, status, path) {
    if (!bvid) return;
    const normalized = status === 'skipped' ? 'success' : status;
    const { badgeClass, badgeText } = summaryBadge(normalized);
    const badge = document.getElementById(`user-badge-${bvid}`);
    if (badge) {
        badge.className = `summary-badge ${badgeClass}`;
        badge.textContent = badgeText;
    }
    const video = userVideoData.get(bvid);
    if (video) {
        video.summary_status = normalized === 'no_subtitle' ? 'no_subtitle' : 'done';
        video.has_summary = true;
        if (path) video.summary_path = path;
    }
}

function setUserVideoSelected(bvid, selected) {
    if (!bvid) return;
    if (selected) selectedUserBvids.add(bvid);
    else selectedUserBvids.delete(bvid);
    const card = document.querySelector(userCardSelector(bvid));
    if (card) card.classList.toggle('selected', selected);
    const input = document.querySelector(`${userCardSelector(bvid)} .video-select input`);
    if (input) input.checked = selected;
    updateUserSelectionToolbar();
    document.getElementById('userBrowseSubtitle').textContent =
        `${currentUserName || currentUser || 'UP 主'} · 第 ${currentUserPage} 页 · 已选 ${selectedUserBvids.size} 个`;
}

function selectCurrentUserPage() {
    currentUserVideos.forEach(v => setUserVideoSelected(v.bvid, true));
}

function clearCurrentUserPageSelection() {
    currentUserVideos.forEach(v => setUserVideoSelected(v.bvid, false));
}

function clearAllUserSelection() {
    selectedUserBvids.clear();
    renderUserVideos(currentUserVideos);
    updateUserSelectionToolbar();
    document.getElementById('userBrowseSubtitle').textContent =
        `${currentUserName || currentUser || 'UP 主'} · 第 ${currentUserPage} 页 · 已选 0 个`;
}

const userGrid = document.getElementById('userVideoGrid');
if (userGrid) {
    userGrid.addEventListener('click', (event) => {
        const card = event.target.closest('.user-video-card');
        if (!card) return;
        const bvid = card.dataset.bvid;
        const checkbox = event.target.closest('.video-select input');
        if (checkbox) {
            event.stopPropagation();
            setUserVideoSelected(bvid, checkbox.checked);
            return;
        }
        setUserVideoSelected(bvid, !selectedUserBvids.has(bvid));
    });
}

document.getElementById('userFolderSelect')?.addEventListener('change', () => {
    if (currentUser) loadUserVideos(currentUserPage);
});

// ---------------------------------------------------------------------------
// Favorites Browser
// ---------------------------------------------------------------------------
let currentFavId = null;
let defaultFavId = null;
let currentFavPage = 1;
let favHasMore = false;
const favVideoData = new Map(); // bvid -> { summaryPath, title, ... }
let pendingSummarizeBvids = [];
let activeUndoToast = null;
let favoriteFoldersClickBound = false;

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
        defaultFavId = defaultFolder ? defaultFolder.id : null;

        let html = '';

        // Default folder always visible
        if (defaultFolder) {
            html += `
                <div class="fav-folder-item" data-fav-id="${defaultFolder.id}" data-fav-title="${escapeAttr(defaultFolder.title)}" title="${escapeAttr(defaultFolder.title)}">
                    <span class="folder-name"><i data-lucide="folder" class="lucide-icon"></i> ${escapeHtml(defaultFolder.title)}</span>
                    <span class="folder-count">${defaultFolder.count}</span>
                </div>`;
        }

        // Other folders in collapsible section
        if (otherFolders.length > 0) {
            html += `
                <div class="fav-folder-toggle" onclick="toggleFavFolders()" title="其他收藏">
                    <span class="toggle-arrow" id="favFoldArrow"><i data-lucide="chevron-right" class="lucide-icon"></i></span>
                    <span>其他收藏 (${otherFolders.length})</span>
                </div>
                <div class="fav-folder-list collapsed" id="favFolderList">
                    ${otherFolders.map(f => `
                        <div class="fav-folder-item" data-fav-id="${f.id}" data-fav-title="${escapeAttr(f.title)}" title="${escapeAttr(f.title)}">
                            <span class="folder-name"><i data-lucide="folder" class="lucide-icon"></i> ${escapeHtml(f.title)}</span>
                            <span class="folder-count">${f.count}</span>
                        </div>
                    `).join('')}
                </div>`;
        }

        container.innerHTML = html;
        lucide.createIcons({ nodes: [container] });

        if (!favoriteFoldersClickBound) {
            favoriteFoldersClickBound = true;
            container.addEventListener('click', (e) => {
                const item = e.target.closest('.fav-folder-item');
                if (!item) return;
                const favId = parseInt(item.dataset.favId);
                const title = item.dataset.favTitle;
                selectFavoriteFolder(favId, title);
            });
        }

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
    const toggle = document.querySelector('.fav-folder-toggle');
    if (!list) return;
    list.classList.toggle('collapsed');
    if (toggle) {
        toggle.classList.toggle('expanded', !list.classList.contains('collapsed'));
    }
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
    favHeaderBeforeReading = null;

    // Clear and load — reset display states
    const grid = document.getElementById('favVideoGrid');
    renderState(grid, { type: 'loading', title: '加载中', message: '正在获取收藏视频' });
    grid.style.display = '';
    document.getElementById('favAutoProgress').innerHTML = '';
    document.getElementById('favReadingView').classList.remove('active');
    updateGlobalBackButton();
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
        loadMore.style.display = favHasMore ? 'block' : 'none';

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
        cover: v.cover || '',
        duration: v.duration || 0,
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
        <div class="generation-actions generation-actions-compact mt-2">
            <button class="btn-secondary btn-secondary-compact" onclick="startPendingSummarize()">
                <i data-lucide="play" class="lucide-icon icon-xs"></i> 总结未总结视频
            </button>
            ${renderGenerationModules('fav')}
            <select class="input input-compact" id="favFolderSelect">${renderFolderOptions(DEFAULT_LOCAL_FOLDER, '默认文件夹')}</select>
        </div>
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
        const modules = getGenerationModules('fav');
        const folder = getSelectedFolder('fav');
        const res = await fetch('/api/favorites/summarize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bvids: targets, modules, folder })
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
                            progressEl.innerHTML = `<div class="text-warning">已完成，仍有 ${remaining} 个视频可重试</div>`;
                        } else {
                            progressEl.innerHTML = `<div class="text-success">处理完成</div>`;
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
    updateGlobalBackButton();

    try {
        // Encode path segments for URL (preserve /)
        const encodedPath = path.split('/').map(s => encodeURIComponent(s)).join('/');
        const res = await fetch(`/api/summary-detail/${encodedPath}`);
        if (!res.ok) {
            renderState(readingContent, { type: 'error', title: '加载失败', message: `HTTP ${res.status}: 无法加载总结` });
            return;
        }
        const data = await res.json();
        if (data.content) {
            const isNoSub = data.content.includes('无法获取字幕');
            const vdata = favVideoData.get(bvid) || {};
            const headerInfo = getSummaryHeaderInfo(data, vdata, bvid);
            if (!favHeaderBeforeReading) {
                favHeaderBeforeReading = snapshotHeader('favBrowseTitle', 'favBrowseSubtitle');
            }
            applyVideoHeader('favBrowseTitle', 'favBrowseSubtitle', headerInfo);
            renderReadingActions('favReadingActions', {
                bvid: headerInfo.bvid || bvid,
                isNoSub,
                showOpen: true,
                showUnfav: true,
                enableRetry: true,
                enableAsr: true,
            });

            readingContent.innerHTML = renderSummaryDetail(data, { fallbackBvid: bvid, knownVideo: vdata });
            setupSummaryDetailInteractions(readingContent);
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
    document.getElementById('favLoadMore').style.display = favHasMore ? 'block' : 'none';
    restoreHeader('favBrowseTitle', 'favBrowseSubtitle', favHeaderBeforeReading);
    favHeaderBeforeReading = null;
    updateGlobalBackButton();
}

async function retrySummarize(bvid, isNoSub = false) {
    // Support both browse and favorites reading views
    const favView = document.getElementById('favReadingView');
    const isFavView = favView && favView.classList.contains('active');
    const readingContent = document.getElementById(isFavView ? 'favReadingContent' : 'readingContent');

    // Legacy no-subtitle summaries can be regenerated directly through the configured ASR mode.
    if (isNoSub) {
        return retryWithASR(bvid, readingContent);
    }

    renderState(readingContent, { type: 'loading', title: '处理中', message: '正在重新转录并生成详细总结' });

    try {
        const res = await fetch(`/api/retry/${bvid}`, { method: 'POST' });
        const data = await res.json();
        if (data.error) {
            renderState(readingContent, { type: 'error', title: '重试失败', message: data.error });
            return;
        }

        const taskId = data.task_id;
        renderState(readingContent, { type: 'loading', title: '处理中', message: '正在转录' });

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
                    renderState(readingContent, { type: 'loading', title: '转录不可用', message: '正在切换到语音识别...' });
                    retryWithASR(bvid, readingContent);
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

async function retryWithASR(bvid, readingContent) {
    renderState(readingContent, { type: 'loading', title: '转录详细总结', message: '准备中...' });

    try {
        const res = await fetch(`/api/asr-summarize/${bvid}`, { method: 'POST' });
        if (!res.ok) {
            const err = await res.json();
            renderState(readingContent, { type: 'error', title: '转录失败', message: err.error || '未知错误' });
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
                        renderState(readingContent, { type: 'error', title: '转录失败', message: d.message });
                        return;
                    }

                    if (d.step === 'done') {
                        const badge = document.getElementById(`badge-${bvid}`);
                        if (badge) {
                            badge.className = 'summary-badge done';
                            badge.textContent = statusText('success');
                        }
                        const vdata = favVideoData.get(bvid);
                        if (vdata && d.path) {
                            vdata.summaryPath = d.path;
                        }
                        // Show the summary in reading view
                        const favView = document.getElementById('favReadingView');
                        const isFavView = favView && favView.classList.contains('active');
                        if (isFavView) {
                            showVideoSummary(bvid, d.path);
                        } else if (d.path) {
                            openSummary(encodePath(d.path));
                        }
                        loadSidebarBrowse();
                        return;
                    }

                    renderState(readingContent, { type: 'loading', title: '转录详细总结', message: d.message });
                } catch (_) { }
            }
        }
    } catch (err) {
        renderState(readingContent, { type: 'error', title: '转录失败', message: err.message });
    }
}

async function asrSummarize(bvid) {
    // Create toast notification
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = `
        <div class="toast-title">转录详细总结</div>
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
                        msgEl.textContent = `成功: 详细总结完成（${d.llm_time}s）`;
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

async function unfavoriteFromBrowse(bvid, btnEl) {
    if (!defaultFavId || !bvid) return;
    const cardEl = btnEl ? btnEl.closest('.video-card, .browse-compact-item') : null;
    const removedVideo = currentBrowseItems.find(v => v.bvid === bvid) || { name: bvid };

    if (cardEl) {
        cardEl.style.opacity = '0.4';
        cardEl.style.pointerEvents = 'none';
    }

    try {
        const res = await fetch(`/api/favorites/${defaultFavId}/video/${bvid}`, {
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

        currentBrowseItems = currentBrowseItems.filter(v => v.bvid !== bvid);
        const favCat = summariesData?.categories?.find(c => c.type === 'favorites');
        if (favCat?.items) {
            favCat.items = favCat.items.filter(v => v.bvid !== bvid);
            favCat.count = favCat.items.length;
        }
        document.getElementById('browseSubtitle').textContent = `共 ${currentBrowseItems.length} 篇总结`;
        renderBrowseItems(currentBrowseItems);

        notifyUnfavoriteUndo({ favId: defaultFavId, bvid, title: removedVideo.title || removedVideo.name || bvid });
    } catch (err) {
        await showAlert('取消收藏失败: ' + err.message, '操作失败');
        if (cardEl) {
            cardEl.style.opacity = '';
            cardEl.style.pointerEvents = '';
        }
    }
}

async function unfavoriteFromReading(bvid) {
    const isBrowseReading = document.getElementById('readingView')?.classList.contains('active');
    const favId = (isBrowseReading && currentBrowseType === 'favorites') ? defaultFavId : currentFavId;
    if (!favId) return;
    const removedVideo = favVideoData.get(bvid) || currentBrowseItems.find(v => v.bvid === bvid) || { title: bvid };

    try {
        const res = await fetch(`/api/favorites/${favId}/video/${bvid}`, {
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
        if (isBrowseReading && currentBrowseType === 'favorites') {
            currentBrowseItems = currentBrowseItems.filter(v => v.bvid !== bvid);
            const favCat = summariesData?.categories?.find(c => c.type === 'favorites');
            if (favCat?.items) {
                favCat.items = favCat.items.filter(v => v.bvid !== bvid);
                favCat.count = favCat.items.length;
            }
            document.getElementById('browseSubtitle').textContent = `共 ${currentBrowseItems.length} 篇总结`;
        }
        // Go back to grid
        if (isBrowseReading && currentBrowseType === 'favorites') {
            closeReading();
            renderBrowseItems(currentBrowseItems);
        } else {
            closeFavReading();
        }
        notifyUnfavoriteUndo({ favId, bvid, title: removedVideo.title });
    } catch (err) {
        await showAlert('取消收藏失败: ' + err.message, '操作失败');
    }
}

function showPage(pageId) {
    switchToPage(pageId, null);
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
let settingsLoaded = false;

function updateAsrSettingsVisibility() {
    const mode = document.getElementById('settingsAsrMode')?.value || 'local';
    document.getElementById('settingsLocalAsrPanel')?.classList.toggle('hidden', mode !== 'local');
    document.getElementById('settingsBailianAsrPanel')?.classList.toggle('hidden', mode !== 'bailian');
}

async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        const data = await res.json();
        document.getElementById('settingsAsrMode').value = data.asr_mode || 'local';
        document.getElementById('settingsBaseUrl').value = data.base_url || '';
        document.getElementById('settingsToken').placeholder = data.auth_token_masked || '输入 API Token';
        document.getElementById('settingsToken').value = '';
        document.getElementById('settingsModel').value = data.default_model || '';
        document.getElementById('settingsTaskConcurrency').value = data.task_concurrency || 12;
        document.getElementById('settingsWhisperModel').value = data.whisper_model || 'whisper-tiny';
        document.getElementById('settingsWhisperDevice').value = data.whisper_device || 'auto';
        document.getElementById('settingsWhisperComputeType').value = data.whisper_compute_type || 'default';
        document.getElementById('settingsBailianApiKey').placeholder = data.bailian_api_key_masked || '输入百炼 API Key';
        document.getElementById('settingsBailianApiKey').value = '';
        document.getElementById('settingsBailianAsrModel').value = data.bailian_asr_model || 'qwen3-asr-flash-filetrans';
        document.getElementById('settingsBailianAsrBaseUrl').value = data.bailian_asr_base_url || 'https://dashscope.aliyuncs.com/api/v1';
        document.getElementById('settingsBailianAsrLanguage').value = data.bailian_asr_language || '';
        document.getElementById('settingsCloudflareR2AccountId').value = data.cloudflare_r2_account_id || '';
        document.getElementById('settingsCloudflareR2EndpointUrl').value = data.cloudflare_r2_endpoint_url || '';
        document.getElementById('settingsCloudflareR2Bucket').value = data.cloudflare_r2_bucket || '';
        document.getElementById('settingsCloudflareR2AccessKeyId').value = data.cloudflare_r2_access_key_id || '';
        document.getElementById('settingsCloudflareR2SecretAccessKey').placeholder = data.cloudflare_r2_secret_access_key_masked || '输入 R2 Secret Access Key';
        document.getElementById('settingsCloudflareR2SecretAccessKey').value = '';
        document.getElementById('settingsCloudflareR2PublicBaseUrl').value = data.cloudflare_r2_public_base_url || '';
        document.getElementById('settingsCloudflareR2KeyPrefix').value = data.cloudflare_r2_key_prefix || 'bilibili-summary/asr';
        document.getElementById('settingsCloudflareR2DeleteAfterUse').checked = data.cloudflare_r2_delete_after_use !== false;
        document.getElementById('settingsTelegramEnabled').checked = !!data.telegram_bot_enabled;
        document.getElementById('settingsTelegramToken').placeholder = data.telegram_bot_token_masked || '已保存，输入新 Token 才会替换';
        document.getElementById('settingsTelegramToken').value = '';
        document.getElementById('settingsTelegramAllowedUsers').value = data.telegram_allowed_user_ids || '';
        document.getElementById('settingsTelegramOutputFolder').value = data.telegram_output_folder || DEFAULT_LOCAL_FOLDER;
        document.getElementById('settingsTelegramStatus').textContent = data.telegram_bot_running
            ? '机器人正在轮询 Telegram 消息'
            : (data.telegram_bot_last_error || '机器人未运行；启用并保存有效 Bot Token 后启动');
        updateAsrSettingsVisibility();
        settingsLoaded = true;
    } catch (err) {
        console.error('加载设置失败:', err);
    }
}

async function saveSettings() {
    const statusEl = document.getElementById('settingsSaveStatus');
    const asrMode = document.getElementById('settingsAsrMode').value;
    const baseUrl = document.getElementById('settingsBaseUrl').value.trim();
    const token = document.getElementById('settingsToken').value.trim();
    const defaultModel = document.getElementById('settingsModel').value.trim();
    const taskConcurrency = Math.max(1, Math.min(20, parseInt(document.getElementById('settingsTaskConcurrency').value) || 12));
    const whisperModel = document.getElementById('settingsWhisperModel').value.trim();
    const whisperDevice = document.getElementById('settingsWhisperDevice').value.trim();
    const whisperComputeType = document.getElementById('settingsWhisperComputeType').value.trim();
    const bailianApiKey = document.getElementById('settingsBailianApiKey').value.trim();
    const bailianAsrModel = document.getElementById('settingsBailianAsrModel').value.trim();
    const bailianAsrBaseUrl = document.getElementById('settingsBailianAsrBaseUrl').value.trim();
    const bailianAsrLanguage = document.getElementById('settingsBailianAsrLanguage').value.trim();
    const cloudflareR2AccountId = document.getElementById('settingsCloudflareR2AccountId').value.trim();
    const cloudflareR2EndpointUrl = document.getElementById('settingsCloudflareR2EndpointUrl').value.trim();
    const cloudflareR2Bucket = document.getElementById('settingsCloudflareR2Bucket').value.trim();
    const cloudflareR2AccessKeyId = document.getElementById('settingsCloudflareR2AccessKeyId').value.trim();
    const cloudflareR2SecretAccessKey = document.getElementById('settingsCloudflareR2SecretAccessKey').value.trim();
    const cloudflareR2PublicBaseUrl = document.getElementById('settingsCloudflareR2PublicBaseUrl').value.trim();
    const cloudflareR2KeyPrefix = document.getElementById('settingsCloudflareR2KeyPrefix').value.trim();
    const cloudflareR2DeleteAfterUse = document.getElementById('settingsCloudflareR2DeleteAfterUse').checked;
    const telegramBotEnabled = document.getElementById('settingsTelegramEnabled').checked;
    const telegramBotToken = document.getElementById('settingsTelegramToken').value.trim();
    const telegramAllowedUsers = document.getElementById('settingsTelegramAllowedUsers').value.trim();
    const telegramOutputFolder = document.getElementById('settingsTelegramOutputFolder').value.trim();

    statusEl.className = 'settings-save-status text-muted-md';
    statusEl.textContent = '保存中...';

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                asr_mode: asrMode,
                base_url: baseUrl,
                auth_token: token,
                default_model: defaultModel,
                task_concurrency: taskConcurrency,
                whisper_model: whisperModel,
                whisper_device: whisperDevice,
                whisper_compute_type: whisperComputeType,
                bailian_api_key: bailianApiKey,
                bailian_asr_base_url: bailianAsrBaseUrl,
                bailian_asr_model: bailianAsrModel,
                bailian_asr_language: bailianAsrLanguage,
                cloudflare_r2_account_id: cloudflareR2AccountId,
                cloudflare_r2_endpoint_url: cloudflareR2EndpointUrl,
                cloudflare_r2_bucket: cloudflareR2Bucket,
                cloudflare_r2_access_key_id: cloudflareR2AccessKeyId,
                cloudflare_r2_secret_access_key: cloudflareR2SecretAccessKey,
                cloudflare_r2_public_base_url: cloudflareR2PublicBaseUrl,
                cloudflare_r2_key_prefix: cloudflareR2KeyPrefix,
                cloudflare_r2_delete_after_use: cloudflareR2DeleteAfterUse,
                telegram_bot_enabled: telegramBotEnabled,
                telegram_bot_token: telegramBotToken,
                telegram_allowed_user_ids: telegramAllowedUsers,
                telegram_output_folder: telegramOutputFolder,
            })
        });
        const data = await res.json();
        if (data.success) {
            const telegramStatusEl = document.getElementById('settingsTelegramStatus');
            const telegramMessage = data.telegram_bot_running
                ? '机器人已启动并正在轮询 Telegram 消息'
                : (data.telegram_bot_last_error || '机器人未运行');
            statusEl.className = data.telegram_bot_last_error
                ? 'settings-save-status text-warning'
                : 'settings-save-status text-success';
            statusEl.textContent = data.telegram_bot_last_error
                ? `配置已保存，但机器人未启动：${data.telegram_bot_last_error}`
                : '保存成功';
            if (telegramStatusEl) {
                telegramStatusEl.textContent = telegramMessage;
            }
            // Reload to show masked token
            setTimeout(() => loadSettings(), 500);
        } else {
            statusEl.className = 'settings-save-status text-error';
            statusEl.textContent = '保存失败: ' + (data.error || '');
        }
    } catch (err) {
        statusEl.className = 'settings-save-status text-error';
        statusEl.textContent = '保存失败: ' + err.message;
    }
    setTimeout(() => {
        statusEl.className = 'settings-save-status';
        statusEl.textContent = '';
    }, 3000);
}

function toggleTokenVisibility() {
    const input = document.getElementById('settingsToken');
    const btn = document.getElementById('toggleTokenBtn');
    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = '<i data-lucide="eye-off" class="lucide-icon icon-sm"></i>';
    } else {
        input.type = 'password';
        btn.innerHTML = '<i data-lucide="eye" class="lucide-icon icon-sm"></i>';
    }
    lucide.createIcons({ nodes: [btn] });
}

function toggleTelegramTokenVisibility() {
    const input = document.getElementById('settingsTelegramToken');
    const btn = document.getElementById('toggleTelegramTokenBtn');
    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = '<i data-lucide="eye-off" class="lucide-icon icon-sm"></i>';
    } else {
        input.type = 'password';
        btn.innerHTML = '<i data-lucide="eye" class="lucide-icon icon-sm"></i>';
    }
    lucide.createIcons({ nodes: [btn] });
}

function toggleBailianTokenVisibility() {
    const input = document.getElementById('settingsBailianApiKey');
    const btn = document.getElementById('toggleBailianTokenBtn');
    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = '<i data-lucide="eye-off" class="lucide-icon icon-sm"></i>';
    } else {
        input.type = 'password';
        btn.innerHTML = '<i data-lucide="eye" class="lucide-icon icon-sm"></i>';
    }
    lucide.createIcons({ nodes: [btn] });
}

function toggleR2SecretVisibility() {
    const input = document.getElementById('settingsCloudflareR2SecretAccessKey');
    const btn = document.getElementById('toggleR2SecretBtn');
    if (input.type === 'password') {
        input.type = 'text';
        btn.innerHTML = '<i data-lucide="eye-off" class="lucide-icon icon-sm"></i>';
    } else {
        input.type = 'password';
        btn.innerHTML = '<i data-lucide="eye" class="lucide-icon icon-sm"></i>';
    }
    lucide.createIcons({ nodes: [btn] });
}

// Load settings when navigating to settings page
const origSwitchToPage = switchToPage;
switchToPage = function (pageId, navEl) {
    origSwitchToPage(pageId, navEl);
    if (pageId === 'url-page') {
        loadUrlTaskLogs(urlTaskLogPage);
    }
    if (pageId === 'settings-page' && !settingsLoaded) {
        loadSettings();
    }
};

loadUrlTaskLogs(1);
