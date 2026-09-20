"""LLM 调用插件 — 协议 v3.0 系统插件
封装 LLM API 调用，支持 chat、chat_json。
其他插件可通过 HTTP 或 system.call 调用。
"""
import json
import aiohttp


async def execute(envelop, agent):
    action = envelop.payload.get("action", "chat")
    
    if action == "chat":
        messages = envelop.payload.get("messages", [])
        model = envelop.payload.get("model", "")
        temperature = envelop.payload.get("temperature", 0.7)
        
        if not messages:
            envelop.payload = {"error": "No messages"}
            return envelop
        
        # 从 agent.config 获取 LLM 配置
        models_config = agent.config.get("models", {})
        providers = models_config.get("providers", {})
        
        if not providers:
            envelop.payload = {"error": "No LLM providers configured"}
            return envelop
        
        # 取第一个 provider（可扩展为按 model 名匹配）
        provider_name, provider_config = next(iter(providers.items()))
        api_url = provider_config.get("api_url", "")
        api_key = provider_config.get("api_key", "")
        
        if not model:
            model = provider_config.get("default_model", "gpt-3.5-turbo")
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, headers=headers, json=body, timeout=120) as resp:
                    data = await resp.json()
                    
                    if "choices" in data:
                        envelop.payload = {
                            "content": data["choices"][0]["message"]["content"],
                            "model": model,
                            "usage": data.get("usage", {}),
                        }
                    elif "error" in data:
                        envelop.payload = {"error": data["error"]}
                    else:
                        envelop.payload = {"raw": data}
        except aiohttp.ClientError as e:
            envelop.payload = {"error": f"LLM request failed: {str(e)}"}
        except Exception as e:
            envelop.payload = {"error": f"LLM error: {str(e)}"}
        
        return envelop
    
    elif action == "chat_json":
        messages = envelop.payload.get("messages", [])
        # 注入 JSON 指令
        json_messages = [
            {"role": "system", "content": "You must respond with valid JSON only. No markdown, no explanations."},
            *messages,
        ]
        envelop.payload["messages"] = json_messages
        envelop.payload["action"] = "chat"
        
        result = await execute(envelop, agent)
        
        if "content" in result.payload:
            try:
                result.payload["json"] = json.loads(result.payload["content"])
            except json.JSONDecodeError:
                result.payload["json"] = {"error": "Invalid JSON response", "raw": result.payload["content"]}
        
        return result
    
    elif action == "models":
        models_config = agent.config.get("models", {})
        envelop.payload = {"models": models_config}
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop