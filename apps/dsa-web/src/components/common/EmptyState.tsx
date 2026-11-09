import type React from 'react';
import { cn } from '../../utils/cn';

interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}

export const EmptyState: React.FC<EmptyStateProps> = ({
  title,
  description,
  icon,
  action,
  className = '',
}) => {
  return (
    <div className={cn('research-empty-state border border-border bg-card px-6 py-9 text-center', className)}>
      {icon ? <div className="research-empty-icon mx-auto mb-4">{icon}</div> : null}
      <h3 className="text-lg font-medium text-foreground">{title}</h3>
      {description ? <p className="mx-auto mt-2 max-w-md text-sm text-secondary-text">{description}</p> : null}
      {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
    </div>
  );
};
