"""Count an agent's tokens with public tokenizers, for the white paper.

The cost model in agent_cost.py turns characters into tokens with an assumed ratio. This
script checks that ratio. It runs ferryGL over a route of 100 items, keeps the text an agent
would read and send (tool results and calls, its instructions and tool definitions, and what
ferryGL prints), and counts it with every public tokenizer of a model family in the
comparison. Claude, GPT-6, Gemini and Grok have no published offline tokenizer, so they aren't
counted; OpenAI's o200k_base, used by its GPT-5 models, is counted for reference.

It downloads the tokenizers from Hugging Face, and needs two packages that ferryGL doesn't:

    uv run --with tiktoken --with tokenizers --with huggingface_hub \\
        python docs/paper/evaluation/count_tokens.py
"""

from __future__ import annotations

import ast
import csv
import json
import sys
from collections.abc import Callable
from pathlib import Path

import tiktoken
from agent_cost import DATA, DELEGATE_INSTRUCTIONS, TOOLS, instructions, scaling
from huggingface_hub import hf_hub_download
from tiktoken.load import load_tiktoken_bpe
from tokenizers import Tokenizer

# Hugging Face tokenizers of the open-weight models in the comparison (models.py). An API
# model "based on" an open model is counted with that model's tokenizer.
HF_TOKENIZERS = {
    "DeepSeek V4 Pro": "deepseek-ai/DeepSeek-V4-Pro-0813",
    "DeepSeek V4.1 Flash": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
    "Qwen3.8 Max": "Qwen/Qwen3.8-2.4T-A95B",
    "Qwen3.8 Flash": "Qwen/Qwen3.8-Flash-Next",
    "GLM-5.3": "zai-org/GLM-5.3",
    "GLM-5.3-Flash": "zai-org/GLM-5.3-Flash",
    "MiniMax-M3": "MiniMaxAI/MiniMax-M3",
    "Mistral Small 4": "mistralai/Mistral-Small-4-119B-2603",
}
# Kimi models ship a tiktoken file and a Python loader instead of tokenizer.json.
KIMI_TOKENIZERS = {
    "Kimi K3": "moonshotai/Kimi-K3",
    "Kimi K2.7 Code": "moonshotai/Kimi-K2.7-Code",
}


def kimi(repo: str) -> Callable[[str], int]:
    """A Kimi tokenizer: a tiktoken BPE file and the split pattern in its loader code."""
    ranks = load_tiktoken_bpe(hf_hub_download(repo, "tiktoken.model"))
    # Parsed, never run: the pattern is a list of string literals passed to "|".join.
    code = Path(hf_hub_download(repo, "tokenization_kimi.py")).read_text("utf-8")
    pattern = None
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pat_str" for t in node.targets
        ):
            pattern = "|".join(ast.literal_eval(node.value.args[0]))
    if pattern is None:
        raise RuntimeError("couldn't find the split pattern in tokenization_kimi.py")
    encoding = tiktoken.Encoding(
        repo.split("/")[1], pat_str=pattern, mergeable_ranks=ranks, special_tokens={}
    )
    return lambda text: len(encoding.encode(text, disallowed_special=()))


def counters() -> dict[str, Callable[[str], int]]:
    o200k = tiktoken.get_encoding("o200k_base")
    found: dict[str, Callable[[str], int]] = {
        "OpenAI o200k_base": lambda text: len(o200k.encode(text, disallowed_special=())),
    }
    for name, repo in HF_TOKENIZERS.items():
        tokenizer = Tokenizer.from_pretrained(repo)
        found[name] = lambda text, t=tokenizer: len(t.encode(text, add_special_tokens=False).ids)
    for name, repo in KIMI_TOKENIZERS.items():
        found[name] = kimi(repo)
    return found


def corpus() -> dict[str, str]:
    """The text of one run of a 100-item route that finds a twentieth of the items changed."""
    changed = scaling([100], seed=20261019, keep_text=True)[100]["changed"]
    return {
        "tool results and calls": "\n".join(changed.texts or []),
        "instructions and tools": instructions() + "\n" + json.dumps(TOOLS),
        "delegation: instructions and ferryGL's output": (
            DELEGATE_INSTRUCTIONS + "\n" + changed.printed_text
        ),
    }


def main() -> int:
    texts = corpus()
    rows = []
    for name, count in counters().items():
        for part, text in texts.items():
            tokens = count(text)
            rows.append(
                {
                    "tokenizer": name,
                    "text": part,
                    "characters": len(text),
                    "tokens": tokens,
                    "characters_per_token": round(len(text) / tokens, 2),
                }
            )
            print(
                f"{name:<20} {part:<48} {len(text):>8,} chars {tokens:>8,} tokens "
                f"{len(text) / tokens:5.2f} chars/token"
            )
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "tokenizers.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
