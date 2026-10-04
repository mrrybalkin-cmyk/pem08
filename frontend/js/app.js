import { api, idPath } from './api.js';
import { state, currentAnalyses, orderedAnalyses } from './state.js';
import { $, el, button } from './dom.js';
import { render } from './workspace.js';

const competitorPath = id => `/competitors/${idPath(id)}`;
const sourcePath = id => `/sources/${idPath(id)}`;
const redraw = () => render(actions);
function message(error, operation = null) {
    if (error.code === 'ANALYSIS_DATA_NOT_READY') {
        if (operation === 'aggregate') return 'Для сводного анализа сначала нужен хотя бы один анализ текущего snapshot источника. Добавьте источник или нажмите «Повторить анализ».';
        if (operation === 'comparison') return 'Для выбранных конкурентов сначала нужен сводный анализ. Откройте карточку и создайте его из доступных анализов источников.';
    }
    return error.message;
}

async function loadList() {
    const version = ++state.listVersion;
    const items = await api.get('/competitors');
    if (version !== state.listVersion) return;
    state.competitors = items;
    state.compareSelection = new Set([...state.compareSelection].filter(id => items.some(item => item.id === id)));
    redraw();
    // List contract has no counts/date: hydrate cards through existing detail API.
    await Promise.all(items.map(async item => {
        try {
            const detail = await api.get(competitorPath(item.id));
            if (version === state.listVersion) { state.competitorDetails[item.id] = detail; redraw(); }
        } catch {
            if (version === state.listVersion) { state.competitorDetails[item.id] = null; redraw(); }
        }
    }));
}

function clearSource() {
    ++state.sourceVersion;
    state.activeSourceId = null;
    state.activeSourceDetail = null;
    state.activeAnalysis = null;
    state.analysisContext = '';
    state.loading.delete('source-detail');
}

async function selectCompetitor(id, preferredSource = null) {
    const version = ++state.competitorVersion;
    state.activeCompetitorId = id;
    state.activeCompetitorDetail = null;
    state.analysisHistory = [];
    state.analysisMode = 'source';
    state.error = '';
    clearSource();
    state.loading.add('detail');
    redraw();
    try {
        const [detail, history] = await Promise.all([api.get(competitorPath(id)), api.get(`${competitorPath(id)}/analyses`)]);
        if (version !== state.competitorVersion || id !== state.activeCompetitorId) return;
        state.activeCompetitorDetail = detail;
        state.competitorDetails[id] = detail;
        state.analysisHistory = orderedAnalyses(history);
        state.loading.delete('detail');
        const selected = detail.sources.find(s => s.id === preferredSource) || detail.sources[0];
        if (selected) await selectSource(selected.id);
        redraw();
        await Promise.all(detail.sources.filter(s => s.id !== selected?.id).map(async source => {
            try {
                const result = await api.get(sourcePath(source.id));
                if (version === state.competitorVersion) { state.sourceDetails[source.id] = result; redraw(); }
            } catch {
                if (version === state.competitorVersion) { state.sourceDetails[source.id] = null; redraw(); }
            }
        }));
    } catch (error) {
        if (version === state.competitorVersion) state.error = message(error);
    } finally {
        if (version === state.competitorVersion) { state.loading.delete('detail'); redraw(); }
    }
}

async function selectSource(id) {
    const version = ++state.sourceVersion;
    const competitorVersion = state.competitorVersion;
    state.activeSourceId = id;
    state.activeSourceDetail = null;
    state.activeAnalysis = null;
    state.analysisMode = 'source';
    state.loading.add('source-detail');
    state.error = '';
    redraw();
    try {
        const detail = await api.get(sourcePath(id));
        if (version !== state.sourceVersion || competitorVersion !== state.competitorVersion || id !== state.activeSourceId) return;
        state.sourceDetails[id] = detail;
        state.activeSourceDetail = detail;
        state.activeAnalysis = currentAnalyses(detail)[0] || null;
        state.analysisContext = detail.source.label;
    } catch (error) {
        if (version === state.sourceVersion) state.error = message(error);
    } finally {
        if (version === state.sourceVersion) { state.loading.delete('source-detail'); redraw(); }
    }
}

