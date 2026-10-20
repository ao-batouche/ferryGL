# ferryGL white paper

[`ferrygl-white-paper.pdf`](ferrygl-white-paper.pdf) describes ferryGL as token-free
automation: its design, its safety invariants, an evaluation of the implementation, and what an
AI agent would spend to do the same job itself or by running ferryGL. Version 1.0 describes ferryGL 0.2.1.

To cite it, use GitHub's **Cite this repository** button, which reads
[`CITATION.cff`](../../CITATION.cff), or:

> Oussama Batouche. *ferryGL: Token-Free Automation for Syncing Cross-Organization GitHub Work
> into Linear.* White paper, version 1.0, October 2026.
> https://doi.org/10.5281/zenodo.23475998

## What's here

| Path | Contents |
|---|---|
| `ferrygl-white-paper.tex` | The paper |
| `references.bib` | The bibliography. Each entry was checked against its DOI record, publisher, arXiv, IETF or documentation page |
| `figures/` | The figures, in TikZ and pgfplots. `style.tex` holds their colors, which are the website's |
| `data/` | The data behind the tables and figures, written by the scripts in `evaluation/` |
| `evaluation/` | The evaluation: `simulate.py` (randomized simulation), `costs.py` (GitHub request costs), `bench.py` (planner timings), `count_tokens.py` (characters per token with public tokenizers), `agent_cost.py` (what an AI agent would spend to do the same job) and `compare_models.py` (the same across 24 models from 10 providers, with prices and speeds in `models.py`), with in-memory GitHub and Linear stand-ins in `world.py` |

## Rebuilding

Build the PDF with a TeX distribution that has `latexmk`, pdfLaTeX and BibTeX:

```console
$ make -C docs/paper
```

Regenerate the data from the repository root, then rebuild:

```console
$ uv run python docs/paper/evaluation/simulate.py
$ uv run python docs/paper/evaluation/costs.py
$ uv run python docs/paper/evaluation/bench.py
$ uv run --with tiktoken --with tokenizers --with huggingface_hub \
    python docs/paper/evaluation/count_tokens.py
$ uv run python docs/paper/evaluation/agent_cost.py
$ uv run python docs/paper/evaluation/compare_models.py
```

`make -C docs/paper data` runs them all, in this order. The simulation is deterministic for a given
`--seed` and takes about ten seconds. It exits with status 1 while any property fails; in
this version, the order-independence check does (see section 6.1 of the paper). Planner
timings depend on the machine. `count_tokens.py` downloads public tokenizers from
Hugging Face. `agent_cost.py` and `compare_models.py` call no model: they measure what an agent
would read and send, and price it under the assumptions stated in sections 6.4 and 6.5 of the
paper. Prices, speeds and their sources, read on 2026-10-19, are in `evaluation/models.py`;
edit them and rerun to update the comparison.

All data in the evaluation is synthetic, like the rest of ferryGL's tests.
