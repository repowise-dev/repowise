# What Repowise is built on

Repowise did not invent the ideas it runs on. Its call graph, its git-history
signals, its health score and its refactoring plans each come from published
software-engineering research, some of it fifty years old and some of it from last
year. This page lists that research: what each work contributed, where it lives in
the code, and how we checked it on real repositories.

It exists for the same reason [BENCHMARKS.md](BENCHMARKS.md) does. A score that says
a file is risky is only worth acting on if you can see where the method came from and
how well it held up.

**The rule for a row.** A work appears here only if its contribution can be stated in
one sentence and pointed at code that implements it. Works we measured and rejected
are listed separately, and so are well-known methods we do *not* implement, so nobody
reads this page as a longer list than it is.

Paths are relative to `packages/core/src/repowise/core/` unless they say otherwise.

**At a glance:** about 45 published works, from McCabe (1976) to 2025, across graph
theory, mining software repositories, object-oriented metrics, defect prediction,
program analysis, refactoring and information retrieval.

---

## The call graph

| Year | Work | What we take from it | Where it lives |
|---|---|---|---|
| 1972 | Tarjan, *Depth-first search and linear graph algorithms*, SIAM J. Computing | Strongly connected components: import and call cycles | `ingestion/graph/_metrics.py` |
| 1993 | Eades, Lin and Smyth, *A fast and effective heuristic for the feedback arc set problem*, Inf. Processing Letters | Which edge to cut to break a cycle | `analysis/health/refactoring/break_cycle.py` |
| 1998 | Brin and Page, *The anatomy of a large-scale hypertextual web search engine*, WWW7 | PageRank centrality: which files and symbols the rest of the code leans on | `ingestion/graph/_metrics.py` |
| 2001 | Brandes, *A faster algorithm for betweenness centrality*, J. Mathematical Sociology | Bridge files that sit between parts of the system | `ingestion/graph/_betweenness.py` |
| 2008 | Blondel et al., *Fast unfolding of communities in large networks*, J. Stat. Mech. | Louvain communities (the fallback) | `analysis/communities.py` |
| 2019 | Traag, Waltman and van Eck, *From Louvain to Leiden: guaranteeing well-connected communities*, Scientific Reports | Leiden communities: module boundaries, and how Split File partitions a file | `analysis/communities.py`, `analysis/health/refactoring/split_file.py` |

