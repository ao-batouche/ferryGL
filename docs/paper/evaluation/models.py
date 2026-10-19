"""The models compared in compare_models.py: prices, tokenizers and speeds.

Prices are each provider's standard (not batch) API prices in US dollars per million tokens,
read from the provider's own documentation on 2026-10-19 (sources below). Where a price is
set to change on an announced date, the later price is used, since the comparison projects a
year ahead; where a provider discounts some hours of the day, the full price is used.

Cache prices follow each provider's rules. Where a provider charges nothing extra to write
the cache, the write price is the input price. Long-prompt prices apply to the whole request.

``tokenizer`` names a tokenizer measured by count_tokens.py, when the model's own (or that
of the open-weight model it is based on) is public.

Speeds come from Artificial Analysis (https://artificialanalysis.ai, model pages retrieved
2026-10-19 12:40-12:45 UTC): median over 72 hours of each model's first-party API, with a
10,000-token prompt. ``first_token`` is the time to the first answer token, which includes
any reasoning, and ``speed`` the output speed, both for the fastest configuration measured
(the lowest time to first answer token among the model's effort settings). ``None`` means
Artificial Analysis had no measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_cost import Price


@dataclass(frozen=True)
class Model:
    provider: str
    name: str
    model_id: str
    price: Price
    tokenizer: str | None = None  # a tokenizer measured in data/tokenizers.csv, if public
    speed: float | None = None  # output tokens per second
    first_token: float | None = None  # seconds to the first answer token
    speed_variant: str = ""  # the Artificial Analysis configuration measured
    note: str = ""


def price(name, input, write, read, output, **extra) -> Price:  # noqa: A002
    return Price(name, input, write, read, output, **extra)


# Anthropic. https://platform.claude.com/docs/en/about-claude/pricing and
# https://platform.claude.com/docs/en/build-with-claude/prompt-caching: 5-minute cache writes
# at 1.25x input; prompts shorter than 512 tokens aren't cached.
ANTHROPIC = [
    Model(
        "Anthropic",
        "Claude Fable 5.1",
        "claude-fable-5-1",
        price("Claude Fable 5.1", 10.00, 12.50, 0.25, 50.00, min_cache=512),
        speed=57.4,
        first_token=2.19,
        speed_variant="Low",
    ),
    Model(
        "Anthropic",
        "Claude Opus 5.5",
        "claude-opus-5-5",
        price("Claude Opus 5.5", 4.00, 5.00, 0.20, 20.00, min_cache=512),
        speed=78.3,
        first_token=9.89,
        speed_variant="Low",
    ),
    Model(
        "Anthropic",
        "Claude Sonnet 5.5",
        "claude-sonnet-5-5",
        price("Claude Sonnet 5.5", 2.00, 2.50, 0.10, 10.00, min_cache=512),
        speed=108.9,
        first_token=0.99,
        speed_variant="Low",
    ),
    Model(
        "Anthropic",
        "Claude Haiku 5.5",
        "claude-haiku-5-5",
        price(
            "Claude Haiku 5.5",
            0.10,
            0.125,
            0.01,
            0.50,
            min_cache=512,
            long_prompt=Price("", 0.50, 0.625, 0.05, 2.50),
            long_threshold=100_000,
        ),
        speed=165.7,
        first_token=8.39,
        speed_variant="Low",
    ),
]

# OpenAI. https://developers.openai.com/api/docs/pricing, the model pages and the prompt
# caching guide: cache writes at 1.25x input; prompts shorter than 1,024 tokens aren't
# cached; prompts over 272K input tokens cost 2x input and cache rates and 1.5x output.
OPENAI = [
    Model(
        "OpenAI",
        "GPT-6 Astra",
        "gpt-6-astra",
        price(
            "GPT-6 Astra",
            10.00,
            12.50,
            1.00,
            50.00,
            min_cache=1024,
            long_prompt=Price("", 20.00, 25.00, 2.00, 75.00),
            long_threshold=272_000,
        ),
        speed=55.8,
        first_token=2.37,
        speed_variant="Low",
    ),
    Model(
        "OpenAI",
        "GPT-6.1 Sol",
        "gpt-6.1-sol",
        price(
            "GPT-6.1 Sol",
            2.00,
            2.50,
            0.10,
            10.00,
            min_cache=1024,
            long_prompt=Price("", 4.00, 5.00, 0.20, 15.00),
            long_threshold=272_000,
        ),
        speed=43.9,
        first_token=3.02,
        speed_variant="Low",
    ),
    Model(
        "OpenAI",
        "GPT-6 Luna",
        "gpt-6-luna",
        price(
            "GPT-6 Luna",
            0.10,
            0.125,
            0.01,
            0.50,
            min_cache=1024,
            long_prompt=Price("", 0.20, 0.25, 0.02, 0.75),
            long_threshold=272_000,
        ),
        speed=123.7,
        first_token=0.79,
        speed_variant="Non-reasoning",
    ),
]

# Google. https://ai.google.dev/gemini-api/docs/pricing and the caching guide: implicit
# caching only, at no extra write cost; Gemini 4 Argon and 3.8 Flash cache prompts of at least
# 4,096 tokens. Argon's price applies from 2026-12-01 and 3.8 Flash's from 2027-01-01; both
# are half that until then.
GOOGLE = [
    Model(
        "Google",
        "Gemini 4 Argon",
        "gemini-4-argon",
        price("Gemini 4 Argon", 4.00, 4.00, 0.20, 20.00, min_cache=4096),
        note="price from 2026-12-01; half until then",
    ),
    Model(
        "Google",
        "Gemini 3.8 Flash",
        "gemini-3.8-flash",
        price("Gemini 3.8 Flash", 1.50, 1.50, 0.15, 7.50, min_cache=4096),
        speed=154.5,
        first_token=23.96,
        speed_variant="High (the only one measured)",
        note="price from 2027-01-01; half until then",
    ),
    # Gemini 3.5 Flash-Lite's shortest cacheable prompt isn't published; none is assumed.
    Model(
        "Google",
        "Gemini 3.5 Flash-Lite",
        "gemini-3.5-flash-lite",
        price("Gemini 3.5 Flash-Lite", 0.30, 0.30, 0.03, 2.50),
        speed=447.1,
        first_token=7.41,
        speed_variant="default",
    ),
]

# xAI. https://docs.x.ai/developers/pricing: automatic caching with no write fee; prompts of
# 200,000 tokens or more are billed at the higher rate for all tokens.
XAI = [
    Model(
        "xAI",
        "Grok 4.7",
        "grok-4.7",
        price(
            "Grok 4.7",
            2.00,
            2.00,
            0.50,
            6.00,
            long_prompt=Price("", 4.00, 4.00, 1.00, 12.00),
            long_threshold=199_999,
        ),
        speed=76.9,
        first_token=7.65,
        speed_variant="Low",
    ),
    Model(
        "xAI",
        "Grok 4.3",
        "grok-4.3",
        price(
            "Grok 4.3",
            1.25,
            1.25,
            0.20,
            2.50,
            long_prompt=Price("", 2.50, 2.50, 0.40, 5.00),
            long_threshold=199_999,
        ),
    ),
]

# DeepSeek. https://api-docs.deepseek.com/quick_start/pricing: automatic disk caching with no
# write fee. Peak prices (01:00-04:00 and 06:00-10:00 UTC on weekdays); other hours cost half.
DEEPSEEK = [
    Model(
        "DeepSeek",
        "DeepSeek V4 Pro",
        "deepseek-v4-pro",
        price("DeepSeek V4 Pro", 1.32, 1.32, 0.044, 3.96),
        tokenizer="DeepSeek V4 Pro",
        speed=104.8,
        first_token=1.65,
        speed_variant="Non-reasoning",
        note="peak price; off-peak hours cost half",
    ),
    Model(
        "DeepSeek",
        "DeepSeek V4.1 Flash",
        "deepseek-flash",
        price("DeepSeek V4.1 Flash", 0.30, 0.30, 0.006, 1.20),
        tokenizer="DeepSeek V4.1 Flash",
        speed=227.1,
        first_token=9.89,
        speed_variant="Max",
        note="peak price; off-peak hours cost half",
    ),
]

# Moonshot AI. https://platform.kimi.ai/docs/pricing/chat: automatic prefix caching; Kimi K3's
# 5-minute cache writes cost the input price; K2.7 Code charges no cache writes.
MOONSHOT = [
    Model(
        "Moonshot AI",
        "Kimi K3",
        "kimi-k3",
        price("Kimi K3", 3.00, 3.00, 0.30, 15.00),
        tokenizer="Kimi K3",
        speed=42.3,
        first_token=50.89,
        speed_variant="Max",
    ),
    Model(
        "Moonshot AI",
        "Kimi K2.7 Code",
        "kimi-k2.7-code",
        price("Kimi K2.7 Code", 0.95, 0.95, 0.19, 4.00),
        tokenizer="Kimi K2.7 Code",
        speed=57.2,
        first_token=41.92,
        speed_variant="default",
    ),
]

# Alibaba Cloud Model Studio, international (Singapore) prices, e.g.
# https://www.alibabacloud.com/help/en/model-studio/qwen3-8-max: implicit caching, automatic,
# for prompts of at least 1,024 tokens, at no extra write cost.
ALIBABA = [
    Model(
        "Alibaba",
        "Qwen3.8 Max",
        "qwen3.8-max",
        price("Qwen3.8 Max", 2.00, 2.00, 0.25, 6.00, min_cache=1024),
        tokenizer="Qwen3.8 Max",
        speed=37.8,
        first_token=56.03,
        speed_variant="default",
    ),
    Model(
        "Alibaba",
        "Qwen3.8 Flash",
        "qwen3.8-flash",
        price("Qwen3.8 Flash", 0.15, 0.15, 0.016, 0.47, min_cache=1024),
        tokenizer="Qwen3.8 Flash",
        speed=52.4,
        first_token=40.20,
        speed_variant="Qwen3.8-Flash-Next",
    ),
]

# Z.ai. https://docs.z.ai/guides/overview/pricing: implicit caching; cache storage is listed
# as "Limited-time Free".
ZAI = [
    Model(
        "Z.ai",
        "GLM-5.3",
        "glm-5.3",
        price("GLM-5.3", 1.40, 1.40, 0.26, 4.40),
        tokenizer="GLM-5.3",
        speed=95.3,
        first_token=23.39,
        speed_variant="Max",
    ),
    Model(
        "Z.ai",
        "GLM-5.3-Flash",
        "glm-5.3-flash",
        price("GLM-5.3-Flash", 0.15, 0.15, 0.03, 0.50),
        tokenizer="GLM-5.3-Flash",
        speed=78.0,
        first_token=28.17,
        speed_variant="default",
    ),
]

# MiniMax. https://platform.minimax.io/docs/pricing/overview: automatic caching for prompts
# of at least 512 tokens; MiniMax-M3 costs twice as much above 512K input tokens.
MINIMAX = [
    Model(
        "MiniMax",
        "MiniMax-M3",
        "MiniMax-M3",
        price(
            "MiniMax-M3",
            0.30,
            0.30,
            0.06,
            1.20,
            min_cache=512,
            long_prompt=Price("", 0.60, 0.60, 0.12, 2.40),
            long_threshold=512_000,
        ),
        tokenizer="MiniMax-M3",
        speed=110.2,
        first_token=19.24,
        speed_variant="default",
    ),
    Model(
        "MiniMax",
        "MiniMax-M3.1-Flash",
        "MiniMax-M3.1-Flash",
        price("MiniMax-M3.1-Flash", 0.15, 0.15, 0.015, 0.60, min_cache=512),
        note="released 2026-10-19",
    ),
]

# Mistral AI. https://docs.mistral.ai/inference/pricing and the prompt caching guide. Mistral
# Large 4 is in public preview and lists its own cached-input price; it is on sale at half its
# regular prices, and the regular prices are used here. Mistral Small 4 lists no cached-input
# price, so the caching guide's rule of 10% of the input price is used.
MISTRAL = [
    Model(
        "Mistral AI",
        "Mistral Large 4",
        "mistral-large-4",
        price("Mistral Large 4", 1.36, 1.36, 0.14, 4.18),
        speed=72.8,
        first_token=29.20,
        speed_variant="Preview",
        note="public preview; regular price",
    ),
    Model(
        "Mistral AI",
        "Mistral Small 4",
        "mistral-small-2603",
        price("Mistral Small 4", 0.15, 0.15, 0.015, 0.60),
        tokenizer="Mistral Small 4",
        speed=176.1,
        first_token=0.80,
        speed_variant="Non-reasoning",
    ),
]

MODELS: list[Model] = [
    *ANTHROPIC,
    *OPENAI,
    *GOOGLE,
    *XAI,
    *DEEPSEEK,
    *MOONSHOT,
    *ALIBABA,
    *ZAI,
    *MINIMAX,
    *MISTRAL,
]

# GitHub Actions, https://docs.github.com/en/billing/reference/actions-runner-pricing: a
# standard Linux 2-core runner in a private repository, per minute, each job rounded up to a
# whole minute; 2,000 minutes a month are free on GitHub Free, and public repositories are free.
ACTIONS_MINUTE_PRICE = 0.006
ACTIONS_FREE_MINUTES = 2000
