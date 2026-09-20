// libs/core.js
// @name: core
// @desc: createCanvas(container,w,h) — 创建Canvas并添加到容器; GradientCache() — 渐变缓存; GameLoop(update,render) — 帧循环,update(dt,fps)每帧更新,render()每帧绘制; Keyboard() — 键盘状态,返回对象有isDown(key)方法; MousePosition(canvas) — 鼠标/触摸坐标,返回对象可直接用.x/.y/.clicked/.pressed,也可调.get()获取
// @example: var kb = Keyboard(); if (kb.isDown('w')) { player.y -= speed; }  // isDown 传入小写字母
// @example: var mp = MousePosition(canvas); if (mp.clicked) { var cx = mp.x; var cy = mp.y; }  // 直接访问属性或调 mp.get()

function createCanvas(container, w, h) {
    var canvas = document.createElement('canvas');
    // ★ 类型检查：防止 __aicp_width__/__aicp_height__ 被函数覆盖
    function _num(v) { return (typeof v === 'number' && v > 0) ? v : null; }
    canvas.width = _num(w) || _num(window.__aicp_width__) || _num(__aicp_width__) || 800;
    canvas.height = _num(h) || _num(window.__aicp_height__) || _num(__aicp_height__) || 600;
    canvas.style.display = 'block';
    canvas.style.touchAction = 'none';
    container.appendChild(canvas);
    return canvas;
}

function GradientCache() {
    var cache = {};
    return {
        get: function(key, fn, ctx) { if (!cache[key]) cache[key] = fn(ctx); return cache[key]; },
        clear: function() { cache = {}; }
    };
}

function GameLoop(update, render) {
    var running = true, lastTime = performance.now(), fps = 0, frames = 0, lastFpsUpdate = lastTime;
    function loop() {
        if (!running) return;
        var now = performance.now(), dt = Math.min((now - lastTime) / 1000, 0.1);
        lastTime = now;
        frames++;
        if (now - lastFpsUpdate >= 1000) { fps = frames; frames = 0; lastFpsUpdate = now; }
        update(dt, fps);
        render();
        requestAnimationFrame(loop);
    }
    loop();
    return { stop: function() { running = false; }, getFPS: function() { return fps; } };
}

function Keyboard() {
    var keys = {};
    function down(e) { keys[e.key.toLowerCase()] = true; }
    function up(e) { keys[e.key.toLowerCase()] = false; }
    document.addEventListener('keydown', down); document.addEventListener('keyup', up);
    window.addEventListener('keydown', down); window.addEventListener('keyup', up);
    return { 
        isDown: function(k) { return !!keys[k.toLowerCase()]; },
        isPressed: function(k) { return !!keys[k.toLowerCase()]; }
    };
}

function MousePosition(canvas) {
    var pos = { x: 0, y: 0, clicked: false, pressed: false };
    function update(e) { var r = canvas.getBoundingClientRect(); pos.x = e.clientX - r.left; pos.y = e.clientY - r.top; }
    canvas.addEventListener('mousedown', function(e) { update(e); pos.pressed = true; pos.clicked = true; });
    canvas.addEventListener('mouseup', function(e) { update(e); pos.pressed = false; });
    canvas.addEventListener('mousemove', function(e) { update(e); });
    canvas.addEventListener('touchstart', function(e) { e.preventDefault(); update(e.touches[0]); pos.pressed = true; pos.clicked = true; }, {passive: false});
    canvas.addEventListener('touchend', function() { pos.pressed = false; });
    canvas.addEventListener('touchmove', function(e) { e.preventDefault(); update(e.touches[0]); }, {passive: false});
    
    // ★ 简化：只用一个 result 对象，用 Object.defineProperty 定义所有属性
    var result = {};
    
    Object.defineProperties(result, {
        'x': { get: function() { return pos.x; }, enumerable: true },
        'y': { get: function() { return pos.y; }, enumerable: true },
        'clicked': { 
            get: function() { return pos.clicked; }, 
            set: function(v) { pos.clicked = v; },
            enumerable: true
        },
        'pressed': { get: function() { return pos.pressed; }, enumerable: true }
    });
    
    result.get = function() { 
        return { x: pos.x, y: pos.y, clicked: pos.clicked, pressed: pos.pressed }; 
    };
    result.consumeClick = function() { 
        var v = pos.clicked; 
        pos.clicked = false; 
        return v; 
    };
    
    return result;
}