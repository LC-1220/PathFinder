// Swap broken profile pictures for the default avatar.
document.querySelectorAll('img[data-fallback-src]').forEach(img => {
  const useFallback = () => {
    if(img.getAttribute('src') === img.dataset.fallbackSrc) return;
    img.src = img.dataset.fallbackSrc;
  };
  img.addEventListener('error', useFallback, {once: true});
  if(img.complete && img.naturalWidth === 0) useFallback();
});

const canManageStudents = document.body.dataset.canManageStudents === 'true';
const canViewAdminData = document.body.dataset.canViewAdminData === 'true';
const mustChangePassword = document.body.dataset.mustChangePassword === 'true';
const currentAdminUserId = document.body.dataset.currentUserId;
const adminChartPalette = {
  set1: ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', '#ffff33', '#a65628', '#f781bf'],
  blues: ['#f7fbff', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6', '#4292c6', '#2171b5', '#084594'],
  greens: ['#f7fcf5', '#e5f5e0', '#c7e9c0', '#a1d99b', '#74c476', '#41ab5d', '#238b45', '#005a32'],
};
Chart.defaults.color = '#4d5866';
Chart.defaults.borderColor = 'rgba(67, 82, 101, 0.16)';

async function fetchJsonOrThrow(url, options = {}){
  const apiUrl = url.startsWith('/api/v1/') ? url : `/api/v1${url.startsWith('/') ? url : `/${url}`}`;
  const res = await fetch(apiUrl, options);
  const contentType = res.headers.get('content-type') || '';

  if (res.redirected && res.url.includes('/')) {
    throw new Error('Admin session expired. Please sign in again.');
  }

  let data = null;
  if (contentType.includes('application/json')) {
    data = await res.json().catch(() => null);
  } else {
    const text = await res.text().catch(() => '');
    if (text && text.trim().startsWith('{')) {
      try { data = JSON.parse(text); } catch (e) { data = null; }
    }
  }

  if (!res.ok) {
    const message = data?.error || data?.message || `${res.status} ${res.statusText}`;
    throw new Error(message || 'Unable to load dashboard data');
  }

  if (!data || typeof data !== 'object') {
    throw new Error('Dashboard data is unavailable. Please refresh or sign in again.');
  }

  return data;
}

function applyAdminPreferences(settings){
  document.body.dataset.defaultAdminView = settings.admin_default_view || 'dashboard';
  document.body.classList.toggle('admin-compact-tables', settings.admin_table_density === 'compact');
}

function updateAdminAcademicContext(){
  const form = document.getElementById('system-settings-form');
  const context = document.getElementById('admin-academic-context');
  if(!form || !context) return;
  const university = form.elements.namedItem('university_name').value.trim();
  const schoolYear = form.elements.namedItem('school_year').value.trim();
  context.textContent = `${university} · ${schoolYear || 'School year not set'}`;
}

function fillSystemSettings(settings){
  const form = document.getElementById('system-settings-form');
  if(!form) return;
  for(const name of ['university_name', 'school_year', 'max_file_size_mb', 'recommendation_limit', 'admin_default_view', 'admin_table_density']){
    if(form.elements.namedItem(name)) form.elements.namedItem(name).value = settings[name] ?? '';
  }
  form.elements.namedItem('automatic_ocr_enabled').checked = Boolean(settings.automatic_ocr_enabled);
  form.querySelectorAll('input[name="available_strands"]').forEach(input => {
    input.checked = (settings.available_strands || []).includes(input.value);
  });
  systemAvailableStrands = settings.available_strands || [];
  updateStudentFilterOptions();
  applyAdminPreferences(settings);
  updateAdminAcademicContext();
}

document.getElementById('system-settings-form')?.addEventListener('input', event => {
  if(event.target.name === 'university_name' || event.target.name === 'school_year') updateAdminAcademicContext();
});

async function loadSystemSettings(){
  if(!canManageStudents) return;
  const status = document.getElementById('system-settings-status');
  try{
    const result = await fetchJsonOrThrow('/admin/system-settings');
    fillSystemSettings(result.settings || {});
    if(status) status.hidden = true;
  }catch(error){
    if(status){ status.textContent = error.message || 'Unable to load system settings.'; status.hidden = false; status.classList.add('is-error'); }
  }
}

document.getElementById('system-settings-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.getElementById('system-settings-status');
  const settings = {
    university_name: form.elements.namedItem('university_name').value,
    school_year: form.elements.namedItem('school_year').value,
    available_strands: [...form.querySelectorAll('input[name="available_strands"]:checked')].map(input => input.value),
    max_file_size_mb: Number(form.elements.namedItem('max_file_size_mb').value),
    automatic_ocr_enabled: form.elements.namedItem('automatic_ocr_enabled').checked,
    recommendation_limit: Number(form.elements.namedItem('recommendation_limit').value),
    admin_default_view: form.elements.namedItem('admin_default_view').value,
    admin_table_density: form.elements.namedItem('admin_table_density').value,
  };
  try{
    const result = await fetchJsonOrThrow('/admin/system-settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(settings)});
    fillSystemSettings(result.settings || settings);
    status.textContent = 'System settings saved. The default page applies on the next dashboard visit.';
    status.hidden = false;
    status.classList.remove('is-error');
  }catch(error){
    status.textContent = error.message || 'Unable to save system settings.';
    status.hidden = false;
    status.classList.add('is-error');
  }
});

async function fetchStats(){
  return fetchJsonOrThrow('/admin/stats');
}

async function fetchStudents(){
  const data = await fetchJsonOrThrow('/admin/students/api');
  return data?.students || [];
}

let chartProfiles = null;
let chartGwa = null;
let chartMonthly = null;
let chartStrand = null;

function destroyIfExists(chart){
  try{ if(chart && typeof chart.destroy === 'function') chart.destroy(); }catch(e){}
}

function sequentialChartColors(values, palette){
  const numericValues = values.map(value => Math.max(0, Number(value) || 0));
  const maximum = Math.max(...numericValues, 1);
  return numericValues.map(value => palette[Math.round((value / maximum) * (palette.length - 1))]);
}

function renderProfileBar(ctxElem, labels, values){
  destroyIfExists(chartProfiles);
  const colors = sequentialChartColors(values, adminChartPalette.blues);
  chartProfiles = new Chart(ctxElem, {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Profiles', data: values, backgroundColor: colors, borderColor: colors, borderWidth: 1, borderRadius: 4, barThickness: 18 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision:0 } }, y: { ticks: { autoSkip: false } } },
      animation: { duration: 800, easing: 'easeOutCubic' }
    }
  });
}

function renderGwa(ctxElem, labels, values){
  destroyIfExists(chartGwa);
  const colors = sequentialChartColors(values, adminChartPalette.greens);
  chartGwa = new Chart(ctxElem, {
    type: 'bar',
    data: { labels, datasets: [{ label: 'Students', data: values, backgroundColor: colors, borderColor: colors, borderWidth: 1, borderRadius: 4 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision:0 } } },
      animation: { duration: 700, easing: 'easeOutCubic' }
    }
  });
}

function renderLineChart(ctxElem, labels, values){
  destroyIfExists(chartMonthly);
  chartMonthly = new Chart(ctxElem, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Uploads',
        data: values,
        borderColor: adminChartPalette.blues[6],
        backgroundColor: 'rgba(66,146,198,0.12)',
        pointBackgroundColor: adminChartPalette.blues[6],
        pointBorderColor: '#ffffff',
        pointBorderWidth: 2,
        pointHoverBackgroundColor: adminChartPalette.blues[6],
        borderWidth: 2.5,
        fill: true,
        tension: 0.35,
        pointRadius: 4,
        pointHoverRadius: 6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 } },
        x: { grid: { display: false } }
      },
      animation: { duration: 700, easing: 'easeOutCubic' }
    }
  });
}

function renderStrandPie(ctxElem, labels, values){
  destroyIfExists(chartStrand);
  const colors = labels.map((_, index) => adminChartPalette.set1[index % adminChartPalette.set1.length]);
  const total = values.reduce((sum, value) => sum + Number(value || 0), 0);
  chartStrand = new Chart(ctxElem, {
    type: 'pie',
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0, hoverBorderWidth: 0, hoverOffset: 4 }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { usePointStyle: true, generateLabels: chart => chart.data.labels.map((label, index) => ({
          text: `${label} ${Number(values[index] || 0)} (${total ? Math.round((Number(values[index] || 0) / total) * 100) : 0}%)`,
          fillStyle: colors[index],
          strokeStyle: colors[index],
          pointStyle: 'circle',
          hidden: false,
          index
        })) } },
        tooltip: { callbacks: { label: context => {
          const value = Number(context.raw || 0);
          const percentage = total ? Math.round((value / total) * 100) : 0;
          return `${context.label}: ${value} (${percentage}%)`;
        } } }
      },
      animation: { duration: 700, easing: 'easeOutCubic' }
    }
  });
}

function renderTopRecommended(containerElem, list, allList = list){
  if(!containerElem) return;
  if(!list || list.length===0){ containerElem.innerHTML = '<div class="no-courses">No recommendations data</div>'; return; }
  const total = allList.reduce((sum, item) => sum + Number(item.count || 0), 0);
  let html = '<div class="admin-recommendation-bars">';
  let rank = 1;
  list.forEach(item => {
    const percentage = total ? Math.round((Number(item.count || 0) / total) * 100) : 0;
    const color = adminChartPalette.set1[(rank - 1) % adminChartPalette.set1.length];
    html += `<div class="admin-bar-row"><div class="admin-bar-label"><span>${rank++}. <strong>${item.course}</strong></span><b>${percentage}%</b></div><div class="admin-bar-track"><i style="width:${percentage}%;background-color:${color}"></i></div></div>`;
  });
  html += '</div>';
  containerElem.innerHTML = html;
}

function renderAllRecommended(containerElem, list){
  if(!containerElem) return;
  if(!list || list.length===0){ containerElem.innerHTML = '<div class="no-courses">No recommended courses yet</div>'; return; }
  const maxCount = Math.max(...list.map(item => Number(item.count || 0)), 1);
  const colors = sequentialChartColors(list.map(item => item.count), adminChartPalette.blues);
  let html = '<div class="admin-recommendation-bars">';
  list.forEach((item, index) => {
    const width = Math.round((Number(item.count || 0) / maxCount) * 100);
    html += `<div class="admin-bar-row"><div class="admin-bar-label"><span><strong>${item.course}</strong></span><b>${item.count}</b></div><div class="admin-bar-track admin-count-track"><i style="width:${width}%;background-color:${colors[index]}"></i></div></div>`;
  });
  html += '</div>';
  containerElem.innerHTML = html;
}

