const statusBadge = document.getElementById('status');
const resultBox = document.getElementById('result');
const placeholderList = document.getElementById('placeholder_list');
const libraryList = document.getElementById('library_list');

const state = {
  templateFile: 'demo.json',
  templateId: 'demo',
  templateText: 'divide(<x/>, add(1, <y/>))',
  fillRules: {},
  metadata: { priority: 100, tags: ['demo'], settings_override: {} },
  placeholders: [],
  datafields: [],
  libraryItems: [],
  settingsOptions: null,
  fileLists: {},
  fileDefaults: {},
  folderLists: {},
  fileFolders: {},
  activeFileKind: 'template',
  placeholderCollapse: {},
  submitUploadName: '',
  submitUploadPath: '',
};

const ui = {
  fileTemplate: document.getElementById('file_template'),
  fileFactors: document.getElementById('file_factors'),
  fileState: document.getElementById('file_state'),
  fileTemplateLibrary: document.getElementById('file_template_library'),
  fileFactorLibrary: document.getElementById('file_factor_library'),
  fileSettings: document.getElementById('file_settings'),
  fileDatafields: document.getElementById('file_datafields'),
  fileOperators: document.getElementById('file_operators'),
  fileKind: document.getElementById('file_kind'),
  fileKindTabs: document.getElementById('file_kind_tabs'),
  fileList: document.getElementById('file_list'),
  fileKindLabel: document.getElementById('file_kind_label'),
  fileFolderLabel: document.getElementById('file_folder_label'),
  fileFolderList: document.getElementById('file_folder_list'),
  fileSummary: document.getElementById('file_summary'),
  autoSave: document.getElementById('auto_save'),
  activeTemplateLabel: document.getElementById('active_template_label'),
  activeLibraryLabel: document.getElementById('active_library_label'),
  activeDatafieldsLabel: document.getElementById('active_datafields_label'),
  cacheTemplateLabel: document.getElementById('cache_template_label'),
  cacheDatafieldsLabel: document.getElementById('cache_datafields_label'),
  expandTemplateLabel: document.getElementById('expand_template_label'),
  templateId: document.getElementById('template_id'),
  templateText: document.getElementById('template_text'),
  datafieldsTarget: document.getElementById('datafields_target'),
  cacheOut: document.getElementById('cache_out'),
  cacheLimit: document.getElementById('cache_limit'),
  cacheRules: document.getElementById('cache_rules'),
  expandOut: document.getElementById('expand_out'),
  expandMax: document.getElementById('expand_max'),
  expandRegion: document.getElementById('expand_region'),
  expandUniverse: document.getElementById('expand_universe'),
  expandDelay: document.getElementById('expand_delay'),
  expandSettings: document.getElementById('expand_settings'),
  submitFile: document.getElementById('submit_file'),
  submitMaxWait: document.getElementById('submit_max_wait'),
  submitConcurrency: document.getElementById('submit_concurrency'),
  submitOrdered: document.getElementById('submit_ordered'),
  submitRetryFailed: document.getElementById('submit_retry_failed'),
  submitUploadInput: document.getElementById('submit_upload_input'),
  submitSourceLabel: document.getElementById('submit_source_label'),
  libDb: document.getElementById('lib_db'),
  libDbAdv: document.getElementById('lib_db_adv'),
  libFile: document.getElementById('lib_file'),
  metaOps: document.getElementById('meta_ops'),
  metaSettings: document.getElementById('meta_settings'),
  summaryPlaceholders: document.getElementById('summary_placeholders'),
  summaryFields: document.getElementById('summary_fields'),
  summaryDatafields: document.getElementById('summary_datafields'),
  summaryTemplate: document.getElementById('summary_template'),
  dreamGenerationCount: document.getElementById('dream_generation_count'),
  dreamIntervalSec: document.getElementById('dream_interval_sec'),
  dreamMaxWaitSec: document.getElementById('dream_max_wait_sec'),
  dreamErrorCooldownSec: document.getElementById('dream_error_cooldown_sec'),
  dreamAuthRefreshSec: document.getElementById('dream_auth_refresh_sec'),
  dreamOperatorRefreshSec: document.getElementById('dream_operator_refresh_sec'),
  dreamBaselineAlphaId: document.getElementById('dream_baseline_alpha_id'),
  dreamForceStage: document.getElementById('dream_force_stage'),
  dreamOperatorsFile: document.getElementById('dream_operators_file'),
  dreamResultsFile: document.getElementById('dream_results_file'),
  dreamSharpeAbsThreshold: document.getElementById('dream_sharpe_abs_threshold'),
  dreamFitnessThreshold: document.getElementById('dream_fitness_threshold'),
  dreamTemplateSharpeThreshold: document.getElementById('dream_template_sharpe_threshold'),
  dreamMaxSeedInPrompt: document.getElementById('dream_max_seed_in_prompt'),
  dreamSeedFile: document.getElementById('dream_seed_file'),
  dreamCursorFile: document.getElementById('dream_cursor_file'),
  dreamHighTemplateFile: document.getElementById('dream_high_template_file'),
  dreamNotifyUrl: document.getElementById('dream_notify_url'),
  dreamPgSeedDsn: document.getElementById('dream_pg_seed_dsn'),
  dreamPgSeedTable: document.getElementById('dream_pg_seed_table'),
  dreamPgSeedMinFitness: document.getElementById('dream_pg_seed_min_fitness'),
  dreamPgSeedTurnoverRange: document.getElementById('dream_pg_seed_turnover_range'),
  dreamSeedBootstrap: document.getElementById('dream_seed_bootstrap'),
  dreamStatusLabel: document.getElementById('dream_status_label'),
};

const FILE_KIND_PREFIX = {
  template: 'templates/',
  factors: 'generated/',
  state: 'runs/',
  template_library: 'templates/',
  factor_library: 'db/',
  settings: 'metadata/',
  datafields: 'metadata/',
  operators: 'metadata/',
};

const FILE_KIND_LABELS = {
  template: '模板',
  factors: '因子',
  state: '状态',
  template_library: '模板库',
  factor_library: '因子库',
  settings: '设置',
  datafields: '字段',
  operators: 'Operators',
};

const fileSelectsByKind = {
  template: [ui.fileTemplate, ui.cacheOut],
  factors: [ui.fileFactors, ui.expandOut, ui.submitFile, ui.libFile],
  state: [ui.fileState],
  template_library: [ui.fileTemplateLibrary],
  factor_library: [ui.fileFactorLibrary, ui.libDb, ui.libDbAdv],
  settings: [ui.fileSettings, ui.metaSettings],
  datafields: [ui.fileDatafields],
  operators: [ui.fileOperators, ui.metaOps],
};

const primarySelectByKind = {
  template: ui.fileTemplate,
  factors: ui.fileFactors,
  state: ui.fileState,
  template_library: ui.fileTemplateLibrary,
  factor_library: ui.fileFactorLibrary,
  settings: ui.fileSettings,
  datafields: ui.fileDatafields,
  operators: ui.fileOperators,
};

const df = {
  modal: document.getElementById('dataFieldsModal'),
  targetLabel: document.getElementById('df_target_label'),
  settingsLabel: document.getElementById('df_settings_label'),
  instrument: document.getElementById('df_instrument'),
  region: document.getElementById('df_region'),
  delay: document.getElementById('df_delay'),
  universe: document.getElementById('df_universe'),
  limit: document.getElementById('df_limit'),
  outLabel: document.getElementById('df_out_label'),
  status: document.getElementById('df_status'),
  useCache: document.getElementById('df_use_cache'),
  loading: document.getElementById('dataFieldsLoading'),
  datasetPanel: document.getElementById('df_dataset_panel'),
  datasetSearch: document.getElementById('df_dataset_search'),
  datasetSelected: document.getElementById('df_dataset_selected'),
  datasetCount: document.getElementById('df_dataset_count'),
  content: document.getElementById('dataFieldsContent'),
  tableBody: document.getElementById('dataFieldsTableBody'),
  selectedList: document.getElementById('selectedDataFields'),
  count: document.getElementById('dataFieldsCount'),
  filtered: document.getElementById('filteredCount'),
  selectedCount: document.getElementById('selectedCount'),
  typeFilter: document.getElementById('typeFilter'),
  filterHighCoverage: document.getElementById('filterHighCoverage'),
  filterPopular: document.getElementById('filterPopular'),
  filterMatrixOnly: document.getElementById('filterMatrixOnly'),
  selectAllBtn: document.getElementById('selectAllFiltered'),
  clearAllBtn: document.getElementById('clearAllSelected'),
  selectAllCheckbox: document.getElementById('selectAllCheckbox'),
};

let dataFieldsModalMode = 'template'; // 'template' or 'ai'

const ai = {
  reportText: document.getElementById('ai_report_text'),
  genCount: document.getElementById('ai_gen_count'),
  includePatterns: document.getElementById('ai_include_patterns'),
  loading: document.getElementById('ai_loading'),
  results: document.getElementById('ai_results'),
  selectedFields: document.getElementById('ai_selected_fields'),
  selectedCount: document.getElementById('ai_selected_count'),
};

let aiSelectedFields = [];

let currentDataFields = [];
let filteredDataFields = [];
let selectedDataFields = new Set();
let sortColumn = '';
let sortOrder = 'asc';
let autoSaveTimer = null;
let dreamStatusPollTimer = null;
let currentDatasets = [];
let selectedDatasets = new Set();
const columnFilters = {
  id: '',
  description: '',
  type: '',
  coverage: { min: null, max: null },
  userCount: null,
  alphaCount: null,
};

const MAX_PLACEHOLDER_CHIPS = 6;

const DATAFIELDS_CACHE_KEY = 'aom_datafields_cache_v1';
const datafieldsCache = new Map();
const DATASET_SELECTION_KEY = 'aom_dataset_selection_v1';
const datasetSelectionCache = new Map();
const DATASET_LIST_CACHE_KEY = 'aom_dataset_list_cache_v1';
const datasetListCache = new Map();

const loadDatafieldsCache = () => {
  try {
    const raw = localStorage.getItem(DATAFIELDS_CACHE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      Object.keys(parsed).forEach((key) => {
        if (Array.isArray(parsed[key])) {
          datafieldsCache.set(key, parsed[key]);
        }
      });
    }
  } catch (err) {
    // ignore cache errors
  }
};

const saveDatafieldsCache = () => {
  try {
    const obj = {};
    datafieldsCache.forEach((value, key) => {
      obj[key] = value;
    });
    localStorage.setItem(DATAFIELDS_CACHE_KEY, JSON.stringify(obj));
  } catch (err) {
    // ignore cache errors
  }
};

const loadDatasetSelectionCache = () => {
  try {
    const raw = localStorage.getItem(DATASET_SELECTION_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      Object.keys(parsed).forEach((key) => {
        if (Array.isArray(parsed[key])) {
          datasetSelectionCache.set(key, parsed[key]);
        }
      });
    }
  } catch (err) {
    // ignore
  }
};

