# repowise Frontend — Stack & Design Options

## Decision: Which approach is best for an enterprise SaaS?

I've analyzed the backend (30+ endpoints, SSE, 13 MCP tools, 605 tests), the PLAN.md spec, and run the UI/UX Pro Max design intelligence against this product type. Here are 3 viable options ranked by enterprise readiness.

---

## Option A: Next.js 15 + shadcn/ui + D3 (RECOMMENDED)

**Design Language:** Linear meets Stripe Docs

| Dimension | Choice |
|-----------|--------|
| Framework | Next.js 15 (App Router, RSC, streaming) |
| Styling | Tailwind CSS v4 + CSS variables |
| Components | shadcn/ui (Radix primitives, copy-paste ownership) |
| MDX | next-mdx-remote (server-side compilation) |
| Code Blocks | Shiki (server-rendered, VSCode-quality) |
| Diagrams | Mermaid.js (client, lazy) |
| Graphs | D3.js (d3-force for dependency graph, d3-hierarchy for treemap) |
| Search | cmdk command palette + nuqs URL state |
| Data Fetching | React Server Components + SWR |
| Animation | Framer Motion |
| Charts | Recharts (simple) + D3 (complex) |

**Why this wins for enterprise:**

1. **shadcn/ui** = you own the code. No library version lock-in. Enterprise buyers hate "we can't customize because the library doesn't support it." shadcn gives you Radix accessibility for free with full control.

2. **Next.js RSC** = wiki pages render on the server. First paint is fast. SEO works (important if enterprise customers want public-facing docs). Streaming SSR means the page shell appears instantly while data loads.

3. **D3.js** = full control over the dependency graph visualization. No opinionated graph library will match the exact requirements (node sizing by PageRank, edge coloring by type, dead code overlay, co-change dashed lines). D3 is the right tool.

4. **Tailwind CSS v4** = design tokens as CSS variables means enterprise white-labeling is trivial. Swap `--accent-primary` and the entire app rebrands.

5. **Shiki server-rendering** = zero client JavaScript for code blocks. Wiki pages with 20 code blocks don't ship 20 instances of a syntax highlighter to the browser.

**Tradeoffs:**
- More initial setup than a component library like Chakra or MUI
- D3 has a steep learning curve for the graph/treemap components
- MDX rendering pipeline requires careful architecture

**Estimated effort:** 8-12 days of focused work for a senior frontend engineer

---

## Option B: Next.js 15 + MUI (Material UI) + React Flow

**Design Language:** Material Design 3, dark theme

| Dimension | Choice |
|-----------|--------|
| Framework | Next.js 15 |
| Styling | MUI's sx prop + Emotion CSS-in-JS |
| Components | MUI v6 (complete component library) |
| Graphs | React Flow (node-based editor framework) |
| Charts | MUI X Charts or Nivo |
| MDX | next-mdx-remote + MUI Typography |

**Pros:**
- Fastest to build — MUI has DataGrid, Tabs, Dialogs, everything out of the box
- React Flow handles the dependency graph with less custom code
- MUI X DataGrid handles the symbol index table with sorting, filtering, pagination built-in
- Material Design is familiar to enterprise users

**Cons:**
- **CSS-in-JS runtime cost** — Emotion adds ~15KB and has runtime overhead. Not ideal for content-heavy wiki pages
- **Vendor lock** — MUI X Pro/Premium features (DataGrid advanced sorting, tree view) require a $600-$15K/year license for commercial use
- **Generic look** — Material Design is recognizable but doesn't signal "premium developer tool." Linear and Vercel deliberately avoid Material Design for this reason
- **React Flow limitations** — great for editable node graphs (like n8n), but repowise's graph is read-only visualization. D3-force gives more control for that use case
- **White-labeling is harder** — MUI's theme system works but is less flexible than raw CSS variables for deep customization

**Estimated effort:** 5-8 days (faster initial, but harder to differentiate visually)

---

## Option C: Next.js 15 + Tailwind + Tremor + Cytoscape.js

**Design Language:** Dashboard-first (like Vercel/Datadog)

| Dimension | Choice |
|-----------|--------|
| Framework | Next.js 15 |
| Styling | Tailwind CSS v4 |
| Components | Tremor (dashboard-focused component library) |
| Graphs | Cytoscape.js (graph theory library) |
| Charts | Tremor built-in charts (based on Recharts) |
| Data Tables | TanStack Table |

**Pros:**
- Tremor is built specifically for dashboards and data-heavy apps
- Beautiful stat cards, charts, and data displays out of the box
- Cytoscape.js is purpose-built for graph theory visualization (PageRank, shortest path, clustering — it speaks repowise's language)
- TanStack Table is the most flexible table solution for the symbol index

**Cons:**
- **Tremor is less mature** than shadcn/ui and has a smaller ecosystem
- **Wiki page rendering** is Tremor's weak spot — it's a dashboard library, not a content/docs library. MDX rendering would need custom work on top of Tremor's design language
- **Two visual languages** — dashboard pages would look "Tremor" while wiki pages would look custom. Consistency is hard
- **Cytoscape.js** is great for analysis but its default rendering is less visually polished than custom D3. Styling nodes/edges to match the design system requires significant work

**Estimated effort:** 7-10 days (good for dashboard pages, extra work for wiki rendering)

---

## My Recommendation: Option A

For an enterprise SaaS product where the wiki page viewer is the core experience, **Option A (Next.js + shadcn/ui + D3)** is the right choice because:

1. **The wiki page IS the product.** 70% of user time will be spent reading wiki pages. The rendering quality of MDX + Shiki + Mermaid + symbol hover cards must be excellent. shadcn/ui + custom components gives full control here.

2. **Enterprise white-labeling.** CSS variable-based theming means any company can rebrand repowise to match their design system. This is a direct sales enabler.

3. **No license risk.** shadcn/ui is MIT with zero licensing concerns. MUI X commercial licenses add cost and complexity for enterprise customers self-hosting.

4. **Long-term flexibility.** Copy-paste components can be modified without waiting for upstream library releases. Enterprise customers will request customizations — you need the freedom to say yes.

5. **Design differentiation.** Linear, Vercel, and Raycast all use custom Radix-based component systems (not MUI/Chakra). This is the pattern that signals "premium developer tool" to enterprise buyers.

---

## Design Decision: Dark-Only in V1

Ship dark mode only. Reasons:

1. **Developer tools are dark.** VS Code, GitHub (Dimmed), Linear, Vercel dashboard — the audience expects dark.
2. **Half the design work.** Every component tested in one theme, not two.
3. **Faster to market.** Add light mode in v1.1 when enterprise customers request it (they will, for projector/meeting use cases).
4. **CSS variables make it easy to add later.** The design token system in FRONTEND_BUILD_PROMPT.md is structured so adding a light theme is a matter of swapping variable values.

---

## What's in FRONTEND_BUILD_PROMPT.md

The comprehensive build prompt includes:

- Full design system (colors, typography, spacing, animation, z-index)
- All 11 pages with detailed specs, data sources, and ASCII wireframes
- Component architecture with implementation notes
- MDX rendering pipeline architecture
- API integration layer with typed client
- SSE subscription patterns
- Complete file structure
- Build order (7 steps, dependency-ordered)
- Performance targets and accessibility requirements
- Enterprise considerations (multi-tenancy, SSO, RBAC, white-labeling, embedding)

**This document is ready to hand to a frontend engineer (or to Claude in the next iteration) to build Phase 8.**
