"""
LLM — Production-grade multi-provider LLM client.

Supports OpenAI-compatible APIs with automatic
failover, retry logic, streaming, and comprehensive error handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Union

import httpx
from openai import AsyncOpenAI

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("aicp.llm")


def _log(msg: str) -> None:
    """Structured debug logging."""
    logger.debug(msg)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default timeout values (seconds)
DEFAULT_REQUEST_TIMEOUT: float = 60.0
DEFAULT_STREAM_TIMEOUT: float = 300.0
DEFAULT_CONNECT_TIMEOUT: float = 10.0
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_MAX_CONCURRENT: int = 10

# HTTP timeout configuration
HTTP_TIMEOUT_CONFIG = {
    "connect": DEFAULT_CONNECT_TIMEOUT,
    "read": 90.0,
    "write": 30.0,
    "pool": DEFAULT_CONNECT_TIMEOUT,
}

# ---------------------------------------------------------------------------
# Error marker
# ---------------------------------------------------------------------------

ERROR_PREFIXES = (
    "[LLM stream error:",
    "[LLM请求失败:",
    "[系统错误:",
    "[服务请求超时",
    "[模型返回空响应",
    "[LLM 达到最大重试次数]",
    "[LLM 未配置]",
    "[空响应]",
)



def is_llm_error_string(text) -> bool:
    """检测文本是否是 LLM 错误包装"""
    if not isinstance(text, str):
        return False
    if not text:
        return False
    return any(text.startswith(p) for p in ERROR_PREFIXES)
# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ModelConfig:
    """Validated model configuration."""
    client_name: str
    max_tokens: int = 4096
    temperature: float = 0.7
    supports_streaming: bool = True
    high_quality: bool = False


@dataclass
class ProviderConfig:
    """Provider configuration container."""
    name: str
    type: str  # "openai"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    models: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base exception for LLM-related errors."""
    pass


class LLMNotConfiguredError(LLMError):
    """Raised when no LLM client is configured."""
    pass


class LLMTimeoutError(LLMError):
    """Raised when an LLM request times out."""
    pass


class LLMEmptyResponseError(LLMError):
    """Raised when LLM returns an empty response."""
    pass


# ---------------------------------------------------------------------------
# Safe fallback constants
# ---------------------------------------------------------------------------

FALLBACK_MESSAGES = {
    "not_configured": "[LLM 未配置]",
    "empty_response": "[模型返回空响应，请稍后重试]",
    "timeout": "[服务请求超时，请稍后重试]",
    "max_retries": "[LLM 达到最大重试次数]",
    "system_error": "[系统错误]",
    "empty": "[空响应]",
}


# ---------------------------------------------------------------------------
# Main LLM class
# ---------------------------------------------------------------------------


