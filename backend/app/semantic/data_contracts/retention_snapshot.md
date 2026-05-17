# contract.retention_snapshot

## Layer Type
contract

## Hierarchy
contract → component → pattern → layout → skill

## Purpose
Semantic contract for customer retention and churn data.

## Fields
- retention_rate: number — percentage of retained users
- churn_rate: number — percentage of lost users
- period: string — monthly | quarterly
- cohort: string — cohort identifier
- total_customers: number — active customers in period
- new_customers: number — acquired in period

## Common Usage
- retention analytics dashboards
- customer success reporting
- SaaS subscription metrics
- cohort analysis views

## Related
- component.RetentionCard
- skill.analytics.retention_overview
- contract.kpi_metric
- pattern.executive_summary

## Keywords
retention, churn, cohort, contract, customer success, saas metric, subscription data
