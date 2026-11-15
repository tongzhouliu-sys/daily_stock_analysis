import { memo } from 'react';
import { cn } from '../../utils/cn';
import ChatAnswer from './ChatAnswer';
import type { ProgressStep } from '../../stores/agentChatStore';
import { describeProgressStage, isStageDoneSuccessful } from '../../utils/chatProgress';

const LIVE_STEP_TYPES = ['stage_start', 'stage_done', 'tool_start', 'tool_done', 'generating'];

interface ChatLiveProgressProps {
  steps: ProgressStep[];
  streamingContent: string | null;
  language: 'zh' | 'en';
}

/**
 * Live "in progress" panel shown while an answer is streaming (T40).
 */
function ChatLiveProgress({ steps, streamingContent, language }: ChatLiveProgressProps) {
  const visibleSteps = steps
    .filter((step) => LIVE_STEP_TYPES.includes(step.type))
    .slice(-6);

  return (
    <div className="chat-msg chat-msg-ai">
      <div role="status" aria-live="polite" className="chat-live-progress">
        <div className="chat-live-head">
          <span className="chat-live-spinner" aria-hidden="true" />
          <span>{describeProgressStage(steps, language)}</span>
        </div>
        {visibleSteps.length > 0 && (
          <div className="chat-live-details">
            {visibleSteps.map((step, index) => (
              <div className="chat-live-step" key={`${index}-${step.type}-${step.stage || step.tool || ''}`}>
                <span
                  className={cn(
                    'chat-live-step-dot',
                    step.type.endsWith('_done') && 'is-done',
                    ((step.type === 'stage_done' && !isStageDoneSuccessful(step.status))
                      || (step.type === 'tool_done' && step.success === false)) && 'is-error',
                  )}
                />
                <span>{describeProgressStage([step], language)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {streamingContent && (
        <div className="chat-prose chat-streaming-preview" aria-live="polite">
          <ChatAnswer content={streamingContent} />
        </div>
      )}
    </div>
  );
}

export default memo(ChatLiveProgress);
