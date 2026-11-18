import apiClient from './index';

export type AuthStatusResponse = {
  authEnabled: boolean;
  loggedIn: boolean;
  passwordSet?: boolean;
  passwordChangeable?: boolean;
  setupState: 'enabled' | 'password_retained' | 'no_password';
};

export type LocationInfo = {
  status: 'resolved' | 'pending' | 'unresolved' | 'not_configured' | 'skipped';
  source?: string;
  countryCode?: string;
  en: {
    country?: string;
    region?: string;
    city?: string;
  };
  zh: {
    country?: string;
    region?: string;
    city?: string;
  };
};

export type ClientInfo = {
  type: 'browser' | 'desktop' | 'script' | 'unknown';
  browser?: string;
  browserVersion?: string;
  os?: string;
  deviceType: 'desktop' | 'mobile' | 'tablet' | 'unknown';
  userAgent?: string;
};

export type LoginLogItem = {
  id: number;
  createdAt: string;
  eventType: 'login' | 'initial_setup' | 'auth_enable' | 'auth_disable' | 'password_change';
  success: boolean;
  failureReason?: string;
  usernameMatched?: boolean;
  ip: string;
  peerIp?: string;
  ipScope: 'public' | 'loopback' | 'private' | 'invalid';
  location: LocationInfo;
  client: ClientInfo;
};

export type LoginLogResponse = {
  items: LoginLogItem[];
  total: number;
  limit: number;
  offset: number;
  retentionDays: number;
  geo: {
    offline: {
      configured: boolean;
      available: boolean;
      databaseType?: string;
      attribution?: 'dbip' | 'maxmind';
    };
    online: {
      provider?: string;
    };
  };
};

export type LoginLogQueryParams = {
  result?: 'all' | 'success' | 'failure';
  limit?: number;
  offset?: number;
};

export const authApi = {
  async getStatus(): Promise<AuthStatusResponse> {
    const { data } = await apiClient.get<AuthStatusResponse>('/api/v1/auth/status');
    return data;
  },

  async updateSettings(
    authEnabled: boolean,
    password?: string,
    passwordConfirm?: string,
    currentPassword?: string
  ): Promise<AuthStatusResponse> {
    const body: {
      authEnabled: boolean;
      password?: string;
      passwordConfirm?: string;
      currentPassword?: string;
    } = { authEnabled };
    if (password !== undefined) {
      body.password = password;
    }
    if (passwordConfirm !== undefined) {
      body.passwordConfirm = passwordConfirm;
    }
    if (currentPassword !== undefined) {
      body.currentPassword = currentPassword;
    }
    const { data } = await apiClient.post<AuthStatusResponse>('/api/v1/auth/settings', body);
    return data;
  },

  async login(username: string, password: string, passwordConfirm?: string): Promise<void> {
    await apiClient.post('/api/v1/auth/login', { username, password, passwordConfirm });
  },

  async changePassword(
    currentPassword: string,
    newPassword: string,
    newPasswordConfirm: string
  ): Promise<void> {
    await apiClient.post('/api/v1/auth/change-password', {
      currentPassword,
      newPassword,
      newPasswordConfirm,
    });
  },

  async logout(): Promise<void> {
    await apiClient.post('/api/v1/auth/logout');
  },

  async getLoginLogs(params: LoginLogQueryParams = {}): Promise<LoginLogResponse> {
    const { result = 'all', limit = 20, offset = 0 } = params;
    const { data } = await apiClient.get<LoginLogResponse>('/api/v1/auth/login-logs', {
      params: { result, limit, offset },
    });
    return data;
  },
};
