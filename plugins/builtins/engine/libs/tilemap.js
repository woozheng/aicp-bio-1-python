// libs/tilemap.js
// @name: TileMap
// @desc: TileMap(ctx,config) — 瓦片地图。config:{tileSize, map:[[0,1,...]], colors:['#000','#fff',...], offsetX, offsetY}。方法:render(), getTile(col,row), setTile(col,row,value), isSolid(col,row)。
// @example: var map = [[1,1,1],[1,0,1],[1,1,1]]; var tiles = TileMap(ctx, {tileSize:32, map:map, colors:['#000','#fff','#333'], offsetX:10, offsetY:10}); if (!tiles.isSolid(1,1)) { tiles.setTile(1,1,2); } tiles.render();
var TileMap = function(ctx, config) {
    var cfg = config || {};
    var tileSize = (cfg.tileSize !== undefined) ? cfg.tileSize : 32;
    var map = (cfg.map && cfg.map.length > 0) ? cfg.map : [[0]];
    var colors = cfg.colors || ['#000', '#fff'];
    var offsetX = cfg.offsetX || 0;
    var offsetY = cfg.offsetY || 0;

    function render() {
        for (var row = 0; row < map.length; row++) {
            for (var col = 0; col < map[row].length; col++) {
                var tile = map[row][col];
                var color = colors[tile] || colors[0] || '#000';
                ctx.fillStyle = color;
                ctx.fillRect(offsetX + col * tileSize, offsetY + row * tileSize, tileSize, tileSize);
            }
        }
    }

    function getTile(col, row) {
        if (row < 0 || row >= map.length) return -1;
        if (col < 0 || col >= map[row].length) return -1;  // ★ 用 map[row] 而不是 map[0]
        return map[row][col];
    }

    function setTile(col, row, value) {
        if (row >= 0 && row < map.length && col >= 0 && col < map[row].length) {  // ★ 用 map[row]
            map[row][col] = value;
        }
    }

    function isSolid(col, row) {
        return getTile(col, row) === 1;
    }

    return { render: render, getTile: getTile, setTile: setTile, isSolid: isSolid };
};