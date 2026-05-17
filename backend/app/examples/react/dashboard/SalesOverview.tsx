import React from 'react';
import { DashboardLayout } from '@/components/layout/DashboardLayout';
import { KpiRow } from './KpiRow';
import { RevenueChart } from './RevenueChart';
import { RevenueTable } from './RevenueTable';

interface SalesOverviewProps {
  metrics: string[];
  timeseriesMetric?: string;
}

export const SalesOverview: React.FC<SalesOverviewProps> = ({
  metrics,
  timeseriesMetric = 'revenue',
}) => {
  return (
    <DashboardLayout>
      <KpiRow metrics={metrics} />
      <RevenueChart metric={timeseriesMetric} />
      <RevenueTable metrics={metrics} />
    </DashboardLayout>
  );
};