**How we checked it.** The call graph is graded against answer keys we did not write:
the Go team's own call graph, the TypeScript checker, and the SCIP indexers built on
each language's compiler. Precision and recall per language are in
[BENCHMARKS.md](BENCHMARKS.md#accuracy-by-language).

---

## Git history

| Year | Work | What we take from it | Where it lives |
|---|---|---|---|
| 2000 | Mockus and Votta, *Identifying reasons for software changes using historic databases*, ICSM | Classifying a commit as a bug fix from its message | `ingestion/git_indexer/_constants.py` |
| 2005 | Nagappan and Ball, *Use of relative code churn measures to predict system defect density*, ICSE | Churn relative to size as a defect signal | `analysis/health/biomarkers/churn_risk.py` |
| 2005 | Ostrand, Weyuker and Bell, *Predicting the location and number of faults in large software systems*, TSE | Files with past faults have more future faults | `analysis/health/biomarkers/prior_defect.py` |
| 2005 | Śliwerski, Zimmermann and Zeller, *When do changes induce fixes?*, MSR | SZZ: linking a fix back to the change that introduced the bug (calibration labels) | `analysis/change_risk/model.py` |
| 2006 | Kim et al., *Automatic identification of bug-introducing changes*, ASE | The annotation-graph refinement of SZZ (calibration labels) | `analysis/change_risk/model.py` |
| 2007 | Kim et al., *Predicting faults from cached history*, ICSE | Recently fixed code is likely to be fixed again | `analysis/health/biomarkers/prior_defect.py` |
| 2009 | Hassan, *Predicting faults using the complexity of code changes*, ICSE | Change entropy: how scattered a file's changes are | `ingestion/git_indexer/co_change.py` |
| 2009 | D'Ambros, Lanza and Robbes, *On the relationship between change coupling and software defects*, WCRE | Files that change together without an import link | `analysis/health/biomarkers/co_change_scatter.py` |
| 2011 | Bird et al., *Don't touch my code! Examining the effects of ownership on software quality*, ESEC/FSE | Ownership and minor contributors as a defect signal | `analysis/health/biomarkers/ownership_risk.py` |
| 2013 | Kamei et al., *A large-scale empirical study of just-in-time quality assurance*, TSE | The change-level features behind change risk: lines added and deleted, files, directories, subsystems, entropy, author experience | `analysis/change_risk/features.py` |

**How we checked it.** Change risk is a logistic model over the Kamei features,
evaluated leave-one-repository-out on 4,102 commits from 7 repositories: AUC 0.772,
against 0.766 for churn alone. That margin is small and the docs say so; the model's
value is in the reasons it gives, not in beating churn.
[Method](architecture/change-risk.md)

---

## Code health

| Year | Work | What we take from it | Where it lives |
|---|---|---|---|
| 1976 | McCabe, *A complexity measure*, TSE | Cyclomatic complexity | `analysis/health/complexity/cyclomatic.py` |
| 1987 | Karp and Rabin, *Efficient randomized pattern-matching algorithms*, IBM J. R&D | Rolling-hash clone detection: duplicated blocks and Extract Helper | `analysis/health/duplication/rabin_karp.py` |
| 1994 | Chidamber and Kemerer, *A metrics suite for object oriented design*, TSE | Weighted methods per class as god-class evidence | `analysis/health/refactoring/extract_class.py` |
| 1995 | Hitz and Montazeri, *Measuring coupling and cohesion in object-oriented systems* | LCOM4: a class that is really several classes | `analysis/health/complexity/class_analysis.py` |
| 1995 | Bieman and Kang, *Cohesion and reuse in an object-oriented system*, SSR | Tight class cohesion, as Extract Class evidence | `analysis/health/complexity/class_analysis.py` |
| 2001 | El Emam et al., *The confounding effect of class size on the validity of object-oriented metrics*, TSE | Size is a confound: weights are fitted with file size as a control | `analysis/health/scoring.py` |
| 2006 | Lanza and Marinescu, *Object-Oriented Metrics in Practice*, Springer | God Class and Brain Method detection strategies | `analysis/health/biomarkers/god_class.py`, `brain_method.py` |
| 2007 | Meszaros, *xUnit Test Patterns*, Addison-Wesley | Test smells: tests that assert nothing | `analysis/health/biomarkers/assertion_free_test.py` |
| 2018 | Campbell, *Cognitive Complexity*, SonarSource | Nesting-weighted complexity per function | `analysis/health/complexity/cyclomatic.py` |
| 2025 | Zhu et al., FSE ([doi:10.1145/3715741](https://doi.org/10.1145/3715741)) | Recognising mocks, so a test that only checks its own mocks is flagged | `analysis/health/mocks/lexicon.py` |

**How it is evaluated.** The evaluation design follows the defect-prediction
literature, not our convenience:

| Year | Work | What we take from it |
|---|---|---|
| 1988 | DeLong, DeLong and Clarke-Pearson, *Comparing the areas under two or more correlated ROC curves*, Biometrics | Paired AUC comparison against another tool |
| 2009 | Mende and Koschke, *Revisiting the evaluation of defect prediction models*, PROMISE | Popt: ranking quality per line reviewed, not per file |
| 2010 | Mende and Koschke, *Effort-aware defect prediction models*, CSMR | Recall at a fixed 20% review budget |
| 2010 | Jureczko and Madeyski, *Towards identifying software project clusters with regard to defect prediction*, PROMISE | jEdit releases as an external, held-out dataset |

**How we checked it.** Every file in 21 open-source repositories across 9 languages
(2,826 files) was scored at a fixed commit and checked against the next six months of
bug fixes: ROC AUC 0.737 [0.683, 0.787], and 0.76 to 0.78 on the held-out jEdit
releases. Against CodeScene on the 2,770 files both tools scored, ranking by Repowise
surfaces 2.3x the defective files inside a 20% review budget (p = 0.003).
[BENCHMARKS.md](BENCHMARKS.md#5-code-health-predicts-defects)

---

## Program analysis and refactoring

| Year | Work | What we take from it | Where it lives |
|---|---|---|---|
| 1973 | Kildall, *A unified approach to global program optimization*, POPL | The worklist fixpoint for dataflow over a control-flow graph | `analysis/health/dataflow/reaching.py` |
| 1986 | Aho, Sethi and Ullman, *Compilers: Principles, Techniques, and Tools* | Reaching definitions with GEN and KILL sets: which values flow into and out of a span | `analysis/health/dataflow/reaching.py` |
| 1992 | Opdyke, *Refactoring Object-Oriented Frameworks*, PhD thesis, UIUC | Preconditions that make a refactoring behaviour-preserving | `analysis/health/refactoring/preconditions.py` |
| 1999 | Fowler, *Refactoring: Improving the Design of Existing Code* | Extract Method, Extract Class, Move Method and the smells that call for them | `analysis/health/refactoring/` |

Extract Method plans are built on that dataflow pass: it finds the exact span to lift
and infers the parameters and return values a behaviour-preserving signature needs.

---

## Architecture across repositories

| Year | Work | What we take from it | Where it lives |
|---|---|---|---|
| 2006 | MacCormack, Rusnak and Baldwin, *Exploring the structure of complex software designs*, Management Science | Propagation cost: how far a change can travel | `workspace/architecture_metrics.py` |
| 2013 | Sturtevant, *System design and the cost of architectural complexity*, PhD thesis, MIT | Core-periphery roles for services | `workspace/architecture_metrics.py` |
| 2014 | Baldwin, MacCormack and Rusnak, *Hidden structure: using network methods to map system architecture*, Research Policy | The cyclic core and the dependency-structure view | `workspace/architecture_metrics.py` |

---

## Search and retrieval

| Year | Work | What we take from it | Where it lives |
|---|---|---|---|
| 2009 | Robertson and Zaragoza, *The probabilistic relevance framework: BM25 and beyond*, Foundations and Trends in IR | BM25 ranking over full-text search, with per-column weights | `persistence/search.py` |
| 2009 | Cormack, Clarke and Büttcher, *Reciprocal rank fusion outperforms Condorcet and individual rank learning methods*, SIGIR | Merging full-text and semantic results | `packages/server/src/repowise/server/mcp_server/tool_search.py` |

**How we checked it.** On 42 sealed retrieval tasks, held out from every round of
tuning and graded without a model, `get_answer` found 0.876 of the files a fix needed,
against 0.610 for the next tool (sign test p = 0.00004).
[BENCHMARKS.md](BENCHMARKS.md#1-finding-the-right-files)

---

## Decisions

| Year | Work | What we take from it | Where it lives |
|---|---|---|---|
| 2011 | Nygard, *Documenting Architecture Decisions* | The architecture decision record format Repowise reads and writes | `analysis/decisions/adr.py` |
| 2018 | Kopp et al., MADR (Markdown Architectural Decision Records) | The MADR variant of the same format | `analysis/decisions/adr.py` |

---

## Where we changed the original

Research methods are written for a study. A tool has to run on any repository, in any
language, every day. Where we departed from the source, we say so:

- **Weights are fitted.** The original detectors use fixed thresholds. Repowise fits
  the weight of each detector on a defect corpus, with file size as a control, and
  ships only the fitted constants. A detector that cannot show defect lift beyond size
  does not move the defect score; 25 of the 53 detectors do.
- **God Class uses simpler inputs.** Lanza and Marinescu's strategy reads attribute
  access across classes (ATFD). Repowise uses size, method count and the presence of a
  brain method, and gates Brain Method on how central the function is in the graph.
- **Change entropy is decayed and capped.** Older changes weigh less, and very large
  commits are left out so one mass rename does not dominate a file's history.
- **Missing evidence reads as no signal.** A class with no detected member references
  reports LCOM4 of 1, not a high value; an unsupported language produces no health
  findings, not guessed ones.
- **Weak signals stay weak.** Developer congestion and knowledge loss calibrated
  weakly, so their weights are floored at 0.5 and 0.4 instead of being removed or
  inflated.

## What we measured and did not ship

| Idea | Source | What happened |
|---|---|---|
| Refactoring-aware SZZ for bug-introducing commits | Śliwerski et al. 2005 and its successors | 74.5% precision against an 80% gate. Not shipped; SZZ only labels offline calibration data |
| PageRank centrality as a defect predictor | Brin and Page 1998 | Rejected as a predictor on the calibration corpus. Kept for ranking pages and plans, not for the defect score |
| Code naturalness as a defect predictor | Ray et al., *On the naturalness of buggy code*, ICSE 2016 | Tested on the calibration corpus, rejected |
| Change bursts as a defect predictor | Nagappan et al., *Change bursts as defect predictors*, ISSRE 2010 | Tested on the calibration corpus, rejected |

The calibration also reports one result against us: file size alone matches the health
score on ROC AUC (0.742, p = 0.92). The score's measured advantage is in effort-aware
ranking, finding more defects per line reviewed, and that is the claim we make.

## What we do not implement

So nobody infers it from the list above: Repowise does **not** compute Halstead
metrics, the Maintainability Index, the full Chidamber-Kemerer suite (CBO, RFC, DIT),
program slicing in Weiser's sense, or rapid type analysis. Its call graph resolves
receivers by declared type instead.

## What is actually new

The pieces are everybody's. What Repowise adds is putting them in one index and
making them answer questions together: the graph decides which code a git signal
belongs to, health scores the code the graph parsed, tests reach code through graph
edges, and decisions attach to the files they govern. Every answer carries how it was
derived and how far to trust it, it is served to people and to coding agents the same
way, and the whole thing is measured in public against answer keys we did not write.

---

Spotted a wrong attribution, or a technique we use that is missing here? Open an
issue. This page is kept to the same standard as the code it describes.
