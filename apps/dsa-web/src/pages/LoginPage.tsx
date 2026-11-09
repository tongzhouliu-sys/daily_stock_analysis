import type React from 'react';
import { useState, useEffect } from 'react';
import { ArrowUpRight, Lock } from 'lucide-react';
import { Button, Input } from '../components/common';
import { UiLanguageToggle } from '../components/i18n/UiLanguageToggle';
import { ResearchMark, ResearchPattern } from '../components/layout/ResearchIdentity';
import { ThemeToggle } from '../components/theme/ThemeToggle';
import { useNavigate, useSearchParams } from 'react-router-dom';
import type { ParsedApiError } from '../api/error';
import { isParsedApiError } from '../api/error';
import { useAuth } from '../hooks';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { SettingsAlert } from '../components/settings';
import { resolveLoginRedirect } from '../utils/loginRedirect';

const LoginPage: React.FC = () => {
  const { login, passwordSet, setupState } = useAuth();
  const { t } = useUiLanguage();
  const navigate = useNavigate();

  // Set page title
  useEffect(() => {
    document.title = t('login.pageTitle');
  }, [t]);
  const [searchParams] = useSearchParams();
  const redirect = resolveLoginRedirect(searchParams.get('redirect'));

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | ParsedApiError | null>(null);

  const isFirstTime = setupState === 'no_password' || !passwordSet;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (isFirstTime && password !== passwordConfirm) {
      setError(t('login.passwordMismatch'));
      return;
    }
    setIsSubmitting(true);
    try {
      const result = await login(username, password, isFirstTime ? passwordConfirm : undefined);
      if (result.success) {
        navigate(redirect, { replace: true });
      } else {
        setError(result.error ?? t('login.loginFailed'));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="research-login">
      <section className="research-login-identity" aria-label={t('layout.appFallbackTitle')}>
        <div className="research-brand"><ResearchMark /><strong>DSA</strong></div>
        <div className="research-login-brand-copy">
          <p className="research-overline">Market Research</p>
          <p className="research-login-wordmark">Daily Stock <br />Analysis</p>
          <p>{t('home.workspaceDescription')}</p>
        </div>
        <ResearchPattern />
      </section>
      <section className="research-login-form-side">
        <div className="research-login-controls">
          <UiLanguageToggle />
          <ThemeToggle />
        </div>
        <div className="research-login-form">
          <div className="research-login-lock"><Lock size={20} aria-hidden="true" /></div>
          <h1 className="research-display">{t(isFirstTime ? 'login.setupTitle' : 'login.adminLogin')}</h1>
          <p className="mt-2 text-sm text-secondary-text">
            {t(isFirstTime ? 'login.setupDescription' : 'login.loginDescription')}
          </p>
          <form onSubmit={handleSubmit} className="mt-7 space-y-5">
            <Input
              id="username"
              name="username"
              type="text"
              appearance="login"
              label={t('login.username')}
              placeholder={t('login.usernamePlaceholder')}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={isSubmitting}
              autoFocus
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
            />
            <Input
              id="password"
              name="password"
              type="password"
              appearance="login"
              allowTogglePassword
              iconType="password"
              label={t(isFirstTime ? 'login.adminPassword' : 'login.loginPassword')}
              placeholder={t(isFirstTime ? 'login.setupPasswordPlaceholder' : 'login.loginPasswordPlaceholder')}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isSubmitting}
              autoComplete={isFirstTime ? 'new-password' : 'current-password'}
              required
            />
            {isFirstTime ? (
              <Input
                id="passwordConfirm"
                type="password"
                appearance="login"
                allowTogglePassword
                iconType="password"
                label={t('login.confirmPassword')}
                placeholder={t('login.confirmPasswordPlaceholder')}
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                disabled={isSubmitting}
                autoComplete="new-password"
                required
              />
            ) : null}

            {error ? (
              <SettingsAlert
                title={t(isFirstTime ? 'login.setupFailed' : 'login.validationFailed')}
                message={isParsedApiError(error) ? error.message : error}
                variant="error"
              />
            ) : null}

            <Button
              type="submit"
              size="lg"
              className="w-full justify-between"
              isLoading={isSubmitting}
              loadingText={t(isFirstTime ? 'login.setupSubmitting' : 'login.loginSubmitting')}
            >
              {t(isFirstTime ? 'login.setupSubmit' : 'login.loginSubmit')}
              <ArrowUpRight size={17} aria-hidden="true" />
            </Button>
          </form>
        </div>
      </section>
    </main>
  );
};

export default LoginPage;
