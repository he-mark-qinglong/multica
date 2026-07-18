"use client";

import { useState } from "react";
import { ChevronRight, ScrollText } from "lucide-react";
import { useWorkspaceId } from "@multica/core/hooks";
import { useTasks, type UseTasksParams } from "@multica/core/hooks/use-tasks";
import { useCurrentWorkspace, useWorkspacePaths } from "@multica/core/paths";
import type { Task, TaskStatus } from "@multica/core/types";
import { useTimeAgo } from "../../i18n";
import { Skeleton } from "@multica/ui/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@multica/ui/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@multica/ui/components/ui/table";
import { Button } from "@multica/ui/components/ui/button";
import { PageHeader } from "../../layout/page-header";
import { WorkspaceAvatar } from "../../workspace/workspace-avatar";
import { AppLink } from "../../navigation";
import { TranscriptButton } from "../../common/task-transcript";
import { useT } from "../../i18n";

// Rows per page — matches the issue list's page size so the two surfaces
// feel consistent.
const PAGE_SIZE = 50;

const ALL_STATUSES: TaskStatus[] = [
  "queued",
  "dispatched",
  "running",
  "completed",
  "failed",
  "cancelled",
];

// Badge styles mirror the transcript dialog's status chips so a run reads
// the same whether it's listed here or opened in the dialog.
function StatusBadge({ status }: { status: TaskStatus }) {
  const { t } = useT("runs");
  const label = t(($) => $.status[status]);
  switch (status) {
    case "completed":
      return (
        <span className="inline-flex items-center rounded-full bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
          {label}
        </span>
      );
    case "failed":
      return (
        <span className="inline-flex items-center rounded-full bg-destructive/15 px-2 py-0.5 text-xs font-medium text-destructive">
          {label}
        </span>
      );
    case "running":
      return (
        <span className="inline-flex items-center rounded-full bg-info/15 px-2 py-0.5 text-xs font-medium text-info">
          {label}
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground capitalize">
          {label}
        </span>
      );
  }
}

function formatDuration(start: string, end: string): string {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return "—";
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${minutes}m ${secs}s`;
}

// Primary source: the canonical snapshot taken at task creation time
// (same fallback chain as the issue detail execution log).
function useTriggerText(task: Task): string {
  const { t } = useT("runs");
  const isRetry = !!task.parent_task_id;
  const retryPrefix = isRetry
    ? task.attempt && task.attempt > 1
      ? t(($) => $.trigger.retry_attempt_prefix, { attempt: task.attempt })
      : t(($) => $.trigger.retry_prefix)
    : "";

  if (task.trigger_summary) return retryPrefix + task.trigger_summary;
  if (isRetry) return retryPrefix.trimEnd();
  if (task.autopilot_run_id) return t(($) => $.trigger.autopilot);
  if (task.trigger_comment_id) return t(($) => $.trigger.comment);
  return t(($) => $.trigger.initial);
}

export function RunsPage() {
  const { t } = useT("runs");
  const wsId = useWorkspaceId();
  const workspace = useCurrentWorkspace();
  const wsPaths = useWorkspacePaths();

  const [statusFilter, setStatusFilter] = useState<TaskStatus | "all">("all");
  const [offset, setOffset] = useState(0);

  const params: UseTasksParams = {
    status: statusFilter === "all" ? undefined : [statusFilter],
    limit: PAGE_SIZE,
    offset,
  };
  const { data, isLoading } = useTasks(wsId, params);
  const tasks = data?.tasks ?? [];
  const total = data?.total ?? 0;

  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleStatusChange = (value: TaskStatus | "all" | null) => {
    if (value === null) return;
    setStatusFilter(value);
    // Reset to the first page whenever the filter changes — the old offset
    // is meaningless against a different result set.
    setOffset(0);
  };

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      {/* Header 1: Workspace breadcrumb — same shape as the issues page */}
      <PageHeader className="gap-1.5">
        <WorkspaceAvatar name={workspace?.name ?? "W"} size="sm" />
        <span className="text-sm text-muted-foreground">{workspace?.name ?? "Workspace"}</span>
        <ChevronRight className="h-3 w-3 text-muted-foreground" />
        <span className="text-sm font-medium">{t(($) => $.page.breadcrumb_title)}</span>
      </PageHeader>

      {/* Header 2: Filters + total */}
      <div className="flex h-12 shrink-0 items-center justify-between px-4">
        <Select value={statusFilter} onValueChange={handleStatusChange}>
          <SelectTrigger size="sm" className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t(($) => $.filter.status_all)}</SelectItem>
            {ALL_STATUSES.map((s) => (
              <SelectItem key={s} value={s}>
                {t(($) => $.status[s])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {total > 0 && (
          <span className="text-xs text-muted-foreground">
            {t(($) => $.page.total_runs, { total })}
          </span>
        )}
      </div>

      {/* Content: scrollable table */}
      {isLoading ? (
        <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full rounded-lg" />
          ))}
        </div>
      ) : tasks.length === 0 ? (
        <div className="flex flex-1 min-h-0 flex-col items-center justify-center gap-2 text-muted-foreground">
          <ScrollText className="h-10 w-10 text-muted-foreground/40" />
          <p className="text-sm">{t(($) => $.page.empty_title)}</p>
          <p className="text-xs">{t(($) => $.page.empty_hint)}</p>
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-2">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t(($) => $.col.status)}</TableHead>
                <TableHead>{t(($) => $.col.agent)}</TableHead>
                <TableHead>{t(($) => $.col.issue)}</TableHead>
                <TableHead>{t(($) => $.col.trigger)}</TableHead>
                <TableHead>{t(($) => $.col.created)}</TableHead>
                <TableHead>{t(($) => $.col.duration)}</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {tasks.map((task) => (
                <RunRow key={task.id} task={task} issueHref={task.issue_id ? wsPaths.issueDetail(task.issue_id) : null} />
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex h-12 shrink-0 items-center justify-end gap-2 border-t px-4">
          <span className="text-xs text-muted-foreground">
            {t(($) => $.pagination.page_of, { page, pages })}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            {t(($) => $.pagination.prev)}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            {t(($) => $.pagination.next)}
          </Button>
        </div>
      )}
    </div>
  );
}

function RunRow({ task, issueHref }: { task: Task; issueHref: string | null }) {
  const { t } = useT("runs");
  const timeAgo = useTimeAgo();
  const trigger = useTriggerText(task);
  const agentName = task.agent?.name ?? "";
  const duration =
    task.started_at && task.completed_at
      ? formatDuration(task.started_at, task.completed_at)
      : "—";

  return (
    <TableRow>
      <TableCell>
        <StatusBadge status={task.status} />
      </TableCell>
      <TableCell className="text-sm">
        {agentName || (task.agent_id ? task.agent_id.slice(0, 8) : "—")}
      </TableCell>
      <TableCell className="text-sm">
        {task.issue_identifier && issueHref ? (
          <AppLink href={issueHref} className="text-primary hover:underline">
            {task.issue_identifier}
          </AppLink>
        ) : (
          "—"
        )}
      </TableCell>
      <TableCell className="max-w-64 truncate text-sm text-muted-foreground" title={trigger}>
        {trigger}
      </TableCell>
      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
        {task.created_at ? timeAgo(task.created_at) : "—"}
      </TableCell>
      <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
        {duration}
      </TableCell>
      <TableCell>
        {task.status !== "queued" && (
          <TranscriptButton task={task} agentName={agentName} title={t(($) => $.transcript_tooltip)} />
        )}
      </TableCell>
    </TableRow>
  );
}
