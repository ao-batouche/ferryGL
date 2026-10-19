# Build the white paper, or regenerate the data behind its tables and figures.
#
#   make          the PDF, with latexmk and pdfLaTeX
#   make data     rerun the evaluation (from the repository root, with uv)
#   make clean    remove LaTeX's intermediate files

PAPER := ferrygl-white-paper
ROOT := ../..

.PHONY: pdf data clean

pdf: $(PAPER).pdf

$(PAPER).pdf: $(PAPER).tex references.bib $(wildcard figures/*.tex) $(wildcard data/*)
	latexmk -pdf -interaction=nonstopmode -halt-on-error $(PAPER).tex

# simulate.py exits with status 1 while a property fails, so its status is ignored here.
data:
	-cd $(ROOT) && uv run python docs/paper/evaluation/simulate.py
	cd $(ROOT) && uv run python docs/paper/evaluation/costs.py
	cd $(ROOT) && uv run python docs/paper/evaluation/bench.py
	cd $(ROOT) && uv run --with tiktoken --with tokenizers --with huggingface_hub \
		python docs/paper/evaluation/count_tokens.py
	cd $(ROOT) && uv run python docs/paper/evaluation/agent_cost.py
	cd $(ROOT) && uv run python docs/paper/evaluation/compare_models.py

clean:
	latexmk -c $(PAPER).tex
