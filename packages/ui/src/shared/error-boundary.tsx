"use client";

import * as React from "react";
import { ApiError } from "./api-error";

/**
 * ErrorBoundary — catches render errors in a subtree and shows a recoverable
 * fallback instead of blanking the page. Default fallback is ApiError with a
 * retry that re-mounts the subtree.
 */
export interface ErrorBoundaryProps {
  children: React.ReactNode;
  /** Custom fallback; receives the error and a reset callback. */
  fallback?: ((error: Error, reset: () => void) => React.ReactNode) | undefined;
  /** Forwarded to the default ApiError fallback. */
  title?: string | undefined;
  onError?: ((error: Error, info: React.ErrorInfo) => void) | undefined;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  override state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: React.ErrorInfo) {
    this.props.onError?.(error, info);
  }

  reset = () => this.setState({ error: null });

  override render() {
    const { error } = this.state;
    if (error) {
      if (this.props.fallback) return this.props.fallback(error, this.reset);
      return (
        <ApiError
          title={this.props.title ?? "Something went wrong"}
          message={error.message}
          onRetry={this.reset}
        />
      );
    }
    return this.props.children;
  }
}