const saveDatasetSelectionCache = () => {
  try {
    const obj = {};
    datasetSelectionCache.forEach((value, key) => {
      obj[key] = value;
    });
    localStorage.setItem(DATASET_SELECTION_KEY, JSON.stringify(obj));
  } catch (err) {
    // ignore
  }
};

const loadDatasetListCache = () => {
  try {
    const raw = localStorage.getItem(DATASET_LIST_CACHE_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      Object.keys(parsed).forEach((key) => {
        if (Array.isArray(parsed[key])) {
          datasetListCache.set(key, parsed[key]);
        }
      });
    }
  } catch (err) {
    // ignore
  }
};

const saveDatasetListCache = () => {
  try {
    const obj = {};
    datasetListCache.forEach((value, key) => {
      obj[key] = value;
    });
    localStorage.setItem(DATASET_LIST_CACHE_KEY, JSON.stringify(obj));
  } catch (err) {
    // ignore
  }
};

const setStatus = (text) => {
  if (statusBadge) statusBadge.textContent = text;
};

const FILE_SELECTION_KEY = 'aom_file_selection_v1';

const saveFileSelection = () => {
  const data = {
    activeFileKind: state.activeFileKind,
    selections: {},
    folders: state.fileFolders || {},
  };
  Object.keys(primarySelectByKind).forEach((kind) => {
    const select = primarySelectByKind[kind];
    if (select && select.value) {
      data.selections[kind] = select.value;
    }
  });
  try {
    localStorage.setItem(FILE_SELECTION_KEY, JSON.stringify(data));
  } catch (err) {
    // ignore
  }
};

const loadFileSelection = () => {
  try {
    const raw = localStorage.getItem(FILE_SELECTION_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (data && typeof data === 'object') {
      if (data.folders && typeof data.folders === 'object') {
        state.fileFolders = { ...state.fileFolders, ...data.folders };
      }
      if (data.selections && typeof data.selections === 'object') {
        Object.keys(data.selections).forEach((kind) => {
          const select = primarySelectByKind[kind];
          if (select && data.selections[kind]) {
            select.value = data.selections[kind];
          }
        });
      }
      if (data.activeFileKind) {
        state.activeFileKind = data.activeFileKind;
      }
    }
  } catch (err) {
    // ignore
  }
};

const setResult = (data) => {
  const sanitized = sanitizeResult(data);
  if (resultBox) resultBox.textContent = typeof sanitized === 'string' ? sanitized : JSON.stringify(sanitized, null, 2);
};

const apiPost = async (endpoint, payload) => {
  setStatus('loading');
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json().catch(() => ({}));
  setStatus(response.ok ? 'ok' : 'error');
  setResult(data);
  if (!response.ok) throw new Error(data.error || 'request failed');
  return data;
};

const extractPlaceholders = (text) => {
  const set = new Set();
  const regex = /<([A-Za-z0-9_]+)\s*\/>/g;
  let match;
  while ((match = regex.exec(text))) {
    set.add(match[1]);
  }
  return Array.from(set);
};

const normalizeFillRules = (placeholders) => {
  const next = {};
  placeholders.forEach((name) => {
    next[name] = Array.isArray(state.fillRules[name]) ? state.fillRules[name] : [];
    if (state.placeholderCollapse[name] === undefined) {
      state.placeholderCollapse[name] = true;
    }
  });
  state.fillRules = next;
};

const addPlaceholderFields = (name, value) => {
  const inputVal = (value || '').trim();
  if (!inputVal) return false;
  // 支持中英文逗号
  const fields = inputVal.split(/[，,]/).map((f) => f.trim()).filter((f) => f);
  if (fields.length > 0) {
    const current = state.fillRules[name] || [];
    const nextSet = new Set(current);
    fields.forEach((f) => nextSet.add(f));
    state.fillRules[name] = Array.from(nextSet);
    return true;
  }
  return false;
};

const renderPlaceholders = () => {
  if (!placeholderList) return;
  placeholderList.innerHTML = '';
  if (state.placeholders.length > 0) {
    const clearAllBtn = document.createElement('div');
    clearAllBtn.className = 'row';
    clearAllBtn.style.marginBottom = '12px';
    clearAllBtn.innerHTML = `<button class="btn-outline" style="color:var(--error); border-color:var(--error);" data-clear-all="true">清空所有占位符规则</button>`;
    placeholderList.appendChild(clearAllBtn);
  }

  state.placeholders.forEach((name) => {
    const values = state.fillRules[name] || [];
    const collapsed = state.placeholderCollapse[name] !== false;
    const shownValues = collapsed ? values.slice(0, MAX_PLACEHOLDER_CHIPS) : values;
    const card = document.createElement('div');
    card.className = 'placeholder-card';
    card.innerHTML = `
      <div class="row" style="justify-content: space-between;">
        <strong>&lt;${name}/&gt;</strong>
        <div class="row">
          <span class="muted">${values.length} 个字段</span>
          <button class="btn-outline" style="padding: 2px 8px; margin-left: 8px; font-size: 12px;" data-clear="${name}" type="button">清空</button>
        </div>
      </div>
      <div class="chip-row" data-name="${name}"></div>
      <div class="row" style="margin-top: 8px;">
        <input data-input="${name}" placeholder="新增字段 (支持逗号分隔)" />
        <button data-add="${name}" type="button">添加</button>
      </div>
      ${values.length > MAX_PLACEHOLDER_CHIPS ? `<div class="row" style="margin-top:6px;"><button class="btn-outline" data-toggle="${name}" type="button">${collapsed ? `展开(${values.length - MAX_PLACEHOLDER_CHIPS})` : '收起'}</button></div>` : ''}
    `;
    placeholderList.appendChild(card);

    const chipRow = card.querySelector('.chip-row');
    values.forEach((value, idx) => {
      if (collapsed && idx >= MAX_PLACEHOLDER_CHIPS) return;
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.innerHTML = `${value} <button data-remove="${name}" data-index="${idx}">×</button>`;
      chipRow.appendChild(chip);
    });
  });
  refreshTargetOptions();
  syncRulesPreview();
  updateSummary();
};

const renderLibrary = () => {
  if (!libraryList) return;
  libraryList.innerHTML = '';
  state.libraryItems.forEach((item) => {
    const key = item.template_id || item.name || 'template';
    const label = item.name || item.template_id || 'template';
    const card = document.createElement('div');
    card.className = 'placeholder-card';
    card.innerHTML = `
      <div class="row" style="justify-content: space-between;">
        <span>${label}</span>
        <div class="row">
          <button data-lib-load="${key}" type="button">加载</button>
          <button class="btn-outline" data-lib-delete="${key}" type="button">删除</button>
        </div>
      </div>
      <div class="muted" style="margin-top: 6px; font-size: 11px;">${(item.template || '').slice(0, 90)}</div>
    `;
    libraryList.appendChild(card);
  });
};

const refreshTargetOptions = () => {
  const current = ui.datafieldsTarget.value;
  ui.datafieldsTarget.innerHTML = '';
  state.placeholders.forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = `<${name}/>`;
    ui.datafieldsTarget.appendChild(option);
  });
  if (current && state.placeholders.includes(current)) {
    ui.datafieldsTarget.value = current;
  }
  if (df.targetLabel && df.modal && df.modal.style.display === 'block') {
    df.targetLabel.textContent = ui.datafieldsTarget.value || '-';
  }
};

const syncRulesPreview = () => {
  ui.cacheRules.value = JSON.stringify(state.fillRules, null, 2);
};

