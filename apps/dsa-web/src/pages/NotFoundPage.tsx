import { useEffect } from 'react';
import { ArrowLeft } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useUiLanguage } from '../contexts/UiLanguageContext';

export default function NotFoundPage() {
  const { t } = useUiLanguage();

  useEffect(() => {
    document.title = t('notFound.pageTitle');
  }, [t]);

  return (
    <div className="research-page">
      <header className="research-page-heading">
        <p className="font-mono text-sm text-primary">404</p>
        <h1 className="research-display mt-2 text-foreground">{t('notFound.title')}</h1>
        <p className="mt-3 text-sm text-secondary-text">{t('notFound.description')}</p>
      </header>
      <Link to="/" className="btn-secondary mt-5">
        <ArrowLeft size={16} aria-hidden="true" />
        {t('notFound.backHome')}
      </Link>
    </div>
  );
}
