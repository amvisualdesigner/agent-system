import React from 'react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { Card } from '@/components/ui/Card';

interface RevenueChartProps {
  metric: string;
}

const data = [
  { month: 'Jan', value: 4000 },
  { month: 'Feb', value: 3000 },
  { month: 'Mar', value: 5000 },
];

export const RevenueChart: React.FC<RevenueChartProps> = ({ metric }) => {
  return (
    <Card>
      <h3 className="chart-title">{metric} over time</h3>
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="month" />
          <YAxis />
          <Tooltip />
          <Area type="monotone" dataKey="value" stroke="#3b82f6" fill="#93c5fd" />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  );
};