async function mutation(key, work, after, operation = null) {
    if (state.loading.has(key)) return;
    state.loading.add(key);
    state.error = '';
    state.status = 'Запрос выполняется…';
    redraw();
    try {
        const result = await work();
        await after(result);
        state.status = 'Операция завершена';
    } catch (error) {
        state.error = message(error, operation);
        state.status = '';
    } finally { state.loading.delete(key); redraw(); }
}

async function reloadActive(id, competitorVersion, sourceVersion, preferred) {
    // Never let a completed mutation navigate away from a newer user selection.
    if (id === state.activeCompetitorId && competitorVersion === state.competitorVersion && sourceVersion === state.sourceVersion) {
        await selectCompetitor(id, preferred);
    } else if (id === state.activeCompetitorId && competitorVersion === state.competitorVersion) {
        const [detail, history] = await Promise.all([api.get(competitorPath(id)), api.get(`${competitorPath(id)}/analyses`)]);
        if (competitorVersion === state.competitorVersion) {
            state.activeCompetitorDetail = detail;
            state.analysisHistory = orderedAnalyses(history);
            if (state.activeSourceId && !detail.sources.some(source => source.id === state.activeSourceId)) {
                clearSource();
                if (detail.sources.length) await selectSource(detail.sources[0].id);
            }
        }
    }
    await loadList();
}

function closeForm() {
    if (state.loading.has('form')) return;
    $('editor-dialog').close();
    state.modal = null;
}

function field(name, label, value = '', options = {}) {
    const wrapper = el('div', null, 'field');
    const caption = el('label', label);
    const input = el(options.multiline ? 'textarea' : 'input');
    input.id = `field-${name}`;
    input.name = name;
    if (!options.multiline) input.type = options.type || 'text';
    if (input.type !== 'file') input.value = value || '';
    input.required = !!options.required;
    if (options.max) input.maxLength = options.max;
    if (options.min) input.minLength = options.min;
    if (options.accept) input.accept = options.accept;
    caption.htmlFor = input.id;
    wrapper.append(caption, input);
    $('editor-fields').append(wrapper);
    return input;
}

function openForm(kind, sourceType = 'text') {
    if (state.loading.has('form')) return;
    const competitor = state.activeCompetitorDetail;
    state.modal = { kind, sourceType, competitorId: state.activeCompetitorId, competitorVersion: state.competitorVersion, sourceVersion: state.sourceVersion };
    $('editor-fields').replaceChildren();
    $('form-error').textContent = '';
    $('form-status').textContent = '';
    $('form-submit').disabled = false;
    $('editor-title').textContent = kind === 'create' ? 'Новый конкурент' : kind === 'edit' ? 'Изменить конкурента' : 'Добавить источник';
    if (kind !== 'source') {
        field('name', 'Название', kind === 'edit' ? competitor.name : '', { required: true, max: 120 });
        field('website_url', 'Сайт (необязательно)', kind === 'edit' ? competitor.website_url : '', { type: 'url' });
        field('niche', 'Ниша (необязательно)', kind === 'edit' ? competitor.niche : '', { max: 160 });
        field('notes', 'Заметки (необязательно)', kind === 'edit' ? competitor.notes : '', { multiline: true, max: 2000 });
    } else {
        const types = el('div', null, 'actions');
        for (const [key, label] of [['text', 'Текст'], ['file', 'Изображение / PDF'], ['url', 'URL']]) types.append(button(label, () => openForm('source', key), false, key === sourceType ? 'active' : ''));
        $('editor-fields').append(types);
        field('label', 'Название источника', '', { max: 120 });
        if (sourceType === 'text') field('text', 'Текст (10–30 000 символов)', '', { multiline: true, required: true, min: 10, max: 30000 });
        if (sourceType === 'url') field('url', 'URL страницы', '', { type: 'url', required: true });
        if (sourceType === 'file') {
            const input = field('file', 'Изображение JPG / PNG / WebP или PDF', '', { type: 'file', required: true, accept: '.jpg,.jpeg,.png,.webp,.pdf,image/jpeg,image/png,image/webp,application/pdf' });
            const info = el('p', 'Выберите файл. Тип и лимиты проверяет сервер.', 'muted');
            input.addEventListener('change', () => { const file = input.files[0]; info.textContent = file ? `${file.name} · ${(file.size / 1024).toFixed(1)} КБ` : 'Файл не выбран'; });
            $('editor-fields').append(info);
        }
    }
    if (!$('editor-dialog').open) $('editor-dialog').showModal();
}

