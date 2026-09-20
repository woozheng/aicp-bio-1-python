"""LLM 代理 — 给 llm.py 提供 OpenAI 格式接口"""
import time
import json
import re
import html as html_module
import asyncio
import core
from pathlib import Path
from typing import List, Dict, Optional

PROJECT = Path(__file__).parent.name


class OpenAIAdapter:
    def __init__(self, agent):
        self.agent = agent

    # ============================================================
    # HTML → 纯文本（标准 OpenAI 格式）
    # ============================================================
    def _html_to_text(self, html: str) -> str:
        """HTML 转纯文本，保留换行"""
        if not html:
            return ""
        import re
        import html as html_module
        
        # 1. 块级标签转换行
        html = re.sub(r'<br\s*/?>', '\n', html)
        html = re.sub(r'</p>', '\n', html)
        html = re.sub(r'</div>', '\n', html)
        html = re.sub(r'</h[1-6]>', '\n', html)
        html = re.sub(r'</li>', '\n', html)
        html = re.sub(r'</tr>', '\n', html)
        
        # 2. 去掉所有 HTML 标签
        html = re.sub(r'<[^>]+>', '', html)
        
        # 3. 解码 HTML 实体
        text = html_module.unescape(html)
        
        # 4. 合并多余空格，但保留换行
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        return text.strip()

    async def _get_all_platforms(self) -> List[str]:
        """获取所有 ready 的平台 ID"""
        try:
            result = await self.agent.system.call(core.Envelop(
                sender=f"builtins/{PROJECT}/llm_proxy",
                receiver=f"builtins/{PROJECT}/engine",
                payload={"action": "status"}
            ))
            probes = result.payload.get("probes", {})
            ready = [pid for pid, info in probes.items() if info.get("status") == "ready"]
            print(f"[OpenAIAdapter] ready platforms: {ready}")
            return ready
        except Exception as e:
            print(f"[OpenAIAdapter] _get_all_platforms error: {e}")
            return []

    async def _is_ready(self) -> bool:
        """检查是否有 ready 的平台"""
        platforms = await self._get_all_platforms()
        return len(platforms) > 0

    # ============================================================
    # 纯文字聊天（竞速模式）
    # ============================================================
    async def chat(self, messages: List[Dict], role: str = "default") -> Optional[str]:
        if not await self._is_ready():
            return None
        
        query = self._extract_text(messages)
        platforms = await self._get_all_platforms()
        
        if not platforms:
            return None
        
        result = await self.agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/llm_proxy",
            receiver=f"builtins/{PROJECT}/engine",
            payload={
                "action": "ask_some",
                "platform_ids": platforms,
                "query": query,
                "images": [],
                "files": [],
                "stream": False,
                "race": True
            }
        ))
        
        for pid, data in result.payload.get("results", {}).items():
            reply = data.get("reply", "")
            if reply and len(reply) > 10:
                print(f"[OpenAIAdapter] ✅ {pid} 胜出，长度: {len(reply)}")
                return self._html_to_text(reply)
        return None

    # ============================================================
    # 图片 + 文字（竞速模式）
    # ============================================================
    async def chat_with_image(self, messages: List[Dict], image_path: str, role: str = "default") -> Optional[str]:
        if not await self._is_ready():
            return None
        
        query = self._extract_text(messages)
        platforms = await self._get_all_platforms()
        
        if not platforms:
            return None
        
        result = await self.agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/llm_proxy",
            receiver=f"builtins/{PROJECT}/engine",
            payload={
                "action": "ask_some",
                "platform_ids": platforms,
                "query": query,
                "images": [image_path],
                "files": [],
                "stream": False,
                "race": True
            }
        ))
        
        for pid, data in result.payload.get("results", {}).items():
            reply = data.get("reply", "")
            if reply and len(reply) > 10:
                return self._html_to_text(reply)
        return None

    # ============================================================
    # 文件 + 文字（竞速模式）
    # ============================================================
    async def chat_with_file(self, messages: List[Dict], file_path: str, role: str = "default") -> Optional[str]:
        if not await self._is_ready():
            return None
        
        query = self._extract_text(messages)
        platforms = await self._get_all_platforms()
        
        if not platforms:
            return None
        
        result = await self.agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/llm_proxy",
            receiver=f"builtins/{PROJECT}/engine",
            payload={
                "action": "ask_some",
                "platform_ids": platforms,
                "query": query,
                "images": [],
                "files": [file_path],
                "stream": False,
                "race": True
            }
        ))
        
        for pid, data in result.payload.get("results", {}).items():
            reply = data.get("reply", "")
            if reply and len(reply) > 10:
                return self._html_to_text(reply)
        return None

    # ============================================================
    # 生图（收集模式）
    # ============================================================
    async def generate_image(self, prompt: str) -> Optional[dict]:
        if not await self._is_ready():
            return None
        
        platforms = await self._get_all_platforms()
        if not platforms:
            return None
        
        result = await self.agent.system.call(core.Envelop(
            sender=f"builtins/{PROJECT}/llm_proxy",
            receiver=f"builtins/{PROJECT}/engine",
            payload={
                "action": "ask_some",
                "platform_ids": platforms,
                "query": prompt,
                "images": [],
                "files": [],
                "stream": False,
                "race": False
            }
        ))
        
        all_urls = []
        for pid, data in result.payload.get("results", {}).items():
            urls = data.get("media_urls", [])
            if urls:
                all_urls.extend(urls)
                print(f"[OpenAIAdapter] {pid} 返回 {len(urls)} 张")
        return {"media_urls": all_urls}

    # ============================================================
    # 辅助
    # ============================================================
    def _extract_text(self, messages: List[Dict]) -> str:
        parts = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        parts.append(part.get("text", ""))
            else:
                parts.append(str(content))
        return "\n".join(parts)

    # ============================================================
    # OpenAI 格式包装
    # ============================================================
    def to_openai_chat(self, content: Optional[str]) -> dict:
        if content is None:
            return {"error": "Nebula 不可用"}
        return {
            "choices": [{
                "message": {"role": "assistant", "content": content},
                "index": 0,
                "finish_reason": "stop"
            }],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "nebula-cluster"
        }

    def to_openai_image(self, urls: Optional[List[str]]) -> dict:
        if urls is None:
            return {"error": "Nebula 不可用"}
        return {
            "data": [{"url": url} for url in urls],
            "created": int(time.time()),
            "model": "nebula-cluster"
        }


