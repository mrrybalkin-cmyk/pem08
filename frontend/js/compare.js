import { $, el, section, empty } from './dom.js';
import { metrics } from './analysis.js';
import { state } from './state.js';

export function renderCompare(actions) {
    const choices = $('compare-choices');
    choices.replaceChildren();
    for (const competitor of state.competitors) {
        const label = el('label', null, 'compare-choice');
        const input = el('input');
        input.type = 'checkbox';
        input.value = competitor.id;
        input.checked = state.compareSelection.has(competitor.id);
        input.disabled = state.loading.has('compare') || (!input.checked && state.compareSelection.size >= 5);
        input.addEventListener('change', () => actions.compareSelect(competitor.id, input.checked));
        label.append(input, el('span', competitor.name));
        choices.append(label);
    }
    $('compare-submit').disabled = state.compareSelection.size < 2 || state.compareSelection.size > 5 || state.loading.has('compare');
    $('compare-submit').textContent = state.loading.has('compare') ? 'Сравнение выполняется…' : 'Сравнить выбранных';
    $('compare-count').textContent = `Выбрано: ${state.compareSelection.size} из 5`;
    const root = $('comparison-result');
    root.replaceChildren();
    if (!state.comparisonResult) {
        root.append(empty('Выберите от 2 до 5 конкурентов для сравнения', 'Для каждого нужен сохранённый сводный анализ. Новые AI-вызовы выполняются только по вашему действию.'));
        return;
    }
    const result = state.comparisonResult;
    root.append(section('Ключевые выводы сравнения', result.executive_summary));
    const wrapper = el('div', null, 'table-scroll');
    const table = el('table');
    table.append(el('caption', 'Матрица общих критериев · оценки 0–10'));
    const criteria = Object.keys(metrics).slice(0, 4);
    const head = el('thead');
    const header = el('tr');
    for (const label of ['Конкурент', ...criteria.map(key => metrics[key])]) {
        const cell = el('th', label);
        cell.scope = 'col';
        header.append(cell);
    }
    head.append(header);
    table.append(head);
    const body = el('tbody');
    for (const row of result.competitors) {
        const tr = el('tr');
        const name = el('th', row.competitor_name);
        name.scope = 'row';
        tr.append(name);
        for (const key of criteria) tr.append(el('td', `${row[key]}/10`));
        body.append(tr);
    }
    table.append(body);
    wrapper.append(table);
    root.append(wrapper);
    for (const row of result.competitors) {
        const card = el('section', null, 'comparison-card');
        card.append(el('h3', row.competitor_name), section('Позиционирование', row.positioning),
            section('Сильные стороны', row.strengths), section('Пробелы', row.gaps));
        root.append(card);
    }
    for (const [key, label] of Object.entries({ shared_patterns: 'Общие паттерны', meaningful_differences: 'Значимые различия',
        market_gaps: 'Рыночные пробелы', opportunities: 'Возможности', limitations: 'Ограничения сравнения' })) root.append(section(label, result[key]));
}
