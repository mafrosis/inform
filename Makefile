.PHONY: lint
lint:
	hatch run lint:lint

.PHONY: typecheck
typecheck:
	hatch run test:mypy

.PHONY: test
test:
	hatch run test:test

.PHONY: dist
dist:
	hatch build
