import type React from 'react';
import { useState, useEffect, useCallback } from 'react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { authApi, type LoginLogItem, type LoginLogResponse } from '../../api/auth';
import { Button, Badge } from '../common';
import { SettingsAlert } from './SettingsAlert';
import { SettingsSectionCard } from './SettingsSectionCard';
import { useAuth } from '../../hooks';

type FilterType = 'all' | 'success' | 'failure';

export const LoginLogCard: React.FC = () => {
  const { t, language } = useUiLanguage();
  const { authEnabled } = useAuth();
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<LoginLogResponse | null>(null);
  const [filter, setFilter] = useState<FilterType>('all');
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 20;

  const fetchLogs = useCallback(async () => {
    if (!authEnabled) return;
    
    setLoading(true);
    setError(null);
    try {
      const offset = (currentPage - 1) * pageSize;
      const response = await authApi.getLoginLogs({
        result: filter,
        limit: pageSize,
        offset,
      });
      setData(response);
    } catch (err) {
      setError(t('settings.loginLogError'));
      console.error('Failed to fetch login logs:', err);
    } finally {
      setLoading(false);
    }
  }, [authEnabled, filter, currentPage, t]);

  useEffect(() => {
    fetchLogs();
  }, [fetchLogs]);

  // Reset to page 1 when filter changes
  useEffect(() => {
    setCurrentPage(1);
  }, [filter]);

  const formatTime = (isoString: string) => {
    const date = new Date(isoString);
    return date.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const getEventLabel = (eventType: string) => {
    const key = `settings.loginLogEvent${eventType.charAt(0).toUpperCase() + eventType.slice(1).replace(/_([a-z])/g, (_, l) => l.toUpperCase())}`;
    return t(key as any) || eventType;
  };

  const getFailureLabel = (reason?: string) => {
    if (!reason) return '';
    const key = `settings.loginLogFailure${reason.charAt(0).toUpperCase() + reason.slice(1).replace(/_([a-z])/g, (_, l) => l.toUpperCase())}`;
    return t(key as any) || reason;
  };

  const getClientLabel = (item: LoginLogItem) => {
    const clientType = item.client.type;
    const typeKey = `settings.loginLogClient${clientType.charAt(0).toUpperCase() + clientType.slice(1)}`;
    const typeLabel = t(typeKey as any) || clientType;
    
    let detail = '';
    if (clientType === 'browser' && item.client.browser) {
      detail = item.client.browser;
      if (item.client.browserVersion) {
        detail += ` ${item.client.browserVersion}`;
      }
    } else if (clientType === 'desktop') {
      detail = item.client.browser || '';
    }
    
    return detail ? `${typeLabel} · ${detail}` : typeLabel;
  };

  const getDeviceLabel = (deviceType: string) => {
    const key = `settings.loginLogDevice${deviceType.charAt(0).toUpperCase() + deviceType.slice(1)}`;
    return t(key as any) || deviceType;
  };

  const getLocationText = (item: LoginLogItem) => {
    const { location } = item;
    
    if (item.ipScope === 'loopback') {
      return t('settings.loginLogScopeLoopback');
    }
    if (item.ipScope === 'private') {
      return t('settings.loginLogScopePrivate');
    }
    if (item.ipScope === 'invalid') {
      return t('settings.loginLogScopeInvalid');
    }
    
    // Public IP
    if (location.status === 'resolved') {
      const names = language === 'zh' ? location.zh : location.en;
      const parts = [names.country, names.region, names.city].filter(Boolean);
      return parts.length > 0 ? parts.join(' · ') : item.ip;
    }
    if (location.status === 'pending') {
      return t('settings.loginLogGeoStatusPending');
    }
    if (location.status === 'not_configured') {
      return t('settings.loginLogGeoStatusNotConfigured');
    }
    if (location.status === 'unresolved') {
      return t('settings.loginLogGeoStatusUnresolved');
    }
    
    return item.ip;
  };

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  if (!authEnabled) {
    return (
      <SettingsSectionCard
        title={t('settings.loginLogTitle')}
        description={t('settings.loginLogDescription')}
      >
        <SettingsAlert
          title={t('settings.loginLogEnableAuthFirst')}
          message=""
          variant="warning"
        />
      </SettingsSectionCard>
    );
  }

  return (
    <SettingsSectionCard
      title={t('settings.loginLogTitle')}
      description={t('settings.loginLogDescription')}
    >
      {/* Geo not configured warning */}
      {data?.geo && !data.geo.offline.configured && (
        <SettingsAlert
          title={t('settings.loginLogGeoNotConfigured')}
          message=""
          variant="warning"
          className="mb-4"
        />
      )}

      {/* Filter buttons */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex gap-2">
          <Button
            variant={filter === 'all' ? 'primary' : 'secondary'}
            onClick={() => setFilter('all')}
            size="sm"
          >
            {t('settings.loginLogFilterAll')}
          </Button>
          <Button
            variant={filter === 'success' ? 'primary' : 'secondary'}
            onClick={() => setFilter('success')}
            size="sm"
          >
            {t('settings.loginLogFilterSuccess')}
          </Button>
          <Button
            variant={filter === 'failure' ? 'primary' : 'secondary'}
            onClick={() => setFilter('failure')}
            size="sm"
          >
            {t('settings.loginLogFilterFailure')}
          </Button>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-500">
            {t('settings.loginLogTotalCount', { count: data?.total || 0 })}
          </span>
          <Button
            variant="secondary"
            onClick={fetchLogs}
            disabled={loading}
            size="sm"
          >
            {t('settings.loginLogRefresh')}
          </Button>
        </div>
      </div>

      {/* Error message */}
      {error && (
        <SettingsAlert title={error} message="" variant="error" className="mb-4" />
      )}

      {/* Loading state */}
      {loading && !data && (
        <div className="text-center py-8 text-gray-500">
          {t('common.loading')}
        </div>
      )}

      {/* Empty state */}
      {!loading && data && data.items.length === 0 && (
        <div className="text-center py-8 text-gray-500">
          {t('settings.loginLogEmpty')}
        </div>
      )}

      {/* Desktop table view */}
      {data && data.items.length > 0 && (
        <>
          <div className="hidden md:block overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-2 px-3 font-medium text-gray-700">
                    {t('settings.loginLogTime')}
                  </th>
                  <th className="text-left py-2 px-3 font-medium text-gray-700">
                    {t('settings.loginLogEvent')}
                  </th>
                  <th className="text-left py-2 px-3 font-medium text-gray-700">
                    {t('settings.loginLogResult')}
                  </th>
                  <th className="text-left py-2 px-3 font-medium text-gray-700">
                    {t('settings.loginLogIpLocation')}
                  </th>
                  <th className="text-left py-2 px-3 font-medium text-gray-700">
                    {t('settings.loginLogClient')}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-3 px-3 text-gray-600">
                      <div title={item.createdAt}>
                        {formatTime(item.createdAt)}
                      </div>
                    </td>
                    <td className="py-3 px-3 text-gray-900">
                      {getEventLabel(item.eventType)}
                    </td>
                    <td className="py-3 px-3">
                      <Badge variant={item.success ? 'success' : 'danger'}>
                        {item.success ? t('common.success') : getFailureLabel(item.failureReason)}
                      </Badge>
                    </td>
                    <td className="py-3 px-3 text-gray-600">
                      <div>
                        {item.ip}
                        {item.peerIp && (
                          <span className="ml-1 text-xs text-gray-400" title={`Peer: ${item.peerIp}`}>
                            ({t('settings.loginLogViaProxy')})
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-gray-500">
                        {getLocationText(item)}
                      </div>
                    </td>
                    <td className="py-3 px-3 text-gray-600">
                      <div>{getClientLabel(item)}</div>
                      <div className="text-xs text-gray-500">
                        {getDeviceLabel(item.client.deviceType)}
                        {item.client.os && ` · ${item.client.os}`}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Mobile card view */}
          <div className="md:hidden space-y-3">
            {data.items.map((item) => (
              <div key={item.id} className="border border-gray-200 rounded-lg p-3">
                <div className="flex items-start justify-between mb-2">
                  <div className="text-sm text-gray-600">
                    {formatTime(item.createdAt)}
                  </div>
                  <Badge variant={item.success ? 'success' : 'danger'}>
                    {item.success ? t('common.success') : getFailureLabel(item.failureReason)}
                  </Badge>
                </div>
                <div className="text-sm font-medium text-gray-900 mb-2">
                  {getEventLabel(item.eventType)}
                </div>
                <div className="text-sm text-gray-600 mb-1">
                  {item.ip}
                  {item.peerIp && (
                    <span className="ml-1 text-xs text-gray-400">
                      ({t('settings.loginLogViaProxy')})
                    </span>
                  )}
                </div>
                <div className="text-xs text-gray-500 mb-2">
                  {getLocationText(item)}
                </div>
                <div className="text-sm text-gray-600">
                  {getClientLabel(item)}
                </div>
                <div className="text-xs text-gray-500">
                  {getDeviceLabel(item.client.deviceType)}
                  {item.client.os && ` · ${item.client.os}`}
                </div>
              </div>
            ))}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-4">
              <Button
                variant="secondary"
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                size="sm"
              >
                ‹
              </Button>
              <span className="text-sm text-gray-600">
                {currentPage} / {totalPages}
              </span>
              <Button
                variant="secondary"
                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                size="sm"
              >
                ›
              </Button>
            </div>
          )}

          {/* Attribution */}
          {data.geo?.offline.attribution === 'dbip' && (
            <div className="mt-4 text-xs text-gray-400 text-center">
              {t('settings.loginLogAttributionDbip')}
            </div>
          )}
          {data.geo?.offline.attribution === 'maxmind' && (
            <div className="mt-4 text-xs text-gray-400 text-center">
              {t('settings.loginLogAttributionMaxmind')}
            </div>
          )}
        </>
      )}
    </SettingsSectionCard>
  );
};
