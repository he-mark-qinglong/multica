"use client";

import { useMemo, useState } from "react";
import { ChevronRight, GitCompareArrows } from "lucide-react";
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { useCurrentWorkspace } from "@multica/core/paths";
import type { GateDetailEntry, RunMetric } from "@multica/core/types";
import { useTimeAgo } from "../../i18n";
import { useCampaigns } from "@multica/core/hooks/use-campaigns";
import { useEquitySeries, useMetrics } from "@multica/core/hooks/use-metrics";
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
import { Input } from "@multica/ui/components/ui/input";
import { Checkbox } from "@multica/ui/components/ui/checkbox";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@multica/ui/components/ui/popover";
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@multica/ui/components/ui/chart";
import { PageHeader } from "../../layout/page-header";
import { WorkspaceAvatar } from "../../workspace/workspace-avatar";
import { useT } from "../../i18n";
import { downsample, parseEquityCsv } from "../utils/equity-csv";

// Hard cap on overlayed equity curves — past five lines the chart stops
// being a comparison and becomes spaghetti, and we run out of chart-*
// color tokens anyway.
const MAX_SELECTED = 5;

function fmt(v: number | null | undefined, digits = 2): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "—";
}

function iterationLabel(m: RunMetric): string {
  return m.iteration || m.id.slice(0, 8);
}