const fillSelectOptions = (select, values, fallback) => {
  if (!select) return;
  select.innerHTML = '';
  const list = values || fallback || [];
  list.forEach((value) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
  if (!list.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = '未加载';
    select.appendChild(option);
  }
};

const fillSelectOptionsWithBlank = (select, values, blankLabel = '不覆盖') => {
  if (!select) return;
  select.innerHTML = '';
  const blank = document.createElement('option');
  blank.value = '';
  blank.textContent = blankLabel;
  select.appendChild(blank);
  const list = values || [];
  list.forEach((value) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
};

const basename = (value) => {
  if (!value) return '';
  const parts = String(value).split(/[/\\]/);
  return parts[parts.length - 1];
};

const getSelectedName = (select, fallback) => {
  if (select && select.value) return select.value;
  return fallback || '';
};

const getKindDefault = (kind) => state.fileDefaults[kind] || '';

const buildPath = (kind, name, folder) => {
  const prefix = FILE_KIND_PREFIX[kind] || '';
  const raw = name || getKindDefault(kind);
  if (!raw) return '';
  const clean = basename(raw);
  const folderName = folder ? String(folder).trim() : '';
  if (folderName) {
    return `${prefix}${folderName}/${clean}`;
  }
  return `${prefix}${clean}`;
};

const getFolder = (kind) => state.fileFolders[kind] || '';

const getFilePath = (kind, select) => {
  const target = select || primarySelectByKind[kind];
  return buildPath(kind, getSelectedName(target, getKindDefault(kind)), getFolder(kind));
};

const formatTimestamp = (date = new Date()) => {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`;
};

const generateFileName = (prefix) => `${prefix}_${formatTimestamp()}.json`;

const ensureSelectOption = (select, name) => {
  if (!select || !name) return;
  const exists = Array.from(select.options).some((opt) => opt.value === name);
  if (!exists) {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  }
  select.value = name;
};

const populateFileSelect = (select, files, defaultName) => {
  if (!select) return;
  const current = select.value;
  const list = Array.isArray(files) && files.length ? files : (defaultName ? [defaultName] : []);
  select.innerHTML = '';
  if (!list.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = '未发现文件';
    select.appendChild(option);
    return;
  }
  list.forEach((value) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  });
  if (current && list.includes(current)) {
    select.value = current;
  } else if (defaultName && list.includes(defaultName)) {
    select.value = defaultName;
  }
};

const updateActiveLabels = () => {
  const templateName = getSelectedName(ui.fileTemplate, getKindDefault('template'));
  const libraryName = getSelectedName(ui.fileTemplateLibrary, getKindDefault('template_library'));
  const datafieldsName = getSelectedName(ui.fileDatafields, getKindDefault('datafields'));
  const settingsName = getSelectedName(ui.fileSettings, getKindDefault('settings'));

  if (ui.activeTemplateLabel) ui.activeTemplateLabel.textContent = templateName || '-';
  if (ui.cacheTemplateLabel) ui.cacheTemplateLabel.textContent = templateName || '-';
  if (ui.expandTemplateLabel) ui.expandTemplateLabel.textContent = templateName || '-';
  if (ui.activeLibraryLabel) ui.activeLibraryLabel.textContent = libraryName || '-';
  if (ui.activeDatafieldsLabel) ui.activeDatafieldsLabel.textContent = datafieldsName || '-';
  if (ui.cacheDatafieldsLabel) ui.cacheDatafieldsLabel.textContent = datafieldsName || '-';
  if (df.settingsLabel) df.settingsLabel.textContent = settingsName || '-';
  if (df.outLabel) df.outLabel.textContent = datafieldsName || '-';
  updateSummary();
  renderFileSummary();
  updateSubmitSourceLabel();
};

const updateSubmitSourceLabel = () => {
  if (!ui.submitSourceLabel) return;
  if (state.submitUploadName) {
    ui.submitSourceLabel.textContent = `来源：上传文件 ${state.submitUploadName}`;
  } else {
    const name = getSelectedName(ui.submitFile, getKindDefault('factors')) || '-';
    ui.submitSourceLabel.textContent = `来源：选择文件 ${name}`;
  }
};

const parseDreamSeedBootstrap = () => {
  const raw = ui.dreamSeedBootstrap ? ui.dreamSeedBootstrap.value : '';
  if (!raw) return [];
  const list = raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line);
  return Array.from(new Set(list));
};

const parseDreamTurnoverRange = () => {
  const raw = ui.dreamPgSeedTurnoverRange ? String(ui.dreamPgSeedTurnoverRange.value || '').trim() : '';
  if (!raw) return { min: 5, max: 200 };
  const parts = raw.split(',').map((x) => Number(String(x).trim())).filter((x) => Number.isFinite(x));
  if (parts.length < 2) return { min: 5, max: 200 };
  const min = Math.min(parts[0], parts[1]);
  const max = Math.max(parts[0], parts[1]);
  return { min, max };
};

const renderDreamStatus = (payload) => {
  if (!ui.dreamStatusLabel) return;
  const data = payload && payload.data ? payload.data : payload;
  if (!data || typeof data !== 'object') {
    ui.dreamStatusLabel.textContent = '状态：未知';
    return;
  }
  const running = !!data.running;
  const stopping = !!data.stopping;
  const stats = data.stats || {};
  const cycles = Number(stats.cycles || 0);
  const simulated = Number(stats.simulated || 0);
  const accepted = Number(stats.accepted || 0);
  const highs = Number(stats.high_templates || 0);
  const errors = Number(stats.errors || 0);
  const pgSaved = Number(stats.pg_seed_saved || 0);
  const pgErrors = Number(stats.pg_seed_errors || 0);
  const statusText = stopping ? '停止中' : (running ? '运行中' : '已停止');
  const lastError = data.last_error ? String(data.last_error).replace(/\s+/g, ' ').slice(0, 120) : '';
  const optimizer = data.optimizer && typeof data.optimizer === 'object' ? data.optimizer : {};
  const stage = optimizer.stage ? String(optimizer.stage) : '-';
  const shortflipQueueSize = Number(optimizer.shortflip_queue_size || 0);
  ui.dreamStatusLabel.textContent = `状态：${statusText} | stage=${stage} cycles=${cycles} simulated=${simulated} accepted=${accepted} high=${highs} errors=${errors} shortflip=${shortflipQueueSize} pg_saved=${pgSaved} pg_err=${pgErrors}${lastError ? ` | last=${lastError}` : ''}`;
};

const stopDreamStatusPolling = () => {
  if (dreamStatusPollTimer) {
    clearInterval(dreamStatusPollTimer);
    dreamStatusPollTimer = null;
  }
};

const startDreamStatusPolling = () => {
  stopDreamStatusPolling();
  dreamStatusPollTimer = setInterval(() => {
    actions.dreamStatus().catch(() => {});
  }, 15000);
};

const setActiveFileKind = (kind) => {
  if (!kind) return;
  state.activeFileKind = kind;
  if (ui.fileKind) ui.fileKind.value = kind;
  if (ui.fileKindLabel) ui.fileKindLabel.textContent = FILE_KIND_LABELS[kind] || kind;
  if (ui.fileFolderLabel) ui.fileFolderLabel.textContent = state.fileFolders[kind] || '根';
  if (ui.fileKindTabs) {
    ui.fileKindTabs.querySelectorAll('.file-kind').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.kind === kind);
    });
  }
  renderFileManager();
  saveFileSelection();
};

const setActiveFileForKind = (kind, name) => {
  const select = primarySelectByKind[kind];
  if (select && name) {
    select.value = name;
  }
  if (kind === 'settings') {
    state.settingsOptions = null;
  }
  if (kind === 'template_library') {
    actions.libraryRefresh().catch(() => {});
  }
  if (kind === 'template') {
    actions.loadTemplate().catch(() => {});
  }
  updateActiveLabels();
  renderFileManager();
  saveFileSelection();
};

const renderFileSummary = () => {
  if (!ui.fileSummary) return;
  ui.fileSummary.innerHTML = '';
  Object.keys(primarySelectByKind).forEach((kind) => {
    const name = getSelectedName(primarySelectByKind[kind], getKindDefault(kind));
    if (!name) return;
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = `${FILE_KIND_LABELS[kind] || kind}: ${name}`;
    ui.fileSummary.appendChild(chip);
  });
};

const renderFileManager = () => {
  if (!ui.fileList) return;
  const kind = state.activeFileKind || 'template';
  const files = state.fileLists[kind] || [];
  const folders = state.folderLists[kind] || [];
  const selected = getSelectedName(primarySelectByKind[kind], getKindDefault(kind));
  if (ui.fileFolderLabel) {
    ui.fileFolderLabel.textContent = state.fileFolders[kind] || '根';
  }
  if (ui.fileFolderList) {
    ui.fileFolderList.innerHTML = '';
    folders.forEach((folder) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip file-folder';
      chip.textContent = folder;
      chip.dataset.folderName = folder;
      ui.fileFolderList.appendChild(chip);
    });
  }
  ui.fileList.innerHTML = '';
  if (!files.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = '暂无文件';
    ui.fileList.appendChild(empty);
    return;
  }
  files.forEach((name) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `file-item${name === selected ? ' active' : ''}`;
    btn.textContent = name;
    btn.dataset.fileName = name;
    btn.dataset.fileKind = kind;
    ui.fileList.appendChild(btn);
  });
};

const refreshFileList = async (kind) => {
  let data;
  try {
    data = await apiPost('/api/files/list', { kind, folder: getFolder(kind) });
  } catch (err) {
    if (getFolder(kind)) {
      state.fileFolders[kind] = '';
      data = await apiPost('/api/files/list', { kind, folder: '' });
    } else {
      throw err;
    }
  }
  const files = data.data && Array.isArray(data.data.files) ? data.data.files : [];
  const dirs = data.data && Array.isArray(data.data.dirs) ? data.data.dirs : [];
  const defName = data.data && data.data.default ? String(data.data.default) : '';
  state.fileLists[kind] = files;
  state.folderLists[kind] = dirs;
  if (defName) state.fileDefaults[kind] = defName;
  const selects = fileSelectsByKind[kind] || [];
  selects.forEach((select) => populateFileSelect(select, files, defName));
  updateActiveLabels();
  renderFileManager();
  return data;
};

const refreshAllFiles = async () => {
  const kinds = Object.keys(fileSelectsByKind);
  for (const kind of kinds) {
    await refreshFileList(kind);
  }
};

const sanitizeResult = (value, key) => {
  const pathKeys = new Set(['path', 'file', 'state', 'db', 'out', 'template', 'datafields', 'settings', 'operators']);
  const isPathLike = (val) => {
    if (typeof val !== 'string') return false;
    if (val.includes(' ')) return false;
    return /^[A-Za-z]:[\\/]/.test(val) || val.startsWith('/');
  };
  if (typeof value === 'string') {
    if (pathKeys.has(String(key || '')) && (value.includes('/') || value.includes('\\'))) {
      return basename(value);
    }
    if (!key && isPathLike(value)) {
      return basename(value);
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeResult(item, key));
  }
  if (value && typeof value === 'object') {
    const out = {};
    Object.keys(value).forEach((k) => {
      out[k] = sanitizeResult(value[k], k);
    });
    return out;
  }
  return value;
};

const normalizeChoiceValues = (items) => {
  if (!Array.isArray(items)) return [];
  return items
    .map((item) => {
      if (item && typeof item === 'object') {
        return item.value !== undefined ? item.value : (item.id ?? item.name ?? '');
      }
      return item;
    })
    .map((v) => String(v))
    .filter((v) => v !== '');
};

const pickChoices = (node, context) => {
  if (!node || !node.choices) return [];
  let c = node.choices;
  
  if (Array.isArray(c)) return normalizeChoiceValues(c);

  const levels = ['instrumentType', 'instrument_type', 'region', 'delay'];
  for (const l of levels) {
    if (Array.isArray(c)) break;
    if (c && typeof c === 'object' && c[l]) {
      const branch = c[l];
      let sel = context[l] || Object.keys(branch)[0];
      if (!branch[sel]) sel = Object.keys(branch)[0];
      c = branch[sel];
    }
  }
  
  if (c && c.choices && Array.isArray(c.choices)) c = c.choices;
  return Array.isArray(c) ? normalizeChoiceValues(c) : [];
};

const resetDatasetList = () => {
  currentDatasets = [];
  selectedDatasets.clear();
  if (df.datasetSearch) df.datasetSearch.value = '';
  renderDatasetList();
  renderDatasetSelected();
  updateDatasetCount();
};

let datasetLoadTimer = null;
const scheduleLoadDatasets = () => {
  if (!state.settingsOptions) return;
  if (datasetLoadTimer) {
    clearTimeout(datasetLoadTimer);
  }
  datasetLoadTimer = setTimeout(() => {
    actions.loadDatasets().catch(() => {});
  }, 200);
};

const updateInstrumentOptions = () => {
  const fallback = ['EQUITY'];
  const list = state.settingsOptions ? pickChoices(state.settingsOptions.instrumentType || state.settingsOptions.instrument_type, {}) : fallback;
  fillSelectOptions(df.instrument, list.length ? list : fallback);
  updateRegionOptions();
};

const updateRegionOptions = () => {
  const fallback = ['USA', 'GLB', 'EUR', 'ASI', 'CHN', 'KOR', 'TWN', 'IND'];
  const list = state.settingsOptions ? pickChoices(state.settingsOptions.region, {
    instrumentType: df.instrument.value,
  }) : fallback;
  fillSelectOptions(df.region, list.length ? list : fallback);
  updateDelayOptions();
};

const updateDelayOptions = () => {
  const fallback = ['1', '0'];
  const list = state.settingsOptions ? pickChoices(state.settingsOptions.delay, {
    instrumentType: df.instrument.value,
    region: df.region.value,
  }) : fallback;
  fillSelectOptions(df.delay, list.length ? list : fallback);
  updateUniverseOptions();
};

const updateUniverseOptions = () => {
  const fallback = ['TOP3000', 'TOP2000', 'TOP1000', 'TOP500', 'TOP200', 'TOPSP500'];
  const list = state.settingsOptions ? pickChoices(state.settingsOptions.universe, {
    instrumentType: df.instrument.value,
    region: df.region.value,
    delay: df.delay.value,
  }) : fallback;
  fillSelectOptions(df.universe, list.length ? list : fallback);
  resetDatasetList();
  scheduleLoadDatasets();
};

const updateExpandOptions = () => {
  if (!state.settingsOptions) return;
  const regions = pickChoices(state.settingsOptions.region, {
    instrumentType: df.instrument.value,
  });
  const delays = pickChoices(state.settingsOptions.delay, {
    instrumentType: df.instrument.value,
    region: df.region.value,
  });
  const universes = pickChoices(state.settingsOptions.universe, {
    instrumentType: df.instrument.value,
    region: df.region.value,
    delay: df.delay.value,
  });
  fillSelectOptionsWithBlank(ui.expandRegion, regions);
  fillSelectOptionsWithBlank(ui.expandDelay, delays);
  fillSelectOptionsWithBlank(ui.expandUniverse, universes);
};

const isValidSettingsOptions = (raw) => {
  if (!raw || typeof raw !== 'object') return false;
  const keys = Object.keys(raw);
  if (keys.includes('instrumentType') || keys.includes('instrument_type')) return true;
  if (keys.includes('region') && keys.includes('delay') && keys.includes('universe')) return true;
  return false;
};

const ensureSettingsOptions = async () => {
  let fileName = getSelectedName(ui.fileSettings, getKindDefault('settings'));
  if (!fileName) {
    await refreshFileList('settings');
    // 尝试从下拉列表中获取第一个有效值
    if (ui.fileSettings && ui.fileSettings.options.length > 0) {
      for (let opt of ui.fileSettings.options) {
        if (opt.value) {
          fileName = opt.value;
          ui.fileSettings.value = fileName;
          setActiveFileForKind('settings', fileName);
          break;
        }
      }
    }
  }
  if (!fileName) {
    // 如果还是没有，尝试寻找默认的 settings_options.json
    const defaultName = 'settings_options.json';
    const exists = state.files && state.files['settings']?.includes(defaultName);
    if (exists) {
      fileName = defaultName;
      ui.fileSettings.value = fileName;
      setActiveFileForKind('settings', fileName);
    }
  }

  if (!fileName) throw new Error('请先在主页「文件管理」中选择或新建一个 settings 类型的 JSON 文件');
  
  const settingsPath = getFilePath('settings', ui.fileSettings);
  let data = await apiPost('/api/settings-options/list', { file: settingsPath });
  let raw = data.data && data.data.raw ? data.data.raw : null;
  
  if (!isValidSettingsOptions(raw)) {
    // 尝试从 API 下载最新的设置
    setResult('正在从 Brain API 下载设置选项元数据...');
    await apiPost('/api/meta/settings', { out: settingsPath });
    await refreshFileList('settings');
    data = await apiPost('/api/settings-options/list', { file: settingsPath });
    raw = data.data && data.data.raw ? data.data.raw : null;
  }
  
  state.settingsOptions = raw;
  updateInstrumentOptions();
  resetDatasetList();
  
  if (df.settingsLabel) df.settingsLabel.textContent = fileName;
  if (df.status) df.status.textContent = '已加载设置选项: ' + fileName;
  return data;
};

const openDataFieldsModal = () => {
  if (!df.modal) return;
  df.modal.style.display = 'block';
  requestAnimationFrame(() => {
    df.modal.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  
  // 强制触发一次下拉列表填充
  updateInstrumentOptions(); 
  
  if (df.targetLabel) {
    df.targetLabel.textContent = ui.datafieldsTarget.value || '-';
  }
  const target = ui.datafieldsTarget.value;
  selectedDataFields = new Set(state.fillRules[target] || []);
  updateSelectedDataFieldsDisplay();
  if (currentDataFields.length) {
    populateDataFieldsList();
  }
  updateActiveLabels();
  if (df.useCache && df.useCache.checked) {
    const params = getDatafieldsParams();
    const cacheKey = buildDatafieldsCacheKey(params);
    if (datafieldsCache.has(cacheKey)) {
      applyDataFieldsResult(datafieldsCache.get(cacheKey), { cached: true });
    }
  }
};

const closeDataFieldsModal = () => {
  if (!df.modal) return;
  df.modal.style.display = 'none';
};

const normalizeDataField = (raw) => {
  const id = raw.id || raw.name || raw.field || raw.short_name || raw.full_name || '';
  return {
    id: String(id),
    description: raw.description || raw.desc || raw.full_name || raw.name || '',
    type: raw.type || raw.data_type || '',
    coverage: typeof raw.coverage === 'number' ? raw.coverage : (Number(raw.coverage || 0) || 0),
    userCount: Number(raw.userCount || raw.user_count || raw.users || 0),
    alphaCount: Number(raw.alphaCount || raw.alpha_count || raw.alphas || 0),
  };
};

const buildDatafieldsCacheKey = (params) => {
  const datasetIds = params.dataset_ids || (params.dataset_id ? [params.dataset_id] : []);
  const sorted = Array.from(datasetIds).map(String).sort();
  return JSON.stringify({
    instrument: params.instrument,
    region: params.region,
    delay: params.delay,
    universe: params.universe,
    dataset_ids: sorted,
    limit: params.limit,
  });
};

const getDatafieldsParams = () => ({
  instrument: df.instrument.value,
  region: df.region.value,
  delay: df.delay.value ? Number(df.delay.value) : null,
  universe: df.universe.value,
  dataset_ids: Array.from(selectedDatasets),
  limit: df.limit.value ? Number(df.limit.value) : 500,
  out: getFilePath('datafields', ui.fileDatafields),
});

const applyDataFieldsResult = (raw, meta) => {
  const list = Array.isArray(raw) ? raw : [];
  currentDataFields = list.map(normalizeDataField);
  state.datafields = currentDataFields;
  if (dataFieldsModalMode === 'ai') {
    selectedDataFields = new Set(aiSelectedFields.map((f) => f.id).filter(Boolean));
  } else {
    const target = ui.datafieldsTarget.value;
    selectedDataFields = new Set(state.fillRules[target] || []);
  }
  sortColumn = '';
  sortOrder = 'asc';
  columnFilters.id = '';
  columnFilters.description = '';
  columnFilters.type = '';
  columnFilters.coverage = { min: null, max: null };
  columnFilters.userCount = null;
  columnFilters.alphaCount = null;
  document.querySelectorAll('.column-filter').forEach((input) => {
    input.value = '';
  });
  document.querySelectorAll('.column-filter-min, .column-filter-max').forEach((input) => {
    input.value = '';
  });
  if (df.filterHighCoverage) df.filterHighCoverage.checked = false;
  if (df.filterPopular) df.filterPopular.checked = false;
  if (df.filterMatrixOnly) df.filterMatrixOnly.checked = false;
  populateTypeFilter();
  populateDataFieldsList();
  updateSelectedDataFieldsDisplay();
  if (df.loading) df.loading.style.display = 'none';
  if (df.content) {
    const body = df.content.parentElement;
    if (body) body.classList.add('data-fields-active');
  }
  if (df.status) {
    let message = meta && meta.cached ? '已使用缓存加载字段' : '已更新字段缓存';
    if (meta && meta.warning) {
      message = `${message}（${meta.warning}）`;
    }
    df.status.textContent = message;
  }
};

const updateDataFieldsStats = () => {
  if (df.count) df.count.textContent = `${currentDataFields.length} fields loaded`;
  if (df.filtered) df.filtered.textContent = `${filteredDataFields.length} filtered`;
  if (df.selectedCount) df.selectedCount.textContent = `${selectedDataFields.size} selected`;
  updateSummary();
};

const updateSelectedDataFieldsDisplay = () => {
  if (!df.selectedList) return;
  df.selectedList.innerHTML = '';
  if (selectedDataFields.size === 0) {
    df.selectedList.innerHTML = '<em style="color:#666;">No data fields selected</em>';
    return;
  }
  selectedDataFields.forEach((fieldId) => {
    const item = document.createElement('span');
    item.className = 'selected-item';
    item.textContent = fieldId;
    const removeButton = document.createElement('button');
    removeButton.className = 'remove-btn';
    removeButton.type = 'button';
    removeButton.textContent = '×';
    removeButton.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      removeSelectedDataField(fieldId);
    });
    item.appendChild(removeButton);
    df.selectedList.appendChild(item);
  });
};

const updateSelectAllCheckbox = () => {
  if (!df.selectAllCheckbox) return;
  const allSelected = filteredDataFields.length > 0 && filteredDataFields.every((f) => selectedDataFields.has(f.id));
  df.selectAllCheckbox.checked = allSelected;
  df.selectAllCheckbox.indeterminate = !allSelected && filteredDataFields.some((f) => selectedDataFields.has(f.id));
};

const toggleDataFieldSelection = (fieldId, row) => {
  const checkbox = row.querySelector('.data-field-checkbox');
  if (selectedDataFields.has(fieldId)) {
    selectedDataFields.delete(fieldId);
    if (checkbox) checkbox.checked = false;
    row.classList.remove('selected');
  } else {
    selectedDataFields.add(fieldId);
    if (checkbox) checkbox.checked = true;
    row.classList.add('selected');
  }
  updateSelectedDataFieldsDisplay();
  updateDataFieldsStats();
  updateSelectAllCheckbox();
};

const removeSelectedDataField = (fieldId) => {
  selectedDataFields.delete(fieldId);
  updateSelectedDataFieldsDisplay();
  updateDataFieldsStats();
  const row = document.querySelector(`tr[data-field-id="${fieldId}"]`);
  if (row) {
    const checkbox = row.querySelector('.data-field-checkbox');
    if (checkbox) checkbox.checked = false;
    row.classList.remove('selected');
  }
  updateSelectAllCheckbox();
};

const populateTypeFilter = () => {
  if (!df.typeFilter) return;
  const uniqueTypes = [...new Set(currentDataFields.map((field) => field.type).filter(Boolean))].sort();
  df.typeFilter.innerHTML = '<option value="">All Types</option>';
  uniqueTypes.forEach((type) => {
    const option = document.createElement('option');
    option.value = type;
    option.textContent = type;
    df.typeFilter.appendChild(option);
  });
  if (columnFilters.type && uniqueTypes.includes(columnFilters.type)) {
    df.typeFilter.value = columnFilters.type;
  }
};

const getSettingsKey = () => {
  return JSON.stringify({
    instrument: df.instrument.value || '',
    region: df.region.value || '',
    delay: df.delay.value || '',
    universe: df.universe.value || '',
  });
};

const normalizeDataset = (raw) => {
  if (typeof raw === 'string' || typeof raw === 'number') {
    const id = String(raw).trim();
    return { id, name: '', theme: '' };
  }
  if (!raw || typeof raw !== 'object') {
    return { id: '', name: '', theme: '' };
  }
  const nested = raw.dataset || raw.dataSet || raw.data_set || null;
  const id =
    raw.id ??
    raw.dataset_id ??
    raw.datasetId ??
    (nested ? (nested.id ?? nested.dataset_id ?? nested.datasetId) : undefined) ??
    raw.code ??
    raw.key ??
    raw.name ??
    (nested ? nested.name : undefined) ??
    '';
  const name =
    raw.name ??
    raw.description ??
    raw.desc ??
    (nested ? (nested.name ?? nested.description ?? nested.desc) : undefined) ??
    '';
  const theme = raw.theme ?? (nested ? nested.theme : undefined) ?? '';
  return {
    id: String(id || '').trim(),
    name: String(name || '').trim(),
    theme: String(theme || '').trim(),
  };
};

const datasetLabel = (ds) => {
  if (ds.id && ds.name && ds.id !== ds.name) return `${ds.id} - ${ds.name}`;
  if (ds.id) return ds.id;
  if (ds.name) return ds.name;
  return '(未知数据集)';
};

const updateDatasetCount = () => {
  if (df.datasetCount) df.datasetCount.textContent = `已选 ${selectedDatasets.size}`;
};

const renderDatasetSelected = () => {
  if (!df.datasetSelected) return;
  df.datasetSelected.innerHTML = '';
  if (!selectedDatasets.size) {
    const empty = document.createElement('span');
    empty.className = 'muted';
    empty.textContent = '未选择数据集';
    df.datasetSelected.appendChild(empty);
    return;
  }
  selectedDatasets.forEach((id) => {
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.innerHTML = `${id} <button data-ds-remove="${id}">×</button>`;
    df.datasetSelected.appendChild(chip);
  });
};

const renderDatasetList = () => {
  if (!df.datasetPanel) return;
  const filter = df.datasetSearch ? df.datasetSearch.value.trim().toLowerCase() : '';
  df.datasetPanel.innerHTML = '';
  const list = currentDatasets.filter((ds) => {
    if (!filter) return true;
    const label = datasetLabel(ds).toLowerCase();
    return label.includes(filter);
  });
  if (!list.length) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = '未加载数据集';
    df.datasetPanel.appendChild(empty);
    return;
  }
  list.forEach((ds) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    const active = selectedDatasets.has(ds.id);
    btn.className = `dataset-item${active ? ' active' : ''}`;
    btn.dataset.datasetId = ds.id;
    btn.innerHTML = `<span>${datasetLabel(ds)}</span><span class="muted">${ds.theme || ''}</span>`;
    df.datasetPanel.appendChild(btn);
  });
};

const applyDatasetSelection = () => {
  const key = getSettingsKey();
  datasetSelectionCache.set(key, Array.from(selectedDatasets).filter((id) => id));
  saveDatasetSelectionCache();
  updateDatasetCount();
  renderDatasetSelected();
  renderDatasetList();
  if (df.useCache && df.useCache.checked) {
    const params = getDatafieldsParams();
    const cacheKey = buildDatafieldsCacheKey(params);
    if (datafieldsCache.has(cacheKey)) {
      applyDataFieldsResult(datafieldsCache.get(cacheKey), { cached: true });
    }
  }
};

const toggleDataset = (id) => {
  if (!id) return;
  if (selectedDatasets.has(id)) {
    selectedDatasets.delete(id);
  } else {
    selectedDatasets.add(id);
  }
  applyDatasetSelection();
};

const populateDatasetList = (datasets) => {
  currentDatasets = datasets.map(normalizeDataset).filter((ds) => ds.id || ds.name);
  const key = getSettingsKey();
  const cached = datasetSelectionCache.get(key) || [];
  if (cached.length) {
    selectedDatasets = new Set(cached.filter((id) => id && currentDatasets.some((ds) => ds.id === id)));
  } else {
    selectedDatasets = new Set();
  }
  updateDatasetCount();
  renderDatasetList();
  renderDatasetSelected();
};

const populateDataFieldsList = () => {
  if (!df.tableBody) return;
  const highCoverage = df.filterHighCoverage && df.filterHighCoverage.checked;
  const popular = df.filterPopular && df.filterPopular.checked;
  const matrixOnly = df.filterMatrixOnly && df.filterMatrixOnly.checked;

  filteredDataFields = currentDataFields.filter((field) => {
    if (columnFilters.id && !field.id.toLowerCase().includes(columnFilters.id.toLowerCase())) return false;
    if (columnFilters.description && !field.description.toLowerCase().includes(columnFilters.description.toLowerCase())) return false;
    if (columnFilters.type && field.type !== columnFilters.type) return false;
    if (columnFilters.coverage.min !== null && field.coverage * 100 < columnFilters.coverage.min) return false;
    if (columnFilters.coverage.max !== null && field.coverage * 100 > columnFilters.coverage.max) return false;
    if (columnFilters.userCount !== null && field.userCount < columnFilters.userCount) return false;
    if (columnFilters.alphaCount !== null && field.alphaCount < columnFilters.alphaCount) return false;
    
    if (df.filterHighCoverage?.checked && field.coverage < 0.9) return false;
    if (df.filterPopular?.checked && field.userCount < 1000) return false;
    if (df.filterMatrixOnly?.checked && field.type !== 'MATRIX') return false;
    
    return true;
  });

  if (sortColumn) {
    filteredDataFields.sort((a, b) => {
      let aVal = a[sortColumn];
      let bVal = b[sortColumn];
      if (sortColumn === 'coverage' || sortColumn === 'userCount' || sortColumn === 'alphaCount') {
        aVal = Number(aVal);
        bVal = Number(bVal);
      } else {
        aVal = String(aVal || '').toLowerCase();
        bVal = String(bVal || '').toLowerCase();
      }
      if (aVal < bVal) return sortOrder === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortOrder === 'asc' ? 1 : -1;
      return 0;
    });
  }

  df.tableBody.innerHTML = '';
  if (filteredDataFields.length === 0) {
    df.tableBody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 30px; color:#666;">No data fields found</td></tr>';
    updateDataFieldsStats();
    return;
  }

  filteredDataFields.forEach((field) => {
    const row = document.createElement('tr');
    row.dataset.fieldId = field.id;
    if (selectedDataFields.has(field.id)) row.classList.add('selected');
    
    const covPct = (field.coverage * 100).toFixed(1);
    const covClass = field.coverage >= 0.9 ? 'coverage-high' : (field.coverage >= 0.5 ? 'coverage-mid' : 'coverage-low');
    const alphaHot = field.alphaCount > 5000 ? 'class="alpha-hot"' : '';

    row.innerHTML = `
      <td><input type="checkbox" class="data-field-checkbox" ${selectedDataFields.has(field.id) ? 'checked' : ''}></td>
      <td><span class="data-field-id">${field.id}</span></td>
      <td><div class="muted" style="max-height: 40px; overflow: hidden; line-height:1.2;" title="${field.description}">${field.description || ''}</div></td>
      <td><span class="data-field-type">${field.type || 'N/A'}</span></td>
      <td><span class="${covClass}">${covPct}%</span></td>
      <td>${field.userCount || 0}</td>
      <td><span ${alphaHot}>${field.alphaCount || 0}</span></td>
    `;
    row.onclick = (e) => {
      if (e.target.type !== 'checkbox') toggleDataFieldSelection(field.id, row);
    };
    const checkbox = row.querySelector('.data-field-checkbox');
    if (checkbox) {
      checkbox.onclick = (e) => {
        e.stopPropagation();
        toggleDataFieldSelection(field.id, row);
      };
    }
    df.tableBody.appendChild(row);
  });

  updateDataFieldsStats();
  updateSelectAllCheckbox();
};

const selectAllFilteredDataFields = () => {
  filteredDataFields.forEach((field) => selectedDataFields.add(field.id));
  populateDataFieldsList();
};

const clearAllSelectedDataFields = () => {
  selectedDataFields.clear();
  populateDataFieldsList();
};

const applySelectedDataFields = () => {
  if (dataFieldsModalMode === 'template') {
    const target = ui.datafieldsTarget.value;
    if (!target) {
      alert('请选择目标占位符');
      return;
    }
    if (!state.fillRules[target]) state.fillRules[target] = [];
    selectedDataFields.forEach((fieldId) => {
      if (!state.fillRules[target].includes(fieldId)) {
        state.fillRules[target].push(fieldId);
      }
    });
    state.placeholderCollapse[target] = true;
    renderPlaceholders();
    scheduleAutoSave();
  } else {
    selectedDataFields.forEach(fid => {
      const field = currentDataFields.find(f => f.id === fid);
      if (field && !aiSelectedFields.some(f => f.id === fid)) {
        aiSelectedFields.push(field);
      }
    });
    renderAiSelectedFields();
  }
  closeDataFieldsModal();
};

const renderAiSelectedFields = () => {
  if (!ai.selectedFields) return;
  ai.selectedFields.innerHTML = '';
  aiSelectedFields.forEach(field => {
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.innerHTML = `
      ${field.id}
      <button onclick="removeAiSelectedField('${field.id}')">&times;</button>
    `;
    ai.selectedFields.appendChild(chip);
  });
  if (ai.selectedCount) {
    ai.selectedCount.textContent = `已选 ${aiSelectedFields.length} 个字段`;
  }
};

window.removeAiSelectedField = (fid) => {
  aiSelectedFields = aiSelectedFields.filter(f => f.id !== fid);
  renderAiSelectedFields();
};

window.copyToClipboard = (text) => {
  navigator.clipboard.writeText(text).then(() => {
    alert('已复制到剪贴板');
  });
};

window.useAiAlphaAsTemplate = (expr) => {
  if (ui.templateText) {
    ui.templateText.value = expr;
    const templateTab = document.querySelector('.tab[data-tab="template"]');
    if (templateTab) templateTab.click();
    syncStateFromInputs();
    scheduleAutoSave();
  }
};

const setupDataFieldsModal = () => {
  if (!df.modal) return;

  if (df.filterHighCoverage) df.filterHighCoverage.onchange = populateDataFieldsList;
  if (df.filterPopular) df.filterPopular.onchange = populateDataFieldsList;
  if (df.filterMatrixOnly) df.filterMatrixOnly.onchange = populateDataFieldsList;

  if (df.instrument) df.instrument.onchange = updateRegionOptions;
  if (df.region) df.region.onchange = updateDelayOptions;
  if (df.delay) df.delay.onchange = updateUniverseOptions;
  if (df.universe) df.universe.onchange = scheduleLoadDatasets;
  if (df.datasetPanel) {
    df.datasetPanel.addEventListener('click', (event) => {
      const btn = event.target.closest('.dataset-item');
      if (!btn) return;
      const id = btn.dataset.datasetId;
      toggleDataset(id);
    });
  }
  if (df.datasetSelected) {
    df.datasetSelected.addEventListener('click', (event) => {
      const btn = event.target.closest('button[data-ds-remove]');
      if (!btn) return;
      const id = btn.dataset.dsRemove;
      if (!id) return;
      selectedDatasets.delete(id);
      applyDatasetSelection();
    });
  }
  if (df.datasetSearch) {
    df.datasetSearch.addEventListener('input', () => {
      renderDatasetList();
    });
  }

  if (df.selectAllBtn) df.selectAllBtn.onclick = selectAllFilteredDataFields;
  if (df.clearAllBtn) df.clearAllBtn.onclick = clearAllSelectedDataFields;
  if (df.selectAllCheckbox) {
    df.selectAllCheckbox.onclick = (e) => {
      e.stopPropagation();
      if (df.selectAllCheckbox.checked) {
        selectAllFilteredDataFields();
      } else {
        clearAllSelectedDataFields();
      }
    };
  }

  document.querySelectorAll('.column-filter').forEach((filter) => {
    const handler = (e) => {
      const column = e.target.dataset.column;
      const value = e.target.value;
      if (column === 'userCount' || column === 'alphaCount') {
        columnFilters[column] = value ? parseInt(value, 10) : null;
      } else {
        columnFilters[column] = value;
      }
      populateDataFieldsList();
    };
    filter.addEventListener('input', handler);
    filter.addEventListener('change', handler);
  });

  document.querySelectorAll('.column-filter-min, .column-filter-max').forEach((filter) => {
    filter.addEventListener('input', (e) => {
      const isMin = e.target.classList.contains('column-filter-min');
      const value = e.target.value;
      if (isMin) {
        columnFilters.coverage.min = value ? parseFloat(value) : null;
      } else {
        columnFilters.coverage.max = value ? parseFloat(value) : null;
      }
      populateDataFieldsList();
    });
  });

  document.querySelectorAll('.sort-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      const column = e.target.dataset.column;
      if (sortColumn === column) {
        sortOrder = sortOrder === 'asc' ? 'desc' : 'asc';
      } else {
        sortColumn = column;
        sortOrder = 'asc';
      }
      populateDataFieldsList();
    });
  });

  // 页面内面板模式：不再通过点击背景关闭
};

const previewModal = {
  overlay: null,
  title: null,
  body: null,
};

const initPreviewModal = () => {
  previewModal.overlay = document.getElementById('previewModal');
  previewModal.title = document.getElementById('previewModalTitle');
  previewModal.body = document.getElementById('previewModalBody');
  if (previewModal.overlay) {
    previewModal.overlay.addEventListener('click', (event) => {
      if (event.target === previewModal.overlay) {
        closePreviewModal();
      }
    });
  }
};

const openPreviewModal = (data) => {
  if (!previewModal.overlay) return;
  previewModal.overlay.style.display = 'block';
  if (previewModal.title) previewModal.title.textContent = data.name || '预览';
  if (previewModal.body) {
    const suffix = data.truncated ? '\n\n...内容已截断' : '';
    previewModal.body.textContent = (data.content || '无法预览该文件') + suffix;
  }
};

const closePreviewModal = () => {
  if (previewModal.overlay) previewModal.overlay.style.display = 'none';
};

function updateSummary() {
  const totalFields = Object.values(state.fillRules).reduce((acc, items) => acc + items.length, 0);
  if (ui.summaryPlaceholders) ui.summaryPlaceholders.textContent = String(state.placeholders.length);
  if (ui.summaryFields) ui.summaryFields.textContent = String(totalFields);
  if (ui.summaryDatafields) ui.summaryDatafields.textContent = String(state.datafields.length);
  if (ui.summaryTemplate) ui.summaryTemplate.textContent = getSelectedName(ui.fileTemplate, getKindDefault('template')) || '-';
}

const syncStateFromInputs = () => {
  state.templateFile = getSelectedName(ui.fileTemplate, getKindDefault('template'));
  state.templateId = ui.templateId.value || 'demo';
  state.templateText = ui.templateText.value || '';
  state.placeholders = extractPlaceholders(state.templateText);
  normalizeFillRules(state.placeholders);
  renderPlaceholders();
};

const scheduleAutoSave = () => {
  if (!ui.autoSave || !ui.autoSave.checked) return;
  if (autoSaveTimer) clearTimeout(autoSaveTimer);
  autoSaveTimer = setTimeout(() => {
    actions.saveTemplate().catch((err) => setResult({ error: err.message }));
  }, 800);
};

const actions = {
  async fileRefreshKind() {
    const kind = state.activeFileKind || ui.fileKind.value;
    return refreshFileList(kind);
  },
  async fileRefreshAll() {
    return refreshAllFiles();
  },
  async fileNew() {
    const kind = state.activeFileKind || ui.fileKind.value;
    const name = prompt('新建文件名（不含路径）');
    if (!name) return;
    const data = await apiPost('/api/files/new', { kind, name, folder: getFolder(kind) });
    await refreshFileList(kind);
    return data;
  },
  async fileRename() {
    const kind = state.activeFileKind || ui.fileKind.value;
    const select = primarySelectByKind[kind];
    const current = select ? select.value : '';
    if (!current) throw new Error('请先选择要重命名的文件');
    const next = prompt('新的文件名（不含路径）', current);
    if (!next || next === current) return;
    const data = await apiPost('/api/files/rename', { kind, name: current, new_name: next, folder: getFolder(kind) });
    await refreshFileList(kind);
    const selects = fileSelectsByKind[kind] || [];
    selects.forEach((item) => {
      if (!item) return;
      if (item.value === current || !item.value) {
        item.value = next;
      }
    });
    updateActiveLabels();
    return data;
  },
  async fileDelete() {
    const kind = state.activeFileKind || ui.fileKind.value;
    const select = primarySelectByKind[kind];
    const current = select ? select.value : '';
    if (!current) throw new Error('请先选择要删除的文件');
    if (!confirm(`确定删除 ${current} ?`)) return;
    const data = await apiPost('/api/files/delete', { kind, name: current, folder: getFolder(kind) });
    await refreshFileList(kind);
    updateActiveLabels();
    return data;
  },
  async fileFolderNew() {
    const kind = state.activeFileKind || ui.fileKind.value;
    const name = prompt('新建文件夹名称');
    if (!name) return;
    const data = await apiPost('/api/files/mkdir', { kind, name, folder: getFolder(kind) });
    await refreshFileList(kind);
    return data;
  },
  async fileFolderUp() {
    const kind = state.activeFileKind || ui.fileKind.value;
    if (!getFolder(kind)) return;
    state.fileFolders[kind] = '';
    await refreshFileList(kind);
    saveFileSelection();
  },
  async filePreview() {
    const kind = state.activeFileKind || ui.fileKind.value;
    const select = primarySelectByKind[kind];
    const current = select ? select.value : '';
    if (!current) throw new Error('请先选择要预览的文件');
    const data = await apiPost('/api/files/preview', { kind, name: current, folder: getFolder(kind) });
    openPreviewModal(data.data || {});
    return data;
  },
  async fileDownload() {
    const kind = state.activeFileKind || ui.fileKind.value;
    const select = primarySelectByKind[kind];
    const current = select ? select.value : '';
    if (!current) throw new Error('请先选择要下载的文件');
    const params = new URLSearchParams({ kind, name: current });
    const folder = getFolder(kind);
    if (folder) params.append('folder', folder);
    const link = document.createElement('a');
    link.href = `/api/files/download?${params.toString()}`;
    link.download = current;
    document.body.appendChild(link);
    link.click();
    link.remove();
    return { ok: true, name: current };
  },
  clearDatafieldsCache() {
    datafieldsCache.clear();
    saveDatafieldsCache();
    if (df.status) df.status.textContent = '已清空字段缓存';
    setResult({ ok: true, message: '已清空字段缓存' });
  },
  closePreviewModal() {
    closePreviewModal();
  },
  async loadTemplate() {
    const data = await apiPost('/api/template/read', { file: getFilePath('template', ui.fileTemplate) });
    if (data.ok && data.data && data.data.content) {
      const parsed = JSON.parse(data.data.content);
      ui.templateId.value = parsed.template_id || 'template';
      ui.templateText.value = parsed.template || '';
      state.fillRules = parsed.fill_rules || {};
      state.metadata = parsed.metadata || { priority: 100, tags: [], settings_override: {} };
      syncStateFromInputs();
    }
  },
  async saveTemplate() {
    syncStateFromInputs();
    const payload = {
      schema_version: '0.1',
      template_id: state.templateId,
      template: state.templateText,
      fill_rules: state.fillRules,
      metadata: state.metadata,
    };
    return apiPost('/api/template/save', { file: getFilePath('template', ui.fileTemplate), content: JSON.stringify(payload, null, 2) });
  },
  async validateTemplate() {
    await actions.saveTemplate();
    return apiPost('/api/template/validate', { file: getFilePath('template', ui.fileTemplate) });
  },
  async openDataFieldsModal() {
    dataFieldsModalMode = 'template';
    try {
      await ensureSettingsOptions();
    } catch (err) {
      console.warn('Settings options not loaded:', err);
    }
    if (df.modal) {
      df.modal.style.display = 'block';
      openDataFieldsModal();
      scheduleLoadDatasets();
    } else {
      console.error('Modal element #dataFieldsModal not found');
      alert('错误：找不到弹窗 DOM 元素');
    }
  },
  closeDataFieldsModal() {
    closeDataFieldsModal();
  },
  clearPlaceholderFields() {
    const target = ui.datafieldsTarget.value;
    if (target && state.fillRules[target]) {
      state.fillRules[target] = [];
      renderPlaceholders();
      scheduleAutoSave();
    }
  },
  async loadDataFields() {
    if (!df.instrument.value || !df.region.value || !df.delay.value || !df.universe.value) {
      throw new Error('请先选择 Instrument/Region/Delay/Universe');
    }
    if (!selectedDatasets.size) {
      throw new Error('请先选择 Dataset（可多选）');
    }
    if (df.loading) df.loading.style.display = 'block';
    if (df.content) {
      const body = df.content.parentElement;
      if (body) body.classList.remove('data-fields-active');
    }
    const useCache = df.useCache ? df.useCache.checked : false;
    try {
      const params = getDatafieldsParams();
      const cacheKey = buildDatafieldsCacheKey(params);
      if (useCache && datafieldsCache.has(cacheKey)) {
        const cached = datafieldsCache.get(cacheKey);
        applyDataFieldsResult(cached, { cached: true });
        setStatus('ok');
        setResult({ cached: true, count: cached.length });
        return { cached: true, count: cached.length, results: cached };
      }
      const data = await apiPost('/api/datafields/fetch', { ...params, use_cache: useCache });
      const raw = (data.data && Array.isArray(data.data.results)) ? data.data.results : (Array.isArray(data.data) ? data.data : []);
      const warning = data.data && data.data.warning ? data.data.warning : '';
      datafieldsCache.set(cacheKey, raw);
      saveDatafieldsCache();
      applyDataFieldsResult(raw, { cached: data.data && data.data.cached, warning });
      return data;
    } catch (err) {
      if (df.content) {
        const body = df.content.parentElement;
        if (body) body.classList.add('data-fields-active');
      }
      if (df.status) {
        const message = err && err.message ? String(err.message) : '';
        if (message.toLowerCase().includes('limit') || message.includes('429')) {
          df.status.textContent = '触发 API 限流';
        } else {
          df.status.textContent = '字段加载失败';
        }
      }
      if (df.tableBody) {
        const msg = err && err.message ? String(err.message) : '未知错误';
        df.tableBody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding: 30px; color:#b91c1c;">加载失败：${msg}</td></tr>`;
      }
      currentDataFields = [];
      filteredDataFields = [];
      updateDataFieldsStats();
      throw err;
    } finally {
      if (df.loading) df.loading.style.display = 'none';
    }
  },
  async loadSettingsOptions() {
    const data = await apiPost('/api/settings-options/list', {
      file: getFilePath('settings', ui.fileSettings),
    });
    state.settingsOptions = data.data && data.data.raw ? data.data.raw : null;
    updateInstrumentOptions();
    resetDatasetList();
    return data;
  },
  async downloadSettingsOptions() {
    const data = await apiPost('/api/meta/settings', { out: getFilePath('settings', ui.fileSettings) });
    await actions.loadSettingsOptions();
    return data;
  },
  async loadDatasets() {
    if (!df.instrument.value || !df.region.value || !df.delay.value || !df.universe.value) {
      throw new Error('请先加载设置选项并选择 Instrument/Region/Delay/Universe');
    }
    const key = getSettingsKey();
    if (datasetListCache.has(key)) {
      populateDatasetList(datasetListCache.get(key));
      if (df.status) df.status.textContent = '已从本地缓存加载数据集';
      return { cached: true, count: datasetListCache.get(key).length };
    }
    const data = await apiPost('/api/datasets/list', {
      instrument: df.instrument.value,
      region: df.region.value,
      delay: Number(df.delay.value),
      universe: df.universe.value,
      use_cache: true,
    });
    const datasets = data.data && Array.isArray(data.data.results) ? data.data.results : [];
    datasetListCache.set(key, datasets);
    saveDatasetListCache();
    populateDatasetList(datasets);
    if (df.status) df.status.textContent = data.data && data.data.cached ? '已从本地清单加载数据集' : '已在线加载数据集';
    return data;
  },
  datasetSelectAll() {
    currentDatasets.forEach((ds) => selectedDatasets.add(ds.id));
    applyDatasetSelection();
  },
  datasetClear() {
    selectedDatasets.clear();
    applyDatasetSelection();
  },
  applySelectedDataFields() {
    applySelectedDataFields();
  },
  async openAiFieldSelector() {
    dataFieldsModalMode = 'ai';
    if (df.targetLabel) df.targetLabel.textContent = 'AI 灵感生成器 (灵感源选择)';
    selectedDataFields = new Set(aiSelectedFields.map((f) => f.id).filter(Boolean));
    updateSelectedDataFieldsDisplay();

    try {
      await ensureSettingsOptions();
    } catch (err) {
      console.warn('Settings options not loaded for AI selector:', err);
      // 使用默认级联选项，避免下拉框显示空白
      state.settingsOptions = null;
      updateInstrumentOptions();
      if (df.status) df.status.textContent = '设置选项加载失败，已使用默认选项';
    }

  if (df.modal) {
    df.modal.style.display = 'block';
    requestAnimationFrame(() => {
      df.modal.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
    updateInstrumentOptions();
    if (currentDataFields.length) {
      populateDataFieldsList();
    }
    scheduleLoadDatasets();
    }
  },
  async aiGenerate() {
    if (aiSelectedFields.length === 0) {
      alert('请先选择至少一个数据字段作为灵感源。');
      return;
    }
    if (ai.loading) ai.loading.style.display = 'block';
    if (ai.results) {
      ai.results.style.display = 'none';
      ai.results.innerHTML = '';
    }
    
    try {
      const context = {
        instrument: df.instrument ? df.instrument.value : 'EQUITY',
        region: df.region ? df.region.value : 'USA',
        delay: df.delay ? df.delay.value : '1',
        universe: df.universe ? df.universe.value : 'TOP3000'
      };

      const payload = {
        fields: aiSelectedFields,
        report_text: ai.reportText ? ai.reportText.value : '',
        count: ai.genCount ? ai.genCount.value : 5,
        include_patterns: ai.includePatterns ? ai.includePatterns.checked : true,
        context: context
      };
      
      const resp = await apiPost('/api/ai/generate', payload);
      const data = resp.data || resp;
      const alphas = data.alphas || [];
      
      if (alphas.length === 0) {
        if (ai.results) ai.results.innerHTML = '<p class="muted">AI 没能生成有效的因子</p>';
      } else {
        if (ai.results) {
          alphas.forEach(alpha => {
            const card = document.createElement('div');
            card.className = 'ai-card';
            card.innerHTML = `
              <h4>${alpha.name} <span class="ai-tag">Generated</span></h4>
              <div class="ai-expr">${alpha.expression}</div>
              <div class="ai-logic">${alpha.logic}</div>
              <div class="ai-actions">
                <button class="btn-secondary" onclick="copyToClipboard('${alpha.expression}')">复制表达式</button>
                <button class="btn-outline" onclick="useAiAlphaAsTemplate('${alpha.expression}')">使用此模板</button>
              </div>
            `;
            ai.results.appendChild(card);
          });
        }
      }
      if (ai.results) ai.results.style.display = 'flex';
    } catch (err) {
      alert('AI 生成失败: ' + err.message);
    } finally {
      if (ai.loading) ai.loading.style.display = 'none';
    }
  },
  async dreamStart() {
    if (aiSelectedFields.length === 0) {
      throw new Error('请先在「选择数据字段」中至少选一个字段');
    }
    const context = {
      instrument: df.instrument ? df.instrument.value : 'EQUITY',
      region: df.region ? df.region.value : 'USA',
      delay: df.delay ? Number(df.delay.value || 1) : 1,
      universe: df.universe ? df.universe.value : 'TOP3000',
    };
    const turnoverRange = parseDreamTurnoverRange();
    const payload = {
      fields: aiSelectedFields,
      context,
      report_text: ai.reportText ? ai.reportText.value : '',
      include_patterns: ai.includePatterns ? ai.includePatterns.checked : true,
      generation_count: 8,
      interval_sec: ui.dreamIntervalSec && ui.dreamIntervalSec.value ? Number(ui.dreamIntervalSec.value) : 30,
      max_wait_sec: ui.dreamMaxWaitSec && ui.dreamMaxWaitSec.value ? Number(ui.dreamMaxWaitSec.value) : 1800,
      error_notify_cooldown_sec: ui.dreamErrorCooldownSec && ui.dreamErrorCooldownSec.value ? Number(ui.dreamErrorCooldownSec.value) : 180,
      auth_refresh_interval_sec: ui.dreamAuthRefreshSec && ui.dreamAuthRefreshSec.value ? Number(ui.dreamAuthRefreshSec.value) : 900,
      operators_refresh_interval_sec: ui.dreamOperatorRefreshSec && ui.dreamOperatorRefreshSec.value ? Number(ui.dreamOperatorRefreshSec.value) : 1800,
      baseline_alpha_id: ui.dreamBaselineAlphaId ? String(ui.dreamBaselineAlphaId.value || '').trim() : '',
      force_stage: ui.dreamForceStage ? String(ui.dreamForceStage.value || '').trim().toUpperCase() : '',
      operators_file: ui.dreamOperatorsFile ? String(ui.dreamOperatorsFile.value || '').trim() : 'metadata/operators.json',
      results_file: ui.dreamResultsFile ? String(ui.dreamResultsFile.value || '').trim() : '',
      pg_seed_dsn: ui.dreamPgSeedDsn ? String(ui.dreamPgSeedDsn.value || '').trim() : '',
      pg_seed_table: ui.dreamPgSeedTable ? String(ui.dreamPgSeedTable.value || '').trim() : 'dream_alpha_good_seeds',
      pg_seed_min_fitness: ui.dreamPgSeedMinFitness && ui.dreamPgSeedMinFitness.value ? Number(ui.dreamPgSeedMinFitness.value) : 0.9,
      pg_seed_min_turnover: turnoverRange.min,
      pg_seed_max_turnover: turnoverRange.max,
      sharpe_abs_threshold: ui.dreamSharpeAbsThreshold && ui.dreamSharpeAbsThreshold.value ? Number(ui.dreamSharpeAbsThreshold.value) : 1.0,
      fitness_threshold: ui.dreamFitnessThreshold && ui.dreamFitnessThreshold.value ? Number(ui.dreamFitnessThreshold.value) : 1.0,
      template_sharpe_threshold: ui.dreamTemplateSharpeThreshold && ui.dreamTemplateSharpeThreshold.value ? Number(ui.dreamTemplateSharpeThreshold.value) : 1.58,
      max_seed_in_prompt: ui.dreamMaxSeedInPrompt && ui.dreamMaxSeedInPrompt.value ? Number(ui.dreamMaxSeedInPrompt.value) : 20,
      seed_file: ui.dreamSeedFile ? ui.dreamSeedFile.value : 'runs/dream_alpha_seed_library.json',
      cursor_file: ui.dreamCursorFile ? ui.dreamCursorFile.value : 'runs/dream_alpha_cursor.json',
      high_template_file: ui.dreamHighTemplateFile ? ui.dreamHighTemplateFile.value : 'runs/dream_alpha_high_templates.jsonl',
      notify_url: ui.dreamNotifyUrl ? ui.dreamNotifyUrl.value : 'https://tgpusher.opener.eu.org/',
      seed_expressions: parseDreamSeedBootstrap(),
    };
    const data = await apiPost('/api/dream-alpha/start', payload);
    renderDreamStatus(data);
    startDreamStatusPolling();
    return data;
  },
  async dreamStatus() {
    const data = await apiPost('/api/dream-alpha/status', {});
    renderDreamStatus(data);
    const node = data && data.data ? data.data : data;
    if (!node || !node.running) {
      stopDreamStatusPolling();
    }
    return data;
  },
  async dreamStop() {
    const data = await apiPost('/api/dream-alpha/stop', {});
    renderDreamStatus(data);
    stopDreamStatusPolling();
    return data;
  },
  async libraryRefresh() {
    const data = await apiPost('/api/template-lib/list', {
      file: getFilePath('template_library', ui.fileTemplateLibrary),
    });
    state.libraryItems = data.data && Array.isArray(data.data.items) ? data.data.items : [];
    renderLibrary();
    return data;
  },
  async librarySave() {
    syncStateFromInputs();
    const item = {
      template_id: state.templateId || 'template',
      name: state.templateId || 'template',
      template: state.templateText,
      fill_rules: state.fillRules,
      metadata: state.metadata,
    };
    const data = await apiPost('/api/template-lib/save', {
      file: getFilePath('template_library', ui.fileTemplateLibrary),
      item,
    });
    state.libraryItems = data.data && Array.isArray(data.data.items) ? data.data.items : state.libraryItems;
    renderLibrary();
    return data;
  },
  async libraryLoad(key) {
    const data = await apiPost('/api/template-lib/get', {
      file: getFilePath('template_library', ui.fileTemplateLibrary),
      key,
    });
    if (data.data && data.data.item) {
      const item = data.data.item;
      ui.templateId.value = item.template_id || item.name || 'template';
      ui.templateText.value = item.template || '';
      state.fillRules = item.fill_rules || {};
      state.metadata = item.metadata || { priority: 100, tags: [], settings_override: {} };
      syncStateFromInputs();
    }
    return data;
  },
  async libraryDelete(key) {
    const data = await apiPost('/api/template-lib/delete', {
      file: getFilePath('template_library', ui.fileTemplateLibrary),
      key,
    });
    state.libraryItems = data.data && Array.isArray(data.data.items) ? data.data.items : state.libraryItems;
    renderLibrary();
    return data;
  },
  async cacheFill() {
    await actions.saveTemplate();
    const outName = generateFileName('filled');
    ensureSelectOption(ui.cacheOut, outName);
    return apiPost('/api/template/cache-fill', {
      template: getFilePath('template', ui.fileTemplate),
      out: buildPath('template', outName, getFolder('template')),
      datafields: getFilePath('datafields', ui.fileDatafields),
      limit: ui.cacheLimit.value ? Number(ui.cacheLimit.value) : 30,
      rules: state.fillRules,
    }).then((data) => {
      refreshFileList('template').catch(() => {});
      setActiveFileForKind('template', outName);
      return data;
    });
  },
  async expandTemplate() {
    await actions.saveTemplate();
    const settings = {};
    if (ui.expandRegion.value) settings.region = ui.expandRegion.value;
    if (ui.expandUniverse.value) settings.universe = ui.expandUniverse.value;
    if (ui.expandDelay.value) settings.delay = Number(ui.expandDelay.value);
    if (ui.expandSettings.value.trim()) {
      try {
        Object.assign(settings, JSON.parse(ui.expandSettings.value));
      } catch (err) {
        throw new Error(`高级覆盖设置 JSON 无效: ${err.message}`);
      }
    }
    const outName = generateFileName('factors');
    ensureSelectOption(ui.expandOut, outName);
    return apiPost('/api/template/expand', {
      template: getFilePath('template', ui.fileTemplate),
      out: buildPath('factors', outName, getFolder('factors')),
      settings,
      rules: state.fillRules,
      append: false,
    }).then((data) => {
      refreshFileList('factors').catch(() => {});
      setActiveFileForKind('factors', outName);
      return data;
    });
  },
  async shareTo0x0() {
    const outName = ui.expandOut.value || 'factors_exp.json';
    const filePath = buildPath('factors', outName, getFolder('factors'));
    setStatus('loading');
    setResult(`[分享中] 正在将 ${outName} 上传到 0x0.st...`);
    try {
      const data = await apiPost('/api/files/share-0x0', { file: filePath });
      const url = data.data.url;
      setResult(`🚀 分享成功！\n链接: ${url}`);
      alert(`分享成功！链接已生成：\n${url}`);
    } catch (err) {
      setResult({ error: `分享失败: ${err.message}` });
    }
  },
  async submitStop() {
    setResult('已发出停止指令');
    return Promise.resolve();
  },
  async submitRun() {
    const filePath = state.submitUploadPath || getFilePath('factors', ui.submitFile);
    setResult(`🚀 任务已启动...\n并发: ${ui.submitConcurrency.value}\n模式: ${ui.submitOrdered.checked ? '流式' : '全量'}`);
    return apiPost('/api/submit/run', {
      file: filePath,
      db: getFilePath('factor_library', ui.libDb),
      max_wait: ui.submitMaxWait.value ? Number(ui.submitMaxWait.value) : 1800,
      concurrency: ui.submitConcurrency.value ? Number(ui.submitConcurrency.value) : 1,
      ordered: ui.submitOrdered.checked,
      retry_failed: ui.submitRetryFailed.checked,
    }).then(data => {
      setResult(data);
      return data;
    });
  },
  async submitStatus() {
    return apiPost('/api/library/stats', { db: getFilePath('factor_library', ui.libDb) });
  },
  async submitBackfill() {
    const statePath = getFilePath('state', ui.fileState);
    if (!statePath) throw new Error('请先选择提交状态文件');
    setResult(`🔄 正在回填状态: ${basename(statePath)}...`);
    return apiPost('/api/submit/backfill', {
      state: statePath,
      force: false,
    });
  },
  submitUpload() {
    if (ui.submitUploadInput) ui.submitUploadInput.click();
  },
  submitUseSelection() {
    state.submitUploadName = '';
    state.submitUploadPath = '';
    updateSubmitSourceLabel();
  },
  async libInit() {
    return apiPost('/api/library/init', { db: getFilePath('factor_library', ui.libDbAdv) });
  },
  async libArchive() {
    return apiPost('/api/library/archive', {
      db: getFilePath('factor_library', ui.libDbAdv),
      file: ui.libFile.value ? getFilePath('factors', ui.libFile) : '',
    });
  },
  async libStats() {
    return apiPost('/api/library/stats', { db: getFilePath('factor_library', ui.libDbAdv) });
  },
  async metaOperators() {
    return apiPost('/api/meta/operators', { out: getFilePath('operators', ui.metaOps) });
  },
  async metaSettings() {
    return apiPost('/api/meta/settings', { out: getFilePath('settings', ui.metaSettings) });
  },
  clearOutput() {
    setResult('等待操作...');
  },
};

window.switchTab = (tab) => {
  if (!tab) return;
  document.querySelectorAll('[data-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('.tab-panel').forEach((panel) => {
    panel.classList.toggle('active', panel.id === `tab-${tab}`);
  });
  if (tab === 'generate' && !state.settingsOptions) {
    ensureSettingsOptions().catch(() => {});
  }
};

