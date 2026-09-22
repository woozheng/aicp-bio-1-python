// config.js — 配置管理
export class Config {
    static STORAGE_KEY = 'aicp_config';

    static DEFAULTS = {
        apiKey: '',
        baseUrl: 'https://gpt-agent.cc/v1',
        model: 'doubao-seed-2.0-code',
        maxIter: 5,
        temperature: 0.1,
    };

    static load() {
        try {
            const saved = JSON.parse(localStorage.getItem(this.STORAGE_KEY));
            if (saved?.apiKey) return { ...this.DEFAULTS, ...saved };
        } catch (e) {}
        return null;
    }

    static save(config) {
        localStorage.setItem(this.STORAGE_KEY, JSON.stringify(config));
    }

    static clear() {
        localStorage.removeItem(this.STORAGE_KEY);
    }

    static prompt(savedConfig = null) {
        const defaults = savedConfig || this.DEFAULTS;
        const apiKey = prompt('API Key:', defaults.apiKey || '');
        if (!apiKey) return null;
        const baseUrl = prompt('Base URL:', defaults.baseUrl);
        const model = prompt('模型:', defaults.model);
        const maxIter = parseInt(prompt('最大重试次数:', String(defaults.maxIter))) || 5;
        const temperature = parseFloat(prompt('温度 (0-1):', String(defaults.temperature))) || 0.1;

        const config = { apiKey, baseUrl, model, maxIter, temperature };
        this.save(config);
        return config;
    }
}