"""Model layer. One call shape for every provider: complete_json(prompt, variables).

- Providers: gemini and openai_compat (OpenAI SDK), anthropic (Anthropic SDK), mock (tests).
- Every call is cached on disk by provider, endpoint, model, prompt version,
  prompt text, temperature and sample index; writes are atomic, and an
  unreadable cache file counts as a miss.
- A caller can pass `check`; if it returns problems, the model is shown them
  once and asked for a corrected answer. The best usable answer is kept and
  both answers are logged.
- Retries follow the HTTP status when there is one (429, 5xx, 408/409);
  timeouts and dropped connections are retried; anything else fails fast.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

SYSTEM = ("You are a senior promo editor for vertical microdrama series. "
          "Answer with one JSON object only: no prose before or after it, no code fences.")

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class LLMError(RuntimeError):
    pass


class PendingAnswer(LLMError):
    """provider 'external': the prompt was written to <exchange>/pending/ and awaits an answer file."""


def load_prompt(name: str) -> tuple[str, str]:
    raw = (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
    first, _, body = raw.partition("\n")
    m = re.match(r"version:\s*(\S+)", first.strip())
    return (m.group(1) if m else "0"), (body if m else raw)


def render(template: str, variables: dict) -> str:
    def sub(m):
        key = m.group(1)
        if key not in variables:
            raise KeyError(f"prompt variable '{key}' not supplied")
        return str(variables[key])
    return re.sub(r"\{\{(\w+)\}\}", sub, template)


def extract_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b <= a:
        raise ValueError("no JSON object in the answer")
    chunk = t[a:b + 1]
    try:
        return json.loads(chunk)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", chunk))   # common slip: trailing commas


def _daily_quota(e: Exception) -> bool:
    """Gemini free tier: a 429 whose quota is per day will not clear by waiting minutes."""
    m = str(e)
    return ("429" in m or "RESOURCE_EXHAUSTED" in m or getattr(e, "status_code", None) == 429) and \
        ("PerDay" in m or "per day" in m.lower() or "daily" in m.lower())


def _retryable(e: Exception) -> bool:
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if isinstance(status, int):
        return status in RETRY_STATUS
    names = {c.__name__ for c in type(e).__mro__}
    if names & {"APITimeoutError", "APIConnectionError", "Timeout", "TimeoutError", "ConnectionError",
                "ReadTimeout", "ConnectTimeout", "RemoteProtocolError"}:
        return True
    msg = str(e).lower()
    return any(k in msg for k in ("timed out", "timeout", "rate limit", "overloaded", "temporarily unavailable",
                                  "connection reset", "connection aborted"))


class LLM:
    def __init__(self, llm_cfg: dict, cache_dir: str | Path, log_path: str | Path | None = None,
                 model: str | None = None, mock_fn=None):
        self.cfg = llm_cfg
        self.provider = llm_cfg["provider"]
        self.model = model or llm_cfg["model"]
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = Path(log_path) if log_path else None
        self.mock_fn = mock_fn
        self._lock = threading.Lock()
        self._client = None
        self.usage = {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0, "errors": 0}
        self.error_samples: list[str] = []
        self.stop_reason: str | None = None     # set once a daily quota is exhausted
        self.pending: set[str] = set()           # provider 'external': prompts waiting for an answer
        self.exchange = Path(llm_cfg.get("exchange_dir") or ".exchange")
        self._meta = threading.local()

    # ---------- provider plumbing ----------
    def _get_client(self):
        if self._client is not None:
            return self._client
        if self.provider in ("gemini", "openai_compat"):
            if not self.cfg.get("api_key"):
                raise LLMError(f"No API key for provider '{self.provider}'. Set GEMINI_API_KEY or LLM_API_KEY.")
            if self.provider == "openai_compat" and not self.cfg.get("base_url"):
                raise LLMError("openai_compat needs LLM_BASE_URL (the endpoint's /v1 URL).")
            from openai import OpenAI
            self._client = OpenAI(api_key=self.cfg["api_key"], base_url=self.cfg.get("base_url"),
                                  timeout=float(self.cfg.get("timeout_s", 180)), max_retries=0)
        elif self.provider == "anthropic":
            if not self.cfg.get("api_key"):
                raise LLMError("No API key for provider 'anthropic'. Set ANTHROPIC_API_KEY.")
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.cfg["api_key"], timeout=float(self.cfg.get("timeout_s", 180)),
                                                  max_retries=0)
        elif self.provider != "mock":
            raise LLMError(f"Unknown provider '{self.provider}'")
        return self._client

    def _raw_call(self, messages: list[dict], temperature: float) -> tuple[str, int, int]:
        max_out = int(self.cfg.get("max_output_tokens", 8000))
        if self.provider in ("gemini", "openai_compat"):
            client = self._get_client()
            kwargs = dict(model=self.model, temperature=temperature, max_tokens=max_out,
                          messages=[{"role": "system", "content": SYSTEM}] + messages)
            try:
                r = client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
            except Exception as e:  # some endpoints reject JSON mode; the prompt already demands JSON
                m = str(e).lower()
                if getattr(e, "status_code", None) == 400 and ("response_format" in m or "json" in m):
                    r = client.chat.completions.create(**kwargs)
                else:
                    raise
            u = getattr(r, "usage", None)
            content = r.choices[0].message.content if r.choices else ""
            return (content or "", getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)
        if self.provider == "anthropic":
            client = self._get_client()
            r = client.messages.create(model=self.model, max_tokens=max_out, temperature=temperature,
                                       system=SYSTEM, messages=messages)
            text = "".join(getattr(b, "text", "") or "" for b in r.content)
            return text, r.usage.input_tokens, r.usage.output_tokens
        if self.provider == "external":
            return self._external(messages, temperature)
        raise LLMError("mock provider has no raw call")

    def _external(self, messages, temperature) -> tuple[str, int, int]:
        """Answer files instead of an API: any model, agent or person can answer the exact prompts."""
        key = hashlib.sha256(json.dumps([self.model, SYSTEM, messages]).encode()).hexdigest()[:32]
        ans = self.exchange / "answers" / f"{key}.txt"
        if ans.exists():
            text = ans.read_text(encoding="utf-8")
            return text, sum(len(m["content"]) for m in messages) // 4, len(text) // 4
        (self.exchange / "pending").mkdir(parents=True, exist_ok=True)
        meta = getattr(self._meta, "value", {}) or {}
        (self.exchange / "pending" / f"{key}.json").write_text(json.dumps(
            {"key": key, "prompt": meta.get("prompt"), "tag": meta.get("tag"), "round": len(messages) // 2 + 1,
             "system": SYSTEM, "messages": messages}, indent=1), encoding="utf-8")
        with self._lock:
            self.pending.add(key)
        raise PendingAnswer(key)

    def _call_with_retries(self, messages, temperature) -> tuple[str, int, int]:
        delay, tries = 4.0, int(self.cfg.get("max_retries", 6))
        for attempt in range(tries + 1):
            try:
                return self._raw_call(messages, temperature)
            except LLMError:
                raise
            except Exception as e:
                if _daily_quota(e):
                    self.stop_reason = ("The model's daily quota is used up. Finished calls are cached: "
                                        "run the same command again after the quota resets to continue.")
                    raise LLMError(self.stop_reason) from e
                if not _retryable(e) or attempt == tries:
                    raise LLMError(f"{type(e).__name__}: {e}") from e
                time.sleep(delay)
                delay = min(delay * 2, 90)
        raise LLMError("unreachable")

    # ---------- cache ----------
    def _cache_get(self, f: Path):
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:      # missing, half-written or corrupt: treat as a miss
            return None

    def _cache_put(self, f: Path, obj: dict):
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)
        os.replace(tmp, f)

    # ---------- public ----------
    def complete_json(self, prompt_name: str, variables: dict, temperature: float | None = None,
                      sample: int = 0, check=None, tag: str = "") -> dict:
        """Returns {"data": dict, "attempts": [raw texts], "problems": [remaining problems], ...}.
        Raises LLMError when no usable JSON object came back."""
        self._meta.value = {"prompt": prompt_name, "tag": tag}
        try:
            return self._complete(prompt_name, variables, temperature, sample, check, tag)
        except PendingAnswer:
            raise
        except LLMError as e:
            with self._lock:
                self.usage["errors"] += 1
                if len(self.error_samples) < 5:
                    self.error_samples.append(f"{prompt_name} {tag}: {e}"[:300])
            raise

    def _complete(self, prompt_name, variables, temperature, sample, check, tag) -> dict:
        if self.stop_reason and self.provider != "mock":
            cached_only = True
        else:
            cached_only = False
        version, template = load_prompt(prompt_name)
        visible = {k: v for k, v in variables.items() if not k.startswith("_")}
        prompt = render(template, visible)
        temp = self.cfg.get("temperature", 0.2) if temperature is None else temperature
        key_src = json.dumps([self.provider, self.cfg.get("base_url"), self.model, prompt_name, version,
                              prompt, temp, sample])
        key = hashlib.sha256(key_src.encode()).hexdigest()[:32]
        cache_file = self.cache_dir / f"{prompt_name}-{key}.json"
        cached = self._cache_get(cache_file)
        if isinstance(cached, dict) and isinstance(cached.get("data"), dict):
            self._log(prompt_name, tag, sample, cached=True, rec=cached)
            return cached
        if cached_only:
            raise LLMError(self.stop_reason)

        def run_check(data) -> list[str]:
            if not isinstance(data, dict):
                return ["The answer must be one JSON object (curly braces), not a list or a string."]
            if not check:
                return []
            try:
                return [str(p) for p in (check(data) or [])]
            except Exception as e:    # a malformed answer must never crash the run
                return [f"The answer has the wrong structure ({type(e).__name__}: {e}). Follow the format exactly."]

        t0 = time.time()
        attempts, tin, tout = [], 0, 0
        messages = [{"role": "user", "content": prompt}]
        best, best_problems = None, None
        for round_ in range(2):  # first answer, then at most one correction
            if self.provider == "mock":
                text = json.dumps(self.mock_fn(prompt_name, variables, sample, round_))
                i_tok, o_tok = len(prompt) // 4, len(text) // 4
            else:
                text, i_tok, o_tok = self._call_with_retries(messages, temp)
            tin, tout = tin + i_tok, tout + o_tok
            attempts.append(text)
            try:
                data = extract_json(text)
                problems = run_check(data)
            except (ValueError, json.JSONDecodeError) as e:
                data, problems = None, [f"Your answer was not valid JSON ({e})."]
            if isinstance(data, dict) and (best is None or len(problems) <= len(best_problems)):
                best, best_problems = data, problems
            if isinstance(data, dict) and not problems:
                break
            feedback = ("Your answer has these problems:\n- " + "\n- ".join(problems[:30]) +
                        "\nReturn the complete corrected JSON object, same format, fixing every problem.")
            if text.strip():
                messages = messages + [{"role": "assistant", "content": text},
                                       {"role": "user", "content": feedback}]
            else:   # some APIs reject an empty assistant turn
                messages = [{"role": "user", "content": prompt + "\n\n(Your previous answer was empty.) " + feedback}]
        if best is None:
            raise LLMError(f"no usable JSON object after correction; first answer began: {attempts[0][:120]!r}")
        out = {"data": best, "attempts": attempts, "problems": best_problems, "model": self.model,
               "prompt_version": version, "input_tokens": tin, "output_tokens": tout,
               "seconds": round(time.time() - t0, 2)}
        self._cache_put(cache_file, out)
        self._log(prompt_name, tag, sample, cached=False, rec=out)
        return out

    def _log(self, prompt_name, tag, sample, cached, rec):
        with self._lock:
            self.usage["calls"] += 1
            if cached:
                self.usage["cached"] += 1
            else:
                self.usage["input_tokens"] += rec.get("input_tokens", 0)
                self.usage["output_tokens"] += rec.get("output_tokens", 0)
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"prompt": prompt_name, "tag": tag, "sample": sample, "cached": cached,
                                         "model": self.model, "rounds": len(rec.get("attempts", [])),
                                         "unfixed_problems": len(rec.get("problems") or []),
                                         "input_tokens": rec.get("input_tokens", 0),
                                         "output_tokens": rec.get("output_tokens", 0),
                                         "seconds": rec.get("seconds", 0)}) + "\n")
