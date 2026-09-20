(function() {
    try {
        const rid = '{rid}';
        
        // 1. 找用户消息
        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: function(node) {
                    if (node.textContent.includes('（ID=' + rid)) {
                        return NodeFilter.FILTER_ACCEPT;
                    }
                    return NodeFilter.FILTER_SKIP;
                }
            }
        );
        let userTextNode = walker.nextNode();
        while (userTextNode && !userTextNode.textContent.includes('（ID=' + rid)) {
            userTextNode = walker.nextNode();
        }
        if (!userTextNode) return '';
        
        let userContainer = userTextNode.parentElement;
        while (userContainer) {
            if (userContainer.innerText.includes('（ID=' + rid)) break;
            userContainer = userContainer.parentElement;
        }
        if (!userContainer) return '';
        const userRect = userContainer.getBoundingClientRect();
        
        // ============================================================
        // 2. 找按钮容器：.segment-assistant-actions-content
        // ============================================================
        const actionsGroups = document.querySelectorAll('.segment-assistant-actions-content');
        let btnContainer = null;
        let maxCount = 0;
        
        actionsGroups.forEach(function(el) {
            const btns = el.querySelectorAll('button, [role="button"], .icon-button');
            const rect = el.getBoundingClientRect();
            if (rect.top > userRect.bottom) {
                if (btns.length > maxCount) {
                    maxCount = btns.length;
                    btnContainer = el;
                }
            }
        });
        
        if (!btnContainer) return '';
        
        // ============================================================
        // 3. 从按钮容器找 AI 回复容器（向上找父级）
        // ============================================================
        let aiContainer = null;
        let current = btnContainer.parentElement;
        let depth = 0;
        while (current && depth < 15) {
            const text = current.innerText || '';
            // 找到包含足够内容且不包含用户ID的容器
            if (text.trim().length > 100 && !text.includes('（ID=' + rid)) {
                // 检查是否是 AI 回复容器（包含 .chat-content-item-assistant）
                if (current.className && current.className.includes('chat-content-item-assistant')) {
                    aiContainer = current;
                    break;
                }
                // 或者向上找 .chat-content-item
                const parentItem = current.closest('.chat-content-item-assistant');
                if (parentItem) {
                    aiContainer = parentItem;
                    break;
                }
            }
            current = current.parentElement;
            depth++;
        }
        
        // 兜底：直接找最新的 .chat-content-item-assistant
        if (!aiContainer) {
            const assistants = document.querySelectorAll('.chat-content-item-assistant');
            if (assistants.length > 0) {
                aiContainer = assistants[assistants.length - 1];
            }
        }
        
        if (!aiContainer) return '';
        
        // ============================================================
        // 4. 取内容
        // ============================================================
        const clone = aiContainer.cloneNode(true);
        // 去掉按钮
        const allBtns = clone.querySelectorAll('button, [role="button"], .icon-button');
        allBtns.forEach(function(b) { b.remove(); });
        // 去掉 SVG
        const svgs = clone.querySelectorAll('svg');
        svgs.forEach(function(svg) { svg.remove(); });
        
        let html = clone.innerHTML || '';
        html = html.replace(/style="[^"]*"/g, '');
        html = html.replace(/data-[^=]*="[^"]*"/g, '');
        html = html.replace(/class=""/g, '');
        
        // ============================================================
        // 5. 处理代码块：保留缩进（空格转 &nbsp;）
        // ============================================================
        const pres = clone.querySelectorAll('pre');
        pres.forEach(function(pre) {
            const text = pre.textContent || '';
            const lines = text.split('\n');
            const htmlLines = lines.map(function(line) {
                const indent = line.match(/^(\s+)/);
                if (indent) {
                    const spaces = indent[0].replace(/ /g, '&nbsp;');
                    const rest = line.replace(/^(\s+)/, '');
                    return spaces + rest;
                }
                return line;
            });
            pre.innerHTML = htmlLines.join('<br>');
        });
        
        // 只合并空格，不合并换行
        html = html.replace(/[ \t]+/g, ' ');
        html = html.replace(/[ \t]+\n/g, '\n');
        html = html.trim();
        
        return html || clone.textContent || '';
        
    } catch(e) {
        return '';
    }
})();