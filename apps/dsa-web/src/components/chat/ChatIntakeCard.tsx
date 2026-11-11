import React, { useState, useMemo } from 'react';
import { cn } from '../../utils/cn';
import type { Question, Answer, QuestionOption } from '../../api/agentIntake';
import { Check } from 'lucide-react';

interface ChatIntakeCardProps {
  questions: Question[];
  initialAnswers?: Answer[];
  onConfirm: (answers: Answer[]) => void;
  onSkip: () => void;
  onCancel: () => void;
  isLoading?: boolean;
}

/**
 * ChatIntakeCard - Displays guided questioning flow for new chat sessions.
 *
 * T3: Frontend guided interface.
 */
export const ChatIntakeCard: React.FC<ChatIntakeCardProps> = ({
  questions,
  initialAnswers = [],
  onConfirm,
  onSkip,
  onCancel,
  isLoading = false,
}) => {
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [answers, setAnswers] = useState<Answer[]>(initialAnswers);

  const currentQuestion = questions[currentQuestionIndex];
  const isLastQuestion = currentQuestionIndex === questions.length - 1;
  const progress = ((currentQuestionIndex + 1) / questions.length) * 100;

  // Get current answer for the question
  const currentAnswer = useMemo(
    () => answers.find((a) => a.question_id === currentQuestion.id),
    [answers, currentQuestion.id]
  );

  // Handle option selection
  const handleOptionSelect = (option: QuestionOption) => {
    if (!currentQuestion) return;

    let newOptionIds: string[];

    if (currentQuestion.type === 'single') {
      // Single choice: replace selection
      newOptionIds = [option.id];
    } else {
      // Multiple choice: toggle selection
      const current = currentAnswer?.option_ids || [];
      if (current.includes(option.id)) {
        newOptionIds = current.filter((id) => id !== option.id);
      } else {
        // Check max_selections
        const maxSelections = currentQuestion.max_selections;
        if (maxSelections && current.length >= maxSelections) {
          // Replace oldest selection
          newOptionIds = [...current.slice(1), option.id];
        } else {
          newOptionIds = [...current, option.id];
        }
      }
    }

    // Determine source
    const source = option.is_unknown ? 'unknown' : 'user_choice';

    // Update answer
    const newAnswer: Answer = {
      question_id: currentQuestion.id,
      option_ids: newOptionIds,
      source,
    };

    setAnswers((prev) => {
      const filtered = prev.filter((a) => a.question_id !== currentQuestion.id);
      return [...filtered, newAnswer];
    });
  };

  // Handle next question
  const handleNext = () => {
    if (isLastQuestion) {
      onConfirm(answers);
    } else {
      setCurrentQuestionIndex((prev) => prev + 1);
    }
  };

  // Handle previous question
  const handlePrevious = () => {
    if (currentQuestionIndex > 0) {
      setCurrentQuestionIndex((prev) => prev - 1);
    }
  };

  // Check if current question has an answer
  const hasAnswer = currentAnswer && currentAnswer.option_ids.length > 0;

  return (
    <div className="bg-card border border-border rounded-lg p-6 space-y-6">
      {/* Progress bar */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>问题 {currentQuestionIndex + 1} / {questions.length}</span>
          <span>{Math.round(progress)}%</span>
        </div>
        <div className="h-2 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full bg-primary transition-all duration-300"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* Question */}
      {currentQuestion && (
        <div className="space-y-4">
          <div className="space-y-2">
            <h3 className="text-lg font-semibold text-foreground">
              {currentQuestion.title}
            </h3>
            {currentQuestion.help && (
              <p className="text-sm text-muted-foreground">
                {currentQuestion.help}
              </p>
            )}
          </div>

          {/* Options */}
          <div className="grid gap-3">
            {currentQuestion.options.map((option) => {
              const isSelected = currentAnswer?.option_ids.includes(option.id) || false;

              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => handleOptionSelect(option)}
                  disabled={isLoading}
                  className={cn(
                    'flex items-center gap-3 p-4 border rounded-lg text-left transition-colors',
                    'hover:bg-accent hover:border-accent-foreground/20',
                    'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
                    isSelected
                      ? 'bg-accent border-primary'
                      : 'bg-background border-border',
                    isLoading && 'opacity-50 cursor-not-allowed'
                  )}
                  aria-pressed={isSelected}
                >
                  {/* Selection indicator */}
                  <div
                    className={cn(
                      'flex-shrink-0 w-5 h-5 rounded-full border-2 flex items-center justify-center',
                      currentQuestion.type === 'multiple' ? 'rounded' : 'rounded-full',
                      isSelected
                        ? 'bg-primary border-primary'
                        : 'bg-background border-muted-foreground/30'
                    )}
                  >
                    {isSelected && (
                      <Check className="w-3 h-3 text-primary-foreground" />
                    )}
                  </div>

                  {/* Option label */}
                  <span className="flex-1 text-sm font-medium text-foreground">
                    {option.label}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-between pt-4 border-t border-border">
        <div className="flex gap-2">
          {/* Previous button */}
          <button
            type="button"
            onClick={handlePrevious}
            disabled={currentQuestionIndex === 0 || isLoading}
            className={cn(
              'px-4 py-2 text-sm font-medium rounded-lg transition-colors',
              'text-muted-foreground hover:text-foreground',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            上一题
          </button>

          {/* Skip button */}
          <button
            type="button"
            onClick={onSkip}
            disabled={isLoading}
            className={cn(
              'px-4 py-2 text-sm font-medium rounded-lg transition-colors',
              'text-muted-foreground hover:text-foreground',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            跳过
          </button>
        </div>

        <div className="flex gap-2">
          {/* Cancel button */}
          <button
            type="button"
            onClick={onCancel}
            disabled={isLoading}
            className={cn(
              'px-4 py-2 text-sm font-medium rounded-lg transition-colors',
              'text-muted-foreground hover:text-foreground',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            取消
          </button>

          {/* Next/Confirm button */}
          <button
            type="button"
            onClick={handleNext}
            disabled={!hasAnswer || isLoading}
            className={cn(
              'px-4 py-2 text-sm font-medium rounded-lg transition-colors',
              'bg-primary text-primary-foreground hover:bg-primary/90',
              'disabled:opacity-50 disabled:cursor-not-allowed'
            )}
          >
            {isLoading ? '处理中...' : isLastQuestion ? '确认' : '下一题'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChatIntakeCard;