const graphExpandOverlay = document.getElementById('graph-expand-overlay');
const graphExpandStage = document.getElementById('graph-expand-stage');
const closeExpandedGraphButton = document.getElementById('close-expanded-graph');
let expandedGraphCard = null;
let expandedGraphPlaceholder = null;
let graphReturnFocus = null;
let graphCloseTimer = null;

function resizeExpandedChart(card){
  card?.querySelectorAll('canvas').forEach(canvas => Chart.getChart(canvas)?.resize());
}

function openExpandedGraph(card, returnFocus){
  if(!graphExpandOverlay || !graphExpandStage || expandedGraphCard || !card) return;
  expandedGraphCard = card;
  graphReturnFocus = returnFocus || card.querySelector('.graph-expand-trigger') || card;
  expandedGraphPlaceholder = document.createElement('div');
  expandedGraphPlaceholder.className = 'graph-expand-placeholder';
  expandedGraphPlaceholder.style.height = `${card.getBoundingClientRect().height}px`;
  card.before(expandedGraphPlaceholder);
  card.classList.add('graph-expand-card');
  graphExpandStage.append(card);
  graphExpandOverlay.hidden = false;
  graphExpandOverlay.setAttribute('aria-hidden', 'false');
  document.body.classList.add('graph-expanded-open');
  requestAnimationFrame(() => {
    graphExpandOverlay.classList.add('is-visible');
    card.classList.add('is-expanded');
    resizeExpandedChart(card);
    closeExpandedGraphButton?.focus();
  });
}

function restoreExpandedGraph(){
  if(!expandedGraphCard || !expandedGraphPlaceholder) return;
  expandedGraphPlaceholder.before(expandedGraphCard);
  expandedGraphPlaceholder.remove();
  expandedGraphCard.classList.remove('graph-expand-card', 'is-expanded');
  resizeExpandedChart(expandedGraphCard);
  expandedGraphCard = null;
  expandedGraphPlaceholder = null;
  graphExpandOverlay.hidden = true;
  graphExpandOverlay.setAttribute('aria-hidden', 'true');
  graphExpandStage.replaceChildren();
  document.body.classList.remove('graph-expanded-open');
  graphReturnFocus?.focus({preventScroll:true});
  graphReturnFocus = null;
}

function closeExpandedGraph(){
  if(!expandedGraphCard || graphCloseTimer) return;
  graphExpandOverlay.classList.remove('is-visible');
  expandedGraphCard.classList.remove('is-expanded');
  graphCloseTimer = window.setTimeout(() => {
    graphCloseTimer = null;
    restoreExpandedGraph();
  }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 320);
}

document.querySelector('.cards.admin-dashboard-content')?.addEventListener('click', event => {
  const card = event.target.closest('[data-expandable-graph]');
  if(card) openExpandedGraph(card, event.target.closest('.graph-expand-trigger'));
});

document.querySelector('.cards.admin-dashboard-content')?.addEventListener('keydown', event => {
  if((event.key === 'Enter' || event.key === ' ') && event.target.matches('[data-expandable-graph]')){
    event.preventDefault();
    openExpandedGraph(event.target);
  }
});

closeExpandedGraphButton?.addEventListener('click', closeExpandedGraph);
graphExpandOverlay?.addEventListener('click', event => {
  if(event.target === graphExpandOverlay) closeExpandedGraph();
});
document.addEventListener('keydown', event => {
  if(event.key === 'Escape' && expandedGraphCard) closeExpandedGraph();
});

const adminSettingsTriggers = document.querySelectorAll('[data-admin-settings]');
const adminSettingsPanel = document.getElementById('admin-settings-panel');
const closeAdminSettings = document.getElementById('close-admin-settings');
const adminAccountTrigger = document.getElementById('admin-account-trigger');
const adminProfileMenu = document.getElementById('admin-profile-menu');
const adminThemeToggle = document.getElementById('admin-theme-toggle');
function syncAdminThemeToggle(){
  const dark = document.documentElement.classList.contains('admin-dark');
  adminThemeToggle.setAttribute('aria-pressed', String(dark));
  adminThemeToggle.querySelector('span').textContent = dark ? 'Light mode' : 'Dark mode';
  adminThemeToggle.querySelector('i').className = dark ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
  const labelColor = dark ? '#cbbfbc' : '#4d5866';
  const gridColor = dark ? 'rgba(203, 191, 188, 0.18)' : 'rgba(67, 82, 101, 0.16)';
  Chart.defaults.color = labelColor;
  Chart.defaults.borderColor = gridColor;
  [chartProfiles, chartGwa, chartMonthly, chartStrand].forEach(chart => {
    if(!chart) return;
    Object.values(chart.options.scales || {}).forEach(scale => {
      scale.ticks.color = labelColor;
      scale.grid.color = gridColor;
    });
    if(chart.options.plugins.legend?.labels) chart.options.plugins.legend.labels.color = labelColor;
    chart.update('none');
  });
}
syncAdminThemeToggle();
adminThemeToggle.addEventListener('click', () => {
  const dark = document.documentElement.classList.toggle('admin-dark');
  try { localStorage.setItem('pathfinder-admin-theme', dark ? 'dark' : 'light'); } catch (error) {}
  syncAdminThemeToggle();
});
const adminPictureInput = document.getElementById('admin-picture-input');
const adminPictureStatus = document.getElementById('admin-picture-status');
const adminNameForm = document.getElementById('admin-name-form');
const adminNameStatus = document.getElementById('admin-name-status');
const adminPasswordForm = document.getElementById('admin-password-form');
const adminPasswordStatus = document.getElementById('admin-password-status');
const adminSettingsTabButtons = document.querySelectorAll('[data-settings-tab]');

function selectAdminSettingsTab(tabName){
  adminSettingsTabButtons.forEach(button => {
    const active = button.dataset.settingsTab === tabName;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  document.querySelectorAll('[data-settings-panel]').forEach(panel => {
    panel.hidden = panel.dataset.settingsPanel !== tabName;
  });
}

adminSettingsTabButtons.forEach(button => button.addEventListener('click', () => selectAdminSettingsTab(button.dataset.settingsTab)));
const logoutConfirmModal = document.getElementById('logout-confirm-modal');
const logoutReturnFocus = {element: null};
let pendingLogoutHref = '';
let logoutCloseTimer = null;

function openLogoutConfirmation(link){
  if(!logoutConfirmModal) return;
  window.clearTimeout(logoutCloseTimer);
  pendingLogoutHref = link.href;
  logoutReturnFocus.element = link;
  logoutConfirmModal.hidden = false;
  logoutConfirmModal.setAttribute('aria-hidden', 'false');
  document.body.classList.add('logout-confirm-open');
  requestAnimationFrame(() => {
    logoutConfirmModal.classList.add('is-open');
    document.getElementById('cancel-admin-logout')?.focus();
  });
}

function closeLogoutConfirmation(){
  if(!logoutConfirmModal || logoutConfirmModal.hidden) return;
  logoutConfirmModal.classList.remove('is-open');
  document.body.classList.remove('logout-confirm-open');
  logoutCloseTimer = window.setTimeout(() => {
    logoutConfirmModal.hidden = true;
    logoutConfirmModal.setAttribute('aria-hidden', 'true');
    logoutReturnFocus.element?.focus({preventScroll:true});
    logoutReturnFocus.element = null;
    pendingLogoutHref = '';
    logoutCloseTimer = null;
  }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 180);
}

document.querySelectorAll('[data-confirm-logout]').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  openLogoutConfirmation(link);
}));
document.getElementById('cancel-admin-logout')?.addEventListener('click', closeLogoutConfirmation);
document.getElementById('confirm-admin-logout')?.addEventListener('click', () => {
  if(pendingLogoutHref) window.location.assign(pendingLogoutHref);
});
logoutConfirmModal?.addEventListener('click', event => {
  if(event.target === logoutConfirmModal) closeLogoutConfirmation();
});
document.addEventListener('keydown', event => {
  if(event.key === 'Escape' && logoutConfirmModal && !logoutConfirmModal.hidden) closeLogoutConfirmation();
});

adminNameForm?.addEventListener('submit', async event => {
  event.preventDefault();
  adminNameStatus.textContent = 'Saving...';
  try{
    const response = await fetch('/api/v1/update_account', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name: document.getElementById('admin-name-input').value})});
    const result = await response.json();
    if(!result.success) throw new Error(result.message || 'Could not update name.');
    document.querySelector('.admin-account-trigger span').textContent = result.name;
    document.querySelector('.admin-settings-card h3').textContent = result.name;
    adminNameStatus.textContent = 'Name updated.';
  }catch(error){
    adminNameStatus.textContent = error.message || 'Could not update name.';
  }
});

adminPasswordForm?.addEventListener('submit', async event => {
  event.preventDefault();
  adminPasswordStatus.textContent = 'Updating...';
  const values = Object.fromEntries(new FormData(adminPasswordForm).entries());
  try{
    const result = await fetchJsonOrThrow('/admin/change_password', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(values)});
    adminPasswordForm.reset();
    adminPasswordStatus.textContent = result.message || 'Password updated.';
  }catch(error){
    adminPasswordStatus.textContent = error.message || 'Could not update password.';
  }
});

const firstPasswordDialog = document.getElementById('first-password-dialog');
firstPasswordDialog?.addEventListener('cancel', event => event.preventDefault());
document.getElementById('first-password-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button[type="submit"]');
  const status = document.getElementById('first-password-status');
  button.disabled = true;
  status.textContent = 'Updating password...';
  try{
    await fetchJsonOrThrow('/admin/change_password', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(Object.fromEntries(new FormData(form).entries()))
    });
    window.location.replace('/admin/dashboard');
  }catch(error){
    status.textContent = error.message || 'Could not update password.';
    button.disabled = false;
  }
});

adminPictureInput?.addEventListener('change', async () => {
  const file = adminPictureInput.files[0];
  if(!file) return;
  adminPictureStatus.textContent = 'Uploading...';
  const formData = new FormData();
  formData.append('profile_picture', file);
  try{
    const response = await fetch('/api/v1/upload_profile_picture', {method:'POST', body:formData});
    const result = await response.json();
    if(!result.success) throw new Error(result.message || 'Upload failed.');
    const imageUrl = `${result.profile_image}?t=${Date.now()}`;
    document.querySelectorAll('.admin-account-trigger img, #admin-settings-picture').forEach(image => { image.src = imageUrl; });
    adminPictureStatus.textContent = 'Picture updated.';
  }catch(error){
    adminPictureStatus.textContent = error.message || 'Upload failed.';
  }
});

function setAdminSettings(open){
  if(!adminSettingsPanel) return;
  adminSettingsPanel.hidden = !open;
  if(open) selectAdminSettingsTab('profile');
  adminSettingsTriggers.forEach(trigger => trigger.setAttribute('aria-expanded', String(open)));
}