async function submitForm(event) {
    event.preventDefault();
    if (state.loading.has('form') || !state.modal) return;
    const modal = { ...state.modal };
    const data = new FormData($('editor-form'));
    const values = Object.fromEntries(data);
    if (modal.kind !== 'source' && !values.name.trim()) { $('form-error').textContent = 'Введите название.'; return; }
    if (modal.kind === 'source' && modal.sourceType === 'text' && !values.text.trim()) { $('form-error').textContent = 'Введите текст.'; return; }
    state.loading.add('form');
    $('form-error').textContent = '';
    $('form-status').textContent = modal.kind === 'source' ? 'Источник отправляется · сервер подготовит контент, выполнит анализ и сохранит результат…' : 'Сохранение…';
    for (const control of $('editor-form').elements) control.disabled = true;
    let result;
    try {
        if (modal.kind !== 'source') {
            const payload = { name: values.name.trim(), ...Object.fromEntries(['website_url', 'niche', 'notes'].map(key => [key, values[key].trim() || null])) };
            result = modal.kind === 'create' ? await api.post('/competitors', payload) : await api.patch(competitorPath(modal.competitorId), payload);
        } else {
            const prefix = `${competitorPath(modal.competitorId)}/sources`;
            const label = values.label.trim();
            if (modal.sourceType === 'file') {
                const payload = new FormData();
                payload.append('file', values.file);
                if (label) payload.append('label', label);
                result = await api.post(`${prefix}/file`, payload);
            } else result = await api.post(`${prefix}/${modal.sourceType}`, {
                ...(label ? { label } : {}), [modal.sourceType === 'text' ? 'text' : 'url']: modal.sourceType === 'text' ? values.text : values.url.trim(),
            });
        }
        state.loading.delete('form');
        closeForm();
        if (modal.kind === 'create') { await loadList(); await selectCompetitor(result.id); }
        else await reloadActive(modal.competitorId, modal.competitorVersion, modal.sourceVersion, result.source?.id || state.activeSourceId);
    } catch (error) {
        $('form-error').textContent = message(error);
        $('form-status').textContent = '';
        // Ingestion persists retryable sources before AI; refresh without closing the form.
        if (modal.kind === 'source') {
            try {
                await reloadActive(modal.competitorId, modal.competitorVersion, modal.sourceVersion, state.activeSourceId);
                if (state.modal?.competitorId === state.activeCompetitorId) {
                    state.modal.competitorVersion = state.competitorVersion;
                    state.modal.sourceVersion = state.sourceVersion;
                }
            } catch { /* Preserve original actionable error. */ }
            $('form-status').textContent = 'Если источник уже появился в списке, закройте форму и используйте «Повторить анализ».';
        }
    } finally {
        state.loading.delete('form');
        for (const control of $('editor-form').elements) control.disabled = false;
        redraw();
    }
}

