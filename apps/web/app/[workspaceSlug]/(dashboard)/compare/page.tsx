"use client";

import { ComparePage } from "@multica/views/compare/components";
import { ErrorBoundary } from "@multica/ui/components/common/error-boundary";

export default function Page() {
  return (
    <ErrorBoundary>
      <ComparePage />
    </ErrorBoundary>
  );
}
