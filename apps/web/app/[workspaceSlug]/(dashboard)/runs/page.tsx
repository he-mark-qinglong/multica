"use client";

import { RunsPage } from "@multica/views/runs/components";
import { ErrorBoundary } from "@multica/ui/components/common/error-boundary";

export default function Page() {
  return (
    <ErrorBoundary>
      <RunsPage />
    </ErrorBoundary>
  );
}
