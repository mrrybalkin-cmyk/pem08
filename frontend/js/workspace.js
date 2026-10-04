import { $, el, button, safeUrl, link, date, empty } from './dom.js';
import { state, currentSnapshot, currentAnalyses } from './state.js';
import { idPath } from './api.js';
import { renderAnalysis } from './analysis.js';
import { renderCompare } from './compare.js';

export const sourceLabels = { text: 'Текст', image: 'Изображение', pdf: 'PDF', url: 'Веб-страница' };
export function render(actions) {
    $('workspace-error').textContent = state.error;
    $('workspace-status').textContent = state.status;
    $('competitor-list').replaceChildren();
    $('initial-loading').hidden = !state.loading.has('initial');
    if (!state.competitors.length && !state.loading.has('initial')) $('competitor-list').append(empty('Пока нет конкурентов', 'Создайте первую карточку, чтобы собрать источники и начать анализ.'));
    for (const item of state.competitors) {
        const card = button('', () => actions.selectCompetitor(item.id), false, `competitor-card ${item.id === state.activeCompetitorId ? 'active' : ''}`);
        card.setAttribute('aria-pressed', String(item.id === state.activeCompetitorId));
        card.append(el('strong', item.name), el('span', safeUrl(item.website_url)?.hostname || 'Сайт не указан', 'muted'));
        const detail = state.competitorDetails[item.id];
        card.append(el('small', detail ? `${detail.sources.length} источников · анализ: ${date(detail.latest_analysis?.created_at)}` : detail === null ? 'Сведения недоступны · откройте карточку для повтора' : 'Загрузка сведений…', 'muted'));
        $('competitor-list').append(card);
    }
    const competitor = state.activeCompetitorDetail;
    $('competitor-name').textContent = competitor?.name || 'Источники';
    $('competitor-description').textContent = competitor?.niche || 'Контекст для доказательного анализа';
    $('competitor-notes').textContent = competitor?.notes || '';
    $('competitor-website').replaceChildren();
    if (competitor?.website_url) $('competitor-website').append(link(competitor.website_url));
    const competitorBusy = state.loading.has(`competitor:${state.activeCompetitorId}`);
    for (const id of ['edit-competitor', 'delete-competitor', 'add-source', 'aggregate', 'latest-aggregate']) $(id).disabled = !competitor || competitorBusy;
    $('delete-competitor').textContent = competitorBusy ? 'Операция выполняется…' : 'Удалить конкурента';
    $('source-list').replaceChildren();
    if (state.loading.has('detail')) $('source-list').append(el('p', 'Загрузка источников…', 'muted'));
    else if (!competitor) $('source-list').append(empty('Конкурент не выбран', 'Откройте карточку слева или создайте конкурента.'));
    else if (!competitor.sources.length) $('source-list').append(empty('У конкурента нет источников', 'Добавьте текст, изображение, PDF или веб-страницу.'));
    for (const source of competitor?.sources || []) {
        const detail = state.sourceDetails[source.id];
        const card = button('', () => actions.selectSource(source.id), false, `source-card ${source.id === state.activeSourceId ? 'active' : ''}`);
        card.setAttribute('aria-pressed', String(source.id === state.activeSourceId));
        const busy = state.loading.has(`source:${source.id}`);
        card.append(el('span', sourceLabels[source.source_type], `badge source-type source-type-${source.source_type}`), el('strong', source.label),
            el('small', source.original_filename || safeUrl(source.url)?.hostname || 'Ручной ввод', 'muted'),
            el('small', date(currentSnapshot(detail)?.captured_at || source.created_at), 'muted'),
            el('small', busy ? 'Обрабатывается…' : detail ? currentAnalyses(detail).length ? 'Проанализирован' : 'Требуется анализ' : detail === null ? 'Состояние недоступно · выберите источник для повтора' : 'Загрузка состояния…', 'source-status'));
        $('source-list').append(card);
    }
    renderPreview(actions);
    $('aggregate').textContent = state.loading.has(`aggregate:${state.activeCompetitorId}`) ? 'Сводный анализ выполняется…' : 'Сводный анализ';
    $('aggregate').disabled ||= state.loading.has(`aggregate:${state.activeCompetitorId}`);
    renderAnalysis(actions);
    renderCompare(actions);
}

function renderPreview(actions) {
    const root = $('source-preview');
    root.replaceChildren();
    if (state.loading.has('source-detail')) { root.append(el('p', 'Загрузка источника…')); return; }
    const detail = state.activeSourceDetail;
    if (!detail) { root.append(empty('Предпросмотр источника', 'Выберите источник из списка.')); return; }
    const source = detail.source;
    const snapshot = currentSnapshot(detail);
    root.append(el('h3', source.label));
    const busy = state.loading.has(`source:${source.id}`);
    const controls = el('div', null, 'actions');
    controls.append(button('Повторить анализ', () => actions.sourceOperation('reanalyze'), busy));
    if (source.source_type === 'url') controls.append(button('Обновить страницу', () => actions.sourceOperation('refresh'), busy));
    controls.append(button('Удалить источник', actions.deleteSource, busy, 'danger'));
    root.append(controls);
    if (busy) root.append(el('p', 'Запрос выполняется · подготовка, анализ и сохранение на сервере…', 'notice'));
    if (!snapshot) { root.append(empty('Snapshot отсутствует', 'Содержимое источника пока недоступно.')); return; }
    root.append(el('p', `Захвачено: ${date(snapshot.captured_at)}`, 'muted'));
    if (source.source_type === 'url') {
        root.append(link(snapshot.final_url || source.url), el('h4', snapshot.title || 'Заголовок отсутствует'), el('p', snapshot.meta_description || 'Описание отсутствует'));
    }
    if (source.source_type === 'image' || (source.source_type === 'url' && snapshot.screenshot_path)) {
        const image = el('img');
        image.alt = source.source_type === 'image' ? 'Загруженное изображение' : 'Снимок веб-страницы';
        image.src = `/api/v2/sources/${idPath(source.id)}/snapshots/${idPath(snapshot.id)}/artifact`;
        image.addEventListener('error', () => image.replaceWith(el('p', 'Изображение недоступно. Повторите анализ или обновите источник.', 'muted')));
        root.append(image);
    }
    if (['image', 'pdf'].includes(source.source_type)) root.append(el('p', source.original_filename || 'Файл', 'muted'));
    const meta = snapshot.metadata_json || {};
    if (source.source_type === 'pdf') {
        root.append(el('p', `Страниц: ${meta.page_count ?? 'неизвестно'} · размер: ${meta.size_bytes ? `${(meta.size_bytes / 1024).toFixed(1)} КБ` : 'неизвестно'}`),
            el('p', `Проанализированы страницы: ${(meta.selected_pages || []).join(', ') || 'не указаны'}`, 'muted'));
        if (meta.partial_analysis) root.append(el('p', 'Частичный анализ PDF: использованы выбранные страницы.', 'notice'));
    }
    if (meta.text_truncated) root.append(el('p', 'Текст ограничен при подготовке snapshot. Учитывайте ограничения анализа.', 'notice'));
    if (snapshot.extracted_text) root.append(el('pre', snapshot.extracted_text, 'text-preview'));
    else if (source.source_type !== 'image') root.append(el('p', 'Извлечённый текст отсутствует.', 'muted'));
}
