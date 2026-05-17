import React from 'react';
import { Card } from '@/components/ui/Card';

interface Column<T> {
  key: keyof T;
  label: string;
  sortable?: boolean;
}

interface AnalyticsTableProps<T> {
  columns: Column<T>[];
  data: T[];
  pageSize?: number;
}

export const AnalyticsTable = <T extends Record<string, unknown>>({
  columns,
  data,
  pageSize = 10,
}: AnalyticsTableProps<T>) => {
  const [sortKey, setSortKey] = React.useState<keyof T | null>(null);
  const [sortDir, setSortDir] = React.useState<'asc' | 'desc'>('asc');
  const [page, setPage] = React.useState(0);

  const sorted = React.useMemo(() => {
    if (!sortKey) return data;
    return [...data].sort((a, b) => {
      const av = String(a[sortKey]);
      const bv = String(b[sortKey]);
      return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
    });
  }, [data, sortKey, sortDir]);

  const paginated = sorted.slice(page * pageSize, (page + 1) * pageSize);

  const handleSort = (key: keyof T) => {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('asc');
    }
  };

  return (
    <Card>
      <table className="analytics-table">
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={String(col.key)}
                onClick={col.sortable ? () => handleSort(col.key) : undefined}
                style={{ cursor: col.sortable ? 'pointer' : 'default' }}
              >
                {col.label}
                {sortKey === col.key && (sortDir === 'asc' ? ' ▲' : ' ▼')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {paginated.map((row, i) => (
            <tr key={i}>
              {columns.map((col) => (
                <td key={String(col.key)}>{String(row[col.key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="pagination">
        <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Prev</button>
        <span>Page {page + 1}</span>
        <button disabled={(page + 1) * pageSize >= data.length} onClick={() => setPage((p) => p + 1)}>Next</button>
      </div>
    </Card>
  );
};
