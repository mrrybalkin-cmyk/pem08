export class ApiError extends Error {
    constructor(message, code, status) {
        super(message);
        this.code = code;
        this.status = status;
    }
}

async function request(method, path, body) {
    let response;
    try {
        response = await fetch(`/api/v2${path}`, {
            method,
            headers: body instanceof FormData || body === undefined ? {} : { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
        });
    } catch {
        throw new ApiError('Не удалось связаться с сервером. Повторите попытку.', 'NETWORK_ERROR', 0);
    }
    if (response.status === 204) {
        await response.text();
        return null;
    }
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
        const message = typeof data?.error?.message === 'string' ? data.error.message :
            response.status === 422 ? 'Проверьте поля формы и ограничения данных.' : 'Операция не выполнена. Повторите попытку.';
        throw new ApiError(message, data?.error?.code || 'REQUEST_ERROR', response.status);
    }
    if (data === null) throw new ApiError('Сервер вернул некорректный ответ.', 'INVALID_RESPONSE', response.status);
    return data;
}

export const api = {
    get: path => request('GET', path),
    post: (path, body) => request('POST', path, body),
    patch: (path, body) => request('PATCH', path, body),
    delete: path => request('DELETE', path),
};
export const idPath = id => encodeURIComponent(id);
