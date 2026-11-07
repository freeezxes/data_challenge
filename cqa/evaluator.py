"""Оценка диалога через Claude API: структурированный ответ, проверка, голосование, стоимость."""
from __future__ import annotations

import hashlib
import json
import statistics
import time

from jsonschema import validate
from collections import Counter
from dataclasses import dataclass

from .checklist import criterion_map, normalize_answer, scale_values
from .facts import extract_facts, public_facts
from .prompt import build_system_prompt, build_user_message
from .schema import output_schema
from .scoring import score
from .verify import check_rationale

# $ за 1M токенов: вход, выход, чтение кэша, запись кэша (5 мин). Цены Anthropic на 2026-10.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
# Модели, для которых включаем серверный fallback при отказе классификатора безопасности.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class EvaluationError(RuntimeError):
    pass


@dataclass
class EvalConfig:
    model: str = "claude-sonnet-5-5"
    effort: str = "high"  # low | medium | high | xhigh | max
    votes: int = 1
    max_tokens: int = 16000
    use_fallback: bool = False
    repair: bool = True  # одна повторная попытка, если ответ противоречит фактам


def cost_usd(model: str, usage: dict) -> float | None:
    price = PRICES.get(model)
    if not price:
        return None
    p_in, p_out, p_read, p_write = price
    return round((usage.get("input_tokens", 0) * p_in + usage.get("output_tokens", 0) * p_out
                  + usage.get("cache_read_input_tokens", 0) * p_read
                  + usage.get("cache_creation_input_tokens", 0) * p_write) / 1_000_000, 6)


def _usage_dict(usage) -> dict:
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {k: int(getattr(usage, k, 0) or 0) for k in keys}


def _add_usage(total: dict, part: dict) -> dict:
    for k, v in part.items():
        total[k] = total.get(k, 0) + v
    return total


def _criterion_by_title(checklist: dict, word: str) -> dict | None:
    return next((c for c in checklist["criteria"] if word.lower() in c["title"].lower()), None)


def validate_run(checklist: dict, conv: dict, facts: dict, raw: dict) -> tuple[dict, list[str]]:
    """Нормализует ответ модели и возвращает (детали по критериям, список проблем)."""
    ids = {m["id"] for m in conv["messages"]}
    cmap = criterion_map(checklist)
    details, issues = {}, []
    for cid, c in cmap.items():
        item = raw["criteria"][cid]
        answer = normalize_answer(c, item["answer"])
        evidence = [e for e in item.get("evidence_ids", []) if e in ids]
        bad = [e for e in item.get("evidence_ids", []) if e not in ids]
        if bad:
            issues.append(f"{cid}: несуществующие ID реплик {bad}")
        d = {"answer": answer, "evidence_ids": evidence, "checked_range": item.get("checked_range", ""),
             "quote": item.get("quote", ""), "reasoning": item.get("reasoning", "")}
        issues += [i for i in check_rationale(conv, facts, c, answer, d) if "несуществующие" not in i]
        if c["type"] == "critical":
            d["violations"] = item.get("violations", [])
            if answer == "yes" and not d["violations"]:
                issues.append(f"{cid}: ответ «Да», но не указано, какое нарушение")
            if answer == "no" and d["violations"]:
                issues.append(f"{cid}: ответ «Нет», но перечислены нарушения {d['violations']}")
        details[cid] = d

    ident = _criterion_by_title(checklist, "Идентификац")
    if ident and conv.get("channel") != "voice call" and details[ident["id"]]["answer"] == "yes" \
            and not facts["identity_confirmed_ids"]:
        issues.append(f"{ident['id']}: «Да», но системного подтверждения личности в чате нет")
    if ident and details[ident["id"]]["answer"] == "na" and facts.get("identity_requested_ids") \
            and not facts["identity_confirmed_ids"] and conv.get("channel") != "voice call":
        issues.append(f"{ident['id']}: N/A, но оператор сам говорил о проверке личности, а подтверждения нет — "
                      "проверь: если работа шла с конкретным счётом или платежом клиента, по памятке это «Нет»")
    closing = _criterion_by_title(checklist, "Завершени")
    cut = {"client_left", "connection_lost", "client_ended_call"}
    if closing and details[closing["id"]]["answer"] == "na" and not any(
            e["type"] in cut for e in facts["end_events"]):
        issues.append(f"{closing['id']}: N/A, но события обрыва (клиент ушёл / связь прервалась) нет")
    return details, issues


