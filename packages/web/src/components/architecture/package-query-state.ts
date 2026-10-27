import {
  DEFAULT_EXTERNAL_DEPENDENCY_STATE,
  type ExternalDependencyRole,
  type ExternalDependencySort,
  type ExternalDependencyTableState,
  type ExternalDependencyUsage,
} from "@repowise-dev/ui/dependencies";

export interface PackageQueryValues {
  q: string;
  ecosystem: string;
  role: ExternalDependencyRole;
  usage: ExternalDependencyUsage;
  category: string;
  sort: ExternalDependencySort;
  order: "asc" | "desc";
  page: number;
}

export function tableStateFromQuery(values: PackageQueryValues): ExternalDependencyTableState {
  return {
    query: values.q,
    ecosystem: values.ecosystem,
    role: values.role,
    usage: values.usage,
    category: values.category,
    sort: values.sort,
    order: values.order,
    page: Math.max(1, values.page),
  };
}

export function queryFromTableState(state: ExternalDependencyTableState): PackageQueryValues {
  return {
    q: state.query,
    ecosystem: state.ecosystem,
    role: state.role,
    usage: state.usage,
    category: state.category,
    sort: state.sort,
    order: state.order,
    page: Math.max(1, state.page),
  };
}

export const DEFAULT_PACKAGE_QUERY_VALUES: PackageQueryValues = queryFromTableState(
  DEFAULT_EXTERNAL_DEPENDENCY_STATE,
);
