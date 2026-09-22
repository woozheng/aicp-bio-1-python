// core/registry.js — 插件注册表（改为类实例，解决全局状态问题）
class Registry {
    constructor() {
        this._plugins = {};
    }
    register(receiver, handler) {
        if (this._plugins[receiver]) {
            console.warn(`⚠️ 插件已存在，将被覆盖: ${receiver}`);
        }
        this._plugins[receiver] = handler;
        console.log(`✅ [Registry] 注册: ${receiver}`);
    }
    get(receiver) {
        return this._plugins[receiver] || null;
    }
    has(receiver) {
        return receiver in this._plugins;
    }
    list() {
        return Object.keys(this._plugins);
    }
    unregister(receiver) {
        if (this._plugins[receiver]) {
            delete this._plugins[receiver];
            return true;
        }
        return false;
    }
    clear() {
        this._plugins = {};
    }
    count() {
        return Object.keys(this._plugins).length;
    }
}
// 默认导出单例，也支持创建独立实例
const defaultRegistry = new Registry();
export { Registry, defaultRegistry };