adminAccountTrigger?.addEventListener('click', event => {
  event.stopPropagation();
  const open = adminProfileMenu.hidden;
  adminProfileMenu.hidden = !open;
  adminAccountTrigger.setAttribute('aria-expanded', String(open));
});

adminSettingsTriggers.forEach(trigger => trigger.addEventListener('click', event => {
  event.preventDefault();
  event.stopPropagation();
  if(adminProfileMenu) adminProfileMenu.hidden = true;
  adminAccountTrigger?.setAttribute('aria-expanded', 'false');
  setAdminSettings(true);
}));
adminSettingsTriggers.forEach(trigger => trigger.addEventListener('keydown', event => {
  if(event.key === 'Enter' || event.key === ' '){
    event.preventDefault();
    setAdminSettings(true);
  }
}));
closeAdminSettings?.addEventListener('click', () => setAdminSettings(false));
adminSettingsPanel?.addEventListener('click', event => {
  if(event.target === adminSettingsPanel) setAdminSettings(false);
});
document.addEventListener('click', event => {
  if(adminProfileMenu && !adminProfileMenu.hidden && !adminProfileMenu.contains(event.target) && event.target !== adminAccountTrigger){
    adminProfileMenu.hidden = true;
    adminAccountTrigger?.setAttribute('aria-expanded', 'false');
  }
  if(adminSettingsPanel && !adminSettingsPanel.hidden && !adminSettingsPanel.contains(event.target) && !event.target.closest('[data-admin-settings]')){
    setAdminSettings(false);
  }
});

document.getElementById('add-admin-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.getElementById('add-admin-status');
  const values = Object.fromEntries(new FormData(form).entries());
  status.textContent = 'Creating admin account...';
  try {
    const result = await fetchJsonOrThrow('/admin/create_admin_user', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(values)
    });
    if(!result.success) throw new Error(result.message || 'Unable to create admin account.');
    status.textContent = result.message || 'Admin account created.';
    form.reset();
    await loadAdminAccounts();
    await loadAdminActivity();
  } catch (error) {
    status.textContent = error.message || 'Unable to create admin account.';
  }
});

let adminStudents = [];
let systemAvailableStrands = (document.body.dataset.availableStrands || '').split(',').filter(Boolean);
let adminSubadmins = [];
let adminReports = [];
let activeReport = null;
let recommendationHistory = [];
let frequentRecommendationCourses = [];
const escapeAdminHtml = value => String(value || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const ADMIN_ROWS_PER_PAGE = 10;
const adminListPages = {students: 1, reports: 1, accounts: 1, subadmins: 1, recommendations: 1, activity: 1};
const adminListConfig = {
  students: {render: () => renderAdminStudents(), section: 'view-students'},
  reports: {render: () => renderReportCards(), section: 'manage-reports'},
  accounts: {render: () => renderAdminAccounts(), section: 'manage-admin-accounts'},
  subadmins: {render: () => renderSubadminProfiles(), section: 'view-subadmins'},
  recommendations: {render: () => renderRecommendationHistory(), section: 'manage-recommendations'},
  activity: {render: () => renderAdminActivity(), section: 'admin-activity', perPage: 20},
};

const adminPageSize = key => adminListConfig[key]?.perPage || ADMIN_ROWS_PER_PAGE;

function paginateAdminList(key, items){
  const perPage = adminPageSize(key);
  const totalPages = Math.max(1, Math.ceil(items.length / perPage));
  adminListPages[key] = Math.min(Math.max(1, adminListPages[key] || 1), totalPages);
  const page = adminListPages[key];
  return items.slice((page - 1) * perPage, page * perPage);
}

function setAdminPage(key, page){
  adminListPages[key] = page;
  adminListConfig[key].render();
  const section = document.getElementById(adminListConfig[key].section);
  if(section && section.getBoundingClientRect().top < 0) section.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function resetAdminPage(key){
  adminListPages[key] = 1;
  adminListConfig[key].render();
}

function buildAdminPager(key, total, noun){
  const current = adminListPages[key];
  const perPage = adminPageSize(key);
  const totalPages = Math.max(1, Math.ceil(total / perPage));
  if(totalPages <= 1) return `<div class="admin-student-pager"><span class="admin-student-pager-info">Showing ${total} of ${total} ${noun}</span></div>`;
  const start = (current - 1) * perPage + 1;
  const end = Math.min(current * perPage, total);
  const pages = [];
  for(let page = 1; page <= totalPages; page += 1){
    if(page === 1 || page === totalPages || Math.abs(page - current) <= 1) pages.push(page);
    else if(pages[pages.length - 1] !== '…') pages.push('…');
  }
  const pageButtons = pages.map(page => page === '…'
    ? '<span class="admin-student-pager-gap" aria-hidden="true">…</span>'
    : `<button type="button" class="admin-student-pager-btn${page === current ? ' is-active' : ''}" data-admin-page="${page}" aria-label="Page ${page}"${page === current ? ' aria-current="page"' : ''}>${page}</button>`).join('');
  return `<nav class="admin-student-pager" aria-label="${noun} pages"><span class="admin-student-pager-info">Showing ${start}–${end} of ${total} ${noun}</span><div class="admin-student-pager-controls"><button type="button" class="admin-student-pager-btn" data-admin-page="${current - 1}" aria-label="Previous page"${current === 1 ? ' disabled' : ''}><i class="fa-solid fa-chevron-left" aria-hidden="true"></i></button>${pageButtons}<button type="button" class="admin-student-pager-btn" data-admin-page="${current + 1}" aria-label="Next page"${current === totalPages ? ' disabled' : ''}><i class="fa-solid fa-chevron-right" aria-hidden="true"></i></button></div></nav>`;
}

function bindAdminPager(wrap, key){
  wrap.querySelectorAll('.admin-student-pager-btn[data-admin-page]').forEach(button => button.addEventListener('click', () => {
    if(!button.disabled && !button.classList.contains('is-active')) setAdminPage(key, Number(button.dataset.adminPage));
  }));
}

let adminAccountUsers = [];

function renderAdminAccounts(users){
  const wrap = document.getElementById('adminAccountsWrap');
  if(!wrap) return;
  if(users) adminAccountUsers = users;
  const accounts = adminAccountUsers.filter(user => ['admin', 'semi_admin'].includes(user.role));
  if(!accounts.length){
    wrap.innerHTML = '<div class="records-empty">No administrator accounts found.</div>';
    return;
  }
  const pageAccounts = paginateAdminList('accounts', accounts);
  wrap.innerHTML = `<div class="admin-accounts-table-wrap" role="region" aria-label="Admin accounts table" tabindex="0"><table class="table-fixed admin-students-table admin-accounts-table"><thead><tr><th>Name</th><th>Username</th><th>Role</th><th>Status</th><th>Last activity</th><th>Actions</th></tr></thead><tbody>${pageAccounts.map(user => {
    const isCurrent = String(user.id) === String(currentAdminUserId);
    const roleLabel = user.role === 'admin' ? 'Super Admin' : 'Semi Admin';
    return `<tr><td>${escapeAdminHtml(user.name || 'Unnamed admin')}${isCurrent ? '<small class="admin-current-account">You</small>' : ''}</td><td>${escapeAdminHtml(user.username || '—')}</td><td><select class="admin-role-select" data-admin-role-id="${escapeAdminHtml(user.id)}" aria-label="Role for ${escapeAdminHtml(user.name)}" ${isCurrent ? 'disabled' : ''}><option value="semi_admin" ${user.role === 'semi_admin' ? 'selected' : ''}>Semi Admin</option><option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Super Admin</option></select><small class="admin-role-current">${roleLabel}</small></td><td><span class="student-status ${user.is_active ? 'is-active' : 'is-inactive'}">${user.is_active ? 'Active' : 'Deactivated'}</span></td><td>${escapeAdminHtml(user.last_login_at ? formatReportDate(user.last_login_at) : 'Never signed in')}</td><td><div class="admin-account-actions"><button class="admin-save-role" type="button" data-admin-id="${escapeAdminHtml(user.id)}" ${isCurrent ? 'disabled' : ''}>Save role</button><button class="admin-remove-account" type="button" data-admin-id="${escapeAdminHtml(user.id)}" ${isCurrent ? 'disabled' : ''}>Remove</button></div></td></tr>`;
      }).join('')}</tbody></table></div>${buildAdminPager('accounts', accounts.length, 'admin accounts')}`;
      bindAdminPager(wrap, 'accounts');
  wrap.querySelectorAll('.admin-save-role').forEach(button => button.addEventListener('click', () => updateAdminRole(button.dataset.adminId, wrap)));
  wrap.querySelectorAll('.admin-remove-account').forEach(button => button.addEventListener('click', () => removeAdminAccount(button.dataset.adminId)));
}

async function loadAdminAccounts(){
  const wrap = document.getElementById('adminAccountsWrap');
  if(!canManageStudents || !wrap) return;
  try{
    const result = await fetchJsonOrThrow('/admin/users');
    renderAdminAccounts(result.users || []);
  }catch(error){
    wrap.innerHTML = `<div class="records-empty">${escapeAdminHtml(error.message || 'Unable to load admin accounts.')}</div>`;
  }
}

async function updateAdminRole(userId, wrap){
  const select = wrap.querySelector(`[data-admin-role-id="${CSS.escape(String(userId))}"]`);
  if(!select) return;
  try{
    const result = await fetchJsonOrThrow('/admin/update_admin_role', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id: userId, role: select.value}),
    });
    if(!result.success) throw new Error(result.message || 'Could not update admin role.');
    await loadAdminAccounts();
    await loadAdminActivity();
    const status = document.getElementById('admin-account-status');
    if(status){ status.textContent = result.message || 'Admin role updated.'; status.hidden = false; }
  }catch(error){
    const status = document.getElementById('admin-account-status');
    if(status){ status.textContent = error.message || 'Could not update admin role.'; status.hidden = false; status.classList.add('is-error'); }
  }
}

async function removeAdminAccount(userId){
  const account = await fetchJsonOrThrow('/admin/users').then(result => (result.users || []).find(user => String(user.id) === String(userId)));
  if(!account || !confirm(`Permanently remove ${account.name || account.username} (${account.role === 'admin' ? 'Super Admin' : 'Semi Admin'})?`)) return;
  try{
    const result = await fetchJsonOrThrow('/admin/remove_admin_account', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id: userId}),
    });
    if(!result.success) throw new Error(result.message || 'Could not remove admin account.');
    await loadAdminAccounts();
    await loadAdminActivity();
  }catch(error){
    const status = document.getElementById('admin-account-status');
    if(status){ status.textContent = error.message || 'Could not remove admin account.'; status.hidden = false; status.classList.add('is-error'); }
  }
}

