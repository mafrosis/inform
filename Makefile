.PHONY: lint
lint:
	hatch fmt --preview

.PHONY: typecheck
typecheck:
	hatch run test:mypy

.PHONY: test
test:
	hatch run test:test

.PHONY: dist
dist:
	hatch build
