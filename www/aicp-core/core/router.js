// core/router.js — 路由引擎（统一返回 {ok, data, error} 结构）
import { defaultRegistry } from './registry.js';

class Router {
    constructor(registry) {
        this._registry = registry || defaultRegistry;
    }
    async route(envelop, agent = null, timeout = 300000) {
        if (!envelop.receiver) {
            return { ok: false, error: 'Missing receiver' };
        }
        if (envelop.ttl <= 0) {
            return { ok: false, error: 'TTL expired' };
        }
        envelop.ttl -= 1;
        const plugin = this._registry.get(envelop.receiver);
        if (!plugin) {
            return { ok: false, error: `Plugin not found: ${envelop.receiver}` };
        }
        if (!agent) {
            agent = new (await import('./agent.js')).Agent();
        }
        if (!envelop.path_history) {
            envelop.path_history = [];
        }
        envelop.path_history.push(envelop.receiver);
        try {
            const result = await Promise.race([
                plugin(envelop, agent),
                new Promise((_, reject) => setTimeout(() => reject(new Error(`Plugin timeout after ${timeout}ms`)), timeout))
            ]);
            return { ok: true, data: result };
        } catch (e) {
            return { ok: false, error: e.message };
        }
    }
}
const defaultRouter = new Router(defaultRegistry);
export { Router, defaultRouter };