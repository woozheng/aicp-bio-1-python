// plugins/common.js — 通用能力合集（仅 LLM 能力）
(function() {
    const registry = window.__aicp_registry__;

    // LLM 对话（配置通过 agent 传入）
    registry.register('llm/chat', async (env, agent) => {
        const { messages, model, temperature } = env.payload;
        const apiKey = agent.get('apiKey');
        const baseUrl = agent.get('baseUrl') || 'https://api.openai.com/v1';
        
        if (!apiKey) {
            return { ok: false, error: 'API Key not configured' };
        }
        
        try {
            const response = await fetch(`${baseUrl}/chat/completions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
                body: JSON.stringify({ model: model || 'gpt-3.5-turbo', messages, temperature: temperature ?? 0.7 }),
            });
            
            const data = await response.json();
            console.log('[llm] response:', data);
            if (data.choices?.[0]?.message?.content) {
                return { ok: true, data: data.choices[0].message.content };
            }
            return { ok: false, error: data.error?.message || 'Empty response' };
        } catch (e) {
            return { ok: false, error: e.message };
        }
    });

    // LLM 流式对话
    registry.register('llm/chatStream', async (env, agent) => {
        const { messages, model, temperature } = env.payload;
        const apiKey = agent.get('apiKey');
        const baseUrl = agent.get('baseUrl') || 'https://api.openai.com/v1';
        
        if (!apiKey) {
            return { ok: false, error: 'API Key not configured' };
        }
        
        try {
            const response = await fetch(`${baseUrl}/chat/completions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
                body: JSON.stringify({ model: model || 'gpt-3.5-turbo', messages, temperature: temperature ?? 0.7, stream: true }),
            });
            return { ok: true, data: response.body };  // 返回 ReadableStream
        } catch (e) {
            return { ok: false, error: e.message };
        }
    });

    console.log('✅ common.js 已加载 (LLM 能力)');
})();