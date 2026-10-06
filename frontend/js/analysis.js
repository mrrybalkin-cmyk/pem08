import { $, el, section, empty, button, date, disclosure, reportSummary } from './dom.js';
import { state, currentAnalyses, currentSnapshot } from './state.js';

export const metrics = {
    positioning_clarity: 'Ясность позиционирования',
    value_proposition: 'Ценностное предложение',
    trust: 'Доверие',
    cta_strength: 'Сила призыва к действию',
    visual_consistency: 'Визуальная согласованность',
    ux_clarity: 'Понятность интерфейса',
};
const overview = {
    positioning: 'Позиционирование', target_audience: 'Целевая аудитория',
    value_propositions: 'Ценностные предложения', differentiators: 'Отличия',
    strengths: 'Сильные стороны', gaps: 'Пробелы', marketing_messages: 'Маркетинговые сообщения',
};

export function renderAnalysis(actions) {
    const root = $('analysis-content');
    root.replaceChildren();
    const analysis = state.activeAnalysis;
    if (!analysis) {
        if (state.activeSourceDetail?.source.source_type === 'url' && !currentSnapshot(state.activeSourceDetail)) {
            const busy = state.loading.has(`source:${state.activeSourceId}`);
            root.append(empty('Источник добавлен, но содержимое пока не получено.', 'Не удалось получить страницу. Повторите получение или добавьте скриншот как отдельный источник.'),
                el('p', state.activeSourceDetail.source.url, 'prose'),
                button('Повторить получение', () => actions.sourceOperation('refresh'), busy),
                button('Загрузить скриншот', actions.screenshotFallback, busy),
                button('Удалить источник', actions.deleteSource, busy, 'danger'));
            return;
        }
        root.append(empty(state.activeSourceId ? 'Для текущего snapshot пока нет анализа' : 'Выберите источник для анализа',
            state.activeSourceId ? 'Повторите анализ текущего содержимого источника.' : 'Добавьте источник или откройте последний сводный результат.'));
        if (state.activeSourceDetail) root.append(button('Повторить анализ', () => actions.sourceOperation('reanalyze'), state.loading.has(`source:${state.activeSourceId}`)));
        return;
    }
    const result = analysis.result_json;
    const aggregate = analysis.analysis_type === 'aggregate';
    const context = el('div', null, `report-context ${aggregate ? 'report-context--aggregate' : 'report-context--source'}`);
    context.append(el('p', aggregate ? 'Сводный анализ конкурента' : 'Анализ отдельного источника', 'mode-badge'),
        el('h2', state.analysisContext),
        el('p', `Конкурент: ${state.activeCompetitorDetail?.name || 'не выбран'}`, 'report-subtitle'),
        el('p', `Сохранённый результат · ${date(analysis.created_at)}`, 'muted'));
    if (aggregate) context.append(el('p', 'Объединяет анализы источников на момент создания. Покрытие и оговорки — во вкладке «Ограничения».', 'muted'));
    root.append(context);
    const history = state.analysisMode === 'aggregate' ? state.analysisHistory.filter(a => a.analysis_type === 'aggregate') : (state.activeSourceDetail?.analyses || []);
    const select = el('select');
    select.id = 'analysis-history';
    select.setAttribute('aria-label', 'История анализов');
    for (const item of history) {
        const option = el('option', `${date(item.created_at)}${item.snapshot_id && !currentAnalyses(state.activeSourceDetail).some(a => a.id === item.id) ? ' · предыдущий snapshot' : ''}`);
        option.value = item.id;
        option.selected = item.id === analysis.id;
        select.append(option);
    }
    select.addEventListener('change', () => actions.history(select.value));
    root.append(select);
    if (analysis.analysis_type === 'source' && !currentAnalyses(state.activeSourceDetail).some(a => a.id === analysis.id)) {
        root.append(el('p', 'Исторический результат — не анализ текущего snapshot.', 'notice'));
    }
    root.append(reportSummary('Ключевые выводы', result.executive_summary));
    const scores = el('section', null, 'scorecard');
    scores.setAttribute('aria-label', 'Оценки');
    for (const [key, label] of Object.entries(metrics)) {
        const metric = result.scorecard[key];
        if (!metric) continue;
        const card = el('div', null, 'score');
        const meter = el('progress');
        meter.max = 10;
        meter.value = metric.score;
        meter.setAttribute('aria-label', label);
        card.append(el('h3', label), el('strong', `${metric.score}/10`), meter,
            disclosure('Обоснование', el('p', metric.rationale, 'prose')));
        scores.append(card);
    }
    root.append(scores);
    const tabs = el('div', null, 'tabs');
    const labels = { overview: 'Обзор', evidence: 'Доказательства', actions: 'Действия', limitations: 'Ограничения' };
    for (const [key, label] of Object.entries(labels)) {
        const tab = button(label, () => actions.tab(key), false, state.analysisTab === key ? 'active' : '');
        tab.setAttribute('aria-pressed', String(state.analysisTab === key));
        tabs.append(tab);
    }
    root.append(tabs);
    const body = el('div', null, 'analysis-body');
    if (state.analysisTab === 'overview') for (const [key, label] of Object.entries(overview)) {
        const content = section(label, result[key]);
        if (['differentiators', 'marketing_messages'].includes(key)) {
            content.firstChild.remove();
            body.append(disclosure(label, content, !matchMedia('(max-width: 767px)').matches));
        } else body.append(content);
    }
    if (state.analysisTab === 'actions') body.append(section('Возможности', result.opportunities), section('Рекомендуемые действия', result.recommended_actions));
    if (state.analysisTab === 'limitations') body.append(section('Ограничения', result.limitations));
    if (state.analysisTab === 'evidence') {
        if (!result.evidence.length) body.append(empty('Доказательства не представлены', 'Учитывайте ограничения и полноту исходных данных.'));
        for (const item of result.evidence) {
            const card = section(item.category, item.finding);
            card.append(el('blockquote', item.evidence), el('p', `Источник: ${item.source_hint}`, 'muted'),
                el('p', `Уверенность: ${{ low: 'низкая', medium: 'средняя', high: 'высокая' }[item.confidence] || item.confidence}`, 'muted'));
            body.append(card);
        }
    }
    root.append(body);
}
