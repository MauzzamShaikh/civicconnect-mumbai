/* ============================================================
   CivicSocial — Frontend Application
   Pure Vanilla JS SPA · Twitter-style layout
   API: http://localhost:8001
   ============================================================ */

const configuredApiUrl = document.querySelector('meta[name="civicsocial-api-url"]')?.content;
const API = window.CIVICSOCIAL_API || configuredApiUrl || `${window.location.protocol}//${window.location.hostname}:8001`;

/* ============================================================
   State
   ============================================================ */
let state = {
  token: null,
  user: null,
  currentPage: 'home',
  bookmarks: [], // stored locally (no API)
  feed: [],
  profile: null,
  chatTarget: null,
  settingsTab: 'account',
  toastTimer: null,
  feedPollTimer: null,
  notificationPollTimer: null,
  notificationRefreshPromise: null,
};

/* ============================================================
   Avatar helpers
   ============================================================ */
const AV_COLORS = ['av-1', 'av-2', 'av-3', 'av-4', 'av-5', 'av-6', 'av-7'];

function avatarColor(handle) {
  let h = 0;
  for (const c of String(handle || '')) h = (h * 31 + c.charCodeAt(0)) & 0xffff;
  return AV_COLORS[h % AV_COLORS.length];
}

function avatarInitials(name) {
  const parts = String(name || '?').trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return String(name || '?').slice(0, 2).toUpperCase();
}

function renderAvatar(user, cls = 'avatar-sm') {
  const avatar = user?.avatar_data || user?.avatar || null;
  if (avatar) {
    return `<div class="${cls} ${avatarColor(user.handle)}" style="padding:0;"><img src="${esc(avatar)}" alt="${esc(user.display_name)}" onerror="this.parentElement.textContent=this.nextElementSibling.textContent"><span class="avatar-fallback" hidden>${esc(avatarInitials(user?.display_name || user?.handle))}</span></div>`;
  }
  return `<div class="${cls} ${avatarColor(user?.handle)}">${avatarInitials(user?.display_name || user?.handle)}</div>`;
}

function setAvatarEl(el, user) {
  el.className = `avatar-sm ${avatarColor(user?.handle)}`;
  const avatar = user?.avatar_data || user?.avatar || null;
  if (avatar) {
    el.innerHTML = `<img src="${esc(avatar)}" alt="">`;
    el.style.padding = '0';
    el.querySelector('img').addEventListener('error', () => {
      el.textContent = avatarInitials(user?.display_name || user?.handle);
      el.style.padding = '';
    }, { once: true });
  } else {
    el.textContent = avatarInitials(user?.display_name || user?.handle);
  }
}

/* ============================================================
   Utilities
   ============================================================ */
