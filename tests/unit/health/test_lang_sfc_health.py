"""Health metrics for Vue, Svelte and Astro single-file components.

The script block reaches the TypeScript walker with the markup blanked out at
the same offsets, so line numbers are the component file's own. A callback
passed at the top of the script is its own unit, named after the call. Each
number below was counted by hand from the source.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# vitepress theme-default VPNavBarSearch.vue (the ``i`` shortcut) and
# VPSocialLink.vue (icon URL shortened).
_VUE = r"""<script setup lang="ts">
import { onKeyStroke } from '@vueuse/core'
import { nextTick, onMounted, ref } from 'vue'

const el = ref<HTMLAnchorElement>()

onKeyStroke('i', (event) => {
  if (
    askAiSidePanelConfig.value &&
    askAiShortcutEnabled.value &&
    (event.ctrlKey || event.metaKey)
  ) {
    event.preventDefault()
    loadAndOpen('askAi')
  }
})

onMounted(async () => {
  await nextTick()
  const span = el.value?.children[0]
  if (
    span instanceof HTMLElement &&
    span.className.startsWith('vpi-social-') &&
    (getComputedStyle(span).maskImage ||
      getComputedStyle(span).webkitMaskImage) === 'none'
  ) {
    span.style.setProperty('--icon', `url('/icons/${props.icon}.svg')`)
  }
})
</script>

<template>
  <a ref="el" class="VPSocialLink no-icon" :href="link">
    <span :class="`vpi-social-${icon}`" />
  </a>
</template>
"""

# svelte.dev site-kit components/Text.svelte onclickcapture (the copy arm and
# two attribute writes dropped) and repl Output/Output.svelte highlight (body
# shortened to its two calls).
_SVELTE = r"""<script lang="ts">
    function onclickcapture(e: Event) {
        const path = e.composedPath();

        let i = path.length;
        while (i--) {
            const target = path[i];

            if (target instanceof HTMLButtonElement) {
                const parent = path.find((node) =>
                    (node as HTMLElement).classList.contains('code-block')
                ) as HTMLElement;

                // navigate tabs
                if (target.role === 'tab') {
                    if (target.getAttribute('aria-selected') === 'true') {
                        return;
                    }

                    // clear existing selection
                    parent.querySelector('[aria-selected="true"]')!.setAttribute('tabindex', '-1');
                    target.setAttribute('tabindex', '0');
                    target.setAttribute('aria-selected', 'true');

                    return;
                }
            }
        }
    }

    const highlight = (
        line: number,
        a: SourceMapSegment,
        b: SourceMapSegment,
        scroll_input: boolean,
        scroll_output: boolean
    ) => {
        workspace.highlight_range(original_range(a, b), scroll_input);
        output.highlight_range(generated_range(line, a, b), scroll_output);
    };
</script>

<div {onclickcapture}>
    <slot />
</div>
"""

# svelte.dev tutorial reactive-classes getters-and-setters App.svelte.
_SVELTE_CLASS = r"""<script>
    const MAX_SIZE = 200;

    class Box {
        #width = $state(0);
        #height = $state(0);
        area = $derived(this.#width * this.#height);

        constructor(width, height) {
            this.#width = width;
            this.#height = height;
        }

        get width() {
            return this.#width;
        }

        get height() {
            return this.#height;
        }

        set width(value) {
            this.#width = Math.max(0, Math.min(MAX_SIZE, value));
        }

        set height(value) {
            this.#height = Math.max(0, Math.min(MAX_SIZE, value));
        }

        embiggen(amount) {
            this.width += amount;
            this.height += amount;
        }
    }

    const box = new Box(100, 100);
</script>

<button onclick={() => box.embiggen(10)}>embiggen</button>
"""

# Written for this test: no Astro project is in the measured corpora.
_ASTRO = r"""---
import Layout from '../layouts/Layout.astro';

const { posts, tag } = Astro.props;

function visiblePosts(posts, tag, now) {
  const out = [];
  for (const post of posts) {
    if (post.data.draft) continue;
    if (tag && !post.data.tags.includes(tag)) continue;
    if (post.data.date > now || (post.data.expires && post.data.expires < now) || post.data.hidden) {
      continue;
    }
    out.push(post);
  }
  return out;
}

const shown = visiblePosts(posts, tag, new Date());
---

<Layout title={tag ?? 'Blog'}>
  <ul>
    {shown.map((post) => <li><a href={`/blog/${post.slug}`}>{post.data.title}</a></li>)}
  </ul>
</Layout>
"""


def _walk(source: str, path: str, language: str):
    fcx = walk_file(path, language, source.encode())
    if not fcx.functions:
        pytest.skip(f"tree-sitter grammar for {language} unavailable")
    return fcx


_CASES = {
    "vue": (_VUE, "components/Sample.vue"),
    "svelte": (_SVELTE, "lib/Sample.svelte"),
    "astro": (_ASTRO, "pages/sample.astro"),
}


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        pytest.param(
            "vue",
            {
                # if + ``&& && ||``; ``?.`` is not a decision.
                "onKeyStroke callback": (5, 1, 10, 1),
                "onMounted callback": (5, 1, 12, 0),
            },
            id="vue",
        ),
        pytest.param(
            "svelte",
            {
                # while > if > if > if; the find() callback adds nothing.
                "onclickcapture": (5, 4, 21, 1),
                "highlight": (1, 0, 4, 5),
            },
            id="svelte",
        ),
        pytest.param(
            "astro",
            # for, if, if + ``&&``, if + ``|| && ||``.
            {"visiblePosts": (9, 2, 12, 3)},
            id="astro",
        ),
    ],
)
def test_script_functions(language: str, expected: dict[str, tuple[int, int, int, int]]) -> None:
    source, path = _CASES[language]
    metrics = {
        f.name: (f.ccn, f.max_nesting, f.nloc, f.param_count)
        for f in _walk(source, path, language).functions
    }
    assert metrics == expected


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        pytest.param(
            "vue",
            [("onKeyStroke callback", 8, 3), ("onMounted callback", 21, 3)],
            id="vue",
        ),
        pytest.param("astro", [("visiblePosts", 11, 3)], id="astro"),
    ],
)
def test_compound_conditions(language: str, expected: list[tuple[str, int, int]]) -> None:
    source, path = _CASES[language]
    conditions = [
        (f.name, c)
        for f in _walk(source, path, language).functions
        for c in f.complex_conditions
        if c.operator_count >= 3
    ]
    assert sorted((name, c.line, c.operator_count) for name, c in conditions) == expected
    # Each mixes ``&&`` and ``||``: not a flat list of alternatives.
    assert not any(c.flat_chain for _, c in conditions)


def test_svelte_class_members() -> None:
    [cls] = _walk(_SVELTE_CLASS, "lib/App.svelte", "svelte").classes
    # The constructor, two getters, two setters and embiggen; fields are not
    # methods, and the blank lines are not code.
    assert (cls.name, cls.method_count, cls.total_nloc) == ("Box", 6, 25)