function getAdminActivityCategory(item){
  return item?.actor_role === 'student' ? 'student' : 'admin';
}

const studentActivityLabels = {
  login: 'Signed in',
  logout: 'Signed out',
  account_registered: 'Created an account',
  report_card_uploaded: 'Uploaded a report card',
  report_card_upload_failed: 'Report card upload failed',
  grades_saved: 'Saved grades and profile',
  recommendations_generated: 'Generated course recommendations',
  account_name_changed: 'Changed account name',
  profile_picture_changed: 'Changed profile picture',
};

function formatActivityAction(item){
  if(item.actor_role === 'student' && studentActivityLabels[item.action]) return studentActivityLabels[item.action];
  const text = String(item.action || '').replaceAll('_', ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

let adminActivityItems = [];

function renderAdminActivity(){
  const wrap = document.getElementById('adminActivityWrap');
  if(!wrap) return;
  if(!adminActivityItems.length){ wrap.innerHTML = '<div class="records-empty">No activity recorded yet.</div>'; return; }
  const category = document.getElementById('adminActivityCategory')?.value || 'all';
  const fromValue = document.getElementById('adminActivityFrom')?.value;
  const toValue = document.getElementById('adminActivityTo')?.value;
  const fromTime = fromValue ? new Date(fromValue).getTime() : null;
  // datetime-local has minute precision, so include the whole "To" minute.
  const toTime = toValue ? new Date(toValue).getTime() + 59999 : null;
  const filtered = adminActivityItems.filter(item => {
    const itemTime = Date.parse(item.created_at);
    const hasTime = Number.isFinite(itemTime) && itemTime > 0;
    const inRange = (fromTime === null || (hasTime && itemTime >= fromTime)) && (toTime === null || (hasTime && itemTime <= toTime));
    return (category === 'all' || getAdminActivityCategory(item) === category) && inRange;
  });
  const pageItems = paginateAdminList('activity', filtered);
  const rows = pageItems.length
    ? pageItems.map(item => `<tr data-activity-category="${getAdminActivityCategory(item)}"><td>${escapeAdminHtml(formatReportDate(item.created_at))}</td><td>${escapeAdminHtml(item.actor_name)}<span class="admin-activity-role is-${item.actor_role === 'student' ? 'student' : 'admin'}">${item.actor_role === 'student' ? 'Student' : 'Admin'}</span><small class="admin-current-account">${escapeAdminHtml(item.actor_email)}</small></td><td>${escapeAdminHtml(formatActivityAction(item))}</td><td>${escapeAdminHtml(item.target_label || '—')}</td><td>${escapeAdminHtml(item.session_ip || '—')}</td><td>${escapeAdminHtml(item.details || '—')}</td></tr>`).join('')
    : '<tr class="admin-activity-filter-empty"><td colspan="6">No activity matches these filters.</td></tr>';
  wrap.innerHTML = `<div class="admin-activity-table-wrap" role="region" aria-label="Admin activity records" tabindex="0"><table class="table-fixed admin-students-table admin-activity-table"><thead><tr><th>When</th><th>User</th><th>Action</th><th>Target</th><th>Session IP</th><th>Details</th></tr></thead><tbody>${rows}</tbody></table></div>${filtered.length ? buildAdminPager('activity', filtered.length, 'activities') : ''}`;
  bindAdminPager(wrap, 'activity');
}

function filterAdminActivity(){
  resetAdminPage('activity');
}

function clearAdminActivityFilters(){
  const category = document.getElementById('adminActivityCategory');
  const from = document.getElementById('adminActivityFrom');
  const to = document.getElementById('adminActivityTo');
  if(category) category.value = 'all';
  if(from) from.value = '';
  if(to) to.value = '';
  filterAdminActivity();
}

document.getElementById('adminActivityCategory')?.addEventListener('change', filterAdminActivity);
document.getElementById('adminActivityFrom')?.addEventListener('change', filterAdminActivity);
document.getElementById('adminActivityTo')?.addEventListener('change', filterAdminActivity);
document.getElementById('clear-admin-activity-filters')?.addEventListener('click', clearAdminActivityFilters);

async function loadAdminActivity(){
  const wrap = document.getElementById('adminActivityWrap');
  if(!canManageStudents || !wrap) return;
  try{
    const result = await fetchJsonOrThrow('/admin/activity');
    adminActivityItems = result.activity || [];
    renderAdminActivity();
  }catch(error){
    wrap.innerHTML = `<div class="records-empty">${escapeAdminHtml(error.message || 'Unable to load admin activity.')}</div>`;
  }
}

function adminProfileImageUrl(value){
  const image = String(value || '').trim();
  if(/^https?:\/\//i.test(image)) return image;
  const stored = image.match(/^db:(\d+):(\w+)$/);
  if(stored) return `/api/v1/profile-image/${stored[1]}/${stored[2]}`;
  const filename = image.split(/[\\/]/).pop();
  if(!filename || ['default.jpg', 'default.png', 'default.svg'].includes(filename.toLowerCase())){
    return '/static/profile_pictures/default.svg';
  }
  return `/static/profile_pictures/${encodeURIComponent(filename)}`;
}

function renderSubadminProfiles(){
  const wrap = document.getElementById('subadminProfilesWrap');
  if(!wrap) return;
  if(!adminSubadmins.length){
    wrap.innerHTML = '<div class="records-empty">No subadmin profiles found.</div>';
    return;
  }
  const query = (document.getElementById('subadminSearch')?.value || '').trim().toLowerCase();
  const filtered = adminSubadmins.filter(user => `${user.name || ''} ${user.username || ''}`.toLowerCase().includes(query));
  if(!filtered.length){
    wrap.innerHTML = '<div class="records-empty">No subadmin profiles match your search.</div>';
    return;
  }
  const pageSubadmins = paginateAdminList('subadmins', filtered);
  wrap.innerHTML = `<table class="table-fixed admin-students-table admin-subadmins-table"><thead><tr><th>Profile</th><th>Name</th><th>Username</th><th>Account status</th><th></th></tr></thead><tbody>${pageSubadmins.map(user => {
    const image = adminProfileImageUrl(user.profile_picture);
    const status = user.is_active ? 'Active' : 'Deactivated';
    return `<tr><td><img class="subadmin-avatar" src="${escapeAdminHtml(image)}" alt=""></td><td>${escapeAdminHtml(user.name || 'Unnamed coordinator')}</td><td>${escapeAdminHtml(user.username || '—')}</td><td><span class="student-status ${user.is_active ? 'is-active' : 'is-inactive'}">${status}</span></td><td><button class="subadmin-view-button" type="button" data-subadmin-id="${escapeAdminHtml(user.id)}">View profile</button></td></tr>`;
      }).join('')}</tbody></table>${buildAdminPager('subadmins', filtered.length, 'subadmins')}`;
      bindAdminPager(wrap, 'subadmins');
  wrap.querySelectorAll('.subadmin-view-button').forEach(button => button.addEventListener('click', () => {
    const user = adminSubadmins.find(item => String(item.id) === button.dataset.subadminId);
    if(!user) return;
    document.getElementById('subadmin-profile-picture').src = adminProfileImageUrl(user.profile_picture);
    document.getElementById('subadmin-profile-picture').alt = `${user.name || 'Subadmin'} profile picture`;
    document.getElementById('subadmin-profile-name').textContent = user.name || 'Subadmin profile';
    document.getElementById('subadmin-profile-username').textContent = user.username || 'Username unavailable';
    document.getElementById('subadmin-profile-status').textContent = user.is_active ? 'Active' : 'Deactivated';
    document.getElementById('subadmin-profile-action-status').textContent = '';
    document.getElementById('remove-subadmin-account').dataset.userId = user.id;
    document.getElementById('subadmin-profile-modal').hidden = false;
  }));
}

document.getElementById('subadminSearch')?.addEventListener('input', () => resetAdminPage('subadmins'));

async function loadSubadminProfiles(){
  const wrap = document.getElementById('subadminProfilesWrap');
  if(!canManageStudents || !wrap) return;
  try{
    const result = await fetchJsonOrThrow('/admin/users');
    adminSubadmins = (result.users || []).filter(user => user.role === 'semi_admin');
    renderSubadminProfiles();
  }catch(error){
    wrap.innerHTML = `<div class="records-empty">${escapeAdminHtml(error.message || 'Unable to load subadmin profiles.')}</div>`;
  }
}

function formatReportDate(value){
  if(!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function reportStatusLabel(report){
  if(report.is_flagged) return 'Flagged';
  return ({processing:'Processing', needs_review:'Needs review', extracted:'Extracted', verified:'Verified', failed:'Failed'})[report.ocr_status] || report.ocr_status || 'Needs review';
}

function renderReportCards(){
  const wrap = document.getElementById('reportCardsWrap');
  if(!wrap) return;
  const query = (document.getElementById('reportSearch')?.value || '').trim().toLowerCase();
  const status = document.getElementById('reportStatusFilter')?.value || '';
  const filtered = adminReports.filter(report => {
    const text = `${report.student_name} ${report.student_number} ${report.original_filename}`.toLowerCase();
    const matchesStatus = !status || (status === 'flagged' ? report.is_flagged : report.ocr_status === status);
    return text.includes(query) && matchesStatus;
  });
  if(!filtered.length){
    wrap.innerHTML = `<div class="records-empty">${adminReports.length ? 'No report cards match your search.' : 'No uploaded report cards found.'}</div>`;
    return;
  }
  const pageReports = paginateAdminList('reports', filtered);
  wrap.innerHTML = `<div class="admin-report-table-wrap" role="region" aria-label="Report card records" tabindex="0"><table class="table-fixed admin-students-table admin-report-table"><thead><tr><th>Uploaded</th><th>Student</th><th>Student number</th><th>OCR status</th><th>Grades</th><th>Flag</th><th></th></tr></thead><tbody>${pageReports.map(report => {
    const statusClass = report.is_flagged ? 'is-inactive' : (report.ocr_status === 'verified' ? 'is-active' : 'is-review');
    return `<tr><td>${escapeAdminHtml(formatReportDate(report.uploaded_at))}</td><td>${escapeAdminHtml(report.student_name || 'Unknown student')}</td><td>${escapeAdminHtml(report.student_number || '—')}</td><td><span class="student-status ${statusClass}">${escapeAdminHtml(reportStatusLabel(report))}</span></td><td>${report.extracted_subject_count} subjects${report.extracted_gwa ? ` · GWA ${escapeAdminHtml(report.extracted_gwa)}` : ''}</td><td>${report.is_flagged ? escapeAdminHtml(report.flag_note || 'Flagged') : '—'}</td><td><button class="report-review-open" type="button" data-report-id="${report.id}"><i class="fa-solid fa-clipboard-check" aria-hidden="true"></i><span>Review</span></button></td></tr>`;
      }).join('')}</tbody></table></div>${buildAdminPager('reports', filtered.length, 'report cards')}`;
      bindAdminPager(wrap, 'reports');
  wrap.querySelectorAll('.report-review-open').forEach(button => button.addEventListener('click', () => openReportReview(button.dataset.reportId)));
}

async function loadReportCards(){
  const wrap = document.getElementById('reportCardsWrap');
  if(!canManageStudents || !wrap) return;
  wrap.innerHTML = '<div class="records-empty">Loading report cards...</div>';
  try{
    const result = await fetchJsonOrThrow('/admin/report_cards');
    adminReports = result.reports || [];
    renderReportCards();
  }catch(error){
    wrap.innerHTML = `<div class="records-empty">${escapeAdminHtml(error.message || 'Unable to load report cards.')}</div>`;
  }
}

function renderFrequentRecommendationCourses(){
  const container = document.getElementById('frequentRecommendationCourses');
  if(!container) return;
  if(!frequentRecommendationCourses.length){
    container.innerHTML = '<div class="records-empty">No saved recommendation history yet.</div>';
    return;
  }
  const maxCount = Math.max(...frequentRecommendationCourses.map(item => Number(item.count || 0)), 1);
  container.innerHTML = `<div class="admin-recommendation-bars">${frequentRecommendationCourses.slice(0, 12).map((item, index) => {
    const width = Math.round(Number(item.count || 0) / maxCount * 100);
    return `<div class="admin-bar-row"><div class="admin-bar-label"><span>${index + 1}. <strong>${escapeAdminHtml(item.course)}</strong></span><b>${Number(item.count || 0)}</b></div><div class="admin-bar-track"><i style="width:${width}%"></i></div></div>`;
  }).join('')}</div>`;
}

function renderRecommendationHistory(){
  const wrap = document.getElementById('recommendationHistoryWrap');
  if(!wrap) return;
  const query = (document.getElementById('recommendationSearch')?.value || '').trim().toLowerCase();
  const filtered = recommendationHistory.filter(item => {
    const recommendationText = (item.recommendations || []).map(rec => `${rec.course || ''} ${rec.reason || ''}`).join(' ');
    return `${item.student_name || ''} ${item.student_number || ''} ${item.strand || ''} ${item.email || ''} ${recommendationText}`.toLowerCase().includes(query);
  });
  if(!filtered.length){
    wrap.innerHTML = `<div class="records-empty">${recommendationHistory.length ? 'No recommendation history matches your search.' : 'No saved recommendation history yet.'}</div>`;
    return;
  }
  const pageHistory = paginateAdminList('recommendations', filtered);
  wrap.innerHTML = `<table class="table-fixed admin-students-table admin-recommendation-history-table"><thead><tr><th>Generated</th><th>Student</th><th>Strand</th><th>GWA</th><th>Top recommendation</th><th>Confidence</th><th>Grade fit</th><th>Actions</th></tr></thead><tbody>${pageHistory.map(item => {
    const top = item.recommendations?.[0] || {};
    const confidence = Number(top.confidence);
    const fit = Number(top.core_grade_fit);
    const recalculateAction = canManageStudents
      ? `<button class="recommendation-recalculate-button" type="button" data-profile-id="${item.profile_id}"><i class="fa-solid fa-rotate" aria-hidden="true"></i><span>Recalculate</span></button>`
      : '';
    return `<tr><td>${escapeAdminHtml(formatReportDate(item.generated_at))}</td><td><strong>${escapeAdminHtml(item.student_name || 'Unknown student')}</strong><small class="recommendation-student-number">${escapeAdminHtml(item.student_number || item.email || '')}</small></td><td>${escapeAdminHtml(item.strand || '—')}</td><td>${escapeAdminHtml(item.gwa || '—')}</td><td>${escapeAdminHtml(top.course || 'No recommendation')}</td><td>${Number.isFinite(confidence) ? `${confidence.toFixed(1)}%` : '—'}</td><td>${Number.isFinite(fit) ? `${fit.toFixed(1)}%` : '—'}</td><td><div class="recommendation-row-actions"><button class="recommendation-detail-button" type="button" data-history-id="${item.history_id}">Details</button>${recalculateAction}</div></td></tr>`;
  }).join('')}</tbody></table>${buildAdminPager('recommendations', filtered.length, 'students')}`;
  bindAdminPager(wrap, 'recommendations');
  wrap.scrollLeft = 0;
  wrap.querySelectorAll('.recommendation-detail-button').forEach(button => button.addEventListener('click', () => openRecommendationDetail(button.dataset.historyId)));
  wrap.querySelectorAll('.recommendation-recalculate-button').forEach(button => button.addEventListener('click', () => recalculateRecommendations(button.dataset.profileId, button)));
}

function openRecommendationDetail(historyId){
  const item = recommendationHistory.find(entry => String(entry.history_id) === String(historyId));
  if(!item) return;
  document.getElementById('recommendation-detail-title').textContent = item.student_name || 'Student recommendations';
  document.getElementById('recommendation-detail-kicker').textContent = `Generated ${formatReportDate(item.generated_at)}`;
  document.getElementById('recommendation-detail-summary').innerHTML = `<span><strong>Student number</strong>${escapeAdminHtml(item.student_number || '—')}</span><span><strong>Strand</strong>${escapeAdminHtml(item.strand || '—')}</span><span><strong>GWA</strong>${escapeAdminHtml(item.gwa || '—')}</span>`;
  const gradeRows = parseReportSubjectRows(item.subjects);
  document.getElementById('recommendation-grade-comparison').innerHTML = gradeRows.length
    ? `<table class="recommendation-grade-table"><thead><tr><th>Subject</th><th>Grade</th></tr></thead><tbody>${gradeRows.map(row => `<tr><td>${escapeAdminHtml(row.subject_name)}</td><td>${escapeAdminHtml(row.grade)}</td></tr>`).join('')}</tbody></table>`
    : '<p class="student-detail-empty">No subject grades were saved with this profile.</p>';
  const recommendations = item.recommendations || [];
  document.getElementById('recommendation-detail-list').innerHTML = recommendations.length
    ? recommendations.map((rec, index) => `<article class="recommendation-detail-item"><div class="recommendation-detail-item-heading"><h4>${index + 1}. ${escapeAdminHtml(rec.course || 'Unspecified course')}</h4><span>${Number.isFinite(Number(rec.confidence)) ? `${Number(rec.confidence).toFixed(1)}% confidence` : 'Confidence unavailable'}</span></div><p>${escapeAdminHtml(rec.description || '')}</p><p class="recommendation-detail-reason"><strong>Reason:</strong> ${escapeAdminHtml(rec.reason || 'No reason saved.')}</p><div class="recommendation-fit-tags"><span>Core grade fit: ${Number.isFinite(Number(rec.core_grade_fit)) ? `${Number(rec.core_grade_fit).toFixed(1)}%` : '—'}</span><span>${rec.strand_grade_based ? 'Strand aligned' : 'Grade profile match'}</span></div></article>`).join('')
    : '<p class="student-detail-empty">No recommendations in this snapshot.</p>';
  document.getElementById('recommendation-detail-modal').hidden = false;
}

async function loadRecommendationManagement(){
  const historyWrap = document.getElementById('recommendationHistoryWrap');
  if(!canViewAdminData || !historyWrap) return;
  historyWrap.innerHTML = '<div class="records-empty">Loading recommendation history...</div>';
  try{
    const result = await fetchJsonOrThrow('/admin/recommendations');
    recommendationHistory = result.history || [];
    frequentRecommendationCourses = result.frequent_courses || [];
    renderFrequentRecommendationCourses();
    renderRecommendationHistory();
  }catch(error){
    historyWrap.innerHTML = `<div class="records-empty">${escapeAdminHtml(error.message || 'Unable to load recommendation history.')}</div>`;
  }
}

async function recalculateRecommendations(profileId, button){
  if(!profileId || !confirm('Recalculate recommendations using this student profile’s current grades and strand?')) return;
  button.disabled = true;
  const previousContent = button.innerHTML;
  button.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i><span>Working...</span>';
  try{
    const result = await fetchJsonOrThrow(`/admin/recommendations/${encodeURIComponent(profileId)}/recalculate`, {method:'POST'});
    await loadRecommendationManagement();
    const status = document.getElementById('recommendationActionStatus');
    status.textContent = result.message || 'Recommendations recalculated.';
    status.hidden = false;
    status.classList.remove('is-error');
  }catch(error){
    button.disabled = false;
    button.innerHTML = previousContent;
    const status = document.getElementById('recommendationActionStatus');
    status.textContent = error.message || 'Unable to recalculate recommendations.';
    status.hidden = false;
    status.classList.add('is-error');
  }
}

function parseReportSubjectRows(subjects){
  return String(subjects || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
    const match = line.match(/^(.+?)\s+-\s*(-?\d+(?:\.\d+)?)$/);
    return {subject_name: match ? match[1] : line, grade: match ? match[2] : ''};
  });
}

function renderReportGradeRows(subjects){
  const body = document.getElementById('report-review-grade-body');
  const disabled = canManageStudents ? '' : 'disabled';
  const action = canManageStudents ? '<button class="editor-remove-row" type="button" data-remove-report-row aria-label="Remove subject row"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button>' : '';
  body.innerHTML = (subjects.length ? subjects : [{subject_name:'', grade:''}]).map(item => `<tr><td><input data-report-subject type="text" value="${escapeAdminHtml(item.subject_name)}" aria-label="Subject name" ${disabled}></td><td><input data-report-grade type="number" min="0" max="100" step="0.01" value="${escapeAdminHtml(item.grade)}" aria-label="Subject grade" ${disabled}></td><td>${action}</td></tr>`).join('');
}

function setReportActionStatus(message, isError = false){
  const status = document.getElementById('report-review-status');
  status.textContent = message;
  status.classList.toggle('is-error', isError);
}

function openReportReview(reportId){
  const report = adminReports.find(item => String(item.id) === String(reportId));
  if(!report) return;
  activeReport = report;
  document.getElementById('report-review-title').textContent = report.original_filename || 'Report card';
  document.getElementById('report-review-kicker').textContent = reportStatusLabel(report);
  document.getElementById('report-review-student').textContent = report.student_name || 'Unknown student';
  document.getElementById('report-review-number').textContent = report.student_number ? `Student no. ${report.student_number}` : 'Student number unavailable';
  document.getElementById('report-review-date').textContent = formatReportDate(report.uploaded_at);
  const reportUrl = `/api/v1/admin/report_cards/${encodeURIComponent(report.id)}`;
  const reportImage = document.getElementById('report-review-image');
  const reportImageStatus = document.getElementById('report-review-image-status');
  reportImage.hidden = true;
  reportImageStatus.textContent = 'Loading original report card...';
  reportImageStatus.hidden = false;
  reportImage.onload = () => {
    reportImage.hidden = false;
    reportImageStatus.hidden = true;
  };
  reportImage.onerror = () => {
    reportImage.hidden = true;
    reportImageStatus.textContent = 'The original report card could not be loaded. Use Open original image to retry.';
    reportImageStatus.hidden = false;
  };
  reportImage.src = reportUrl;
  if(reportImage.complete && reportImage.naturalWidth > 0){
    reportImage.hidden = false;
    reportImageStatus.hidden = true;
  }
  document.getElementById('report-review-original-link').href = reportUrl;
  const errorNode = document.getElementById('report-review-error');
  errorNode.textContent = report.ocr_error ? `OCR error: ${report.ocr_error}` : '';
  errorNode.hidden = !report.ocr_error;
  const flagNote = document.getElementById('report-flag-note');
  if(flagNote) flagNote.value = report.flag_note || '';
  const readonlyFlagNote = document.getElementById('report-flag-readonly');
  if(readonlyFlagNote) readonlyFlagNote.textContent = report.is_flagged ? `Flagged: ${report.flag_note || 'No note provided.'}` : 'No issue flag.';
  const flagButton = document.getElementById('flag-report-card');
  if(flagButton) flagButton.querySelector('span').textContent = report.is_flagged ? 'Clear flag' : 'Flag upload';
  setReportActionStatus('');
  renderReportGradeRows(parseReportSubjectRows(report.extracted_subjects));
  document.getElementById('report-review-modal').hidden = false;
}

async function refreshReportCards(){
  await loadReportCards();
  const report = activeReport && adminReports.find(item => String(item.id) === String(activeReport.id));
  if(report) openReportReview(report.id);
}

function renderAdminStudents(){
  const wrap = document.getElementById('studentsWrap');
  if(!wrap) return;
  const query = (document.getElementById('globalSearch')?.value || '').trim().toLowerCase();
  const strand = document.getElementById('strandFilter')?.value || '';
  const status = document.getElementById('studentStatusFilter')?.value || '';
  const filtered = adminStudents.filter(student => {
    const matchesQuery = `${student.student_name || ''} ${student.student_number || ''} ${student.email || ''}`.toLowerCase().includes(query);
    return matchesQuery
      && (!strand || student.strand === strand)
      && (!status || (status === 'active') === Boolean(student.is_active));
  });
  if(!filtered.length){ wrap.innerHTML = '<div class="records-empty">No students found.</div>'; return; }
  const pageStudents = paginateAdminList('students', filtered);
  wrap.innerHTML = `<table class="table-fixed admin-students-table"><thead><tr><th>Student number</th><th>Student</th><th>Strand</th><th>GWA</th><th>Account</th><th>Report</th></tr></thead><tbody>${pageStudents.map(student => {
    const accountStatus = student.is_active ? 'Active' : 'Deactivated';
    const reportAction = student.report_card_upload_id
      ? `<button class="student-report-button" type="button" data-report-id="${student.report_card_upload_id}" aria-label="View uploaded report card"><i class="fa-solid fa-eye" aria-hidden="true"></i><span>View</span></button>`
      : '<span class="student-report-empty">Not uploaded</span>';
    return `<tr><td>${escapeAdminHtml(student.student_number || '—')}</td><td><button class="student-name-link" type="button" data-student-id="${student.id}">${escapeAdminHtml(student.student_name || 'Unnamed student')}<small>${escapeAdminHtml(student.email)}</small></button></td><td>${escapeAdminHtml(student.strand || '—')}</td><td>${escapeAdminHtml(student.gwa || '—')}</td><td><span class="student-status ${student.is_active ? 'is-active' : 'is-inactive'}">${accountStatus}</span></td><td>${reportAction}</td></tr>`;
  }).join('')}</tbody></table>${buildAdminPager('students', filtered.length, 'students')}`;
  bindAdminPager(wrap, 'students');
  wrap.querySelectorAll('.student-name-link').forEach(button => button.addEventListener('click', () => openStudentEditor(button.dataset.studentId)));
  wrap.querySelectorAll('.student-report-button').forEach(button => button.addEventListener('click', () => {
    const reportUrl = `/api/v1/admin/report_cards/${encodeURIComponent(button.dataset.reportId)}`;
    window.open(reportUrl, '_blank', 'noopener,noreferrer');
  }));
}

function updateStudentFilterOptions(){
  const setOptions = (id, values, label) => {
    const select = document.getElementById(id);
    if(!select) return;
    const selected = select.value;
    const options = [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b));
    select.innerHTML = `<option value="">All ${label}</option>${options.map(value => `<option value="${escapeAdminHtml(value)}">${escapeAdminHtml(value)}</option>`).join('')}`;
    if(options.includes(selected)) select.value = selected;
  };
  setOptions('strandFilter', [...systemAvailableStrands, ...adminStudents.map(student => student.strand)], 'strands');
}

function renderEditorGradeRows(subjects){
  const body = document.getElementById('student-editor-grade-body');
  if(!body) return;
  const rows = String(subjects || '').split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
    const match = line.match(/^(.+?)\s+-\s+(\d+(?:\.\d+)?)$/);
    return {subject: match ? match[1] : line, grade: match ? match[2] : ''};
  });
  body.innerHTML = (rows.length ? rows : [{subject:'', grade:''}]).map(row => `<tr><td><input data-editor-subject type="text" value="${escapeAdminHtml(row.subject)}" disabled></td><td><input data-editor-grade type="text" value="${escapeAdminHtml(row.grade)}" disabled></td><td><button type="button" class="editor-remove-row" data-remove-editor-row disabled>Remove</button></td></tr>`).join('');
}

async function openStudentEditor(studentId){
  const modal = document.getElementById('student-editor-modal');
  const form = document.getElementById('student-editor-form');
  if(!modal || !form) return;
  try{
    const result = await fetchJsonOrThrow(`/admin/students/${encodeURIComponent(studentId)}`);
    const student = result.student;
    const hasProfile = Boolean(student.profile_id);
    form.elements.id.value = student.profile_id || '';
    form.elements.user_id.value = student.id;
    form.elements.email.value = student.email;
    form.elements.student_number.value = student.student_number;
    form.elements.student_name.value = [
      student.first_name,
      student.middle_initial ? `${String(student.middle_initial).replace(/\.$/, '')}.` : '',
      student.last_name,
    ].filter(Boolean).join(' ') || student.account_name;
    form.elements.strand.value = student.strand;
    form.elements.gwa.value = student.gwa;
    document.getElementById('student-editor-title').textContent = student.student_name || 'Student details';
    document.getElementById('student-editor-kicker').textContent = student.is_active ? 'Active student account' : 'Deactivated student account';
    renderEditorGradeRows(student.subjects);
    form.querySelectorAll('input, textarea').forEach(field => {
      field.disabled = !['id', 'user_id'].includes(field.name);
      field.readOnly = field.name === 'email';
    });
    const saveButton = form.querySelector('button[type="submit"]');
    if(saveButton) saveButton.disabled = true;
    const editButton = document.getElementById('edit-student-record');
    if(editButton) editButton.textContent = canManageStudents ? (hasProfile ? 'Edit profile' : 'Create profile') : 'View only';
    if(editButton) editButton.disabled = !canManageStudents;
    const deactivateButton = document.getElementById('deactivate-student-account');
    deactivateButton.hidden = !canManageStudents;
    deactivateButton.textContent = student.is_active ? 'Deactivate account' : 'Reactivate account';
    deactivateButton.dataset.active = String(student.is_active);
    const deleteProfileButton = document.getElementById('delete-student-profile');
    deleteProfileButton.hidden = !canManageStudents || !hasProfile;
    document.querySelectorAll('[data-remove-editor-row]').forEach(button => { button.disabled = true; });
    document.getElementById('student-editor-status').textContent = !hasProfile
      ? 'No academic profile has been saved for this account.'
      : canManageStudents ? '' : 'View-only access';
    const reportImage = document.getElementById('student-report-card-image');
    const reportLink = document.getElementById('student-report-card-link');
    const reportEmpty = document.getElementById('student-report-card-empty');
    const reportPages = document.getElementById('student-report-card-pages');
    const reportIds = (student.report_card_upload_ids?.length ? student.report_card_upload_ids : [student.report_card_upload_id]).filter(Boolean);
    const hasReport = reportIds.length > 0;
    const showReportPage = index => {
      const reportUrl = `/api/v1/admin/report_cards/${encodeURIComponent(reportIds[index])}`;
      reportImage.hidden = false;
      reportLink.hidden = false;
      reportEmpty.hidden = true;
      reportImage.src = reportUrl;
      reportLink.href = reportUrl;
      reportLink.textContent = reportIds.length > 1 ? `Open image ${index + 1} of ${reportIds.length}` : 'Open original upload';
      reportPages.querySelectorAll('button').forEach((button, buttonIndex) => {
        button.classList.toggle('is-active', buttonIndex === index);
        button.setAttribute('aria-pressed', String(buttonIndex === index));
      });
    };
    reportPages.innerHTML = reportIds.length > 1
      ? reportIds.map((_, index) => `<button type="button" aria-pressed="false">Image ${index + 1}</button>`).join('')
      : '';
    reportPages.hidden = reportIds.length < 2;
    reportPages.querySelectorAll('button').forEach((button, index) => button.addEventListener('click', () => showReportPage(index)));
    reportImage.onerror = () => {
      reportImage.hidden = true;
      reportLink.hidden = true;
      reportEmpty.hidden = false;
      reportEmpty.textContent = 'The original report card could not be loaded.';
    };
    reportImage.hidden = !hasReport;
    reportLink.hidden = !hasReport;
    reportEmpty.hidden = hasReport;
    if(hasReport){
      reportEmpty.textContent = 'No original report card upload is available for this profile.';
      showReportPage(0);
    }else{
      reportImage.removeAttribute('src');
      reportLink.removeAttribute('href');
    }
    const recommendations = document.getElementById('student-editor-recommendations');
    recommendations.innerHTML = student.recommendation?.length
      ? `<ol>${student.recommendation.map(item => `<li><strong>${escapeAdminHtml(item.course)}</strong>${item.description ? `<span>${escapeAdminHtml(item.description)}</span>` : ''}</li>`).join('')}</ol>`
      : '<p class="student-detail-empty">No course recommendations saved.</p>';
    modal.hidden = false;
  }catch(error){ alert(error.message || 'Unable to load student record.'); }
}

document.getElementById('globalSearch')?.addEventListener('input', () => resetAdminPage('students'));
document.getElementById('strandFilter')?.addEventListener('change', () => resetAdminPage('students'));
document.getElementById('studentStatusFilter')?.addEventListener('change', () => resetAdminPage('students'));
document.getElementById('close-student-editor')?.addEventListener('click', () => { document.getElementById('student-editor-modal').hidden = true; });
document.getElementById('close-subadmin-profile')?.addEventListener('click', () => { document.getElementById('subadmin-profile-modal').hidden = true; });
document.getElementById('subadmin-profile-modal')?.addEventListener('click', event => {
  if(event.target === event.currentTarget) event.currentTarget.hidden = true;
});
document.getElementById('reportSearch')?.addEventListener('input', () => resetAdminPage('reports'));
document.getElementById('reportStatusFilter')?.addEventListener('change', () => resetAdminPage('reports'));
document.getElementById('recommendationSearch')?.addEventListener('input', () => resetAdminPage('recommendations'));
document.getElementById('refreshRecommendationHistory')?.addEventListener('click', loadRecommendationManagement);
document.getElementById('close-recommendation-detail')?.addEventListener('click', () => { document.getElementById('recommendation-detail-modal').hidden = true; });
document.getElementById('recommendation-detail-modal')?.addEventListener('click', event => {
  if(event.target === event.currentTarget) event.currentTarget.hidden = true;
});
document.getElementById('close-report-review')?.addEventListener('click', () => { document.getElementById('report-review-modal').hidden = true; });
document.getElementById('report-review-modal')?.addEventListener('click', event => {
  if(event.target === event.currentTarget) event.currentTarget.hidden = true;
});
document.getElementById('report-review-grade-body')?.addEventListener('click', event => {
  const removeButton = event.target.closest('[data-remove-report-row]');
  if(removeButton) removeButton.closest('tr')?.remove();
});
document.getElementById('add-report-grade')?.addEventListener('click', () => {
  const body = document.getElementById('report-review-grade-body');
  body.insertAdjacentHTML('beforeend', '<tr><td><input data-report-subject type="text" aria-label="Subject name"></td><td><input data-report-grade type="number" min="0" max="100" step="0.01" aria-label="Subject grade"></td><td><button class="editor-remove-row" type="button" data-remove-report-row aria-label="Remove subject row"><i class="fa-solid fa-xmark" aria-hidden="true"></i></button></td></tr>');
  body.querySelector('tr:last-child [data-report-subject]')?.focus();
});
document.getElementById('save-report-grades')?.addEventListener('click', async () => {
  if(!activeReport) return;
  const subjects = [...document.querySelectorAll('#report-review-grade-body tr')].map(row => ({
    subject_name: row.querySelector('[data-report-subject]')?.value.trim() || '',
    grade: row.querySelector('[data-report-grade]')?.value.trim() || '',
  })).filter(row => row.subject_name || row.grade);
  setReportActionStatus('Saving corrected grades...');
  try{
    const result = await fetchJsonOrThrow(`/admin/report_cards/${encodeURIComponent(activeReport.id)}/edit`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({subjects}),
    });
    await refreshReportCards();
    setReportActionStatus(result.message || 'Grades saved.');
  }catch(error){
    setReportActionStatus(error.message || 'Unable to save grades.', true);
  }
});
document.getElementById('flag-report-card')?.addEventListener('click', async () => {
  if(!activeReport) return;
  const isFlagged = !activeReport.is_flagged;
  setReportActionStatus(isFlagged ? 'Flagging report...' : 'Clearing flag...');
  try{
    await fetchJsonOrThrow(`/admin/report_cards/${encodeURIComponent(activeReport.id)}/flag`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({is_flagged: isFlagged, note: document.getElementById('report-flag-note').value}),
    });
    await refreshReportCards();
    setReportActionStatus(isFlagged ? 'Report flagged.' : 'Flag cleared.');
  }catch(error){
    setReportActionStatus(error.message || 'Unable to update report flag.', true);
  }
});
document.getElementById('reprocess-report-card')?.addEventListener('click', async () => {
  if(!activeReport || !confirm('Re-run Docling OCR? This replaces the current extracted values; verified student grades remain unchanged until you save the new results.')) return;
  setReportActionStatus('Re-processing with Docling...');
  try{
    const result = await fetchJsonOrThrow(`/admin/report_cards/${encodeURIComponent(activeReport.id)}/reprocess`, {method:'POST'});
    await refreshReportCards();
    setReportActionStatus(result.message || 'Report card reprocessed.');
  }catch(error){
    setReportActionStatus(error.message || 'Unable to reprocess report card.', true);
  }
});
document.getElementById('delete-report-card')?.addEventListener('click', async () => {
  if(!activeReport || !confirm('Permanently delete this report-card image and its OCR record? Student grades already saved to the profile will remain.')) return;
  setReportActionStatus('Deleting report card...');
  try{
    await fetchJsonOrThrow(`/admin/report_cards/${encodeURIComponent(activeReport.id)}`, {method:'DELETE'});
    activeReport = null;
    document.getElementById('report-review-modal').hidden = true;
    await loadReportCards();
  }catch(error){
    setReportActionStatus(error.message || 'Unable to delete report card.', true);
  }
});
document.getElementById('remove-subadmin-account')?.addEventListener('click', async event => {
  const button = event.currentTarget;
  const user = adminSubadmins.find(item => String(item.id) === button.dataset.userId);
  if(!user || !confirm(`Permanently remove ${user.name || user.username || 'this subadmin'} and its linked data?`)) return;
  const status = document.getElementById('subadmin-profile-action-status');
  status.textContent = 'Removing account...';
  try{
    await fetchJsonOrThrow('/admin/delete_user', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id: user.id})
    });
    document.getElementById('subadmin-profile-modal').hidden = true;
    await loadSubadminProfiles();
  }catch(error){
    status.textContent = error.message || 'Unable to remove subadmin account.';
  }
});
document.getElementById('student-editor-form')?.addEventListener('submit', async event => {
  event.preventDefault();
  if(!canManageStudents) return;
  const form = event.currentTarget;
  const status = document.getElementById('student-editor-status');
  status.textContent = 'Saving...';
  try{
    const data = Object.fromEntries(new FormData(form).entries());
    const nameParts = String(data.student_name || '').trim().split(/\s+/).filter(Boolean);
    data.first_name = nameParts.shift() || '';
    data.last_name = nameParts.pop() || '';
    data.middle_initial = nameParts.length === 1 && /^[A-Za-z]{1,2}\.$/.test(nameParts[0]) ? nameParts[0] : '';
    if(data.middle_initial) nameParts.pop();
    if(nameParts.length) data.first_name = [data.first_name, ...nameParts].join(' ');
    const gradeRows = [...document.querySelectorAll('#student-editor-grade-body tr')].map(row => ({
      subject: row.querySelector('[data-editor-subject]')?.value.trim() || '',
      grade: row.querySelector('[data-editor-grade]')?.value.trim() || ''
    })).filter(row => row.subject || row.grade);
    data.subjects = gradeRows.filter(row => row.subject && row.grade).map(row => `${row.subject} - ${row.grade}`).join('\n');
    data.grades = gradeRows.map(row => row.grade).filter(Boolean).join(', ');
    const result = await fetchJsonOrThrow('/admin/update_student_profile', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    status.textContent = result.message || 'Saved.';
    document.getElementById('student-editor-modal').hidden = true;
    const refreshed = await fetchStudents();
    adminStudents = refreshed;
    updateStudentFilterOptions();
    renderAdminStudents();
  }catch(error){ status.textContent = error.message || 'Unable to save changes.'; }
});
document.getElementById('edit-student-record')?.addEventListener('click', () => {
  const form = document.getElementById('student-editor-form');
  form.querySelectorAll('input:not([name="id"]):not([name="user_id"]):not([name="email"]), textarea, [data-remove-editor-row]').forEach(field => { field.disabled = false; });
  form.querySelector('button[type="submit"]').disabled = false;
  document.getElementById('edit-student-record').disabled = true;
});
document.getElementById('deactivate-student-account')?.addEventListener('click', async event => {
  const form = document.getElementById('student-editor-form');
  const button = event.currentTarget;
  const userId = form.elements.user_id.value;
  const currentlyActive = button.dataset.active === 'true';
  const action = currentlyActive ? 'deactivate' : 'reactivate';
  if(!userId || !confirm(`Are you sure you want to ${action} this student account?`)) return;
  try{
    const result = await fetchJsonOrThrow('/admin/deactivate_student', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({id: userId, is_active: !currentlyActive})
    });
    document.getElementById('student-editor-status').textContent = result.message;
    adminStudents = await fetchStudents();
    updateStudentFilterOptions();
    renderAdminStudents();
    await openStudentEditor(userId);
  }catch(error){
    document.getElementById('student-editor-status').textContent = error.message || 'Could not update account status.';
  }
});
document.getElementById('delete-student-profile')?.addEventListener('click', async () => {
  const id = document.querySelector('#student-editor-form [name="id"]')?.value;
  if(!id || !confirm('Remove this student profile and all saved grades?')) return;
  try{
    const response = await fetch('/api/v1/admin/delete_student', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})});
    const result = await response.json();
    if(!result.success) throw new Error(result.message || 'Could not remove profile.');
    document.getElementById('student-editor-modal').hidden = true;
    adminStudents = await fetchStudents();
    updateStudentFilterOptions();
    renderAdminStudents();
  }catch(error){ document.getElementById('student-editor-status').textContent = error.message || 'Could not remove profile.'; }
});
document.getElementById('student-editor-grade-body')?.addEventListener('click', event => {
  const removeButton = event.target.closest('[data-remove-editor-row]');
  if(removeButton && !removeButton.disabled) removeButton.closest('tr')?.remove();
});