document.addEventListener('click', (event) => {
  const target = event.target;
  const tabBtn = target.closest('[data-tab]');
  if (tabBtn) {
    const tabName = tabBtn.dataset.tab;
    window.switchTab(tabName);
    if (tabBtn.classList.contains('tab')) return;
  }

  const actionBtn = target.closest('[data-action]');
  if (actionBtn) {
    const actionName = actionBtn.dataset.action;
    if (actions[actionName]) {
      const result = actions[actionName]();
      if (result && typeof result.catch === 'function') {
        result.catch((err) => {
          console.error('Action error:', err);
          setResult({ error: err.message });
        });
      }
    }
  }
});

if (ui.fileKindTabs) {
  ui.fileKindTabs.addEventListener('click', (event) => {
    const btn = event.target.closest('.file-kind');
    if (!btn) return;
    const kind = btn.dataset.kind;
    if (kind) setActiveFileKind(kind);
  });
}

if (ui.fileList) {
  ui.fileList.addEventListener('click', (event) => {
    const btn = event.target.closest('.file-item');
    if (!btn) return;
    const kind = btn.dataset.fileKind;
    const name = btn.dataset.fileName;
    if (kind && name) setActiveFileForKind(kind, name);
  });
}

if (ui.fileFolderList) {
  ui.fileFolderList.addEventListener('click', (event) => {
    const btn = event.target.closest('.chip');
    if (!btn) return;
    const folder = btn.dataset.folderName;
    if (!folder) return;
    const kind = state.activeFileKind || 'template';
    state.fileFolders[kind] = folder;
    refreshFileList(kind).catch((err) => setResult({ error: err.message }));
    saveFileSelection();
  });
}

