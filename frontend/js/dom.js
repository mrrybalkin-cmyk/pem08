export const $ = id => document.getElementById(id);
export function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
}
export function button(label, action, disabled = false, className = '') {
    const node = el('button', label, className);
    node.type = 'button';
    node.disabled = disabled;
    node.addEventListener('click', action);
    return node;
}
export function safeUrl(value) {
    try {
        const url = new URL(value);
        return ['http:', 'https:'].includes(url.protocol) ? url : null;
    } catch { return null; }
}
export function link(value) {
    const url = safeUrl(value);
    if (!url) return el('span', value);
    const node = el('a', value);
    node.href = url.href;
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
    return node;
}
export function date(value) {
    if (!value) return 'ещё нет';
    // SQLite timestamps can omit the UTC suffix.
    const parsed = new Date(/[Z+]|-\d\d:\d\d$/.test(value.slice(10)) ? value : `${value}Z`);
    return Number.isNaN(parsed.getTime()) ? 'Дата недоступна' : parsed.toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });
}
export function section(title, value) {
    const node = el('section', null, 'result-section');
    node.append(el('h3', title));
    if (Array.isArray(value) && value.length) {
        const list = el('ul');
        for (const item of value) list.append(el('li', item));
        node.append(list);
    } else node.append(el('p', Array.isArray(value) ? 'Не указано в результате' : value || 'Не указано в результате', 'prose'));
    return node;
}
export function empty(title, detail) {
    const node = el('div', null, 'empty');
    node.append(el('h3', title), el('p', detail));
    return node;
}
