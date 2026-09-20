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
        
        // 2. 找按钮容器
        const allFlex = document.querySelectorAll('.ds-flex');
        let btnContainer = null;
        let maxTop = -Infinity;
        
        allFlex.forEach(function(el) {
            const btns = el.querySelectorAll('[role="button"]');
            if (btns.length >= 4 && btns.length <= 8) {
                const rect = el.getBoundingClientRect();
                if (rect.top > userRect.bottom && rect.top > maxTop) {
                    maxTop = rect.top;
                    btnContainer = el;
                }
            }
        });
        if (!btnContainer) return '';
        
        // 3. 向上找内容容器
        let aiContainer = btnContainer.parentElement;
        let depth = 0;
        while (aiContainer && depth < 15) {
            if ((aiContainer.innerText || '').trim().length > 100) break;
            aiContainer = aiContainer.parentElement;
            depth++;
        }
        if (!aiContainer) return '';
        
        // 4. 取最新的 AI 回复块
        const contents = aiContainer.querySelectorAll('.ds-assistant-message-main-content');
        let contentElement = null;
        if (contents.length > 0) {
            contentElement = contents[contents.length - 1];
        }
        if (!contentElement) {
            const markdowns = aiContainer.querySelectorAll('.ds-markdown');
            if (markdowns.length > 0) {
                contentElement = markdowns[markdowns.length - 1];
            }
        }
        if (!contentElement) {
            const allChildren = aiContainer.querySelectorAll('*');
            let bestChild = null;
            let maxLen = 0;
            const arr = Array.from(allChildren);
            for (let i = arr.length - 1; i >= 0; i--) {
                const child = arr[i];
                const text = child.textContent || '';
                const len = text.trim().length;
                if (len > maxLen && len > 50 && !text.includes('（ID=' + rid)) {
                    maxLen = len;
                    bestChild = child;
                }
            }
            contentElement = bestChild || aiContainer;
        }
        
        // 5. 克隆并清理
        const clone = contentElement.cloneNode(true);
        clone.querySelectorAll('[role="button"]').forEach(function(b) { b.remove(); });
        clone.querySelectorAll('svg').forEach(function(svg) {
            if (!svg.closest('.md-code-block')) {
                svg.remove();
            }
        });
        
        // ============================================================
        // 6. 🔥 提取纯文本（先转 HTML，再转文本，避免截断）
        // ============================================================
        
        // 处理代码块：保留缩进
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
        
        // 清理多余属性
        let html = clone.innerHTML || '';
        html = html.replace(/style="[^"]*"/g, '');
        html = html.replace(/data-[^=]*="[^"]*"/g, '');
        html = html.replace(/class=""/g, '');
        html = html.replace(/[ \t]+/g, ' ');
        html = html.replace(/[ \t]+\n/g, '\n');
        
        // 🔥 从 HTML 提取纯文本
        const tempDiv = document.createElement('div');
        tempDiv.innerHTML = html;
        
        // 在块级元素后插入换行标记
        const blockTags = ['P', 'DIV', 'BR', 'LI', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'PRE', 'BLOCKQUOTE', 'TABLE', 'TR', 'UL', 'OL'];
        blockTags.forEach(function(tag) {
            tempDiv.querySelectorAll(tag).forEach(function(el) {
                el.appendChild(document.createTextNode('\n'));
            });
        });
        
        // 提取纯文本
        let text = tempDiv.textContent || '';
        
        // 清理
        text = text.replace(/&nbsp;/g, ' ');
        text = text.replace(/\n{3,}/g, '\n\n');
        text = text.split('\n').map(function(line) {
            return line.replace(/[ \t]+$/g, '');
        }).join('\n');
        text = text.replace(/^\n+/, '').replace(/\n+$/, '');
        
        return text;
    } catch(e) {
        return '';
    }
})();