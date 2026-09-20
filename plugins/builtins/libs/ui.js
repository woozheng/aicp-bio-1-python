// libs/ui.js
// @name: ui
// @desc: Button(text,onClick,style) — 按钮,style:'gold'/'red'/'blue'; Input(placeholder,onEnter,onInput) — 输入框; SearchBar(placeholder,onSearch) — 搜索栏+按钮组合; Card(title,content) — 卡片; Table(data,columns) — 表格,columns:[{label:'列名',key:'字段名'}]或['字段名']; Flex(items,direction,gap) — Flex布局; Grid(items,cols) — Grid布局; Badge(text,color) — 标签
// @example: var btn = Button('搜索', function() { ... }, 'gold'); var card = Card('标题', '内容'); var layout = Flex([card, btn], 'column', 12);
function Button(text, onClick, style) {
    var btn = document.createElement('button');
    btn.textContent = text;
    var bg = style === 'gold' ? '#e2b714' : style === 'red' ? '#e94560' : style === 'blue' ? '#007AFF' : '#e94560';
    var clr = style === 'gold' ? '#000' : '#fff';
    btn.style.cssText = 'padding:10px 20px;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;font-family:inherit;transition:all 0.2s;background:' + bg + ';color:' + clr + ';';
    if (onClick) { btn.addEventListener('click', onClick); btn.addEventListener('touchend', function(e) { e.preventDefault(); onClick(); }); }
    return btn;
}

function Input(placeholder, onEnter, onInput) {
    var input = document.createElement('input');
    input.placeholder = placeholder || '';
    input.style.cssText = 'width:100%;padding:10px 14px;background:#0f3460;color:#fff;border:1px solid #1a1a5e;border-radius:8px;font-size:16px;outline:none;font-family:inherit;';
    if (onEnter) input.addEventListener('keypress', function(e) { if (e.key === 'Enter') onEnter(input.value); });
    if (onInput) input.addEventListener('input', function() { onInput(input.value); });
    return input;
}

function SearchBar(placeholder, onSearch) {
    var div = document.createElement('div');
    div.style.cssText = 'display:flex;gap:8px;margin-bottom:16px;';
    var input = Input(placeholder, function(v) { onSearch(v); });
    input.style.flex = '1';
    div.appendChild(input);
    div.appendChild(Button('搜索', function() { onSearch(input.value); }, 'gold'));
    return div;
}

function Card(title, content) {
    var div = document.createElement('div');
    div.style.cssText = 'background:rgba(15,15,30,0.8);border:1px solid rgba(255,255,255,0.1);border-radius:12px;padding:20px;margin-bottom:12px;word-break:break-word;';
    if (title) { var h3 = document.createElement('h3'); h3.style.cssText = 'color:#e2b714;margin-bottom:10px;font-size:16px;'; h3.textContent = title; div.appendChild(h3); }
    if (typeof content === 'string') { var p = document.createElement('div'); p.style.cssText = 'color:#a0a0a0;font-size:14px;line-height:1.6;white-space:pre-wrap;'; p.textContent = content; div.appendChild(p); }
    else if (content instanceof HTMLElement) div.appendChild(content);
    return div;
}

function Table(data, columns) {
    var table = document.createElement('table');
    table.style.cssText = 'width:100%;border-collapse:collapse;font-size:13px;';
    var thead = document.createElement('thead'), tr = document.createElement('tr');
    columns.forEach(function(col) { var th = document.createElement('th'); th.style.cssText = 'background:#0f3460;padding:10px;text-align:left;color:#e2b714;'; th.textContent = typeof col === 'string' ? col : col.label; tr.appendChild(th); });
    thead.appendChild(tr); table.appendChild(thead);
    var tbody = document.createElement('tbody');
    data.forEach(function(row) { var r = document.createElement('tr'); columns.forEach(function(col) { var td = document.createElement('td'); td.style.cssText = 'padding:10px;border-bottom:1px solid rgba(255,255,255,0.1);'; var key = typeof col === 'string' ? col : col.key; var val = row[key] || ''; if (typeof col === 'object' && col.render) col.render(td, val, row); else td.textContent = val; r.appendChild(td); }); tbody.appendChild(r); });
    table.appendChild(tbody);
    return table;
}

function Flex(items, direction, gap) { var d = document.createElement('div'); d.style.cssText = 'display:flex;flex-direction:' + (direction || 'column') + ';gap:' + (gap || '12') + 'px;'; items.forEach(function(i) { d.appendChild(i); }); return d; }
function Grid(items, cols) { var d = document.createElement('div'); d.style.cssText = 'display:grid;grid-template-columns:repeat(' + (cols || 2) + ',1fr);gap:12px;'; items.forEach(function(i) { d.appendChild(i); }); return d; }
function Badge(text, color) { var s = document.createElement('span'); s.style.cssText = 'display:inline-block;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600;background:' + (color || '#e2b714') + ';color:' + (color ? '#fff' : '#000') + ';'; s.textContent = text; return s; }