function esc(str) {
  return String(str ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatTime(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const now = new Date();
  const diff = Math.floor((now - d) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)}d ago`;
  return d.toLocaleDateString('en-IN', { month: 'short', day: 'numeric', timeZone: 'Asia/Kolkata' });
}

function formatFullTime(isoStr) {
  if (!isoStr) return '';
  return new Date(isoStr).toLocaleString('en-IN', {
    hour: 'numeric', minute: '2-digit',
    month: 'short', day: 'numeric', year: 'numeric', timeZone: 'Asia/Kolkata'
  });
}

function linkifyText(text) {
  return esc(text)
    .replace(/(#\w+)/g, '<span class="hashtag">$1</span>')
    .replace(/@([a-zA-Z0-9_]+)/g, (_match, handle) => `<button class="mention mention-link" onclick="event.stopPropagation(); navigate('user-profile', {handle:'${handle}'})">@${handle}</button>`);
}

function toast(msg, dur = 2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => el.classList.remove('show'), dur);
}

function showLoading(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = [1, 2, 3].map(() => `
    <div class="skeleton-post">
      <div class="skeleton skeleton-avatar"></div>
      <div class="skeleton-body">
        <div class="skeleton skeleton-line-1"></div>
        <div class="skeleton skeleton-line-2"></div>
        <div class="skeleton skeleton-line-3"></div>
      </div>
    </div>`).join('');
}

/* ============================================================
   API helpers
   ============================================================ */
async function apiFetch(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (state.token) headers['Authorization'] = `Bearer ${state.token}`;
  const res = await fetch(API + path, { ...opts, headers, signal: opts.signal || AbortSignal.timeout(20000) });
  const data = await res.json().catch(() => ({}));
  if ((res.status === 401 || (res.status === 403 && data.error_code === 'EMAIL_NOT_VERIFIED')) && state.token) {
    const rejectedEmail = data.email || state.user?.email || '';
    clearAuth();
    showAuthLanding();
    if (data.error_code === 'EMAIL_NOT_VERIFIED') showVerificationRequired(rejectedEmail);
    else toast('Your session has ended. Please sign in again.');
  }
  if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { code: data.error_code, email: data.email });
  return data;
}

/* ============================================================
   Auth persistence
   ============================================================ */
function saveAuth(token, user) {
  state.token = token;
  state.user = user;
  try {
    localStorage.setItem('cs_token', token);
    localStorage.setItem('cs_user', JSON.stringify(user));
  } catch (_) { }
}

function loadAuth() {
  try {
    state.token = localStorage.getItem('cs_token');
    const u = localStorage.getItem('cs_user');
    if (u) state.user = JSON.parse(u);
  } catch (_) { }
}

function clearAuth() {
  state.token = null;
  state.user = null;
  try { localStorage.removeItem('cs_token'); localStorage.removeItem('cs_user'); } catch (_) { }
}

/* ============================================================
   Bookmarks (local)
   ============================================================ */
function loadBookmarks() {
  try { state.bookmarks = JSON.parse(localStorage.getItem('cs_bookmarks') || '[]'); } catch (_) { state.bookmarks = []; }
}

function saveBookmarks() {
  try { localStorage.setItem('cs_bookmarks', JSON.stringify(state.bookmarks)); } catch (_) { }
}

function isBookmarked(postId) {
  return state.bookmarks.some(b => b.id === postId);
}

function toggleBookmark(post) {
  if (isBookmarked(post.id)) {
    state.bookmarks = state.bookmarks.filter(b => b.id !== post.id);
    saveBookmarks();
    return false;
  } else {
    state.bookmarks.unshift(post);
    saveBookmarks();
    return true;
  }
}

/* ============================================================
   App Boot
   ============================================================ */
async function boot() {
  loadAuth();
  loadBookmarks();
  try { document.documentElement.dataset.theme = localStorage.getItem('cs_theme') === 'light' ? 'light' : 'dark'; } catch (_) { document.documentElement.dataset.theme = 'dark'; }
  const linkPath = window.location.pathname.replace(/\/$/, '').toLowerCase();
  const actionToken = new URLSearchParams(window.location.search).get('token');
  if (linkPath.endsWith('/verify-email') && actionToken) {
    clearAuth();
    showAuthLanding();
    const panel = document.getElementById('auth-form-panel');
    if (panel) panel.innerHTML = '<div class="auth-form-inner"><div class="auth-form-title">Verifying your email…</div></div>';
    try {
      const result = await apiFetch(`/auth/verify-email?token=${encodeURIComponent(actionToken)}`);
      if (panel) panel.innerHTML = `<div class="auth-form-inner"><div class="auth-form-title">Email verified</div><div class="auth-form-sub">${esc(result.message || 'Your email is verified. You can sign in now.')}</div><button class="btn-primary full-width" onclick="history.replaceState(null,'','/'); showLogin()">Continue to sign in</button></div>`;
    } catch (error) {
      if (panel) panel.innerHTML = `<div class="auth-form-inner"><div class="auth-form-title">Verification link expired</div><div class="auth-form-sub">${esc(error.message)}</div><button class="btn-primary full-width" onclick="history.replaceState(null,'','/'); showLogin()">Back to sign in</button></div>`;
    }
    return;
  }
  if (linkPath.endsWith('/reset-password') && actionToken) {
    showAuthLanding();
    const panel = document.getElementById('auth-form-panel');
    if (panel) panel.innerHTML = renderResetPasswordForm(actionToken);
    return;
  }
  if (state.token && state.user) {
    try {
      const session = await apiFetch('/auth/session');
      state.user = session.user;
      showApp();
      const sharedPost = window.location.hash.match(/^#post\/(\d+)/);
      if (sharedPost) navigate('post-detail', { postId: Number(sharedPost[1]) });
    } catch (error) {
      if (error.code === 'EMAIL_NOT_VERIFIED') showVerificationRequired(error.email || state.user?.email || '');
      else if (state.token) { clearAuth(); showAuthLanding(); }
    }
  } else {
    showAuthLanding();
  }
}

function renderResetPasswordForm(token) {
  return `<div class="auth-form-inner"><div class="auth-form-title">Choose a new password</div><div class="auth-form-sub">Use at least 8 characters.</div><form onsubmit="submitResetPassword(event, '${esc(token)}')"><div class="form-group"><label class="form-label">New password</label><input class="form-input" id="reset-password" type="password" minlength="8" autocomplete="new-password" required></div><button class="btn-primary full-width" type="submit">Update password</button></form></div>`;
}

async function submitResetPassword(event, token) {
  event.preventDefault();
  const new_password = document.getElementById('reset-password').value;
  try {
    const result = await apiFetch('/auth/reset-password', { method: 'POST', body: JSON.stringify({ token, new_password }) });
    history.replaceState(null, '', '/');
    const panel = document.getElementById('auth-form-panel');
    if (panel) panel.innerHTML = renderLoginForm(result.message || 'Password updated. Please sign in.');
  } catch (error) { toast(error.message || 'Could not update password'); }
}

function showApp() {
  document.getElementById('auth-screen').classList.add('hidden');
  const shell = document.getElementById('app-shell');
  shell.classList.remove('hidden');
  shell.style.display = 'grid';

  // Populate sidebar user
  const u = state.user;
  if (u) {
    const sa = document.getElementById('sidebar-avatar');
    const ma = document.getElementById('mobile-avatar');
    setAvatarEl(sa, u);
    setAvatarEl(ma, u);
    setAvatarEl(document.getElementById('compose-avatar'), u);
    document.getElementById('sidebar-name').innerHTML = `${esc(u.display_name)} ${u.is_official ? '<span class="verified-badge" title="Official account">✓</span>' : ''}`;
    document.getElementById('sidebar-handle').textContent = `@${u.handle}`;
    document.getElementById('pm-handle').textContent = `@${u.handle}`;
  }

  navigate('home');
  renderRightPanel();
  refreshNotifBadge();
  if (!state.notificationPollTimer) state.notificationPollTimer = setInterval(() => {
    if (state.token && !document.hidden) refreshNotifBadge();
  }, 15000);
}

async function refreshNotifBadge() {
  if (!state.token) return updateNotifBadge(0);
  if (state.notificationRefreshPromise) return state.notificationRefreshPromise;
  state.notificationRefreshPromise = (async () => {
    try {
      const data = await apiFetch('/notifications');
      if (!Array.isArray(data.notifications)) throw new Error('Unexpected notifications response');
      updateNotifBadge(data.notifications.filter(item => !item.read).length);
      if (state.currentPage === 'notifications') renderNotificationList(data.notifications);
    } catch (_) { /* Keep the last confirmed backend count when a refresh fails. */ }
    finally { state.notificationRefreshPromise = null; }
  })();
  return state.notificationRefreshPromise;
}

function renderNotificationList(notifications) {
  const list = document.getElementById('notif-list');
  if (!list) return;
  list.innerHTML = notifications.length ? notifications.map(n => renderNotifItem(n)).join('') : '<div class="empty-state"><div class="empty-state-icon">' + mentionSvg() + '</div><h3>You’re all caught up</h3><p>Likes, replies, follows, and mentions will appear here.</p></div>';
}

window.addEventListener('focus', () => { if (state.token) refreshNotifBadge(); });

/* ============================================================
   Navigation
   ============================================================ */
function navigate(page, payload = {}) {
  if (page !== 'home' && state.feedPollTimer) {
    clearInterval(state.feedPollTimer);
    state.feedPollTimer = null;
  }
  state.currentPage = page;
  if (page === 'post-detail' && payload.postId) history.replaceState(null, '', `#post/${payload.postId}`);
  else if (window.location.hash.startsWith('#post/')) history.replaceState(null, '', window.location.pathname + window.location.search);

  // Update nav active states
  document.querySelectorAll('.nav-item[data-page], .mob-nav-item[data-page]').forEach(b => {
    b.classList.toggle('active', b.dataset.page === page);
  });

  const main = document.getElementById('main-content');
  switch (page) {
    case 'home': renderHome(main); break;
    case 'explore': renderExplore(main); break;
    case 'notifications': renderNotifications(main); break;
    case 'bookmarks': renderBookmarks(main); break;
    case 'profile': renderProfile(main, payload.handle || state.user?.handle); break;
    case 'settings': renderSettings(main); break;
    case 'post-detail': renderPostDetail(main, payload.postId); break;
    case 'user-profile': renderProfile(main, payload.handle); break;
    default: renderHome(main);
  }

  // Scroll to top
  main.scrollTo(0, 0);
  window.scrollTo(0, 0);
}

/* ============================================================
   Right Panel (trending + suggestions)
   ============================================================ */
function renderRightPanel() {
  const el = document.getElementById('right-panel-content');
  if (!el) return;

  el.innerHTML = `
    <div class="widget-card">
      <h2 class="widget-title">What’s happening</h2>
      ${trendingTopics().map(t => `
        <div class="trend-item" onclick="exploreTag('${esc(t.tag)}')">
          <div class="trend-info">
            <div class="trend-category">${esc(t.category)}</div>
            <div class="trend-tag">${esc(t.tag)}</div>
            <div class="trend-count">${t.count.toLocaleString()} conversations</div>
          </div>
          <svg class="trend-more" width="18" height="18" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="5" r="1.5" fill="currentColor"/>
            <circle cx="12" cy="12" r="1.5" fill="currentColor"/>
            <circle cx="12" cy="19" r="1.5" fill="currentColor"/>
          </svg>
        </div>`).join('') || '<p class="widget-empty">Hashtags from new posts will appear here.</p>'}
      <div class="widget-footer">
        <button onclick="navigate('explore')">See all trending →</button>
      </div>
    </div>

    <div class="widget-card">
      <h2 class="widget-title">Neighbors to know</h2>
      ${suggestedUsers().map(u => `
        <div class="neighbor-item">
          ${renderAvatar(u)}
          <div class="neighbor-info">
            <div class="neighbor-name">${esc(u.display_name)} ${u.is_official ? '<span class="verified-badge" title="Official account">✓</span>' : ''}</div>
          <div class="neighbor-sub">@${esc(u.handle)}${u.is_official ? ' · Official' : ''}</div>
          </div>
          <button class="btn-follow" onclick="followUser(this,'${esc(u.handle)}')">Follow</button>
        </div>`).join('') || '<p class="widget-empty">People who post will appear here.</p>'}
      <div class="widget-footer">
        <button onclick="navigate('explore')">Find more neighbors →</button>
      </div>
    </div>

    <div class="rs-footer">
      © 2026 CivicSocial · Your town, in the conversation
    </div>
  `;
}

function trendingTopics() {
  const counts = new Map();
  state.feed.forEach(post => (post.text?.match(/#[\p{L}\p{N}_]+/gu) || []).forEach(tag => counts.set(tag, (counts.get(tag) || 0) + 1)));
  return [...counts].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([tag, count]) => ({ tag, category: 'From recent posts', count }));
}

function suggestedUsers() {
  const seen = new Set([state.user?.handle]);
  return state.feed.map(post => post.author).filter(user => user?.handle && !seen.has(user.handle) && seen.add(user.handle)).slice(0, 3);
}

/* ============================================================
   Sample Data
   ============================================================ */
function samplePosts() {
  return [
    {
      id: 1001,
      author: { handle: 'jordan_rivera', display_name: 'Jordan Rivera', is_official: true },
      text: "Saturday cleanup was a HUGE success! We filled 18 bags and met so many lovely neighbors. Huge thanks to everyone who showed up for #KeepOaklandClean 🌿 Next one is in two weeks — mark your calendars!",
      like_count: 32, reply_count: 8, created_at: new Date(Date.now() - 24 * 60 * 1000).toISOString(),
    },
    {
      id: 1002,
      author: { handle: 'amaralee', display_name: 'Amara Lee', is_official: false },
      text: "The city council is reviewing the safer streets proposal this Thursday. Protected crossings around schools would make such a difference. Meeting starts at 6pm at City Hall — hope to see you there! #SafeStreets",
      like_count: 24, reply_count: 12, created_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    },
    {
      id: 1003,
      author: { handle: 'devkapoor', display_name: 'Dev Kapoor', is_official: false },
      text: "Our neighborhood garden has its first ripe tomatoes 🍅 Free produce table is up on the corner of 8th & Pine. Take what you need, leave what you can! Come by anytime before sunset.",
      like_count: 51, reply_count: 6, created_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    },
    {
      id: 1004,
      author: { handle: 'niacampbell', display_name: 'Nia Campbell', is_official: false },
      text: "Reminder: The Oakland Public Library is hosting a free digital literacy workshop this weekend. All ages welcome! Register at the front desk or online. Let's close the digital divide together 💻 #OaklandReads",
      like_count: 18, reply_count: 3, created_at: new Date(Date.now() - 5 * 60 * 60 * 1000).toISOString(),
    },
    {
      id: 1005,
      author: { handle: 'theokim', display_name: 'Theo Kim', is_official: true },
      text: "BREAKING: City approves $2.4M for new bike lanes along MacArthur Blvd. Construction starts Q1 next year. Full story on Oakland Tribune. #SafeStreets #Cycling",
      like_count: 147, reply_count: 34, created_at: new Date(Date.now() - 8 * 60 * 60 * 1000).toISOString(),
    },
    {
      id: 1006,
      author: { handle: 'oaklandconnect', display_name: 'Oakland Connect', is_official: true },
      text: "Tonight's town hall on the proposed development at 14th & Broadway was well attended. Community voices were heard! Summary thread below 👇 #OaklandCommunity",
      like_count: 89, reply_count: 21, created_at: new Date(Date.now() - 12 * 60 * 60 * 1000).toISOString(),
    },
    {
      id: 1007,
      author: { handle: 'mayashah', display_name: 'Maya Shah', is_official: false },
      text: "Does anyone know a good local plumber? The one I've been using retired. Looking for someone reliable in the Temescal area. Replies appreciated! 🔧",
      like_count: 7, reply_count: 14, created_at: new Date(Date.now() - 18 * 60 * 60 * 1000).toISOString(),
    },
  ];
}

function sampleNotifications() {
  return [
    { id: 1, type: 'like', read: false, actor: { handle: 'jordan_rivera', display_name: 'Jordan Rivera' }, post_id: 1001, created_at: new Date(Date.now() - 5 * 60 * 1000).toISOString() },
    { id: 2, type: 'follow', read: false, actor: { handle: 'theokim', display_name: 'Theo Kim' }, created_at: new Date(Date.now() - 30 * 60 * 1000).toISOString() },
    { id: 3, type: 'reply', read: true, actor: { handle: 'amaralee', display_name: 'Amara Lee' }, post_id: 1003, created_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString() },
    { id: 4, type: 'mention', read: true, actor: { handle: 'niacampbell', display_name: 'Nia Campbell' }, post_id: 1004, created_at: new Date(Date.now() - 4 * 60 * 60 * 1000).toISOString() },
    { id: 5, type: 'like', read: true, actor: { handle: 'devkapoor', display_name: 'Dev Kapoor' }, post_id: 1001, created_at: new Date(Date.now() - 6 * 60 * 60 * 1000).toISOString() },
    { id: 6, type: 'follow', read: true, actor: { handle: 'oaklandconnect', display_name: 'Oakland Connect' }, created_at: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString() },
  ];
}

function sampleConversations() {
  return [
    {
      id: 'c1',
      user: { handle: 'jordan_rivera', display_name: 'Jordan Rivera' },
      lastMsg: "Let's coordinate the next cleanup!",
      time: '2h',
      unread: true,
      messages: [
        { mine: false, text: "Hey! Great work organizing the cleanup last weekend.", time: '2:10 PM' },
        { mine: true, text: "Thank you! The turnout was amazing 🙌", time: '2:15 PM' },
        { mine: false, text: "Let's coordinate the next cleanup! Are you free Saturday morning?", time: '2:17 PM' },
      ]
    },
    {
      id: 'c2',
      user: { handle: 'theokim', display_name: 'Theo Kim' },
      lastMsg: "I'll include your quote in the article.",
      time: '5h',
      unread: false,
      messages: [
        { mine: false, text: "Hi! I'm writing a piece about community organizing. Would you be willing to share your story?", time: '10:30 AM' },
        { mine: true, text: "Sure! Happy to help. What would you like to know?", time: '10:45 AM' },
        { mine: false, text: "I'll include your quote in the article. Thanks so much!", time: '11:02 AM' },
      ]
    },
    {
      id: 'c3',
      user: { handle: 'niacampbell', display_name: 'Nia Campbell' },
      lastMsg: "See you at the library workshop!",
      time: '1d',
      unread: false,
      messages: [
        { mine: true, text: "Are you going to the digital literacy workshop this weekend?", time: 'Sat' },
        { mine: false, text: "Yes! I actually helped organize it. See you at the library workshop!", time: 'Sat' },
      ]
    },
  ];
}

/* ============================================================
   HOME PAGE
   ============================================================ */
function renderHome(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-header-left">
        <h1>Home</h1>
        <div class="subtitle">Your community, in real time</div>
      </div>
    </div>

    <div class="inline-composer">
      ${renderAvatar({ display_name: state.user?.display_name, handle: state.user?.handle })}
      <div class="composer-right">
        <textarea class="composer-input" id="quick-compose" placeholder="What's happening in your community?" rows="2" onclick="openCompose()" readonly></textarea>
        <div class="composer-footer">
          <div class="composer-tools">
            <button class="icon-btn" title="Photo" onclick="openCompose()">
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="2"/><circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/><path d="M21 15l-5-5L5 21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
          <button class="btn-primary" onclick="openCompose()" style="height:36px;font-size:13.5px;">Post</button>
        </div>
      </div>
    </div>

    <div class="feed-tabs">
      <button class="feed-tab active" id="tab-foryou" onclick="switchFeedTab('foryou')">Latest</button>
      <button class="feed-tab" id="tab-media" onclick="switchFeedTab('media')">Media</button>
    </div>

    <div id="home-feed">
      ${renderSkeletonFeed()}
    </div>
  `;

  // Load actual feed
  loadFeed('foryou');
  if (!state.feedPollTimer) {
    state.feedPollTimer = setInterval(() => {
      if (state.currentPage === 'home' && !document.hidden) loadFeed('foryou');
    }, 30000);
  }
}

function renderSkeletonFeed() {
  return [1, 2, 3, 4].map(() => `
    <div class="skeleton-post">
      <div class="skeleton skeleton-avatar"></div>
      <div class="skeleton-body">
        <div class="skeleton skeleton-line-1"></div>
        <div class="skeleton skeleton-line-2"></div>
        <div class="skeleton skeleton-line-3"></div>
      </div>
    </div>`).join('');
}

async function loadFeed(tab) {
  const feedEl = document.getElementById('home-feed');
  if (!feedEl) return;

  try {
    const data = await apiFetch('/posts/feed');
    state.feed = data.posts || [];
    renderRightPanel();
    const posts = tab === 'media' ? state.feed.filter(p => p.image_data || p.video_data) : state.feed;
    feedEl.innerHTML = posts.length
      ? posts.map(p => renderPostCard(p)).join('')
      : `<div class="empty-state"><div class="empty-state-icon">${replySvg()}</div><h3>${tab === 'media' ? 'No media posts yet' : 'The conversation starts here'}</h3><p>${tab === 'media' ? 'Photos and videos shared by the community will appear here.' : 'Be the first to post something for your community.'}</p>${tab === 'foryou' ? '<button class="btn-primary" onclick="openCompose()">Write the first post</button>' : ''}</div>`;
  } catch (error) {
    feedEl.innerHTML = `<div class="empty-state api-error"><h3>Couldn’t load the feed</h3><p>${esc(error.message || 'Check that the CivicSocial API is running on port 8001.')}</p><button class="btn-secondary" onclick="loadFeed('${tab}')">Try again</button></div>`;
  }
}

function switchFeedTab(tab) {
  document.querySelectorAll('.feed-tab').forEach(t => t.classList.remove('active'));
  const activeTab = document.getElementById(`tab-${tab}`);
  if (activeTab) activeTab.classList.add('active');
  const feedEl = document.getElementById('home-feed');
  if (feedEl) feedEl.innerHTML = renderSkeletonFeed();
  loadFeed(tab);
}

/* ============================================================
   Post Card Renderer
   ============================================================ */
function renderPostCard(post, isDetailed = false) {
  const u = post.author || {};
  const liked = Boolean(post._liked || post.liked_by_viewer);
  const bkmk = isBookmarked(post.id);

  return `
    <article class="post-card" onclick="openPostDetail(${post.id})" data-post-id="${post.id}">
      <div onclick="event.stopPropagation(); navigate('user-profile', {handle:'${esc(u.handle)}'})">
        ${renderAvatar(u)}
      </div>
      <div class="post-body">
        <div class="post-head">
          <span class="post-name" onclick="event.stopPropagation(); navigate('user-profile', {handle:'${esc(u.handle)}'})">${esc(u.display_name || u.handle)}</span>
          ${u.is_official ? '<span class="verified-badge" title="Verified">✓</span>' : ''}
          <span class="post-handle">@${esc(u.handle)}</span>
          <span class="post-dot">·</span>
          <span class="post-time">${formatTime(post.created_at)}</span>
          <button class="post-more-btn" onclick="event.stopPropagation(); showPostMenu(this, ${post.id})">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="5" cy="12" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><circle cx="19" cy="12" r="1.5" fill="currentColor"/></svg>
          </button>
        </div>
        <p class="post-text">${linkifyText(post.text)}</p>
        ${post.image_data ? `<div class="post-media"><img src="${esc(post.image_data)}" alt="Post image" loading="lazy" onerror="this.closest('.post-media').classList.add('media-error');this.remove()"></div>` : ''}
        ${post.video_data ? `<div class="post-media"><video controls preload="metadata" playsinline src="${esc(post.video_data)}" onerror="this.closest('.post-media').classList.add('media-error');this.remove()"></video></div>` : ''}
        <div class="post-reactions" onclick="event.stopPropagation()">
          <button class="reaction-btn ${liked ? 'liked' : ''}" onclick="toggleLike(this, ${post.id})" title="Like">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="${liked ? 'currentColor' : 'none'}"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" stroke="currentColor" stroke-width="2"/></svg>
            <span class="like-count">${post.like_count || 0}</span>
          </button>
          <button class="reaction-btn" onclick="openReply(${post.id})" title="Reply">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <span>${post.reply_count || 0}</span>
          </button>
          <button class="reaction-btn" onclick="sharePost(${post.id})" title="Share">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <span>Share</span>
          </button>
          <button class="reaction-btn ${bkmk ? 'liked' : ''}" onclick="bookmarkPost(this, ${post.id})" title="Bookmark">
            <svg width="17" height="17" viewBox="0 0 24 24" fill="${bkmk ? 'currentColor' : 'none'}"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z" stroke="currentColor" stroke-width="2"/></svg>
          </button>
        </div>
      </div>
    </article>
  `;
}

/* ============================================================
   POST INTERACTIONS
   ============================================================ */
async function toggleLike(btn, postId) {
  const countEl = btn.querySelector('.like-count');
  const wasLiked = btn.classList.contains('liked');
  btn.classList.toggle('liked');
  const svg = btn.querySelector('svg path');
  if (svg) svg.setAttribute('fill', btn.classList.contains('liked') ? 'currentColor' : 'none');
  const count = parseInt(countEl.textContent) || 0;
  countEl.textContent = wasLiked ? count - 1 : count + 1;

  const post = state.feed.find(p => p.id === postId);
  if (post) post._liked = !wasLiked;

  try {
    const result = await apiFetch(`/posts/${postId}/like`, { method: 'POST' });
    if (post) post._liked = result.liked;
    if (result.liked) refreshNotifBadge();
  } catch (error) {
    btn.classList.toggle('liked', wasLiked);
    if (svg) svg.setAttribute('fill', wasLiked ? 'currentColor' : 'none');
    countEl.textContent = count;
    if (post) post._liked = wasLiked;
    toast(error.message || 'Sign in to like posts');
  }
}

function openReply(postId) {
  openCompose(postId);
}

function sharePost(postId) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(`${window.location.origin}${window.location.pathname}#post/${postId}`);
  }
  toast('Link copied to clipboard ✓');
}

function bookmarkPost(btn, postId) {
  const post = state.feed.find(p => p.id === postId) || state.profilePosts?.find(p => p.id === postId) || state.bookmarks.find(p => p.id === postId) || { id: postId };
  const added = toggleBookmark(post);
  btn.classList.toggle('liked', added);
  const svg = btn.querySelector('svg path');
  if (svg) svg.setAttribute('fill', added ? 'currentColor' : 'none');
  toast(added ? 'Saved to bookmarks ✓' : 'Removed from bookmarks');
}

function showPostMenu(btn, postId) {
  // Remove existing
  document.querySelectorAll('.post-dropdown').forEach(d => d.remove());

  const dropdown = document.createElement('div');
  dropdown.className = 'post-dropdown';
  dropdown.innerHTML = `
    <button class="post-dropdown-item" onclick="sharePost(${postId}); this.closest('.post-dropdown').remove()">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      Share post
    </button>
    <button class="post-dropdown-item" onclick="bookmarkPostMenu(${postId}); this.closest('.post-dropdown').remove()">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z" stroke="currentColor" stroke-width="2"/></svg>
      Bookmark
    </button>
  `;

  const article = btn.closest('.post-card, .post-detail');
  if (article) {
    article.style.position = 'relative';
    article.appendChild(dropdown);
    setTimeout(() => document.addEventListener('click', () => dropdown.remove(), { once: true }), 50);
  }
}

function bookmarkPostMenu(postId) {
  const post = state.feed.find(p => p.id === postId) || { id: postId };
  const added = toggleBookmark(post);
  toast(added ? 'Saved to bookmarks ✓' : 'Removed from bookmarks');
}

function openPostDetail(postId) {
  navigate('post-detail', { postId });
}

/* ============================================================
   EXPLORE PAGE
   ============================================================ */
function renderExplore(container) {
  container.innerHTML = `
    <div class="page-header">
      <h1>Explore</h1>
    </div>
    <div class="explore-search">
      <div class="explore-search-box">
        <svg class="search-icon" width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/><path d="M21 21l-4.35-4.35" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        <input type="search" id="explore-search-input" placeholder="Search posts and people in the loaded feed…" oninput="filterExplore(this.value)">
      </div>
    </div>
    <div id="explore-feed">
      ${state.feed.length ? state.feed.map(p => renderPostCard(p)).join('') : renderSkeletonFeed()}
    </div>
  `;
  if (!state.feed.length) loadExploreFeed();
}

async function loadExploreFeed() {
  try {
    const data = await apiFetch('/posts/feed');
    state.feed = data.posts || [];
    renderRightPanel();
    filterExplore(document.getElementById('explore-search-input')?.value || '');
  } catch (error) {
    const feedEl = document.getElementById('explore-feed');
    if (feedEl) feedEl.innerHTML = `<div class="empty-state api-error"><h3>Explore is unavailable</h3><p>${esc(error.message)}</p><button class="btn-secondary" onclick="loadExploreFeed()">Try again</button></div>`;
  }
}

function filterExplore(query) {
  const needle = query.trim().toLocaleLowerCase();
  const posts = state.feed.filter(p =>
    !needle || p.text?.toLocaleLowerCase().includes(needle) ||
    p.author?.display_name?.toLocaleLowerCase().includes(needle) ||
    p.author?.handle?.toLocaleLowerCase().includes(needle)
  );
  const feedEl = document.getElementById('explore-feed');
  if (feedEl) feedEl.innerHTML = posts.length ? posts.map(p => renderPostCard(p)).join('') : `
    <div class="empty-state">
      <div class="empty-state-icon"><svg width="32" height="32" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/><path d="M21 21l-4.35-4.35" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></div>
      <h3>${needle ? `No results for “${esc(query)}”` : 'No posts to explore yet'}</h3>
      <p>${needle ? 'Try a different name, handle, hashtag, or phrase.' : 'New conversations from the community will show up here.'}</p>
    </div>`;
}

function exploreTag(tag) {
  navigate('explore');
  setTimeout(() => {
    const input = document.getElementById('explore-search-input');
    if (input) { input.value = tag; filterExplore(tag); }
  }, 100);
}

/* ============================================================
   NOTIFICATIONS PAGE
   ============================================================ */
async function renderNotifications(container) {
  container.innerHTML = `
    <div class="page-header">
      <h1>Notifications</h1>
      <button class="btn-secondary" style="font-size:12.5px;height:34px;" onclick="markAllRead()">Mark all read</button>
    </div>
    <div id="notif-list">${renderSkeletonFeed()}</div>
  `;
  try {
    const data = await apiFetch('/notifications');
    const notifs = Array.isArray(data.notifications) ? data.notifications : [];
    const list = document.getElementById('notif-list');
    if (!list) return;
    renderNotificationList(notifs);
    updateNotifBadge(notifs.filter(n => !n.read).length);
  } catch (error) {
    const list = document.getElementById('notif-list');
    if (list) list.innerHTML = `<div class="empty-state api-error"><h3>Couldn’t load notifications</h3><p>${esc(error.message)}</p><button class="btn-secondary" onclick="navigate('notifications')">Try again</button></div>`;
  }
}

function renderNotifItem(n) {
  const iconMap = {
    like: ['notif-like', likeSvg()],
    follow: ['notif-follow', followSvg()],
    reply: ['notif-reply', replySvg()],
    mention: ['notif-mention', mentionSvg()],
  };
  const [cls, icon] = iconMap[n.type] || ['notif-follow', followSvg()];

  const texts = {
    like: `<b>${esc(n.actor.display_name)} ${n.actor.is_official ? '<span class="verified-badge">✓</span>' : ''}</b> liked your post`,
    follow: `<b>${esc(n.actor.display_name)} ${n.actor.is_official ? '<span class="verified-badge">✓</span>' : ''}</b> started following you`,
    reply: `<b>${esc(n.actor.display_name)} ${n.actor.is_official ? '<span class="verified-badge">✓</span>' : ''}</b> replied to your post`,
    mention: `<b>${esc(n.actor.display_name)} ${n.actor.is_official ? '<span class="verified-badge">✓</span>' : ''}</b> mentioned you in a post`,
  };

  return `
    <div class="notif-item ${n.read ? '' : 'unread'}" data-id="${n.id}" onclick="handleNotification(${n.id}, ${n.post_id || 'null'}, '${esc(n.actor.handle)}', this)">
      <div class="notif-icon-wrap ${cls}">${icon}</div>
      ${renderAvatar(n.actor)}
      <div class="notif-text">
        ${texts[n.type] || `<b>${esc(n.actor.display_name)}</b> interacted with you`}
        <div class="notif-time">${formatTime(n.created_at)}</div>
      </div>
    </div>`;
}

function likeSvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z"/></svg>'; }
function followSvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M16 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" stroke="currentColor" stroke-width="2"/><circle cx="8.5" cy="7" r="4" stroke="currentColor" stroke-width="2"/><path d="M20 8v6M23 11h-6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'; }
function replySvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'; }
function mentionSvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="2"/><path d="M16 8v5a3 3 0 006 0v-1a10 10 0 10-3.92 7.94" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'; }

async function handleNotification(id, postId, actorHandle, el) {
  if (el.classList.contains('unread')) {
    try {
      await apiFetch(`/notifications/${id}/read`, { method: 'POST' });
      el.classList.remove('unread');
      updateNotifBadge(Math.max(0, document.querySelectorAll('.notif-item.unread').length));
      await refreshNotifBadge();
    } catch (error) { toast(error.message); }
  }
  if (postId) navigate('post-detail', { postId });
  else if (actorHandle) navigate('user-profile', { handle: actorHandle });
}

async function markAllRead() {
  const items = [...document.querySelectorAll('.notif-item.unread')];
  try {
    await Promise.all(items.map(item => apiFetch(`/notifications/${item.dataset.id}/read`, { method: 'POST' })));
    items.forEach(el => el.classList.remove('unread'));
    updateNotifBadge(0);
    await refreshNotifBadge();
    toast('All notifications marked as read');
  } catch (error) { toast(error.message || 'Could not update notifications'); }
}

function updateNotifBadge(count) {
  const badge = document.getElementById('notif-badge');
  if (!badge) return;
  if (count > 0) {
    badge.textContent = count > 9 ? '9+' : count;
    badge.style.display = 'flex';
  } else {
    badge.style.display = 'none';
  }
}

/* ============================================================
   MESSAGES PAGE
   ============================================================ */
function renderMessages(container) {
  const convos = sampleConversations();
  state.chatTarget = convos[0];

  container.innerHTML = `
    <div class="page-header" style="padding:0;">
      <div style="padding: 0 20px; height:60px; display:flex; align-items:center; justify-content:space-between; width:100%;">
        <h1>Messages</h1>
        <button class="icon-btn" onclick="toast('New message')">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none"><path d="M12 20h9M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>
    </div>
    <div class="messages-layout" id="messages-layout">
      <div class="conversations-list" id="convo-list">
        ${convos.map(c => renderConvoItem(c, c.id === convos[0].id)).join('')}
      </div>
      <div id="chat-pane">
        ${renderChatPane(convos[0])}
      </div>
    </div>
  `;
}

function renderConvoItem(convo, active = false) {
  return `
    <div class="convo-item ${active ? 'active' : ''}" onclick="openConvo('${convo.id}')" id="convo-${convo.id}">
      ${renderAvatar(convo.user)}
      <div class="convo-info">
        <div class="convo-name">${esc(convo.user.display_name)}</div>
        <div class="convo-preview">${esc(convo.lastMsg)}</div>
      </div>
      <div class="convo-meta">
        <span class="convo-time">${convo.time}</span>
        ${convo.unread ? '<div class="convo-unread"></div>' : ''}
      </div>
    </div>`;
}

function renderChatPane(convo) {
  return `
    <div class="chat-area">
      <div class="chat-header">
        ${renderAvatar(convo.user)}
        <div>
          <div style="font-weight:700;font-size:15px;">${esc(convo.user.display_name)}</div>
          <div style="font-size:12.5px;color:var(--muted);">@${esc(convo.user.handle)}</div>
        </div>
        <button class="icon-btn" style="margin-left:auto;" onclick="toast('Profile info')">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/><path d="M12 16v-4M12 8h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </button>
      </div>
      <div class="chat-messages" id="chat-messages">
        ${convo.messages.map(m => renderMessage(m)).join('')}
      </div>
      <div class="chat-input-row">
        <button class="icon-btn" onclick="toast('Add image')">
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" stroke-width="2"/><circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/><path d="M21 15l-5-5L5 21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <textarea class="chat-input" id="chat-input" placeholder="Start a message…" rows="1" onkeydown="sendMessageKey(event, '${convo.id}')"></textarea>
        <button class="btn-primary" style="height:42px;padding:0 16px;" onclick="sendMessage('${convo.id}')">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
      </div>
    </div>`;
}

function renderMessage(msg) {
  return `
    <div class="chat-msg ${msg.mine ? 'mine' : ''}">
      ${!msg.mine ? renderAvatar({ handle: 'other', display_name: 'O' }) : ''}
      <div>
        <div class="msg-bubble">${esc(msg.text)}</div>
        <div class="msg-time">${msg.time}</div>
      </div>
    </div>`;
}

function openConvo(id) {
  const convos = sampleConversations();
  const convo = convos.find(c => c.id === id);
  if (!convo) return;
  state.chatTarget = convo;
  document.querySelectorAll('.convo-item').forEach(el => el.classList.remove('active'));
  const el = document.getElementById(`convo-${id}`);
  if (el) el.classList.add('active');
  const pane = document.getElementById('chat-pane');
  if (pane) pane.innerHTML = renderChatPane(convo);
}

function sendMessageKey(e, convoId) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage(convoId);
  }
}

