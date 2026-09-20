// libs/tween.js
// @name: Tween
// @desc: Tween(obj,props,duration,easing,onComplete) — 缓动动画。obj:{x,y}目标对象,props:{x:100,y:200}目标值,duration:毫秒,easing:'ease'/'linear'/'bounce'。调用 tween.start()开始。
// @example: var player = {x:0, y:0}; var tween = Tween(player, {x:300, y:200}, 1000, 'ease', function() { console.log('done'); }); tween.start();
var Tween = function(obj, props, duration, easing, onComplete) {
    var startTime = null;
    var ease = easing || 'ease';
    var dur = (duration !== undefined) ? duration : 500;
    var callback = onComplete || function() {};
    var running = false;
    var startProps = {};

    // ★ 记录初始值（在 start 时记录，而不是创建时）
    function _recordStartProps() {
        startProps = {};
        for (var key in props) {
            startProps[key] = (obj[key] !== undefined) ? obj[key] : 0;
        }
    }

    function easeFunc(t) {
        if (ease === 'linear') return t;
        if (ease === 'bounce') {
            if (t < 1 / 2.75) return 7.5625 * t * t;
            else if (t < 2 / 2.75) { t -= 1.5 / 2.75; return 7.5625 * t * t + 0.75; }
            else if (t < 2.5 / 2.75) { t -= 2.25 / 2.75; return 7.5625 * t * t + 0.9375; }
            else { t -= 2.625 / 2.75; return 7.5625 * t * t + 0.984375; }
        }
        return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
    }

    function update(now) {
        if (!startTime) startTime = now;
        var elapsed = now - startTime;
        var progress = Math.min(elapsed / dur, 1);
        var eased = easeFunc(progress);

        for (var key in props) {
            obj[key] = startProps[key] + (props[key] - startProps[key]) * eased;
        }

        if (progress < 1) {
            requestAnimationFrame(update);
        } else {
            running = false;
            callback();
        }
    }

    function start() {
        if (running) return;
        running = true;
        startTime = null;
        _recordStartProps();  // ★ 每次 start 都重新记录初始值
        requestAnimationFrame(update);
    }

    function stop() {
        running = false;
        startTime = null;
    }

    return { 
        start: start,
        stop: stop  // ★ 新增 stop 方法
    };
};