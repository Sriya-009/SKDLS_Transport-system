"""Production LLM orchestration for the logistics AI system.

The provider keeps the existing `LLMProvider.generate(...)` API intact while
adding provider failover, retrieval memory, streaming, confidence scoring,
summarization, and tool-selection helpers used by the logistics agent layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import time
import uuid
from typing import Any, Dict, Generator, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import requests

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None

try:
    import google.generativeai as genai
except Exception:  # pragma: no cover - optional dependency
    genai = None

try:
    from db import (
        get_db_connection,
        get_user_preferences,
        load_ai_conversation_memory,
        load_workflow_memory,
        save_ai_tool_metric,
    )
except Exception:  # pragma: no cover - import-cycle safe
    get_db_connection = None
    get_user_preferences = None
    load_ai_conversation_memory = None
    load_workflow_memory = None
    save_ai_tool_metric = None


PUBLIC_INTENTS = {
    "BOOK_SHIPMENT",
    "TRACK_SHIPMENT",
    "GET_PRICE_ESTIMATE",
    "MAKE_PAYMENT",
    "RECONCILE_PAYMENT",
    "GENERATE_INVOICE",
    "DELIVERY_CONFIRMATION",
    "DELAY_MANAGEMENT",
    "FAILED_SHIPMENT",
    "CUSTOMER_NOTIFICATION",
    "GET_ANALYTICS",
    "DRIVER_UPDATE",
    "CUSTOMER_SUPPORT",
    "UNRELATED",
}

DEFAULT_TOOL_BY_INTENT = {
    "BOOK_SHIPMENT": "create_shipment",
    "TRACK_SHIPMENT": "track_shipment",
    "GET_PRICE_ESTIMATE": "calculate_eta",
    "MAKE_PAYMENT": "create_payment",
    "RECONCILE_PAYMENT": "reconcile_payment",
    "GENERATE_INVOICE": "generate_invoice",
    "DELIVERY_CONFIRMATION": "confirm_delivery",
    "DELAY_MANAGEMENT": "manage_delay",
    "FAILED_SHIPMENT": "handle_failed_shipment",
    "CUSTOMER_NOTIFICATION": "notify_customer",
    "GET_ANALYTICS": "get_analytics",
    "DRIVER_UPDATE": "update_shipment_status",
    "CUSTOMER_SUPPORT": "customer_support",
}


@dataclass(slots=True)
class ProviderAttempt:
    provider: str
    model: str
    status: str
    duration_ms: int
    retry_count: int = 0
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: List[ProviderAttempt] = field(default_factory=list)
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _normalize_provider(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _estimate_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, tuple)) else str(value or "")
    return max(1, int(len(text) / 4))


def _json_dumps(value: Any, limit: int = 8000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value or "")
    return text[:limit]


def _safe_json_loads(text: Any) -> Dict[str, Any]:
    if isinstance(text, dict):
        return dict(text)
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _tail(items: Iterable[Any], limit: int = 8) -> List[Any]:
    values = list(items or [])
    return values[-limit:]


class LLMProvider:
    """Multi-provider LLM orchestration with logistics-aware fallbacks."""

    def __init__(self, *, preferred: Optional[str] = None, logger: Optional[Any] = None):
        self.preferred = _normalize_provider(preferred or os.getenv("LLM_PROVIDER") or "auto")
        self.logger = logger
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.gemini_model = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash").strip()
        self.openai_model = os.getenv("OPENAI_MODEL_NAME", os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
        self.max_context_tokens = _env_int("LLM_MAX_CONTEXT_TOKENS", 12000)
        self.default_timeout = _env_int("LLM_TIMEOUT_SECONDS", 20)
        self.max_retries = _env_int("LLM_RETRIES", 2)
        self.openai_client = None

        if OpenAI is not None and self.openai_key:
            try:
                self.openai_client = OpenAI(api_key=self.openai_key, timeout=self.default_timeout)
            except Exception:
                self.openai_client = None

        if genai is not None and self.gemini_key:
            try:
                genai.configure(api_key=self.gemini_key)
            except Exception:
                pass

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 256,
        temperature: float = 0.2,
        timeout: int = 10,
        retries: int = 2,
        stream: bool = False,
        provider: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        memory: Optional[Mapping[str, Any]] = None,
        system_prompt: str = "",
    ) -> Union[str, Generator[str, None, None]]:
        """Generate text from Gemini/OpenAI/fallback providers.

        Existing callers receive a plain string. Streaming callers receive a
        generator yielding chunks and still benefit from provider failover.
        """
        request_id = uuid.uuid4().hex
        provider_order = self._provider_order(provider)
        prompt_text = self._prepare_prompt(
            prompt,
            user_id=user_id,
            session_id=session_id,
            memory=memory,
            system_prompt=system_prompt,
            max_output_tokens=max_tokens,
        )

        if stream:
            return self._stream_with_failover(
                provider_order,
                prompt_text,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout or self.default_timeout,
                retries=retries,
                request_id=request_id,
            )

        response = self.generate_with_metadata(
            prompt_text,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout or self.default_timeout,
            retries=retries,
            provider_order=provider_order,
            request_id=request_id,
        )
        return response.text

    def generate_with_metadata(
        self,
        prompt: str,
        *,
        max_tokens: int = 256,
        temperature: float = 0.2,
        timeout: int = 10,
        retries: int = 2,
        provider_order: Optional[Sequence[str]] = None,
        request_id: Optional[str] = None,
    ) -> LLMResponse:
        attempts: List[ProviderAttempt] = []
        last_error = ""
        started = time.perf_counter()
        providers = list(provider_order or self._provider_order(None))
        input_tokens = _estimate_tokens(prompt)

        for provider_name in providers:
            provider_name = _normalize_provider(provider_name)
            per_provider_retries = max(0, int(retries if retries is not None else self.max_retries))
            for retry_index in range(per_provider_retries + 1):
                attempt_start = time.perf_counter()
                try:
                    text, model = self._call_provider(
                        provider_name,
                        prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        timeout=timeout,
                    )
                    duration_ms = int((time.perf_counter() - attempt_start) * 1000)
                    output_tokens = _estimate_tokens(text)
                    attempt = ProviderAttempt(
                        provider=provider_name,
                        model=model,
                        status="success",
                        duration_ms=duration_ms,
                        retry_count=retry_index,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                    attempts.append(attempt)
                    total_ms = int((time.perf_counter() - started) * 1000)
                    confidence = self.score_confidence(
                        prompt=prompt,
                        response=text,
                        provider=provider_name,
                        attempts=attempts,
                    )
                    self._record_metric(
                        provider_name=provider_name,
                        action="generate",
                        duration_ms=duration_ms,
                        retry_count=retry_index,
                        status="success",
                        request_id=request_id,
                        metadata={
                            "model": model,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "failover_attempts": len([item for item in attempts if item.status != "success"]),
                        },
                    )
                    return LLMResponse(
                        text=str(text or "").strip(),
                        provider=provider_name,
                        model=model,
                        latency_ms=total_ms,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        attempts=attempts,
                        confidence=confidence,
                        metadata={"request_id": request_id},
                    )
                except Exception as error:
                    duration_ms = int((time.perf_counter() - attempt_start) * 1000)
                    last_error = str(error)
                    attempts.append(
                        ProviderAttempt(
                            provider=provider_name,
                            model=self._model_for(provider_name),
                            status="error",
                            duration_ms=duration_ms,
                            retry_count=retry_index,
                            error=last_error,
                            input_tokens=input_tokens,
                        )
                    )
                    self._record_metric(
                        provider_name=provider_name,
                        action="generate",
                        duration_ms=duration_ms,
                        retry_count=retry_index,
                        status="error",
                        request_id=request_id,
                        metadata={"error": last_error, "input_tokens": input_tokens},
                    )
                    self._log_warning("provider failed", provider_name, last_error)
                    if retry_index < per_provider_retries:
                        time.sleep(min(0.35 * (2 ** retry_index), 2.0))

        recovery = self.recover_from_failure(last_error, {"prompt": prompt, "providers": providers})
        return LLMResponse(
            text=recovery["message"],
            provider="fallback",
            model="deterministic_recovery",
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=_estimate_tokens(recovery["message"]),
            attempts=attempts,
            confidence=0.35,
            metadata={"request_id": request_id, "recovery": recovery},
        )

    def retrieve_memory(
        self,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        query: str = "",
        limit: int = 6,
    ) -> Dict[str, Any]:
        """Retrieve logistics memory for RAG prompts."""
        memory = {
            "shipment_history": [],
            "payment_history": [],
            "workflow_memory": [],
            "support_tickets": [],
            "user_preferences": {},
            "conversation_memory": [],
        }

        if get_user_preferences is not None and user_id:
            try:
                memory["user_preferences"] = get_user_preferences(user_id) or {}
            except Exception:
                memory["user_preferences"] = {}

        if load_workflow_memory is not None and user_id:
            try:
                memory["workflow_memory"] = load_workflow_memory(user_id) or []
            except Exception:
                memory["workflow_memory"] = []

        if load_ai_conversation_memory is not None and (user_id or session_id):
            try:
                memory["conversation_memory"] = load_ai_conversation_memory(user_id=user_id, session_id=session_id, limit=limit) or []
            except Exception:
                memory["conversation_memory"] = []

        if get_db_connection is None:
            return memory

        user_filter = str(user_id or "").strip()
        try:
            if user_filter:
                memory["shipment_history"] = self._query_rows(
                    """
                    SELECT id, pickup_location, drop_location, cargo_type, truck_type, weight,
                           estimated_price, payment_status, shipment_status, assigned_driver_id, created_at
                    FROM shipments
                    WHERE user_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (user_filter, int(limit)),
                )
                memory["payment_history"] = self._query_rows(
                    """
                    SELECT p.id, p.booking_id, p.shipment_id, p.amount, p.payment_status,
                           p.status, p.payment_type, p.razorpay_order_id, p.razorpay_payment_id, p.created_at
                    FROM payments p
                    LEFT JOIN shipments s ON p.shipment_id = s.id
                    LEFT JOIN bookings b ON p.booking_id = b.id
                    WHERE s.user_id = %s OR b.user_id = %s
                    ORDER BY p.created_at DESC, p.id DESC
                    LIMIT %s
                    """,
                    (user_filter, user_filter, int(limit)),
                )

            memory["support_tickets"] = self._load_support_tickets(user_filter, query=query, limit=limit)
        except Exception as error:
            self._log_warning("memory retrieval failed", str(error))

        return memory

    def build_rag_prompt(
        self,
        prompt: str,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        memory: Optional[Mapping[str, Any]] = None,
        max_context_tokens: Optional[int] = None,
    ) -> str:
        retrieved = dict(memory or self.retrieve_memory(user_id=user_id, session_id=session_id, query=prompt))
        memory_block = {
            "shipment_history": _tail(retrieved.get("shipment_history"), 5),
            "payment_history": _tail(retrieved.get("payment_history"), 5),
            "workflow_memory": _tail(retrieved.get("workflow_memory"), 5),
            "support_tickets": _tail(retrieved.get("support_tickets"), 5),
            "user_preferences": retrieved.get("user_preferences") or {},
            "conversation_memory": _tail(retrieved.get("conversation_memory"), 5),
        }
        budget = int(max_context_tokens or self.max_context_tokens)
        block = self._trim_text(_json_dumps(memory_block, limit=24000), max(1000, budget - _estimate_tokens(prompt) - 500))
        return (
            "Use the logistics memory below only when relevant. Do not invent records.\n"
            f"MEMORY JSON:\n{block}\n\n"
            f"USER TASK:\n{prompt}"
        )

    def select_tool(
        self,
        message: str,
        *,
        role: str = "customer",
        available_tools: Optional[Sequence[str]] = None,
        memory: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Select the best logistics tool with deterministic fallback."""
        available = list(available_tools or DEFAULT_TOOL_BY_INTENT.values())
        intent_payload = self._classify_intent_fallback(message, role=role)
        intent = intent_payload["intent"]
        tool = DEFAULT_TOOL_BY_INTENT.get(intent, "customer_support")
        text = str(message or "").lower()

        if intent == "DRIVER_UPDATE":
            if any(word in text for word in ("assign", "dispatch", "allocate")):
                tool = "assign_driver"
            elif any(word in text for word in ("delay", "late", "stuck")):
                tool = "manage_delay"
            elif any(word in text for word in ("deliver", "pod", "proof")):
                tool = "confirm_delivery"
        elif intent == "MAKE_PAYMENT":
            if any(word in text for word in ("invoice", "receipt", "bill")):
                tool = "generate_invoice"
            elif any(word in text for word in ("reconcile", "settle", "verify")):
                tool = "reconcile_payment"

        if tool not in available and available:
            tool = "customer_support" if "customer_support" in available else available[0]

        confidence = self.score_confidence(
            prompt=message,
            response=json.dumps(intent_payload),
            intent=intent,
            tool_name=tool,
            memory=memory,
        )
        return {
            "intent": intent,
            "tool_name": tool,
            "confidence": confidence,
            "reasoning": intent_payload.get("reasoning") or "deterministic_tool_selection",
        }

    def score_confidence(
        self,
        *,
        prompt: str,
        response: str,
        provider: str = "",
        attempts: Optional[Sequence[ProviderAttempt]] = None,
        intent: str = "",
        tool_name: str = "",
        memory: Optional[Mapping[str, Any]] = None,
    ) -> float:
        score = 0.42
        text = str(response or "").strip()
        prompt_text = str(prompt or "").lower()
        parsed = _safe_json_loads(text)

        if text:
            score += 0.12
        if parsed:
            score += 0.14
        if (intent or parsed.get("intent") or "").upper() in PUBLIC_INTENTS:
            score += 0.12
        if provider in {"openai", "gemini"}:
            score += 0.08
        if tool_name:
            score += 0.05
        if memory and any(memory.get(key) for key in ("shipment_history", "payment_history", "workflow_memory", "user_preferences")):
            score += 0.05
        if any(word in prompt_text for word in ("maybe", "not sure", "unknown", "something")):
            score -= 0.08
        if attempts and any(item.status == "error" for item in attempts):
            score -= min(0.12, 0.04 * len([item for item in attempts if item.status == "error"]))

        return round(max(0.0, min(0.99, score)), 2)

    def summarize_conversation(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        max_tokens: int = 180,
        provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized = [
            {
                "role": str(item.get("role") or item.get("sender") or "user"),
                "content": str(item.get("content") or item.get("text") or item.get("message") or ""),
            }
            for item in list(messages or [])[-20:]
            if str(item.get("content") or item.get("text") or item.get("message") or "").strip()
        ]

        if not normalized:
            return {"summary": "", "confidence": 0.0, "provider": "fallback"}

        prompt = (
            "Summarize this logistics conversation for future AI workflow memory. "
            "Include active shipment IDs, payment state, missing fields, customer preferences, and next action. "
            "Return 4 concise bullets.\n"
            f"{_json_dumps(normalized, limit=8000)}"
        )
        try:
            result = self.generate_with_metadata(
                prompt,
                max_tokens=max_tokens,
                temperature=0.2,
                timeout=self.default_timeout,
                retries=1,
                provider_order=self._provider_order(provider),
            )
            return {"summary": result.text, "confidence": result.confidence, "provider": result.provider}
        except Exception as error:
            return {
                "summary": self._extractive_summary(normalized),
                "confidence": 0.45,
                "provider": "fallback",
                "error": str(error),
            }

    def recover_from_failure(self, error: Any, context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        context = dict(context or {})
        error_text = str(error or "").strip()
        retryable = any(token in error_text.lower() for token in ("timeout", "rate", "temporar", "connection", "503", "429"))
        prompt = str(context.get("prompt") or "")

        if "RESPOND ONLY WITH VALID JSON" in prompt:
            fallback = self._classify_intent_fallback(prompt)
            return {
                "status": "recovered",
                "retryable": retryable,
                "message": json.dumps(fallback, ensure_ascii=False),
                "reason": error_text,
                "next_provider": "fallback",
            }

        return {
            "status": "recovered",
            "retryable": retryable,
            "message": "I hit a temporary AI provider issue. I can still help with booking, tracking, payments, driver updates, and support if you share the shipment or booking reference.",
            "reason": error_text,
            "next_provider": "fallback",
        }

    def _prepare_prompt(
        self,
        prompt: str,
        *,
        user_id: Optional[str],
        session_id: Optional[str],
        memory: Optional[Mapping[str, Any]],
        system_prompt: str,
        max_output_tokens: int,
    ) -> str:
        base = str(prompt or "").strip()
        if memory is not None or user_id or session_id:
            base = self.build_rag_prompt(base, user_id=user_id, session_id=session_id, memory=memory)
        if system_prompt:
            base = f"{system_prompt.strip()}\n\n{base}"
        return self._trim_text(base, max(1000, self.max_context_tokens - int(max_output_tokens or 0)))

    def _provider_order(self, provider: Optional[str]) -> List[str]:
        requested = _normalize_provider(provider or self.preferred or "auto")
        configured = [item.strip() for item in os.getenv("LLM_PROVIDER_ORDER", "").split(",") if item.strip()]
        if configured and requested == "auto":
            return [_normalize_provider(item) for item in configured] + ["fallback"]
        if requested and requested != "auto":
            return [requested, "gemini", "openai", "fallback"] if requested != "fallback" else ["fallback"]

        order: List[str] = []
        if self.gemini_key:
            order.append("gemini")
        if self.openai_key:
            order.append("openai")
        order.append("fallback")
        return list(dict.fromkeys(order))

    def _call_provider(self, provider: str, prompt: str, *, max_tokens: int, temperature: float, timeout: int) -> Tuple[str, str]:
        if provider == "openai":
            return self._call_openai(prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        if provider == "gemini":
            return self._call_gemini(prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        if provider in {"fallback", "rule_based", "echo"}:
            return self._call_fallback(prompt, max_tokens=max_tokens), "deterministic_fallback"
        raise RuntimeError(f"Unsupported LLM provider: {provider}")

    def _call_openai(self, prompt: str, *, max_tokens: int, temperature: float, timeout: int) -> Tuple[str, str]:
        if not self.openai_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        if self.openai_client is not None:
            response = self.openai_client.chat.completions.create(
                model=self.openai_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            text = "".join(choice.message.content or "" for choice in response.choices)
            return text.strip(), self.openai_model

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.openai_key}", "Content-Type": "application/json"},
            json={
                "model": self.openai_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        text = "".join((choice.get("message") or {}).get("content") or "" for choice in payload.get("choices", []))
        return text.strip(), str(payload.get("model") or self.openai_model)

    def _call_gemini(self, prompt: str, *, max_tokens: int, temperature: float, timeout: int) -> Tuple[str, str]:
        if genai is None:
            raise RuntimeError("google-generativeai is not installed")
        if not self.gemini_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        model = genai.GenerativeModel(self.gemini_model)
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens, temperature=temperature),
            request_options={"timeout": timeout},
        )
        return str(getattr(response, "text", "") or "").strip(), self.gemini_model

    def _stream_with_failover(
        self,
        provider_order: Sequence[str],
        prompt: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: int,
        retries: int,
        request_id: str,
    ) -> Generator[str, None, None]:
        yielded = False
        for provider_name in provider_order:
            provider_name = _normalize_provider(provider_name)
            for retry_index in range(max(0, int(retries)) + 1):
                start = time.perf_counter()
                try:
                    for chunk in self._stream_provider(provider_name, prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout):
                        yielded = True
                        yield chunk
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    self._record_metric(provider_name=provider_name, action="stream", duration_ms=duration_ms, retry_count=retry_index, status="success", request_id=request_id)
                    return
                except Exception as error:
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    self._record_metric(provider_name=provider_name, action="stream", duration_ms=duration_ms, retry_count=retry_index, status="error", request_id=request_id, metadata={"error": str(error)})
                    self._log_warning("stream provider failed", provider_name, str(error))
                    if yielded:
                        return
                    if retry_index < int(retries):
                        time.sleep(min(0.35 * (2 ** retry_index), 2.0))

        recovery = self.recover_from_failure("all stream providers failed", {"prompt": prompt})
        yield recovery["message"]

    def _stream_provider(self, provider: str, prompt: str, *, max_tokens: int, temperature: float, timeout: int) -> Generator[str, None, None]:
        if provider == "openai":
            yield from self._openai_stream(prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout)
            return
        if provider == "gemini":
            yield from self._gemini_stream(prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout)
            return
        text = self._call_fallback(prompt, max_tokens=max_tokens)
        for chunk in self._chunk_text(text):
            yield chunk

    def _openai_stream(self, prompt: str, *, max_tokens: int, temperature: float, timeout: int) -> Generator[str, None, None]:
        if not self.openai_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        if self.openai_client is None:
            text, _model = self._call_openai(prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout)
            yield from self._chunk_text(text)
            return

        stream = self.openai_client.chat.completions.create(
            model=self.openai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            timeout=timeout,
        )
        for event in stream:
            for choice in event.choices:
                text = getattr(choice.delta, "content", None)
                if text:
                    yield text

    def _gemini_stream(self, prompt: str, *, max_tokens: int, temperature: float, timeout: int) -> Generator[str, None, None]:
        if genai is None:
            raise RuntimeError("google-generativeai is not installed")
        if not self.gemini_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        model = genai.GenerativeModel(self.gemini_model)
        stream = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(max_output_tokens=max_tokens, temperature=temperature),
            request_options={"timeout": timeout},
            stream=True,
        )
        for event in stream:
            text = str(getattr(event, "text", "") or "")
            if text:
                yield text

    def _call_fallback(self, prompt: str, *, max_tokens: int) -> str:
        if "RESPOND ONLY WITH VALID JSON" in prompt or '"intent"' in prompt:
            return json.dumps(self._classify_intent_fallback(prompt), ensure_ascii=False)
        if "Summarize this logistics conversation" in prompt:
            payload = _safe_json_loads(prompt)
            return self._extractive_summary(payload if isinstance(payload, list) else [{"content": prompt}])
        recovery = self.recover_from_failure("provider unavailable", {"prompt": prompt})
        return recovery["message"][: max(64, max_tokens * 4)]

    def _classify_intent_fallback(self, text: str, *, role: str = "customer") -> Dict[str, Any]:
        raw = str(text or "")
        message = raw
        match = re.search(r"User Message:\s*(.+?)(?:\n\s*Database Context:|\n\n|$)", raw, flags=re.DOTALL | re.IGNORECASE)
        if match:
            message = match.group(1)
        message_lower = message.strip().lower()

        intent = "UNRELATED"
        if any(word in message_lower for word in ("book", "shipment", "truck", "lorry", "pickup", "drop", "from ", " to ")):
            intent = "BOOK_SHIPMENT"
        if any(word in message_lower for word in ("track", "where", "location", "eta", "arrival", "status")):
            intent = "TRACK_SHIPMENT"
        if any(word in message_lower for word in ("price", "quote", "fare", "cost", "estimate", "rate")):
            intent = "GET_PRICE_ESTIMATE"
        if any(word in message_lower for word in ("pay", "payment", "razorpay", "advance")):
            intent = "MAKE_PAYMENT"
        if any(word in message_lower for word in ("reconcile", "settle", "verify payment")):
            intent = "RECONCILE_PAYMENT"
        if any(word in message_lower for word in ("invoice", "receipt", "bill")):
            intent = "GENERATE_INVOICE"
        if any(word in message_lower for word in ("delivered", "delivery", "pod", "proof of delivery")):
            intent = "DELIVERY_CONFIRMATION"
        if any(word in message_lower for word in ("delay", "late", "stuck", "held")):
            intent = "DELAY_MANAGEMENT"
        if any(word in message_lower for word in ("failed", "cancelled", "rejected", "unable to deliver")):
            intent = "FAILED_SHIPMENT"
        if any(word in message_lower for word in ("notify", "alert customer", "send update")):
            intent = "CUSTOMER_NOTIFICATION"
        if any(word in message_lower for word in ("analytics", "dashboard", "revenue", "report", "fleet")):
            intent = "GET_ANALYTICS"
        if any(word in message_lower for word in ("driver", "assign", "dispatch")):
            intent = "DRIVER_UPDATE"
        if any(word in message_lower for word in ("help", "support", "agent", "human", "issue")) and intent == "UNRELATED":
            intent = "CUSTOMER_SUPPORT"

        confidence = 0.72 if intent != "UNRELATED" else 0.35
        return {
            "intent": intent,
            "confidence": confidence,
            "reply": self._fallback_reply(intent),
            "reasoning": "deterministic_fallback_classifier",
        }

    def _fallback_reply(self, intent: str) -> str:
        return {
            "BOOK_SHIPMENT": "I can help book the shipment. Share pickup, drop, load weight, and truck type.",
            "TRACK_SHIPMENT": "Share the shipment or booking reference and I will open tracking.",
            "GET_PRICE_ESTIMATE": "Share pickup, drop, load weight, and truck type for a fare estimate.",
            "MAKE_PAYMENT": "Share your booking or shipment reference and I will prepare the payment workflow.",
            "RECONCILE_PAYMENT": "Share the booking or shipment reference and I will reconcile the payment records.",
            "GENERATE_INVOICE": "Share the booking or shipment reference and I will generate the invoice.",
            "DELIVERY_CONFIRMATION": "Share the shipment reference and delivery proof details to confirm delivery.",
            "DELAY_MANAGEMENT": "Share the shipment reference and delay reason so I can log the delay.",
            "FAILED_SHIPMENT": "Share the shipment reference and failure reason so I can start recovery.",
            "CUSTOMER_NOTIFICATION": "Share the shipment or booking reference and the update to notify the customer.",
            "GET_ANALYTICS": "I can pull the operations dashboard for authorized admin users.",
            "DRIVER_UPDATE": "Share the driver or shipment reference and the assignment or status update.",
            "CUSTOMER_SUPPORT": "I can help with logistics support. Share the shipment, booking, or payment reference.",
        }.get(intent, "I can help with bookings, tracking, payments, driver updates, and shipment support.")

    def _query_rows(self, sql: str, params: Tuple[Any, ...]) -> List[Dict[str, Any]]:
        if get_db_connection is None:
            return []
        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
            return [self._serialize_row(row) for row in rows]
        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None and conn.is_connected():
                conn.close()

    def _load_support_tickets(self, user_id: str, *, query: str, limit: int) -> List[Dict[str, Any]]:
        if not user_id:
            return []
        table_names = ("support_tickets", "customer_support_tickets", "tickets")
        for table_name in table_names:
            try:
                return self._query_rows(
                    f"""
                    SELECT *
                    FROM {table_name}
                    WHERE user_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                    """,
                    (user_id, int(limit)),
                )
            except Exception:
                continue
        return []

    def _serialize_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        serialized = {}
        for key, value in dict(row or {}).items():
            if hasattr(value, "isoformat"):
                serialized[key] = value.isoformat(sep=" ", timespec="seconds")
            else:
                serialized[key] = value
        return serialized

    def _model_for(self, provider: str) -> str:
        if provider == "openai":
            return self.openai_model
        if provider == "gemini":
            return self.gemini_model
        return "deterministic_fallback"

    def _trim_text(self, text: str, max_tokens: int) -> str:
        token_budget = max(1, int(max_tokens))
        raw = str(text or "")
        if _estimate_tokens(raw) <= token_budget:
            return raw
        approx_chars = token_budget * 4
        head = raw[: int(approx_chars * 0.35)]
        tail = raw[-int(approx_chars * 0.6):]
        return f"{head}\n...[context trimmed]...\n{tail}"

    def _chunk_text(self, text: str, chunk_size: int = 42) -> Generator[str, None, None]:
        current = ""
        for word in str(text or "").split(" "):
            candidate = f"{current} {word}".strip()
            if len(candidate) >= chunk_size and current:
                yield current + " "
                current = word
            else:
                current = candidate
        if current:
            yield current

    def _extractive_summary(self, messages: Sequence[Any]) -> str:
        lines = []
        for item in list(messages or [])[-6:]:
            if isinstance(item, Mapping):
                content = str(item.get("content") or item.get("text") or item.get("message") or item)
            else:
                content = str(item)
            content = re.sub(r"\s+", " ", content).strip()
            if content:
                lines.append(f"- {content[:180]}")
        return "\n".join(lines[-4:])

    def _record_metric(
        self,
        *,
        provider_name: str,
        action: str,
        duration_ms: int,
        retry_count: int,
        status: str,
        request_id: Optional[str],
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if save_ai_tool_metric is None:
            return
        try:
            save_ai_tool_metric(
                user_id=None,
                role="system",
                workflow_type="llm",
                action=action,
                tool_name=provider_name,
                duration_ms=int(duration_ms),
                retry_count=int(retry_count),
                execution_status=status,
                ai_latency_ms=float(duration_ms),
                websocket_latency_ms=0,
                correlation_id=request_id,
                execution_id=request_id,
                metadata_json=dict(metadata or {}),
            )
        except Exception:
            pass

    def _log_warning(self, *parts: Any) -> None:
        if self.logger is None:
            return
        try:
            self.logger.warning("[llm_provider] " + " ".join(str(part) for part in parts))
        except Exception:
            pass


__all__ = ["LLMProvider", "LLMResponse", "ProviderAttempt"]
