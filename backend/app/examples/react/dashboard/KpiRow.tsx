import React from 'react';
import { Card } from '@/components/ui/Card';

interface KpiRowProps {
  metrics: string[];
}

export const KpiRow: React.FC<KpiRowProps> = ({ metrics }) => {
  return (
    <div className="kpi-row">
      {metrics.map((metric) => (
        <Card key={metric}>
          <div className="kpi-card">
            <span className="kpi-label">{metric}</span>
            <span className="kpi-value">--</span>
          </div>
        </Card>
      ))}
    </div>
  );
};
