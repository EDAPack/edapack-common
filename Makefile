# edapack-common — developer convenience targets.
.PHONY: test test-py test-sh lint

test: lint test-py test-sh

test-py:
	python3 -m pytest tests -q

test-sh:
	bash tests/shell/test_build_common.sh

lint:
	@for f in scripts/*.sh tests/shell/*.sh; do bash -n "$$f" && echo "ok: $$f"; done
