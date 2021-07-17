function* serialize(name, o) {
    if (o instanceof Array) {
        for (const v of o) { // of *not* in, see: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/for...of#difference_between_for...of_and_for...in
            yield [name, o.toString()];
        }
    }
    else if (o instanceof Object) {
        for (const v in o) {
            yield* serialize(v, o[v]);
        }
    }
    else {
        yield [name, o.toString()];
    }
}
function* serializefd(name, o) {
    if (o instanceof Array) {
        for (const v of o) { // of *not* in, see: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/for...of#difference_between_for...of_and_for...in
            yield [name, o];
        }
    }
    else if (o instanceof Object) {
        for (const v in o) {
            yield* serializefd(v, o[v]);
        }
    }
    else {
        yield [name, o];
    }
}
function toFormData($data) {
    const fd = new FormData();
    for (const [key, val] of serializefd('', $data)) {
        fd.append(key, val);
    }
    return fd;
}
function toParams($data) {
    const $uv = new URLSearchParams();
    for (const [key, val] of serialize('', $data)) {
        $uv.append(key, val);
    }
    return $uv;
}
function process_request($request) {
    return fetch($request).then(resp => {
        if (resp.ok) {
            return resp.json();
        }
        if (resp.status === 400) {
            return resp.json().then(err => Promise.reject(err));
        }
        return resp.text().then(txt => Promise.reject(txt));
    });
}
function get(url, $data) {
    const $request = new Request(`${url}?${toParams($data)}`);
    return process_request($request);
}
function post(url, $data) {
    const headers = new Headers({
        'Content-Type': 'application/json; charset=utf-8',
    });
    // see https://developer.mozilla.org/en-US/docs/Web/API/Request/Request
    const $request = new Request(`${url}`, {
        method: "POST",
        body: JSON.stringify($data),
        headers: headers
    });
    return process_request($request);
}
function formdata(url, $data) {
    const headers = new Headers({
        'Content-Type': 'multipart/form-data; charset=utf-8',
    });
    // see https://developer.mozilla.org/en-US/docs/Web/API/Request/Request
    const $request = new Request(`${url}`, {
        method: "POST",
        body: toFormData($data),
        headers: headers
    });
    return process_request($request);
}