function sendMessage(convoId) {
  const input = document.getElementById('chat-input');
  const text = input?.value.trim();
  if (!text) return;
  input.value = '';

  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata' });
  const msgEl = document.createElement('div');
  msgEl.innerHTML = renderMessage({ mine: true, text, time: timeStr });
  const messages = document.getElementById('chat-messages');
  if (messages) {
    messages.appendChild(msgEl.firstElementChild);
    messages.scrollTop = messages.scrollHeight;
  }
}

/* ============================================================
   BOOKMARKS PAGE
   ============================================================ */
function renderBookmarks(container) {
  container.innerHTML = `
    <div class="page-header">
      <div class="page-header-left">
        <h1>Bookmarks</h1>
        ${state.user ? `<div class="subtitle">@${esc(state.user.handle)}</div>` : ''}
      </div>
      ${state.bookmarks.length > 0 ? `<button class="btn-secondary" style="font-size:12.5px;height:34px;" onclick="clearBookmarks()">Clear all</button>` : ''}
    </div>
    <div id="bookmarks-feed">
      ${state.bookmarks.length === 0 ? `
        <div class="empty-state">
          <div class="empty-state-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z" stroke="currentColor" stroke-width="2"/></svg>
          </div>
          <h3>No bookmarks yet</h3>
          <p>Save posts to read later. They'll appear here.</p>
        </div>` :
      state.bookmarks.map(p => renderPostCard(p)).join('')}
    </div>
  `;
}

