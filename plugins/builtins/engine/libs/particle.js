// libs/particle.js
// @name: ParticleSystem
// @desc: ParticleSystem(ctx,config) — 粒子系统。config:{source:{x,y}, gravity, spread, color, size:{min,max}, lifetime, count, shape:'circle'/'rect', fadeOut:true}。用 ps.update()和ps.render()驱动。
// @example: var ps = ParticleSystem(ctx, {source:{x:400,y:50}, gravity:0.5, color:'#87CEEB', count:300, lifetime:180}); function update(dt) { ps.update(); } function render() { ps.render(); }

var ParticleSystem = function(ctx, config) {
    var cfg = config || {};
    var particles = [];
    var source = cfg.source || {x: 0, y: 0};
    var gravity = cfg.gravity || 0;
    var spread = cfg.spread || 10;
    var color = cfg.color || '#fff';
    var sizeMin = (cfg.size && cfg.size.min !== undefined) ? cfg.size.min : 1;
    var sizeMax = (cfg.size && cfg.size.max !== undefined) ? cfg.size.max : 3;
    var lifetime = (cfg.lifetime !== undefined) ? cfg.lifetime : 60;
    var maxCount = (cfg.count !== undefined) ? cfg.count : 100;
    var shape = cfg.shape || 'circle';
    var fadeOut = cfg.fadeOut !== false;
    var emitRate = cfg.emitRate || 3;  // ★ 新增：每帧发射数量

    function create() {
        if (particles.length >= maxCount) particles.shift();
        var angle = (Math.random() - 0.5) * spread * (Math.PI / 180);
        var speed = 1 + Math.random() * 3;
        particles.push({
            x: source.x + (Math.random() - 0.5) * 20,
            y: source.y + (Math.random() - 0.5) * 10,
            vx: Math.sin(angle) * speed,
            vy: Math.cos(angle) * speed + (gravity > 0 ? 0 : -1 - Math.random()),
            size: sizeMin + Math.random() * (sizeMax - sizeMin),
            life: lifetime,
            maxLife: lifetime
        });
    }

    function update() {
        for (var i = particles.length - 1; i >= 0; i--) {
            var p = particles[i];
            p.x += p.vx;
            p.y += p.vy;
            p.vy += gravity;
            p.life--;
            if (p.life <= 0) particles.splice(i, 1);
        }
        for (var j = 0; j < emitRate; j++) create();
    }

    function render() {
        for (var i = 0; i < particles.length; i++) {
            var p = particles[i];
            var alpha = fadeOut ? (p.life / p.maxLife) : 1;
            ctx.globalAlpha = alpha;
            ctx.fillStyle = color;
            if (shape === 'circle') {
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
                ctx.fill();
            } else if (shape === 'rect') {
                ctx.fillRect(p.x, p.y, p.size, p.size);
            } else {
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.globalAlpha = 1;
        }
    }

    return { update: update, render: render };
};