function showAdminView(view){
  document.querySelectorAll('.admin-dashboard-content').forEach(section => {
    section.style.display = view === 'dashboard' ? '' : 'none';
  });
  document.querySelectorAll('[data-admin-view]').forEach(link => {
    if(link.dataset.adminView === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.querySelectorAll('.admin-section-card, .admin-students-section').forEach(section => {
    section.style.display = section.id === view ? 'block' : 'none';
  });
  history.replaceState(null, '', view === 'dashboard' ? '#dashboard' : `#${view}`);
  if(view !== 'dashboard') document.getElementById(view)?.scrollIntoView({block:'start'});
}

function renderAdminGeneratedReport(report){
  const output = document.getElementById('admin-generated-report');
  output.replaceChildren();
  const header = document.createElement('header');
  header.className = 'admin-generated-report-header';
  const brandRow = document.createElement('div');
  brandRow.className = 'admin-generated-report-brand';
  const logo = document.createElement('img');
  logo.src = '/static/images/PathFinder2.png';
  logo.alt = '';
  logo.width = 38;
  logo.height = 38;
  const brand = document.createElement('p');
  brand.textContent = 'PathFinder';
  brandRow.append(logo, brand);
  const title = document.createElement('h2');
  title.textContent = report.title;
  const generated = document.createElement('p');
  generated.textContent = `Generated ${new Date(report.generated_at).toLocaleString()}`;
  header.append(brandRow, title, generated);
  output.append(header);

  const filterLine = document.createElement('p');
  filterLine.className = 'admin-generated-report-filters';
  filterLine.textContent = `Strand: ${report.filters.strand} | Dates: ${report.filters.from_date} to ${report.filters.to_date}`;
  output.append(filterLine);
  (report.sections || [report]).forEach(section => {
    const container = document.createElement('section');
    container.className = 'admin-generated-report-section';
    if(report.sections){
      const heading = document.createElement('h3');
      heading.textContent = section.title;
      container.append(heading);
    }
    renderAdminReportBody(section, container);
    output.append(container);
  });
}

function renderAdminReportBody(report, output){
  if(report.summary.length){
    const summary = document.createElement('dl');
    summary.className = 'admin-generated-report-summary';
    report.summary.forEach(item => {
      const group = document.createElement('div');
      const label = document.createElement('dt');
      label.textContent = item.label;
      const value = document.createElement('dd');
      value.textContent = String(item.value);
      group.append(label, value);
      summary.append(group);
    });
    output.append(summary);
  }

  const tableWrap = document.createElement('div');
  tableWrap.className = 'admin-generated-report-table-wrap';
  const table = document.createElement('table');
  table.className = 'admin-generated-report-table';
  const tableHead = document.createElement('thead');
  const headingRow = document.createElement('tr');
  report.columns.forEach(column => {
    const cell = document.createElement('th');
    cell.scope = 'col';
    cell.textContent = column.label;
    headingRow.append(cell);
  });
  tableHead.append(headingRow);
  const tableBody = document.createElement('tbody');
  if(report.rows.length){
    report.rows.forEach(row => {
      const rowElement = document.createElement('tr');
      report.columns.forEach(column => {
        const cell = document.createElement('td');
        cell.textContent = String(row[column.key] ?? '');
        rowElement.append(cell);
      });
      tableBody.append(rowElement);
    });
  }else{
    const emptyRow = document.createElement('tr');
    const emptyCell = document.createElement('td');
    emptyCell.colSpan = report.columns.length;
    emptyCell.textContent = 'No records match the selected filters.';
    emptyRow.append(emptyCell);
    tableBody.append(emptyRow);
  }
  table.append(tableHead, tableBody);
  tableWrap.append(table);
  output.append(tableWrap);
}

const adminReportForm = document.getElementById('admin-report-form');
const adminReportType = adminReportForm?.elements.report_type;
const adminReportDateFilters = document.querySelector('.admin-report-date-filters');
let generatedAdminReportPayload = null;
adminReportType?.addEventListener('change', () => {
  const showDateFilters = ['report_cards', 'recommendations'].includes(adminReportType.value);
  adminReportDateFilters.hidden = !showDateFilters;
  ['from_date', 'to_date'].forEach(name => {
    adminReportForm.elements[name].disabled = !showDateFilters;
    if(!showDateFilters) adminReportForm.elements[name].value = '';
  });
});
adminReportForm?.addEventListener('submit', async event => {
  event.preventDefault();
  const submitButton = adminReportForm.querySelector('button[type="submit"]');
  const printButton = document.getElementById('print-admin-report');
  const downloadButton = document.getElementById('download-admin-report');
  const status = document.getElementById('admin-report-status');
  const output = document.getElementById('admin-generated-report');
  submitButton.disabled = true;
  printButton.disabled = true;
  downloadButton.disabled = true;
  generatedAdminReportPayload = null;
  status.hidden = false;
  status.textContent = 'Generating report...';
  try{
    const formData = new FormData(adminReportForm);
    const payload = JSON.stringify(Object.fromEntries(formData.entries()));
    const result = await fetchJsonOrThrow('/admin/reports/generate', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: payload
    });
    renderAdminGeneratedReport(result.report);
    generatedAdminReportPayload = payload;
    printButton.disabled = false;
    downloadButton.disabled = false;
    status.hidden = true;
  }catch(error){
    output.replaceChildren();
    const message = document.createElement('p');
    message.className = 'records-empty';
    message.textContent = error.message || 'Unable to generate report.';
    output.append(message);
    status.textContent = 'Report generation failed.';
  }finally{
    submitButton.disabled = false;
  }
});
document.getElementById('print-admin-report')?.addEventListener('click', () => window.print());
document.getElementById('download-admin-report')?.addEventListener('click', async event => {
  if(!generatedAdminReportPayload) return;
  const button = event.currentTarget;
  const status = document.getElementById('admin-report-status');
  button.disabled = true;
  status.hidden = false;
  status.textContent = 'Preparing PDF...';
  try{
    const response = await fetch('/api/v1/admin/reports/download', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: generatedAdminReportPayload
    });
    if(!response.ok){
      const error = await response.json().catch(() => ({}));
      throw new Error(error.message || error.error || 'Unable to download report.');
    }
    if(!response.headers.get('content-type')?.includes('application/pdf')) throw new Error('PDF download is unavailable. Please sign in again.');
    const filename = response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1] || 'pathfinder-report.pdf';
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    status.textContent = 'PDF downloaded.';
  }catch(error){
    status.textContent = error.message || 'Unable to download report.';
  }finally{
    button.disabled = false;
  }
});

