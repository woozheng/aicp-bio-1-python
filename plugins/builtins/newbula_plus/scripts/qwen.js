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
        // 2. 找按钮容器：排除代码块内的按钮
        // ============================================================
        const allEls = document.querySelectorAll('*');
        let btnContainer = null;
        let maxCount = 0;
        
        allEls.forEach(function(el) {
            const clickables = el.querySelectorAll('[role="button"], .cursor-pointer, .hover\\:bg-tag');
            if (clickables.length >= 4) {
                const rect = el.getBoundingClientRect();
                if (rect.top > userRect.bottom) {
                    const cls = (el.className || '').toString();
                    if (cls.includes('flex') && cls.includes('gap-2')) {
                        const inMarkdown = el.closest('.qk-markdown');
                        if (inMarkdown) {
                            return;
                        }
                        if (clickables.length > maxCount) {
                            maxCount = clickables.length;
                            btnContainer = el;
                        }
                    }
                }
            }
        });
        
        if (!btnContainer) {
            const groups = document.querySelectorAll('.flex.items-center.gap-2');
            groups.forEach(function(el) {
                const inMarkdown = el.closest('.qk-markdown');
                if (inMarkdown) {
                    return;
                }
                const clickables = el.querySelectorAll('[role="button"], .cursor-pointer, .hover\\:bg-tag');
                const rect = el.getBoundingClientRect();
                if (rect.top > userRect.bottom && clickables.length >= 4) {
                    if (clickables.length > maxCount) {
                        maxCount = clickables.length;
                        btnContainer = el;
                    }
                }
            });
        }
        
        if (!btnContainer) return '';
        
        // ============================================================
        // 3. 从按钮容器找 AI 回复容器
        // ============================================================
        let aiContainer = null;
        const answers = btnContainer.querySelectorAll('.message-select-wrapper-answer-rqWekn');
        if (answers.length > 0) {
            aiContainer = answers[answers.length - 1];
        } else {
            let current = btnContainer.parentElement;
            let depth = 0;
            while (current && depth < 15) {
                const cls = (current.className || '').toString();
                if (cls.includes('message-select-wrapper-answer')) {
                    aiContainer = current;
                    break;
                }
                current = current.parentElement;
                depth++;
            }
        }
        
        if (!aiContainer) return '';
        
        // ============================================================
        // 4. 取内容
        // ============================================================
        let markdown = aiContainer.querySelector('.qk-markdown');
        if (!markdown) {
            let bestChild = null;
            let maxLen = 0;
            const children = aiContainer.querySelectorAll('*');
            children.forEach(function(child) {
                const text = child.textContent || '';
                const len = text.trim().length;
                const hasBtn = child.querySelector('button, [role="button"], .cursor-pointer');
                if (!hasBtn && len > maxLen && len > 50) {
                    maxLen = len;
                    bestChild = child;
                }
            });
            markdown = bestChild || aiContainer;
        }
        
        const clone = markdown.cloneNode(true);
        
        // ============================================================
        // 5. 🔥 处理代码块：删除行号 + 保留缩进（空格转 &nbsp;）
        // ============================================================
        const pres = clone.querySelectorAll('pre');
        pres.forEach(function(pre) {
            // 删除行号标签
            const lineNumbers = pre.querySelectorAll('.linenumber');
            lineNumbers.forEach(function(el) {
                el.remove();
            });
            
            const code = pre.querySelector('code');
            if (code) {
                const text = code.textContent || '';
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
                code.innerHTML = htmlLines.join('<br>');
            }
        });
        
        // ============================================================
        // 6. 清理按钮和 SVG
        // ============================================================
        const allBtns = clone.querySelectorAll('button, [role="button"], .cursor-pointer, .hover\\:bg-tag');
        allBtns.forEach(function(b) { b.remove(); });
        
        const svgs = clone.querySelectorAll('svg');
        svgs.forEach(function(svg) {
            if (svg.closest('pre') || svg.closest('code') || svg.closest('.qk-md-paragraph')) {
                return;
            }
            const ariaLabel = svg.getAttribute('aria-label') || '';
            const role = svg.getAttribute('role') || '';
            if (!ariaLabel && role !== 'img') {
                svg.remove();
            }
        });
        
        const emptySpans = clone.querySelectorAll('span:empty');
        emptySpans.forEach(function(span) { span.remove(); });
        
        let html = clone.innerHTML || '';
        html = html.replace(/style="[^"]*"/g, '');
        html = html.replace(/data-[^=]*="[^"]*"/g, '');
        html = html.replace(/class=""/g, '');
        
        html = html.replace(/[ \t]+/g, ' ');
        html = html.replace(/[ \t]+\n/g, '\n');
        html = html.trim();
        
        return html || clone.textContent || '';
        
    } catch(e) {
        return '';
    }
})();