function GateBadge({ metric }: { metric: RunMetric }) {
  const { t } = useT("compare");
  const status = metric.gate_status;
  if (status !== "pass" && status !== "fail") {
    return <span className="text-sm text-muted-foreground">{t(($) => $.gate.none)}</span>;
  }
  const detail = metric.gate_detail ?? [];
  const badge =
    status === "pass" ? (
      <span className="inline-flex items-center rounded-full bg-success/15 px-2 py-0.5 text-xs font-medium text-success">
        {t(($) => $.gate.pass)}
      </span>
    ) : (
      <span className="inline-flex items-center rounded-full bg-destructive/15 px-2 py-0.5 text-xs font-medium text-destructive">
        {t(($) => $.gate.fail)}
      </span>
    );
  if (detail.length === 0) return badge;
  return (
    <Popover>
      <PopoverTrigger render={<button type="button" className="cursor-pointer" />}>
        {badge}
      </PopoverTrigger>
      <PopoverContent className="w-80">
        <p className="mb-2 text-xs font-medium">{t(($) => $.gate.details_title)}</p>
        <ul className="space-y-1">
          {detail.map((g: GateDetailEntry, i: number) => (
            <li key={i} className="flex items-center justify-between gap-2 text-xs">
              <span className="text-muted-foreground">
                {g.rule} {g.op} {g.threshold ?? "—"}
              </span>
              <span className={g.pass ? "text-success" : "text-destructive"}>
                {fmt(g.actual)} · {g.pass ? t(($) => $.gate.rule_pass) : t(($) => $.gate.rule_fail)}
              </span>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

type SeriesWarning = "no_artifact" | "unparseable" | "fetch_failed";

interface EquitySeriesData {
  key: string;
  label: string;
  points: number[];
}

export function ComparePage() {
  const { t } = useT("compare");
  const timeAgo = useTimeAgo();
  const workspace = useCurrentWorkspace();

  const [campaign, setCampaign] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const campaignsQuery = useCampaigns();
  const campaigns = campaignsQuery.data?.campaigns ?? [];
  const { data, isLoading } = useMetrics(campaign);
  const metrics = data?.metrics ?? [];

  // Selected rows that still exist in the current result set — switching
  // campaigns drops stale ids automatically instead of needing an effect.
  const selectedMetrics = useMemo(
    () => metrics.filter((m) => selectedIds.includes(m.id)),
    [metrics, selectedIds],
  );

  const toggleSelected = (id: string, checked: boolean) => {
    setSelectedIds((prev) =>
      checked ? [...prev, id].slice(0, MAX_SELECTED) : prev.filter((x) => x !== id),
    );
  };

  // --- Equity overlay -----------------------------------------------------
  const equityQueries = useEquitySeries(selectedMetrics);

  const { series, warnings, equityLoading } = useMemo(() => {
    const series: EquitySeriesData[] = [];
    const warnings: { label: string; kind: SeriesWarning }[] = [];
    let equityLoading = false;
    selectedMetrics.forEach((m, i) => {
      const label = iterationLabel(m);
      const q = equityQueries[i];
      if (!m.task_id) {
        warnings.push({ label, kind: "no_artifact" });
        return;
      }
      if (!q || q.isPending) {
        equityLoading = true;
        return;
      }
      if (q.isError) {
        warnings.push({ label, kind: "fetch_failed" });
        return;
      }
      if (!q.data?.csv) {
        warnings.push({ label, kind: "no_artifact" });
        return;
      }
      const parsed = parseEquityCsv(q.data.csv);
      if (!parsed) {
        warnings.push({ label, kind: "unparseable" });
        return;
      }
      series.push({ key: `s${i}`, label, points: downsample(parsed) });
    });
    return { series, warnings, equityLoading };
  }, [selectedMetrics, equityQueries]);

  const chartConfig = useMemo(
    () =>
      Object.fromEntries(
        series.map((s, i) => [
          s.key,
          { label: s.label, color: `var(--chart-${(i % 5) + 1})` },
        ]),
      ) satisfies ChartConfig,
    [series],
  );

  // Common-index merge: row i holds every series' i-th point (series may
  // have different lengths — missing points stay undefined and recharts
  // just ends that line early).
  const chartData = useMemo(() => {
    const maxLen = Math.max(0, ...series.map((s) => s.points.length));
    return Array.from({ length: maxLen }, (_, i) => {
      const row: Record<string, number> = { step: i };
      for (const s of series) {
        const v = s.points[i];
        if (v !== undefined) row[s.key] = v;
      }
      return row;
    });
  }, [series]);

  const warningText = (kind: SeriesWarning, label: string): string => {
    switch (kind) {
      case "no_artifact":
        return t(($) => $.chart.series_missing, { label });
      case "unparseable":
        return t(($) => $.chart.series_unparseable, { label });
      case "fetch_failed":
        return t(($) => $.chart.series_error, { label });
    }
  };

  return (
    <div className="flex flex-1 min-h-0 flex-col">
      {/* Header 1: Workspace breadcrumb — same shape as the runs page */}
      <PageHeader className="gap-1.5">
        <WorkspaceAvatar name={workspace?.name ?? "W"} size="sm" />
        <span className="text-sm text-muted-foreground">{workspace?.name ?? "Workspace"}</span>
        <ChevronRight className="h-3 w-3 text-muted-foreground" />
        <span className="text-sm font-medium">{t(($) => $.page.breadcrumb_title)}</span>
      </PageHeader>

      {/* Header 2: campaign picker + row count */}
      <div className="flex h-12 shrink-0 items-center justify-between px-4">
        {campaigns.length > 0 ? (
          <Select
            value={campaign ?? ""}
            onValueChange={(value: string | null) => {
              if (value) setCampaign(value);
            }}
          >
            <SelectTrigger size="sm" className="w-56">
              <SelectValue placeholder={t(($) => $.filter.campaign_placeholder)} />
            </SelectTrigger>
            <SelectContent>
              {campaigns.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          // Free-text fallback: when the campaigns endpoint is empty (or
          // not deployed yet) the user can still type a campaign name.
          <form
            className="flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (draft.trim()) setCampaign(draft.trim());
            }}
          >
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={t(($) => $.filter.campaign_free_placeholder)}
              className="h-8 w-56"
            />
            <Button type="submit" variant="outline" size="sm">
              {t(($) => $.filter.campaign_apply)}
            </Button>
          </form>
        )}
        {campaign && metrics.length > 0 && (
          <span className="text-xs text-muted-foreground">
            {t(($) => $.page.total_iterations, { total: metrics.length })}
          </span>
        )}
      </div>

      {/* Content */}
      {!campaign ? (
        <div className="flex flex-1 min-h-0 flex-col items-center justify-center gap-2 text-muted-foreground">
          <GitCompareArrows className="h-10 w-10 text-muted-foreground/40" />
          <p className="text-sm">{t(($) => $.page.no_campaign_title)}</p>
          <p className="text-xs">{t(($) => $.page.no_campaign_hint)}</p>
        </div>
      ) : isLoading ? (
        <div className="flex-1 min-h-0 overflow-y-auto p-2 space-y-1">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full rounded-lg" />
          ))}
        </div>
      ) : metrics.length === 0 ? (
        <div className="flex flex-1 min-h-0 flex-col items-center justify-center gap-2 text-muted-foreground">
          <GitCompareArrows className="h-10 w-10 text-muted-foreground/40" />
          <p className="text-sm">{t(($) => $.page.empty_title)}</p>
          <p className="text-xs">{t(($) => $.page.empty_hint)}</p>
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-2">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8" />
                <TableHead>{t(($) => $.col.iteration)}</TableHead>
                <TableHead>{t(($) => $.col.timeframe)}</TableHead>
                <TableHead>{t(($) => $.col.sharpe)}</TableHead>
                <TableHead>{t(($) => $.col.ann_return)}</TableHead>
                <TableHead>{t(($) => $.col.max_drawdown)}</TableHead>
                <TableHead>{t(($) => $.col.profit_factor)}</TableHead>
                <TableHead>{t(($) => $.col.oos_sharpe)}</TableHead>
                <TableHead>{t(($) => $.col.oos_windows)}</TableHead>
                <TableHead>{t(($) => $.col.gate)}</TableHead>
                <TableHead>{t(($) => $.col.created)}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {metrics.map((m) => {
                const checked = selectedIds.includes(m.id);
                return (
                  <TableRow key={m.id}>
                    <TableCell>
                      <Checkbox
                        checked={checked}
                        disabled={!checked && selectedIds.length >= MAX_SELECTED}
                        onCheckedChange={(v) => toggleSelected(m.id, v === true)}
                        aria-label={iterationLabel(m)}
                      />
                    </TableCell>
                    <TableCell className="text-sm font-medium">{iterationLabel(m)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {m.timeframe || "—"}
                    </TableCell>
                    <TableCell className="text-sm">{fmt(m.sharpe)}</TableCell>
                    <TableCell className="text-sm">{fmt(m.ann_return)}</TableCell>
                    <TableCell className="text-sm">{fmt(m.max_drawdown)}</TableCell>
                    <TableCell className="text-sm">{fmt(m.profit_factor)}</TableCell>
                    <TableCell className="text-sm">{fmt(m.oos_sharpe)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {m.oos_windows ?? "—"}
                    </TableCell>
                    <TableCell>
                      <GateBadge metric={m} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                      {m.created_at ? timeAgo(m.created_at) : "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          {/* Equity overlay */}
          <div className="mt-4 rounded-lg border p-4">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="text-sm font-medium">{t(($) => $.chart.title)}</h2>
              <span className="text-xs text-muted-foreground">
                {t(($) => $.chart.select_hint, { max: MAX_SELECTED })}
              </span>
            </div>

            {warnings.map((w) => (
              <p key={w.label} className="text-xs text-warning">
                {warningText(w.kind, w.label)}
              </p>
            ))}

            {series.length > 0 ? (
              <ChartContainer config={chartConfig} className="aspect-[3/1] w-full">
                <LineChart data={chartData} margin={{ left: 0, right: 0, top: 4, bottom: 0 }}>
                  <CartesianGrid vertical={false} />
                  <XAxis
                    dataKey="step"
                    tickLine={false}
                    axisLine={false}
                    tickMargin={8}
                    interval="preserveStartEnd"
                  />
                  <YAxis
                    tickLine={false}
                    axisLine={false}
                    tickMargin={8}
                    width={70}
                    tickFormatter={(v: number) =>
                      Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${v}`
                    }
                  />
                  <ChartTooltip content={<ChartTooltipContent />} />
                  <ChartLegend content={<ChartLegendContent />} />
                  {series.map((s) => (
                    <Line
                      key={s.key}
                      dataKey={s.key}
                      name={s.label}
                      stroke={`var(--color-${s.key})`}
                      strokeWidth={1.5}
                      dot={false}
                      connectNulls
                      isAnimationActive={false}
                    />
                  ))}
                </LineChart>
              </ChartContainer>
            ) : selectedMetrics.length === 0 ? (
              <p className="py-8 text-center text-xs text-muted-foreground">
                {t(($) => $.chart.select_hint, { max: MAX_SELECTED })}
              </p>
            ) : equityLoading ? (
              <Skeleton className="aspect-[3/1] w-full rounded-lg" />
            ) : (
              // Missing equity artifacts are a normal state (older runs
              // didn't upload one), so this is a hint, not an error.
              <div className="flex flex-col items-center justify-center gap-1 py-8 text-muted-foreground">
                <p className="text-sm">{t(($) => $.chart.no_equity_title)}</p>
                <p className="text-xs">{t(($) => $.chart.no_equity_hint)}</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