def aggregate(checklist: dict, runs: list[dict]) -> dict:
    """Голосование по прогонам: большинство для Да/Нет, медиана для шкалы."""
    out = {}
    for c in checklist["criteria"]:
        cid = c["id"]
        votes = [r["details"][cid]["answer"] for r in runs]
        if c["type"] == "scale":
            na = votes.count("na")
            nums = [v for v in votes if v != "na"]
            if na > len(nums):
                final = "na"
            else:
                med = statistics.median_low(nums)
                final = min(scale_values(c), key=lambda s: abs(s - med))
        else:
            counts = Counter(votes)
            top = max(counts.values())
            final = next(v for v in votes if counts[v] == top)  # при равенстве — первый прогон
        source = next(r["details"][cid] for r in runs if r["details"][cid]["answer"] == final) \
            if final in votes else runs[0]["details"][cid]
        out[cid] = {**source, "answer": final,
                    "confidence": round(votes.count(final) / len(votes), 2), "votes": votes}
    return out


class Evaluator:
    def __init__(self, checklist: dict, client=None, config: EvalConfig | None = None,
                 examples: list[tuple[dict, dict]] | None = None):
        self.checklist = checklist
        self.config = config or EvalConfig()
        if client is None:
            import anthropic
            client = anthropic.Anthropic(timeout=120.0, max_retries=2)
        self.client = client
        self.system_prompt = build_system_prompt(checklist, examples)
        self.schema = output_schema(checklist)
        self.prompt_hash = hashlib.sha256(
            (self.system_prompt + json.dumps(self.schema, sort_keys=True)).encode()).hexdigest()[:12]

    def _call(self, user_text: str, calls: list) -> tuple[dict, dict, str]:
        cfg = self.config
        kwargs = dict(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=[{"type": "text", "text": self.system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_text}],
            output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": self.schema}},
        )
        if cfg.use_fallback and cfg.model in FALLBACK_MODELS:
            resp = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            resp = self.client.messages.create(**kwargs)
        calls.append({"model": getattr(resp, "model", cfg.model), "usage": _usage_dict(resp.usage),
                      "stop_reason": resp.stop_reason,
                      "content": [{"type": b.type, "text": b.text} for b in resp.content if getattr(b, "type", None) == "text"]})
        if resp.stop_reason == "refusal":
            raise EvaluationError("модель отказалась отвечать (refusal)")
        if resp.stop_reason == "max_tokens":
            raise EvaluationError("ответ обрезан по max_tokens")
        texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        if not texts:
            raise EvaluationError("в ответе нет текстового блока")
        raw = json.loads(texts[-1])
        validate(raw, self.schema)
        return raw, _usage_dict(resp.usage), getattr(resp, "model", cfg.model)

    def evaluate_once(self, conv: dict, facts: dict, calls: list) -> dict:
        user_text = build_user_message(conv, facts)
        usage: dict = {}
        raw, u, served = self._call(user_text, calls)
        _add_usage(usage, u)
        details, issues = validate_run(self.checklist, conv, facts, raw)
        repaired = False
        if issues and self.config.repair:
            note = ("\n\nВНИМАНИЕ: предыдущая попытка оценки содержала ошибки, перепроверь эти места:\n- "
                    + "\n- ".join(issues))
            raw2, u2, served = self._call(user_text + "\nПредыдущий ответ:\n" + json.dumps(raw, ensure_ascii=False) + note, calls)
            _add_usage(usage, u2)
            details2, issues2 = validate_run(self.checklist, conv, facts, raw2)
            if len(issues2) <= len(issues):
                raw, details, issues, repaired = raw2, details2, issues2, True
        return {"details": details, "issues": issues, "summary": raw.get("summary", ""),
                "usage": usage, "served_model": served, "repaired": repaired}

    def evaluate(self, conv: dict) -> dict:
        started = time.monotonic()
        facts = extract_facts(conv)
        calls = []
        try:
            runs = [self.evaluate_once(conv, facts, calls) for _ in range(max(1, self.config.votes))]
        except Exception as exc:
            error = EvaluationError(str(exc))
            error.calls = calls
            raise error from exc
        criteria = aggregate(self.checklist, runs)
        answers = {cid: d["answer"] for cid, d in criteria.items()}
        usage: dict = {}
        for r in runs:
            _add_usage(usage, r["usage"])
        return {
            "conversation_id": conv["conversation_id"],
            "model": self.config.model,
            "served_models": sorted({c["model"] for c in calls}),
            "calls": calls,
            "status": "needs_review" if any(r["issues"] for r in runs) else "ok",
            "language": conv.get("language"), "channel": conv.get("channel"),
            "effort": self.config.effort,
            "votes": len(runs),
            "checklist_id": self.checklist.get("id"),
            "checklist_version": self.checklist.get("version"),
            "prompt_hash": self.prompt_hash,
            "answers": answers,
            "criteria": criteria,
            "score": score(self.checklist, answers),
            "summary": runs[0]["summary"],
            "issues": sorted({i for r in runs for i in r["issues"]}),
            "facts": public_facts(facts),
            "usage": usage,
            "cost_usd": sum(cost_usd(c["model"], c["usage"]) for c in calls)
                        if all(cost_usd(c["model"], c["usage"]) is not None for c in calls) else None,
            "latency_s": round(time.monotonic() - started, 2),
        }