document.querySelectorAll('[data-admin-view]').forEach(link => {
  link.addEventListener('click', event => {
    event.preventDefault();
    showAdminView(link.dataset.adminView);
    if(link.dataset.adminView === 'view-subadmins') loadSubadminProfiles();
    if(link.dataset.adminView === 'manage-recommendations') loadRecommendationManagement();
    if(link.dataset.adminView === 'manage-reports') loadReportCards();
    if(link.dataset.adminView === 'manage-admin-accounts') loadAdminAccounts();
    if(link.dataset.adminView === 'admin-activity') loadAdminActivity();
    if(link.dataset.adminView === 'system-settings') loadSystemSettings();
  });
});

document.getElementById('refresh-admin-accounts')?.addEventListener('click', loadAdminAccounts);
document.getElementById('refresh-admin-activity')?.addEventListener('click', loadAdminActivity);

const availableAdminViews = ['dashboard'];
if(canViewAdminData) availableAdminViews.push('view-students', 'manage-reports', 'manage-recommendations', 'admin-reports');
if(canManageStudents) availableAdminViews.push('add-users', 'view-subadmins', 'manage-admin-accounts', 'admin-activity', 'system-settings');
const initialAdminView = window.location.hash.slice(1) || document.body.dataset.defaultAdminView || 'dashboard';
const selectedAdminView = availableAdminViews.includes(initialAdminView) ? initialAdminView : 'dashboard';
showAdminView(selectedAdminView);
if(selectedAdminView === 'view-subadmins') loadSubadminProfiles();
if(selectedAdminView === 'manage-admin-accounts') loadAdminAccounts();
if(selectedAdminView === 'admin-activity') loadAdminActivity();
if(selectedAdminView === 'system-settings') loadSystemSettings();
if(selectedAdminView === 'manage-reports') loadReportCards();
if(selectedAdminView === 'manage-recommendations') loadRecommendationManagement();

