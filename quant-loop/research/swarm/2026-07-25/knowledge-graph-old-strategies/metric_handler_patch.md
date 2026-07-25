# metric.go patch proposal

Two small, backward-compatible changes to `server/internal/handler/metric.go`:

## 1. Parse `daily_returns` from the metrics blob

Add `daily_returns` to the known float-array keys so it can be forwarded to the
strict gate as a profit-factor fallback.

```go
type runMetricFields struct {
    Sharpe       *float64
    Sortino      *float64
    Calmar       *float64
    AnnReturn    *float64
    MaxDrawdown  *float64
    ProfitFactor *float64
    OOSSharpe    *float64
    OOSWindows   *int32
    Timeframe    string
    Symbols      []string
    Params       json.RawMessage
    Extra        json.RawMessage
    DailyReturns []float64   // NEW
}
```

In `parseRunMetricJSON`, after the float-column loop:

```go
if v, present := obj["daily_returns"]; present {
    if arr, ok := v.([]any); ok {
        for _, item := range arr {
            if n, isNum := jsonNumber(item); isNum {
                f.DailyReturns = append(f.DailyReturns, n)
            }
        }
    }
}
```

## 2. Forward daily returns to the gate evaluator

```go
func gateMetricsFromFields(f *runMetricFields) gate.Metrics {
    return gate.Metrics{
        Sharpe:       f.Sharpe,
        AnnReturn:    f.AnnReturn,
        MaxDrawdown:  f.MaxDrawdown,
        ProfitFactor: f.ProfitFactor,
        OOSSharpe:    f.OOSSharpe,
        OOSWindows:   f.OOSWindows,
        DailyReturns: f.DailyReturns, // NEW
    }
}
```

Strategy workers that already emit a `daily_returns` array (or a compatible
`equity` array from which they compute one) get automatic profit-factor
fallback.  Strategies that do not emit it keep the old behaviour: missing PF
-> fail in strict mode, hold in legacy mode.