if (placeholderList) {
  placeholderList.addEventListener('click', (event) => {
    const target = event.target;
    if (target.dataset.clearAll) {
      if (confirm('确定要清空所有占位符的规则吗？')) {
        state.placeholders.forEach((name) => {
          state.fillRules[name] = [];
        });
        renderPlaceholders();
        scheduleAutoSave();
      }
      return;
    }
    if (target.dataset.clear) {
      const name = target.dataset.clear;
      if (confirm(`确定要清空 <${name}/> 的所有规则吗？`)) {
        state.fillRules[name] = [];
        renderPlaceholders();
        scheduleAutoSave();
      }
      return;
    }
    if (target.dataset.toggle) {
      const name = target.dataset.toggle;
      const current = state.placeholderCollapse[name];
      state.placeholderCollapse[name] = current === false ? true : false;
      renderPlaceholders();
      return;
    }
    if (target.dataset.add) {
      const name = target.dataset.add;
      const input = placeholderList.querySelector(`input[data-input="${name}"]`);
      if (input && addPlaceholderFields(name, input.value)) {
        input.value = '';
        renderPlaceholders();
        scheduleAutoSave();
      }
    }
    if (target.dataset.remove) {
      const name = target.dataset.remove;
      const idx = Number(target.dataset.index);
      state.fillRules[name].splice(idx, 1);
      renderPlaceholders();
      scheduleAutoSave();
    }
  });

  placeholderList.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    const input = event.target;
    if (!input.dataset.input) return;
    event.preventDefault();
    const name = input.dataset.input;
    if (addPlaceholderFields(name, input.value)) {
      input.value = '';
      renderPlaceholders();
      scheduleAutoSave();
    }
  });
}

