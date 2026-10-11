.PHONY: build test clean

build:
	cd typescript_pkg && npm run build

test:
	cd typescript_pkg && npm run type-check

clean:
	rm -rf typescript_pkg/dist
	find . -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
