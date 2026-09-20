// libs/chart.js
// @name: Chart
// @desc: Chart(ctx,config) — 图表。config:{type:'bar'/'line'/'pie', data:[{label,value,color}], x, y, width, height, title}。调用 chart.render() 绘制。
// @example: var chart = Chart(ctx, {type:'bar', data:[{label:'A',value:50,color:'#e94560'},{label:'B',value:80,color:'#e2b714'}], x:50, y:50, width:300, height:200}); chart.render();

var Chart = function(ctx, config) {
    var cfg = config || {};
    var type = cfg.type || 'bar';
    var data = cfg.data || [];
    var x = cfg.x || 0;
    var y = cfg.y || 0;
    var w = cfg.width || 300;
    var h = cfg.height || 200;
    var title = cfg.title || '';

    function render() {
        if (title) {
            ctx.fillStyle = '#e0e0e0';
            ctx.font = '14px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(title, x + w / 2, y - 10);
        }

        if (type === 'bar') renderBar();
        else if (type === 'line') renderLine();
        else if (type === 'pie') renderPie();
    }

    function renderBar() {
        var barW = w / data.length * 0.6;
        var gap = w / data.length * 0.4;
        var maxVal = 1;
        for (var i = 0; i < data.length; i++) {
            if (data[i].value > maxVal) maxVal = data[i].value;
        }
        for (var i = 0; i < data.length; i++) {
            var bh = (data[i].value / maxVal) * h;
            ctx.fillStyle = data[i].color || '#e94560';
            ctx.fillRect(x + i * (barW + gap), y + h - bh, barW, bh);
            ctx.fillStyle = '#a0a0a0';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(data[i].label, x + i * (barW + gap) + barW / 2, y + h + 14);
        }
    }

    function renderLine() {
        if (data.length < 2) return;
        var maxVal = 1;
        for (var i = 0; i < data.length; i++) {
            if (data[i].value > maxVal) maxVal = data[i].value;
        }
        var stepX = w / (data.length - 1);
        ctx.strokeStyle = '#e94560';
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (var i = 0; i < data.length; i++) {
            var px = x + i * stepX;
            var py = y + h - (data[i].value / maxVal) * h;
            if (i === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        }
        ctx.stroke();
        for (var i = 0; i < data.length; i++) {
            var px = x + i * stepX;
            var py = y + h - (data[i].value / maxVal) * h;
            ctx.fillStyle = data[i].color || '#e94560';
            ctx.beginPath();
            ctx.arc(px, py, 4, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = '#a0a0a0';
            ctx.font = '10px sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(data[i].label, px, y + h + 14);
        }
    }

    function renderPie() {
        var total = 0;
        for (var i = 0; i < data.length; i++) total += data[i].value;
        if (total === 0) return;
        var cx = x + w / 2, cy = y + h / 2, r = Math.min(w, h) / 2;
        var angle = -Math.PI / 2;
        for (var i = 0; i < data.length; i++) {
            var slice = (data[i].value / total) * Math.PI * 2;
            ctx.fillStyle = data[i].color || '#e94560';
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.arc(cx, cy, r, angle, angle + slice);
            ctx.closePath();
            ctx.fill();
            angle += slice;
        }
    }

    return { render: render };
};