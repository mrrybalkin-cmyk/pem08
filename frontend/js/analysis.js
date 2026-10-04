import { $, el, section, empty, button, date } from './dom.js';
import { state, currentAnalyses } from './state.js';

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
        root.append(empty(state.activeSourceId ? 'Для текущего snapshot пока нет анализа' : 'Выберите источник для анализа',
            state.activeSourceId ? 'Повторите анализ текущего содержимого источника.' : 'Добавьте источник или откройте последний сводный результат.'));
        if (state.activeSourceDetail) root.append(button('Повторить анализ', () => actions.sourceOperation('reanalyze'), state.loading.has(`source:${state.activeSourceId}`)));
        return;
    }
    const result = analysis.result_json;
    root.append(el('h2', state.analysisContext), el('p', `${analysis.analysis_type === 'aggregate' ? 'Сводный анализ' : 'Анализ источника'} · ${date(analysis.created_at)}`, 'muted'));
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
    const summary = section('Ключевые выводы', result.executive_summary);
    summary.classList.add('summary');
    summary.append(section('Позиционирование', result.positioning), section('Целевая аудитория', result.target_audience),
        section('Ценностные предложения', result.value_propositions));
    root.append(summary);
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
        card.append(el('h3', label), el('strong', `${metric.score}/10`), meter, el('p', metric.rationale));
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
    if (state.analysisTab === 'overview') for (const [key, label] of Object.entries(overview)) body.append(section(label, result[key]));
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
