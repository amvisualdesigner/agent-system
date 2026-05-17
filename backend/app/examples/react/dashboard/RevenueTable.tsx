import React from 'react';
import { Card } from '@/components/ui/Card';

interface RevenueTableProps {
  metrics: string[];
}

interface Row {
  metric: string;
  current: number;
  previous: number;
  change: number;
}

const mockRows: Row[] = [
  { metric: 'revenue', current: 12000, previous: 10000, change: 20 },
  { metric: 'growth', current: 15, previous: 12, change: 25 },
];

export const RevenueTable: React.FC<RevenueTableProps> = ({ metrics }) => {
  const rows = mockRows.filter((r) => metrics.includes(r.metric));

  return (
    <Card>
      <table className="revenue-table">
        <thead>
          <tr>
            <th>Metric</th>
            <th>Current</th>
            <th>Previous</th>
            <th>Change %</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.metric}>
              <td>{row.metric}</td>
              <td>{row.current}</td>
              <td>{row.previous}</td>
              <td>{row.change}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
};
