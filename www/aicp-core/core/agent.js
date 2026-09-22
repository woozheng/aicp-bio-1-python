// core/agent.js — Agent 容器
class Agent {
    constructor(props = {}) {
        // 所有属性动态注入
        Object.assign(this, props);
    }

    get(key, defaultValue = null) {
        return this[key] ?? defaultValue;
    }

    set(key, value) {
        this[key] = value;
    }

    has(key) {
        return key in this;
    }
}

// 导出
export { Agent };