# ============================================================
# 插件入口
# ============================================================
async def execute(envelop, agent):
    action = envelop.payload.get("action", "")
    adapter = OpenAIAdapter(agent)
    
    if action == "chat":
        messages = envelop.payload.get("messages", [])
        role = envelop.payload.get("role", "default")
        reply = await adapter.chat(messages, role)
        envelop.payload = adapter.to_openai_chat(reply)
        return envelop
    
    elif action == "chat_with_image":
        messages = envelop.payload.get("messages", [])
        image_path = envelop.payload.get("image_path", "")
        role = envelop.payload.get("role", "default")
        reply = await adapter.chat_with_image(messages, image_path, role)
        envelop.payload = adapter.to_openai_chat(reply)
        return envelop
    
    elif action == "chat_with_file":
        messages = envelop.payload.get("messages", [])
        file_path = envelop.payload.get("file_path", "")
        role = envelop.payload.get("role", "default")
        reply = await adapter.chat_with_file(messages, file_path, role)
        envelop.payload = adapter.to_openai_chat(reply)
        return envelop
    
    elif action == "image":
        prompt = envelop.payload.get("prompt", "")
        n = envelop.payload.get("n", 4)
        result = await adapter.generate_image(prompt)
        envelop.payload = adapter.to_openai_image(result["media_urls"][:n] if result else [])
        return envelop
    
    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop