// aicp-core.js — 主入口，彻底不抛异常
import { Agent } from './core/agent.js';
import { Envelop } from './core/envelop.js';
import { Registry } from './core/registry.js';
import { Router } from './core/router.js';

class AICP {
    constructor(config = {}) {
        this.config = {
            apiKey: '',
            baseUrl: 'https://gpt-agent.cc/v1',
            model: 'doubao-seed-2.0-code',
            maxIter: 5,
            temperature: 0.1,
        };
        
        try {
            const saved = localStorage.getItem('aicp_config');
            if (saved) {
                const parsed = JSON.parse(saved);
                if (parsed && parsed.apiKey) {
                    this.config = parsed;
                }
            }
        } catch(e) {}
        
        this.registry = new Registry();
        this.router = new Router(this.registry);
        this.agent = new Agent(this.config);
        window.__aicp_registry__ = this.registry;
    }

    async init() {
        const pluginFiles = ['llm.js', 'runner.js'];
        const base = new URL('./plugins/', import.meta.url).href;
        await Promise.all(pluginFiles.map(f => import(base + f)));
    }

    async send(receiver, payload = {}, intent = '') {
        const envelop = new Envelop({ sender: 'main', receiver, intent, payload });
        return await this.router.route(envelop, this.agent);
    }
}

const aicp = new AICP();
await aicp.init();
export { AICP, aicp };
export default aicp;