function clearBookmarks() {
  state.bookmarks = [];
  saveBookmarks();
  navigate('bookmarks');
  toast('Bookmarks cleared');
}

/* ============================================================
   PROFILE PAGE
   ============================================================ */
async function renderProfile(container, handle) {
  const isOwnProfile = handle === state.user?.handle;

  container.innerHTML = `
    <div class="page-header">
      <button class="back-btn" onclick="navigate('home')">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M19 12H5M12 19l-7-7 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Back
      </button>
    </div>
    <div id="profile-body">${renderSkeletonFeed()}</div>
  `;

  // Load profile data
  let profileUser, profilePosts;
  try {
    const data = await apiFetch(`/users/${handle}`);
    profileUser = data.user;
    profilePosts = data.posts;
  } catch (error) {
    const body = document.getElementById('profile-body');
    if (body) body.innerHTML = `<div class="empty-state api-error"><h3>Profile unavailable</h3><p>${esc(error.message)}</p><button class="btn-secondary" onclick="navigate('home')">Back to Home</button></div>`;
    return;
  }

  const body = document.getElementById('profile-body');
  if (!body) return;

  const u = profileUser;
  state.profile = u;
  state.profilePosts = profilePosts || [];
  const followerCount = u.follower_count || 0;
  const followingCount = u.following_count || 0;

  body.innerHTML = `
    <div class="profile-banner"></div>
    <div class="profile-header">
      <div class="profile-avatar-wrap">
        ${renderAvatar(u, 'avatar-lg')}
      </div>
      <div class="profile-actions-row">
        ${isOwnProfile
      ? `<button class="btn-secondary" onclick="openEditProfile()">Edit profile</button>`
      : `<button class="btn-follow ${u.is_followed_by_viewer ? 'following' : ''}" id="follow-profile-btn" onclick="followProfileUser(this, '${esc(u.handle)}')">${u.is_followed_by_viewer ? 'Following' : 'Follow'}</button>`}
      </div>
      <div class="profile-name">
        ${esc(u.display_name || u.handle)}
        ${u.is_official ? '<span class="verified-pill">✓ Official</span>' : ''}
      </div>
      <div class="profile-handle-line">@${esc(u.handle)}</div>
      ${u.bio ? `<p class="profile-bio">${esc(u.bio)}</p>` : ''}
      <div class="profile-meta-row">
        <div class="profile-meta-item">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><rect x="3" y="4" width="18" height="18" rx="2" stroke="currentColor" stroke-width="2"/><path d="M16 2v4M8 2v4M3 10h18" stroke="currentColor" stroke-width="2"/></svg>
          Joined ${u.created_at ? new Date(u.created_at).toLocaleDateString('en-IN', { month: 'long', year: 'numeric', timeZone: 'Asia/Kolkata' }) : 'recently'}
        </div>
      </div>
      <div class="profile-stats">
        <div class="profile-stat">
          <span class="stat-num">${followingCount}</span>
          <span class="stat-label">Following</span>
        </div>
        <div class="profile-stat">
          <span class="stat-num">${followerCount}</span>
          <span class="stat-label">Followers</span>
        </div>
      </div>
    </div>

    <div class="feed-tabs">
      <button class="feed-tab active" onclick="switchProfileTab(this,'posts', '${esc(u.handle)}')">Posts</button>
      <button class="feed-tab" onclick="switchProfileTab(this,'media', '${esc(u.handle)}')">Media</button>
    </div>

    <div id="profile-feed">
      ${profilePosts.length > 0
      ? profilePosts.map(p => renderPostCard(p)).join('')
      : `<div class="empty-state"><div class="empty-state-icon">${replySvg()}</div><h3>No posts yet</h3><p>When ${esc(u.display_name)} posts, it’ll show up here.</p></div>`}
    </div>
  `;
}

