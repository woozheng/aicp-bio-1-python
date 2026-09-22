// core/envelop.js — Envelop 消息信封
class Envelop {
    constructor({
        sender = '',
        receiver = '',
        intent = '',
        payload = {},
        channel_id = '',
        ttl = 10,
        meta = {}
    } = {}) {
        this.sender = sender;
        this.receiver = receiver;
        this.intent = intent;
        this.payload = payload;
        this.trace_id = 'tr_' + Math.random().toString(36).substr(2, 8);
        this.message_id = 'msg_' + Math.random().toString(36).substr(2, 6);
        this.channel_id = channel_id;
        this.ttl = ttl;
        this.meta = meta;
        this.created_at = new Date().toISOString();
        this.path_history = [];
    }

    toJSON() {
        return {
            sender: this.sender,
            receiver: this.receiver,
            intent: this.intent,
            payload: this.payload,
            trace_id: this.trace_id,
            message_id: this.message_id,
            channel_id: this.channel_id,
            ttl: this.ttl,
            meta: this.meta,
            created_at: this.created_at,
        };
    }

    static fromJSON(data) {
        const env = new Envelop({
            sender: data.sender || '',
            receiver: data.receiver || '',
            intent: data.intent || '',
            payload: data.payload || {},
            channel_id: data.channel_id || '',
            ttl: data.ttl ?? 10,
            meta: data.meta || {},
        });
        env.trace_id = data.trace_id || env.trace_id;
        env.message_id = data.message_id || env.message_id;
        env.created_at = data.created_at || env.created_at;
        return env;
    }
}

// 导出
export { Envelop };