if (libraryList) {
  libraryList.addEventListener('click', (event) => {
    const target = event.target;
    if (target.dataset.libLoad) {
      const key = target.dataset.libLoad;
      actions.libraryLoad(key).catch((err) => setResult({ error: err.message }));
    }
    if (target.dataset.libDelete) {
      const key = target.dataset.libDelete;
      actions.libraryDelete(key).catch((err) => setResult({ error: err.message }));
    }
  });
}

if (ui.templateText) {
  ui.templateText.addEventListener('input', () => {
    syncStateFromInputs();
    scheduleAutoSave();
  });
}

try {
  loadDatafieldsCache();
  loadDatasetSelectionCache();
  loadDatasetListCache();
  loadFileSelection();
  syncStateFromInputs();
  setResult('等待操作...');
  setupDataFieldsModal();
  initPreviewModal();
  refreshAllFiles()
    .then(() => {
      setActiveFileKind(state.activeFileKind);
      updateActiveLabels();
      actions.loadTemplate().catch(() => {});
      actions.dreamStatus().catch(() => {});
      return actions.libraryRefresh().catch(() => {});
    })
    .catch(() => {});
} catch (e) {
  console.error('Initialization error:', e);
}

if (ui.fileTemplate) {
  ui.fileTemplate.addEventListener('change', () => {
    syncStateFromInputs();
    updateActiveLabels();
    renderFileManager();
    saveFileSelection();
    actions.loadTemplate().catch(() => {});
  });
}
if (ui.fileTemplateLibrary) {
  ui.fileTemplateLibrary.addEventListener('change', () => {
    updateActiveLabels();
    actions.libraryRefresh().catch(() => {});
    renderFileManager();
    saveFileSelection();
  });
}
if (ui.fileDatafields) {
  ui.fileDatafields.addEventListener('change', () => {
    updateActiveLabels();
    renderFileManager();
    saveFileSelection();
  });
}
if (ui.fileSettings) {
  ui.fileSettings.addEventListener('change', () => {
    state.settingsOptions = null;
    updateActiveLabels();
    renderFileManager();
    saveFileSelection();
  });
}
if (ui.datafieldsTarget) {
  ui.datafieldsTarget.addEventListener('change', () => {
    if (df.targetLabel) df.targetLabel.textContent = ui.datafieldsTarget.value || '-';
    selectedDataFields = new Set(state.fillRules[ui.datafieldsTarget.value] || []);
    updateSelectedDataFieldsDisplay();
    if (currentDataFields.length) {
      populateDataFieldsList();
    }
  });
}
if (ui.submitUploadInput) {
  ui.submitUploadInput.addEventListener('change', async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      const data = await apiPost('/api/submit/upload', { name: file.name, content: text });
      const name = data.data && data.data.name ? data.data.name : file.name;
      state.submitUploadName = name;
      state.submitUploadPath = `runs/uploads/${name}`;
      updateSubmitSourceLabel();
      const stateName = generateFileName('submit_state');
      try {
        await apiPost('/api/files/new', { kind: 'state', name: stateName, folder: getFolder('state') });
        await refreshFileList('state');
        setActiveFileForKind('state', stateName);
      } catch (err) {
        setResult({ error: `创建状态文件失败: ${err.message}` });
      }
    } catch (err) {
      setResult({ error: err.message });
    } finally {
      event.target.value = '';
    }
  });
}
