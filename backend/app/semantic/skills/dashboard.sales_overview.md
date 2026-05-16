---
type: skill
name: dashboard.sales_overview
tags:
  - dashboard
  - analytics
  - sales
  - bi
priority: 10
---

# Sales Overview Dashboard Skill

## Intent
Create a full sales analytics dashboard with KPI metrics and revenue visualization.

---

## Composition Model

This skill composes a complete BI page using semantic building blocks.

### Layout
- AnalyticsGrid (root container)

### Sections

#### 1. KPI Row
Use:
- KpiCard

Metrics:
- revenue
- growth
- retention

Layout rules:
- horizontal row
- max 3-4 KPIs
- always first section

---

#### 2. Revenue Timeseries
Use:
- dashboard.timeseries_panel

Data:
- metric: monthly_revenue
- aggregation: monthly

Layout rules:
- full width row
- second section
- must include loading + empty state

---

#### 3. Optional Insights (future extension)
Reserved for:
- anomaly detection
- comparisons
- alerts

---

## Data Contracts

### KPI Card
- title: string
- value: number
- delta: number

### Timeseries
- x: datetime
- y: number

---

## Layout Contract

Must use:
- AnalyticsGrid

Constraints:
- max_columns: 4
- responsive: true

---

## Rendering Intent

This skill does NOT define implementation.

It defines:

- structure
- composition
- ordering
- constraints
- semantic meaning

---

## Anti-patterns

- Do NOT generate raw JSX
- Do NOT invent new components
- Do NOT bypass AnalyticsGrid
- Do NOT inline charts outside timeseries_panel