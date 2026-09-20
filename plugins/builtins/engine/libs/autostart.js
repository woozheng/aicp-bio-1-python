// ===== autostart.js =====
// @name: autostart
// @desc: (内部) 自动检测并启动应用，兼容 runner.js 和直接打开 HTML
// @example: // 自动执行，无需手动调用

(function() {
    var container = (typeof __aicp_container__ !== 'undefined') 
        ? __aicp_container__ 
        : document.getElementById('aicp-container');
    
    if (!container) {
        console.error('❌ AICP 容器不存在');
        return;
    }

    var width = (typeof __aicp_width__ !== 'undefined') 
        ? __aicp_width__ 
        : container.clientWidth || window.innerWidth || 800;
    
    var height = (typeof __aicp_height__ !== 'undefined') 
        ? __aicp_height__ 
        : container.clientHeight || window.innerHeight || 600;

    window.__aicp_container__ = container;
    window.__aicp_width__ = width;
    window.__aicp_height__ = height;

    container.style.color = '#e0e0e0';
    container.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif';

    // ★ 工具函数：给元素加滚动
    function _makeScrollable(el) {
        if (!el) return el;
        el.style.maxHeight = '100%';
        el.style.height = '100%';
        el.style.overflowY = 'auto';
        el.style.overflowX = 'hidden';
        el.style.webkitOverflowScrolling = 'touch';
        el.style.scrollBehavior = 'smooth';
        return el;
    }

    setTimeout(function() {
        var hasCanvas = container.querySelector('canvas') !== null;
        
        if (typeof execute === 'function') {
            try {
                var result = execute(container);
                
                // Canvas 游戏：直接返回
                if (hasCanvas || container.querySelector('canvas')) {
                    return;
                }
                
                // 返回 HTMLElement：加滚动后 append
                if (result instanceof HTMLElement && result !== container) {
                    if (result.parentNode) result.parentNode.removeChild(result);
                    _makeScrollable(result);
                    container.appendChild(result);
                    return;
                }
                
                // 返回 Promise：等待 resolve 后加滚动 append
                if (result && typeof result.then === 'function') {
                    container.appendChild(Loading());
                    result.then(function(r) {
                        container.innerHTML = '';
                        if (r instanceof HTMLElement && r !== container) {
                            if (r.parentNode) r.parentNode.removeChild(r);
                            _makeScrollable(r);
                            container.appendChild(r);
                        }
                    }).catch(function(err) {
                        container.innerHTML = '';
                        container.appendChild(ErrorBox(err.message || '执行出错'));
                    });
                    return;
                }
                
                // 返回 undefined：可能已经自己 append 了
                if (container.children.length > 0) {
                    // 给 container 的直接子元素加滚动
                    for (var i = 0; i < container.children.length; i++) {
                        _makeScrollable(container.children[i]);
                    }
                    return;
                }
            } catch(err) {
                console.error('执行出错:', err);
                container.appendChild(ErrorBox(err.message || '执行出错'));
            }
            return;
        }
        
        if (hasCanvas) return;
        container.appendChild(ErrorBox('应用未渲染任何内容'));
    }, 100);
})();