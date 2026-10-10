/**
 * A real Fix-first queue: three items read with `load_fix_first` from an index
 * of this repository (commit f72d50dc), one per kind. Totals are that run's, with
 * `shown` matching the three items kept here.
 */

import type { FixFirstQueue } from "@repowise-dev/types/fix-first";

export const FIX_FIRST_QUEUE: FixFirstQueue = {
  "items": [
    {
      "id": "fix1_2b0e5c0acbb469f46aa9",
      "rank": 3,
      "tier": "now",
      "kind": "refactor",
      "improves": "defect",
      "title": "Extract lines 60-122 of quick_repo_scan into compute_info",
      "target": {
        "file_path": "packages/cli/src/repowise/cli/ui/repo_scanner.py",
        "symbol": "quick_repo_scan",
        "line_start": 60,
        "line_end": 133
      },
      "why": "quick_repo_scan: CCN 15, 44 lines, nests 3 deep; 4 files import it, changed 5 times in 90 days.",
      "facts": [
        {
          "label": "health recoverable",
          "value": "+1.7",
          "basis": "inferred"
        },
        {
          "label": "steps",
          "value": "1 (1 mechanical)",
          "basis": "measured"
        },
        {
          "label": "files that import it",
          "value": "4",
          "basis": "measured"
        },
        {
          "label": "changes in 90 days",
          "value": "5",
          "basis": "measured"
        },
        {
          "label": "file size",
          "value": "149 lines",
          "basis": "measured"
        }
      ],
      "action": {
        "summary": "1 step, 1 mechanical",
        "steps": [
          {
            "order": 1,
            "text": "Extract lines 60-122 of quick_repo_scan into compute_info(repo_path) -> info",
            "file_path": "packages/cli/src/repowise/cli/ui/repo_scanner.py",
            "line": 60,
            "mechanical": true
          }
        ],
        "steps_total": 1,
        "mechanical": true
      },
      "gain": {
        "kind": "health_points",
        "value": 1.671,
        "text": "+1.7 health on this file"
      },
      "effort": {
        "bucket": "S",
        "basis": "sized by the stored plan"
      },
      "risk": {
        "level": "low",
        "dependents": 4,
        "files_touched": 1,
        "text": "Touches 1 file; 4 files import it."
      },
      "confidence": {
        "level": "high",
        "reason": "1 of 1 step proven mechanical by the plan"
      },
      "verify": {
        "tests": [
          {
            "path": "tests/unit/cli/test_repo_scanner.py",
            "reason": "reaches the changed code through the call-graph"
          },
          {
            "path": "tests/unit/cli/test_edenai_reachability.py",
            "reason": "reaches the changed code through the call-graph"
          },
          {
            "path": "tests/unit/cli/test_file_page_volume_prompt.py",
            "reason": "reaches the changed code through the call-graph"
          },
          {
            "path": "tests/unit/cli/test_ui_package.py",
            "reason": "reaches the changed code through the call-graph"
          }
        ],
        "tests_total": 4,
        "command": "pytest tests/unit/cli/test_edenai_reachability.py tests/unit/cli/test_file_page_volume_prompt.py tests/unit/cli/test_repo_scanner.py tests/unit/cli/test_ui_package.py",
        "basis": "inferred"
      },
      "context": [
        {
          "label": "changes in 90 days",
          "value": "5"
        },
        {
          "label": "function hotspot",
          "value": "quick_repo_scan has been modified across 3 commits (repo p80=2) and carries CCN=15 / nesting=3"
        },
        {
          "label": "change entropy",
          "value": "changes are scattered across noisy commits (top 13% among files eligible for change-entropy ranking); a strong history-based fault predictor"
        }
      ],
      "source": {
        "opportunity_id": "refop3_8ba13d978db910d160b1",
        "plan_ids": [
          "refac3_28f8e2028062393ff0b2"
        ],
        "finding_ids": [
          "finding_daa787aeb0baf5bc61ad"
        ]
      },
      "next_call": {
        "purpose": "The full plan: ordered steps, validation and evidence",
        "mcp": "get_health(opportunity_id=\"refop3_8ba13d978db910d160b1\")",
        "cli": null,
        "tool": "get_health",
        "arguments": {
          "opportunity_id": "refop3_8ba13d978db910d160b1"
        }
      },
      "why_ranked": [
        {
          "factor": "value",
          "value": "3"
        },
        {
          "factor": "health gain",
          "value": "1.67"
        },
        {
          "factor": "problem size",
          "value": "0"
        },
        {
          "factor": "hot file",
          "value": "yes"
        },
        {
          "factor": "tier",
          "value": "worth doing, and the plan is safe to start"
        }
      ]
    },
    {
      "id": "fix1_8ac4c08f700f837e9087",
      "rank": 0,
      "tier": "next",
      "kind": "finding",
      "improves": "defect",
      "title": "Break up GraphFlow (CCN 260, 1,094 lines)",
      "target": {
        "file_path": "packages/ui/src/graph/graph-flow.tsx",
        "symbol": "GraphFlow",
        "line_start": 198,
        "line_end": 1654
      },
      "why": "GraphFlow: CCN 260, 1,094 lines, nests 7 deep; 4 files import it, changed 13 times in 90 days.",
      "facts": [
        {
          "label": "finding",
          "value": "nested_complexity",
          "basis": "measured"
        },
        {
          "label": "severity",
          "value": "critical",
          "basis": "measured"
        },
        {
          "label": "files that import it",
          "value": "4",
          "basis": "measured"
        },
        {
          "label": "changes in 90 days",
          "value": "13",
          "basis": "measured"
        }
      ],
      "action": {
        "summary": "Flatten the control flow.",
        "steps": [
          {
            "order": 1,
            "text": "Flatten the control flow.",
            "file_path": "packages/ui/src/graph/graph-flow.tsx",
            "line": 198,
            "mechanical": false
          }
        ],
        "steps_total": 1,
        "mechanical": false
      },
      "gain": {
        "kind": "health_points",
        "value": 1.821,
        "text": "up to +1.8 health on this file"
      },
      "effort": {
        "bucket": "M",
        "basis": "not sized; no stored plan covers this finding"
      },
      "risk": {
        "level": "low",
        "dependents": 4,
        "files_touched": 1,
        "text": "Touches 1 file; 4 files import it."
      },
      "confidence": {
        "level": "medium",
        "reason": "Measured from the code; no stored plan has checked a fix."
      },
      "verify": {
        "tests": [],
        "tests_total": 0,
        "command": null,
        "basis": "unknown"
      },
      "context": [
        {
          "label": "changes in 90 days",
          "value": "13"
        },
        {
          "label": "change entropy",
          "value": "changes are scattered across noisy commits (top 4.3% among files eligible for change-entropy ranking); a strong history-based fault predictor"
        },
        {
          "label": "function hotspot",
          "value": "GraphFlow has been modified across 19 commits (repo p80=2) and carries CCN=260 / nesting=7"
        }
      ],
      "source": {
        "opportunity_id": null,
        "plan_ids": [],
        "finding_ids": [
          "finding_23450305a33258e5bd64"
        ]
      },
      "next_call": {
        "purpose": "Every open finding in the file, with its line and reason",
        "mcp": "get_health(targets=[\"packages/ui/src/graph/graph-flow.tsx\"], include=[\"biomarkers\"])",
        "cli": "repowise health --file packages/ui/src/graph/graph-flow.tsx",
        "tool": "get_health",
        "arguments": {
          "targets": [
            "packages/ui/src/graph/graph-flow.tsx"
          ],
          "include": [
            "biomarkers"
          ]
        }
      },
      "why_ranked": [
        {
          "factor": "value",
          "value": "4"
        },
        {
          "factor": "health gain",
          "value": "1.82"
        },
        {
          "factor": "problem size",
          "value": "4"
        },
        {
          "factor": "hot file",
          "value": "yes"
        },
        {
          "factor": "tier",
          "value": "worth doing; the fix needs judgment"
        }
      ]
    },
    {
      "id": "fix1_26c2da3b4c98f8fdef56",
      "rank": 14,
      "tier": "next",
      "kind": "perf_fix",
      "improves": "performance",
      "title": "Batch the database calls loops make through ensure_repo_registration",
      "target": {
        "file_path": "packages/server/src/repowise/server/repo_db.py",
        "symbol": "ensure_repo_registration",
        "line_start": null,
        "line_end": null
      },
      "why": "ensure_repo_registration makes a database call once per loop iteration; 2 call sites in 2 files reach it, an entry point reaches it, the loop grows with the data.",
      "facts": [
        {
          "label": "call sites",
          "value": "2",
          "basis": "measured"
        },
        {
          "label": "files",
          "value": "2",
          "basis": "measured"
        },
        {
          "label": "reachable from an entry point",
          "value": "Yes",
          "basis": "inferred"
        },
        {
          "label": "loop size",
          "value": "grows with data",
          "basis": "inferred"
        }
      ],
      "action": {
        "summary": "Batch the calls, or fetch the data once before the loop",
        "steps": [
          {
            "order": 1,
            "text": "Add a form of ensure_repo_registration that takes every key at once",
            "file_path": "packages/server/src/repowise/server/repo_db.py",
            "line": null,
            "mechanical": false
          },
          {
            "order": 2,
            "text": "Collect the keys before the loop and call the batched form once (rediscover_repo_dbs)",
            "file_path": "packages/server/src/repowise/server/repo_db.py",
            "line": 274,
            "mechanical": false
          },
          {
            "order": 3,
            "text": "Collect the keys before the loop and call the batched form once (sync_workspace)",
            "file_path": "packages/server/src/repowise/server/routers/workspace.py",
            "line": 920,
            "mechanical": false
          }
        ],
        "steps_total": 3,
        "mechanical": false
      },
      "gain": {
        "kind": "performance",
        "value": null,
        "text": "one database call per loop iteration, grows with the data"
      },
      "effort": {
        "bucket": "M",
        "basis": "sized by the stored plan"
      },
      "risk": {
        "level": "medium",
        "dependents": null,
        "files_touched": 2,
        "text": "Touches 2 files."
      },
      "confidence": {
        "level": "medium",
        "reason": "The shared I/O sink is proven; no concrete batch API or result-equivalence proof is available."
      },
      "verify": {
        "tests": [
          {
            "path": "tests/unit/server/test_app_lifespan.py",
            "reason": "reaches the changed code through the call-graph"
          },
          {
            "path": "tests/unit/server/test_app_workspace_registry.py",
            "reason": "reaches the changed code through the call-graph"
          },
          {
            "path": "tests/unit/server/test_workspace_search_postgres.py",
            "reason": "reaches the changed code through the call-graph"
          },
          {
            "path": "tests/unit/server/test_workspace_router.py",
            "reason": "reaches the changed code through the call-graph"
          }
        ],
        "tests_total": 4,
        "command": "pytest tests/unit/server/test_app_lifespan.py tests/unit/server/test_app_workspace_registry.py tests/unit/server/test_workspace_router.py tests/unit/server/test_workspace_search_postgres.py",
        "basis": "inferred"
      },
      "context": [
        {
          "label": "changes in 90 days",
          "value": "2"
        }
      ],
      "source": {
        "opportunity_id": "perf2_5ca551b7b7c3293951d0",
        "plan_ids": [],
        "finding_ids": []
      },
      "next_call": {
        "purpose": "The full opportunity: ordered steps, validation and other causes here",
        "mcp": "get_health(opportunity_id=\"perf2_5ca551b7b7c3293951d0\")",
        "cli": null,
        "tool": "get_health",
        "arguments": {
          "opportunity_id": "perf2_5ca551b7b7c3293951d0"
        }
      },
      "why_ranked": [
        {
          "factor": "value",
          "value": "3"
        },
        {
          "factor": "runs in",
          "value": "production"
        },
        {
          "factor": "entry reachable",
          "value": "entry_reachable"
        },
        {
          "factor": "loop size",
          "value": "grows_with_data"
        },
        {
          "factor": "boundary",
          "value": "db"
        },
        {
          "factor": "tier",
          "value": "worth doing; the fix needs judgment"
        }
      ]
    }
  ],
  "lead": {
    "id": "fix1_2b0e5c0acbb469f46aa9",
    "rank": 3,
    "tier": "now",
    "kind": "refactor",
    "improves": "defect",
    "title": "Extract lines 60-122 of quick_repo_scan into compute_info",
    "target": {
      "file_path": "packages/cli/src/repowise/cli/ui/repo_scanner.py",
      "symbol": "quick_repo_scan",
      "line_start": 60,
      "line_end": 133
    },
    "why": "quick_repo_scan: CCN 15, 44 lines, nests 3 deep; 4 files import it, changed 5 times in 90 days.",
    "facts": [
      {
        "label": "health recoverable",
        "value": "+1.7",
        "basis": "inferred"
      },
      {
        "label": "steps",
        "value": "1 (1 mechanical)",
        "basis": "measured"
      },
      {
        "label": "files that import it",
        "value": "4",
        "basis": "measured"
      },
      {
        "label": "changes in 90 days",
        "value": "5",
        "basis": "measured"
      },
      {
        "label": "file size",
        "value": "149 lines",
        "basis": "measured"
      }
    ],
    "action": {
      "summary": "1 step, 1 mechanical",
      "steps": [
        {
          "order": 1,
          "text": "Extract lines 60-122 of quick_repo_scan into compute_info(repo_path) -> info",
          "file_path": "packages/cli/src/repowise/cli/ui/repo_scanner.py",
          "line": 60,
          "mechanical": true
        }
      ],
      "steps_total": 1,
      "mechanical": true
    },
    "gain": {
      "kind": "health_points",
      "value": 1.671,
      "text": "+1.7 health on this file"
    },
    "effort": {
      "bucket": "S",
      "basis": "sized by the stored plan"
    },
    "risk": {
      "level": "low",
      "dependents": 4,
      "files_touched": 1,
      "text": "Touches 1 file; 4 files import it."
    },
    "confidence": {
      "level": "high",
      "reason": "1 of 1 step proven mechanical by the plan"
    },
    "verify": {
      "tests": [
        {
          "path": "tests/unit/cli/test_repo_scanner.py",
          "reason": "reaches the changed code through the call-graph"
        },
        {
          "path": "tests/unit/cli/test_edenai_reachability.py",
          "reason": "reaches the changed code through the call-graph"
        },
        {
          "path": "tests/unit/cli/test_file_page_volume_prompt.py",
          "reason": "reaches the changed code through the call-graph"
        },
        {
          "path": "tests/unit/cli/test_ui_package.py",
          "reason": "reaches the changed code through the call-graph"
        }
      ],
      "tests_total": 4,
      "command": "pytest tests/unit/cli/test_edenai_reachability.py tests/unit/cli/test_file_page_volume_prompt.py tests/unit/cli/test_repo_scanner.py tests/unit/cli/test_ui_package.py",
      "basis": "inferred"
    },
    "context": [
      {
        "label": "changes in 90 days",
        "value": "5"
      },
      {
        "label": "function hotspot",
        "value": "quick_repo_scan has been modified across 3 commits (repo p80=2) and carries CCN=15 / nesting=3"
      },
      {
        "label": "change entropy",
        "value": "changes are scattered across noisy commits (top 13% among files eligible for change-entropy ranking); a strong history-based fault predictor"
      }
    ],
    "source": {
      "opportunity_id": "refop3_8ba13d978db910d160b1",
      "plan_ids": [
        "refac3_28f8e2028062393ff0b2"
      ],
      "finding_ids": [
        "finding_daa787aeb0baf5bc61ad"
      ]
    },
    "next_call": {
      "purpose": "The full plan: ordered steps, validation and evidence",
      "mcp": "get_health(opportunity_id=\"refop3_8ba13d978db910d160b1\")",
      "cli": null,
      "tool": "get_health",
      "arguments": {
        "opportunity_id": "refop3_8ba13d978db910d160b1"
      }
    },
    "why_ranked": [
      {
        "factor": "value",
        "value": "3"
      },
      {
        "factor": "health gain",
        "value": "1.67"
      },
      {
        "factor": "problem size",
        "value": "0"
      },
      {
        "factor": "hot file",
        "value": "yes"
      },
      {
        "factor": "tier",
        "value": "worth doing, and the plan is safe to start"
      }
    ]
  },
  "totals": {
    "candidates": 1973,
    "eligible": 423,
    "shown": 3,
    "excluded": {
      "test": 612,
      "tooling": 91,
      "generated": 5,
      "expected": 158,
      "unknown": 0,
      "no_strategy": 0,
      "no_plan": 12,
      "below_min_worth": 505,
      "history_only": 167,
      "vendored": 0,
      "docs_example": 0,
      "deprecated": 0,
      "gated_off": 0,
      "unreachable": 0,
      "inherent_dispatch": 0,
      "small_function": 0,
      "no_concrete_step": 0,
      "low_value_kind": 0
    },
    "dormant": 0
  },
  "by_improves": {
    "defect": 266,
    "maintainability": 31,
    "performance": 126
  },
  "model_version": 1,
  "basis": {
    "analyzed_commit": "f72d50dc0c29a22bf30bb673321e9743ba638e4c",
    "health_analyzed_at": "2026-10-01T08:48:22.812504"
  }
};