function switchProfileTab(btn, tab, handle) {
  document.querySelectorAll('.feed-tabs .feed-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const feed = document.getElementById('profile-feed');
  if (!feed) return;

  const posts = (state.profilePosts || []).filter(p => p.author?.handle === handle && (tab === 'posts' || p.image_data || p.video_data));
  feed.innerHTML = posts.length ? posts.map(p => renderPostCard(p)).join('') : `<div class="empty-state"><div class="empty-state-icon">${replySvg()}</div><h3>${tab === 'media' ? 'No media posts yet' : 'No posts yet'}</h3></div>`;
}

async function followProfileUser(btn, handle) {
  const following = btn.classList.contains('following');
  try {
    const data = await apiFetch(`/users/${handle}/follow`, { method: 'POST' });
    btn.classList.toggle('following', data.following);
    btn.textContent = data.following ? 'Following' : 'Follow';
    const followerCount = document.querySelectorAll('.profile-stat .stat-num')[1];
    if (followerCount) followerCount.textContent = String(Math.max(0, Number(followerCount.textContent || 0) + (data.following ? 1 : -1)));
    toast(data.following ? `Following @${handle}` : `Unfollowed @${handle}`);
    refreshNotifBadge();
  } catch (_) {
    toast('Could not update follow status. Sign in and try again.');
  }
}

function openEditProfile() {
  editAvatarData = null;
  // Show a simple edit modal
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.alignItems = 'center';
  overlay.innerHTML = `
    <div class="compose-modal-inner" style="max-width:480px;">
      <div class="compose-modal-header">
        <button class="icon-btn close-btn" onclick="this.closest('.modal-overlay').remove()">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>
        </button>
        <span class="compose-title">Edit profile</span>
        <button class="btn-primary" style="height:34px;font-size:13px;margin-left:auto;" onclick="saveProfile(this)">Save</button>
      </div>
      <div class="compose-body">
        <div class="form-group">
          <label class="form-label">Display name</label>
          <input class="form-input" id="edit-name" value="${esc(state.user?.display_name || '')}" maxlength="80">
        </div>
        <div class="form-group">
          <label class="form-label">Bio</label>
          <textarea class="form-input" id="edit-bio" rows="3" maxlength="300" style="height:auto;padding:12px 15px;resize:vertical;">${esc(state.profile?.bio || '')}</textarea>
        </div>
        <div class="form-group">
          <label class="form-label">Profile photo</label>
          <input class="form-input" id="edit-avatar" type="file" accept="image/*" onchange="previewProfileAvatar(event)">
          <div class="profile-edit-avatar" id="edit-avatar-preview">${state.profile?.avatar ? `<img src="${esc(state.profile.avatar)}" alt="Profile photo preview">` : ''}</div>
        </div>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}

let editAvatarData = null;
function previewProfileAvatar(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 4 * 1024 * 1024) { toast('Choose an image smaller than 4 MB'); event.target.value = ''; return; }
  const reader = new FileReader();
  reader.onload = () => {
    editAvatarData = reader.result;
    document.getElementById('edit-avatar-preview').innerHTML = `<img src="${esc(editAvatarData)}" alt="Profile photo preview">`;
  };
  reader.readAsDataURL(file);
}

async function saveProfile(btn) {
  const name = document.getElementById('edit-name')?.value.trim();
  if (!name) return toast('Display name is required');
  btn.disabled = true;
  btn.textContent = 'Saving…';
  try {
    const updates = { display_name: name, bio: document.getElementById('edit-bio')?.value || '' };
    if (editAvatarData) updates.avatar_data = editAvatarData;
    const data = await apiFetch('/users/me/profile', {
      method: 'PATCH',
      body: JSON.stringify(updates)
    });
    if (state.user) {
      state.user.display_name = data.display_name || name;
      state.user.avatar = data.avatar || state.user.avatar;
      saveAuth(state.token, state.user);
      setAvatarEl(document.getElementById('sidebar-avatar'), state.user);
      setAvatarEl(document.getElementById('mobile-avatar'), state.user);
      setAvatarEl(document.getElementById('compose-avatar'), state.user);
    }
    state.profile = data;
    document.getElementById('sidebar-name').innerHTML = `${esc(name)} ${state.user?.is_official ? '<span class="verified-badge" title="Official account">✓</span>' : ''}`;
    btn.closest('.modal-overlay')?.remove();
    editAvatarData = null;
    if (state.currentPage === 'profile') renderProfile(document.getElementById('main-content'), state.user.handle);
    toast('Profile updated ✓');
  } catch (error) {
    btn.disabled = false;
    btn.textContent = 'Save';
    toast(error.message || 'Could not save profile changes');
  }
}

/* ============================================================
   POST DETAIL PAGE
   ============================================================ */
async function renderPostDetail(container, postId) {
  container.innerHTML = `
    <div class="page-header">
      <button class="back-btn" onclick="navigate('home')">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M19 12H5M12 19l-7-7 7-7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Post
      </button>
    </div>
    <div id="post-detail-body">${renderSkeletonFeed()}</div>
  `;

  let post, replies;
  try {
    const data = await apiFetch(`/posts/${postId}`);
    post = data.post;
    // The server returns direct children only; enforce the selected thread boundary
    // in the view as well, so stale/cached API payloads cannot mix conversations.
    replies = (data.replies || []).filter(reply => Number(reply.parent_post_id) === Number(post.id));
  } catch (error) {
    const body = document.getElementById('post-detail-body');
    if (body) body.innerHTML = `<div class="empty-state api-error"><h3>Post unavailable</h3><p>${esc(error.message)}</p><button class="btn-secondary" onclick="navigate('home')">Back to Home</button></div>`;
    return;
  }

  const body = document.getElementById('post-detail-body');
  if (!body) return;

  const u = post.author || {};
  const bkmk = isBookmarked(post.id);

  body.innerHTML = `
    <div class="post-detail">
      <div class="post-head" style="margin-bottom:14px;">
        ${renderAvatar(u)}
        <div style="margin-left:10px;">
          <div class="post-name">${esc(u.display_name || u.handle)} ${u.is_official ? '<span class="verified-badge" title="Official account">✓</span>' : ''}</div>
          <div class="post-handle">@${esc(u.handle)}</div>
        </div>
        <button class="post-more-btn" style="margin-left:auto;" onclick="showPostMenu(this, ${post.id})">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="5" cy="12" r="1.5" fill="currentColor"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/><circle cx="19" cy="12" r="1.5" fill="currentColor"/></svg>
        </button>
      </div>
      <p class="post-text" style="font-size:22px;line-height:1.5;">${linkifyText(post.text)}</p>
      ${post.image_data ? `<div class="post-media"><img src="${esc(post.image_data)}" alt="Post image" loading="lazy" onerror="this.closest('.post-media').classList.add('media-error');this.remove()"></div>` : ''}
      ${post.video_data ? `<div class="post-media"><video controls preload="metadata" playsinline src="${esc(post.video_data)}"></video></div>` : ''}
      <div class="post-detail-meta">
        <span>${formatFullTime(post.created_at)}</span>
      </div>
      <div class="post-detail-stats">
        <div class="detail-stat"><b>${post.reply_count || 0}</b> <span class="stat-label">Replies</span></div>
        <div class="detail-stat"><b id="detail-like-count">${post.like_count || 0}</b> <span class="stat-label">Likes</span></div>
      </div>
      <div class="detail-reactions">
        <button class="reaction-btn" onclick="openReply(${post.id})" title="Reply">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="reaction-btn ${post.liked_by_viewer ? 'liked' : ''}" onclick="toggleLikeDetail(this, ${post.id})" title="Like">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" stroke="currentColor" stroke-width="2"/></svg>
        </button>
        <button class="reaction-btn" onclick="sharePost(${post.id})" title="Share">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8M16 6l-4-4-4 4M12 2v13" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="reaction-btn ${bkmk ? 'liked' : ''}" onclick="bookmarkPost(this, ${post.id})" title="Bookmark">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="${bkmk ? 'currentColor' : 'none'}"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z" stroke="currentColor" stroke-width="2"/></svg>
        </button>
      </div>
    </div>

    <!-- Reply composer -->
    <div class="inline-composer thread-composer">
      ${renderAvatar({ display_name: state.user?.display_name, handle: state.user?.handle })}
      <div class="composer-right">
        <div class="mention-composer">
          <textarea class="composer-input" id="reply-input" placeholder="Post your reply…" rows="2" oninput="handleMentionInput(this)" onkeydown="handleMentionKeydown(event, this)" autocomplete="off"></textarea>
          <div class="mention-suggestions hidden" role="listbox" aria-label="Account suggestions"></div>
        </div>
        <div class="composer-footer">
          <button class="thread-media-action" onclick="openCompose(${post.id})" type="button">Add media</button>
          <button class="btn-primary" style="height:36px;font-size:13.5px;" onclick="submitReply(${post.id})">Reply</button>
        </div>
      </div>
    </div>

    <!-- Replies -->
    <div id="replies-section" class="reply-thread">
      ${replies.length ? replies.map(r => renderPostCard(r)).join('') : '<div class="thread-empty">No replies yet. Start the conversation.</div>'}
    </div>
  `;
}

async function toggleLikeDetail(btn, postId) {
  const wasLiked = btn.classList.contains('liked');
  btn.classList.toggle('liked', !wasLiked);
  const svg = btn.querySelector('svg path');
  if (svg) svg.setAttribute('fill', wasLiked ? 'none' : 'currentColor');
  const count = document.getElementById('detail-like-count');
  const previousCount = Number(count?.textContent || 0);
  if (count) count.textContent = String(Math.max(0, previousCount + (wasLiked ? -1 : 1)));
  try { await apiFetch(`/posts/${postId}/like`, { method: 'POST' }); }
  catch (error) { btn.classList.toggle('liked', wasLiked); if (svg) svg.setAttribute('fill', wasLiked ? 'currentColor' : 'none'); if (count) count.textContent = String(previousCount); toast(error.message); }
}

async function submitReply(postId) {
  const input = document.getElementById('reply-input');
  const text = input?.value.trim();
  if (!text) { toast('Write something first'); return; }

  const button = input?.closest('.composer-right')?.querySelector('.composer-footer .btn-primary');
  if (button) { button.disabled = true; button.textContent = 'Replying…'; }
  try {
    const reply = await apiFetch('/posts', { method: 'POST', body: JSON.stringify({ text, parent_post_id: postId }) });
    const repliesSection = document.getElementById('replies-section');
    if (repliesSection) {
      repliesSection.querySelector('.thread-empty')?.remove();
      const tmp = document.createElement('div');
      tmp.innerHTML = renderPostCard(reply);
      repliesSection.append(tmp.firstElementChild);
    }
    const replyStat = [...document.querySelectorAll('.detail-stat')].find(el => el.querySelector('.stat-label')?.textContent === 'Replies')?.querySelector('b');
    if (replyStat) replyStat.textContent = String(Number(replyStat.textContent || 0) + 1);
    const parent = state.feed.find(item => Number(item.id) === Number(postId));
    if (parent) parent.reply_count = Number(parent.reply_count || 0) + 1;
    input.value = '';
    toast('Reply posted');
    refreshNotifBadge();
  } catch (error) { toast(error.message || 'Could not post reply'); }
  finally { if (button) { button.disabled = false; button.textContent = 'Reply'; } }
}

/* ============================================================
   SETTINGS PAGE
   ============================================================ */
function renderSettings(container) {
  const tabs = [
    { id: 'account', label: 'Account', icon: accountSvg() },
    { id: 'appearance', label: 'Appearance', icon: appearanceSvg() },
  ];

  container.innerHTML = `
    <div class="page-header">
      <h1>Settings</h1>
    </div>
    <div class="settings-layout">
      <div class="settings-nav" id="settings-nav">
        ${tabs.map(t => `
          <button class="settings-nav-item ${t.id === state.settingsTab ? 'active' : ''}"
            onclick="selectSettingsTab('${t.id}')">
            ${t.icon}
            ${t.label}
          </button>`).join('')}
        <div style="margin-top:auto;padding:12px 20px;border-top:1px solid var(--border);margin-top:32px;">
          <button class="settings-nav-item danger" style="color:var(--danger);" onclick="logout()">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            Log out
          </button>
        </div>
      </div>
      <div class="settings-content" id="settings-content">
        ${renderSettingsContent(state.settingsTab)}
      </div>
    </div>
  `;
}

function accountSvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" stroke="currentColor" stroke-width="2"/><circle cx="12" cy="7" r="4" stroke="currentColor" stroke-width="2"/></svg>'; }
function privacySvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" stroke="currentColor" stroke-width="2"/></svg>'; }
function notifSvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9" stroke="currentColor" stroke-width="2"/><path d="M13.73 21a2 2 0 01-3.46 0" stroke="currentColor" stroke-width="2"/></svg>'; }
function appearanceSvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="5" stroke="currentColor" stroke-width="2"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'; }
function accessSvg() { return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/><path d="M12 8v4M12 16h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'; }

function selectSettingsTab(tabId) {
  state.settingsTab = tabId;
  document.querySelectorAll('.settings-nav-item').forEach(b => b.classList.remove('active'));
  const btns = document.querySelectorAll('.settings-nav-item');
  const tabs = ['account', 'appearance'];
  const idx = tabs.indexOf(tabId);
  if (btns[idx]) btns[idx].classList.add('active');
  const content = document.getElementById('settings-content');
  if (content) content.innerHTML = renderSettingsContent(tabId);
}

function renderSettingsContent(tab) {
  switch (tab) {
    case 'account': return renderAccountSettings();
    case 'privacy': return renderPrivacySettings();
    case 'notifs': return renderNotifSettings();
    case 'appearance': return renderAppearanceSettings();
    case 'accessibility': return renderAccessibilitySettings();
    default: return renderAccountSettings();
  }
}

function renderAccountSettings() {
  const u = state.user || {};
  return `
    <div class="settings-section-title">Account information</div>
    <div class="settings-row">
      <div class="settings-row-info">
        <div class="settings-row-label">Display name</div>
        <div class="settings-row-desc">${esc(u.display_name || '—')}</div>
      </div>
      <button class="btn-secondary" onclick="openEditProfile()">Edit</button>
    </div>
    <div class="settings-row">
      <div class="settings-row-info">
        <div class="settings-row-label">Handle</div>
        <div class="settings-row-desc">@${esc(u.handle || '—')}</div>
      </div>
    </div>
    <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Public profile</div><div class="settings-row-desc">Update your name, bio, or photo.</div></div><button class="btn-secondary" onclick="openEditProfile()">Edit profile</button></div>
    <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Sign-in email</div><div class="settings-row-desc">Email changes are not available from this screen.</div></div></div>
    <div class="settings-row"><div class="settings-row-info"><div class="settings-row-label">Password</div><div class="settings-row-desc">Use “Forgot password” on the sign-in screen to reset it.</div></div><button class="btn-secondary" onclick="logout()">Sign out</button></div>`;
}

function renderPrivacySettings() {
  const rows = [
    ['Private account', 'Only approved followers can see your posts', false],
    ['Protect your posts', 'Hide your posts from the Explore tab', false],
    ['Allow mentions from', 'Everyone', true],
    ['Direct messages', 'Allow messages from everyone', true],
    ['Discoverability', 'Let others find you by email', true],
  ];
  return `
    <div class="settings-section-title">Privacy & Safety</div>
    ${rows.map(([label, desc, on]) => `
      <div class="settings-row">
        <div class="settings-row-info">
          <div class="settings-row-label">${esc(label)}</div>
          <div class="settings-row-desc">${esc(desc)}</div>
        </div>
        <label class="toggle">
          <input type="checkbox" ${on ? 'checked' : ''} onchange="toast('Setting updated')">
          <span class="toggle-slider"></span>
        </label>
      </div>`).join('')}`;
}

function renderNotifSettings() {
  const rows = [
    ['Push notifications', 'Receive push notifications', true],
    ['Email notifications', 'Get email updates', false],
    ['Likes', 'When someone likes your post', true],
    ['Replies', 'When someone replies to you', true],
    ['New followers', 'When someone follows you', true],
    ['Mentions', 'When someone mentions you', true],
    ['Community updates', 'Local civic news and events', true],
  ];
  return `
    <div class="settings-section-title">Notifications</div>
    ${rows.map(([label, desc, on]) => `
      <div class="settings-row">
        <div class="settings-row-info">
          <div class="settings-row-label">${esc(label)}</div>
          <div class="settings-row-desc">${esc(desc)}</div>
        </div>
        <label class="toggle">
          <input type="checkbox" ${on ? 'checked' : ''} onchange="toast('Setting updated')">
          <span class="toggle-slider"></span>
        </label>
      </div>`).join('')}`;
}

function renderAppearanceSettings() {
  return `
    <div class="settings-section-title">Appearance</div>
    <div class="settings-row">
      <div class="settings-row-info">
        <div class="settings-row-label">Theme</div>
        <div class="settings-row-desc">Choose your preferred color theme</div>
      </div>
    </div>
    <div style="display:flex;gap:12px;margin-bottom:24px;">
      <button onclick="setTheme('light')" style="flex:1;height:80px;border:1.5px solid var(--border);border-radius:var(--radius-md);background:#f8f9fa;cursor:pointer;font-weight:700;color:#0f1923;">☀️ Light</button>
      <button onclick="setTheme('dark')" style="flex:1;height:80px;border:1.5px solid var(--primary);border-radius:var(--radius-md);background:#000;cursor:pointer;font-weight:700;color:white;">🌙 Dark</button>
    </div>
    <p class="settings-row-desc">Dark mode is the default. Your selection is saved in this browser.</p>`;
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark';
  try { localStorage.setItem('cs_theme', document.documentElement.dataset.theme); } catch (_) { }
  toast(`${theme === 'light' ? 'Light' : 'Dark'} theme applied`);
}

function renderAccessibilitySettings() {
  const rows = [
    ['Keyboard navigation', 'Use Tab and arrow keys to navigate', true],
    ['Screen reader support', 'Optimize for screen readers', false],
    ['High contrast', 'Increase color contrast', false],
    ['Autoplay media', 'Automatically play videos', false],
    ['Alt text reminders', 'Remind me to add alt text to images', true],
  ];
  return `
    <div class="settings-section-title">Accessibility</div>
    ${rows.map(([label, desc, on]) => `
      <div class="settings-row">
        <div class="settings-row-info">
          <div class="settings-row-label">${esc(label)}</div>
          <div class="settings-row-desc">${esc(desc)}</div>
        </div>
        <label class="toggle">
          <input type="checkbox" ${on ? 'checked' : ''} onchange="toast('Accessibility setting updated')">
          <span class="toggle-slider"></span>
        </label>
      </div>`).join('')}`;
}

/* ============================================================
   COMPOSE MODAL
   ============================================================ */
let composeImageBase64 = null;
let composeVideoBase64 = null;
let composeReplyTo = null;
let postSubmissionController = null;
let postSubmissionLoading = false;
const mentionState = new WeakMap();

function mentionTokenAtCursor(input) {
  const before = input.value.slice(0, input.selectionStart ?? input.value.length);
  const match = /(^|[\s([{])@([a-zA-Z0-9_]{0,30})$/.exec(before);
  return match ? { start: before.length - match[0].length + match[1].length, query: match[2] } : null;
}

function hideMentionSuggestions(input) {
  const stateForInput = mentionState.get(input);
  if (stateForInput) { clearTimeout(stateForInput.timer); stateForInput.sequence++; }
  input.closest('.mention-composer')?.querySelector('.mention-suggestions')?.classList.add('hidden');
}

function handleMentionInput(input) {
  const widget = input.closest('.mention-composer')?.querySelector('.mention-suggestions');
  const token = mentionTokenAtCursor(input);
  if (!widget || !token || !state.token) { hideMentionSuggestions(input); return; }
  const current = mentionState.get(input) || { timer: null, sequence: 0, users: [], activeIndex: -1, token: null };
  mentionState.set(input, current);
  current.token = token;
  current.activeIndex = -1;
  current.sequence++;
  const sequence = current.sequence;
  clearTimeout(current.timer);
  widget.classList.remove('hidden');
  widget.innerHTML = '<div class="mention-suggestion-status" role="status">Searching accounts…</div>';
  current.timer = setTimeout(async () => {
    try {
      const result = await apiFetch(`/users/search?q=${encodeURIComponent(token.query)}`);
      if (current.sequence !== sequence || mentionTokenAtCursor(input)?.query !== token.query) return;
      current.users = result.users || [];
      widget.innerHTML = current.users.length ? current.users.map((user, index) => `
        <button type="button" class="mention-suggestion" role="option" aria-selected="false" onclick="selectMention(this, ${index})" onmousedown="event.preventDefault()">
          ${renderAvatar(user, 'avatar-sm')}<span class="mention-suggestion-copy"><b>${esc(user.display_name)}</b><small>@${esc(user.handle)}</small></span>
          ${user.is_official ? '<span class="verified-badge" aria-label="Official account">✓</span>' : ''}
        </button>`).join('') : '<div class="mention-suggestion-status">No matching accounts</div>';
    } catch (error) {
      if (current.sequence !== sequence) return;
      current.users = [];
      widget.innerHTML = `<div class="mention-suggestion-status mention-suggestion-error">${esc(error.message || 'Could not load account suggestions')}</div>`;
    }
  }, 180);
}

function handleMentionKeydown(event, input) {
  const current = mentionState.get(input);
  const widget = input.closest('.mention-composer')?.querySelector('.mention-suggestions');
  if (!current || !widget || widget.classList.contains('hidden')) return;
  if (event.key === 'Escape') { hideMentionSuggestions(input); return; }
  if (!current.users.length) return;
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const direction = event.key === 'ArrowDown' ? 1 : -1;
    current.activeIndex = (current.activeIndex + direction + current.users.length) % current.users.length;
    widget.querySelectorAll('.mention-suggestion').forEach((row, index) => {
      row.classList.toggle('active', index === current.activeIndex);
      row.setAttribute('aria-selected', String(index === current.activeIndex));
    });
  } else if (event.key === 'Enter' && current.activeIndex >= 0) {
    event.preventDefault();
    chooseMention(input, current.activeIndex);
  }
}

function selectMention(button, index) {
  const input = button.closest('.mention-composer')?.querySelector('textarea');
  if (input) chooseMention(input, index);
}

function chooseMention(input, index) {
  const current = mentionState.get(input);
  const user = current?.users[index];
  const token = current?.token;
  if (!user || !token) return;
  const end = input.selectionStart ?? input.value.length;
  const suffix = input.value.slice(end);
  const replacement = `@${user.handle} `;
  input.value = input.value.slice(0, token.start) + replacement + suffix;
  const cursor = token.start + replacement.length;
  input.setSelectionRange(cursor, cursor);
  hideMentionSuggestions(input);
  input.focus();
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function openCompose(replyToPostId = null) {
  composeReplyTo = replyToPostId;
  const modal = document.getElementById('compose-modal');
  const postButton = modal.querySelector('.btn-primary');
  if (postButton && !postSubmissionController?.busy) {
    postButton.disabled = false;
    postButton.textContent = 'Post';
  }
  modal.classList.remove('hidden');
  const titleEl = modal.querySelector('.compose-title');
  if (titleEl) titleEl.textContent = replyToPostId ? 'Reply' : 'New post';
  setTimeout(() => {
    const ta = document.getElementById('compose-text');
    if (ta) ta.focus();
  }, 80);
}

function closeCompose() {
  const modal = document.getElementById('compose-modal');
  modal.classList.add('hidden');
  const postButton = modal.querySelector('.btn-primary');
  if (postButton && !postSubmissionController?.busy) {
    postButton.disabled = false;
    postButton.textContent = 'Post';
  }
  document.getElementById('compose-text').value = '';
  composeImageBase64 = null;
  composeVideoBase64 = null;
  composeReplyTo = null;
  document.getElementById('image-preview-wrap').classList.add('hidden');
  document.getElementById('video-preview-wrap').classList.add('hidden');
  document.getElementById('video-preview').removeAttribute('src');
  document.getElementById('image-preview').removeAttribute('src');
  document.getElementById('image-upload').value = '';
  document.getElementById('video-upload').value = '';
  document.getElementById('char-count').textContent = '500';
  document.getElementById('char-count').className = 'char-count';
}

function closeComposeOnBackdrop(e) {
  if (e.target.id === 'compose-modal') closeCompose();
}

function updateCharCount() {
  const ta = document.getElementById('compose-text');
  const cc = document.getElementById('char-count');
  const remaining = 500 - ta.value.length;
  cc.textContent = remaining;
  cc.className = 'char-count' + (remaining <= 50 ? ' warn' : '') + (remaining <= 20 ? ' danger' : '');
}

function triggerImageUpload() {
  document.getElementById('image-upload').click();
}

function handleImageUpload(e) {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 8 * 1024 * 1024) { toast('Choose an image smaller than 8 MB'); e.target.value = ''; return; }
  const reader = new FileReader();
  reader.onload = ev => {
    composeImageBase64 = ev.target.result;
    const preview = document.getElementById('image-preview');
    preview.src = composeImageBase64;
    document.getElementById('image-preview-wrap').classList.remove('hidden');
  };
  reader.readAsDataURL(file);
}

function triggerVideoUpload() { document.getElementById('video-upload').click(); }
function handleVideoUpload(e) {
  const file = e.target.files?.[0];
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) { toast('Choose a video smaller than 20 MB'); e.target.value = ''; return; }
  const reader = new FileReader();
  reader.onload = event => {
    composeVideoBase64 = event.target.result;
    const preview = document.getElementById('video-preview');
    preview.src = composeVideoBase64;
    document.getElementById('video-preview-wrap').classList.remove('hidden');
  };
  reader.readAsDataURL(file);
}

function removeVideo() {
  composeVideoBase64 = null;
  document.getElementById('video-preview').removeAttribute('src');
  document.getElementById('video-preview-wrap').classList.add('hidden');
  document.getElementById('video-upload').value = '';
}

function removeImage() {
  composeImageBase64 = null;
  document.getElementById('image-preview-wrap').classList.add('hidden');
  document.getElementById('image-upload').value = '';
}

async function submitPost() {
  const text = document.getElementById('compose-text').value.trim();
  if (!text && !composeImageBase64 && !composeVideoBase64) {
    toast('Write something first!');
    return;
  }

  // Set a synchronous guard before loading the controller module so two fast
  // clicks cannot race through the dynamic import and submit twice.
  if (postSubmissionController?.busy || postSubmissionLoading) return;
  postSubmissionLoading = true;
  const initialButton = document.querySelector('#compose-modal .btn-primary');
  if (initialButton) { initialButton.disabled = true; initialButton.textContent = 'Posting…'; }
  if (!postSubmissionController) {
    try {
      const { createSubmissionController } = await import('./post-submission.mjs');
      postSubmissionController = createSubmissionController(isBusy => {
        const button = document.querySelector('#compose-modal .btn-primary');
        if (button) { button.disabled = isBusy; button.textContent = isBusy ? 'Posting…' : 'Post'; }
      });
    } catch (error) {
      const button = document.querySelector('#compose-modal .btn-primary');
      if (button) { button.disabled = false; button.textContent = 'Post'; }
      postSubmissionLoading = false;
      toast('Could not prepare the post. Please try again.');
      return;
    }
  }
  postSubmissionLoading = false;
  const isReply = Boolean(composeReplyTo);
  const body = { text };
  if (composeImageBase64) body.image_base64 = composeImageBase64;
  if (composeVideoBase64) body.video_base64 = composeVideoBase64;
  if (composeReplyTo) body.parent_post_id = composeReplyTo;
  const submission = await postSubmissionController.run(() => apiFetch('/posts', { method: 'POST', body: JSON.stringify(body) }));
  if (!submission.ok) {
    if (!submission.duplicate) toast(submission.error?.message || 'Could not publish post. Please try again.');
    return;
  }
  try {
    const created = submission.value;
    if (!composeReplyTo) {
      state.feed.unshift(created);
      const feedEl = document.getElementById('home-feed');
      if (feedEl && state.currentPage === 'home' && document.getElementById('tab-foryou')?.classList.contains('active')) {
        const tmp = document.createElement('div');
        tmp.innerHTML = renderPostCard(created);
        feedEl.prepend(tmp.firstElementChild);
      }
    } else if (state.currentPage === 'post-detail') {
      const replies = document.getElementById('replies-section');
      if (replies && Number(created.parent_post_id) === Number(composeReplyTo) && !replies.querySelector(`[data-post-id="${created.id}"]`)) {
        replies.querySelector('.thread-empty')?.remove();
        const tmp = document.createElement('div'); tmp.innerHTML = renderPostCard(created); replies.append(tmp.firstElementChild);
        const replyStat = [...document.querySelectorAll('.detail-stat')].find(el => el.querySelector('.stat-label')?.textContent === 'Replies')?.querySelector('b');
        if (replyStat) replyStat.textContent = String(Number(replyStat.textContent || 0) + 1);
      }
    }
    closeCompose();
    refreshNotifBadge();
    if (created.integration?.state === 'not_civic') {
      toast('Post published. It was not forwarded to BMC because it was not classified as an actionable civic complaint.', 6000);
    } else if (created.integration?.state === 'uncertain') {
      toast('Post published. Civic issue detection was uncertain, so it was not sent to BMC. Please clarify the issue in a follow-up post.', 7000);
    } else if (created.integration?.state === 'classification_retry') {
      toast('Post published. Civic issue detection is temporarily unavailable; BMC forwarding will retry automatically.', 7000);
    } else if (['pending', 'sending', 'queued'].includes(created.integration?.state)) {
      toast('Post published. BMC forwarding is queued and will continue in the background.', 7000);
    } else if (created.integration?.state === 'delivery_state_unavailable') {
      toast('Post published, but BMC delivery status could not be saved. Your post remains on CivicSocial.', 7000);
    } else if (created.integration && created.integration.state !== 'acknowledged') {
      toast(`Post published. BMC delivery ${created.integration.state}: ${created.integration.error || 'it will retry automatically; check the delivery status later.'}`, 6000);
    } else if (created.integration?.complaint_reference) {
      toast(`Post published and registered with BMC: ${created.integration.complaint_reference}`);
    } else toast(isReply ? 'Reply posted' : 'Your post is live');
  } catch (error) {
    toast(error.message || 'Could not publish post. Please try again.');
  }
}

/* ============================================================
   SEARCH
   ============================================================ */
function handleSearch(e) {
  if (e.key === 'Enter') {
    const q = e.target.value.trim();
    navigate('explore');
    setTimeout(() => {
      const input = document.getElementById('explore-search-input');
      if (input) { input.value = q; filterExplore(q); }
    }, 100);
  }
}

/* ============================================================
   AUTH — Landing / Login / Signup
   ============================================================ */
function showAuthLanding() {
  document.getElementById('app-shell').classList.add('hidden');
  document.getElementById('app-shell').style.display = 'none';
  const screen = document.getElementById('auth-screen');
  screen.classList.remove('hidden');

  const landing = document.getElementById('auth-landing');
  landing.innerHTML = `
    <div class="auth-split">
      <div class="auth-visual">
        <div class="auth-visual-brand">
          <div class="auth-visual-icon">
            <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
              <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              <path d="M9 22V12h6v10" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </div>
          <div class="auth-visual-title">CivicSocial</div>
          <div class="auth-visual-sub">Connect with your neighbors.<br>Engage with local issues.<br>Make your community better.</div>
        </div>
        <div class="auth-visual-features">
          <div class="auth-feature">
            <div class="auth-feature-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" stroke="white" stroke-width="2"/><circle cx="12" cy="10" r="3" stroke="white" stroke-width="2"/></svg>
            </div>
            <div>
              <div style="font-weight:700;">Hyper-local conversations</div>
              <div style="font-size:13px;opacity:.75;">Discussions for your neighborhood</div>
            </div>
          </div>
          <div class="auth-feature">
            <div class="auth-feature-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" stroke="white" stroke-width="2"/><circle cx="9" cy="7" r="4" stroke="white" stroke-width="2"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75" stroke="white" stroke-width="2" stroke-linecap="round"/></svg>
            </div>
            <div>
              <div style="font-weight:700;">Know your neighbors</div>
              <div style="font-size:13px;opacity:.75;">Connect with real people near you</div>
            </div>
          </div>
          <div class="auth-feature">
            <div class="auth-feature-icon">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" stroke="white" stroke-width="2"/></svg>
            </div>
            <div>
              <div style="font-weight:700;">Civic engagement</div>
              <div style="font-size:13px;opacity:.75;">Track local government & initiatives</div>
            </div>
          </div>
        </div>
      </div>
      <div class="auth-form-panel" id="auth-form-panel">
        ${renderLoginForm()}
      </div>
    </div>
  `;
  renderGoogleButton();
}

function renderLoginForm(errorMsg = '') {
  return `
    <div class="auth-form-inner" id="auth-form-inner">
      <div class="auth-form-title">Welcome back</div>
      <div class="auth-form-sub">Sign in to your CivicSocial account</div>
      ${errorMsg ? `<div class="auth-error-banner">${esc(errorMsg)}</div>` : ''}
      <div class="google-button-shell"><div class="google-button-container" id="google-button-container" data-google-context="signin"></div><div class="google-config-message" id="google-config-message" role="status" aria-live="polite"></div></div>
      <div class="auth-divider">or</div>
      <form onsubmit="submitLogin(event)">
        <div class="form-group">
          <label class="form-label">Handle</label>
          <input class="form-input" id="login-handle" type="text" placeholder="@yourhandle" autocomplete="username" required>
        </div>
        <div class="form-group">
          <label class="form-label">Password</label>
          <input class="form-input" id="login-password" type="password" placeholder="••••••••" autocomplete="current-password" required>
        </div>
        <button type="submit" id="login-btn" class="btn-primary full-width" style="margin-top:6px;">Sign in</button>
      </form>
      <div style="text-align:right;margin-top:12px;">
        <a href="#" onclick="event.preventDefault(); showForgotPassword()" style="font-size:13px;color:var(--primary);">Forgot password?</a>
      </div>
      <div class="auth-switch">
        New to CivicSocial? <a onclick="showSignup()">Create an account</a>
      </div>
      <div style="text-align:center;margin-top:16px;">
        <a onclick="demoLogin()" style="font-size:12.5px;color:var(--muted);cursor:pointer;text-decoration:underline;">Try the seeded demo account</a>
      </div>
    </div>`;
}

function renderSignupForm(errorMsg = '') {
  return `
    <div class="auth-form-inner" id="auth-form-inner">
      <div class="auth-form-title">Join CivicSocial</div>
      <div class="auth-form-sub">Be part of your community's conversation</div>
      ${errorMsg ? `<div class="auth-error-banner">${esc(errorMsg)}</div>` : ''}
      <div class="google-button-shell"><div class="google-button-container" id="google-button-container" data-google-context="signup"></div><div class="google-config-message" id="google-config-message" role="status" aria-live="polite"></div></div>
      <div class="auth-divider">or</div>
      <form onsubmit="submitSignup(event)">
        <div class="form-group">
          <label class="form-label">Display name</label>
          <input class="form-input" id="signup-name" type="text" placeholder="Your full name" required maxlength="80">
        </div>
        <div class="form-group">
          <label class="form-label">Handle</label>
          <input class="form-input" id="signup-handle" type="text" placeholder="yourhandle" required maxlength="30" pattern="[a-z0-9_]{3,30}" oninput="this.value=this.value.toLowerCase()">
          <div style="font-size:12px;color:var(--muted);margin-top:5px;">3–30 characters: letters, numbers, underscores</div>
        </div>
        <div class="form-group">
          <label class="form-label">Email</label>
          <input class="form-input" id="signup-email" type="email" placeholder="you@example.com" required>
        </div>
        <div class="form-group">
          <label class="form-label">Password</label>
          <input class="form-input" id="signup-password" type="password" placeholder="At least 8 characters" required minlength="8">
        </div>
        <button type="submit" id="signup-btn" class="btn-primary full-width" style="margin-top:6px;">Create account</button>
      </form>
      <div class="auth-switch">
        Already have an account? <a onclick="showLogin()">Sign in</a>
      </div>
      <div style="text-align:center;margin-top:16px;font-size:12px;color:var(--subtle);">
        By creating an account you agree to our
        <a href="#" style="color:var(--primary);">Terms of Service</a> and
        <a href="#" style="color:var(--primary);">Privacy Policy</a>.
      </div>
    </div>`;
}

function showLogin() {
  const panel = document.getElementById('auth-form-panel');
  if (panel) { panel.innerHTML = renderLoginForm(); renderGoogleButton(); }
}

function showSignup() {
  const panel = document.getElementById('auth-form-panel');
  if (panel) { panel.innerHTML = renderSignupForm(); renderGoogleButton(); }
}

function showForgotPassword() {
  const panel = document.getElementById('auth-form-panel');
  if (!panel) return;
  panel.innerHTML = `
    <div class="auth-form-inner">
      <div class="auth-form-title">Reset password</div>
      <div class="auth-form-sub">We'll send a reset link to your email</div>
      <form onsubmit="submitForgot(event)">
        <div class="form-group">
          <label class="form-label">Email address</label>
          <input class="form-input" id="forgot-email" type="email" placeholder="you@example.com" required>
        </div>
        <button type="submit" class="btn-primary full-width">Send reset link</button>
      </form>
      <div class="auth-switch"><a onclick="showLogin()">← Back to sign in</a></div>
    </div>`;
}

async function submitForgot(e) {
  e.preventDefault();
  const email = document.getElementById('forgot-email').value.trim();
  try {
    await apiFetch('/auth/forgot-password', { method: 'POST', body: JSON.stringify({ email }) });
  } catch (_) { }
  toast('Reset link sent if that email is registered ✓');
  showLogin();
}

async function submitLogin(e) {
  e.preventDefault();
  const btn = document.getElementById('login-btn');
  const handle = document.getElementById('login-handle').value.trim().replace(/^@/, '');
  const password = document.getElementById('login-password').value;

  btn.disabled = true;
  btn.textContent = 'Signing in…';

  try {
    const data = await apiFetch('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ handle, password }),
    });
    saveAuth(data.access_token, data.user);
    showApp();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Sign in';
    if (err.code === 'EMAIL_NOT_VERIFIED') {
      showVerificationRequired(err.email || '');
      return;
    }
    const panel = document.getElementById('auth-form-panel');
    if (panel) panel.innerHTML = renderLoginForm(err.message || 'Invalid handle or password');
  }
}

async function submitSignup(e) {
  e.preventDefault();
  const btn = document.getElementById('signup-btn');
  const name = document.getElementById('signup-name').value.trim();
  const handle = document.getElementById('signup-handle').value.trim();
  const email = document.getElementById('signup-email').value.trim();
  const password = document.getElementById('signup-password').value;

  btn.disabled = true;
  btn.textContent = 'Creating account…';

  try {
    const data = await apiFetch('/auth/signup', {
      method: 'POST',
      body: JSON.stringify({ handle, display_name: name, email, password }),
    });
    showCheckEmail(data.email, data.email_sent);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Create account';
    const panel = document.getElementById('auth-form-panel');
    if (panel) panel.innerHTML = renderSignupForm(err.message || 'Could not create account. Please try again.');
  }
}

let googleConfigPromise;
let googleScriptPromise;
let googleInitialized = false;
let verificationEmail = '';
let resendCooldownTimer;

function loadGoogleScript() {
  if (window.google?.accounts?.id) return Promise.resolve();
  if (googleScriptPromise) return googleScriptPromise;
  googleScriptPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-civicsocial-google="true"]');
    const script = existing || document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.defer = true;
    script.dataset.civicsocialGoogle = 'true';
    script.onload = resolve;
    script.onerror = () => reject(new Error('Google Sign-In could not be loaded.'));
    if (!existing) document.head.appendChild(script);
  });
  return googleScriptPromise;
}

function renderGoogleButton() {
  const container = document.getElementById('google-button-container');
  const message = document.getElementById('google-config-message');
  if (!container || !message) return;
  message.textContent = '';
  if (!googleConfigPromise) googleConfigPromise = apiFetch('/auth/config');
  googleConfigPromise.then(async config => {
    if (!document.getElementById('google-button-container')) return;
    if (!config.google_client_id) {
      message.textContent = 'Google Sign-In is not configured on this server.';
      return;
    }
    await loadGoogleScript();
    if (!googleInitialized) {
      window.google.accounts.id.initialize({
        client_id: config.google_client_id,
        callback: async response => {
          try {
            const data = await apiFetch('/auth/google', { method: 'POST', body: JSON.stringify({ google_id_token: response.credential }) });
            saveAuth(data.access_token, data.user);
            showApp();
          } catch (error) { toast(error.message || 'Google sign-in failed'); }
        },
        auto_select: false,
      });
      googleInitialized = true;
    }
    const current = document.getElementById('google-button-container');
    if (!current) return;
    current.replaceChildren();
    window.google.accounts.id.renderButton(current, {
      type: 'standard',
      theme: 'outline',
      size: 'large',
      shape: 'rectangular',
      text: current.dataset.googleContext === 'signup' ? 'signup_with' : 'signin_with',
      width: Math.min(380, Math.floor(current.getBoundingClientRect().width || 380)),
    });
  }).catch(error => {
    const currentMessage = document.getElementById('google-config-message');
    if (currentMessage) currentMessage.textContent = error.message || 'Google Sign-In configuration could not be loaded.';
  });
}

function verificationStateMarkup(email, emailSent = null) {
  const delivery = emailSent === true
    ? 'We sent a verification link. Open it before signing in.'
    : emailSent === false
      ? 'Your account was created, but the verification email could not be delivered. You can try again after checking the mail configuration.'
      : 'Verify your email before signing in. If you no longer have the message, request another verification email.';
  const emailLabel = emailSent === false ? 'Account email address:' : 'Email address on the account:';
  return `<div class="auth-form-inner verification-state"><div class="auth-form-title">Check your email</div><div class="auth-form-sub">${emailLabel}</div><div class="verification-email">${esc(email || 'the address on your account')}</div><p class="verification-instructions">${delivery}</p><button class="btn-primary full-width" id="resend-verification-button" onclick="resendVerification()">Resend verification email</button><p class="verification-instructions" id="resend-verification-status" role="status" aria-live="polite"></p><div class="auth-switch"><a onclick="showLogin()">Return to sign in</a></div><div class="google-button-shell"><div class="google-button-container" id="google-button-container" data-google-context="signin"></div><div class="google-config-message" id="google-config-message" role="status" aria-live="polite"></div></div></div>`;
}

function showCheckEmail(email, emailSent) {
  verificationEmail = email || '';
  const panel = document.getElementById('auth-form-panel');
  if (panel) panel.innerHTML = verificationStateMarkup(verificationEmail, emailSent);
  renderGoogleButton();
}

function showVerificationRequired(email) {
  verificationEmail = email || '';
  const panel = document.getElementById('auth-form-panel');
  if (panel) panel.innerHTML = verificationStateMarkup(verificationEmail);
  renderGoogleButton();
}

async function resendVerification() {
  const button = document.getElementById('resend-verification-button');
  if (!button) return;
  button.disabled = true;
  try {
    const result = await apiFetch('/auth/resend-verification', { method: 'POST', body: JSON.stringify({ email: verificationEmail }) });
    const notice = document.getElementById('resend-verification-status');
    if (notice) notice.textContent = result.message;
  } catch (error) {
    const notice = document.getElementById('resend-verification-status');
    if (notice) notice.textContent = error.message || 'Could not submit the resend request.';
    button.disabled = false;
    return;
  }

  let seconds = 60;
  button.textContent = `Try again in ${seconds}s`;
  clearInterval(resendCooldownTimer);
  resendCooldownTimer = setInterval(() => {
    seconds -= 1;
    const current = document.getElementById('resend-verification-button');
    if (!current || seconds <= 0) {
      clearInterval(resendCooldownTimer);
      if (current) { current.disabled = false; current.textContent = 'Resend verification email'; }
    } else current.textContent = `Try again in ${seconds}s`;
  }, 1000);
}

async function demoLogin() {
  try {
    const data = await apiFetch('/auth/login', { method: 'POST', body: JSON.stringify({ handle: 'mybmc', password: 'CivicSocialDemo123!' }) });
    saveAuth(data.access_token, data.user);
    showApp();
    toast('Signed in as @mybmc');
  } catch (error) { toast(error.message || 'Make sure the API and seed data are ready.'); }
}

/* ============================================================
   PROFILE MENU POPOVER
   ============================================================ */
let profileMenuOpen = false;

function showProfileMenu() {
  const menu = document.getElementById('profile-menu');
  if (profileMenuOpen) {
    closeProfileMenu();
  } else {
    menu.classList.remove('hidden');
    profileMenuOpen = true;
    setTimeout(() => document.addEventListener('click', closeProfileMenuHandler, { once: true }), 50);
  }
}

function closeProfileMenu() {
  document.getElementById('profile-menu').classList.add('hidden');
  profileMenuOpen = false;
}

function closeProfileMenuHandler(e) {
  if (!document.getElementById('profile-menu').contains(e.target)) {
    closeProfileMenu();
  }
}

/* ============================================================
   FOLLOW USER (right sidebar)
   ============================================================ */
async function followUser(btn, handle) {
  try {
    const data = await apiFetch(`/users/${handle}/follow`, { method: 'POST' });
    btn.classList.toggle('following', data.following);
    btn.textContent = data.following ? 'Following' : 'Follow';
    toast(data.following ? `Following @${handle}` : `Unfollowed @${handle}`);
  } catch (error) { toast(error.message || 'Sign in to follow people'); }
}

/* ============================================================
   LOGOUT
   ============================================================ */
function logout() {
  clearAuth();
  closeProfileMenu();
  showAuthLanding();
  toast('You\'ve been signed out');
}

/* ============================================================
   KEYBOARD SHORTCUTS
   ============================================================ */
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if ((e.key === 'n' || e.key === 'N') && !e.metaKey && !e.ctrlKey) {
    if (state.token && document.getElementById('app-shell')?.style.display !== 'none') {
      openCompose();
    }
  }
  if (e.key === 'Escape') {
    closeCompose();
    closeProfileMenu();
    document.querySelectorAll('.post-dropdown').forEach(d => d.remove());
  }
});

/* ============================================================
   Boot
   ============================================================ */
boot();