const actions = {
    selectCompetitor, selectSource,
    tab(key) { state.analysisTab = key; redraw(); },
    history(id) {
        const items = state.analysisMode === 'aggregate' ? state.analysisHistory.filter(a => a.analysis_type === 'aggregate') : state.activeSourceDetail?.analyses || [];
        state.activeAnalysis = items.find(item => item.id === id) || null;
        redraw();
    },
    async sourceOperation(operation) {
        const id = state.activeSourceId, competitor = state.activeCompetitorId;
        const cv = state.competitorVersion, sv = state.sourceVersion;
        if (!id || (operation === 'refresh' && state.activeSourceDetail?.source.source_type !== 'url')) return;
        await mutation(`source:${id}`, async () => {
            try { return await api.post(`${sourcePath(id)}/${operation}`); }
            catch (error) {
                // Refresh may persist a new snapshot before an AI error.
                try { await reloadActive(competitor, cv, sv, id); } catch { /* Report the original error. */ }
                throw error;
            }
        },
            () => reloadActive(competitor, cv, sv, id));
    },
    async deleteSource() {
        const id = state.activeSourceId, competitor = state.activeCompetitorId;
        const cv = state.competitorVersion, sv = state.sourceVersion;
        if (!id || !window.confirm('Удалить источник, его snapshots и анализы?')) return;
        await mutation(`source:${id}`, () => api.delete(sourcePath(id)), async () => {
            delete state.sourceDetails[id];
            await reloadActive(competitor, cv, sv, null);
        });
    },
    async deleteCompetitor() {
        const id = state.activeCompetitorId, cv = state.competitorVersion;
        if (!id || !window.confirm('Удалить конкурента и все его источники и анализы?')) return;
        await mutation(`competitor:${id}`, () => api.delete(competitorPath(id)), async () => {
            await loadList();
            delete state.competitorDetails[id];
            if (cv === state.competitorVersion && id === state.activeCompetitorId) {
                if (state.competitors.length) await selectCompetitor(state.competitors[0].id);
                else { ++state.competitorVersion; state.activeCompetitorId = null; state.activeCompetitorDetail = null; state.analysisHistory = []; clearSource(); }
            }
        });
    },
    async aggregate() {
        const id = state.activeCompetitorId, cv = state.competitorVersion, sv = state.sourceVersion;
        if (!id) return;
        await mutation(`aggregate:${id}`, () => api.post(`${competitorPath(id)}/aggregate-analysis`), async result => {
            await loadList();
            if (cv === state.competitorVersion && sv === state.sourceVersion) {
                state.analysisHistory.unshift(result);
                state.activeAnalysis = result;
                state.analysisMode = 'aggregate';
                state.analysisContext = 'Сводный анализ';
            }
        }, 'aggregate');
    },
    latestAggregate() {
        const result = state.analysisHistory.find(item => item.analysis_type === 'aggregate');
        if (!result) { state.error = 'Сводного анализа пока нет. Создайте его из анализов источников.'; redraw(); return; }
        ++state.sourceVersion; // Invalidate a pending source selection before showing aggregate.
        state.loading.delete('source-detail');
        state.activeAnalysis = result;
        state.analysisMode = 'aggregate';
        state.analysisContext = 'Сводный анализ';
        redraw();
    },
    compareSelect(id, selected) {
        if (selected && state.compareSelection.size < 5) state.compareSelection.add(id);
        else state.compareSelection.delete(id);
        state.comparisonResult = null;
        $('compare-error').textContent = '';
        redraw();
    },
};

$('new-competitor').addEventListener('click', () => openForm('create'));
$('edit-competitor').addEventListener('click', () => openForm('edit'));
$('delete-competitor').addEventListener('click', actions.deleteCompetitor);
$('add-source').addEventListener('click', () => openForm('source'));
$('aggregate').addEventListener('click', actions.aggregate);
$('latest-aggregate').addEventListener('click', actions.latestAggregate);
$('editor-form').addEventListener('submit', submitForm);
$('form-cancel').addEventListener('click', closeForm);
$('editor-dialog').addEventListener('cancel', event => { if (state.loading.has('form')) event.preventDefault(); else state.modal = null; });
$('open-compare').addEventListener('click', () => { state.compareOpen = true; redraw(); $('compare-dialog').showModal(); });
$('close-compare').addEventListener('click', () => { $('compare-dialog').close(); state.compareOpen = false; });
$('compare-dialog').addEventListener('close', () => { state.compareOpen = false; });
$('compare-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (state.loading.has('compare') || state.compareSelection.size < 2 || state.compareSelection.size > 5) return;
    state.loading.add('compare');
    state.comparisonResult = null;
    $('compare-error').textContent = '';
    redraw();
    try { state.comparisonResult = await api.post('/comparisons', { competitor_ids: [...state.compareSelection] }); }
    catch (error) { $('compare-error').textContent = message(error, 'comparison'); }
    finally { state.loading.delete('compare'); redraw(); }
});
$('retry-load').addEventListener('click', initialize);
async function initialize() {
    if (state.loading.has('initial')) return;
    state.loading.add('initial');
    state.error = '';
    redraw();
    try {
        await loadList();
        if (state.competitors.length) await selectCompetitor(state.activeCompetitorId || state.competitors[0].id);
    } catch (error) { state.error = message(error); }
    finally { state.loading.delete('initial'); redraw(); }
}
initialize();
