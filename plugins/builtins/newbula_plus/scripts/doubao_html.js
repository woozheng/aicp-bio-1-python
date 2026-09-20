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
        // 6. 取 innerHTML，排除按钮 + 清理图片/SVG
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
        // 7. 🔥 处理代码块：保留缩进（把空格转 &nbsp;）
        // ============================================================
        const pres = clone.querySelectorAll('pre');
        pres.forEach(function(pre) {
            const code = pre.querySelector('code');
            if (code) {
                const text = code.textContent || '';
                const lines = text.split('\n');
                // 把每行的缩进空格转成 &nbsp;
                const htmlLines = lines.map(function(line) {
                    // 匹配行首的空格
                    const indent = line.match(/^(\s+)/);
                    if (indent) {
                        const spaces = indent[0].replace(/ /g, '&nbsp;');
                        const rest = line.replace(/^(\s+)/, '');
                        return spaces + rest;
                    }
                    return line;
                });
                code.innerHTML = htmlLines.join('<br>');
            } else {
                // 没有 code 标签，直接处理 pre 内容
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
            }
        });
        
        let html = clone.innerHTML || '';
        html = html.replace(/style="[^"]*"/g, '');
        html = html.replace(/data-[^=]*="[^"]*"/g, '');
        html = html.replace(/class=""/g, '');
        
        // 只合并空格，不合并换行
        html = html.replace(/[ \t]+/g, ' ');
        html = html.replace(/[ \t]+\n/g, '\n');
        html = html.trim();
        
        if (!html || html.length < 10) {
            return '';
        }
        
        return html;
        
    } catch(e) {
        return '';
    }
})();