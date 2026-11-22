import type React from 'react';
import { AlertTriangle, CheckCircle2, CircleDashed, CircleSlash, Loader2, WifiOff } from 'lucide-react';
import { cn } from '../../utils/cn';
import type { RunState } from '../../utils/runStatus';

interface RunStatusIconProps {
  state: RunState;
  className?: string;
}

const ICON_CLASS = 'h-4 w-4 shrink-0';

/**
 * One icon per run state. Status is never conveyed by colour alone (a11y).
 */
export const RunStatusIcon: React.FC<RunStatusIconProps> = ({ state, className }) => {
  const common = cn(ICON_CLASS, className);
  switch (state) {
    case 'done':
      return <CheckCircle2 className={cn(common, 'text-success')} aria-hidden="true" />;
    case 'failed':
      return <AlertTriangle className={cn(common, 'text-danger')} aria-hidden="true" />;
    case 'cancelled':
      return <CircleSlash className={cn(common, 'text-muted-text')} aria-hidden="true" />;
    case 'disconnected':
      return <WifiOff className={cn(common, 'text-warning')} aria-hidden="true" />;
    case 'queued':
      return <CircleDashed className={cn(common, 'text-muted-text')} aria-hidden="true" />;
    case 'stalled':
      return <Loader2 className={cn(common, 'animate-spin text-warning')} aria-hidden="true" />;
    case 'slow':
    case 'running':
    default:
      return <Loader2 className={cn(common, 'animate-spin text-cyan')} aria-hidden="true" />;
  }
};

export default RunStatusIcon;
