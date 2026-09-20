(function() {
    try {
        const rid = '{rid}';
        const selector = '.bp5-overflow-list';
        const minButtons = 4;
        
        // ============================================================
        // 1. 找用户消息
        // ============================================================
        const allNodes = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: function(node) {
                    if (node.textContent.includes('（ID=' + rid) || node.textContent.includes('(ID=' + rid)) {
                        return NodeFilter.FILTER_ACCEPT;
                    }
                    return NodeFilter.FILTER_SKIP;
                }
            }
        );
        let userTextNode = null;
        let node = allNodes.nextNode();
        while (node) {
            if (node.textContent.includes('（ID=' + rid)) {
                userTextNode = node;
                break;
            }
            node = allNodes.nextNode();
        }
        
        if (!userTextNode) {
            return '';
        }
        
        // ============================================================
        // 2. 找用户消息容器
        // ============================================================
        let userContainer = userTextNode.parentElement;
        while (userContainer) {
            const text = userContainer.innerText || '';
            if (text.includes('（ID=' + rid)) {
                break;
            }
            userContainer = userContainer.parentElement;
        }
        
        // ============================================================
        // 3. 全局找所有按钮，取数量最多的
        // ============================================================
        const allBtnGroups = document.querySelectorAll(selector);
        let bestBtn = null;
        let maxCount = 0;
        
        allBtnGroups.forEach(function(el) {
            const b = el.querySelectorAll('button, [role="button"]');
            if (b.length > maxCount) {
                maxCount = b.length;
                bestBtn = el;
            }
        });
        
        if (!bestBtn || maxCount < minButtons) {
            return '';
        }
        
        // ============================================================
        // 4. 用位置判断：按钮必须在用户消息之后
        // ============================================================
        const userRect = userContainer.getBoundingClientRect();
        const btnRect = bestBtn.getBoundingClientRect();
        
        if (btnRect.top <= userRect.top) {
            return '';
        }
        
        // ============================================================
        // 5. 从按钮往上找 AI 回复容器
        // ============================================================
        let parent = bestBtn.parentElement;
        let depth = 0;
        let aiContainer = null;
        
        while (parent && depth < 10) {
            const text = parent.innerText || '';
            if (text.trim().length > 0 && !text.includes('（ID=' + rid)) {
                aiContainer = parent;
                break;
            }
            parent = parent.parentElement;
            depth++;
        }
        
        if (!aiContainer) {
            return '';
        }
        
        // ============================================================
        // 6. 复制容器，去掉按钮和装饰元素
        // ============================================================
        const clone = aiContainer.cloneNode(true);
        const btn = clone.querySelector(selector);
        if (btn) btn.remove();
        
        // 清理图片：去掉 data:image/svg 占位图 和 小图标
        const imgs = clone.querySelectorAll('img');
        imgs.forEach(function(img) {
            const src = img.src || '';
            if (src.startsWith('data:image') && src.includes('svg')) {
                img.remove();
            }
            if (img.width < 50 || img.height < 50) {
                img.remove();
            }
        });

        // 清理 SVG：去掉装饰性图标
        const svgs = clone.querySelectorAll('svg');
        svgs.forEach(function(svg) {
            const ariaLabel = svg.getAttribute('aria-label') || '';
            const role = svg.getAttribute('role') || '';
            if (!ariaLabel && role !== 'img') {
                svg.remove();
            }
        });

        // 去掉空 span
        const emptySpans = clone.querySelectorAll('span:empty');
        emptySpans.forEach(function(span) { span.remove(); });
        
        // ============================================================
        // 7. ★ 核心改动：用 textContent 拿纯文本 ★
        //    不需要处理 &nbsp; 和 <br>，textContent 自动转好了
        // ============================================================
        let text = clone.textContent || '';
        
        // 清理多余空行
        text = text.replace(/\r\n/g, '\n');
        text = text.replace(/\r/g, '\n');
        text = text.trim();
        
        // 压缩多个连续空行
        text = text.replace(/\n{3,}/g, '\n\n');
        
        if (!text || text.length < 10) {
            return '';
        }
        
        return text;
        
    } catch(e) {
        return '';
    }
})();