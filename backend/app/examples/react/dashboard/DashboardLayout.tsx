import React from 'react';

interface DashboardLayoutProps {
  children: React.ReactNode;
}

export const DashboardLayout: React.FC<DashboardLayoutProps> = ({ children }) => {
  return (
    <div className="dashboard-layout">
      <header className="dashboard-header">
        <h1>Dashboard</h1>
      </header>
      <div className="dashboard-body">
        <aside className="dashboard-sidebar">
          <nav>Navigation</nav>
        </aside>
        <main className="dashboard-main">
          {children}
        </main>
      </div>
    </div>
  );
};
