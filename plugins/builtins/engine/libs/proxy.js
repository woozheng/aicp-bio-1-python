// libs/proxy.js
// @name: proxyFetch
// @desc: proxyFetch(url, options) — 通过后端代理调用外部 API，解决跨域。支持 JSON 和图片二进制。
// @example: var resp = await proxyFetch("https://api.example.com/image", {method:"GET"}); if (resp.isImage) { var img = document.createElement("img"); img.src = URL.createObjectURL(resp.blob); }

async function proxyFetch(url, options) {
    options = options || {};
    var token = localStorage.getItem('aicp_token') || '';
    var resp = await fetch("/api/os/_proxy", {
        method: "POST",
        headers: { 
            "Content-Type": "application/json",
            "X-AICP-Token": token
        },
        body: JSON.stringify({
            action: "forward",
            url: url,
            method: options.method || "GET",
            headers: options.headers || {},
            body: options.body || null
        })
    });
    if (!resp.ok) throw new Error("代理请求失败: " + resp.status);
    var wrapper = await resp.json();
    
    // 图片响应 — 转 blob
    if (wrapper.data && wrapper.data.is_image) {
        var b64 = wrapper.data.image_base64;
        var byteChars = atob(b64);
        var bytes = new Uint8Array(byteChars.length);
        for (var i = 0; i < byteChars.length; i++) bytes[i] = byteChars.charCodeAt(i);
        var blob = new Blob([bytes], {type: wrapper.data.content_type});
        return { 
            isImage: true, 
            blob: blob, 
           json: function() { 
    var d = wrapper.data;
    // 剥掉 _proxy.py 的 {"ok":true,"data":{...}} 封装
    if (d && d.ok && d.data) d = d.data;
    // 剥掉 gateway 的封装
    return Promise.resolve(d);
}
        };
    }
    
    // JSON 响应 — 保持兼容
    return { 
        isImage: false,
        json: function() { return Promise.resolve(wrapper.data); } 
    };
}