class LLM:
    """Production-grade multi-provider LLM client.

    Supports:
    - OpenAI-compatible APIs (via openai library)
    - Streaming responses
    - JSON structured output
    - Concurrency control via semaphore
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """Initialize LLM client from configuration.

        Args:
            config: Configuration dictionary with 'models' section
        """
        models_cfg = config.get("models", {})

        # Role-based model selection
        self._roles: Dict[str, str] = models_cfg.get("roles", {})
        self.default_model: str = self._roles.get(
            "default",
            models_cfg.get("default", "gpt-3.5-turbo"),
        )

        # Providers
        self.providers: Dict[str, Any] = models_cfg.get("providers", {})

        # Internal state
        self._clients: Dict[str, AsyncOpenAI] = {}
        self._model_to_client: Dict[str, str] = {}
        self._model_configs: Dict[str, ModelConfig] = {}

        # Retry & timeout configuration
        self.max_retries: int = models_cfg.get("max_retries", DEFAULT_MAX_RETRIES)
        self.request_timeout: float = models_cfg.get(
            "request_timeout", DEFAULT_REQUEST_TIMEOUT
        )
        self.stream_timeout: float = models_cfg.get(
            "stream_timeout", DEFAULT_STREAM_TIMEOUT
        )
        self.connect_timeout: float = models_cfg.get(
            "connect_timeout", DEFAULT_CONNECT_TIMEOUT
        )

        # Concurrency control
        max_concurrent: int = models_cfg.get("max_concurrent", DEFAULT_MAX_CONCURRENT)
        self._semaphore = asyncio.Semaphore(max_concurrent)

        _log(f"📋 Config models keys: {list(models_cfg.keys())}")
        _log(f"⏱️ request_timeout: {self.request_timeout}s")

        # Initialize all providers
        self._init_clients()

    # ======================================================================
    # Provider initialization
    # ======================================================================

    def _init_clients(self) -> None:
        """Initialize all configured providers."""
        for name, cfg in self.providers.items():
            #print(f"[DEBUG] _init_clients: 处理 provider: {name}, cfg={cfg}")
            provider = ProviderConfig(
                name=name,
                type=cfg.get("type", "openai"),
                api_key=cfg.get("api_key", ""),
                base_url=cfg.get("base_url", "https://api.openai.com/v1"),
                models=cfg.get("models", []),
            )
            #print(f"[DEBUG] _init_clients: provider.name={provider.name}, models={len(provider.models)}")
            self._init_openai_provider(provider)

        # Validate default model
        self._validate_default_model()

        # Log summary
        _log(f"🤖 Default: {self.default_model} | Models: {len(self._model_to_client)}")
        if self._roles:
            _log(f"📋 Roles: {self._roles}")

    def _init_openai_provider(self, provider: ProviderConfig) -> None:
        """Initialize an OpenAI-compatible provider."""
        #print(f"[DEBUG] _init_openai_provider 被调用: {provider.name}, api_key={provider.api_key[:10] if provider.api_key else 'EMPTY'}...")
        
        # Skip if API key is not set
        if not provider.api_key or provider.api_key.startswith("${"):
            _log(f"⚠️ Skip {provider.name}: API key not configured")
            #print(f"[DEBUG] _init_openai_provider: 跳过 {provider.name}, api_key 为空或变量")
            return

        try:
            #print(f"[DEBUG] _init_openai_provider: 创建 AsyncOpenAI 客户端, base_url={provider.base_url}")
            timeout = httpx.Timeout(
                timeout=120.0,
                connect=self.connect_timeout,
                read=HTTP_TIMEOUT_CONFIG["read"],
                write=HTTP_TIMEOUT_CONFIG["write"],
                pool=self.connect_timeout,
            )

            client = AsyncOpenAI(
                api_key=provider.api_key,
                base_url=provider.base_url,
                timeout=timeout,
                http_client=httpx.AsyncClient(
                    timeout=timeout,
                    limits=httpx.Limits(
                        max_keepalive_connections=5,
                        max_connections=10,
                    ),
                ),
                max_retries=0,
            )
            self._clients[provider.name] = client
            #print(f"[DEBUG] _init_openai_provider: AsyncOpenAI 客户端已创建, provider.name={provider.name}")

            for model_cfg in provider.models:
                model_id = model_cfg.get("id")
                if not model_id:
                    continue

                self._model_configs[model_id] = ModelConfig(
                    client_name=provider.name,
                    max_tokens=model_cfg.get("max_tokens", 4096),
                    temperature=model_cfg.get("temperature", 0.7),
                    supports_streaming=model_cfg.get("supports_streaming", True),
                    high_quality=model_cfg.get("high_quality", False),
                )
                self._model_to_client[model_id] = provider.name
                #print(f"[DEBUG] _init_openai_provider: 模型 {model_id} -> {provider.name}")

                if model_cfg.get("high_quality"):
                    self.high_quality_model = model_id

                _log(f"✅ {model_id} ({provider.name})")

        except Exception as exc:
            #print(f"[DEBUG] _init_openai_provider: 异常 {provider.name}: {exc}")
            _log(f"❌ Failed to init {provider.name}: {exc}")

    def _validate_default_model(self) -> None:
        """Ensure default_model exists, or fall back to first available."""
        #print(f"[DEBUG] _validate_default_model: default_model={self.default_model}")
        #print(f"[DEBUG] _validate_default_model: _model_to_client={self._model_to_client}")
        if self.default_model not in self._model_to_client:
            if self._model_to_client:
                self.default_model = next(iter(self._model_to_client))
                _log(f"⚠️ roles.default 模型不可用，改用: {self.default_model}")
                #print(f"[DEBUG] _validate_default_model: 降级到 {self.default_model}")
            else:
                #print("[DEBUG] _validate_default_model: 没有任何可用模型！")
                _log("❌ 没有任何可用模型！")

    # ======================================================================
    # Model resolution
    # ======================================================================

    def _resolve_model(self, role: Optional[str] = None) -> str:
        """Resolve model from role or return default.

        Args:
            role: Role name (e.g., "coding", "reasoning")

        Returns:
            Resolved model ID (never None)
        """
        if not role:
            return self.default_model

        model = self._roles.get(role)
        if model and model in self._model_to_client:
            return model

        if model:
            _log(f"⚠️ roles.{role}={model} 不可用，降级到 default")

        return self.default_model

    def _get_client(self, model: Optional[str] = None) -> tuple[Optional[AsyncOpenAI], str]:
        """Get client and actual model name.

        Args:
            model: Requested model ID

        Returns:
            Tuple of (client, resolved_model)
        """
        resolved_model = model or self.default_model
        #print(f"[DEBUG] _get_client: resolved_model={resolved_model}")
        client_name = self._model_to_client.get(resolved_model)
        #print(f"[DEBUG] _get_client: client_name={client_name}")

        if client_name and client_name in self._clients:
            #print(f"[DEBUG] _get_client: 返回客户端 {client_name}")
            return self._clients[client_name], resolved_model

        # Fallback to first available client
        if self._clients:
            name = next(iter(self._clients))
            #print(f"[DEBUG] _get_client: fallback 到 {name}")
            return self._clients[name], resolved_model

        #print(f"[DEBUG] _get_client: 没有可用客户端!")
        return None, resolved_model

    # ======================================================================
    # Retry logic
    # ======================================================================

    @staticmethod
    def _calculate_backoff(attempt: int, max_wait: int = 10) -> float:
        """Calculate exponential backoff with jitter.

        Args:
            attempt: Current attempt number (0-based)
            max_wait: Maximum wait time in seconds

        Returns:
            Wait time in seconds
        """
        import random
        wait = min(2 ** attempt, max_wait)
        jitter = random.uniform(0, wait * 0.5)
        return wait + jitter

    # ======================================================================
    # Core chat implementation
    # ======================================================================

    async def _chat_impl(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Internal chat implementation. Guaranteed to return a string.

        Args:
            messages: Chat messages
            model: Model ID (optional)
            **kwargs: Additional parameters

        Returns:
            Response string (never None)
        """
        client, actual_model = self._get_client(model)
        #print(f"[DEBUG] _chat_impl: client={client}, actual_model={actual_model}")

        # No client available
        if client is None:
            #print("[DEBUG] _chat_impl: client 为 None, 返回 FALLBACK_MESSAGES['not_configured']")
            return FALLBACK_MESSAGES["not_configured"]

        config = self._model_configs.get(actual_model, ModelConfig(client_name="unknown"))
        max_tokens = kwargs.pop("max_tokens", config.max_tokens)
        temperature = kwargs.pop("temperature", config.temperature)

        # ★★★ 打印实际请求 URL ★★★
        #print(f"[DEBUG] _chat_impl: client.base_url = {client.base_url}")
        #print(f"[DEBUG] _chat_impl: 请求 URL = {client.base_url}/chat/completions")
        _log(f"🔍 实际请求 URL: {client.base_url}/chat/completions")

        last_error: Optional[str] = None

        for attempt in range(self.max_retries):
            try:
                _log(f"🔄 API call {attempt + 1}/{self.max_retries} → {actual_model}")
                #print(f"[DEBUG] _chat_impl: 尝试 {attempt+1}/{self.max_retries}")
                t0 = time.time()

                task = asyncio.create_task(
                    client.chat.completions.create(
                        model=actual_model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        **kwargs,
                    )
                )

                try:
                    response = await asyncio.wait_for(
                        task,
                        timeout=self.request_timeout,
                    )
                except asyncio.TimeoutError:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
                    #print("[DEBUG] _chat_impl: 超时")
                    raise

                elapsed = time.time() - t0

                # Validate response
                if (
                    response
                    and response.choices
                    and response.choices[0].message.content
                ):
                    result = response.choices[0].message.content.strip()
                    if result:
                        _log(f"✅ 成功 ({elapsed:.1f}s, {len(result)} chars)")
                        #print(f"[DEBUG] _chat_impl: 成功, 返回 {len(result)} 字符")
                        return result

                _log(f"⚠️ 空响应 ({elapsed:.1f}s)")
                last_error = "empty_response"

            except asyncio.TimeoutError:
                _log(f"⏱️ 超时 attempt {attempt + 1}")
                last_error = "timeout"
            except Exception as exc:
                error_msg = str(exc)[:200]
                _log(f"❌ 错误 attempt {attempt + 1}: {type(exc).__name__}: {error_msg[:100]}")
                #print(f"[DEBUG] _chat_impl: 异常 {type(exc).__name__}: {error_msg}")
                last_error = error_msg

            # Don't sleep on last attempt
            if attempt < self.max_retries - 1:
                wait = self._calculate_backoff(attempt)
                _log(f"⏳ 等待 {wait:.1f}s 后重试...")
                await asyncio.sleep(wait)

        # All retries exhausted
        # All retries exhausted
        if last_error == "timeout":
            return FALLBACK_MESSAGES["timeout"]
        elif last_error == "empty_response":
            return FALLBACK_MESSAGES["empty_response"]
        else:
            return f"[LLM请求失败: {last_error[:200]}]"

    # ======================================================================
    # Public API
    # ======================================================================

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        role: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Chat completion interface.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Direct model ID (highest priority)
            role: Role-based model selection ("default", "coding", "reasoning")
            **kwargs: Additional parameters passed to API

        Returns:
            Response string (guaranteed non-None, non-empty)
        """
        # Resolve model
        if model:
            resolved_model = model
        elif role:
            resolved_model = self._resolve_model(role)
        else:
            resolved_model = self.default_model

        _log(f"💬 chat: {resolved_model}" + (f" (role={role})" if role else ""))
        #print(f"[DEBUG] chat: resolved_model={resolved_model}")

        try:
            async with self._semaphore:
                result = await self._chat_impl(messages, resolved_model, **kwargs)

                # Guarantee a valid string return
                if result is None:
                    return FALLBACK_MESSAGES["system_error"]
                if not isinstance(result, str):
                    result = str(result)
                if not result.strip():
                    return FALLBACK_MESSAGES["empty"]

                return result

        except Exception as exc:
            _log(f"❌ chat 异常: {type(exc).__name__}: {str(exc)[:100]}")
            return f"[系统错误: {str(exc)[:100]}]"

    # ======================================================================
    # Streaming
    # ======================================================================

    async def _chat_stream_impl(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Internal streaming implementation.

        Args:
            messages: Chat messages
            model: Model ID
            **kwargs: Additional parameters

        Yields:
            Text chunks
        """
        client, actual_model = self._get_client(model)
        #print(f"[DEBUG] _chat_stream_impl: client={client}, actual_model={actual_model}")

        # No client available
        if client is None:
            #print("[DEBUG] _chat_stream_impl: client 为 None")
            yield FALLBACK_MESSAGES["not_configured"]
            return

        # ★★★ 打印实际请求 URL ★★★
        #print(f"[DEBUG] _chat_stream_impl: client.base_url = {client.base_url}")
        #print(f"[DEBUG] _chat_stream_impl: 请求 URL = {client.base_url}/chat/completions")
        _log(f"🔍 实际请求 URL: {client.base_url}/chat/completions")

        config = self._model_configs.get(actual_model, ModelConfig(client_name="unknown"))

        # If streaming not supported, fall back to non-streaming
        if not config.supports_streaming:
            #print("[DEBUG] _chat_stream_impl: 不支持流式, 降级到非流式")
            result = await self._chat_impl(messages, model, **kwargs)
            if result and not result.startswith("["):
                yield result
            else:
                yield FALLBACK_MESSAGES["empty"]
            return

        max_tokens = kwargs.pop("max_tokens", config.max_tokens)
        temperature = kwargs.pop("temperature", config.temperature)

        for attempt in range(self.max_retries):
            stream = None
            try:
                _log(f"📡 stream {attempt + 1}/{self.max_retries} → {actual_model}")
                #print(f"[DEBUG] _chat_stream_impl: 流式尝试 {attempt+1}/{self.max_retries}")

                stream = await client.chat.completions.create(
                    model=actual_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    **kwargs,
                )

                collected: List[str] = []
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        token = chunk.choices[0].delta.content
                        if token:
                            collected.append(token)
                            yield token

                full_text = "".join(collected)
                _log(f"✅ stream 完成: {len(full_text)} chars")
                #print(f"[DEBUG] _chat_stream_impl: 流式完成, {len(full_text)} 字符")
                return

            except asyncio.TimeoutError:
                _log(f"⏱️ stream 超时 attempt {attempt + 1}")
                if attempt == self.max_retries - 1:
                    yield FALLBACK_MESSAGES["timeout"]
                    return
            except Exception as exc:
                error_msg = str(exc)[:200]
                _log(f"❌ stream 错误: {type(exc).__name__}: {error_msg[:100]}")
                #print(f"[DEBUG] _chat_stream_impl: 异常 {type(exc).__name__}: {error_msg}")
                if attempt == self.max_retries - 1:
                    yield f"[LLM stream error: {error_msg[:200]}]"
                    return
            finally:
                # Always close stream to release resources
                if stream is not None:
                    try:
                        await stream.close()
                    except Exception:
                        pass

            if attempt < self.max_retries - 1:
                await asyncio.sleep(self._calculate_backoff(attempt, max_wait=10))

        yield FALLBACK_MESSAGES["max_retries"]

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        role: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Streaming chat interface.

        Args:
            messages: List of message dicts
            model: Direct model ID (highest priority)
            role: Role-based model selection
            **kwargs: Additional parameters

        Yields:
            Text chunks
        """
        if model:
            resolved_model = model
        elif role:
            resolved_model = self._resolve_model(role)
        else:
            resolved_model = self.default_model

        #print(f"[DEBUG] chat_stream: resolved_model={resolved_model}")

        async with self._semaphore:
            async for token in self._chat_stream_impl(
                messages,
                resolved_model,
                **kwargs,
            ):
                if token:
                    yield token

    # ======================================================================
    # JSON output
    # ======================================================================

    async def chat_json(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        role: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """JSON-formatted chat interface with automatic retry.

        Args:
            messages: List of message dicts
            model: Direct model ID
            role: Role-based model selection
            **kwargs: Additional parameters

        Returns:
            Parsed JSON dict (or dict with error/parse_error keys)
        """
        max_json_attempts = 3
        local_messages = list(messages)

        for attempt in range(max_json_attempts):
            raw = await self.chat(
                local_messages,
                model=model,
                role=role,
                **kwargs,
            )

            # Check for error responses
            if raw.startswith("[") and raw.endswith("]"):
                if attempt == max_json_attempts - 1:
                    return {"error": raw}
                continue

            # Try to extract and parse JSON
            cleaned = self._extract_json(raw)

            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as exc:
                _log(f"JSON 解析失败 {attempt + 1}: {exc}")
                if attempt == max_json_attempts - 1:
                    return {"content": cleaned, "parse_error": str(exc)}

                # Add hint for retry
                local_messages.append({
                    "role": "system",
                    "content": "请只返回有效的 JSON 格式，不要包含任何其他文本。",
                })

        return {"content": raw, "error": "max_json_attempts_exceeded"}

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Extract JSON from markdown code blocks or raw text.

        Args:
            raw: Raw response text

        Returns:
            Cleaned JSON string
        """
        cleaned = raw.strip()

        # Remove ```json blocks
        if "```json" in cleaned:
            parts = cleaned.split("```json", 1)
            if len(parts) > 1:
                cleaned = parts[1].split("```", 1)[0].strip()
        elif "```" in cleaned:
            parts = cleaned.split("```")
            if len(parts) > 1:
                cleaned = parts[1].split("```", 1)[0].strip()

        return cleaned

    # ======================================================================
    # Health check
    # ======================================================================

    async def health_check(self) -> bool:
        """Check if any provider is reachable.

        Returns:
            True if at least one provider is healthy
        """
        for name, client in self._clients.items():
            try:
                await asyncio.wait_for(
                    client.models.list(),
                    timeout=5.0,
                )
                _log(f"✅ health check: {name} OK")
                return True
            except Exception as exc:
                _log(f"⚠️ health check: {name} failed: {exc}")
                continue

        return False