async function loadDashboard(){
  try {
    const stats = await fetchStats();
    const allRecList = stats.all_recommended_courses || [];

    const formatCount = value => new Intl.NumberFormat().format(Number(value || 0));
    document.getElementById('statTotalStudents').innerText = formatCount(stats.total_students);
    document.getElementById('statTotalProfiles').innerText = formatCount(stats.total_profiles);
    document.getElementById('statCourseRecommendations').innerText = formatCount(stats.total_course_recommendations);
    document.getElementById('statStudentsWithReports').innerText = formatCount(stats.students_who_uploaded_reports);
    document.getElementById('statAverageGwa').innerText = stats.avg_gwa == null ? '—' : Number(stats.avg_gwa).toFixed(2);
    document.getElementById('statMostRecommendedCourse').innerText = stats.most_recommended_course || '—';
    const mostRecommendedCount = Number(stats.most_recommended_course_count || 0);
    document.getElementById('statMostRecommendedCount').innerText = mostRecommendedCount
      ? `${formatCount(mostRecommendedCount)} ${mostRecommendedCount === 1 ? 'recommendation' : 'recommendations'}`
      : 'no recommendations yet';
    document.getElementById('statCommonStrand').innerText = stats.most_common_strand || '—';
    const commonStrandCount = Number(stats.most_common_strand_count || 0);
    document.getElementById('statCommonStrandCount').innerText = commonStrandCount
      ? `${formatCount(commonStrandCount)} ${commonStrandCount === 1 ? 'profile' : 'profiles'}`
      : 'no profiles yet';
    document.getElementById('statActiveUsers').innerText = formatCount(stats.active_users);

    const gwaBuckets = stats.gwa_buckets || {};
    const gLabels = Object.keys(gwaBuckets);
    const gValues = gLabels.map(k => gwaBuckets[k] || 0);
    renderGwa(document.getElementById('chartGwa'), gLabels, gValues);

    renderTopRecommended(document.getElementById('topRecommended'), stats.top_recommended || [], stats.all_recommended_courses || []);
    renderAllRecommended(document.getElementById('allRecommended'), stats.all_recommended_courses || []);

    const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
    const uploads = months.map(m => stats.monthly_uploads?.[m] || 0);
    renderLineChart(document.getElementById('chartMonthly'), months, uploads);

    const strandCounts = stats.strand_counts || {};
    const strandLabels = Object.keys(strandCounts);
    const strandValues = strandLabels.map(k => strandCounts[k]);
    renderStrandPie(document.getElementById('chartStrand'), strandLabels, strandValues);

    adminStudents = await fetchStudents();
    updateStudentFilterOptions();
    renderAdminStudents();

    document.getElementById('dashboardError').style.display = 'none';
  } catch (err) {
    const errorNode = document.getElementById('dashboardError');
    if (errorNode) {
      errorNode.innerText = `Dashboard error: ${err.message}`;
      errorNode.style.display = 'block';
    }

    const studentsWrap = document.getElementById('studentsWrap');
    const topRecommended = document.getElementById('topRecommended');
    if (studentsWrap) {
      studentsWrap.innerHTML = '<div style="color:#b91c1c;text-align:center;background:aliceblue;">Unable to load student dashboard data.</div>';
    }
    if (topRecommended) {
      topRecommended.innerHTML = '<div style="color:#b91c1c;text-align:center;background:aliceblue;">Unable to load recommendations.</div>';
    }
  }
}

if(mustChangePassword){
  firstPasswordDialog.showModal();
  firstPasswordDialog.querySelector('[name="current_password"]').focus();
}else{
  loadDashboard();
}
